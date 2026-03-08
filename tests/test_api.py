from fastapi.testclient import TestClient

from conduit.config import Settings
from conduit.main import create_app


def test_health_reports_runtime_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    app = create_app(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "conduit.db"),
        )
    )
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["model"] == "claude-sonnet-4-6"
    assert response.json()["provider"] == "anthropic"
    assert response.json()["provider_api_key_configured"] is False


def test_create_and_list_sessions(tmp_path):
    app = create_app(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "conduit.db"),
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
