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
    assert permissions["gmail_search_messages"] == "allow"
    assert permissions["gmail_create_draft"] == "ask"
    assert permissions["calendar_list_events"] == "allow"
    assert permissions["calendar_create_event"] == "ask"
    assert permissions["drive_search_files"] == "allow"
    assert permissions["docs_get_document"] == "allow"
    assert permissions["docs_create_document"] == "ask"
    assert permissions["polymarket_search_markets"] == "allow"
    assert permissions["polymarket_list_markets"] == "allow"
    assert permissions["polymarket_get_market"] == "allow"
    assert permissions["polymarket_get_price_history"] == "allow"
    assert permissions["send_email"] == "deny"


def test_permission_summary_includes_arguments():
    assert (
        permission_summary("web_fetch", {"url": "https://example.com"})
        == "Run web_fetch(url='https://example.com')."
    )


def test_permission_summary_compacts_workspace_write_tool_arguments():
    assert (
        permission_summary(
            "gmail_create_draft",
            {
                "to": ["alice@example.com", "bob@example.com"],
                "subject": "Quarterly planning draft",
                "body_text": "Long body that should not appear in the approval summary.",
            },
        )
        == "Draft Gmail email to alice@example.com and 1 more with subject 'Quarterly planning draft'."
    )

    assert (
        permission_summary(
            "docs_append_text",
            {
                "document_id": "doc-1234567890",
                "text": "This is a long appended paragraph that should be previewed briefly.",
            },
        )
        == "Append text to Google Doc 'doc-1234567890': 'This is a long appended paragraph that should be previewed…'."
    )
