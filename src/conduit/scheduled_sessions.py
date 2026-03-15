"""Scheduled-session config loading and runtime orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from datetime import tzinfo
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import yaml

from conduit.agent import list_available_tool_names
from conduit.config import Settings
from conduit.model_registry import infer_provider
from conduit.notification_hub import NotificationEvent
from conduit.notification_hub import NotificationHub

if TYPE_CHECKING:
    from conduit.runtime import ConduitRuntime


logger = logging.getLogger(__name__)
SCHEDULED_SESSIONS_TIMEZONE = UTC


@dataclass(frozen=True, slots=True)
class ScheduledSessionDefinition:
    id: str
    schedule: str
    model: str
    seed_query: str
    allowed_tools: tuple[str, ...]


def scheduled_sessions_timezone() -> tzinfo:
    """Return the fixed timezone used for scheduled sessions."""

    return SCHEDULED_SESSIONS_TIMEZONE


def load_scheduled_sessions(
    config_path: str | None,
    *,
    settings: Settings,
) -> tuple[ScheduledSessionDefinition, ...]:
    """Load and validate scheduled session definitions from disk."""

    if not config_path:
        return ()

    path = Path(config_path)
    if not path.exists():
        return ()

    payload = yaml.safe_load(path.read_text()) or {}
    raw_definitions = payload.get("scheduled_sessions", payload)
    if raw_definitions is None:
        return ()
    if not isinstance(raw_definitions, list):
        raise ValueError("scheduled session config must define a list of sessions")

    available_tool_names = set(list_available_tool_names(settings))
    timezone = scheduled_sessions_timezone()
    definitions: list[ScheduledSessionDefinition] = []
    seen_ids: set[str] = set()
    logger.info("Loading scheduled sessions from %s", config_path)

    for index, raw_definition in enumerate(raw_definitions, start=1):
        if not isinstance(raw_definition, dict):
            raise ValueError(
                f"scheduled session entry #{index} must be a mapping"
            )

        session_id = _require_text(raw_definition, "id", entry_index=index)
        if session_id in seen_ids:
            raise ValueError(f"duplicate scheduled session id: {session_id}")
        seen_ids.add(session_id)

        schedule = _require_text(raw_definition, "schedule", entry_index=index)
        try:
            CronTrigger.from_crontab(schedule, timezone=timezone)
        except ValueError as exc:
            raise ValueError(
                f"scheduled session {session_id!r} has an invalid cron schedule: {exc}"
            ) from exc

        model = _require_text(raw_definition, "model", entry_index=index)
        provider = infer_provider(model)
        if provider not in {"anthropic", "google"}:
            raise ValueError(
                f"scheduled session {session_id!r} uses an unsupported model: {model}"
            )
        if not settings.provider_api_key_configured_for(provider):
            raise ValueError(
                f"scheduled session {session_id!r} requires configured {provider} credentials"
            )

        seed_query = _require_text(raw_definition, "seed_query", entry_index=index)
        allowed_tools = _load_allowed_tools(
            raw_definition,
            entry_index=index,
            session_id=session_id,
            available_tool_names=available_tool_names,
        )
        definitions.append(
            ScheduledSessionDefinition(
                id=session_id,
                schedule=schedule,
                model=model,
                seed_query=seed_query,
                allowed_tools=allowed_tools,
            )
        )

    logger.info(
        "Loaded %d scheduled session(s): %s",
        len(definitions),
        ", ".join(d.id for d in definitions) or "(none)",
    )
    return tuple(definitions)


class ScheduledSessionScheduler:
    """Manage in-process scheduled session execution."""

    def __init__(
        self,
        *,
        runtime: ConduitRuntime,
        definitions: tuple[ScheduledSessionDefinition, ...],
        notification_hub: NotificationHub | None = None,
    ) -> None:
        self.runtime = runtime
        self.definitions = {definition.id: definition for definition in definitions}
        self.notification_hub = notification_hub
        self.timezone = scheduled_sessions_timezone()
        self._scheduler = AsyncIOScheduler(
            timezone=self.timezone,
            job_defaults={"coalesce": False},
        )
        self._started = False
        self._state_lock = asyncio.Lock()
        self._running_jobs: set[str] = set()

    async def start(self) -> None:
        """Register jobs and start the scheduler."""

        if self._started:
            return

        for definition in self.definitions.values():
            trigger = CronTrigger.from_crontab(
                definition.schedule,
                timezone=self.timezone,
            )
            self._scheduler.add_job(
                self.run_job,
                trigger=trigger,
                id=definition.id,
                kwargs={"job_id": definition.id},
                replace_existing=True,
                max_instances=1,
            )
            logger.info(
                "Registered scheduled job %r (schedule=%r, model=%s, next_run=%s)",
                definition.id,
                definition.schedule,
                definition.model,
                trigger.get_next_fire_time(None, datetime.now(self.timezone)),
            )

        self._scheduler.start()
        self._started = True
        logger.info("Scheduled session scheduler started with %d job(s).", len(self.definitions))

    async def shutdown(self) -> None:
        """Stop the scheduler."""

        if not self._started:
            return

        logger.info("Shutting down scheduled session scheduler.")
        self._scheduler.shutdown(wait=False)
        self._started = False

    async def run_job(self, job_id: str) -> None:
        """Execute one scheduled session definition immediately."""

        if job_id not in self.definitions:
            raise KeyError(f"unknown scheduled session id: {job_id}")
        if not await self._mark_running(job_id):
            logger.info("Skipping overlapping scheduled session run for %s.", job_id)
            return

        start_time = datetime.now(self.timezone)
        logger.info("Starting scheduled session run for %r at %s", job_id, start_time.isoformat())
        try:
            result = await self.runtime.run_scheduled_session(
                job_id,
                current_time=start_time,
            )
            elapsed = (datetime.now(self.timezone) - start_time).total_seconds()
            logger.info(
                "Completed scheduled session %r in %.1fs (session_id=%s, reply_len=%d, tool_calls=%d)",
                job_id,
                elapsed,
                result.session_id,
                len(result.reply),
                len(result.tool_calls),
            )
            if self.notification_hub:
                definition = self.definitions[job_id]
                await self.notification_hub.broadcast(
                    NotificationEvent(
                        type="new_message",
                        session_id=result.session_id,
                        session_title=definition.seed_query[:80],
                        session_kind="scheduled",
                        scheduled_job_id=job_id,
                        preview=result.reply[:200],
                    )
                )
        except Exception:  # pragma: no cover - defensive logging
            elapsed = (datetime.now(self.timezone) - start_time).total_seconds()
            logger.exception("Scheduled session %s failed after %.1fs.", job_id, elapsed)
        finally:
            await self._clear_running(job_id)

    def get_next_run_time(self, job_id: str) -> str | None:
        """Return the ISO-formatted next run time for a job, or None."""

        job = self._scheduler.get_job(job_id)
        if job is None or job.next_run_time is None:
            return None
        return job.next_run_time.isoformat()

    async def _mark_running(self, job_id: str) -> bool:
        async with self._state_lock:
            if job_id in self._running_jobs:
                return False
            self._running_jobs.add(job_id)
            return True

    async def _clear_running(self, job_id: str) -> None:
        async with self._state_lock:
            self._running_jobs.discard(job_id)


def _load_allowed_tools(
    raw_definition: dict[object, object],
    *,
    entry_index: int,
    session_id: str,
    available_tool_names: set[str],
) -> tuple[str, ...]:
    raw_allowed_tools = raw_definition.get("allowed_tools")
    if raw_allowed_tools is None:
        raise ValueError(
            f"scheduled session {session_id!r} is missing allowed_tools"
        )
    if not isinstance(raw_allowed_tools, list):
        raise ValueError(
            f"scheduled session {session_id!r} allowed_tools must be a list"
        )

    allowed_tools: list[str] = []
    seen_tools: set[str] = set()
    for raw_tool_name in raw_allowed_tools:
        if not isinstance(raw_tool_name, str) or not raw_tool_name.strip():
            raise ValueError(
                f"scheduled session entry #{entry_index} has an invalid tool name"
            )
        tool_name = raw_tool_name.strip()
        if tool_name in seen_tools:
            raise ValueError(
                f"scheduled session {session_id!r} repeats allowed tool {tool_name!r}"
            )
        if tool_name not in available_tool_names:
            supported = ", ".join(sorted(available_tool_names))
            raise ValueError(
                f"scheduled session {session_id!r} references unknown tool {tool_name!r}; "
                f"available tools: {supported}"
            )
        seen_tools.add(tool_name)
        allowed_tools.append(tool_name)
    return tuple(allowed_tools)


def _require_text(
    raw_definition: dict[object, object],
    field_name: str,
    *,
    entry_index: int,
) -> str:
    raw_value = raw_definition.get(field_name)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError(
            f"scheduled session entry #{entry_index} must define non-empty {field_name}"
        )
    return raw_value.strip()
