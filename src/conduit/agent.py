"""ADK agent construction for Conduit."""

from __future__ import annotations

from collections.abc import Iterable

from google.adk.agents.context import Context
from google.adk.agents import Agent
from google.adk.models.llm_request import LlmRequest
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from conduit.anthropic_extended_thinking import ConduitAnthropicLlm
from conduit.config import Settings
from conduit.model_registry import infer_provider
from conduit.tool_permissions import effective_tool_permission
from conduit.tool_permissions import permission_summary
from conduit.tools.bash import build_bash_tool
from conduit.tools.polymarket import build_polymarket_tools
from conduit.tools.recipe_lookup import build_recipe_lookup_tool
from conduit.tools.web_search import build_web_search_tool
from conduit.tools.web_fetch import build_web_fetch_tool
from conduit.user_context import build_context_instructions

POLYMARKET_TOOL_NAMES = frozenset(
    {
        "polymarket_search_markets",
        "polymarket_list_markets",
        "polymarket_get_market",
        "polymarket_get_price_history",
    }
)


def build_root_agent(
    settings: Settings,
    *,
    model_name: str,
    enable_bash: bool = True,
    allowed_tools: set[str] | None = None,
    allow_tool_confirmation: bool = True,
) -> Agent:
    """Build the single-agent runtime used by the API and ADK Web."""

    allowed_tool_names = None if allowed_tools is None else set(allowed_tools)
    tool_specs = build_tool_specs(settings, enable_bash=enable_bash)
    tools: list[object] = []
    description_parts = ["A personal assistant that can "]
    instruction_parts = ["You are Conduit, a research assistant. "]
    included_polymarket_tools: set[str] = set()

    def include(tool_name: str) -> bool:
        return allowed_tool_names is None or tool_name in allowed_tool_names

    for tool_name, tool in tool_specs:
        if not include(tool_name):
            continue
        tools.append(tool)
        if tool_name == "web_search":
            description_parts.append("search the web, ")
            instruction_parts.append(
                "Use web_search when you need to discover fresh information. "
            )
            continue
        if tool_name == "web_fetch":
            description_parts.append("fetch webpages, ")
            instruction_parts.append(
                "Use web_fetch when you need to inspect a specific page or URL in detail. "
            )
            continue
        if tool_name == "bash":
            description_parts.append("run Bash commands on the host, ")
            instruction_parts.extend(
                [
                    "Use bash when you need to inspect or operate on the local host computer. ",
                    "The bash tool can execute arbitrary host commands and every bash call ",
                    "requires explicit user confirmation before it runs. ",
                    "When bash returns, read its stdout, stderr, exit_code, and timed_out ",
                    "fields literally. If stdout or stderr contains text, quote or summarize ",
                    "that text directly and do not claim the output was missing. Only say the ",
                    "command produced no visible output when both stdout and stderr are empty. ",
                ]
            )
            continue
        if tool_name == "recipe_lookup":
            description_parts.append("look up recipes from a local catalog, ")
            instruction_parts.extend(
                [
                    "Use recipe_lookup when the user asks for recipes, ingredients, steps, or macros ",
                    "for dishes in the local recipe catalog. ",
                    "The recipe catalog is local and read-only through this tool, so do not promise ",
                    "that you can add or edit recipes unless a separate write tool is available. ",
                ]
            )
            continue
        if tool_name in POLYMARKET_TOOL_NAMES:
            included_polymarket_tools.add(tool_name)

    provider = infer_provider(model_name)
    model = model_name
    if provider == "anthropic":
        model = ConduitAnthropicLlm(
            model=model_name,
            max_tokens=settings.anthropic_max_tokens,
            thinking_budget_tokens=settings.anthropic_thinking_budget_tokens,
            interleaved_thinking=settings.anthropic_interleaved_thinking,
        )

    return Agent(
        name="conduit",
        model=model,
        description=(
            "".join(description_parts)
            + (
                "and inspect Polymarket prediction markets."
                if included_polymarket_tools
                else "help answer questions."
            )
        ),
        instruction=(
            "".join(
                instruction_parts
                + _polymarket_instruction_parts(included_polymarket_tools)
                + [
                    "If a tool reports an error, treat it as a failed attempt and keep working when useful. ",
                    "Prefer citing concrete facts from fetched pages when possible. ",
                    "If you are uncertain, say so directly.",
                ]
            )
        ),
        before_model_callback=_build_before_model_callback(),
        before_tool_callback=_build_before_tool_callback(
            settings,
            allow_confirmation=allow_tool_confirmation,
        ),
        tools=tools,
    )


