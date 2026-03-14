"""Helpers for Conduit-specific session metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SESSION_KIND_KEY = "conduit_session_kind"
READ_ONLY_KEY = "conduit_session_read_only"
SCHEDULE_ID_KEY = "conduit_schedule_id"
SCHEDULED_FOR_KEY = "conduit_scheduled_for"
MODEL_KEY_KEY = "conduit_model_key"
ALLOWED_TOOLS_KEY = "conduit_allowed_tools"

INTERACTIVE_SESSION_KIND = "interactive"
SCHEDULED_SESSION_KIND = "scheduled"


def build_scheduled_session_state(
    *,
    schedule_id: str,
    scheduled_for: str,
    model_key: str,
    allowed_tools: tuple[str, ...],
) -> dict[str, Any]:
    """Return the stored state payload for a scheduled session."""

    return {
        SESSION_KIND_KEY: SCHEDULED_SESSION_KIND,
        READ_ONLY_KEY: True,
        SCHEDULE_ID_KEY: schedule_id,
        SCHEDULED_FOR_KEY: scheduled_for,
        MODEL_KEY_KEY: model_key,
        ALLOWED_TOOLS_KEY: list(allowed_tools),
    }


def session_kind_from_state(state: Mapping[str, Any] | None) -> str:
    """Return the public kind for a stored session state."""

    if not state:
        return INTERACTIVE_SESSION_KIND

    raw_kind = state.get(SESSION_KIND_KEY)
    if raw_kind == SCHEDULED_SESSION_KIND:
        return SCHEDULED_SESSION_KIND
    return INTERACTIVE_SESSION_KIND


def session_read_only_from_state(state: Mapping[str, Any] | None) -> bool:
    """Return whether a stored session should reject new messages."""

    if not state:
        return False
    return bool(state.get(READ_ONLY_KEY))
