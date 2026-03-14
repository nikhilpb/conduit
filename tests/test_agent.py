import asyncio
from types import SimpleNamespace

from conduit.agent import build_root_agent
from conduit.config import Settings
from conduit.runtime import ConduitRuntime


def test_build_root_agent_includes_registered_tools():
    agent = build_root_agent(
        Settings(_env_file=None),
        model_name="claude-sonnet-4-6",
    )

    tool_names = [
        getattr(tool, "__name__", getattr(tool, "name", type(tool).__name__))
        for tool in agent.tools
    ]

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

    tool_names = [
        getattr(tool, "__name__", getattr(tool, "name", type(tool).__name__))
        for tool in agent.tools
    ]

    assert "recipe_lookup" in tool_names
    assert "Use recipe_lookup" in agent.instruction


def test_build_root_agent_can_disable_bash():
    agent = build_root_agent(
        Settings(_env_file=None),
        model_name="claude-sonnet-4-6",
        enable_bash=False,
    )

    tool_names = [
        getattr(tool, "__name__", getattr(tool, "name", type(tool).__name__))
        for tool in agent.tools
    ]

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

    websocket_tool_names = [
        getattr(tool, "__name__", getattr(tool, "name", type(tool).__name__))
        for tool in runtime.app.root_agent.tools
    ]
    http_tool_names = [
        getattr(tool, "__name__", getattr(tool, "name", type(tool).__name__))
        for tool in runtime.http_app.root_agent.tools
    ]

    assert "bash" in websocket_tool_names
    assert "bash" not in http_tool_names


def test_build_root_agent_can_limit_tools_and_auto_approve_bash():
    agent = build_root_agent(
        Settings(_env_file=None),
        model_name="claude-sonnet-4-6",
        allowed_tools=("bash", "web_fetch"),
        auto_approve_tools=True,
    )

    tool_names = [
        getattr(tool, "__name__", getattr(tool, "name", type(tool).__name__))
        for tool in agent.tools
    ]

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
    tool_names = [
        getattr(tool, "__name__", getattr(tool, "name", type(tool).__name__))
        for tool in scheduled_runtime.app.root_agent.tools
    ]

    assert scheduled_runtime.definition.model == "gemini-3-flash-preview"
    assert scheduled_runtime.app.root_agent.model == "gemini-3-flash-preview"
    assert tool_names == ["web_fetch", "bash"]
