# Conduit Agent Notes

[WRITING STYLE] This file describes the **current state** of the project as a standalone reference. Do not write it as a changelog or sequence of diffs (e.g. "added X", "changed Y to Z"). Each section should read as if written from scratch today, so a future agent with no prior context can understand the project immediately.

[VERY IMPORTANT] For each new commit reflect on whether `AGENTS.md` can be updated for future agent sessions. Only important changes should go in the file.

[VERY IMPORTANT] After completing work in a git worktree, always commit the changes and open a pull request unless the user explicitly asks not to.

[VERY IMPORTANT] Always show proof of work using screenshots in the final response when the completed work can be exercised visually. Prefer emulator/device screenshots for client changes and capture the most relevant visible evidence for other user-facing behavior.

Prefer this file for the current implementation state. [DESIGN.md](DESIGN.md) includes broader future intent and may be ahead of the code.

## Repo Snapshot

- Single-user private assistant for a Tailscale-only deployment.
- Backend: Python, FastAPI, Google ADK `1.26.0`, SQLite persistence, `uv` for package/runtime management.
- Client: Flutter Android thin client in `flutter/`.
- Deployment: Docker Compose on port `18423`.

## Current Product Shape

- One ADK agent only. No router/specialist hierarchy is implemented yet.
- Tooling is currently limited to:
  - `bash`: executes arbitrary `bash -lc` commands on the host and returns structured stdout/stderr, exit status, and timeout metadata. This tool always requires user approval before execution.
  - `web_search`: Brave Search API first, Ecosia HTML fallback.
  - `web_fetch`: HTTP/HTML/text fetch with cleaned content extraction.
  - `polymarket_search_markets` / `polymarket_list_markets` / `polymarket_get_market` / `polymarket_get_price_history`: public Polymarket market lookup, current pricing, price history, liquidity, and volume snapshots.
  - `recipe_lookup`: read-only lookup against a local `recipes.json` catalog when `config/recipes.yaml` resolves to an existing file.
  - Agent instruction biases future-looking probability questions toward the Polymarket tools when relevant.
- Model choice is server-owned and persisted in `config/models.yaml`.
- Headless scheduled sessions can be configured on the backend via `config/scheduled_sessions.yaml`; each scheduled run uses its configured raw model name, UTC cron schedule, seed query, and allowed tool list.
- Scheduled sessions keep an internal rolling memory of the last 7 stored run summaries for the same scheduled job. Each successful scheduled run triggers a second no-tool LLM call with the same raw model to summarize the final assistant reply, stores that summary in SQLite, and injects the prior summaries back into later runs as hidden context.
- The repo default scheduled config currently includes `iran-us-conflict-news`, which runs daily at `06:00 UTC` using `claude-opus-4-6` with `web_search`, `web_fetch`, and all Polymarket tools.
- Supported base models:
  - `Claude Opus 4.6`
  - `Claude Sonnet 4.6`
  - `Gemini 3 Flash` (`gemini-3-flash-preview`)
  - `Gemini 3.1 Pro` (`gemini-3.1-pro-preview`)
  - `GPT-5.4` (`gpt-5.4`)
  - `GPT-5.4 mini` (`gpt-5.4-mini`)
- OpenAI requests are routed through ADK's `LiteLlm` wrapper (backed by the `litellm` library) with the `openai/` model prefix.
- Anthropic requests use manual extended thinking via `src/conduit/anthropic_extended_thinking.py`.
- The client receives thinking traces as separate data and renders them collapsibly; they are not merged into the visible assistant answer.
- Per-turn hidden context is injected from the client:
  - current local time
  - saved location
  - saved personal instructions

## Backend Structure

- `src/conduit/main.py`
  - FastAPI entrypoint.
  - HTTP: `/health`, `/settings/model`, `/sessions`, `/sessions/{id}`, `/chat`.
  - WebSocket: `/chat`.
- `src/conduit/runtime.py`
  - ADK `App` + `Runner` wrapper.
  - Applies model registry changes live.
  - Uses `ResumabilityConfig(is_resumable=True)`.
  - Loads the last 7 stored summaries for a scheduled job into hidden per-turn context before each scheduled run and performs the post-run internal summary pass.
