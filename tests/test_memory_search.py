import pytest

from conduit.config import Settings
from conduit.tools import memory_search as memory_search_module


@pytest.mark.anyio
async def test_memory_search_returns_empty_matches_when_file_is_missing(tmp_path):
    tool = memory_search_module.build_memory_search_tool(
        Settings(
            _env_file=None,
            memory_file_path=str(tmp_path / "memory" / "memory.md"),
        )
    )

    result = await tool("concise answers")

    assert result == {
        "ok": True,
        "path": str((tmp_path / "memory" / "memory.md").resolve()),
        "exists": False,
        "query": "concise answers",
        "matches": [],
    }


@pytest.mark.anyio
async def test_memory_search_prefers_exact_substring_matches_and_reports_line_numbers(
    tmp_path,
):
    memory_path = tmp_path / "memory.md"
    memory_path.write_text(
        "\n".join(
            [
                "# Memory",
                "## User Preferences",
                "- User prefers concise answers during work hours.",
                "- User likes concise replies.",
                "## Notes",
                "- Remember to mention Zurich weather when relevant.",
            ]
        )
    )
    tool = memory_search_module.build_memory_search_tool(
        Settings(
            _env_file=None,
            memory_file_path=str(memory_path),
        )
    )

    result = await tool("concise answers")

    assert result["ok"] is True
    assert result["exists"] is True
    assert result["matches"][0] == {
        "line": 3,
        "score": 102.0,
        "snippet": "- User prefers concise answers during work hours.",
    }
    assert result["matches"][1]["line"] == 4
    assert result["matches"][1]["score"] == 1.0


@pytest.mark.anyio
async def test_memory_search_clamps_max_results(tmp_path):
    memory_path = tmp_path / "memory.md"
    memory_path.write_text(
        "\n".join(f"- concise note {index}" for index in range(12))
    )
    tool = memory_search_module.build_memory_search_tool(
        Settings(
            _env_file=None,
            memory_file_path=str(memory_path),
        )
    )

    result = await tool("concise", max_results=99)

    assert len(result["matches"]) == 10
