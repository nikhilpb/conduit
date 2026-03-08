# Conduit — Design Document

*A personal assistant agent that channels your intent to the right specialist.*

**Stack:** Google ADK + FastAPI + Flutter + SQLite  
**Deployment:** Private Tailscale network on GCP  
**Status:** Draft · March 2026

---

## 1. Overview

A single-user personal assistant agent deployed on a private GCP virtual machine, accessible exclusively via Tailscale. The system uses Google's Agent Development Kit (ADK) for agent orchestration, FastAPI for the API layer, SQLite for session persistence, and a Flutter thin client for the Android front end.

The assistant supports a pluggable architecture where specialist agents can be added, removed, or reconfigured without changes to the core infrastructure. It routes user requests to the appropriate specialist via LLM-driven dynamic delegation, with the ability to switch between Anthropic, Google, and OpenAI models on a per-agent basis.

### 1.1 Design Principles

- **Single-user by design.** One operator, one shared session namespace. No app-level identity, user accounts, or multi-tenant data model.
- **Private by default.** No public endpoints. All traffic flows over Tailscale. Tailnet membership is the trust boundary.
- **Thin client, thick server.** The Flutter app is a chat UI with affordances for voice input and image attachments. All intelligence lives on the server.
- **Pluggable agents.** Adding a new specialist agent is a small file-plus-config change on the server. No changes to routing, state, or the client.
- **Filesystem skills.** ADK skills live on disk and are attached statically to specialists at startup for reusable instructions and references.
- **Model-agnostic.** Each agent can use a different LLM provider (Claude, Gemini, GPT). Model selection is a configuration concern, not an architectural one.
- **Minimal infrastructure.** SQLite, not Postgres. Docker Compose, not Kubernetes. Designed for a single user with a few concurrent clients, not 2,000 users.

### 1.2 Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Agent framework | Google ADK | Native multi-agent hierarchy, LiteLLM for model switching, MCP support, workflow agents (Sequential, Parallel, Loop), built-in dev UI |
| API layer | FastAPI + WebSocket | Async-native, WebSocket for streaming |
| Persistence | SQLite | Single-file DB, zero ops, sufficient for a single user and a few concurrent clients. WAL mode for concurrent reads |
| Client | Flutter (Android) | Single codebase extensible to iOS later |
| Network | Tailscale | Replaces auth entirely. WireGuard encryption at the network layer |
| Identity model | Single-user | No user accounts. Tailnet access implies full access to the assistant |
| Skills | ADK filesystem skills | Reusable instruction/resource bundles loaded from `skills/` and attached statically to specialists |
| Tool permissions | Server-side policy + client/CLI approval | Per-tool `allow` / `ask` / `deny`, with approval prompts surfaced only in clients |
| Deployment | Docker Compose on GCP VM | Co-located with existing services |

---

## 2. System Architecture

The system follows a three-tier architecture: thin client, API gateway, and agent runtime. All components communicate over Tailscale's WireGuard mesh.

### 2.1 Component Diagram

