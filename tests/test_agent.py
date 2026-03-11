from conduit.agent import build_root_agent
from conduit.config import Settings
from conduit.runtime import ConduitRuntime


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
    assert "do not claim the output was missing" in agent.instruction
    assert "future-looking probabilities" in agent.instruction
    assert "check Polymarket first when it is relevant" in agent.instruction


def test_build_root_agent_can_disable_bash():
    agent = build_root_agent(
        Settings(_env_file=None),
        model_name="claude-sonnet-4-6",
        enable_bash=False,
    )

    tool_names = [tool.__name__ for tool in agent.tools]

    assert "bash" not in tool_names
    assert "Use bash when you need to inspect" not in agent.instruction


def test_runtime_uses_bash_only_for_websocket_runner(tmp_path):
    runtime = ConduitRuntime(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "conduit.db"),
            models_config_path=str(tmp_path / "models.yaml"),
        )
    )

    websocket_tool_names = [tool.__name__ for tool in runtime.app.root_agent.tools]
    http_tool_names = [tool.__name__ for tool in runtime.http_app.root_agent.tools]

    assert "bash" in websocket_tool_names
    assert "bash" not in http_tool_names