def build_tool_specs(
    settings: Settings,
    *,
    enable_bash: bool = True,
) -> list[tuple[str, object]]:
    """Return the configured tool set in stable presentation order."""

    tool_specs: list[tuple[str, object]] = [
        ("web_search", build_web_search_tool(settings)),
        ("web_fetch", build_web_fetch_tool(settings)),
    ]
    if enable_bash:
        tool_specs.append(("bash", build_bash_tool(settings)))

    tool_specs.extend(
        (
            getattr(tool, "__name__", getattr(tool, "name", type(tool).__name__)),
            tool,
        )
        for tool in build_polymarket_tools(settings)
    )

    recipe_lookup = build_recipe_lookup_tool(settings)
    if recipe_lookup is not None:
        tool_specs.append(("recipe_lookup", recipe_lookup))

    return tool_specs


def available_tool_names(
    settings: Settings,
    *,
    enable_bash: bool = True,
) -> set[str]:
    """Return the names of tools that can be exposed by the current server."""

    return {tool_name for tool_name, _ in build_tool_specs(settings, enable_bash=enable_bash)}


def _build_before_model_callback():
    async def before_model(
        callback_context: Context,
        llm_request: LlmRequest,
        **_: object,
    ):
        instructions = build_context_instructions(callback_context.state)
        if instructions:
            llm_request.append_instructions(instructions)
        return None

    return before_model


def _build_before_tool_callback(
    settings: Settings,
    *,
    allow_confirmation: bool,
):
    async def before_tool(
        tool: BaseTool,
        args: dict,
        tool_context: ToolContext,
    ) -> dict | None:
        mode = effective_tool_permission(
            tool.name,
            permissions=settings.tool_permissions,
        )
        if mode == "allow":
            return None

        if mode == "deny":
            return {
                "error": f"Tool `{tool.name}` is disabled by server policy.",
            }

        if not allow_confirmation:
            return {
                "error": (
                    f"Tool `{tool.name}` requires user confirmation and cannot be "
                    "used in non-interactive scheduled sessions."
                ),
            }

        summary = permission_summary(tool.name, args)
        if not tool_context.tool_confirmation:
            tool_context.request_confirmation(
                hint=summary,
                payload={
                    "tool": tool.name,
                    "args": args,
                    "summary": summary,
                    "permission": "ask",
                },
            )
            tool_context.actions.skip_summarization = True
            return {
                "error": "This tool call requires confirmation.",
            }

        if not tool_context.tool_confirmation.confirmed:
            return {
                "error": f"Tool `{tool.name}` was denied by the user.",
            }

        return None

    return before_tool


def _polymarket_instruction_parts(
    included_tools: Iterable[str],
) -> list[str]:
    included = set(included_tools)
    if not included:
        return []

    parts = [
        "The Polymarket tools are read-only and only expose public market data. "
    ]
    if {
        "polymarket_search_markets",
        "polymarket_list_markets",
    } & included:
        parts.extend(
            [
                "Use polymarket_search_markets or polymarket_list_markets to find ",
                "prediction markets on Polymarket. ",
            ]
        )
    if "polymarket_get_market" in included:
        parts.append(
            "Use polymarket_get_market for current prices, liquidity, and trade volume. "
        )
    if "polymarket_get_price_history" in included:
        parts.append(
            "Use polymarket_get_price_history for historical price series by outcome. "
        )
    parts.extend(
        [
            "When the user asks for future-looking probabilities or who is likely ",
            "to win or happen, such as an election outcome or a geopolitical event, ",
            "check Polymarket first when it is relevant. ",
        ]
    )
    return parts
