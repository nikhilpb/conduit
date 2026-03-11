"""Recipe catalog lookup tool for Conduit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from conduit.config import Settings
from conduit.recipe_catalog import load_recipes
from conduit.recipe_catalog import rank_recipes
from conduit.recipe_catalog import resolve_recipe_catalog_path


def build_recipe_lookup_tool(settings: Settings):
    """Create a configured recipe lookup tool closure."""

    catalog_path = resolve_recipe_catalog_path(settings.recipe_catalog_config_path)
    if catalog_path is None:
        return None

    async def recipe_lookup(
        query: str,
        max_results: int | None = None,
        include_steps: bool = True,
    ) -> dict[str, Any]:
        """Look up recipes from the local catalog by title and ingredients.

        Args:
            query: Keywords to match against recipe titles and ingredients.
            max_results: Optional result limit. Defaults to the server config.
            include_steps: Include full preparation steps for each matched recipe.

        Returns:
            Ranked recipe matches with ingredients, macros, and optional steps.
        """

        cleaned_query = query.strip()
        if not cleaned_query:
            raise ValueError("query must not be empty")

        limit = max(1, min(max_results or settings.recipe_lookup_max_results, 10))

        try:
            recipes = load_recipes(catalog_path)
        except (OSError, ValueError) as exc:
            return _error_result(
                catalog_path=catalog_path,
                message=f"Unable to load recipe catalog: {exc}",
            )

        ranked = rank_recipes(recipes, cleaned_query)[:limit]
        return {
            "ok": True,
            "query": cleaned_query,
            "catalog_path": str(catalog_path),
            "total_recipes": len(recipes),
            "match_count": len(ranked),
            "matches": [
                {
                    "score": entry.score,
                    "title_token_hits": entry.title_token_hits,
                    "ingredient_token_hits": entry.ingredient_token_hits,
                    "fuzzy_score": entry.fuzzy_score,
                    "recipe": _serialize_recipe(
                        entry.recipe,
                        include_steps=include_steps,
                    ),
                }
                for entry in ranked
            ],
        }

    return recipe_lookup


def _serialize_recipe(
    recipe: dict[str, Any],
    *,
    include_steps: bool,
) -> dict[str, Any]:
    steps = [
        step
        for step in recipe.get("steps", [])
        if isinstance(step, str) and step.strip()
    ]
    serialized = {
        "id": recipe.get("id"),
        "title": recipe.get("title"),
        "source": recipe.get("source"),
        "servings": recipe.get("servings"),
        "ingredients": recipe.get("ingredients", []),
        "step_count": len(steps),
        "notes": recipe.get("notes", []),
        "macros": recipe.get("macros"),
    }
    if include_steps:
        serialized["steps"] = steps
    return serialized


def _error_result(*, catalog_path: Path, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "catalog_path": str(catalog_path),
        "matches": [],
        "error": message,
    }
