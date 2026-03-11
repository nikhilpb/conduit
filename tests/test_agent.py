from conduit.agent import build_root_agent
from conduit.config import Settings


def test_build_root_agent_includes_polymarket_tools():
    agent = build_root_agent(
        Settings(_env_file=None),
        model_name="claude-sonnet-4-6",
    )

    tool_names = [
        getattr(tool, "__name__", getattr(tool, "name", type(tool).__name__))
        for tool in agent.tools
    ]

    assert "web_search" in tool_names
    assert "web_fetch" in tool_names
    assert "polymarket_search_markets" in tool_names
    assert "polymarket_list_markets" in tool_names
    assert "polymarket_get_market" in tool_names
    assert "polymarket_get_price_history" in tool_names
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
