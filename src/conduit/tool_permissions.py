"""Server-side tool permission policy for Conduit."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from typing import Literal
from typing import Mapping

import yaml

ToolPermissionMode = Literal["allow", "ask", "deny"]
MANDATORY_CONFIRMATION_TOOLS = frozenset({"bash"})

DEFAULT_TOOL_PERMISSIONS: dict[str, ToolPermissionMode] = {
    "bash": "ask",
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
        permissions[tool_name] = effective_tool_permission(
            tool_name,
            mode,
        )

    return permissions


def effective_tool_permission(
    tool_name: str,
    configured_mode: str | None = None,
    permissions: Mapping[str, str] | None = None,
) -> ToolPermissionMode:
    """Return the enforced permission mode for a tool."""

    mode = configured_mode
    if mode is None and permissions is not None:
        mode = permissions.get(tool_name)
    if mode not in {"allow", "ask", "deny"}:
        mode = "allow"
    if tool_name in MANDATORY_CONFIRMATION_TOOLS and mode == "allow":
        return "ask"
    return mode


def permission_summary(tool_name: str, args: dict[str, Any]) -> str:
    """Build a compact approval summary for a tool invocation."""

    if not args:
        return f"Run {tool_name}()."

    parts = ", ".join(f"{key}={_format_permission_arg(value)}" for key, value in args.items())
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


def _format_permission_arg(value: Any) -> str:
    rendered = repr(value).replace("\n", "\\n")
    if len(rendered) <= 200:
        return rendered
    return f"{rendered[:197]}..."
