# Codex Tool

Spawns a headless OpenAI Codex CLI agent to work on a configured GitHub repository — clone, implement changes, commit, push, and open a pull request.

## Scope

V1: single-turn only. The user says "use codex to implement feature X on conduit" and gets back "PR #N created". No multi-turn codex interaction. Host-only (not Docker).

## Tool Interface

```
codex_task(repo: str, prompt: str) -> dict
```

- **repo**: Key from `config/repos.yaml` (e.g. `"conduit"`).
- **prompt**: Implementation task for Codex (e.g. `"add input validation to the signup form"`).

Returns a dict with `ok: bool` plus either success or error details.

## Execution Flow

1. Validate `repo` against configured repos. Error if unknown.
2. Create temp dir under `settings.codex_work_dir` (default `/tmp/conduit-codex`).
3. `git clone --depth=1 --branch <default_branch> <url> <temp_dir>`.
4. Create branch: `codex/<slug>-<8char-uuid>` (slug from first ~40 chars of prompt).
5. `git checkout -b <branch>`.
6. Run: `codex --approval-mode full-auto -q "<prompt>"` in the cloned dir.
   - Env: inherit `OPENAI_API_KEY` from settings.
   - Timeout: `settings.codex_timeout_seconds` (default 300s).
7. Check `git diff` — if no changes, return error.
8. `git add -A && git commit -m "<prompt>"`.
9. `git push -u origin <branch>`.
10. `gh pr create --title "<prompt>" --body "..." --base <default_branch>`.
11. Clean up temp dir (in `finally` block).
12. Return `{"ok": True, "pr_number": N, "pr_url": "...", "message": "PR #N created"}`.

## Configuration

### config/repos.yaml

```yaml
repos:
  conduit:
    url: https://github.com/nikhilpb/conduit
    default_branch: main
```

### Settings (config.py)

| Field | Default | Description |
|-------|---------|-------------|
| `codex_timeout_seconds` | `300.0` | Max seconds for the codex CLI process |
| `codex_work_dir` | `/tmp/conduit-codex` | Parent directory for temp clones |
| `repos_config_path` | `config/repos.yaml` | Path to repo configuration |
| `openai_api_key` | `None` | API key for OpenAI Codex CLI |

### Tool Permission

`codex_task` defaults to `ask` mode — the user must confirm before execution since it creates PRs.

## Error Handling

All errors are returned as `{"ok": False, "error": "<message>"}` (non-raising, consistent with `web_fetch`):

| Scenario | Error message |
|----------|---------------|
| Unknown repo | `Unknown repository 'foo'. Available: conduit, other-repo` |
| Clone failure | `Failed to clone: <stderr>` |
| Codex timeout | `Codex timed out after 300s` |
| Codex non-zero exit | `Codex failed: <stderr>` |
| No changes | `Codex made no changes to the repository` |
| Push/PR failure | `Failed to create PR: <stderr>` |

## Host Prerequisites

The host running Conduit needs:
- `codex` CLI installed and on PATH (`npm install -g @openai/codex`)
- `gh` CLI installed and authenticated (`gh auth login`)
- `git` configured with push credentials for target repos
- `OPENAI_API_KEY` set in `.env`
