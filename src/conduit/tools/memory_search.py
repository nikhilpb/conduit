"""A simple local-memory search tool for ADK agents."""

from __future__ import annotations

from typing import Any

from conduit.agent_memory import resolve_memory_file_path
from conduit.agent_memory import search_memory_file
from conduit.config import Settings


def build_memory_search_tool(settings: Settings):
    """Create a configured search tool for the local memory Markdown file."""

    async def memory_search(
        query: str,
        max_results: int | None = None,
    ) -> dict[str, Any]:
        """Search the local memory file for matching notes."""

        path = resolve_memory_file_path(settings.memory_file_path)
        return search_memory_file(
            path,
            query,
            max_results=max_results,
        )

    return memory_search
