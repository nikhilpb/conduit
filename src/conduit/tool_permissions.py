"""Server-side tool permission policy for Conduit."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Literal

import yaml

ToolPermissionMode = Literal["allow", "ask", "deny"]

DEFAULT_TOOL_PERMISSIONS: dict[str, ToolPermissionMode] = {
    "web_search": "allow",
    "web_fetch": "allow",
    "gmail_search_messages": "allow",
    "gmail_get_message": "allow",
    "gmail_create_draft": "ask",
    "calendar_list_events": "allow",
    "calendar_create_event": "ask",
    "calendar_update_event": "ask",
    "drive_search_files": "allow",
    "docs_get_document": "allow",
    "docs_create_document": "ask",
    "docs_append_text": "ask",
    "docs_replace_text": "ask",
    "polymarket_search_markets": "allow",
    "polymarket_list_markets": "allow",
    "polymarket_get_market": "allow",
    "polymarket_get_price_history": "allow",
    "codex_task": "ask",
}


def load_tool_permissions(config_path: str | None) -> dict[str, ToolPermissionMode]:
    """Load per-tool permission policy from disk."""

    permissions = dict(DEFAULT_TOOL_PERMISSIONS)
    if not config_path:
        return permissions

    path = Path(config_path)
    if not path.exists():
        return permissions

    payload = yaml.safe_load(path.read_text()) or {}
    raw_tools = payload.get("tools", payload)
    if not isinstance(raw_tools, dict):
        raise ValueError("tool permission config must be a mapping")

    for tool_name, raw_config in raw_tools.items():
        mode = _extract_mode(tool_name, raw_config)
        permissions[tool_name] = mode

    return permissions


def permission_summary(tool_name: str, args: dict[str, Any]) -> str:
    """Build a compact approval summary for a tool invocation."""

    if tool_name == "gmail_create_draft":
        recipients = _summarize_recipients(args.get("to"))
        subject = _preview_text(args.get("subject"), limit=50)
        return f"Draft Gmail email to {recipients} with subject {subject}."

    if tool_name == "calendar_create_event":
        summary = _preview_text(args.get("summary"), limit=50)
        start_time = _preview_text(args.get("start_time"), limit=32)
        end_time = _preview_text(args.get("end_time"), limit=32)
        return f"Create calendar event {summary} from {start_time} to {end_time}."

    if tool_name == "calendar_update_event":
        event_id = _preview_text(args.get("event_id"), limit=24)
        fields = ", ".join(
            key
            for key in ("summary", "start_time", "end_time", "location", "description", "attendees")
            if args.get(key) is not None
        )
        field_summary = fields or "no fields"
        return f"Update calendar event {event_id} with {field_summary}."

    if tool_name == "docs_create_document":
        title = _preview_text(args.get("title"), limit=50)
        return f"Create Google Doc {title}."

    if tool_name == "docs_append_text":
        document_id = _preview_text(args.get("document_id"), limit=24)
        text = _preview_text(args.get("text"), limit=60)
        return f"Append text to Google Doc {document_id}: {text}."

    if tool_name == "docs_replace_text":
        document_id = _preview_text(args.get("document_id"), limit=24)
        search_text = _preview_text(args.get("search_text"), limit=40)
        replace_text = _preview_text(args.get("replace_text"), limit=40)
        return (
            f"Replace text in Google Doc {document_id}: "
            f"{search_text} -> {replace_text}."
        )

    if not args:
        return f"Run {tool_name}()."

    parts = ", ".join(f"{key}={value!r}" for key, value in args.items())
    return f"Run {tool_name}({parts})."


def _extract_mode(tool_name: str, raw_config: Any) -> ToolPermissionMode:
    if isinstance(raw_config, str):
        mode = raw_config
    elif isinstance(raw_config, dict):
        mode = raw_config.get("mode")
    else:
        raise ValueError(
            f"tool permission for {tool_name} must be a string or mapping"
        )

    if mode not in {"allow", "ask", "deny"}:
        raise ValueError(
            f"tool permission for {tool_name} must be one of allow, ask, deny"
        )
    return mode


def _preview_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        return "empty"
    if len(text) <= limit:
        return repr(text)
    return repr(f"{text[: limit - 1].rstrip()}…")


def _summarize_recipients(value: Any) -> str:
    if isinstance(value, list):
        recipients = [str(item).strip() for item in value if str(item).strip()]
    elif value is None:
        recipients = []
    else:
        candidate = str(value).strip()
        recipients = [candidate] if candidate else []

    if not recipients:
        return "no recipients"
    if len(recipients) == 1:
        return recipients[0]
    return f"{recipients[0]} and {len(recipients) - 1} more"
