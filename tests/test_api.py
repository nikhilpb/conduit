from fastapi.testclient import TestClient
from google.adk.events.event import Event
from google.genai import types

from conduit.config import Settings
from conduit.main import _build_transcript
from conduit.main import create_app


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

    detail_response = client.get(f"/sessions/{session_id}")
    assert detail_response.status_code == 200
    assert detail_response.json() == {
        "session_id": session_id,
        "messages": [],
    }


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
