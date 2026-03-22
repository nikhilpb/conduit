"""ADK agent construction for Conduit."""

from __future__ import annotations

from collections.abc import Collection

from google.adk.agents.context import Context
from google.adk.agents import Agent
from google.adk.models.llm_request import LlmRequest
from google.adk.tools import AgentTool
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from pydantic import BaseModel
from pydantic import Field

from conduit.anthropic_extended_thinking import ConduitAnthropicLlm
from conduit.config import Settings
from conduit.model_registry import infer_provider
from conduit.tool_permissions import effective_tool_permission
from conduit.tool_permissions import permission_summary
from conduit.tools.bash import build_bash_tool
from conduit.tools.memory_search import build_memory_search_tool
from conduit.tools.polymarket import build_polymarket_tools
from conduit.tools.recipe_lookup import build_recipe_lookup_tool
from conduit.tools.web_fetch import build_web_fetch_tool
from conduit.tools.web_search import build_web_search_tool
from conduit.user_context import build_context_instructions


class ResearchRequest(BaseModel):
    """Structured request for the research worker."""

    request: str = Field(
        min_length=1,
        description="A narrowly scoped web research task to investigate.",
    )


class ResearchSource(BaseModel):
    """A single cited source used by the research worker."""

    title: str = Field(min_length=1)
    url: str = Field(min_length=1)


class ResearchReport(BaseModel):
    """Structured report returned by the research worker."""

    report_markdown: str = Field(
        min_length=1,
        description="Citation-grounded Markdown report for the research subtask.",
    )
    sources: list[ResearchSource] = Field(
        default_factory=list,
        description="Distinct sources cited or relied on in the report.",
    )
    gaps: str | None = Field(
        default=None,
        description="Optional note about missing, weak, or conflicting evidence.",
    )


def build_root_agent(
    settings: Settings,
    *,
    model_name: str,
    enable_bash: bool = True,
    allowed_tools: Collection[str] | None = None,
    auto_approve_tools: bool = False,
) -> Agent:
    """Build the single-agent runtime used by the API and ADK Web."""

    tool_registry = _build_tool_registry(settings, enable_bash=enable_bash)
    selected_tool_names = _select_tool_names(
        tool_registry,
        allowed_tools=allowed_tools,
    )
    before_model_callback = _build_before_model_callback()
    before_tool_callback = _build_before_tool_callback(
        settings,
        auto_approve_tools=auto_approve_tools,
    )
    tools, root_tool_names, sub_agents = _build_root_tools(
        settings,
        tool_registry=tool_registry,
        selected_tool_names=selected_tool_names,
        model_name=model_name,
        auto_approve_tools=auto_approve_tools,
        before_model_callback=before_model_callback,
        before_tool_callback=before_tool_callback,
    )

    return Agent(
        name="conduit",
        model=_build_model(settings, model_name),
        description=_build_agent_description(root_tool_names),
        instruction=_build_agent_instruction(
            root_tool_names,
            auto_approve_tools=auto_approve_tools,
        ),
        before_model_callback=before_model_callback,
        before_tool_callback=before_tool_callback,
        tools=tools,
        sub_agents=list(sub_agents),
    )


def list_available_tool_names(
    settings: Settings,
    *,
    enable_bash: bool = True,
) -> tuple[str, ...]:
    """Return the names of the currently registered tools."""

    return tuple(_build_tool_registry(settings, enable_bash=enable_bash).keys())


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
    auto_approve_tools: bool = False,
):
    async def before_tool(
        tool: BaseTool,
        args: dict,
        tool_context: ToolContext,
    ) -> dict | None:
        if auto_approve_tools:
            return None

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


def _build_root_tools(
    settings: Settings,
    *,
    tool_registry: dict[str, object],
    selected_tool_names: tuple[str, ...],
    model_name: str,
    auto_approve_tools: bool,
    before_model_callback,
    before_tool_callback,
) -> tuple[list[object], tuple[str, ...], tuple[Agent, ...]]:
    research_tool_enabled = _should_enable_research_tool(
        settings,
        selected_tool_names=selected_tool_names,
        auto_approve_tools=auto_approve_tools,
    )
    research_tool: AgentTool | None = None
    sub_agents: tuple[Agent, ...] = ()
    if research_tool_enabled:
        research_agent = _build_research_agent(
            settings,
            model_name=model_name,
            web_search_tool=tool_registry["web_search"],
            web_fetch_tool=tool_registry["web_fetch"],
            before_model_callback=before_model_callback,
            before_tool_callback=before_tool_callback,
        )
        research_tool = AgentTool(research_agent, skip_summarization=True)
        sub_agents = (research_agent,)

    root_tools: list[object] = []
    root_tool_names: list[str] = []
    added_research_tool = False
    for tool_name in selected_tool_names:
        if research_tool_enabled and tool_name in {"web_search", "web_fetch"}:
            if not added_research_tool and research_tool is not None:
                root_tools.append(research_tool)
                root_tool_names.append("research")
                added_research_tool = True
            continue
        root_tools.append(tool_registry[tool_name])
        root_tool_names.append(tool_name)

    return root_tools, tuple(root_tool_names), sub_agents


