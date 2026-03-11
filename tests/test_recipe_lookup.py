import pytest

from conduit.config import Settings
from conduit.recipe_catalog import rank_recipes
from conduit.recipe_catalog import resolve_recipe_catalog_path
from conduit.tools import recipe_lookup as recipe_lookup_module


def _catalog_payload() -> str:
    return """
{
  "version": 1,
  "recipes": [
    {
      "id": "matar-paneer",
      "title": "Matar Paneer",
      "source": {"name": "Example", "url": "https://example.com/matar-paneer"},
      "servings": 4,
      "ingredients": [
        {"item": "paneer", "amount": 200, "unit": "g", "prep_note": null, "original_text": "200 g paneer"},
        {"item": "peas", "amount": 150, "unit": "g", "prep_note": null, "original_text": "150 g peas"}
      ],
      "steps": ["Cook onions.", "Add paneer and peas."],
      "notes": [],
      "macros": {"calories_kcal": 300, "protein_g": 18, "carbs_g": 10, "fat_g": 20, "per_serving": true, "provenance_source": "estimated", "provenance_reasoning": "test"},
      "search_text": "matar paneer paneer peas",
      "created_at": "2026-03-01T10:00:00Z",
      "updated_at": "2026-03-01T10:00:00Z"
    },
    {
      "id": "kala-chana",
      "title": "Kala Chana Curry",
      "source": {"name": "Example", "url": "https://example.com/kala-chana"},
      "servings": 4,
      "ingredients": [
        {"item": "kala chana", "amount": 250, "unit": "g", "prep_note": null, "original_text": "250 g kala chana"}
      ],
      "steps": ["Pressure cook chickpeas."],
      "notes": [],
      "macros": {"calories_kcal": 260, "protein_g": 12, "carbs_g": 40, "fat_g": 6, "per_serving": true, "provenance_source": "estimated", "provenance_reasoning": "test"},
      "search_text": "kala chana chickpeas curry",
      "created_at": "2026-03-01T10:00:00Z",
      "updated_at": "2026-03-01T10:00:00Z"
    }
  ]
}
"""


def test_resolve_recipe_catalog_path_uses_first_existing_candidate(tmp_path):
    catalog_path = tmp_path / "recipes.json"
    catalog_path.write_text(_catalog_payload())
    config_path = tmp_path / "recipes.yaml"
    config_path.write_text(
        f"catalog:\n  paths:\n    - ./missing.json\n    - {catalog_path}\n"
    )

    resolved = resolve_recipe_catalog_path(str(config_path))

    assert resolved == catalog_path


def test_rank_recipes_prefers_title_matches():
    recipes = [
        {
            "title": "Matar Paneer",
            "ingredients": [{"item": "paneer"}],
        },
        {
            "title": "Kala Chana Curry",
            "ingredients": [{"item": "paneer"}],
        },
    ]

    ranked = rank_recipes(recipes, "paneer")

    assert ranked[0].recipe["title"] == "Matar Paneer"
    assert ranked[0].title_token_hits >= ranked[1].title_token_hits


@pytest.mark.anyio
async def test_recipe_lookup_returns_ranked_recipe_details(tmp_path):
    catalog_path = tmp_path / "recipes.json"
    catalog_path.write_text(_catalog_payload())
    config_path = tmp_path / "recipes.yaml"
    config_path.write_text(f"catalog:\n  path: {catalog_path}\n")

    tool = recipe_lookup_module.build_recipe_lookup_tool(
        Settings(
            _env_file=None,
            recipe_catalog_config_path=str(config_path),
            recipe_lookup_max_results=2,
        )
    )

    assert tool is not None

    result = await tool("paneer")

    assert result["ok"] is True
    assert result["match_count"] == 1
    assert result["matches"][0]["recipe"]["id"] == "matar-paneer"
    assert result["matches"][0]["recipe"]["steps"] == [
        "Cook onions.",
        "Add paneer and peas.",
    ]


def test_build_recipe_lookup_tool_returns_none_without_catalog(tmp_path):
    config_path = tmp_path / "recipes.yaml"
    config_path.write_text("catalog:\n  path: ./missing.json\n")

    tool = recipe_lookup_module.build_recipe_lookup_tool(
        Settings(
            _env_file=None,
            recipe_catalog_config_path=str(config_path),
        )
    )

    assert tool is None
