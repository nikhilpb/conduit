import asyncio

from fastapi.testclient import TestClient
from google.adk.events.event import Event
from google.adk.flows.llm_flows.functions import (
    REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
)
from google.genai import types
import pytest

from conduit.config import Settings
from conduit.main import create_app
from conduit.sessions import SQLiteSessionService
from conduit.websocket_chat import WebSocketChatManager


class FakeRuntime:
    def __init__(
        self,
        *,
        db_path: str,
        reply: str,
        thought_trace: str = "",
        delay_seconds: float = 0.0,
        require_approval: bool = False,
        tool_error: str | None = None,
        tool_name: str = "web_search",
        tool_response: dict[str, object] | None = None,
    ):
        self.settings = Settings(
            _env_file=None,
            db_path=db_path,
        )
        self.session_service = SQLiteSessionService(db_path)
        self.reply = reply
        self.thought_trace = thought_trace
        self.delay_seconds = delay_seconds
        self.require_approval = require_approval
        self.tool_error = tool_error
        self.tool_name = tool_name
        self.tool_response = tool_response
        self.iter_event_calls = 0
        self.received_state_deltas: list[dict[str, object] | None] = []

    async def create_session(self, session_id: str | None = None):
        return await self.session_service.create_session(
            app_name=self.settings.app_name,
            user_id=self.settings.internal_user_id,
            session_id=session_id,
        )

    async def get_or_create_session(self, session_id: str | None = None):
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
        return "inv-test"

    def tool_permission_mode(self, tool_name: str) -> str:
        if self.require_approval and tool_name == "web_fetch":
            return "ask"
        return "allow"

    async def iter_events(
        self,
        *,
        session,
        new_message,
        invocation_id: str | None,
        state_delta=None,
    ):
        del session
        del invocation_id
        self.iter_event_calls += 1
        self.received_state_deltas.append(
            dict(state_delta) if state_delta is not None else None
        )

        if _is_approval_response(new_message):
            confirmed = bool(
                new_message.parts[0].function_response.response.get("confirmed")
            )
            if confirmed and self.require_approval:
                yield _tool_result_event(
                    tool_call_id="tc_1",
                    tool_name="web_fetch",
                    response={"ok": True},
                )
            yield _text_event("Approved result." if confirmed else "Denied result.")
            return

        message = new_message.parts[0].text or ""
        yield _tool_call_event(
            tool_call_id="tc_1",
            tool_name="web_fetch" if self.require_approval else self.tool_name,
            args={
                "url": "https://example.com"
                if self.require_approval
                else message,
            },
        )
        if self.require_approval:
            yield _approval_required_event()
            return
        if self.tool_error:
            yield _tool_result_event(
                tool_call_id="tc_1",
                tool_name=self.tool_name,
                response={"ok": False, "error": self.tool_error},
            )
        elif self.tool_response is not None:
            yield _tool_result_event(
                tool_call_id="tc_1",
                tool_name=self.tool_name,
                response=self.tool_response,
            )
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.thought_trace:
            yield _thought_event(self.thought_trace)
        yield _text_event(self.reply)


def test_websocket_turn_streams_ack_tool_calls_tokens_and_done(tmp_path):
    reply = "This reply is long enough to be chunked into multiple token events."
    thought_trace = "Search the query, inspect candidates, then summarize."
    app, runtime = _create_websocket_test_app(
        tmp_path=tmp_path,
        reply=reply,
        thought_trace=thought_trace,
    )
    client = TestClient(app)

    with client.websocket_connect("/chat") as websocket:
        websocket.send_json(
            {
                "type": "text",
                "message_id": "m1",
                "content": "openai homepage",
            }
        )
        events = _collect_events_until_terminal(websocket)

    assert events[0]["type"] == "ack"
    assert events[0]["message_id"] == "m1"
    assert events[0]["session_id"]
    assert any(event["type"] == "tool_call" for event in events)
    assert [event["content"] for event in events if event["type"] == "thought"] == [
        thought_trace
    ]
    assert "".join(
        event["content"] for event in events if event["type"] == "token"
    ) == reply
    assert events[-1]["type"] == "done"
    assert runtime.iter_event_calls == 1


def test_websocket_turn_passes_context_into_runtime_state(tmp_path):
    app, runtime = _create_websocket_test_app(
        tmp_path=tmp_path,
        reply="Context works.",
    )
    client = TestClient(app)

    with client.websocket_connect("/chat") as websocket:
        websocket.send_json(
            {
                "type": "text",
                "message_id": "m1",
                "content": "What time is it for me?",
                "context": {
                    "current_time": "2026-03-10 19:15:00 CET (UTC+01:00)",
                    "location": "Zurich, Switzerland",
                    "personal_instructions": "Prefer concise answers.",
                },
            }
        )
        _collect_events_until_terminal(websocket)

    assert runtime.received_state_deltas == [
        {
            "conduit:current_time": "2026-03-10 19:15:00 CET (UTC+01:00)",
            "user:conduit_location": "Zurich, Switzerland",
            "user:conduit_personal_instructions": "Prefer concise answers.",
        }
    ]


