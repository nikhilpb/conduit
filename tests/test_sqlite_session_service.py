from google.adk.events.event import Event
from google.adk.sessions.base_session_service import GetSessionConfig
from google.adk.sessions.state import State
from google.genai import types
import pytest
import sqlite3

from conduit.sessions import SQLiteSessionService


@pytest.mark.anyio
async def test_sqlite_session_service_persists_sessions_and_events(tmp_path):
    db_path = tmp_path / "conduit.db"

    service = SQLiteSessionService(str(db_path))
    session = await service.create_session(
        app_name="conduit",
        user_id="single-user",
        state={
            "topic": "research",
            State.APP_PREFIX + "theme": "amber",
            State.USER_PREFIX + "timezone": "Europe/Zurich",
        },
        session_id="session-1",
    )

    event = Event(
        invocation_id="inv-1",
        author="user",
        content=types.Content(
            role="user",
            parts=[types.Part(text="hello world")],
        ),
    )
    await service.append_event(session, event)

    restarted_service = SQLiteSessionService(str(db_path))
    restored_session = await restarted_service.get_session(
        app_name="conduit",
        user_id="single-user",
        session_id="session-1",
    )

    assert restored_session is not None
    assert restored_session.id == "session-1"
    assert restored_session.state["topic"] == "research"
    assert restored_session.state["app:theme"] == "amber"
    assert restored_session.state["user:timezone"] == "Europe/Zurich"
    assert [item.content.parts[0].text for item in restored_session.events] == [
        "hello world"
    ]


@pytest.mark.anyio
async def test_sqlite_session_service_respects_recent_event_filter(tmp_path):
    db_path = tmp_path / "conduit.db"
    service = SQLiteSessionService(str(db_path))
    session = await service.create_session(
        app_name="conduit",
        user_id="single-user",
        session_id="session-2",
    )

    for index in range(3):
        await service.append_event(
            session,
            Event(
                invocation_id=f"inv-{index}",
                author="user",
                content=types.Content(
                    role="user",
                    parts=[types.Part(text=f"message-{index}")],
                ),
            ),
        )

    restored_session = await service.get_session(
        app_name="conduit",
        user_id="single-user",
        session_id="session-2",
        config=GetSessionConfig(num_recent_events=2),
    )

    assert restored_session is not None
    assert [item.content.parts[0].text for item in restored_session.events] == [
        "message-1",
        "message-2",
    ]


@pytest.mark.anyio
async def test_sqlite_session_service_persists_scheduled_session_metadata(tmp_path):
    db_path = tmp_path / "conduit.db"
    service = SQLiteSessionService(str(db_path))

    session = await service.create_session(
        app_name="conduit",
        user_id="single-user",
        session_id="scheduled-1",
        session_kind="scheduled",
        scheduled_job_id="daily-briefing",
    )
    metadata = await service.get_session_metadata(
        app_name="conduit",
        user_id="single-user",
        session_id=session.id,
    )
    summaries = await service.get_session_summaries(
        app_name="conduit",
        user_id="single-user",
    )

    assert metadata is not None
    assert metadata.session_kind == "scheduled"
    assert metadata.scheduled_job_id == "daily-briefing"
    assert summaries == [
        summaries[0].__class__(
            session_id="scheduled-1",
            last_update_time=summaries[0].last_update_time,
            event_count=0,
            title="Session schedule",
            session_kind="scheduled",
            scheduled_job_id="daily-briefing",
        )
    ]


def test_sqlite_session_service_migrates_existing_sessions_table(tmp_path):
    db_path = tmp_path / "conduit.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE sessions (
                app_name TEXT NOT NULL,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                state_json TEXT NOT NULL,
                last_update_time REAL NOT NULL,
                PRIMARY KEY (app_name, user_id, session_id)
            );
            """
        )
        connection.execute(
            """
            INSERT INTO sessions (
                app_name,
                user_id,
                session_id,
                state_json,
                last_update_time
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("conduit", "single-user", "legacy-session", "{}", 1.0),
        )

    service = SQLiteSessionService(str(db_path))

    metadata = service._get_session_metadata_sync(  # noqa: SLF001
        app_name="conduit",
        user_id="single-user",
        session_id="legacy-session",
    )

    assert metadata is not None
    assert metadata.session_kind == "interactive"
    assert metadata.scheduled_job_id is None
