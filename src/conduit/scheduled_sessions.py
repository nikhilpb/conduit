"""Scheduled session configuration and background execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from croniter import CroniterBadCronError
from croniter import croniter
import yaml

from conduit.agent import available_tool_names
from conduit.config import Settings
from conduit.model_registry import ModelRegistry
from conduit.tool_permissions import effective_tool_permission

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScheduledSessionDefinition:
    id: str
    schedule: str
    model_key: str
    seed_query: str
    allowed_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ScheduledSessionsConfig:
    timezone: str
    tzinfo: ZoneInfo
    sessions: tuple[ScheduledSessionDefinition, ...]


@dataclass(slots=True)
class _ScheduledJobState:
    definition: ScheduledSessionDefinition
    next_run_at: datetime


def load_scheduled_sessions_config(
    path_str: str | None,
    *,
    settings: Settings,
    model_registry: ModelRegistry,
) -> ScheduledSessionsConfig:
    """Load and validate scheduled session configuration from disk."""

    if not path_str:
        return _empty_config()

    path = Path(path_str)
    if not path.exists():
        return _empty_config()

    payload = yaml.safe_load(path.read_text()) or {}
    if not isinstance(payload, dict):
        raise ValueError("scheduled session config must be a mapping")

    raw_sessions = payload.get("sessions") or []
    if not isinstance(raw_sessions, list):
        raise ValueError("scheduled session config `sessions` must be a list")

    raw_timezone = str(payload.get("timezone") or "").strip()
    if raw_sessions and not raw_timezone:
        raise ValueError(
            "scheduled session config must include `timezone` when sessions are configured"
        )
    timezone = raw_timezone or "UTC"
    tzinfo = _load_timezone(timezone)
    available_tools = available_tool_names(settings)

    seen_ids: set[str] = set()
    sessions: list[ScheduledSessionDefinition] = []
    for index, raw_session in enumerate(raw_sessions):
        if not isinstance(raw_session, dict):
            raise ValueError(
                f"scheduled session entry at index {index} must be a mapping"
            )

        session_id = str(raw_session.get("id") or "").strip()
        if not session_id:
            raise ValueError(
                f"scheduled session entry at index {index} must include `id`"
            )
        if session_id in seen_ids:
            raise ValueError(f"scheduled session id `{session_id}` is duplicated")
        seen_ids.add(session_id)

        schedule = str(raw_session.get("schedule") or "").strip()
        if not schedule:
            raise ValueError(
                f"scheduled session `{session_id}` must include `schedule`"
            )
        _validate_schedule(session_id=session_id, schedule=schedule, tzinfo=tzinfo)

        model_key = str(raw_session.get("model_key") or "").strip()
        if not model_key:
            raise ValueError(
                f"scheduled session `{session_id}` must include `model_key`"
            )
        try:
            model_registry.get(model_key)
        except KeyError as exc:
            raise ValueError(exc.args[0]) from exc

        seed_query = str(raw_session.get("seed_query") or "").strip()
        if not seed_query:
            raise ValueError(
                f"scheduled session `{session_id}` must include `seed_query`"
            )

        raw_allowed_tools = raw_session.get("allowed_tools")
        if not isinstance(raw_allowed_tools, list):
            raise ValueError(
                f"scheduled session `{session_id}` must include `allowed_tools` as a list"
            )

        allowed_tools: list[str] = []
        for raw_tool_name in raw_allowed_tools:
            if not isinstance(raw_tool_name, str):
                raise ValueError(
                    f"scheduled session `{session_id}` allowed tool names must be strings"
                )
            tool_name = raw_tool_name.strip()
            if not tool_name:
                raise ValueError(
                    f"scheduled session `{session_id}` contains an empty tool name"
                )
            if tool_name == "bash":
                raise ValueError(
                    f"scheduled session `{session_id}` cannot allow the `bash` tool"
                )
            if tool_name not in available_tools:
                raise ValueError(
                    f"scheduled session `{session_id}` references unknown tool `{tool_name}`"
                )
            if effective_tool_permission(
                tool_name,
                permissions=settings.tool_permissions,
            ) == "ask":
                raise ValueError(
                    f"scheduled session `{session_id}` cannot allow `{tool_name}` "
                    "because it requires user confirmation"
                )
            if tool_name not in allowed_tools:
                allowed_tools.append(tool_name)

        sessions.append(
            ScheduledSessionDefinition(
                id=session_id,
                schedule=schedule,
                model_key=model_key,
                seed_query=seed_query,
                allowed_tools=tuple(allowed_tools),
            )
        )

    return ScheduledSessionsConfig(
        timezone=timezone,
        tzinfo=tzinfo,
        sessions=tuple(sessions),
    )


class ScheduledSessionService:
    """Poll for due scheduled sessions and persist their results."""

    def __init__(
        self,
        runtime,
        config: ScheduledSessionsConfig,
        *,
        poll_interval_seconds: float = 5.0,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.runtime = runtime
        self.config = config
        self.poll_interval_seconds = poll_interval_seconds
        self._now_provider = now_provider or datetime.now
        self._job_states: list[_ScheduledJobState] = []
        self._initialized = False
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def initialize(self, *, now: datetime | None = None) -> None:
        """Initialize the in-memory schedule cursor."""

        localized_now = self._coerce_timezone(now or self._now_provider())
        self._job_states = [
            _ScheduledJobState(
                definition=session,
                next_run_at=_next_occurrence(
                    session.schedule,
                    localized_now,
                ),
            )
            for session in self.config.sessions
        ]
        self._initialized = True

    async def start(self) -> None:
        """Start the background polling task."""

        if not self.config.sessions or self._task is not None:
            return

        self.initialize()
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background polling task."""

        if self._task is None:
            return

        self._stop_event.set()
        task = self._task
        self._task = None
        await task

    async def run_due_jobs(self, *, now: datetime | None = None) -> None:
        """Run any scheduled jobs due at or before `now`."""

        if not self.config.sessions:
            return
        if not self._initialized:
            self.initialize(now=now)

        current_time = self._coerce_timezone(now or self._now_provider())
        for job_state in self._job_states:
            if job_state.next_run_at > current_time:
                continue

            scheduled_for = job_state.next_run_at
            job_state.next_run_at = _next_occurrence(
                job_state.definition.schedule,
                current_time,
            )
            await self._run_job(
                definition=job_state.definition,
                scheduled_for=scheduled_for,
            )

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            await self.run_due_jobs()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def _run_job(
        self,
        *,
        definition: ScheduledSessionDefinition,
        scheduled_for: datetime,
    ) -> None:
        scheduled_for_key = scheduled_for.isoformat()
        claimed = await self.runtime.session_service.claim_scheduled_run(
            schedule_id=definition.id,
            scheduled_for=scheduled_for_key,
        )
        if not claimed:
            return

        try:
            session_id = await self.runtime.run_scheduled_session(
                schedule_id=definition.id,
                scheduled_for=scheduled_for,
                model_key=definition.model_key,
                seed_query=definition.seed_query,
                allowed_tools=definition.allowed_tools,
            )
        except Exception as exc:
            logger.exception(
                "scheduled session %s failed for %s",
                definition.id,
                scheduled_for_key,
            )
            await self.runtime.session_service.mark_scheduled_run_failed(
                schedule_id=definition.id,
                scheduled_for=scheduled_for_key,
                error_message=str(exc),
            )
            return

        await self.runtime.session_service.mark_scheduled_run_completed(
            schedule_id=definition.id,
            scheduled_for=scheduled_for_key,
            session_id=session_id,
        )

    def _coerce_timezone(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=self.config.tzinfo)
        return value.astimezone(self.config.tzinfo)


def _empty_config() -> ScheduledSessionsConfig:
    tzinfo = ZoneInfo("UTC")
    return ScheduledSessionsConfig(
        timezone="UTC",
        tzinfo=tzinfo,
        sessions=(),
    )


def _load_timezone(raw_timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(raw_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"scheduled session config timezone `{raw_timezone}` is invalid"
        ) from exc


def _validate_schedule(
    *,
    session_id: str,
    schedule: str,
    tzinfo: ZoneInfo,
) -> None:
    try:
        croniter(schedule, datetime.now(tzinfo))
    except (CroniterBadCronError, ValueError) as exc:
        raise ValueError(
            f"scheduled session `{session_id}` has invalid cron schedule `{schedule}`"
        ) from exc


def _next_occurrence(schedule: str, after: datetime) -> datetime:
    return croniter(schedule, after).get_next(datetime)