def test_websocket_turn_streams_failed_tool_result_and_done(tmp_path):
    app, runtime = _create_websocket_test_app(
        tmp_path=tmp_path,
        reply="The fetch was blocked, so I would try another source next.",
        tool_error="HTTP 403 Forbidden",
    )
    client = TestClient(app)

    with client.websocket_connect("/chat") as websocket:
        websocket.send_json(
            {
                "type": "text",
                "message_id": "m1",
                "content": "look up blocked page",
            }
        )
        events = _collect_events_until_terminal(websocket)

    tool_result_events = [
        event for event in events if event["type"] == "tool_result"
    ]
    assert len(tool_result_events) == 1
    assert tool_result_events[0]["tool_call_id"] == "tc_1"
    assert tool_result_events[0]["status"] == "failed"
    assert tool_result_events[0]["error"] == "HTTP 403 Forbidden"
    assert events[-1]["type"] == "done"
    assert runtime.iter_event_calls == 1


def test_websocket_turn_streams_bash_tool_result_response(tmp_path):
    app, runtime = _create_websocket_test_app(
        tmp_path=tmp_path,
        reply="Bash result was used.",
        tool_name="bash",
        tool_response={
            "ok": True,
            "stdout": "hello",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "duration_seconds": 0.01,
        },
    )
    client = TestClient(app)

    with client.websocket_connect("/chat") as websocket:
        websocket.send_json(
            {
                "type": "text",
                "message_id": "m1",
                "content": "run hello",
            }
        )
        events = _collect_events_until_terminal(websocket)

    tool_result_events = [
        event for event in events if event["type"] == "tool_result"
    ]
    assert len(tool_result_events) == 1
    assert tool_result_events[0]["tool"] == "bash"
    assert tool_result_events[0]["response"]["stdout"] == "hello"
    assert tool_result_events[0]["response"]["exit_code"] == 0
    assert events[-1]["type"] == "done"
    assert runtime.iter_event_calls == 1


def test_websocket_replays_completed_turn_for_duplicate_message_id(tmp_path):
    reply = "Replayable response from the fake runtime."
    app, runtime = _create_websocket_test_app(
        tmp_path=tmp_path,
        reply=reply,
    )
    client = TestClient(app)

    with client.websocket_connect("/chat") as websocket:
        websocket.send_json(
            {
                "type": "text",
                "message_id": "m1",
                "content": "openai homepage",
            }
        )
        first_events = _collect_events_until_terminal(websocket)

    session_id = first_events[0]["session_id"]

    with client.websocket_connect("/chat") as websocket:
        websocket.send_json(
            {
                "type": "text",
                "session_id": session_id,
                "message_id": "m1",
                "content": "openai homepage",
            }
        )
        replayed_events = _collect_events_until_terminal(websocket)

    assert replayed_events == first_events
    assert runtime.iter_event_calls == 1


@pytest.mark.anyio
async def test_websocket_duplicate_message_attaches_to_in_progress_turn(tmp_path):
    reply = "In-flight response that should not trigger a second runtime execution."
    runtime = FakeRuntime(
        db_path=str(tmp_path / "in-flight.db"),
        reply=reply,
        delay_seconds=0.2,
    )
    manager = WebSocketChatManager(runtime)
    queue_one = await manager.register_connection()
    queue_two = await manager.register_connection()

    await manager.handle_client_message(
        queue=queue_one,
        payload={
            "type": "text",
            "message_id": "m1",
            "content": "openai homepage",
        },
    )
    first_ack = await asyncio.wait_for(queue_one.get(), timeout=1.0)
    assert first_ack["type"] == "ack"

    await manager.handle_client_message(
        queue=queue_two,
        payload={
            "type": "text",
            "session_id": first_ack["session_id"],
            "message_id": "m1",
            "content": "openai homepage",
        },
    )

    events_two = await _collect_queue_events_until_terminal(queue_two)
    events_one = await _collect_queue_events_until_terminal(
        queue_one,
        initial_events=[first_ack],
    )
    await manager.unregister_connection(queue_one)
    await manager.unregister_connection(queue_two)

    assert runtime.iter_event_calls == 1
    assert events_two[0]["type"] == "ack"
    assert "".join(
        event["content"] for event in events_two if event["type"] == "token"
    ) == reply
    assert events_one[-1]["type"] == "done"