def _build_tool_registry(
    settings: Settings,
    *,
    enable_bash: bool,
) -> dict[str, object]:
    tool_registry: dict[str, object] = {
        "web_search": build_web_search_tool(settings),
        "web_fetch": build_web_fetch_tool(settings),
        "memory_search": build_memory_search_tool(settings),
    }
    if enable_bash:
        tool_registry["bash"] = build_bash_tool(settings)
    for tool in build_polymarket_tools(settings):
        tool_registry[_tool_name(tool)] = tool
    recipe_lookup = build_recipe_lookup_tool(settings)
    if recipe_lookup is not None:
        tool_registry["recipe_lookup"] = recipe_lookup
    return tool_registry


def _select_tool_names(
    tool_registry: dict[str, object],
    *,
    allowed_tools: Collection[str] | None,
) -> tuple[str, ...]:
    if allowed_tools is None:
        return tuple(tool_registry.keys())

    allowed_tool_names = set(allowed_tools)
    unknown_tools = sorted(allowed_tool_names - set(tool_registry))
    if unknown_tools:
        raise ValueError(
            f"Unknown agent tools requested: {', '.join(unknown_tools)}"
        )
    return tuple(
        tool_name for tool_name in tool_registry if tool_name in allowed_tool_names
    )


def _build_agent_description(tool_names: tuple[str, ...]) -> str:
    capabilities: list[str] = []
    if "research" in tool_names:
        capabilities.append("delegate web research subtasks")
    else:
        if "web_search" in tool_names:
            capabilities.append("search the web")
        if "web_fetch" in tool_names:
            capabilities.append("fetch webpages")
    if "memory_search" in tool_names:
        capabilities.append("search saved notes from local memory")
    if "bash" in tool_names:
        capabilities.append("run Bash commands on the host")
    if any(tool_name.startswith("polymarket_") for tool_name in tool_names):
        capabilities.append("inspect Polymarket prediction markets")
    if "recipe_lookup" in tool_names:
        capabilities.append("look up recipes from a local catalog")

    if not capabilities:
        return "A personal assistant."
    if len(capabilities) == 1:
        return f"A personal assistant that can {capabilities[0]}."
    return (
        "A personal assistant that can "
        + ", ".join(capabilities[:-1])
        + f", and {capabilities[-1]}."
    )


