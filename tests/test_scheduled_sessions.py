from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from conduit.agent import build_root_agent
from conduit.config import Settings
from conduit.model_registry import load_model_registry
from conduit.runtime import ConduitRuntime
from conduit.runtime import RuntimeTurnUpdate
from conduit.scheduled_sessions import load_scheduled_sessions_config
from conduit.scheduled_sessions import ScheduledSessionService
from conduit.session_metadata import session_kind_from_state
from conduit.session_metadata import session_read_only_from_state


def test_load_scheduled_sessions_config_parses_valid_entries(tmp_path):
    config_path = tmp_path / "scheduled_sessions.yaml"
    config_path.write_text(
        """
timezone: Europe/Zurich
sessions:
  - id: daily-brief
    schedule: "0 8 * * *"
    model_key: claude_sonnet_4_6
    seed_query: Summarize the morning.
    allowed_tools:
      - web_search
      - web_fetch
"""
    )
    settings = Settings(_env_file=None, models_config_path=str(tmp_path / "models.yaml"))
    registry = load_model_registry(
        settings.models_config_path,
        fallback_model=settings.model,
    )

    config = load_scheduled_sessions_config(
        str(config_path),
        settings=settings,
        model_registry=registry,
    )

    assert config.timezone == "Europe/Zurich"
    assert config.tzinfo == ZoneInfo("Europe/Zurich")
    assert len(config.sessions) == 1
    assert config.sessions[0].id == "daily-brief"
    assert config.sessions[0].allowed_tools == ("web_search", "web_fetch")


@pytest.mark.parametrize(
    ("config_text", "tool_config_text", "error_match"),
    [
        (
            """
timezone: Europe/Zurich
sessions:
  - id: daily-brief
    schedule: "0 8 * * *"
    model_key: claude_sonnet_4_6
    seed_query: Summarize the morning.
""",
            None,
            "must include `allowed_tools` as a list",
        ),
        (
            """
timezone: Europe/Zurich
sessions:
  - id: daily-brief
    schedule: "0 8 * * *"
    model_key: claude_sonnet_4_6
    seed_query: Summarize the morning.
    allowed_tools:
      - unknown_tool
""",
            None,
            "references unknown tool `unknown_tool`",
        ),
        (
            """
timezone: Europe/Zurich
sessions:
  - id: daily-brief
    schedule: "0 8 * * *"
    model_key: claude_sonnet_4_6
    seed_query: Summarize the morning.
    allowed_tools:
      - bash
""",
            None,
            "cannot allow the `bash` tool",
        ),
        (
            """
timezone: Europe/Zurich
sessions:
  - id: daily-brief
    schedule: "0 8 * * *"
    model_key: claude_sonnet_4_6
    seed_query: Summarize the morning.
    allowed_tools:
      - web_search
""",
            """
tools:
  web_search: ask
""",
            "requires user confirmation",
        ),
        (
            """
timezone: Europe/Zurich
sessions:
  - id: daily-brief
    schedule: "0 8 * * *"
    model_key: missing_model
    seed_query: Summarize the morning.
    allowed_tools:
      - web_search
""",
            None,
            "Unknown model key: missing_model",
        ),
    ],
)
def test_load_scheduled_sessions_config_rejects_invalid_entries(
    tmp_path,
    config_text,
    tool_config_text,
    error_match,
):
    config_path = tmp_path / "scheduled_sessions.yaml"
    config_path.write_text(config_text)
    settings_kwargs: dict[str, object] = {
        "_env_file": None,
        "models_config_path": str(tmp_path / "models.yaml"),
    }
    if tool_config_text is not None:
        tool_config_path = tmp_path / "tools.yaml"
        tool_config_path.write_text(tool_config_text)
        settings_kwargs["tool_permissions_path"] = str(tool_config_path)
    settings = Settings(**settings_kwargs)
    registry = load_model_registry(
        settings.models_config_path,
        fallback_model=settings.model,
    )

    with pytest.raises(ValueError, match=error_match):
        load_scheduled_sessions_config(
            str(config_path),
            settings=settings,
            model_registry=registry,
        )


def test_build_root_agent_keeps_globally_denied_tools_unusable(tmp_path):
    tool_config_path = tmp_path / "tools.yaml"
    tool_config_path.write_text(
        """
tools:
  web_search: deny
"""
    )
    agent = build_root_agent(
        Settings(
            _env_file=None,
            tool_permissions_path=str(tool_config_path),
        ),
        model_name="claude-sonnet-4-6",
        allowed_tools={"web_search"},
    )

    tool_response = asyncio.run(
        agent.before_tool_callback(
            tool=SimpleNamespace(name="web_search"),
            args={"query": "hello"},
            tool_context=SimpleNamespace(tool_confirmation=None),
        )
    )

    assert tool_response == {
        "error": "Tool `web_search` is disabled by server policy."
    }


