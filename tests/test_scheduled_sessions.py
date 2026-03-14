import asyncio

import pytest

from conduit.config import Settings
from conduit.scheduled_sessions import ScheduledSessionDefinition
from conduit.scheduled_sessions import ScheduledSessionScheduler
from conduit.scheduled_sessions import load_scheduled_sessions


def test_load_scheduled_sessions_accepts_valid_config(tmp_path):
    config_path = tmp_path / "scheduled_sessions.yaml"
    config_path.write_text(
        """
scheduled_sessions:
  - id: morning-briefing
    schedule: "0 9 * * 1-5"
    model: gemini-3-flash-preview
    seed_query: Summarize the top news.
    allowed_tools:
      - web_search
      - web_fetch
"""
    )

    definitions = load_scheduled_sessions(
        str(config_path),
        settings=Settings(
            _env_file=None,
            google_api_key="google-test",
        ),
    )

    assert definitions == (
        ScheduledSessionDefinition(
            id="morning-briefing",
            schedule="0 9 * * 1-5",
            model="gemini-3-flash-preview",
            seed_query="Summarize the top news.",
            allowed_tools=("web_search", "web_fetch"),
        ),
    )


def test_load_scheduled_sessions_rejects_duplicate_ids(tmp_path):
    config_path = tmp_path / "scheduled_sessions.yaml"
    config_path.write_text(
        """
scheduled_sessions:
  - id: duplicate
    schedule: "0 9 * * *"
    model: gemini-3-flash-preview
    seed_query: First.
    allowed_tools: []
  - id: duplicate
    schedule: "0 10 * * *"
    model: gemini-3-flash-preview
    seed_query: Second.
    allowed_tools: []
"""
    )

    with pytest.raises(ValueError, match="duplicate scheduled session id"):
        load_scheduled_sessions(
            str(config_path),
            settings=Settings(_env_file=None, google_api_key="google-test"),
        )


def test_load_scheduled_sessions_rejects_invalid_cron(tmp_path):
    config_path = tmp_path / "scheduled_sessions.yaml"
    config_path.write_text(
        """
scheduled_sessions:
  - id: invalid-cron
    schedule: "invalid cron"
    model: gemini-3-flash-preview
    seed_query: Hi.
    allowed_tools: []
"""
    )

    with pytest.raises(ValueError, match="invalid cron schedule"):
        load_scheduled_sessions(
            str(config_path),
            settings=Settings(_env_file=None, google_api_key="google-test"),
        )


def test_load_scheduled_sessions_rejects_unknown_tools(tmp_path):
    config_path = tmp_path / "scheduled_sessions.yaml"
    config_path.write_text(
        """
scheduled_sessions:
  - id: unknown-tool
    schedule: "0 9 * * *"
    model: gemini-3-flash-preview
    seed_query: Hi.
    allowed_tools:
      - send_email
"""
    )

    with pytest.raises(ValueError, match="unknown tool"):
        load_scheduled_sessions(
            str(config_path),
            settings=Settings(_env_file=None, google_api_key="google-test"),
        )


def test_load_scheduled_sessions_rejects_missing_provider_credentials(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    config_path = tmp_path / "scheduled_sessions.yaml"
    config_path.write_text(
        """
scheduled_sessions:
  - id: no-creds
    schedule: "0 9 * * *"
    model: gemini-3-flash-preview
    seed_query: Hi.
    allowed_tools: []
"""
    )

    with pytest.raises(ValueError, match="requires configured google credentials"):
        load_scheduled_sessions(
            str(config_path),
            settings=Settings(_env_file=None),
        )


class _FakeScheduledRuntime:
    def __init__(self, *, delay_seconds: float = 0.0):
        self.calls: list[str] = []
        self.delay_seconds = delay_seconds

    async def run_scheduled_session(self, job_id: str) -> None:
        self.calls.append(job_id)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)


@pytest.mark.anyio
async def test_scheduled_session_scheduler_skips_overlapping_runs():
    runtime = _FakeScheduledRuntime(delay_seconds=0.05)
    scheduler = ScheduledSessionScheduler(
        runtime=runtime,
        definitions=(
            ScheduledSessionDefinition(
                id="daily-briefing",
                schedule="0 9 * * *",
                model="gemini-3-flash-preview",
                seed_query="Hi.",
                allowed_tools=("web_fetch",),
            ),
        ),
    )

    first_run = asyncio.create_task(scheduler.run_job("daily-briefing"))
    await asyncio.sleep(0)
    await scheduler.run_job("daily-briefing")
    await first_run

    assert runtime.calls == ["daily-briefing"]


@pytest.mark.anyio
async def test_scheduled_session_scheduler_does_not_catch_up_on_start():
    runtime = _FakeScheduledRuntime()
    scheduler = ScheduledSessionScheduler(
        runtime=runtime,
        definitions=(
            ScheduledSessionDefinition(
                id="yearly-briefing",
                schedule="0 0 1 1 *",
                model="gemini-3-flash-preview",
                seed_query="Hi.",
                allowed_tools=(),
            ),
        ),
    )

    await scheduler.start()
    await asyncio.sleep(0)
    await scheduler.shutdown()

    assert runtime.calls == []
