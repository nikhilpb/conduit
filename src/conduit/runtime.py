"""Runtime helpers for executing ADK agents from FastAPI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import field
from typing import AsyncIterator
from typing import Any
from typing import Literal
import uuid

from google.adk.apps import App
from google.adk.apps import ResumabilityConfig
from google.adk.events.event import Event
from google.adk.runners import Runner
from google.adk.sessions.session import Session
from google.genai import types

from conduit.agent import build_root_agent
from conduit.config import Settings
from conduit.model_registry import ModelOption
from conduit.model_registry import ModelRegistry
from conduit.model_registry import persist_model_registry
from conduit.model_registry import load_model_registry
from conduit.sessions import SQLiteSessionService
from conduit.tool_permissions import effective_tool_permission
from conduit.tool_call_utils import tool_response_status


@dataclass(slots=True)
class TurnResult:
    session_id: str
    reply: str
    tool_calls: list[dict[str, Any]]


@dataclass(slots=True)
class RuntimeTurnUpdate:
    kind: Literal["tool_call", "tool_result", "reply"]
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_status: str = "pending"
    tool_error: str | None = None
    text: str = ""


class ConduitRuntime:
    """Thin wrapper around ADK's runner and session service."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.session_service = SQLiteSessionService(settings.db_path)
        self._model_lock = asyncio.Lock()
        self._model_registry = load_model_registry(
            settings.models_config_path,
            fallback_model=settings.model,
        )
        persist_model_registry(settings.models_config_path, self._model_registry)
        self._apply_model_registry(self._model_registry)

    @property
    def active_model(self) -> ModelOption:
        return self._model_registry.active

    @property
    def model_registry(self) -> ModelRegistry:
        return self._model_registry

    async def set_active_model(self, model_key: str) -> ModelOption:
        """Persist and activate a new base model."""

        async with self._model_lock:
            registry = self._model_registry.with_active(model_key)
            if not self.settings.provider_api_key_configured_for(
                registry.active.provider
            ):
                raise ValueError(
                    f"{registry.active.provider} credentials are not configured."
                )

            persist_model_registry(self.settings.models_config_path, registry)
            self._model_registry = registry
            self._apply_model_registry(registry)
            return registry.active

    async def create_session(self, session_id: str | None = None) -> Session:
        """Create a new chat session."""

        return await self.session_service.create_session(
            app_name=self.settings.app_name,
            user_id=self.settings.internal_user_id,
            session_id=session_id,
        )

    async def list_sessions(self) -> list[Session]:
        """List existing sessions."""

        response = await self.session_service.list_sessions(
            app_name=self.settings.app_name,
            user_id=self.settings.internal_user_id,
        )
        return sorted(
            response.sessions,
            key=lambda session: session.last_update_time,
            reverse=True,
        )

    async def delete_session(self, session_id: str) -> None:
        """Delete a session if it exists."""

        await self.session_service.delete_session(
            app_name=self.settings.app_name,
            user_id=self.settings.internal_user_id,
            session_id=session_id,
        )

    async def get_or_create_session(self, session_id: str | None = None) -> Session:
        """Return an existing session or create a new one."""

        if not session_id:
            return await self.create_session()

        session = await self.session_service.get_session(
            app_name=self.settings.app_name,
            user_id=self.settings.internal_user_id,
            session_id=session_id,
        )
        if session is not None:
            return session
        return await self.create_session(session_id=session_id)

    def create_invocation_id(self) -> str:
        """Create a stable invocation id for a websocket turn."""

        return f"inv_{uuid.uuid4().hex}"

    def tool_permission_mode(self, tool_name: str) -> str:
        """Return the configured permission mode for a tool."""

        return effective_tool_permission(
            tool_name,
            permissions=self.settings.tool_permissions,
        )

    async def iter_events(
        self,
        *,
        session: Session,
        new_message: types.Content,
        invocation_id: str | None = None,
        state_delta: dict[str, Any] | None = None,
    ) -> AsyncIterator[Event]:
        """Yield raw ADK events for a session invocation."""

        async for event in self.runner.run_async(
            user_id=self.settings.internal_user_id,
            session_id=session.id,
            invocation_id=invocation_id,
            new_message=new_message,
            state_delta=state_delta,
        ):
            yield event

    async def stream_turn(
        self,
        *,
        session: Session,
        message: str,
        state_delta: dict[str, Any] | None = None,
    ) -> AsyncIterator[RuntimeTurnUpdate]:
        """Yield structured updates for a single turn."""

        final_reply = ""
        fallback_reply = ""
        seen_tool_call_ids: set[str] = set()
        fallback_tool_call_index = 0

        async for event in self.iter_events(
            session=session,
            new_message=types.UserContent(parts=[types.Part(text=message)]),
            state_delta=state_delta,
        ):
            if event.author == "user":
                continue

            if event.actions.requested_tool_confirmations:
                continue

            for function_call in event.get_function_calls():
                if _is_internal_function_call(function_call.name):
                    continue
                tool_call_id = getattr(function_call, "id", None)
                if not tool_call_id:
                    tool_call_id = f"tool_{fallback_tool_call_index}"
                    fallback_tool_call_index += 1
                if tool_call_id in seen_tool_call_ids:
                    continue
                seen_tool_call_ids.add(tool_call_id)
                yield RuntimeTurnUpdate(
                    kind="tool_call",
                    tool_call_id=tool_call_id,
                    tool_name=function_call.name,
                    tool_args=dict(function_call.args or {}),
                )

            for function_response in event.get_function_responses():
                if _is_internal_function_call(function_response.name):
                    continue
                tool_call_id = getattr(function_response, "id", None)
                status, error = tool_response_status(function_response.response)
                yield RuntimeTurnUpdate(
                    kind="tool_result",
                    tool_call_id=tool_call_id,
                    tool_name=function_response.name,
                    tool_status=status,
                    tool_error=error,
                )

            text = _extract_text(event.content)
            if not text:
                continue

            if event.is_final_response():
                final_reply = text
            elif not event.partial:
                fallback_reply = text

        yield RuntimeTurnUpdate(
            kind="reply",
            text=final_reply or fallback_reply,
        )

    async def run_turn(
        self,
        *,
        message: str,
        session_id: str | None = None,
        state_delta: dict[str, Any] | None = None,
    ) -> TurnResult:
        """Run a single user turn against the ADK runner."""

        session = await self.get_or_create_session(session_id)
        tool_calls: list[dict[str, Any]] = []
        tool_call_index_by_id: dict[str, int] = {}
        reply = ""

        async for update in self.stream_turn(
            session=session,
            message=message,
            state_delta=state_delta,
        ):
            if update.kind == "tool_call":
                tool_call = {
                    "tool_call_id": update.tool_call_id,
                    "name": update.tool_name or "",
                    "args": dict(update.tool_args),
                    "status": "pending",
                    "error": None,
                }
                if update.tool_call_id:
                    tool_call_index_by_id[update.tool_call_id] = len(tool_calls)
                tool_calls.append(tool_call)
                continue
            if update.kind == "tool_result":
                tool_call = {
                    "tool_call_id": update.tool_call_id,
                    "name": update.tool_name or "",
                    "args": {},
                    "status": update.tool_status,
                    "error": update.tool_error,
                }
                if update.tool_call_id and update.tool_call_id in tool_call_index_by_id:
                    tool_calls[tool_call_index_by_id[update.tool_call_id]] = {
                        **tool_calls[tool_call_index_by_id[update.tool_call_id]],
                        "name": update.tool_name
                        or tool_calls[tool_call_index_by_id[update.tool_call_id]]["name"],
                        "status": update.tool_status,
                        "error": update.tool_error,
                    }
                else:
                    if update.tool_call_id:
                        tool_call_index_by_id[update.tool_call_id] = len(tool_calls)
                    tool_calls.append(tool_call)
                continue
            reply = update.text

        return TurnResult(
            session_id=session.id,
            reply=reply,
            tool_calls=tool_calls,
        )

    def _apply_model_registry(self, registry: ModelRegistry) -> None:
        self.app = App(
            name=self.settings.app_name,
            root_agent=build_root_agent(
                self.settings,
                model_name=registry.active.model,
            ),
            resumability_config=ResumabilityConfig(is_resumable=True),
        )
        self.runner = Runner(
            app=self.app,
            session_service=self.session_service,
        )


def _extract_text(content: types.Content | None) -> str:
    """Extract plain text from a content payload."""

    if content is None or not content.parts:
        return ""

    parts = [
        part.text.strip()
        for part in content.parts
        if part.text and not getattr(part, "thought", False)
    ]
    return "\n".join(part for part in parts if part).strip()


def _is_internal_function_call(name: str | None) -> bool:
    return name in {"adk_request_confirmation"}