```
┌──────────────────────────────────────────────────────┐
│  Android Device (on Tailscale)                       │
│  ┌────────────────────────────────────────────────┐  │
│  │  Flutter Chat App                              │  │
│  │  • Text input + send                           │  │
│  │  • Voice input (STT)                           │  │
│  │  • Image attachment                            │  │
│  │  • Streaming message display                   │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
              │ WebSocket (Tailscale IP)
              ▼
┌──────────────────────────────────────────────────────┐
│  GCP VM (on Tailscale)                               │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │  FastAPI Gateway  :18423                       │  │
│  │  • WebSocket /chat                             │  │
│  │  • REST /sessions, /health                     │  │
│  └────────────────────────────────────────────────┘  │
│          │                                           │
│          ▼                                           │
│  ┌────────────────────────────────────────────────┐  │
│  │  Google ADK Runtime                            │  │
│  │  ┌──────────────────────────────────────────┐  │  │
│  │  │  Router Agent (Gemini Flash)             │  │  │
│  │  │    ├── Specialist Agent A                │  │  │
│  │  │    ├── Specialist Agent B                │  │  │
│  │  │    ├── Specialist Agent C                │  │  │
│  │  │    └── ...                               │  │  │
│  │  └──────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────┘  │
│          │                │                          │
│          ▼                ▼                          │
│  ┌───────────────┐  ┌─────────────────────────┐     │
│  │ SQLite DB     │  │ MCP Servers             │     │
│  │ (sessions,    │  │ • Local (localhost)      │     │
│  │  messages)    │  │ • Remote (internet)      │     │
│  └───────────────┘  └─────────────────────────┘     │
│          │                                           │
│          ▼                                           │
│  ┌────────────────────────────────────────────────┐  │
│  │  LLM APIs (outbound internet)                  │  │
│  │  • Anthropic (Claude)                          │  │
│  │  • Google (Gemini)                             │  │
│  │  • OpenAI (GPT)                                │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

### 2.2 Network Topology

All components communicate over Tailscale's WireGuard mesh network. The GCP VM binds FastAPI to its Tailscale IP (100.x.y.z) on port 18423. The Flutter client connects to this IP directly. No DNS, no TLS termination, no reverse proxy needed for this service — Tailscale encrypts all traffic at the network layer.

The VM retains outbound internet access for LLM API calls (Anthropic, Google, OpenAI). Existing services on the VM are unaffected.

### 2.3 Local and Remote Integrations

MCP servers running on the same GCP VM should be accessed via localhost rather than their public URLs. This avoids unnecessary round-trips through the public internet and removes external availability dependencies.

Gmail and Google Calendar are not treated as remote hosted MCP dependencies in this design. Their execution layer lives on the same VM, either as local CLI-backed tools or local services invoked by the server. ADK skills provide reusable instructions and references for these integrations, but the executable integration remains a regular tool or MCP server.

| Integration | Access Path |
|-------------|-------------|
| Self-hosted MCP servers | `localhost:PORT` (internal) |
| Gmail | Local CLI wrapper or localhost service |
| Google Calendar | Local CLI wrapper or localhost service |
| Notion | `mcp.notion.com/mcp` (internet) |
| Linear | `mcp.linear.app/mcp` (internet) |

---

## 3. Backend

### 3.1 Project Structure

```
conduit/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── config/
│   ├── models.yaml          # model assignments per agent
│   ├── agents.yaml          # agent registry + instructions + attached skills
│   └── tools.yaml           # per-tool permission policy
├── skills/
│   ├── gmail/
│   │   ├── SKILL.md
│   │   └── references/
│   └── calendar/
│       ├── SKILL.md
│       └── references/
├── src/
│   ├── main.py              # FastAPI app + WebSocket
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── root.py          # router agent definition
│   │   └── specialists/     # one file per specialist
│   │       ├── research.py
│   │       └── ...
│   ├── tools/               # tool functions
│   │   ├── web_search.py
│   │   ├── gmail_cli.py
│   │   ├── calendar_cli.py
│   │   ├── permissions.py   # allow / ask / deny enforcement
│   │   └── ...
│   ├── sessions/
│   │   └── sqlite_service.py  # custom SessionService
│   ├── models/
│   │   └── config.py        # model registry + LiteLLM
│   └── cli/
│       ├── __init__.py
│       ├── main.py          # click/typer entrypoint
│       ├── chat.py          # interactive + one-shot
│       ├── sessions.py      # session management
│       └── config.py        # CLI config reader
├── data/
│   ├── conduit.db         # SQLite database (gitignored)
│   └── artifacts/         # stored image attachments and other blobs
└── tests/
```

### 3.2 Google ADK Agent Architecture

The agent hierarchy follows ADK's native pattern: a root LlmAgent acts as a router, with specialist agents registered as `sub_agents`. The router's LLM (a fast, cheap model like Gemini Flash) classifies the user's intent and delegates to the appropriate specialist via ADK's built-in transfer mechanism.

**Router agent.** Uses a lightweight model (Gemini 2.0 Flash recommended). Its instruction describes the available specialists and when to delegate to each. It handles simple greetings and chitchat directly without delegation.

**Specialist agents.** Each is an independent ADK Agent with its own model, instruction, tools, and optionally attached skills. They are defined in individual files under `src/agents/specialists/` and registered with the root agent at startup. Adding a new specialist requires creating a new file and adding it to the agent registry.

**Model assignment.** Configured via `config/models.yaml`. Each agent references a model key. Gemini models are used natively; Claude and GPT models are prefixed with `litellm/` for routing through the LiteLLM adapter (e.g., `litellm/anthropic/claude-sonnet-4-20250514`).

**Skills.** ADK's filesystem-based skills live under `skills/` and are loaded with `load_skill_from_dir(...)` into a `SkillToolset` attached to a specialist's tools list. Skill attachment is static and configured at startup; the router does not dynamically add or remove skills per request. Skills provide reusable instructions and reference material, while executable integrations remain regular ADK tools or MCP servers.

### 3.3 SQLite Session Service

ADK's default `InMemorySessionService` loses all state when the process restarts. Since this system runs on a single VM with no container orchestration, a custom `SQLiteSessionService` is required to persist sessions and conversation history across restarts.

#### 3.3.1 Schema

```sql
CREATE TABLE sessions (
    session_id   TEXT PRIMARY KEY,
    app_name     TEXT NOT NULL DEFAULT 'conduit',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    state_json   TEXT DEFAULT '{}'
);

