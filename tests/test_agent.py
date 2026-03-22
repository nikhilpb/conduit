import asyncio
from types import SimpleNamespace

from conduit.agent import _parse_research_report
from conduit.agent import build_root_agent
from conduit.config import Settings
from conduit.runtime import ConduitRuntime


def _empty_scheduled_sessions_path(tmp_path) -> str:
    path = tmp_path / "scheduled_sessions.yaml"
    path.write_text("scheduled_sessions: []\n")
    return str(path)


def _tool_names(agent) -> list[str]:
    return [
        getattr(tool, "__name__", getattr(tool, "name", type(tool).__name__))
        for tool in agent.tools
    ]


def _research_child(agent):
    assert len(agent.sub_agents) == 1
    research_agent = agent.sub_agents[0]
    assert research_agent.name == "research"
    return research_agent


def test_build_root_agent_includes_registered_tools():
    agent = build_root_agent(
        Settings(_env_file=None),
        model_name="claude-sonnet-4-6",
    )

    tool_names = _tool_names(agent)
    research_agent = _research_child(agent)
    research_tool_names = _tool_names(research_agent)

    assert "bash" in tool_names
    assert "research" in tool_names
    assert "web_search" not in tool_names
    assert "web_fetch" not in tool_names
    assert "polymarket_search_markets" in tool_names
    assert "polymarket_list_markets" in tool_names
    assert "polymarket_get_market" in tool_names
    assert "polymarket_get_price_history" in tool_names
    assert research_tool_names == ["web_search", "web_fetch"]
    assert "Use research for web investigation" in agent.instruction
    assert "every bash call requires explicit user confirmation" in agent.instruction
    assert "do not claim the output was missing" in agent.instruction
    assert "future-looking probabilities" in agent.instruction
    assert "check Polymarket first when it is relevant" in agent.instruction


def test_build_root_agent_includes_recipe_lookup_when_catalog_is_configured(tmp_path):
    catalog_path = tmp_path / "recipes.json"
    catalog_path.write_text(
        """
{
  "version": 1,
  "recipes": [
    {
      "id": "matar-paneer",
      "title": "Matar Paneer",
      "source": {"name": "Example", "url": "https://example.com/matar-paneer"},
      "servings": 4,
      "ingredients": [{"item": "paneer", "amount": 200, "unit": "g", "prep_note": null, "original_text": "200 g paneer"}],
      "steps": ["Cook everything."],
      "notes": [],
      "macros": {"calories_kcal": 300, "protein_g": 18, "carbs_g": 10, "fat_g": 20, "per_serving": true, "provenance_source": "estimated", "provenance_reasoning": "test"},
      "search_text": "matar paneer paneer peas",
      "created_at": "2026-03-01T10:00:00Z",
      "updated_at": "2026-03-01T10:00:00Z"
    }
  ]
}
"""
    )
    config_path = tmp_path / "recipes.yaml"
    config_path.write_text(f"catalog:\n  path: {catalog_path}\n")

    agent = build_root_agent(
        Settings(
            _env_file=None,
            recipe_catalog_config_path=str(config_path),
        ),
        model_name="claude-sonnet-4-6",
    )

    tool_names = _tool_names(agent)

    assert "recipe_lookup" in tool_names
    assert "Use recipe_lookup" in agent.instruction


def test_build_root_agent_can_disable_bash():
    agent = build_root_agent(
        Settings(_env_file=None),
        model_name="claude-sonnet-4-6",
        enable_bash=False,
    )

    tool_names = _tool_names(agent)

    assert "bash" not in tool_names
    assert "Use bash when you need to inspect" not in agent.instruction


def test_runtime_uses_bash_only_for_websocket_runner(tmp_path):
    runtime = ConduitRuntime(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "conduit.db"),
            models_config_path=str(tmp_path / "models.yaml"),
            scheduled_sessions_config_path=_empty_scheduled_sessions_path(tmp_path),
        )
    )

    websocket_tool_names = _tool_names(runtime.app.root_agent)
    http_tool_names = _tool_names(runtime.http_app.root_agent)

    assert "bash" in websocket_tool_names
    assert "bash" not in http_tool_names