- `src/conduit/agent.py`
  - Builds the single root agent.
  - Wires `before_model_callback` for hidden context injection.
  - Wires `before_tool_callback` for permission policy.
- `src/conduit/websocket_chat.py`
  - Own websocket protocol layer.
  - Handles `ack`, `tool_call`, `tool_result`, `thought`, `token`, `done`, `approval_required`, `error`.
  - Replays completed turns and reattaches to in-flight turns by `message_id`.
- `src/conduit/sessions/sqlite_service.py`
  - Custom ADK `BaseSessionService`.
  - Persists ADK sessions/events plus `client_turns` for websocket replay/idempotency.
  - Stores scheduled-session summary memory in a dedicated `scheduled_session_summaries` table keyed by source scheduled session id.
- `src/conduit/model_registry.py`
  - Loads/persists model options and active model from `config/models.yaml`.
- `src/conduit/scheduled_sessions.py`
  - Loads/validates `config/scheduled_sessions.yaml`.
  - Runs configured scheduled sessions through an in-process APScheduler service.
- `src/conduit/user_context.py`
  - Converts client context into ADK state delta and hidden model instructions.
  - Formats prior scheduled-session summaries from temp state into hidden model instructions for scheduled runs.
- `src/conduit/context_estimate.py`
  - Deterministic character/token estimation for app-facing context usage.
  - Counts session text plus non-internal tool calls/results with a fixed chars-per-token ratio.
- `src/conduit/tool_permissions.py`
  - Loads `allow` / `ask` / `deny` policy from `config/tools.yaml`.
  - Enforces that `bash` stays approval-gated even if configured as `allow`.
- `src/conduit/schemas.py`
  - Pydantic models for the API surface (health, sessions, transcripts, chat, model settings, context estimates).
- `src/conduit/tool_call_utils.py`
  - Helpers for tool response status, sanitized bash payloads, and internal tool-call filtering.
- `src/conduit/recipe_catalog.py`
  - Resolves the configured recipe catalog path and ranks recipe matches.
- `src/conduit/tools/bash.py`
  - Executes `bash -lc` on the host with structured stdout/stderr, timeout, and exit-code results.
- `src/conduit/tools/web_search.py`
  - Brave Search API with Ecosia HTML fallback; normalizes navigational queries.
- `src/conduit/tools/web_fetch.py`
  - HTTP fetch with HTML cleaning via BeautifulSoup; returns structured content or error payloads.
- `src/conduit/tools/polymarket.py`
  - Public Polymarket Gamma/CLOB API integration for market lookup and pricing history.
- `src/conduit/tools/recipe_lookup.py`
  - Local recipe catalog lookup tool.

## Client Structure

- `flutter/lib/main.dart`
  - Main app, session list, chat screen, settings screen, most UI logic.
- `flutter/lib/conduit_api.dart`
  - HTTP client and websocket transport.
- `flutter/lib/models.dart`
  - DTOs for health, sessions, transcript, websocket events, model settings.
- `flutter/lib/settings_store.dart`
  - Local persistence for server URL, location, personal instructions.
- `flutter/lib/context_estimate.dart`
  - Client-side helpers for hidden-context estimation, tool-call char counting, and composer usage-bar formatting.

## Implemented UX/Protocol Decisions

