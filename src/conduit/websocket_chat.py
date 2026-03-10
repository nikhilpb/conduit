"""WebSocket chat protocol support for Conduit."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from dataclasses import field
import uuid
from typing import Any

from google.adk.flows.llm_flows.functions import (
    REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
)
from google.adk.sessions.session import Session
from google.genai import types

from conduit.runtime import ConduitRuntime
from conduit.sessions.sqlite_service import ClientTurnRecord
from conduit.tool_permissions import permission_summary
from conduit.user_context import build_state_delta
from conduit.user_context import coerce_turn_context


def _make_turn_id() -> str:
    return f"t_{uuid.uuid4().hex}"


def _make_assistant_message_id() -> str:
    return f"a_{uuid.uuid4().hex}"


def _chunk_text(text: str, *, chunk_size: int = 32) -> list[str]:
    if not text:
        return []
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]


@dataclass(slots=True)
class ActiveTurn:
    session_id: str
    message_id: str
    turn_id: str
    assistant_message_id: str
    invocation_id: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    subscribers: set[asyncio.Queue[Any]] = field(default_factory=set)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    seen_tool_call_ids: set[str] = field(default_factory=set)
    pending_approval_id: str | None = None
    state_delta: dict[str, Any] = field(default_factory=dict)
    completed: bool = False
    failed: bool = False
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def attach(self, queue: asyncio.Queue[Any]) -> None:
        async with self._lock:
            self.subscribers.add(queue)
            history = [copy.deepcopy(item) for item in self.history]

        for item in history:
            await queue.put(item)

    async def detach(self, queue: asyncio.Queue[Any]) -> None:
        async with self._lock:
            self.subscribers.discard(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        async with self._lock:
            self.history.append(copy.deepcopy(event))
            subscribers = list(self.subscribers)

        for queue in subscribers:
            await queue.put(copy.deepcopy(event))

    async def snapshot_history(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [copy.deepcopy(item) for item in self.history]

    async def mark_done(self) -> None:
        async with self._lock:
            self.completed = True
            self.pending_approval_id = None

    async def mark_failed(self) -> None:
        async with self._lock:
            self.failed = True
            self.pending_approval_id = None

    async def set_pending_approval(self, approval_id: str | None) -> None:
        async with self._lock:
            self.pending_approval_id = approval_id


class WebSocketChatManager:
    """Manage active websocket turns and replay completed messages."""

    def __init__(self, runtime: ConduitRuntime):
        self.runtime = runtime
        self._active_turns: dict[tuple[str, str], ActiveTurn] = {}
        self._approval_index: dict[str, tuple[str, str]] = {}
        self._lock = asyncio.Lock()

    async def register_connection(self) -> asyncio.Queue[Any]:
        return asyncio.Queue()

    async def unregister_connection(self, queue: asyncio.Queue[Any]) -> None:
        async with self._lock:
            turns = list(self._active_turns.values())

        for turn in turns:
            await turn.detach(queue)

    async def create_session(self, *, client_request_id: str | None) -> dict[str, Any]:
        session = await self.runtime.create_session()
        return {
            "type": "session_created",
            "session_id": session.id,
            "client_request_id": client_request_id,
        }

    async def handle_client_message(
        self,
        *,
        queue: asyncio.Queue[Any],
        payload: dict[str, Any],
    ) -> None:
        message_type = payload.get("type")

        if message_type == "new_session":
            await queue.put(
                await self.create_session(
                    client_request_id=_optional_string(payload.get("client_request_id"))
                )
            )
            return

        if message_type == "approval":
            await self._handle_approval_response(
                queue=queue,
                payload=payload,
            )
            return

        if message_type not in {"text", "voice"}:
            await queue.put(
                {
                    "type": "error",
                    "message": f"unsupported message type: {message_type}",
                }
            )
            return

        message_id = _required_string(payload, "message_id")
        content = _required_string(payload, "content")
        session_id = _optional_string(payload.get("session_id"))
        state_delta = build_state_delta(coerce_turn_context(payload.get("context")))
        if not content:
            raise ValueError("content must be a non-empty string")

        session = await self.runtime.get_or_create_session(session_id)
        existing = await self.runtime.session_service.get_client_turn(
            app_name=self.runtime.settings.app_name,
            user_id=self.runtime.settings.internal_user_id,
            session_id=session.id,
            message_id=message_id,
        )
        if existing is not None:
            await self._handle_duplicate_turn(
                queue=queue,
                record=existing,
            )
            return

        turn = ActiveTurn(
            session_id=session.id,
            message_id=message_id,
            turn_id=_make_turn_id(),
            assistant_message_id=_make_assistant_message_id(),
            state_delta=state_delta,
        )

        async with self._lock:
            self._active_turns[(session.id, message_id)] = turn
        await turn.attach(queue)

        await self.runtime.session_service.save_client_turn_started(
            app_name=self.runtime.settings.app_name,
            user_id=self.runtime.settings.internal_user_id,
            session_id=session.id,
            message_id=message_id,
            turn_id=turn.turn_id,
            assistant_message_id=turn.assistant_message_id,
        )

        await turn.publish(
            {
                "type": "ack",
                "message_id": message_id,
                "session_id": session.id,
                "turn_id": turn.turn_id,
            }
        )
        asyncio.create_task(
            self._process_invocation(
                turn=turn,
                session=session,
                new_message=types.UserContent(parts=[types.Part(text=content)]),
                state_delta=state_delta,
            )
        )

    async def _handle_approval_response(
        self,
        *,
        queue: asyncio.Queue[Any],
        payload: dict[str, Any],
    ) -> None:
        approval_id = _required_string(payload, "approval_id")
        decision = _required_string(payload, "decision")
        if decision not in {"approve", "deny"}:
            raise ValueError("decision must be either approve or deny")

        async with self._lock:
            turn_key = self._approval_index.get(approval_id)
            turn = self._active_turns.get(turn_key) if turn_key else None

        if turn is None:
            await queue.put(
                {
                    "type": "error",
                    "message": "approval request is no longer active",
                }
            )
            return

        confirmed = decision == "approve"
        session = await self.runtime.get_or_create_session(turn.session_id)
        await turn.set_pending_approval(None)
        async with self._lock:
            self._approval_index.pop(approval_id, None)

        asyncio.create_task(
            self._process_invocation(
                turn=turn,
                session=session,
                new_message=_build_approval_message(
                    approval_id=approval_id,
                    confirmed=confirmed,
                ),
                state_delta=turn.state_delta,
            )
        )

    async def _handle_duplicate_turn(
        self,
        *,
        queue: asyncio.Queue[Any],
        record: ClientTurnRecord,
    ) -> None:
        if record.status == "completed":
            for event in record.event_history:
                await queue.put(copy.deepcopy(event))
            return

        if record.status == "failed":
            for event in record.event_history:
                await queue.put(copy.deepcopy(event))
            if not record.event_history:
                await queue.put(
                    {
                        "type": "error",
                        "turn_id": record.turn_id,
                        "message": record.error_message or "turn failed",
                    }
                )
            return

        async with self._lock:
            turn = self._active_turns.get((record.session_id, record.message_id))

        if turn is not None:
            await turn.attach(queue)
            return

        error_event = {
            "type": "error",
            "turn_id": record.turn_id,
            "message": "turn was interrupted before completion; retry with a new message_id",
        }
        event_history = list(record.event_history)
        event_history.append(error_event)
        await self.runtime.session_service.save_client_turn_failed(
            app_name=self.runtime.settings.app_name,
            user_id=self.runtime.settings.internal_user_id,
            session_id=record.session_id,
            message_id=record.message_id,
            turn_id=record.turn_id,
            assistant_message_id=record.assistant_message_id,
            error_message=error_event["message"],
            event_history=event_history,
        )
        for event in event_history:
            await queue.put(copy.deepcopy(event))

    async def _process_invocation(
        self,
        *,
        turn: ActiveTurn,
        session: Session,
        new_message: types.Content,
        state_delta: dict[str, Any] | None = None,
    ) -> None:
        final_reply = ""
        fallback_reply = ""
        terminal = False

        try:
            async for event in self.runtime.iter_events(
                session=session,
                invocation_id=turn.invocation_id,
                new_message=new_message,
                state_delta=state_delta,
            ):
                if event.author == "user":
                    continue
                if turn.invocation_id is None:
                    turn.invocation_id = event.invocation_id

                approval_event = _extract_approval_required_event(
                    event=event,
                    turn=turn,
                )
                if approval_event is not None:
                    approval_id = approval_event["approval_id"]
                    await turn.set_pending_approval(approval_id)
                    async with self._lock:
                        self._approval_index[approval_id] = (
                            turn.session_id,
                            turn.message_id,
                        )
                    await turn.publish(approval_event)
                    return

                for function_call in event.get_function_calls():
                    if function_call.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME:
                        continue

                    tool_call_id = getattr(function_call, "id", None) or f"tool_{len(turn.tool_calls)}"
                    if tool_call_id in turn.seen_tool_call_ids:
                        continue

                    turn.seen_tool_call_ids.add(tool_call_id)
                    tool_call = {
                        "tool_call_id": tool_call_id,
                        "name": function_call.name or "",
                        "args": dict(function_call.args or {}),
                    }
                    turn.tool_calls.append(tool_call)
                    await turn.publish(
                        {
                            "type": "tool_call",
                            "turn_id": turn.turn_id,
                            "tool_call_id": tool_call_id,
                            "tool": function_call.name,
                            "args": dict(function_call.args or {}),
                            "permission": self.runtime.tool_permission_mode(
                                function_call.name or ""
                            ),
                        }
                    )

                if event.actions.requested_tool_confirmations:
                    continue

                thought = _extract_thought_text(event.content)
                if thought:
                    await turn.publish(
                        {
                            "type": "thought",
                            "turn_id": turn.turn_id,
                            "message_id": turn.assistant_message_id,
                            "content": thought,
                            "agent": "conduit",
                        }
                    )

                text = _extract_text(event.content)
                if not text:
                    continue

                if event.is_final_response():
                    final_reply = text
                elif not event.partial:
                    fallback_reply = text

            reply = final_reply or fallback_reply
            for chunk in _chunk_text(reply):
                await turn.publish(
                    {
                        "type": "token",
                        "turn_id": turn.turn_id,
                        "message_id": turn.assistant_message_id,
                        "content": chunk,
                        "agent": "conduit",
                    }
                )

            await turn.publish(
                {
                    "type": "done",
                    "turn_id": turn.turn_id,
                    "session_id": session.id,
                    "message_id": turn.assistant_message_id,
                }
            )
            event_history = await turn.snapshot_history()
            await self.runtime.session_service.save_client_turn_completed(
                app_name=self.runtime.settings.app_name,
                user_id=self.runtime.settings.internal_user_id,
                session_id=session.id,
                message_id=turn.message_id,
                turn_id=turn.turn_id,
                assistant_message_id=turn.assistant_message_id,
                reply=reply,
                tool_calls=turn.tool_calls,
                event_history=event_history,
            )
            await turn.mark_done()
            terminal = True
        except Exception as exc:
            await turn.publish(
                {
                    "type": "error",
                    "turn_id": turn.turn_id,
                    "message": str(exc),
                }
            )
            event_history = await turn.snapshot_history()
            await self.runtime.session_service.save_client_turn_failed(
                app_name=self.runtime.settings.app_name,
                user_id=self.runtime.settings.internal_user_id,
                session_id=session.id,
                message_id=turn.message_id,
                turn_id=turn.turn_id,
                assistant_message_id=turn.assistant_message_id,
                error_message=str(exc),
                event_history=event_history,
            )
            await turn.mark_failed()
            terminal = True
        finally:
            if terminal:
                async with self._lock:
                    if turn.pending_approval_id:
                        self._approval_index.pop(turn.pending_approval_id, None)
                    self._active_turns.pop((turn.session_id, turn.message_id), None)


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected a string")
    return value


def _extract_text(content: types.Content | None) -> str:
    if content is None or not content.parts:
        return ""

    parts = [
        part.text.strip()
        for part in content.parts
        if part.text and not getattr(part, "thought", False)
    ]
    return "\n".join(part for part in parts if part).strip()


def _extract_thought_text(content: types.Content | None) -> str:
    if content is None or not content.parts:
        return ""

    parts = [
        part.text.strip()
        for part in content.parts
        if part.text and getattr(part, "thought", False)
    ]
    return "\n\n".join(part for part in parts if part).strip()


def _extract_approval_required_event(
    *,
    event,
    turn: ActiveTurn,
) -> dict[str, Any] | None:
    for function_call in event.get_function_calls():
        if function_call.name != REQUEST_CONFIRMATION_FUNCTION_CALL_NAME:
            continue

        args = dict(function_call.args or {})
        original_function_call = args.get("originalFunctionCall", {}) or {}
        tool_confirmation = args.get("toolConfirmation", {}) or {}
        payload = tool_confirmation.get("payload", {}) or {}
        tool_name = original_function_call.get("name", "tool")
        tool_args = dict(original_function_call.get("args") or {})

        return {
            "type": "approval_required",
            "turn_id": turn.turn_id,
            "approval_id": function_call.id or "",
            "tool_call_id": original_function_call.get("id") or "",
            "tool": tool_name,
            "summary": payload.get("summary")
            or tool_confirmation.get("hint")
            or permission_summary(tool_name, tool_args),
        }

    return None


def _build_approval_message(
    *,
    approval_id: str,
    confirmed: bool,
) -> types.Content:
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=approval_id,
                    name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                    response={"confirmed": confirmed},
                )
            )
        ],
    )
