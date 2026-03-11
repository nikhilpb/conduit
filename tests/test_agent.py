from conduit.agent import build_root_agent
from conduit.config import Settings


def test_build_root_agent_includes_registered_tools():
    agent = build_root_agent(
        Settings(_env_file=None),
        model_name="claude-sonnet-4-6",
    )

    tool_names = [tool.__name__ for tool in agent.tools]

    assert "bash" in tool_names
    assert "web_search" in tool_names
    assert "web_fetch" in tool_names
    assert "polymarket_search_markets" in tool_names
    assert "polymarket_list_markets" in tool_names
    assert "polymarket_get_market" in tool_names
    assert "polymarket_get_price_history" in tool_names
    assert "every bash call requires explicit user confirmation" in agent.instruction
    assert "future-looking probabilities" in agent.instruction
    assert "check Polymarket first when it is relevant" in agent.instruction
