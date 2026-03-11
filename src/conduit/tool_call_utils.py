"""Helpers for tool-call status tracking."""

from __future__ import annotations

from typing import Any
from typing import Mapping


BASH_PUBLIC_RESPONSE_FIELDS = (
    "ok",
    "working_directory",
    "timeout_seconds",
    "duration_seconds",
    "exit_code",
    "stdout",
    "stderr",
    "stdout_truncated",
    "stderr_truncated",
    "timed_out",
    "error",
)


def tool_response_status(
    response: Mapping[str, Any] | None,
) -> tuple[str, str | None]:
    """Map a tool response payload to a UI-friendly status."""

    payload = dict(response or {})
    ok = payload.get("ok")
    error = _stringify_error(payload.get("error"))

    if ok is False:
        return "failed", error or "Tool call failed."
    if error:
        return "failed", error
    return "completed", None


def public_tool_response(
    tool_name: str | None,
    response: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the app-facing subset of a tool response payload."""

    if tool_name != "bash" or not response:
        return None

    payload = dict(response)
    sanitized = {
        field_name: payload[field_name]
        for field_name in BASH_PUBLIC_RESPONSE_FIELDS
        if field_name in payload
    }
    return sanitized or None


def _stringify_error(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None
