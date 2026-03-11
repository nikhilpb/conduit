import pytest

from conduit.config import Settings
from conduit.tools import bash as bash_module


@pytest.mark.anyio
async def test_bash_tool_returns_stdout_and_exit_code(tmp_path):
    tool = bash_module.build_bash_tool(
        Settings(
            _env_file=None,
            bash_timeout_seconds=5.0,
            bash_max_output_chars=200,
        )
    )

    result = await tool(
        "printf 'hello from bash'",
        working_directory=str(tmp_path),
    )

    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["stdout"] == "hello from bash"
    assert result["stderr"] == ""
    assert result["timed_out"] is False
    assert result["working_directory"] == str(tmp_path.resolve())


@pytest.mark.anyio
async def test_bash_tool_returns_error_payload_for_non_zero_exit():
    tool = bash_module.build_bash_tool(
        Settings(
            _env_file=None,
            bash_timeout_seconds=5.0,
            bash_max_output_chars=200,
        )
    )

    result = await tool("printf 'bad'; printf 'worse' >&2; exit 7")

    assert result["ok"] is False
    assert result["exit_code"] == 7
    assert result["stdout"] == "bad"
    assert result["stderr"] == "worse"
    assert result["error"] == "Command exited with status 7."


@pytest.mark.anyio
async def test_bash_tool_returns_timeout_payload():
    tool = bash_module.build_bash_tool(
        Settings(
            _env_file=None,
            bash_timeout_seconds=0.05,
            bash_max_output_chars=200,
        )
    )

    result = await tool("sleep 1")

    assert result["ok"] is False
    assert result["timed_out"] is True
    assert result["error"] == "Command timed out after 0.05 seconds."
    assert result["timeout_seconds"] == 0.05


@pytest.mark.anyio
async def test_bash_tool_returns_error_for_invalid_working_directory(tmp_path):
    tool = bash_module.build_bash_tool(Settings(_env_file=None))

    result = await tool(
        "pwd",
        working_directory=str(tmp_path / "missing"),
    )

    assert result["ok"] is False
    assert result["exit_code"] is None
    assert result["error"] == f"working_directory does not exist: {tmp_path / 'missing'}"
