"""Helpers for tool-call status tracking."""

from __future__ import annotations

from typing import Any
from typing import Mapping


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


def _stringify_error(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None
