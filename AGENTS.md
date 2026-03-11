# Conduit Agent Notes

[VERY IMPORTANT] For each new commit reflect on whether `AGENTS.md` can be updated for future agent sessions. Only important changes should go in the file.

[VERY IMPORTANT] After completing work in a git worktree, always commit the changes and open a pull request unless the user explicitly asks not to.

Prefer this file for the current implementation state. [DESIGN.md](/Users/nikhilbhat/git/conduit/DESIGN.md) includes broader future intent and may be ahead of the code.

## Repo Snapshot

- Single-user private assistant for a Tailscale-only deployment.
- Backend: Python, FastAPI, Google ADK `1.26.0`, SQLite persistence, `uv` for package/runtime management.
- Client: Flutter Android thin client in `client/think_client/`.
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
- Supported base models:
  - `Claude Opus 4.6`
  - `Claude Sonnet 4.6`
  - `Gemini 3 Flash` (`gemini-3-flash-preview`)
  - `Gemini 3.1 Pro` (`gemini-3.1-pro-preview`)
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
- `src/conduit/model_registry.py`
  - Loads/persists model options and active model from `config/models.yaml`.
- `src/conduit/user_context.py`
  - Converts client context into ADK state delta and hidden model instructions.
- `src/conduit/tool_permissions.py`
  - Loads `allow` / `ask` / `deny` policy from `config/tools.yaml`.
  - Enforces that `bash` stays approval-gated even if configured as `allow`.
- `src/conduit/recipe_catalog.py`
  - Resolves the configured recipe catalog path and ranks recipe matches.
- `src/conduit/tools/bash.py`
  - Executes `bash -lc` on the host with structured stdout/stderr, timeout, and exit-code results.
- `src/conduit/tools/polymarket.py`
  - Public Polymarket Gamma/CLOB API integration for market lookup and pricing history.
- `src/conduit/tools/recipe_lookup.py`
  - Local recipe catalog lookup tool.

## Client Structure

- `client/think_client/lib/main.dart`
  - Main app, session list, chat screen, settings screen, most UI logic.
- `client/think_client/lib/conduit_api.dart`
  - HTTP client and websocket transport.
- `client/think_client/lib/models.dart`
  - DTOs for health, sessions, transcript, websocket events, model settings.
- `client/think_client/lib/settings_store.dart`
  - Local persistence for server URL, location, personal instructions.

## Implemented UX/Protocol Decisions

- Sessions are lazy-created from the first sent message; opening “New session” alone does not create one.
- Session title is derived from the first user message.
- Session list/settings still use HTTP; chat uses websocket.
- Assistant markdown is rendered, not shown raw.
- Tool calls get explicit UI treatment; approval requests are surfaced inline.
- The Flutter client hides internal `adk_request_confirmation` tool-call transcript items; approvals only appear through the dedicated approval UI.
- Standalone tool-call transcript items render as chips without an enclosing chat bubble; `bash` chips are labeled as `Bash(<truncated command>)`.
- Tool results are tracked separately from tool invocations; failed tool calls remain visible in the transcript and render in red in the client.
- `bash` tool results now preserve sanitized runtime payloads (`stdout`, `stderr`, `exit_code`, timeout metadata) through websocket replay and session transcripts, and the Flutter client renders those details inline for completed bash calls.
- The websocket/interactive chat runner exposes `bash`; the plain HTTP `/chat` runner intentionally excludes `bash` because that surface cannot complete approval handshakes.
- Chat composer shows the currently active model label.
- Current server URL comes from `--dart-define=CONDUIT_SERVER_URL=...` on first launch, but user settings can override later.

## Tool Failure Semantics

- `bash` returns structured results for non-zero exits, invalid working directories, spawn failures, and timeouts instead of raising; stdout/stderr are truncated to a server-side cap.
- `web_fetch` returns structured error payloads for invalid URLs, HTTP status failures, and network failures instead of raising; the agent can continue the turn after a failed fetch.
- Tool-call records now carry `tool_call_id`, `status`, and optional `error` across HTTP transcript responses and websocket replay state.

## Configuration + Runtime

- Environment comes from `.env` plus `CONDUIT_*` vars via `src/conduit/config.py`.
- Important secrets:
  - `ANTHROPIC_API_KEY`
  - `GOOGLE_API_KEY` or `GEMINI_API_KEY`
  - `BRAVE_API_KEY`
- Important paths:
  - DB: `data/conduit.db`
  - model config: `config/models.yaml`
  - recipe catalog config: `config/recipes.yaml`
  - tool permissions: `config/tools.yaml`
- Default backend bind: `0.0.0.0:18423`
- Docker Compose mounts `./data` and `./config` into the container and publishes `18423`.

## ADK Web

- ADK Web entrypoint is `adk_agents/conduit_app/agent.py`.
- `conduit_app` exists to avoid a package-name collision with the installed Python package `conduit`.
- ADK Web reads the same model config at startup, but it is a separate process; restarting ADK Web is required after a server-side model change if you want both to match.

## Commands

- Backend dev:
  - `uv run conduit-api`
- Backend tests:
  - `uv run pytest`
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