- Sessions are lazy-created from the first sent message; opening “New session” alone does not create one.
- Scheduled runs create a fresh session per trigger and store the seed query as the first normal user event.
- Scheduled runs also inject their scheduler fire time in UTC into the same per-turn current-time context channel that interactive turns use.
- Scheduled runs inject up to 7 prior stored summaries for the same scheduled job as hidden context, ordered oldest-to-newest within that window.
- Session title is derived from the first user message.
- Session list/settings still use HTTP; chat uses websocket.
- Assistant markdown is rendered, not shown raw.
- Tool calls get explicit UI treatment; approval requests are surfaced inline.
- The Flutter client hides internal `adk_request_confirmation` transcript items entirely once their hidden tool calls are stripped; approvals only appear through the dedicated approval UI.
- Standalone tool-call transcript items render as chips without an enclosing chat bubble; `bash` chips are labeled as `Bash(<truncated command>)`.
- Tool results are tracked separately from tool invocations; failed tool calls remain visible in the transcript and render in red in the client.
- Session records include `session_kind` (`interactive` or `scheduled`) and an optional `scheduled_job_id`.
- `bash` tool results preserve sanitized runtime payloads (`stdout`, `stderr`, `exit_code`, timeout metadata) through websocket replay and session transcripts, but the Flutter client does not render inline bash output; it keeps a single bash invocation chip in history and in live turns.
- The websocket/interactive chat runner exposes `bash`; the plain HTTP `/chat` runner intentionally excludes `bash` because that surface cannot complete approval handshakes.
- Scheduled runners are separate headless ADK runners: they use only their configured `allowed_tools` list and auto-approve those tools, including `bash`.
- Chat composer shows the currently active model label.
- Chat composer shows an estimated next-turn context token count and a soft usage bar based on completed session history, the current draft, and hidden per-turn context.
- `/health` exposes `context_chars_per_token`; session detail / HTTP chat responses expose `context_estimate`; websocket `tool_result` events expose `context_chars_delta` and `done` events include an authoritative `context_estimate`.
- Current server URL comes from `--dart-define=CONDUIT_SERVER_URL=...` on first launch, but user settings can override later.

## Tool Failure Semantics

- `bash` returns structured results for non-zero exits, invalid working directories, spawn failures, and timeouts instead of raising; stdout/stderr are truncated to a server-side cap.
- `web_fetch` returns structured error payloads for invalid URLs, HTTP status failures, and network failures instead of raising; the agent can continue the turn after a failed fetch.
- Tool-call records carry `tool_call_id`, `status`, and optional `error` across HTTP transcript responses and websocket replay state.

## Configuration + Runtime

- Environment comes from `.env` plus `CONDUIT_*` vars via `src/conduit/config.py`.
- Important secrets:
  - `ANTHROPIC_API_KEY`
  - `GOOGLE_API_KEY` or `GEMINI_API_KEY`
  - `BRAVE_API_KEY`
  - `OPENAI_API_KEY` for GPT-5.4 / GPT-5.4 mini model routing and the in-container Codex CLI
- Important paths:
  - DB: `data/conduit.db`
  - model config: `config/models.yaml`
  - scheduled session config: `config/scheduled_sessions.yaml`
  - recipe catalog config: `config/recipes.yaml`
  - tool permissions: `config/tools.yaml`
  - Codex workspace mount in Docker: `/workspace`
- Default backend bind: `0.0.0.0:18423`
- Docker Compose mounts the repo root at `/workspace`, plus `./data` and `./config`, and publishes `18423`.

## ADK Web

- ADK Web entrypoint is `adk_agents/conduit_app/agent.py`.
- `conduit_app` exists to avoid a package-name collision with the installed Python package `conduit`.
- ADK Web reads the same model config at startup, but it is a separate process; restarting ADK Web is required after a server-side model change if you want both to match.

## Commands

- Backend dev:
  - `uv run conduit-api`
- Backend tests:
  - `uv run pytest`
  - Pytest sets `CONDUIT_SCHEDULED_SESSIONS_CONFIG_PATH` at `tests/conftest.py` import time to an empty temp file by default, so importing `conduit.main` during collection does not require real provider credentials; scheduled-session tests opt into explicit configs when needed.
- In-container Codex:
  - `docker compose exec -w /workspace conduit-api codex`
- GitHub PR checks:
  - backend: `uv sync --locked --dev`, `uv run pytest`, `docker build .`
  - client: `flutter pub get`, `flutter analyze`, `flutter test`, `flutter build apk --debug`
- ADK Web:
  - `uv run adk web adk_agents --host 127.0.0.1 --port 4201`
- Flutter checks:
  - `flutter analyze`
  - `flutter test`
- Flutter run examples:
  - emulator: `flutter run -d android --dart-define=CONDUIT_SERVER_URL=http://10.0.2.2:18423`
  - physical device / VM over Tailscale: `flutter run -d android --dart-define=CONDUIT_SERVER_URL=http://100.x.y.z:18423`

## Current Gaps Relative To Design

- No multi-agent router/specialists yet.
- No filesystem skill loading yet.
- Voice/image buttons exist in the client but are not wired.
- Binary artifact storage beyond text/web fetch is not implemented.
- `DESIGN.md` describes a broader final architecture; do not assume it is already built.
