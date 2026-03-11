from conduit.agent import build_root_agent
from conduit.config import Settings


def test_build_root_agent_includes_polymarket_tools():
    agent = build_root_agent(
        Settings(_env_file=None),
        model_name="claude-sonnet-4-6",
    )

    tool_names = [tool.__name__ for tool in agent.tools]

    assert "web_search" in tool_names
    assert "web_fetch" in tool_names
    assert "polymarket_search_markets" in tool_names
    assert "polymarket_list_markets" in tool_names
    assert "polymarket_get_market" in tool_names
    assert "polymarket_get_price_history" in tool_names
    assert "future-looking probabilities" in agent.instruction
    assert "check Polymarket first when it is relevant" in agent.instruction


def test_build_root_agent_includes_google_workspace_tools_when_enabled(tmp_path):
    credentials_path = tmp_path / "gws-credentials.json"
    credentials_path.write_text("{}")

    agent = build_root_agent(
        Settings(
            _env_file=None,
            gws_enabled=True,
            gws_binary_path="/bin/echo",
            gws_credentials_file=str(credentials_path),
        ),
        model_name="claude-sonnet-4-6",
    )

    tool_names = [tool.__name__ for tool in agent.tools]

    assert "gmail_search_messages" in tool_names
    assert "gmail_get_message" in tool_names
    assert "gmail_create_draft" in tool_names
    assert "calendar_list_events" in tool_names
    assert "calendar_create_event" in tool_names
    assert "calendar_update_event" in tool_names
    assert "drive_search_files" in tool_names
    assert "docs_get_document" in tool_names
    assert "docs_create_document" in tool_names
    assert "docs_append_text" in tool_names
    assert "docs_replace_text" in tool_names
    assert "Gmail drafts" in agent.instruction
