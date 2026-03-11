"""Helpers for locating and ranking local recipe catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class RankedRecipeMatch:
    recipe: dict[str, Any]
    score: float
    phrase_hit: bool
    title_token_hits: int
    ingredient_token_hits: int
    fuzzy_score: float


def resolve_recipe_catalog_path(config_path: str | None) -> Path | None:
    """Return the first configured recipe catalog path that exists."""

    if not config_path:
        return None

    path = Path(config_path)
    if not path.exists():
        return None

    payload = yaml.safe_load(path.read_text()) or {}
    raw_catalog = payload.get("catalog", payload)
    if not isinstance(raw_catalog, dict):
        raise ValueError("recipe catalog config must be a mapping")

    required = bool(raw_catalog.get("required", False))
    candidates = _resolve_candidate_paths(
        raw_catalog.get("paths", raw_catalog.get("path")),
        config_dir=path.parent,
    )
    if not candidates:
        if required:
            raise ValueError("recipe catalog config must include `path` or `paths`")
        return None

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    if required:
        raise ValueError(
            "recipe catalog config did not resolve to an existing file: "
            + ", ".join(str(candidate) for candidate in candidates)
        )
    return None


def load_recipes(catalog_path: Path) -> list[dict[str, Any]]:
    """Load recipes from the configured catalog file."""

    payload = json.loads(catalog_path.read_text())
    if not isinstance(payload, dict):
        raise ValueError("recipe catalog root must be a JSON object")

    recipes = payload.get("recipes", [])
    if not isinstance(recipes, list):
        raise ValueError("recipe catalog must contain a list at `recipes`")
    return [recipe for recipe in recipes if isinstance(recipe, dict)]


def rank_recipes(recipes: list[dict[str, Any]], query: str) -> list[RankedRecipeMatch]:
    """Rank recipes by title and ingredient overlap for a query."""

    ranked = [_score_recipe(recipe, query) for recipe in recipes]
    ranked = [
        entry
        for entry in ranked
        if entry.score > 0
        and (
            entry.phrase_hit
            or entry.title_token_hits > 0
            or entry.ingredient_token_hits > 0
        )
    ]
    ranked.sort(key=lambda entry: (-entry.score, str(entry.recipe.get("title", "")).lower()))
    return ranked


def _resolve_candidate_paths(
    raw_paths: object,
    *,
    config_dir: Path,
) -> list[Path]:
    if raw_paths is None:
        return []

    values: list[str] = []
    if isinstance(raw_paths, str):
        values = [raw_paths]
    elif isinstance(raw_paths, list):
        if not all(isinstance(value, str) for value in raw_paths):
            raise ValueError("recipe catalog config `paths` entries must be strings")
        values = raw_paths
    else:
        raise ValueError("recipe catalog config `path` or `paths` must be a string or list")

    resolved: list[Path] = []
    for value in values:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = (config_dir / candidate).resolve()
        resolved.append(candidate)
    return resolved


def _score_recipe(recipe: dict[str, Any], query: str) -> RankedRecipeMatch:
    title = str(recipe.get("title", ""))
    title_tokens = _tokenize(title)
    query_tokens = _tokenize(query)

    ingredient_items = [
        str(item.get("item", ""))
        for item in recipe.get("ingredients", [])
        if isinstance(item, dict)
    ]
    ingredient_text = " ".join(ingredient_items)
    ingredient_tokens = _tokenize(ingredient_text)

    title_hits = sum(1 for token in query_tokens if token in title_tokens)
    ingredient_hits = sum(1 for token in query_tokens if token in ingredient_tokens)

    score = 0.0
    query_text = " ".join(query_tokens)

    phrase_hit = bool(query_text and query_text in title.lower())
    if phrase_hit:
        score += 100.0

    if query_tokens:
        score += 30.0 * (title_hits / len(query_tokens))
        score += 12.0 * (ingredient_hits / len(query_tokens))

    score += 10.0 * title_hits
    score += 4.0 * ingredient_hits

    fuzzy = SequenceMatcher(None, query.lower(), title.lower()).ratio()
    score += fuzzy * 10.0

    return RankedRecipeMatch(
        recipe=recipe,
        score=round(score, 4),
        phrase_hit=phrase_hit,
        title_token_hits=title_hits,
        ingredient_token_hits=ingredient_hits,
        fuzzy_score=round(fuzzy, 4),
    )


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())
