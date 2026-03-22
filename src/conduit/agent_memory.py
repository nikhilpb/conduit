"""Helpers for locating and searching the local agent memory file."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any


DEFAULT_MEMORY_SEARCH_MAX_RESULTS = 5
MAX_MEMORY_SEARCH_RESULTS = 10
MAX_MEMORY_SNIPPET_CHARS = 240
_QUERY_TOKEN_RE = re.compile(r"[a-z0-9]+")


def resolve_memory_file_path(path_str: str) -> Path:
    """Resolve a configured memory-file path against the current workspace."""

    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def search_memory_file(
    path: Path,
    query: str,
    *,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Search a Markdown memory file with simple case-insensitive line matching."""

    cleaned_query = query.strip()
    effective_max_results = _clamp_max_results(max_results)
    if not cleaned_query:
        return {
            "ok": False,
            "path": str(path),
            "exists": path.exists(),
            "query": query,
            "matches": [],
            "error": "query must be a non-empty string",
        }

    if not path.exists():
        return {
            "ok": True,
            "path": str(path),
            "exists": False,
            "query": cleaned_query,
            "matches": [],
        }

    lowered_query = cleaned_query.lower()
    query_tokens = tuple(dict.fromkeys(_QUERY_TOKEN_RE.findall(lowered_query)))
    matches: list[dict[str, Any]] = []

    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        score = _match_score(
            line,
            lowered_query=lowered_query,
            query_tokens=query_tokens,
        )
        if score <= 0:
            continue

        matches.append(
            {
                "line": line_number,
                "score": score,
                "snippet": _truncate_snippet(line),
            }
        )

    matches.sort(key=lambda match: (-match["score"], match["line"]))
    return {
        "ok": True,
        "path": str(path),
        "exists": True,
        "query": cleaned_query,
        "matches": matches[:effective_max_results],
    }


def _clamp_max_results(max_results: int | None) -> int:
    if max_results is None:
        return DEFAULT_MEMORY_SEARCH_MAX_RESULTS
    return max(1, min(max_results, MAX_MEMORY_SEARCH_RESULTS))


def _match_score(
    line: str,
    *,
    lowered_query: str,
    query_tokens: tuple[str, ...],
) -> float:
    lowered_line = line.lower()
    score = 0.0
    if lowered_query in lowered_line:
        score += 100.0

    token_hits = sum(1 for token in query_tokens if token in lowered_line)
    if token_hits:
        score += float(token_hits)

    return score


def _truncate_snippet(line: str) -> str:
    if len(line) <= MAX_MEMORY_SNIPPET_CHARS:
        return line
    return f"{line[: MAX_MEMORY_SNIPPET_CHARS - 3]}..."
