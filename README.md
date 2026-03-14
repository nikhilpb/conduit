# Conduit

[![PR Checks](https://github.com/nikhilpb/conduit/actions/workflows/pr-checks.yml/badge.svg)](https://github.com/nikhilpb/conduit/actions/workflows/pr-checks.yml)

A single-user personal assistant agent deployed on a private Tailscale network. Uses Google ADK for agent orchestration, FastAPI for the API layer, SQLite for persistence, and a Flutter thin client on Android.

```
Android (Flutter)  ──WebSocket──▶  FastAPI :18423  ──▶  Google ADK Runtime
                                        │                     │
                                        ▼                     ▼
                                   SQLite DB           LLM APIs (Claude,
                                                       Gemini, GPT)
```

## Architecture

- **Backend:** Python, FastAPI, Google ADK, SQLite, managed with `uv`.
- **Client:** Flutter Android app in `flutter/`.
- **Deployment:** Docker Compose, Tailscale-only access on port `18423`.
- **Models:** Claude Opus 4.6, Claude Sonnet 4.6, Gemini 3 Flash, Gemini 3.1 Pro — switchable at runtime via `config/models.yaml`.
- **Built-in tools:** `web_search`, `web_fetch`, `bash` (host command execution with mandatory approval), and public Polymarket market lookup/price-history tools.
- **Scheduled sessions:** Optional headless sessions can be configured in `config/scheduled_sessions.yaml` with a cron schedule, raw model name, seed query, and per-job allowed tools.

See [DESIGN.md](DESIGN.md) for the full design document.

## Running the Backend (Docker)

### Prerequisites

- Docker and Docker Compose
- API keys for at least one LLM provider

### Setup

1. Copy the example environment file and fill in your credentials:

   ```bash
   cp .env.example .env
   # Edit .env with your API keys:
   #   ANTHROPIC_API_KEY, GOOGLE_API_KEY, BRAVE_API_KEY
   ```

2. To bind the server to a Tailscale IP (recommended for production), set `CONDUIT_PUBLISH_IP` in `.env`:

   ```bash
   CONDUIT_PUBLISH_IP=100.x.y.z
   ```

   The default is `127.0.0.1` (localhost only).

3. Build and start:

   ```bash
   docker compose up -d --build
   ```

4. Verify it's running:

   ```bash
   curl http://127.0.0.1:18423/health
   ```

### Data and Configuration

Docker Compose mounts two host directories into the container:

| Host Path | Container Path | Purpose |
|-----------|---------------|---------|
| `./data`  | `/app/data`   | SQLite database (`conduit.db`) |
| `./config`| `/app/config` | Model config (`models.yaml`), tool permissions (`tools.yaml`), scheduled sessions (`scheduled_sessions.yaml`) |

Both persist across container rebuilds.

`config/tools.yaml` controls per-tool permissions. The `bash` tool is always approval-gated even if it is configured as `allow`.

`config/scheduled_sessions.yaml` configures optional headless scheduled runs. Each entry defines `id`, `schedule` (5-field cron in the backend process timezone), `model`, `seed_query`, and `allowed_tools`. Each trigger creates a fresh scheduled session in the main SQLite session history.

### Running without Docker

```bash
# Install dependencies
uv sync

# Start the server
uv run conduit-api
```

## Running the Flutter Client

### Prerequisites

- Flutter SDK
- Android SDK / emulator or a physical Android device

### Build and Run

```bash
cd flutter

# Get dependencies
flutter pub get

# Run on an emulator (uses Android's 10.0.2.2 to reach host localhost)
flutter run -d android --dart-define=CONDUIT_SERVER_URL=http://10.0.2.2:18423

# Run on a physical device over Tailscale
flutter run -d android --dart-define=CONDUIT_SERVER_URL=http://100.x.y.z:18423
```

The server URL can also be changed later from the in-app settings screen.

### Checks

```bash
cd flutter
flutter analyze
flutter test
```

## Backend Development

```bash
# Run the server locally
uv run conduit-api

# Run tests
uv run pytest

# Run ADK Web dev UI (port 4201)
uv run adk web adk_agents --host 127.0.0.1 --port 4201
```

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | Claude API access | — |
| `GOOGLE_API_KEY` | Gemini API access | — |
| `BRAVE_API_KEY` | Web search (Brave) | — |
| `CONDUIT_PUBLISH_IP` | Docker host-side bind IP | `127.0.0.1` |
| `CONDUIT_HOST` | Server bind address | `0.0.0.0` |
| `CONDUIT_PORT` | Server port | `18423` |
| `CONDUIT_DB_PATH` | SQLite database path | `data/conduit.db` |
