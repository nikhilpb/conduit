"""Codex CLI tool for ADK agents — clone, implement, and open a PR."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import uuid
from typing import Any

from conduit.config import Settings
from conduit.repos import RepoConfig
from conduit.repos import load_repos


def build_codex_tool(settings: Settings):
    """Create the codex_task closure exposed to the agent."""

    repos = load_repos(settings.repos_config_path)
    repo_keys = ", ".join(sorted(repos)) if repos else "(none configured)"

    async def codex_task(repo: str, prompt: str) -> dict[str, Any]:
        """Run OpenAI Codex on a GitHub repository to implement a task.

        Args:
            repo: Key of a configured repository (e.g. "conduit").
            prompt: The implementation task for Codex (e.g. "add input validation to the signup form").

        Returns:
            Result with PR URL on success, or error details on failure.
        """

        # --- validate repo ---
        if repo not in repos:
            return {
                "ok": False,
                "error": f"Unknown repository '{repo}'. Available: {repo_keys}",
            }

        repo_cfg: RepoConfig = repos[repo]

        # --- validate api key ---
        api_key = settings.openai_api_key
        if not api_key:
            return {
                "ok": False,
                "error": "OPENAI_API_KEY is not configured on the server.",
            }

        # --- build branch name ---
        slug = _slugify(prompt, max_len=40)
        short_id = uuid.uuid4().hex[:8]
        branch = f"codex/{slug}-{short_id}"

        work_dir = os.path.join(
            settings.codex_work_dir,
            f"{repo}-{short_id}",
        )

        try:
            os.makedirs(work_dir, exist_ok=True)

            # --- clone ---
            rc, _stdout, stderr = await _run(
                [
                    "git", "clone", "--depth=1",
                    f"--branch={repo_cfg.default_branch}",
                    repo_cfg.url, work_dir,
                ],
                cwd=settings.codex_work_dir,
                timeout=120,
            )
            if rc != 0:
                return {"ok": False, "error": f"Failed to clone: {stderr.strip()}"}

            # --- create branch ---
            rc, _stdout, stderr = await _run(
                ["git", "checkout", "-b", branch],
                cwd=work_dir,
            )
            if rc != 0:
                return {"ok": False, "error": f"Failed to create branch: {stderr.strip()}"}

            # --- run codex ---
            codex_env = {**os.environ, "OPENAI_API_KEY": api_key}
            try:
                rc, _stdout, stderr = await _run(
                    ["codex", "--approval-mode", "full-auto", "-q", prompt],
                    cwd=work_dir,
                    env=codex_env,
                    timeout=settings.codex_timeout_seconds,
                )
            except asyncio.TimeoutError:
                return {
                    "ok": False,
                    "error": f"Codex timed out after {int(settings.codex_timeout_seconds)}s",
                }

            if rc != 0:
                return {"ok": False, "error": f"Codex failed: {stderr.strip()}"}

            # --- check for changes ---
            rc, diff_out, _ = await _run(["git", "diff", "--stat"], cwd=work_dir)
            rc_staged, staged_out, _ = await _run(
                ["git", "diff", "--staged", "--stat"], cwd=work_dir
            )
            rc_untracked, untracked_out, _ = await _run(
                ["git", "status", "--porcelain"], cwd=work_dir
            )
            if not diff_out.strip() and not staged_out.strip() and not untracked_out.strip():
                return {"ok": False, "error": "Codex made no changes to the repository"}

            # --- commit ---
            await _run(["git", "add", "-A"], cwd=work_dir)
            commit_msg = prompt if len(prompt) <= 72 else prompt[:69] + "..."
            rc, _stdout, stderr = await _run(
                ["git", "commit", "-m", commit_msg],
                cwd=work_dir,
            )
            if rc != 0:
                return {"ok": False, "error": f"Failed to commit: {stderr.strip()}"}

            # --- push ---
            rc, _stdout, stderr = await _run(
                ["git", "push", "-u", "origin", branch],
                cwd=work_dir,
                timeout=120,
            )
            if rc != 0:
                return {"ok": False, "error": f"Failed to push: {stderr.strip()}"}

            # --- create PR ---
            pr_title = prompt if len(prompt) <= 72 else prompt[:69] + "..."
            pr_body = (
                f"Automated PR created by Conduit codex_task tool.\n\n"
                f"**Prompt:** {prompt}"
            )
            rc, pr_stdout, stderr = await _run(
                [
                    "gh", "pr", "create",
                    "--title", pr_title,
                    "--body", pr_body,
                    f"--base={repo_cfg.default_branch}",
                ],
                cwd=work_dir,
                timeout=60,
            )
            if rc != 0:
                return {"ok": False, "error": f"Failed to create PR: {stderr.strip()}"}

            pr_url = pr_stdout.strip()
            pr_number = _extract_pr_number(pr_url)
            message = f"PR #{pr_number} created" if pr_number else "PR created"

            return {
                "ok": True,
                "pr_number": pr_number,
                "pr_url": pr_url,
                "message": message,
            }

        finally:
            # Clean up the temp clone
            shutil.rmtree(work_dir, ignore_errors=True)

    return codex_task


async def _run(
    cmd: list[str],
    *,
    cwd: str,
    env: dict[str, str] | None = None,
    timeout: float = 60,
) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode, stdout.decode(), stderr.decode()


def _slugify(text: str, max_len: int = 40) -> str:
    """Convert text to a branch-name-safe slug."""

    slug = text.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "task"


def _extract_pr_number(pr_url: str) -> int | None:
    """Extract the PR number from a GitHub PR URL."""

    match = re.search(r"/pull/(\d+)", pr_url)
    return int(match.group(1)) if match else None