def test_websocket_approval_required_and_resume(tmp_path):
    app, runtime = _create_websocket_test_app(
        tmp_path=tmp_path,
        reply="unused",
        require_approval=True,
    )
    client = TestClient(app)

    with client.websocket_connect("/chat") as websocket:
        websocket.send_json(
            {
                "type": "text",
                "message_id": "m1",
                "content": "fetch example.com",
            }
        )
        initial_events = _collect_events_until_type(websocket, "approval_required")
        approval_event = initial_events[-1]

        websocket.send_json(
            {
                "type": "approval",
                "approval_id": approval_event["approval_id"],
                "decision": "approve",
            }
        )
        resumed_events = _collect_events_until_terminal(websocket)

    assert [event["type"] for event in initial_events] == [
        "ack",
        "tool_call",
        "approval_required",
    ]
    assert approval_event["tool"] == "web_fetch"
    assert approval_event["tool_call_id"] == "tc_1"
    assert any(event["type"] == "tool_result" for event in resumed_events)
    assert "".join(
        event["content"] for event in resumed_events if event["type"] == "token"
    ) == "Approved result."
    assert resumed_events[-1]["type"] == "done"
    assert runtime.iter_event_calls == 2


def _create_websocket_test_app(
    *,
    tmp_path,
    reply: str,
    thought_trace: str = "",
    delay_seconds: float = 0.0,
    require_approval: bool = False,
    tool_error: str | None = None,
    tool_name: str = "web_search",
    tool_response: dict[str, object] | None = None,
):
    app = create_app(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "http-app.db"),
        )
    )
    runtime = FakeRuntime(
        db_path=str(tmp_path / "websocket.db"),
        reply=reply,
        thought_trace=thought_trace,
        delay_seconds=delay_seconds,
        require_approval=require_approval,
        tool_error=tool_error,
        tool_name=tool_name,
        tool_response=tool_response,
    )
    app.state.runtime = runtime
    app.state.chat_manager = WebSocketChatManager(runtime)
    return app, runtime


def _collect_events_until_terminal(websocket, initial_events: list[dict] | None = None):
    events = list(initial_events or [])
    while not events or events[-1]["type"] not in {"done", "error"}:
        events.append(websocket.receive_json())
    return events


def _collect_events_until_type(websocket, target_type: str):
    events: list[dict] = []
    while not events or events[-1]["type"] != target_type:
        events.append(websocket.receive_json())
    return events


async def _collect_queue_events_until_terminal(
    queue,
    initial_events: list[dict] | None = None,
):
    events = list(initial_events or [])
    while not events or events[-1]["type"] not in {"done", "error"}:
        events.append(await asyncio.wait_for(queue.get(), timeout=1.0))
    return events


def _tool_call_event(*, tool_call_id: str, tool_name: str, args: dict[str, object]) -> Event:
    function_call = types.FunctionCall(
        id=tool_call_id,
        name=tool_name,
        args=args,
    )
    return Event(
        invocation_id="inv-test",
        author="conduit",
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=function_call)],
        ),
    )


def _tool_result_event(
    *,
    tool_call_id: str,
    tool_name: str,
    response: dict[str, object],
) -> Event:
    function_response = types.FunctionResponse(
        id=tool_call_id,
        name=tool_name,
        response=response,
    )
    return Event(
        invocation_id="inv-test",
        author=tool_name,
        content=types.Content(
            role="tool",
            parts=[types.Part(function_response=function_response)],
        ),
    )


def _approval_required_event() -> Event:
    function_call = types.FunctionCall(
        id="approval-1",
        name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
        args={
            "originalFunctionCall": {
                "id": "tc_1",
                "name": "web_fetch",
                "args": {"url": "https://example.com"},
            },
            "toolConfirmation": {
                "hint": "Run web_fetch(url='https://example.com').",
                "payload": {
                    "summary": "Run web_fetch(url='https://example.com').",
                },
            },
        },
    )
    return Event(
        invocation_id="inv-test",
        author="conduit",
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=function_call)],
        ),
    )


def _text_event(text: str) -> Event:
    return Event(
        invocation_id="inv-test",
        author="conduit",
        content=types.Content(
            role="model",
            parts=[types.Part(text=text)],
        ),
    )


def _thought_event(text: str) -> Event:
    return Event(
        invocation_id="inv-test",
        author="conduit",
        content=types.Content(
            role="model",
            parts=[types.Part(text=text, thought=True)],
        ),
    )


def _is_approval_response(content: types.Content) -> bool:
    if not content.parts:
        return False
    response = content.parts[0].function_response
    return (
        response is not None
        and response.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
    )
