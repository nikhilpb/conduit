"""ADK agent construction for Conduit."""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from typing import Any

from google.adk.agents.context import Context
from google.adk.agents import Agent
from google.adk.models.llm_request import LlmRequest
from google.adk.tools import FunctionTool
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from pydantic import BaseModel
from pydantic import Field

from conduit.anthropic_extended_thinking import ConduitAnthropicLlm
from conduit.config import Settings
from conduit.model_registry import infer_provider
from conduit.tool_permissions import effective_tool_permission
from conduit.tool_permissions import permission_summary
from conduit.tools.bash import build_bash_tool
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


_JSON_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*(\{.*\})\s*```",
    re.DOTALL | re.IGNORECASE,
)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_URL_PATTERN = re.compile(r"https?://[^\s)>\]]+")


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
    research_tool: FunctionTool | None = None
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
        research_tool = _build_research_tool(research_agent)
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


def _build_research_tool(research_agent: Agent) -> FunctionTool:
    async def research(request: str, tool_context: ToolContext) -> dict[str, Any]:
        """Delegate a scoped web research task and return a structured subreport."""

        tool_context.actions.skip_summarization = True

        cleaned_request = request.strip()
        if not cleaned_request:
            return {
                "error": "request must be a non-empty string",
            }

        return await _run_research_agent(
            research_agent,
            request=cleaned_request,
            tool_context=tool_context,
        )

    return FunctionTool(research)


async def _run_research_agent(
    research_agent: Agent,
    *,
    request: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
    from google.adk.runners import Runner
    from google.adk.sessions.in_memory_session_service import InMemorySessionService
    from google.adk.tools._forwarding_artifact_service import ForwardingArtifactService
    from google.adk.utils.context_utils import Aclosing

    invocation_context = tool_context._invocation_context
    if invocation_context is None:
        return {
            "error": "research tool cannot run without an invocation context",
        }

    content = types.Content(
        role="user",
        parts=[
            types.Part.from_text(
                text=ResearchRequest(request=request).model_dump_json(
                    exclude_none=True
                )
            )
        ],
    )

    child_app_name = invocation_context.app_name or research_agent.name
    runner = Runner(
        app_name=child_app_name,
        agent=research_agent,
        artifact_service=ForwardingArtifactService(tool_context),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
        credential_service=invocation_context.credential_service,
        plugins=invocation_context.plugin_manager.plugins,
    )

    state_dict = {
        key: value
        for key, value in tool_context.state.to_dict().items()
        if not key.startswith("_adk")
    }
    session = await runner.session_service.create_session(
        app_name=child_app_name,
        user_id=invocation_context.user_id,
        state=state_dict,
    )

    last_content = None
    try:
        async with Aclosing(
            runner.run_async(
                user_id=session.user_id,
                session_id=session.id,
                new_message=content,
            )
        ) as events:
            async for event in events:
                if event.actions.state_delta:
                    tool_context.state.update(event.actions.state_delta)
                if event.content:
                    last_content = event.content
    finally:
        await runner.close()

    merged_text = ""
    if last_content is not None and last_content.parts is not None:
        merged_text = "\n".join(
            part.text for part in last_content.parts if part.text and not part.thought
        )

    return _parse_research_report(merged_text)


def _parse_research_report(raw_text: str) -> dict[str, Any]:
    stripped_text = raw_text.strip()
    for candidate in _candidate_research_payloads(stripped_text):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue

        try:
            report = ResearchReport.model_validate(
                _normalize_research_payload(payload)
            )
        except ValueError:
            continue

        return report.model_dump(exclude_none=True)

    return _fallback_research_report(stripped_text)


def _candidate_research_payloads(raw_text: str) -> tuple[str, ...]:
    candidates: list[str] = []

    def add_candidate(candidate: str) -> None:
        cleaned = candidate.strip()
        if cleaned and cleaned not in candidates:
            candidates.append(cleaned)

    add_candidate(raw_text)

    for match in _JSON_FENCE_PATTERN.finditer(raw_text):
        add_candidate(match.group(1))

    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end > start:
        add_candidate(raw_text[start : end + 1])

    return tuple(candidates)


def _normalize_research_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("research payload must be a JSON object")

    report_markdown = payload.get("report_markdown")
    if report_markdown is None:
        report_markdown = payload.get("report") or payload.get("markdown")
    if not isinstance(report_markdown, str) or not report_markdown.strip():
        raise ValueError("research payload is missing report_markdown")

    normalized_sources = _normalize_research_sources(payload.get("sources"))
    if not normalized_sources:
        normalized_sources = _extract_sources(report_markdown)

    gaps = payload.get("gaps")
    if gaps is None:
        gaps = payload.get("evidence_gaps")
    if gaps is not None:
        gaps = str(gaps).strip() or None

    return {
        "report_markdown": report_markdown.strip(),
        "sources": normalized_sources,
        "gaps": gaps,
    }


def _normalize_research_sources(raw_sources: Any) -> list[dict[str, str]]:
    if not isinstance(raw_sources, list):
        return []

    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in raw_sources:
        if isinstance(item, str):
            title = item.strip()
            url = title
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("link") or "").strip()
            title = str(
                item.get("title") or item.get("name") or item.get("label") or url
            ).strip()
        else:
            continue

        if not title or not url or url in seen_urls:
            continue

        seen_urls.add(url)
        sources.append(
            {
                "title": title,
                "url": url,
            }
        )

    return sources


def _fallback_research_report(raw_text: str) -> dict[str, Any]:
    report_markdown = raw_text or "Research worker returned no report."
    report = ResearchReport(
        report_markdown=report_markdown,
        sources=_extract_sources(report_markdown),
        gaps=(
            "Research worker returned unstructured output; preserved the raw "
            "report text."
        ),
    )
    return report.model_dump(exclude_none=True)


def _extract_sources(text: str) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    def add_source(title: str, url: str) -> None:
        cleaned_url = url.strip().rstrip(".,")
        cleaned_title = title.strip()
        if cleaned_title == url.strip():
            cleaned_title = cleaned_url
        if not cleaned_title or not cleaned_url or cleaned_url in seen_urls:
            return
        seen_urls.add(cleaned_url)
        sources.append(
            {
                "title": cleaned_title,
                "url": cleaned_url,
            }
        )

    for title, url in _MARKDOWN_LINK_PATTERN.findall(text):
        add_source(title, url)

    for url in _URL_PATTERN.findall(text):
        add_source(url, url)

    return sources


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
            "After you finish using tools, reply with JSON only and no Markdown code fence. "
            'Use this exact shape: {"report_markdown":"...","sources":[{"title":"...","url":"..."}],"gaps":"..."} '
            "report_markdown must be citation-grounded Markdown with inline Markdown-link citations. "
            "Prefer citing fetched page URLs over raw search-result snippets whenever you have fetched the page. "
            "sources must list the distinct sources actually used in the report. "
            "Set gaps to null or omit it when evidence is strong and complete."
        ),
        before_model_callback=before_model_callback,
        before_tool_callback=before_tool_callback,
        tools=[web_search_tool, web_fetch_tool],
        input_schema=ResearchRequest,
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
