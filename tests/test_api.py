import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
from google.adk.events.event import Event
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from conduit.agent import build_root_agent
from conduit.config import Settings
from conduit.main import _build_transcript
from conduit.main import create_app
from conduit.runtime import TurnResult
from conduit.user_context import CURRENT_TIME_STATE_KEY
from conduit.user_context import LOCATION_STATE_KEY
from conduit.user_context import PERSONAL_INSTRUCTIONS_STATE_KEY


def test_health_reports_runtime_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    app = create_app(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "conduit.db"),
            models_config_path=str(tmp_path / "models.yaml"),
        )
    )
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["model"] == "claude-sonnet-4-6"
    assert response.json()["model_label"] == "Claude Sonnet 4.6"
    assert response.json()["provider"] == "anthropic"
    assert response.json()["provider_api_key_configured"] is False


def test_create_and_list_sessions(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "conduit.db"),
            models_config_path=str(tmp_path / "models.yaml"),
        )
    )
    client = TestClient(app)

    create_response = client.post("/sessions")
    assert create_response.status_code == 201

    session_id = create_response.json()["session_id"]
    list_response = client.get("/sessions")

    assert list_response.status_code == 200
    assert list_response.json()["sessions"][0]["session_id"] == session_id
    assert list_response.json()["sessions"][0]["event_count"] == 0
    assert list_response.json()["sessions"][0]["title"] == f"Session {session_id[:8]}"

    detail_response = client.get(f"/sessions/{session_id}")
    assert detail_response.status_code == 200
    assert detail_response.json() == {
        "session_id": session_id,
        "messages": [],
    }


def test_list_sessions_uses_first_user_message_as_title(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "conduit.db"),
            models_config_path=str(tmp_path / "models.yaml"),
        )
    )
    runtime = app.state.runtime
    session = runtime.session_service._create_session_sync(  # noqa: SLF001
        app_name=runtime.settings.app_name,
        user_id=runtime.settings.internal_user_id,
        session_id="session-1",
    )
    runtime.session_service._append_event_sync(  # noqa: SLF001
        session=session,
        event=Event(
            invocation_id="inv-user",
            author="user",
            content=types.Content(
                role="user",
                parts=[types.Part(text="Tell me about Swiss trains in Zurich.")],
            ),
        ),
    )
    runtime.session_service._append_event_sync(  # noqa: SLF001
        session=session,
        event=Event(
            invocation_id="inv-model",
            author="conduit",
            content=types.Content(
                role="model",
                parts=[types.Part(text="Here is an answer.")],
            ),
        ),
    )
    client = TestClient(app)

    response = client.get("/sessions")

    assert response.status_code == 200
    assert response.json()["sessions"] == [
        {
            "session_id": "session-1",
            "last_update_time": response.json()["sessions"][0]["last_update_time"],
            "event_count": 2,
            "title": "Tell me about Swiss trains in Zurich.",
        }
    ]


def test_model_settings_can_be_listed_and_updated(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "conduit.db"),
            models_config_path=str(tmp_path / "models.yaml"),
            anthropic_api_key="anthropic-test",
            google_api_key="google-test",
        )
    )
    client = TestClient(app)

    list_response = client.get("/settings/model")

    assert list_response.status_code == 200
    assert list_response.json()["active_key"] == "claude_sonnet_4_6"
    assert {
        option["key"]: option["available"]
        for option in list_response.json()["options"]
    } == {
        "claude_opus_4_6": True,
        "claude_sonnet_4_6": True,
        "gemini_3_flash": True,
        "gemini_3_1_pro": True,
    }

    update_response = client.put(
        "/settings/model",
        json={"model_key": "gemini_3_flash"},
    )

    assert update_response.status_code == 200
    assert update_response.json()["active_key"] == "gemini_3_flash"
    assert update_response.json()["active_model"] == "gemini-3-flash-preview"
    assert update_response.json()["provider"] == "google"

    health_response = client.get("/health")
    assert health_response.status_code == 200
    assert health_response.json()["model"] == "gemini-3-flash-preview"
    assert health_response.json()["model_label"] == "Gemini 3 Flash"
    assert health_response.json()["provider"] == "google"
    assert health_response.json()["provider_api_key_configured"] is True


def test_chat_passes_turn_context_into_runtime_state(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "conduit.db"),
            models_config_path=str(tmp_path / "models.yaml"),
        )
    )
    captured: dict[str, object] = {}

    async def fake_run_turn(*, message: str, session_id: str | None = None, state_delta=None):
        captured["message"] = message
        captured["session_id"] = session_id
        captured["state_delta"] = dict(state_delta or {})
        return TurnResult(
            session_id=session_id or "session-1",
            reply="ok",
            tool_calls=[],
        )

    app.state.runtime.run_turn = fake_run_turn  # type: ignore[method-assign]
    client = TestClient(app)

    response = client.post(
        "/chat",
        json={
            "session_id": "session-1",
            "message": "What should I do next?",
            "context": {
                "current_time": "2026-03-10 20:00:00 CET (UTC+01:00)",
                "location": "Zurich, Switzerland",
                "personal_instructions": "Keep the answer short.",
            },
        },
    )

    assert response.status_code == 200
    assert captured == {
        "message": "What should I do next?",
        "session_id": "session-1",
        "state_delta": {
            CURRENT_TIME_STATE_KEY: "2026-03-10 20:00:00 CET (UTC+01:00)",
            LOCATION_STATE_KEY: "Zurich, Switzerland",
            PERSONAL_INSTRUCTIONS_STATE_KEY: "Keep the answer short.",
        },
    }
    assert response.json()["reply"] == "ok"


def test_build_transcript_includes_thinking_trace():
    events = [
        Event(
            invocation_id="inv-test",
            author="conduit",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(text="Plan the search.", thought=True),
                    types.Part(text="Final answer."),
                ],
            ),
        )
    ]

    transcript = _build_transcript(events)

    assert len(transcript) == 1
    assert transcript[0].role == "assistant"
    assert transcript[0].text == "Final answer."
    assert transcript[0].thinking_trace == "Plan the search."
    assert transcript[0].tool_calls == []


def test_before_model_callback_accepts_keyword_callback_context():
    agent = build_root_agent(
        Settings(_env_file=None),
        model_name="claude-sonnet-4-6",
    )
    request = LlmRequest()

    asyncio.run(
        agent.before_model_callback(
            callback_context=SimpleNamespace(
                state={
                    CURRENT_TIME_STATE_KEY: "2026-03-10 21:57:00 CET (UTC+01:00)",
                    LOCATION_STATE_KEY: "Zurich, Switzerland",
                    PERSONAL_INSTRUCTIONS_STATE_KEY: "Keep answers brief.",
                }
            ),
            llm_request=request,
        )
    )

    system_instruction = request.config.system_instruction
    assert isinstance(system_instruction, str)
    text = system_instruction
    assert "Current local time for the user" in text
    assert "Zurich, Switzerland" in text
    assert "Keep answers brief." in text
