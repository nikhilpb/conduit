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

if TYPE_CHECKING:
    from conduit.runtime import ConduitRuntime


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScheduledSessionDefinition:
    id: str
    schedule: str
    model: str
    seed_query: str
    allowed_tools: tuple[str, ...]


def process_timezone() -> tzinfo:
    """Return the backend process timezone."""

    return datetime.now().astimezone().tzinfo or UTC


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
    timezone = process_timezone()
    definitions: list[ScheduledSessionDefinition] = []
    seen_ids: set[str] = set()

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

    return tuple(definitions)


class ScheduledSessionScheduler:
    """Manage in-process scheduled session execution."""

    def __init__(
        self,
        *,
        runtime: ConduitRuntime,
        definitions: tuple[ScheduledSessionDefinition, ...],
    ) -> None:
        self.runtime = runtime
        self.definitions = {definition.id: definition for definition in definitions}
        self.timezone = process_timezone()
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
            self._scheduler.add_job(
                self.run_job,
                trigger=CronTrigger.from_crontab(
                    definition.schedule,
                    timezone=self.timezone,
                ),
                id=definition.id,
                kwargs={"job_id": definition.id},
                replace_existing=True,
                max_instances=1,
            )

        self._scheduler.start()
        self._started = True

    async def shutdown(self) -> None:
        """Stop the scheduler."""

        if not self._started:
            return

        self._scheduler.shutdown(wait=False)
        self._started = False

    async def run_job(self, job_id: str) -> None:
        """Execute one scheduled session definition immediately."""

        if job_id not in self.definitions:
            raise KeyError(f"unknown scheduled session id: {job_id}")
        if not await self._mark_running(job_id):
            logger.info("Skipping overlapping scheduled session run for %s.", job_id)
            return

        try:
            await self.runtime.run_scheduled_session(
                job_id,
                current_time=datetime.now(self.timezone),
            )
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Scheduled session %s failed.", job_id)
        finally:
            await self._clear_running(job_id)

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