def _build_agent_instruction(
    tool_names: tuple[str, ...],
    *,
    auto_approve_tools: bool,
) -> str:
    instruction_parts = [
        "You are Conduit, a research assistant. ",
    ]
    if "research" in tool_names:
        instruction_parts.extend(
            [
                "Use research for web investigation, latest-information synthesis, and cited reports. ",
                "You may call research multiple times with narrowly scoped subtask requests before composing the final answer. ",
                "When you use research, preserve or reuse its citations instead of stripping them out. ",
                "For research-heavy answers, return one Markdown report with a brief summary, findings, and a Sources section. ",
            ]
        )
    if "web_search" in tool_names:
        instruction_parts.append(
            "Use web_search when you need to discover fresh information. "
        )
    if "web_fetch" in tool_names:
        instruction_parts.append(
            "Use web_fetch when you need to inspect a specific page or URL in detail. "
        )
    if "memory_search" in tool_names:
        instruction_parts.extend(
            [
                "Use memory_search before you answer questions about what you remember, ",
                "the user's saved preferences, or prior saved facts. ",
                "memory_search only searches the local memory/memory.md file, so do not ",
                "claim something is remembered unless it appears in the current conversation ",
                "or in that file. ",
            ]
        )
        if "bash" in tool_names:
            instruction_parts.extend(
                [
                    "When the user explicitly asks you to remember something or take a note, ",
                    "or when you learn a stable user preference likely to matter later, update ",
                    "memory/memory.md using bash. ",
                    "Before writing, use memory_search or inspect the file so you do not create ",
                    "duplicate notes. ",
                    "Create the memory directory and file if they do not exist. ",
                    "Keep Markdown headings `## User Preferences` and `## Notes`, and append ",
                    "concise dated bullet points under the appropriate heading. ",
                    "Do not store transient one-off facts unless the user explicitly asks you ",
                    "to save them. ",
                ]
            )
        else:
            instruction_parts.extend(
                [
                    "This surface does not expose bash, so you cannot persist new notes to ",
                    "memory/memory.md here; say that clearly instead of pretending you saved ",
                    "something. ",
                ]
            )
    if "bash" in tool_names:
        instruction_parts.append(
            "Use bash when you need to inspect or operate on the local host computer. "
        )
        if not auto_approve_tools:
            instruction_parts.extend(
                [
                    "The bash tool can execute arbitrary host commands and every bash call ",
                    "requires explicit user confirmation before it runs. ",
                ]
            )
        instruction_parts.extend(
            [
                "When bash returns, read its stdout, stderr, exit_code, and timed_out ",
                "fields literally. If stdout or stderr contains text, quote or summarize ",
                "that text directly and do not claim the output was missing. Only say the ",
                "command produced no visible output when both stdout and stderr are empty. ",
            ]
        )
    if "recipe_lookup" in tool_names:
        instruction_parts.extend(
            [
                "Use recipe_lookup when the user asks for recipes, ingredients, steps, or macros ",
                "for dishes in the local recipe catalog. ",
                "The recipe catalog is local and read-only through this tool, so do not promise ",
                "that you can add or edit recipes unless a separate write tool is available. ",
            ]
        )
    if "polymarket_search_markets" in tool_names:
        instruction_parts.append(
            "Use polymarket_search_markets to find prediction markets on Polymarket by keyword. "
        )
    if "polymarket_list_markets" in tool_names:
        instruction_parts.append(
            "Use polymarket_list_markets to browse Polymarket markets by tag, activity, or ranking. "
        )
    if "polymarket_get_market" in tool_names:
        instruction_parts.append(
            "Use polymarket_get_market for current prices, liquidity, and trade volume. "
        )
    if "polymarket_get_price_history" in tool_names:
        instruction_parts.append(
            "Use polymarket_get_price_history for historical price series by outcome. "
        )
    if any(tool_name.startswith("polymarket_") for tool_name in tool_names):
        instruction_parts.extend(
            [
                "When the user asks for future-looking probabilities or who is likely ",
                "to win or happen, such as an election outcome or a geopolitical event, ",
                "check Polymarket first when it is relevant. ",
                "The Polymarket tools are read-only and only expose public market data. ",
            ]
        )
    instruction_parts.extend(
        [
            "If a tool reports an error, treat it as a failed attempt and keep working when useful. ",
            "Prefer citing concrete facts from fetched pages when possible. ",
            "If you are uncertain, say so directly.",
        ]
    )
    return "".join(instruction_parts)


def _build_research_agent(
    settings: Settings,
    *,
    model_name: str,
    web_search_tool: object,
    web_fetch_tool: object,
    before_model_callback,
    before_tool_callback,
) -> Agent:
    return Agent(
        name="research",
        model=_build_model(settings, model_name),
        description=(
            "A specialized web research worker that searches, fetches, and "
            "returns citation-grounded subreports."
        ),
        instruction=(
            "You are Research, a specialized web research worker. "
            "Use web_search to discover current sources and web_fetch to inspect pages in detail. "
            "Return only structured output matching the schema. "
            "report_markdown must be citation-grounded Markdown with inline Markdown-link citations. "
            "Prefer citing fetched page URLs over raw search-result snippets whenever you have fetched the page. "
            "sources must list the distinct sources actually used in the report. "
            "Set gaps when evidence is weak, missing, or conflicting."
        ),
        before_model_callback=before_model_callback,
        before_tool_callback=before_tool_callback,
        tools=[web_search_tool, web_fetch_tool],
        input_schema=ResearchRequest,
        output_schema=ResearchReport,
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


def _build_model(settings: Settings, model_name: str):
    provider = infer_provider(model_name)
    if provider != "anthropic":
        return model_name
    return ConduitAnthropicLlm(
        model=model_name,
        max_tokens=settings.anthropic_max_tokens,
        thinking_budget_tokens=settings.anthropic_thinking_budget_tokens,
        interleaved_thinking=settings.anthropic_interleaved_thinking,
    )


def _should_enable_research_tool(
    settings: Settings,
    *,
    selected_tool_names: tuple[str, ...],
    auto_approve_tools: bool,
) -> bool:
    web_tool_names = {"web_search", "web_fetch"}
    if not web_tool_names.issubset(selected_tool_names):
        return False
    if auto_approve_tools:
        return True
    return all(
        effective_tool_permission(
            tool_name,
            permissions=settings.tool_permissions,
        )
        == "allow"
        for tool_name in web_tool_names
    )


def _tool_name(tool: object) -> str:
    return getattr(tool, "name", getattr(tool, "__name__", type(tool).__name__))
