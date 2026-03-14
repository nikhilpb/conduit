"""SQLite-backed ADK session persistence for Conduit."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import time
from typing import Any
from typing import Optional
import uuid

from typing_extensions import override

from google.adk.errors.already_exists_error import AlreadyExistsError
from google.adk.events.event import Event
from google.adk.sessions import _session_util
from google.adk.sessions.base_session_service import BaseSessionService
from google.adk.sessions.base_session_service import GetSessionConfig
from google.adk.sessions.base_session_service import ListSessionsResponse
from google.adk.sessions.session import Session
from google.adk.sessions.state import State

from conduit.session_metadata import session_kind_from_state
from conduit.session_metadata import session_read_only_from_state


@dataclass(slots=True)
class ClientTurnRecord:
    session_id: str
    message_id: str
    turn_id: str
    assistant_message_id: str
    status: str
    reply: str
    tool_calls: list[dict[str, Any]]
    event_history: list[dict[str, Any]]
    error_message: str | None
    created_at: float
    updated_at: float


@dataclass(slots=True)
class SessionSummaryRecord:
    session_id: str
    last_update_time: float
    event_count: int
    title: str
    kind: str
    read_only: bool


class SQLiteSessionService(BaseSessionService):
    """Persist ADK sessions, state, and events in SQLite."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = asyncio.Lock()
        self._initialize_database()

    @override
    async def create_session(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Session:
        async with self._write_lock:
            return await asyncio.to_thread(
                self._create_session_sync,
                app_name=app_name,
                user_id=user_id,
                state=state,
                session_id=session_id,
            )

    @override
    async def get_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Optional[GetSessionConfig] = None,
    ) -> Optional[Session]:
        return await asyncio.to_thread(
            self._get_session_sync,
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            config=config,
        )

    @override
    async def list_sessions(
        self,
        *,
        app_name: str,
        user_id: Optional[str] = None,
    ) -> ListSessionsResponse:
        return await asyncio.to_thread(
            self._list_sessions_sync,
            app_name=app_name,
            user_id=user_id,
        )

    @override
    async def delete_session(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> None:
        async with self._write_lock:
            await asyncio.to_thread(
                self._delete_session_sync,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )

    @override
    async def append_event(self, session: Session, event: Event) -> Event:
        if event.partial:
            return event

        async with self._write_lock:
            event = await super().append_event(session=session, event=event)
            session.last_update_time = event.timestamp
            await asyncio.to_thread(
                self._append_event_sync,
                session=copy.deepcopy(session),
                event=copy.deepcopy(event),
            )
            return event

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    app_name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    last_update_time REAL NOT NULL,
                    PRIMARY KEY (app_name, user_id, session_id)
                );

                CREATE TABLE IF NOT EXISTS events (
                    app_name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_order INTEGER NOT NULL,
                    event_timestamp REAL NOT NULL,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (app_name, user_id, session_id, event_id),
                    FOREIGN KEY (app_name, user_id, session_id)
                        REFERENCES sessions(app_name, user_id, session_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_events_session_order
                ON events(app_name, user_id, session_id, event_order);

                CREATE TABLE IF NOT EXISTS app_state (
                    app_name TEXT NOT NULL,
                    state_key TEXT NOT NULL,
                    state_value_json TEXT NOT NULL,
                    PRIMARY KEY (app_name, state_key)
                );

                CREATE TABLE IF NOT EXISTS user_state (
                    app_name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    state_key TEXT NOT NULL,
                    state_value_json TEXT NOT NULL,
                    PRIMARY KEY (app_name, user_id, state_key)
                );

                CREATE TABLE IF NOT EXISTS client_turns (
                    app_name TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    assistant_message_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reply_text TEXT NOT NULL,
                    tool_calls_json TEXT NOT NULL,
                    event_history_json TEXT NOT NULL,
                    error_message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (app_name, user_id, session_id, message_id),
                    FOREIGN KEY (app_name, user_id, session_id)
                        REFERENCES sessions(app_name, user_id, session_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS scheduled_session_runs (
                    schedule_id TEXT NOT NULL,
                    scheduled_for TEXT NOT NULL,
                    status TEXT NOT NULL,
                    session_id TEXT,
                    error_message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (schedule_id, scheduled_for)
                );
                """
            )

    def _create_session_sync(
        self,
        *,
        app_name: str,
        user_id: str,
        state: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Session:
        normalized_session_id = (
            session_id.strip()
            if session_id and session_id.strip()
            else str(uuid.uuid4())
        )
        session_state: dict[str, Any] = {}
        now = time.time()

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT 1
                FROM sessions
                WHERE app_name = ? AND user_id = ? AND session_id = ?
                """,
                (app_name, user_id, normalized_session_id),
            ).fetchone()
            if existing is not None:
                raise AlreadyExistsError(
                    f"Session with id {normalized_session_id} already exists."
                )

            state_deltas = _session_util.extract_state_delta(state or {})
            self._write_app_state(connection, app_name, state_deltas["app"])
            self._write_user_state(
                connection,
                app_name,
                user_id,
                state_deltas["user"],
            )
            session_state = state_deltas["session"]
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
                (
                    app_name,
                    user_id,
                    normalized_session_id,
                    json.dumps(session_state),
                    now,
                ),
            )

        return Session(
            id=normalized_session_id,
            app_name=app_name,
            user_id=user_id,
            state=self._merge_state(
                app_name=app_name,
                user_id=user_id,
                session_state=session_state,
            ),
            last_update_time=now,
        )

    def _get_session_sync(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        config: Optional[GetSessionConfig] = None,
    ) -> Optional[Session]:
        with self._connect() as connection:
            session_row = connection.execute(
                """
                SELECT state_json, last_update_time
                FROM sessions
                WHERE app_name = ? AND user_id = ? AND session_id = ?
                """,
                (app_name, user_id, session_id),
            ).fetchone()
            if session_row is None:
                return None

            session_state = json.loads(session_row["state_json"])
            events = self._load_events(
                connection,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )

        if config is not None:
            if config.num_recent_events:
                events = events[-config.num_recent_events :]
            if config.after_timestamp:
                filtered_events: list[Event] = []
                for event in events:
                    if event.timestamp >= config.after_timestamp:
                        filtered_events.append(event)
                events = filtered_events

        return Session(
            id=session_id,
            app_name=app_name,
            user_id=user_id,
            state=self._merge_state(
                app_name=app_name,
                user_id=user_id,
                session_state=session_state,
            ),
            events=events,
            last_update_time=session_row["last_update_time"],
        )

    def _list_sessions_sync(
        self,
        *,
        app_name: str,
        user_id: Optional[str] = None,
    ) -> ListSessionsResponse:
        query = """
            SELECT user_id, session_id, state_json, last_update_time
            FROM sessions
            WHERE app_name = ?
        """
        params: list[Any] = [app_name]
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        query += " ORDER BY last_update_time DESC"

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()

            sessions = [
                Session(
                    id=row["session_id"],
                    app_name=app_name,
                    user_id=row["user_id"],
                    state=self._merge_state(
                        app_name=app_name,
                        user_id=row["user_id"],
                        session_state=json.loads(row["state_json"]),
                    ),
                    events=[],
                    last_update_time=row["last_update_time"],
                )
                for row in rows
            ]

        return ListSessionsResponse(sessions=sessions)

    def _delete_session_sync(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM sessions
                WHERE app_name = ? AND user_id = ? AND session_id = ?
                """,
                (app_name, user_id, session_id),
            )

    def _append_event_sync(self, *, session: Session, event: Event) -> None:
        event_order = max(len(session.events) - 1, 0)
        state_delta = (
            event.actions.state_delta
            if event.actions and event.actions.state_delta
            else {}
        )
        state_deltas = _session_util.extract_state_delta(state_delta)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO events (
                    app_name,
                    user_id,
                    session_id,
                    event_id,
                    event_order,
                    event_timestamp,
                    event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.app_name,
                    session.user_id,
                    session.id,
                    event.id,
                    event_order,
                    event.timestamp,
                    event.model_dump_json(exclude_none=True),
                ),
            )
            connection.execute(
                """
                UPDATE sessions
                SET state_json = ?, last_update_time = ?
                WHERE app_name = ? AND user_id = ? AND session_id = ?
                """,
                (
                    json.dumps(
                        {
                            key: value
                            for key, value in session.state.items()
                            if not key.startswith(State.APP_PREFIX)
                            and not key.startswith(State.USER_PREFIX)
                            and not key.startswith(State.TEMP_PREFIX)
                        }
                    ),
                    event.timestamp,
                    session.app_name,
                    session.user_id,
                    session.id,
                ),
            )
            self._write_app_state(connection, session.app_name, state_deltas["app"])
            self._write_user_state(
                connection,
                session.app_name,
                session.user_id,
                state_deltas["user"],
            )

    def _write_app_state(
        self,
        connection: sqlite3.Connection,
        app_name: str,
        state_delta: dict[str, Any],
    ) -> None:
        for key, value in state_delta.items():
            connection.execute(
                """
                INSERT INTO app_state (app_name, state_key, state_value_json)
                VALUES (?, ?, ?)
                ON CONFLICT(app_name, state_key)
                DO UPDATE SET state_value_json = excluded.state_value_json
                """,
                (app_name, key, json.dumps(value)),
            )

    def _write_user_state(
        self,
        connection: sqlite3.Connection,
        app_name: str,
        user_id: str,
        state_delta: dict[str, Any],
    ) -> None:
        for key, value in state_delta.items():
            connection.execute(
                """
                INSERT INTO user_state (app_name, user_id, state_key, state_value_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(app_name, user_id, state_key)
                DO UPDATE SET state_value_json = excluded.state_value_json
                """,
                (app_name, user_id, key, json.dumps(value)),
            )

    def _merge_state(
        self,
        *,
        app_name: str,
        user_id: str,
        session_state: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(session_state)

        with self._connect() as connection:
            app_rows = connection.execute(
                """
                SELECT state_key, state_value_json
                FROM app_state
                WHERE app_name = ?
                """,
                (app_name,),
            ).fetchall()
            user_rows = connection.execute(
                """
                SELECT state_key, state_value_json
                FROM user_state
                WHERE app_name = ? AND user_id = ?
                """,
                (app_name, user_id),
            ).fetchall()

        for row in app_rows:
            merged[State.APP_PREFIX + row["state_key"]] = json.loads(
                row["state_value_json"]
            )
        for row in user_rows:
            merged[State.USER_PREFIX + row["state_key"]] = json.loads(
                row["state_value_json"]
            )

        return merged

    def _load_events(
        self,
        connection: sqlite3.Connection,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> list[Event]:
        rows = connection.execute(
            """
            SELECT event_json
            FROM events
            WHERE app_name = ? AND user_id = ? AND session_id = ?
            ORDER BY event_order ASC
            """,
            (app_name, user_id, session_id),
        ).fetchall()
        return [
            Event.model_validate(json.loads(row["event_json"]))
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            timeout=30.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _get_session_summaries_sync(
        self,
        *,
        app_name: str,
        user_id: str,
    ) -> list[SessionSummaryRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    s.session_id,
                    s.state_json,
                    s.last_update_time,
                    COUNT(e.event_id) AS event_count
                FROM sessions AS s
                LEFT JOIN events AS e
                    ON e.app_name = s.app_name
                    AND e.user_id = s.user_id
                    AND e.session_id = s.session_id
                WHERE s.app_name = ? AND s.user_id = ?
                GROUP BY s.session_id, s.state_json, s.last_update_time
                ORDER BY s.last_update_time DESC
                """,
                (app_name, user_id),
            ).fetchall()

            summaries: list[SessionSummaryRecord] = []
            for row in rows:
                session_state = json.loads(row["state_json"])
                summaries.append(
                    SessionSummaryRecord(
                        session_id=row["session_id"],
                        last_update_time=row["last_update_time"],
                        event_count=int(row["event_count"] or 0),
                        title=self._load_session_title(
                            connection,
                            app_name=app_name,
                            user_id=user_id,
                            session_id=row["session_id"],
                        ),
                        kind=session_kind_from_state(session_state),
                        read_only=session_read_only_from_state(session_state),
                    )
                )

            return summaries

    def _load_session_title(
        self,
        connection: sqlite3.Connection,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> str:
        rows = connection.execute(
            """
            SELECT event_json
            FROM events
            WHERE app_name = ? AND user_id = ? AND session_id = ?
            ORDER BY event_order ASC
            """,
            (app_name, user_id, session_id),
        ).fetchall()

        for row in rows:
            title = _extract_title_from_event_payload(json.loads(row["event_json"]))
            if title:
                return title

        return f"Session {session_id[:8]}"

    async def get_client_turn(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> ClientTurnRecord | None:
        return await asyncio.to_thread(
            self._get_client_turn_sync,
            app_name=app_name,
            user_id=user_id,
            session_id=session_id,
            message_id=message_id,
        )

    async def save_client_turn_started(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        message_id: str,
        turn_id: str,
        assistant_message_id: str,
    ) -> None:
        async with self._write_lock:
            await asyncio.to_thread(
                self._save_client_turn_started_sync,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                turn_id=turn_id,
                assistant_message_id=assistant_message_id,
            )

    async def save_client_turn_completed(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        message_id: str,
        turn_id: str,
        assistant_message_id: str,
        reply: str,
        tool_calls: list[dict[str, Any]],
        event_history: list[dict[str, Any]],
    ) -> None:
        async with self._write_lock:
            await asyncio.to_thread(
                self._save_client_turn_completed_sync,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                turn_id=turn_id,
                assistant_message_id=assistant_message_id,
                reply=reply,
                tool_calls=tool_calls,
                event_history=event_history,
            )

    async def save_client_turn_failed(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        message_id: str,
        turn_id: str,
        assistant_message_id: str,
        error_message: str,
        event_history: list[dict[str, Any]],
    ) -> None:
        async with self._write_lock:
            await asyncio.to_thread(
                self._save_client_turn_failed_sync,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                turn_id=turn_id,
                assistant_message_id=assistant_message_id,
                error_message=error_message,
                event_history=event_history,
            )

    async def get_session_summaries(
        self,
        *,
        app_name: str,
        user_id: str,
    ) -> list[SessionSummaryRecord]:
        return await asyncio.to_thread(
            self._get_session_summaries_sync,
            app_name=app_name,
            user_id=user_id,
        )

    async def claim_scheduled_run(
        self,
        *,
        schedule_id: str,
        scheduled_for: str,
    ) -> bool:
        async with self._write_lock:
            return await asyncio.to_thread(
                self._claim_scheduled_run_sync,
                schedule_id=schedule_id,
                scheduled_for=scheduled_for,
            )

    async def mark_scheduled_run_completed(
        self,
        *,
        schedule_id: str,
        scheduled_for: str,
        session_id: str,
    ) -> None:
        async with self._write_lock:
            await asyncio.to_thread(
                self._mark_scheduled_run_completed_sync,
                schedule_id=schedule_id,
                scheduled_for=scheduled_for,
                session_id=session_id,
            )

    async def mark_scheduled_run_failed(
        self,
        *,
        schedule_id: str,
        scheduled_for: str,
        error_message: str,
    ) -> None:
        async with self._write_lock:
            await asyncio.to_thread(
                self._mark_scheduled_run_failed_sync,
                schedule_id=schedule_id,
                scheduled_for=scheduled_for,
                error_message=error_message,
            )

    def _get_client_turn_sync(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        message_id: str,
    ) -> ClientTurnRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM client_turns
                WHERE app_name = ? AND user_id = ? AND session_id = ? AND message_id = ?
                """,
                (app_name, user_id, session_id, message_id),
            ).fetchone()

        if row is None:
            return None

        return ClientTurnRecord(
            session_id=row["session_id"],
            message_id=row["message_id"],
            turn_id=row["turn_id"],
            assistant_message_id=row["assistant_message_id"],
            status=row["status"],
            reply=row["reply_text"],
            tool_calls=json.loads(row["tool_calls_json"]),
            event_history=json.loads(row["event_history_json"]),
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _claim_scheduled_run_sync(
        self,
        *,
        schedule_id: str,
        scheduled_for: str,
    ) -> bool:
        now = time.time()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT 1
                FROM scheduled_session_runs
                WHERE schedule_id = ? AND scheduled_for = ?
                """,
                (schedule_id, scheduled_for),
            ).fetchone()
            if existing is not None:
                return False

            connection.execute(
                """
                INSERT INTO scheduled_session_runs (
                    schedule_id,
                    scheduled_for,
                    status,
                    session_id,
                    error_message,
                    created_at,
                    updated_at
                ) VALUES (?, ?, 'running', NULL, NULL, ?, ?)
                """,
                (schedule_id, scheduled_for, now, now),
            )
        return True

    def _mark_scheduled_run_completed_sync(
        self,
        *,
        schedule_id: str,
        scheduled_for: str,
        session_id: str,
    ) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scheduled_session_runs
                SET status = 'completed',
                    session_id = ?,
                    error_message = NULL,
                    updated_at = ?
                WHERE schedule_id = ? AND scheduled_for = ?
                """,
                (session_id, now, schedule_id, scheduled_for),
            )

    def _mark_scheduled_run_failed_sync(
        self,
        *,
        schedule_id: str,
        scheduled_for: str,
        error_message: str,
    ) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE scheduled_session_runs
                SET status = 'failed',
                    error_message = ?,
                    updated_at = ?
                WHERE schedule_id = ? AND scheduled_for = ?
                """,
                (error_message, now, schedule_id, scheduled_for),
            )

    def _save_client_turn_started_sync(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        message_id: str,
        turn_id: str,
        assistant_message_id: str,
    ) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO client_turns (
                    app_name,
                    user_id,
                    session_id,
                    message_id,
                    turn_id,
                    assistant_message_id,
                    status,
                    reply_text,
                    tool_calls_json,
                    event_history_json,
                    error_message,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'in_progress', '', '[]', '[]', NULL, ?, ?)
                ON CONFLICT(app_name, user_id, session_id, message_id)
                DO UPDATE SET
                    turn_id = excluded.turn_id,
                    assistant_message_id = excluded.assistant_message_id,
                    status = 'in_progress',
                    reply_text = '',
                    tool_calls_json = '[]',
                    event_history_json = '[]',
                    error_message = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    app_name,
                    user_id,
                    session_id,
                    message_id,
                    turn_id,
                    assistant_message_id,
                    now,
                    now,
                ),
            )

    def _save_client_turn_completed_sync(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        message_id: str,
        turn_id: str,
        assistant_message_id: str,
        reply: str,
        tool_calls: list[dict[str, Any]],
        event_history: list[dict[str, Any]],
    ) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE client_turns
                SET
                    turn_id = ?,
                    assistant_message_id = ?,
                    status = 'completed',
                    reply_text = ?,
                    tool_calls_json = ?,
                    event_history_json = ?,
                    error_message = NULL,
                    updated_at = ?
                WHERE app_name = ? AND user_id = ? AND session_id = ? AND message_id = ?
                """,
                (
                    turn_id,
                    assistant_message_id,
                    reply,
                    json.dumps(tool_calls),
                    json.dumps(event_history),
                    now,
                    app_name,
                    user_id,
                    session_id,
                    message_id,
                ),
            )

    def _save_client_turn_failed_sync(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        message_id: str,
        turn_id: str,
        assistant_message_id: str,
        error_message: str,
        event_history: list[dict[str, Any]],
    ) -> None:
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE client_turns
                SET
                    turn_id = ?,
                    assistant_message_id = ?,
                    status = 'failed',
                    event_history_json = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE app_name = ? AND user_id = ? AND session_id = ? AND message_id = ?
                """,
                (
                    turn_id,
                    assistant_message_id,
                    json.dumps(event_history),
                    error_message,
                    now,
                    app_name,
                    user_id,
                    session_id,
                    message_id,
                ),
            )


def _extract_title_from_event_payload(payload: dict[str, Any]) -> str | None:
    if payload.get("author") != "user":
        return None

    content = payload.get("content") or {}
    parts = content.get("parts") or []
    text_parts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = str(part.get("text") or "").strip()
        if not text or part.get("thought"):
            continue
        text_parts.append(text)

    if not text_parts:
        return None

    title = " ".join(text_parts).replace("\n", " ").strip()
    return " ".join(title.split()) or None