CREATE TABLE messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(session_id),
    turn_id      TEXT NOT NULL,
    role         TEXT NOT NULL,  -- 'user' | 'assistant' | 'tool'
    agent_name   TEXT,           -- which agent authored this
    content      TEXT NOT NULL,
    content_type TEXT DEFAULT 'text',  -- 'text' | 'image' | 'tool_call' | 'tool_result'
    artifact_path TEXT,          -- optional path under data/artifacts/
    metadata     TEXT DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_messages_session ON messages(session_id, created_at);
CREATE INDEX idx_messages_turn ON messages(session_id, turn_id);
```

The `SQLiteSessionService` implements ADK's `BaseSessionService` interface, providing `get_session`, `create_session`, `update_session`, and `delete_session` methods. All writes use WAL mode for safe concurrent access from the FastAPI async event loop.

Binary attachments are not stored inline in SQLite. The server writes uploaded images and future large artifacts to `data/artifacts/` and stores a reference in `messages.artifact_path`.

### 3.4 Tool Permissions and Approvals

Every executable tool is assigned a permission mode in `config/tools.yaml`:

- `allow`: Execute immediately.
- `ask`: Pause execution, emit an approval request to the active client, and resume only after the client or CLI responds.
- `deny`: Do not expose the tool to the agent runtime.

```yaml
tools:
  web_search:
    mode: allow
  read_calendar:
    mode: allow
  create_calendar_event:
    mode: ask
  draft_email:
    mode: allow
  send_email:
    mode: ask
  delete_calendar_event:
    mode: ask
```

Approval prompts are surfaced only in the Flutter client or CLI. Enforcement still lives on the server, which blocks tool execution until an approval decision arrives. This policy layer is implemented in Conduit rather than delegated to ADK's built-in confirmation support, because ADK's confirmation feature is experimental and its documented session support does not include database-backed session services.

### 3.5 FastAPI Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/chat` | WebSocket | Primary chat interface. Streams agent responses token-by-token. Accepts text, images (base64), voice transcriptions, and approval responses. |
| `/sessions` | GET | List all sessions. Returns session IDs, timestamps, and message preview. |
| `/sessions/{session_id}` | DELETE | Delete a session and its messages. |
| `/health` | GET | Health check. Returns agent status, DB connectivity, model availability. |
| `/agents` | GET | List registered specialist agents. Returns names, models, descriptions. |

### 3.6 WebSocket Protocol

The WebSocket connection at `/chat` uses a simple JSON protocol with explicit IDs for idempotency and approvals. The client sends user messages and the server streams agent responses.

#### Client → Server messages:

```json
// Text message
{"type": "text", "message_id": "m1", "content": "What's the weather?"}

// Image attachment
{"type": "image", "message_id": "m2", "content": "<base64>", "mime": "image/jpeg"}

// Voice transcription (client-side STT)
{"type": "voice", "message_id": "m3", "content": "transcribed text"}

// New session request
{"type": "new_session", "client_request_id": "r1"}

// Approval response for an ask-gated tool call
{"type": "approval", "approval_id": "ap_123", "decision": "approve"}
```

#### Server → Client messages:

```json
// Acknowledgement for a client message
{"type": "ack", "message_id": "m1", "session_id": "abc123", "turn_id": "t1"}

// Streaming text token
{"type": "token", "turn_id": "t1", "message_id": "a1", "content": "The", "agent": "research"}

// Agent delegation event
{"type": "routing", "turn_id": "t1", "from": "router", "to": "research"}

// Tool invocation (for UI display)
{"type": "tool_call", "turn_id": "t1", "tool_call_id": "tc_1", "tool": "web_search", "args": {...}, "permission": "allow"}

// Client approval required before executing a tool
{"type": "approval_required", "turn_id": "t1", "approval_id": "ap_123", "tool_call_id": "tc_2", "tool": "send_email", "summary": "Send email to Alice with subject 'Trip update'"}

// End of response
{"type": "done", "turn_id": "t1", "session_id": "abc123", "message_id": "a1"}

// Error
{"type": "error", "turn_id": "t1", "message": "Rate limited by Anthropic"}
```