@pytest.mark.anyio
async def test_runtime_run_scheduled_session_persists_read_only_transcript(tmp_path):
    runtime = ConduitRuntime(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "conduit.db"),
            models_config_path=str(tmp_path / "models.yaml"),
            anthropic_api_key="anthropic-test",
        )
    )

    async def fake_stream_turn(*, session, message, state_delta=None, runner=None):
        del session, message, state_delta, runner
        yield RuntimeTurnUpdate(kind="reply", text="Automated answer")

    runtime.stream_turn = fake_stream_turn  # type: ignore[method-assign]

    session_id = await runtime.run_scheduled_session(
        schedule_id="daily-brief",
        scheduled_for=datetime(2026, 3, 14, 8, 0, tzinfo=ZoneInfo("Europe/Zurich")),
        model_key="claude_sonnet_4_6",
        seed_query="What changed overnight?",
        allowed_tools=("web_search", "web_fetch"),
    )

    session = await runtime.get_session(session_id)
    assert session is not None
    assert session_kind_from_state(session.state) == "scheduled"
    assert session_read_only_from_state(session.state) is True
    assert [event.content.parts[0].text for event in session.events] == [
        "What changed overnight?",
        "Automated answer",
    ]

    summaries = await runtime.session_service.get_session_summaries(
        app_name=runtime.settings.app_name,
        user_id=runtime.settings.internal_user_id,
    )
    assert [
        (summary.session_id, summary.event_count, summary.kind, summary.read_only)
        for summary in summaries
    ] == [(session_id, 2, "scheduled", True)]


@pytest.mark.anyio
async def test_scheduled_session_service_deduplicates_runs_across_restarts(tmp_path):
    runtime = ConduitRuntime(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "conduit.db"),
            models_config_path=str(tmp_path / "models.yaml"),
            anthropic_api_key="anthropic-test",
        )
    )

    async def fake_stream_turn(*, session, message, state_delta=None, runner=None):
        del session, message, state_delta, runner
        yield RuntimeTurnUpdate(kind="reply", text="Automated answer")

    runtime.stream_turn = fake_stream_turn  # type: ignore[method-assign]

    config_path = tmp_path / "scheduled_sessions.yaml"
    config_path.write_text(
        """
timezone: Europe/Zurich
sessions:
  - id: minute-brief
    schedule: "1 * * * *"
    model_key: claude_sonnet_4_6
    seed_query: What changed?
    allowed_tools:
      - web_search
"""
    )
    config = load_scheduled_sessions_config(
        str(config_path),
        settings=runtime.settings,
        model_registry=runtime.model_registry,
    )
    first_service = ScheduledSessionService(runtime, config)
    second_service = ScheduledSessionService(runtime, config)
    initial_now = datetime(2026, 3, 14, 8, 0, tzinfo=ZoneInfo("Europe/Zurich"))
    due_now = datetime(2026, 3, 14, 8, 1, tzinfo=ZoneInfo("Europe/Zurich"))

    first_service.initialize(now=initial_now)
    await first_service.run_due_jobs(now=due_now)

    second_service.initialize(now=initial_now)
    await second_service.run_due_jobs(now=due_now)

    summaries = await runtime.session_service.get_session_summaries(
        app_name=runtime.settings.app_name,
        user_id=runtime.settings.internal_user_id,
    )
    assert len(summaries) == 1
    assert summaries[0].title == "What changed?"


@pytest.mark.anyio
async def test_scheduled_session_service_skips_missed_runs_on_startup(tmp_path):
    runtime = ConduitRuntime(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "conduit.db"),
            models_config_path=str(tmp_path / "models.yaml"),
            anthropic_api_key="anthropic-test",
        )
    )

    async def fake_stream_turn(*, session, message, state_delta=None, runner=None):
        del session, message, state_delta, runner
        yield RuntimeTurnUpdate(kind="reply", text="Automated answer")

    runtime.stream_turn = fake_stream_turn  # type: ignore[method-assign]

    config_path = tmp_path / "scheduled_sessions.yaml"
    config_path.write_text(
        """
timezone: Europe/Zurich
sessions:
  - id: morning-brief
    schedule: "0 8 * * *"
    model_key: claude_sonnet_4_6
    seed_query: What changed?
    allowed_tools:
      - web_search
"""
    )
    config = load_scheduled_sessions_config(
        str(config_path),
        settings=runtime.settings,
        model_registry=runtime.model_registry,
    )
    service = ScheduledSessionService(runtime, config)
    startup_now = datetime(2026, 3, 14, 8, 5, tzinfo=ZoneInfo("Europe/Zurich"))

    service.initialize(now=startup_now)
    await service.run_due_jobs(now=startup_now)

    summaries = await runtime.session_service.get_session_summaries(
        app_name=runtime.settings.app_name,
        user_id=runtime.settings.internal_user_id,
    )
    assert summaries == []
