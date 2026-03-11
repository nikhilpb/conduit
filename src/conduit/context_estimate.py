"""Deterministic context-estimation helpers for app-facing UI."""

from __future__ import annotations

from dataclasses import dataclass
import json
from math import ceil
from typing import Any
from typing import Iterable

from google.adk.events.event import Event
from google.genai import types

from conduit.tool_call_utils import is_internal_tool_call

CONTEXT_CHARS_PER_TOKEN = 4.0


@dataclass(frozen=True, slots=True)
class ContextEstimate:
    chars: int
    tokens: int
    chars_per_token: float = CONTEXT_CHARS_PER_TOKEN

    def to_payload(self) -> dict[str, float | int]:
        return {
            "chars": self.chars,
            "tokens": self.tokens,
            "chars_per_token": self.chars_per_token,
        }


def build_context_estimate(
    chars: int,
    *,
    chars_per_token: float = CONTEXT_CHARS_PER_TOKEN,
) -> ContextEstimate:
    bounded_chars = max(0, int(chars))
    tokens = int(ceil(bounded_chars / chars_per_token)) if bounded_chars else 0
    return ContextEstimate(
        chars=bounded_chars,
        tokens=tokens,
        chars_per_token=chars_per_token,
    )


def empty_context_estimate() -> ContextEstimate:
    return build_context_estimate(0)


def estimate_events_context(events: Iterable[Event]) -> ContextEstimate:
    return build_context_estimate(
        sum(estimate_event_context_chars(event) for event in events)
    )


def estimate_event_context_chars(event: Event) -> int:
    return estimate_content_context_chars(getattr(event, "content", None))


def estimate_content_context_chars(content: types.Content | None) -> int:
    if content is None or not content.parts:
        return 0

    total = 0
    for part in content.parts:
        if part.text and not getattr(part, "thought", False):
            total += len(part.text.strip())

        function_call = getattr(part, "function_call", None)
        if function_call and not is_internal_tool_call(function_call.name):
            total += estimate_tool_call_chars(
                function_call.name,
                dict(function_call.args or {}),
            )

        function_response = getattr(part, "function_response", None)
        if function_response and not is_internal_tool_call(function_response.name):
            total += estimate_tool_result_chars(
                function_response.name,
                function_response.response,
            )

    return total


def estimate_tool_call_chars(name: str | None, args: Any) -> int:
    return _estimate_named_json_chars(name, args)


def estimate_tool_result_chars(name: str | None, response: Any) -> int:
    return _estimate_named_json_chars(name, response)


def _estimate_named_json_chars(name: str | None, value: Any) -> int:
    normalized_name = (name or "").strip()
    if not normalized_name:
        return 0
    return len(normalized_name) + len(_canonical_json(value))


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except TypeError:
        return json.dumps(
            str(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
