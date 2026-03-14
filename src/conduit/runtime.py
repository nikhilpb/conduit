"""Runtime helpers for executing ADK agents from FastAPI."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
import time
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
from conduit.context_estimate import ContextEstimate
from conduit.context_estimate import empty_context_estimate
from conduit.context_estimate import estimate_events_context
from conduit.model_registry import ModelOption
from conduit.model_registry import ModelRegistry
from conduit.model_registry import load_model_registry
from conduit.model_registry import persist_model_registry
from conduit.session_metadata import build_scheduled_session_state
from conduit.session_metadata import session_read_only_from_state
from conduit.sessions import SQLiteSessionService
from conduit.tool_permissions import effective_tool_permission
from conduit.tool_call_utils import is_internal_tool_call
from conduit.tool_call_utils import public_tool_response
from conduit.tool_call_utils import tool_response_status


@dataclass(slots=True)
class TurnResult:
    session_id: str
    reply: str
    tool_calls: list[dict[str, Any]]
    context_estimate: ContextEstimate = field(default_factory=empty_context_estimate)


@dataclass(slots=True)
class RuntimeTurnUpdate:
    kind: Literal["tool_call", "tool_result", "reply"]
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_response: dict[str, Any] | None = None
    tool_status: str = "pending"
    tool_error: str | None = None
    text: str = ""


@dataclass(slots=True)
class RunnerBundle:
    app: App
    runner: Runner


class ReadOnlySessionError(ValueError):
    """Raised when a caller attempts to write into a read-only session."""


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

    async def create_session(
        self,
        session_id: str | None = None,
        *,
        state: dict[str, Any] | None = None,
    ) -> Session:
        """Create a new chat session."""

        return await self.session_service.create_session(
            app_name=self.settings.app_name,
            user_id=self.settings.internal_user_id,
            session_id=session_id,
            state=state,
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

    async def get_session_context_estimate(self, session_id: str) -> ContextEstimate:
        session = await self.session_service.get_session(
            app_name=self.settings.app_name,
            user_id=self.settings.internal_user_id,
            session_id=session_id,
        )
        if session is None:
            return empty_context_estimate()
        return estimate_events_context(session.events)

    async def get_session(self, session_id: str) -> Session | None:
        """Return one session by id if it exists."""

        return await self.session_service.get_session(
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

    async def get_or_create_writable_session(
        self,
        session_id: str | None = None,
    ) -> Session:
        """Return a writable session or create a new one."""

        session = await self.get_or_create_session(session_id)
        if session_read_only_from_state(session.state):
            raise ReadOnlySessionError(
                f"Session {session.id} is read-only and cannot accept new messages."
            )
        return session

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
        runner: Runner | None = None,
    ) -> AsyncIterator[Event]:
        """Yield raw ADK events for a session invocation."""

        active_runner = runner or self.runner
        async for event in active_runner.run_async(
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
        runner: Runner | None = None,
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
            runner=runner,
        ):
            if event.author == "user":
                continue

            if event.actions.requested_tool_confirmations:
                continue

            for function_call in event.get_function_calls():
                if is_internal_tool_call(function_call.name):
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
                if is_internal_tool_call(function_response.name):
                    continue
                tool_call_id = getattr(function_response, "id", None)
                response = public_tool_response(
                    function_response.name,
                    function_response.response,
                )
                status, error = tool_response_status(function_response.response)
                yield RuntimeTurnUpdate(
                    kind="tool_result",
                    tool_call_id=tool_call_id,
                    tool_name=function_response.name,
                    tool_response=response,
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

        session = await self.get_or_create_writable_session(session_id)
        return await self._run_turn_with_runner(
            session=session,
            message=message,
            state_delta=state_delta,
            runner=self.http_runner,
        )

    async def run_scheduled_session(
        self,
        *,
        schedule_id: str,
        scheduled_for: datetime,
        model_key: str,
        seed_query: str,
        allowed_tools: tuple[str, ...],
    ) -> str:
        """Execute one scheduled query and persist the final read-only session."""

        model_option = self.model_registry.get(model_key)
        if not self.settings.provider_api_key_configured_for(model_option.provider):
            raise ValueError(
                f"{model_option.provider} credentials are not configured."
            )

        temporary_session = await self.create_session()
        try:
            runner_bundle = self._build_runner_bundle(
                model_option=model_option,
                enable_bash=False,
                allowed_tools=set(allowed_tools),
                allow_tool_confirmation=False,
            )
            result = await self._run_turn_with_runner(
                session=temporary_session,
                message=seed_query,
                runner=runner_bundle.runner,
            )
        finally:
            await self.delete_session(temporary_session.id)

        scheduled_session = await self.create_session(
            state=build_scheduled_session_state(
                schedule_id=schedule_id,
                scheduled_for=scheduled_for.isoformat(),
                model_key=model_key,
                allowed_tools=allowed_tools,
            )
        )
        invocation_id = self.create_invocation_id()
        await self.session_service.append_event(
            scheduled_session,
            Event(
                invocation_id=invocation_id,
                author="user",
                timestamp=scheduled_for.timestamp(),
                content=types.Content(
                    role="user",
                    parts=[types.Part(text=seed_query)],
                ),
            ),
        )
        await self.session_service.append_event(
            scheduled_session,
            Event(
                invocation_id=invocation_id,
                author=self.settings.app_name,
                timestamp=max(time.time(), scheduled_for.timestamp()),
                content=types.Content(
                    role="model",
                    parts=[types.Part(text=result.reply)],
                ),
            ),
        )
        return scheduled_session.id

    async def _run_turn_with_runner(
        self,
        *,
        session: Session,
        message: str,
        state_delta: dict[str, Any] | None = None,
        runner: Runner,
    ) -> TurnResult:
        tool_calls: list[dict[str, Any]] = []
        tool_call_index_by_id: dict[str, int] = {}
        reply = ""

        async for update in self.stream_turn(
            session=session,
            message=message,
            state_delta=state_delta,
            runner=runner,
        ):
            if update.kind == "tool_call":
                tool_call = {
                    "tool_call_id": update.tool_call_id,
                    "name": update.tool_name or "",
                    "args": dict(update.tool_args),
                    "status": "pending",
                    "error": None,
                    "response": None,
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
                    "response": update.tool_response,
                }
                if update.tool_call_id and update.tool_call_id in tool_call_index_by_id:
                    tool_calls[tool_call_index_by_id[update.tool_call_id]] = {
                        **tool_calls[tool_call_index_by_id[update.tool_call_id]],
                        "name": update.tool_name
                        or tool_calls[tool_call_index_by_id[update.tool_call_id]]["name"],
                        "status": update.tool_status,
                        "error": update.tool_error,
                        "response": update.tool_response,
                    }
                else:
                    if update.tool_call_id:
                        tool_call_index_by_id[update.tool_call_id] = len(tool_calls)
                    tool_calls.append(tool_call)
                continue
            reply = update.text

        context_estimate = await self.get_session_context_estimate(session.id)
        return TurnResult(
            session_id=session.id,
            reply=reply,
            tool_calls=tool_calls,
            context_estimate=context_estimate,
        )

    def _apply_model_registry(self, registry: ModelRegistry) -> None:
        interactive = self._build_runner_bundle(
            model_option=registry.active,
            enable_bash=True,
        )
        self.app = interactive.app
        self.runner = interactive.runner

        http = self._build_runner_bundle(
            model_option=registry.active,
            enable_bash=False,
        )
        self.http_app = http.app
        self.http_runner = http.runner

    def _build_runner_bundle(
        self,
        *,
        model_option: ModelOption,
        enable_bash: bool,
        allowed_tools: set[str] | None = None,
        allow_tool_confirmation: bool = True,
    ) -> RunnerBundle:
        app = App(
            name=self.settings.app_name,
            root_agent=build_root_agent(
                self.settings,
                model_name=model_option.model,
                enable_bash=enable_bash,
                allowed_tools=allowed_tools,
                allow_tool_confirmation=allow_tool_confirmation,
            ),
            resumability_config=ResumabilityConfig(is_resumable=True),
        )
        return RunnerBundle(
            app=app,
            runner=Runner(
                app=app,
                session_service=self.session_service,
            ),
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
