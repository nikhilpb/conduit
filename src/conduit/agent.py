"""ADK agent construction for Conduit."""

from google.adk.agents.context import Context
from google.adk.agents import Agent
from google.adk.models.llm_request import LlmRequest
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from conduit.anthropic_extended_thinking import ConduitAnthropicLlm
from conduit.config import Settings
from conduit.model_registry import infer_provider
from conduit.tool_permissions import permission_summary
from conduit.tools.polymarket import build_polymarket_tools
from conduit.tools.web_search import build_web_search_tool
from conduit.tools.web_fetch import build_web_fetch_tool
from conduit.user_context import build_context_instructions


def build_root_agent(settings: Settings, *, model_name: str) -> Agent:
    """Build the single-agent runtime used by the API and ADK Web."""

    web_search = build_web_search_tool(settings)
    web_fetch = build_web_fetch_tool(settings)
    polymarket_tools = build_polymarket_tools(settings)
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
            "A personal assistant that can search the web, fetch webpages, "
            "and inspect Polymarket prediction markets."
        ),
        instruction=(
            "You are Conduit, a research assistant. "
            "Use web_search when you need to discover fresh information. "
            "Use web_fetch when you need to inspect a specific page or URL in detail. "
            "If a tool reports an error, treat it as a failed attempt and keep working when useful. "
            "Use polymarket_search_markets or polymarket_list_markets to find "
            "prediction markets on Polymarket. "
            "Use polymarket_get_market for current prices, liquidity, and trade volume. "
            "Use polymarket_get_price_history for historical price series by outcome. "
            "When the user asks for future-looking probabilities or who is likely "
            "to win or happen, such as an election outcome or a geopolitical event, "
            "check Polymarket first when it is relevant. "
            "The Polymarket tools are read-only and only expose public market data. "
            "Prefer citing concrete facts from fetched pages when possible. "
            "If you are uncertain, say so directly."
        ),
        before_model_callback=_build_before_model_callback(),
        before_tool_callback=_build_before_tool_callback(settings),
        tools=[
            web_search,
            web_fetch,
            *polymarket_tools,
        ],
    )


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


def _build_before_tool_callback(settings: Settings):
    async def before_tool(
        tool: BaseTool,
        args: dict,
        tool_context: ToolContext,
    ) -> dict | None:
        mode = settings.tool_permissions.get(tool.name, "allow")
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
