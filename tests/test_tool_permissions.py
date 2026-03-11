from conduit.tool_permissions import effective_tool_permission
from conduit.tool_permissions import load_tool_permissions
from conduit.tool_permissions import permission_summary


def test_load_tool_permissions_merges_with_defaults(tmp_path):
    config_path = tmp_path / "tools.yaml"
    config_path.write_text(
        """
tools:
  web_fetch:
    mode: ask
  send_email:
    mode: deny
"""
    )

    permissions = load_tool_permissions(str(config_path))

    assert permissions["bash"] == "ask"
    assert permissions["web_search"] == "allow"
    assert permissions["web_fetch"] == "ask"
    assert permissions["polymarket_search_markets"] == "allow"
    assert permissions["polymarket_list_markets"] == "allow"
    assert permissions["polymarket_get_market"] == "allow"
    assert permissions["polymarket_get_price_history"] == "allow"
    assert permissions["recipe_lookup"] == "allow"
    assert permissions["send_email"] == "deny"


def test_permission_summary_includes_arguments():
    assert (
        permission_summary("web_fetch", {"url": "https://example.com"})
        == "Run web_fetch(url='https://example.com')."
    )


def test_bash_permission_cannot_be_configured_to_allow():
    assert effective_tool_permission("bash", configured_mode="allow") == "ask"
    assert effective_tool_permission("bash", configured_mode="deny") == "deny"
