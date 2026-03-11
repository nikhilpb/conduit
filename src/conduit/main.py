"""FastAPI entrypoint for Conduit."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi import WebSocket
from fastapi import WebSocketDisconnect
import uvicorn

from conduit.config import Settings
from conduit.config import get_settings
from conduit.context_estimate import CONTEXT_CHARS_PER_TOKEN
from conduit.context_estimate import ContextEstimate
from conduit.context_estimate import estimate_events_context
from conduit.runtime import ConduitRuntime
from conduit.schemas import ChatRequest
from conduit.schemas import ChatResponse
from conduit.schemas import ContextEstimateResponse
from conduit.schemas import CreateSessionResponse
from conduit.schemas import HealthResponse
from conduit.schemas import ModelOptionResponse
from conduit.schemas import ModelSettingsResponse
from conduit.schemas import SessionDetailResponse
from conduit.schemas import SessionListResponse
from conduit.schemas import SessionResponse
from conduit.schemas import TranscriptMessage
from conduit.schemas import ToolCall
from conduit.schemas import UpdateModelRequest
from conduit.tool_call_utils import public_tool_response
from conduit.tool_call_utils import tool_response_status
from conduit.user_context import build_state_delta
from conduit.user_context import coerce_turn_context
from conduit.websocket_chat import WebSocketChatManager


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the FastAPI application."""

    resolved_settings = settings or get_settings()
    runtime = ConduitRuntime(resolved_settings)
    chat_manager = WebSocketChatManager(runtime)

    app = FastAPI(
        title="Conduit API",
        version="0.1.0",
        description="Minimal ADK-backed FastAPI gateway for Conduit.",
    )
    app.state.settings = resolved_settings
    app.state.runtime = runtime
    app.state.chat_manager = chat_manager

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        active_model = runtime.active_model
        return HealthResponse(
            ok=True,
            app_name=resolved_settings.app_name,
            model=active_model.model,
            model_label=active_model.label,
            provider=active_model.provider,
            provider_api_key_configured=resolved_settings.provider_api_key_configured_for(
                active_model.provider
            ),
            context_chars_per_token=CONTEXT_CHARS_PER_TOKEN,
        )

    @app.get("/settings/model", response_model=ModelSettingsResponse)
    async def get_model_settings() -> ModelSettingsResponse:
        return _build_model_settings_response(runtime)

    @app.put("/settings/model", response_model=ModelSettingsResponse)
    async def update_model_settings(payload: UpdateModelRequest) -> ModelSettingsResponse:
        try:
            await runtime.set_active_model(payload.model_key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _build_model_settings_response(runtime)

    @app.post("/sessions", response_model=CreateSessionResponse, status_code=201)
    async def create_session() -> CreateSessionResponse:
        session = await runtime.create_session()
        return CreateSessionResponse(session_id=session.id)

    @app.get("/sessions", response_model=SessionListResponse)
    async def list_sessions() -> SessionListResponse:
        sessions = await runtime.session_service.get_session_summaries(
            app_name=runtime.settings.app_name,
            user_id=runtime.settings.internal_user_id,
        )
        return SessionListResponse(
            sessions=[
                SessionResponse(
                    session_id=session.session_id,
                    last_update_time=session.last_update_time,
                    event_count=session.event_count,
                    title=session.title,
                )
                for session in sessions
            ]
        )

    @app.get("/sessions/{session_id}", response_model=SessionDetailResponse)
    async def get_session(session_id: str, request: Request) -> SessionDetailResponse:
        session = await _lookup_session(request, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return SessionDetailResponse(
            session_id=session.id,
            messages=_build_transcript(session.events),
            context_estimate=_context_estimate_response(
                estimate_events_context(session.events)
            ),
        )

    @app.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str, request: Request) -> None:
        session = await _lookup_session(request, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        await runtime.delete_session(session_id)

    @app.post("/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest) -> ChatResponse:
        result = await runtime.run_turn(
            message=payload.message,
            session_id=payload.session_id,
            state_delta=build_state_delta(coerce_turn_context(payload.context)),
        )
        return ChatResponse(
            session_id=result.session_id,
            reply=result.reply,
            tool_calls=[ToolCall(**tool_call) for tool_call in result.tool_calls],
            context_estimate=_context_estimate_response(result.context_estimate),
        )

    @app.websocket("/chat")
    async def chat_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = await websocket.app.state.chat_manager.register_connection()
        writer_task = asyncio.create_task(_websocket_writer(websocket, queue))

        try:
            while True:
                payload = await websocket.receive_json()
                try:
                    await websocket.app.state.chat_manager.handle_client_message(
                        queue=queue,
                        payload=payload,
                    )
                except ValueError as exc:
                    await queue.put(
                        {
                            "type": "error",
                            "message": str(exc),
                        }
                    )
        except WebSocketDisconnect:
            pass
        finally:
            await websocket.app.state.chat_manager.unregister_connection(queue)
            writer_task.cancel()
            with suppress(asyncio.CancelledError):
                await writer_task

    return app


async def _lookup_session(request: Request, session_id: str):
    runtime: ConduitRuntime = request.app.state.runtime
    return await runtime.session_service.get_session(
        app_name=runtime.settings.app_name,
        user_id=runtime.settings.internal_user_id,
        session_id=session_id,
    )


def _build_transcript(events) -> list[TranscriptMessage]:
    messages: list[TranscriptMessage] = []

    for event in events:
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) or []
        if not parts:
            continue

        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in parts:
            if getattr(part, "function_call", None):
                tool_calls.append(
                    ToolCall(
                        tool_call_id=part.function_call.id,
                        name=part.function_call.name,
                        args=dict(part.function_call.args or {}),
                        status="pending",
                    )
                )
            if getattr(part, "function_response", None):
                status, error = tool_response_status(part.function_response.response)
                tool_calls.append(
                    ToolCall(
                        tool_call_id=part.function_response.id,
                        name=part.function_response.name or "tool",
                        args={},
                        status=status,
                        error=error,
                        response=public_tool_response(
                            part.function_response.name,
                            part.function_response.response,
                        ),
                    )
                )
            if part.text and not getattr(part, "thought", False):
                text_parts.append(part.text.strip())
            if part.text and getattr(part, "thought", False):
                thinking_parts.append(part.text.strip())

        text = "\n".join(part for part in text_parts if part).strip()
        thinking_trace = "\n\n".join(part for part in thinking_parts if part).strip()
        if not text and not tool_calls and not thinking_trace:
            continue

        messages.append(
            TranscriptMessage(
                message_id=event.id,
                role="user" if event.author == "user" else "assistant",
                text=text,
                created_at=event.timestamp,
                thinking_trace=thinking_trace,
                tool_calls=tool_calls,
            )
        )

    return messages


async def _websocket_writer(websocket: WebSocket, queue: asyncio.Queue[dict]) -> None:
    while True:
        event = await queue.get()
        await websocket.send_json(event)


def _build_model_settings_response(runtime: ConduitRuntime) -> ModelSettingsResponse:
    registry = runtime.model_registry
    active = registry.active
    return ModelSettingsResponse(
        active_key=active.key,
        active_model=active.model,
        active_label=active.label,
        provider=active.provider,
        options=[
            ModelOptionResponse(
                key=option.key,
                label=option.label,
                model=option.model,
                provider=option.provider,
                available=runtime.settings.provider_api_key_configured_for(
                    option.provider
                ),
            )
            for option in registry.options
        ],
    )


def _context_estimate_response(estimate: ContextEstimate) -> ContextEstimateResponse:
    return ContextEstimateResponse(**estimate.to_payload())


app = create_app()


def run() -> None:
    """Run the API server with uvicorn."""

    settings = get_settings()
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
    )
