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

    assert permissions["web_search"] == "allow"
    assert permissions["web_fetch"] == "ask"
    assert permissions["send_email"] == "deny"


def test_permission_summary_includes_arguments():
    assert (
        permission_summary("web_fetch", {"url": "https://example.com"})
        == "Run web_fetch(url='https://example.com')."
    )
