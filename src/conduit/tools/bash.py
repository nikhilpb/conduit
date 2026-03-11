"""A Bash execution tool for ADK agents."""

from __future__ import annotations

import asyncio
import codecs
from pathlib import Path
from time import monotonic
from typing import Any

from conduit.config import Settings

_READ_CHUNK_SIZE = 4096


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
        stdout_task = asyncio.create_task(
            _capture_stream(
                process.stdout,
                max_chars=settings.bash_max_output_chars,
            )
        )
        stderr_task = asyncio.create_task(
            _capture_stream(
                process.stderr,
                max_chars=settings.bash_max_output_chars,
            )
        )
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            timed_out = True
            process.kill()
            await process.wait()

        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task
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


async def _capture_stream(
    stream: asyncio.StreamReader | None,
    *,
    max_chars: int,
) -> tuple[str, bool]:
    if stream is None:
        return "", False

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    parts: list[str] = []
    captured_chars = 0
    truncated = False

    while True:
        chunk = await stream.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        text = decoder.decode(chunk)
        captured_chars, truncated = _append_capped_text(
            parts,
            text,
            captured_chars=captured_chars,
            max_chars=max_chars,
            truncated=truncated,
        )

    tail = decoder.decode(b"", final=True)
    captured_chars, truncated = _append_capped_text(
        parts,
        tail,
        captured_chars=captured_chars,
        max_chars=max_chars,
        truncated=truncated,
    )
    return "".join(parts), truncated


def _append_capped_text(
    parts: list[str],
    text: str,
    *,
    captured_chars: int,
    max_chars: int,
    truncated: bool,
) -> tuple[int, bool]:
    if not text:
        return captured_chars, truncated

    remaining = max(max_chars - captured_chars, 0)
    if remaining > 0:
        parts.append(text[:remaining])
    if len(text) > remaining:
        truncated = True
    return captured_chars + len(text), truncated


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
