"""Per-turn user context helpers for Conduit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from google.adk.sessions.state import State

CURRENT_TIME_STATE_KEY = "conduit:current_time"
LOCATION_STATE_KEY = State.USER_PREFIX + "conduit_location"
PERSONAL_INSTRUCTIONS_STATE_KEY = (
    State.USER_PREFIX + "conduit_personal_instructions"
)


@dataclass(frozen=True, slots=True)
class TurnContext:
    current_time: str = ""
    location: str = ""
    personal_instructions: str = ""

    def is_empty(self) -> bool:
        return (
            not self.current_time.strip()
            and not self.location.strip()
            and not self.personal_instructions.strip()
        )


def build_state_delta(context: TurnContext | None) -> dict[str, Any]:
    if context is None or context.is_empty():
        return {}

    state_delta: dict[str, Any] = {}
    if context.current_time.strip():
        state_delta[CURRENT_TIME_STATE_KEY] = context.current_time.strip()
    if context.location.strip():
        state_delta[LOCATION_STATE_KEY] = context.location.strip()
    if context.personal_instructions.strip():
        state_delta[PERSONAL_INSTRUCTIONS_STATE_KEY] = (
            context.personal_instructions.strip()
        )
    return state_delta


def build_current_time_state_delta(
    current_time: datetime | None = None,
) -> dict[str, str]:
    """Build a state delta containing the formatted current local time."""

    return {
        CURRENT_TIME_STATE_KEY: format_current_time(
            current_time or datetime.now().astimezone()
        )
    }


def coerce_turn_context(value: Any) -> TurnContext | None:
    if value is None:
        return None

    if isinstance(value, TurnContext):
        return None if value.is_empty() else value

    if isinstance(value, dict):
        context = TurnContext(
            current_time=_coerce_string(value.get("current_time")),
            location=_coerce_string(value.get("location")),
            personal_instructions=_coerce_string(value.get("personal_instructions")),
        )
        return None if context.is_empty() else context

    if not any(
        hasattr(value, attribute)
        for attribute in (
            "current_time",
            "location",
            "personal_instructions",
        )
    ):
        raise ValueError("context must be an object")

    context = TurnContext(
        current_time=_coerce_string(getattr(value, "current_time", "")),
        location=_coerce_string(getattr(value, "location", "")),
        personal_instructions=_coerce_string(
            getattr(value, "personal_instructions", "")
        ),
    )
    return None if context.is_empty() else context


def build_context_instructions(state: Any) -> list[str]:
    instructions: list[str] = []

    current_time = _safe_get(state, CURRENT_TIME_STATE_KEY)
    if current_time:
        instructions.append(f"Current local time for the user: {current_time}")

    location = _safe_get(state, LOCATION_STATE_KEY)
    if location:
        instructions.append(f"Current user location: {location}")

    personal_instructions = _safe_get(state, PERSONAL_INSTRUCTIONS_STATE_KEY)
    if personal_instructions:
        instructions.append(
            "User-specific instructions to follow when relevant:\n"
            f"{personal_instructions}"
        )

    return instructions


def format_current_time(current_time: datetime) -> str:
    """Render a datetime in the same user-facing format as client context."""

    localized_time = current_time.astimezone()
    utc_offset = localized_time.strftime("%z")
    if utc_offset:
        utc_offset = f"{utc_offset[:3]}:{utc_offset[3:]}"
    else:
        utc_offset = "+00:00"
    timezone_name = localized_time.tzname() or "UTC"
    return (
        f"{localized_time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"{timezone_name} (UTC{utc_offset})"
    )


def _safe_get(state: Any, key: str) -> str:
    if state is None:
        return ""

    try:
        value = state.get(key, "")
    except AttributeError:
        value = ""

    if not isinstance(value, str):
        return ""
    return value.strip()


def _coerce_string(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()
