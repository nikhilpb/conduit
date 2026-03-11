"""A Bash execution tool for ADK agents."""

from __future__ import annotations

import asyncio
from pathlib import Path
from time import monotonic
from typing import Any

from conduit.config import Settings


def build_bash_tool(settings: Settings):
    """Create a configured Bash tool closure."""

    async def bash(
        command: str,
        working_directory: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Run a Bash command on the host computer.

        Args:
            command: Bash source executed with `bash -lc`.
            working_directory: Optional directory to run the command in.
            timeout_seconds: Optional timeout override, capped by the server setting.

        Returns:
            Structured stdout, stderr, exit status, and timeout metadata.
        """

        if not command.strip():
            return _error_result(
                command=command,
                working_directory=working_directory,
                timeout_seconds=timeout_seconds,
                message="command must be a non-empty string",
            )

        resolved_working_directory, working_directory_error = _resolve_working_directory(
            working_directory
        )
        if working_directory_error:
            return _error_result(
                command=command,
                working_directory=working_directory,
                timeout_seconds=timeout_seconds,
                message=working_directory_error,
            )

        effective_timeout, timeout_error = _resolve_timeout(
            requested_timeout=timeout_seconds,
            max_timeout=settings.bash_timeout_seconds,
        )
        if timeout_error:
            return _error_result(
                command=command,
                working_directory=str(resolved_working_directory)
                if resolved_working_directory
                else working_directory,
                timeout_seconds=timeout_seconds,
                message=timeout_error,
            )

        started_at = monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                "bash",
                "-lc",
                command,
                cwd=str(resolved_working_directory) if resolved_working_directory else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return _error_result(
                command=command,
                working_directory=str(resolved_working_directory)
                if resolved_working_directory
                else working_directory,
                timeout_seconds=effective_timeout,
                message=f"{type(exc).__name__}: {exc}",
            )

        timed_out = False
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            timed_out = True
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()

        stdout, stdout_truncated = _decode_output(
            stdout_bytes,
            max_chars=settings.bash_max_output_chars,
        )
        stderr, stderr_truncated = _decode_output(
            stderr_bytes,
            max_chars=settings.bash_max_output_chars,
        )
        duration_seconds = round(monotonic() - started_at, 3)

        error: str | None = None
        ok = process.returncode == 0 and not timed_out
        if timed_out:
            error = f"Command timed out after {effective_timeout:g} seconds."
        elif process.returncode != 0:
            error = f"Command exited with status {process.returncode}."

        return {
            "ok": ok,
            "command": command,
            "working_directory": str(resolved_working_directory)
            if resolved_working_directory
            else None,
            "timeout_seconds": effective_timeout,
            "duration_seconds": duration_seconds,
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "timed_out": timed_out,
            "error": error,
        }

    return bash


def _resolve_working_directory(
    working_directory: str | None,
) -> tuple[Path | None, str | None]:
    if working_directory is None:
        return None, None

    path = Path(working_directory).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()

    if not path.exists():
        return None, f"working_directory does not exist: {working_directory}"
    if not path.is_dir():
        return None, f"working_directory is not a directory: {working_directory}"
    return path, None


def _resolve_timeout(
    *,
    requested_timeout: float | None,
    max_timeout: float,
) -> tuple[float, str | None]:
    if requested_timeout is None:
        return max_timeout, None
    if requested_timeout <= 0:
        return requested_timeout, "timeout_seconds must be greater than zero"
    return min(requested_timeout, max_timeout), None


def _decode_output(
    output: bytes | None,
    *,
    max_chars: int,
) -> tuple[str, bool]:
    text = (output or b"").decode("utf-8", errors="replace")
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _error_result(
    *,
    command: str,
    working_directory: str | None,
    timeout_seconds: float | None,
    message: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "command": command,
        "working_directory": working_directory,
        "timeout_seconds": timeout_seconds,
        "duration_seconds": 0.0,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "stdout_truncated": False,
        "stderr_truncated": False,
        "timed_out": False,
        "error": message,
    }