The `routing` event allows the Flutter client to display which specialist agent is handling the request (e.g., a small chip showing "Research agent" above the response). The explicit `message_id`, `turn_id`, `tool_call_id`, and `approval_id` fields make reconnects, de-duplication, and human approvals unambiguous in both the Flutter client and CLI.

---

## 4. Flutter Client

### 4.1 Scope

The Flutter app is a thin chat client. It has no local intelligence, no caching of conversations (beyond what's needed for smooth UI), and no offline mode. If the Tailscale connection drops, it shows a disconnection state and reconnects automatically.

### 4.2 Screens

| Screen | Description |
|--------|-------------|
| Session List | Shows recent conversations. Pull-to-refresh. Tap to resume a session, swipe to delete. FAB to start a new session. |
| Chat | The primary screen. Message bubbles with streaming text. Routing chip shows which agent is active. Input bar with text field, voice button, and image attach button. Approval prompts for `ask` tools are shown inline as modal confirmations. |
| Settings | Server URL (Tailscale IP:port). Stored in SharedPreferences. |

### 4.3 Input Affordances

**Text.** Standard text field with send button. Multiline support.

**Voice.** Mic button triggers on-device speech-to-text (Android's SpeechRecognizer API). The transcribed text is sent as a regular text message with `type: voice`. All STT happens on-device — no audio is sent to the server.

**Image.** Camera or gallery picker. Image is resized client-side (max 1024px longest edge), base64-encoded, and sent with `type: image`. The server passes it to the active agent's LLM if the model supports vision (Gemini, GPT-4o, and Claude Sonnet all do), then persists the artifact separately from the message transcript.

### 4.4 Streaming Display

The chat screen renders streaming tokens as they arrive over the WebSocket. The current approach uses a simple state accumulator: each `token` event appends to the current message's content, and the UI rebuilds the message bubble. Markdown rendering is deferred to a future iteration — v1 displays plain text with basic formatting (bold, italic, code blocks).

### 4.5 Tool Approval UX

When the server emits `approval_required`, the chat screen presents a blocking confirmation sheet with the tool name, a short summary, and `Approve` / `Deny` actions. The user's choice is sent back as an `approval` event. Only tools configured as `ask` trigger this flow; `allow` tools run immediately and `deny` tools are never exposed.

### 4.6 Connection Management

The app connects to the server via WebSocket at `ws://<tailscale-ip>:18423/chat`. Since Tailscale handles encryption, plain WebSocket (`ws://`) is used rather than secure WebSocket (`wss://`). The app implements exponential backoff reconnection: 1s, 2s, 4s, 8s, max 30s. A connection status indicator is shown in the app bar. Client-generated `message_id` values allow the app to safely retry a send after reconnect without creating duplicate turns.

---

## 5. CLI Interface

### 5.1 Purpose

A local command-line interface for interacting with Conduit directly from the terminal. This serves three purposes: quick interactions without opening the Flutter app, scripting and automation (e.g., piping output into other tools), and development/debugging when working on the server itself.

The CLI connects to the same FastAPI backend as the Flutter client — it is not a separate agent runtime.

### 5.2 Usage

```bash
# Interactive chat (new session)
conduit chat

# Resume an existing session
conduit chat --session abc123

# One-shot query (prints response and exits)
conduit ask "What's on my calendar today?"

# Pipe-friendly mode (no streaming animation, plain text output)
conduit ask --plain "Summarize my last 5 Linear issues" | pbcopy

# List sessions
conduit sessions

# Delete a session
conduit sessions delete abc123

# Check server health
conduit status

# List registered agents
conduit agents
```

### 5.3 Implementation

The CLI is a Python package using `click` or `typer`, installed alongside the backend or as a standalone tool. It communicates with the FastAPI server over the same Tailscale network using HTTP and WebSocket.

```
conduit/
├── src/
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── main.py          # click/typer app entrypoint
│   │   ├── chat.py          # interactive + one-shot chat
│   │   ├── sessions.py      # session management commands
│   │   └── config.py        # CLI config (~/.config/conduit/config.yaml)
```

### 5.4 Configuration

The CLI reads server connection details from `~/.config/conduit/config.yaml`:

```yaml
server:
  host: 100.x.y.z   # Tailscale IP of the GCP VM
  port: 18423
```

This can be overridden per-command with `--host` and `--port` flags.

### 5.5 Interactive Mode

`conduit chat` opens an interactive REPL with streaming output. Features:

- Streaming token display with a typing indicator
- Routing events shown inline (e.g., `→ routing to research agent`)
- Approval prompts for `ask` tools (e.g., `Approve send_email? [y/N]`)
- `/new` to start a fresh session within the REPL
- `/agents` to list available specialists
- `/quit` or Ctrl+C to exit
- Readline-style input with history (persisted in `~/.config/conduit/history`)

---

## 6. ADK Dev UI

### 6.1 Purpose

Google ADK ships with ADK Web, a browser-based development UI built with Angular. It provides visual inspection of agent events, traces, and artifacts during development and debugging. It runs alongside the FastAPI server and connects to the same ADK runtime.

### 6.2 Access

ADK Web runs on port 4200 and is bound to the Tailscale IP, same as the FastAPI server. It is a development and debugging tool, but in this deployment it intentionally runs on the same VM so traces and tool events are always available when needed.

```yaml
services:
  conduit:
    # ...existing config...
    ports:
      - "100.x.y.z:18423:18423"  # FastAPI
      - "100.x.y.z:4200:4200"  # ADK Web (dev only)
    environment:
      - CONDUIT_ADK_WEB=true    # intentionally enabled for debugging on the Tailscale network
```

### 6.3 Capabilities

| Feature | Description |
|---------|-------------|
| Event inspector | View all events flowing through the ADK runtime in real time — model calls, tool invocations, agent delegations, and responses. |
| Session viewer | Browse active and historical sessions. Inspect the full message history and state for any session. |
| Agent hierarchy | Visualize the root agent and its registered sub-agents, including their models, tools, and instructions. |
| Trace timeline | See the sequence and timing of each step in an agent's execution — useful for identifying slow tool calls or routing issues. |
| Artifact viewer | Inspect any artifacts produced during agent execution (structured outputs, intermediate data). |

### 6.4 When to Use

ADK Web is primarily useful during development and live debugging. Typical workflows:

- **Debugging routing.** When the router sends a request to the wrong specialist, the event inspector shows the LLM's reasoning and the delegation decision.
- **Profiling latency.** The trace timeline reveals which step is the bottleneck — model inference, tool execution, or MCP server round-trips.
- **Testing new agents.** After adding a new specialist, use ADK Web to send test queries and verify delegation, tool usage, and response quality before testing via the Flutter client.

For production-style monitoring, structured logging and metrics are still preferred over ADK Web (see section 10, Future Considerations).

---

## 7. Deployment

### 7.1 Docker Compose

The assistant runs as a single Docker container alongside existing services on the GCP VM. It is added to the existing Docker Compose configuration.

```yaml
services:
  conduit:
    build: ./conduit
    ports:
      - "100.x.y.z:18423:18423"  # FastAPI (Tailscale only)
      - "100.x.y.z:4200:4200"  # ADK Web dev UI (Tailscale only)
    volumes:
      - ./conduit/data:/app/data        # SQLite DB
      - ./conduit/config:/app/config    # agent/model config
      - ./conduit/skills:/app/skills    # ADK filesystem skills
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - GOOGLE_API_KEY=${GOOGLE_API_KEY}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - CONDUIT_ADK_WEB=true            # intentionally enabled for debugging
    restart: unless-stopped
```

Binding to the Tailscale IP (100.x.y.z) ensures the port is not accessible from the public internet or the VM's GCP internal network — only from devices on the Tailscale mesh.

### 7.2 Data Persistence

The SQLite database file (`conduit.db`) lives in a Docker volume mounted to the host filesystem. This survives container rebuilds and image updates. The database should be included in any existing backup strategy for the VM. Uploaded images and future large artifacts are stored in `data/artifacts/` on the same persistent volume.

No migrations framework is used in v1. Schema changes are applied manually via a SQL file in the repository. For a single user and a small schema, this is sufficient.

### 7.3 Environment Variables

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude API access |
| `GOOGLE_API_KEY` | Gemini API access |
| `OPENAI_API_KEY` | GPT API access |
| `CONDUIT_DB_PATH` | SQLite database path (default: `/app/data/conduit.db`) |
| `CONDUIT_HOST` | Bind address (default: `0.0.0.0`) |
| `CONDUIT_PORT` | Port (default: `18423`) |
| `CONDUIT_ADK_WEB` | Enable ADK Web dev UI on port 4200 (default: `true`) |

---

## 8. Adding a New Specialist Agent

The system is designed so that adding a specialist is a file addition plus config changes. No modifications to the router, the API layer, or the Flutter client are needed.

### Step 1: Create the agent file

```python
# src/agents/specialists/fitness.py
import pathlib

from google.adk import Agent
from google.adk.skills import load_skill_from_dir
from google.adk.tools import skill_toolset
from src.tools.strava import get_activities, get_stats

SKILLS_DIR = pathlib.Path(__file__).resolve().parents[3] / "skills"
fitness_skill = load_skill_from_dir(SKILLS_DIR / "fitness")
fitness_skillset = skill_toolset.SkillToolset(skills=[fitness_skill])

fitness_agent = Agent(
    name="fitness",
    model=MODELS["fitness"],
    instruction=(
        "You are a fitness and training specialist. "
        "Help with race preparation, training plans, "
        "and activity analysis using Strava data."
    ),
    tools=[fitness_skillset, get_activities, get_stats],
)
```

### Step 2: Add the optional ADK skill directory

```text
skills/
└── fitness/
    ├── SKILL.md
    └── references/
        └── training-metrics.md
```

The skill directory follows ADK's native filesystem skill format. `SKILL.md` is required. `references/` and `assets/` are optional. `scripts/` can exist for future compatibility, but executable behavior should not depend on it because ADK skills do not currently support script execution.

### Step 3: Register in config

```yaml
# config/agents.yaml
agents:
  - name: fitness
    module: src.agents.specialists.fitness
    class: fitness_agent
    description: Training, Strava, race planning
    skills:
      - fitness

# config/models.yaml
models:
  fitness: litellm/anthropic/claude-sonnet-4-20250514
```

On restart, the root agent's instruction is auto-generated from the agent registry, and the new specialist is added to `sub_agents`. The router LLM delegates based on the specialist's name, description, and instruction, while the specialist's attached skills provide additional task-specific guidance and references.

---

## 9. Known Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| No crash recovery for in-flight requests | If the process dies mid-response, the partial response is lost | ADK sessions are persisted per-turn, so only the current turn is lost. The client detects disconnection and reconnects. User can re-send the message. |
| SQLite write contention | Concurrent WebSocket handlers writing to the same DB | WAL mode + IMMEDIATE transactions. For a single user and a few concurrent clients, contention is negligible. |
| Pending approval is interrupted | An `ask`-gated tool call stalls if the client disconnects before responding | Persist pending approval state in the session metadata and let the Flutter client or CLI resume and answer after reconnect. |
| LiteLLM adapter limitations | Some Gemini-native features (grounding, audio streaming) may not work for Claude/GPT models | Accept this trade-off. Use Gemini for agents that need Gemini-specific features. Use Claude/GPT for general reasoning. |
| Tailscale dependency | If Tailscale goes down, the system is inaccessible | Tailscale is extremely reliable. Accept this as an operational dependency. |
| No app-level auth | Any device on the approved Tailscale network has full access to the assistant | Acceptable for a personal single-user system. Keep the tailnet restricted to personal devices only. |
| ADK skills are experimental | Filesystem skill APIs or behavior may change, and `scripts/` are not executable today | Keep skills limited to instructions and reference material. Put executable behavior in normal tools or MCP integrations. |
| Model API cost | Uncontrolled spending if agents loop | Set ADK's `max_turns` on each agent. Monitor API spend via provider dashboards. Add a simple per-day token budget in the session service. |

---

## 10. Future Considerations

- **iOS client.** Flutter supports iOS natively. Add Tailscale to the iPhone, point the app at the same server. No backend changes.
- **Markdown rendering.** Add `flutter_markdown` to the chat screen for rich response display.
- **Conversation search.** Full-text search over message content using SQLite's FTS5 extension.
- **Agent memory.** Persistent user preferences and facts stored in a dedicated SQLite table, injected into agent context per session.
- **Voice output.** Server-side TTS (Google Cloud TTS or on-device) for a voice-first experience.
- **A2A interoperability.** ADK supports the Agent2Agent protocol. Future agents built on other frameworks (LangGraph, CrewAI) can participate via A2A without rearchitecting.
- **Observability.** ADK Web remains available for debugging. Add structured logging and a simple dashboard for production-style monitoring.
