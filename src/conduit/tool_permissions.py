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
    "polymarket_search_markets": "allow",
    "polymarket_list_markets": "allow",
    "polymarket_get_market": "allow",
    "polymarket_get_price_history": "allow",
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
