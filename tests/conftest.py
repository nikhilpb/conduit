import pytest


@pytest.fixture(autouse=True)
def _discard_default_scheduled_sessions_config(monkeypatch, tmp_path_factory):
    path = tmp_path_factory.mktemp("scheduled-config") / "scheduled_sessions.yaml"
    path.write_text("scheduled_sessions: []\n")
    monkeypatch.setenv("CONDUIT_SCHEDULED_SESSIONS_CONFIG_PATH", str(path))