def test_build_root_agent_uses_direct_web_tools_when_research_is_not_safe(tmp_path):
    permissions_path = tmp_path / "tools.yaml"
    permissions_path.write_text(
        """
tools:
  web_fetch:
    mode: ask
"""
    )
    agent = build_root_agent(
        Settings(
            _env_file=None,
            tool_permissions_path=str(permissions_path),
        ),
        model_name="claude-sonnet-4-6",
    )

    tool_names = _tool_names(agent)

    assert "research" not in tool_names
    assert "web_search" in tool_names
    assert "web_fetch" in tool_names
    assert agent.sub_agents == []


def test_build_root_agent_can_limit_tools_and_auto_approve_bash():
    agent = build_root_agent(
        Settings(_env_file=None),
        model_name="claude-sonnet-4-6",
        allowed_tools=("bash", "web_fetch"),
        auto_approve_tools=True,
    )

    tool_names = _tool_names(agent)

    assert tool_names == ["web_fetch", "bash"]
    assert "requires explicit user confirmation" not in agent.instruction

    callback_result = asyncio.run(
        agent.before_tool_callback(
            tool=SimpleNamespace(name="bash"),
            args={"command": "pwd"},
            tool_context=SimpleNamespace(
                tool_confirmation=None,
                actions=SimpleNamespace(skip_summarization=False),
            ),
        )
    )
    assert callback_result is None


def test_runtime_builds_scheduled_runner_with_raw_model_and_allowlist(tmp_path):
    scheduled_config_path = tmp_path / "scheduled_sessions.yaml"
    scheduled_config_path.write_text(
        """
scheduled_sessions:
  - id: daily-briefing
    schedule: "0 9 * * *"
    model: gemini-3-flash-preview
    seed_query: Summarize the day.
    allowed_tools:
      - bash
      - web_fetch
"""
    )

    runtime = ConduitRuntime(
        Settings(
            _env_file=None,
            db_path=str(tmp_path / "conduit.db"),
            models_config_path=str(tmp_path / "models.yaml"),
            google_api_key="google-test",
            scheduled_sessions_config_path=str(scheduled_config_path),
        )
    )

    scheduled_runtime = runtime.scheduled_session_runtimes["daily-briefing"]
    tool_names = _tool_names(scheduled_runtime.app.root_agent)

    assert scheduled_runtime.definition.model == "gemini-3-flash-preview"
    assert scheduled_runtime.app.root_agent.model == "gemini-3-flash-preview"
    assert tool_names == ["web_fetch", "bash"]


def test_build_root_agent_wraps_web_research_as_a_single_tool():
    agent = build_root_agent(
        Settings(_env_file=None),
        model_name="claude-sonnet-4-6",
        allowed_tools=("web_search", "web_fetch"),
    )

    assert _tool_names(agent) == ["research"]
    assert _tool_names(_research_child(agent)) == ["web_search", "web_fetch"]


def test_research_child_leaves_output_schema_unset_for_gemini():
    agent = build_root_agent(
        Settings(_env_file=None, google_api_key="google-test"),
        model_name="gemini-3-flash-preview",
        allowed_tools=("web_search", "web_fetch"),
    )

    assert _research_child(agent).output_schema is None


def test_parse_research_report_accepts_fenced_json_and_normalizes_sources():
    report = _parse_research_report(
        """```json
{
  "report_markdown": "Latest update from [Reuters](https://www.reuters.com/world/).",
  "sources": [
    {"title": "Reuters", "url": "https://www.reuters.com/world/"},
    {"name": "BBC", "link": "https://www.bbc.com/news"}
  ],
  "gaps": "Conflicting casualty counts."
}
```"""
    )

    assert report == {
        "report_markdown": "Latest update from [Reuters](https://www.reuters.com/world/).",
        "sources": [
            {
                "title": "Reuters",
                "url": "https://www.reuters.com/world/",
            },
            {
                "title": "BBC",
                "url": "https://www.bbc.com/news",
            },
        ],
        "gaps": "Conflicting casualty counts.",
    }


def test_parse_research_report_falls_back_to_raw_markdown_and_extracts_links():
    report = _parse_research_report(
        "Summary with [BBC](https://www.bbc.com/news) and https://example.com/live."
    )

    assert report == {
        "report_markdown": (
            "Summary with [BBC](https://www.bbc.com/news) and "
            "https://example.com/live."
        ),
        "sources": [
            {
                "title": "BBC",
                "url": "https://www.bbc.com/news",
            },
            {
                "title": "https://example.com/live",
                "url": "https://example.com/live",
            },
        ],
        "gaps": (
            "Research worker returned unstructured output; preserved the raw "
            "report text."
        ),
    }
