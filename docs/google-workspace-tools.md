# Google Workspace Tools

## Overview

Conduit can optionally expose a small set of Google Workspace tools backed by the local [`gws`](https://github.com/googleworkspace/cli) binary. The CLI is an implementation detail. The agent only sees Conduit-owned tool names and normalized result shapes.

The current Workspace surface is:

- Gmail:
  - `gmail_search_messages`
  - `gmail_get_message`
  - `gmail_create_draft`
- Calendar:
  - `calendar_list_events`
  - `calendar_create_event`
  - `calendar_update_event`
- Drive:
  - `drive_search_files`
- Docs:
  - `docs_get_document`
  - `docs_create_document`
  - `docs_append_text`
  - `docs_replace_text`

Out of scope for this integration:

- sending email
- deleting calendar events
- recurring event authoring
- Meet link creation
- Drive sharing / permissions
- binary file uploads
- rich Docs formatting beyond append / replace text

## Runtime Design

Conduit shells out to a pinned native `gws` binary from the API container. The runtime wrapper:

- uses `asyncio.create_subprocess_exec`
- never uses `shell=True`
- forces `--format json`
- passes `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE` and optional `GOOGLE_WORKSPACE_CLI_ACCOUNT`
- enforces `CONDUIT_GWS_TIMEOUT_SECONDS`
- converts CLI failures into structured tool errors instead of uncaught exceptions

Tool results are normalized before they reach the model:

- Gmail reads flatten message headers and plain-text body
- Calendar reads normalize all-day vs timed events into one shape
- Drive search returns compact file metadata
- Docs reads flatten visible text and truncate it to `CONDUIT_GWS_MAX_CONTENT_CHARS`

Write tools default to `ask` in `config/tools.yaml`. Read/search tools default to `allow`.

## Authentication

The container does not rely on the host machine's interactive `gws auth login` state or keyring. Instead, Conduit reads an exported credential JSON from a bind-mounted `secrets/` directory.

### Why this approach

- it works in Docker without browser access
- it survives container restarts
- it avoids baking secrets into the image
- it keeps the runtime contract explicit and easy to debug

### Credential flow

1. Install and authenticate `gws` on the VM or any trusted machine:

```bash
gws auth setup
gws auth login -s drive,gmail,calendar,docs
```

2. Export the credential material:

```bash
mkdir -p secrets
gws auth export --unmasked > secrets/gws-credentials.json
chmod 600 secrets/gws-credentials.json
```

3. Start Conduit with:

- `CONDUIT_GWS_ENABLED=true`
- `./secrets:/app/secrets:ro` mounted in Compose
- `CONDUIT_GWS_CREDENTIALS_FILE=/app/secrets/gws-credentials.json`

### Important caveat

If the Google OAuth consent screen remains in **Testing** mode, Google can issue refresh tokens that expire quickly for user-scoped OAuth apps. For a stable always-on VM deployment, move the OAuth app to a production-ready state before relying on long-lived refresh behavior.

## Container Wiring

The image installs `gws` during `docker build` from a pinned GitHub release:

- current pinned version: `0.11.1`
- install path in container: `/usr/local/bin/gws`

Compose mounts the repo-local `secrets/` directory read-only:

```yaml
volumes:
  - ./secrets:/app/secrets:ro
```

Relevant env vars:

```env
CONDUIT_GWS_ENABLED=true
CONDUIT_GWS_BINARY_PATH=/usr/local/bin/gws
CONDUIT_GWS_CREDENTIALS_FILE=/app/secrets/gws-credentials.json
CONDUIT_GWS_ACCOUNT=
```

`secrets/` is ignored by both Git and Docker build context.

## Tool Behavior

### Gmail

- `gmail_search_messages` searches Gmail with a query string and returns compact summaries.
- `gmail_get_message` fetches one message and flattens the body to plain text.
- `gmail_create_draft` creates a Gmail draft only. It does not send.

### Calendar

- `calendar_list_events` defaults to the next 7 days when no explicit window is supplied.
- `calendar_create_event` and `calendar_update_event` only support timed RFC3339 events in v1.
- Event writes use `sendUpdates=none` to avoid sending guest notifications implicitly.

### Drive

- `drive_search_files` searches non-trashed My Drive items by name or full text.
- `file_type` can narrow searches to common types such as Google Docs or PDFs.

### Docs

- `docs_get_document` reads the document body and flattens visible text.
- `docs_create_document` can optionally seed initial text.
- `docs_append_text` appends plain text at the end of the document body.
- `docs_replace_text` uses Docs `replaceAllText`.

## Validation

When Workspace tools are enabled, Conduit fails startup if:

- the configured `gws` binary cannot be resolved
- the configured credential file does not exist

This is intentional. A misconfigured deployment should fail immediately rather than expose half-working tools at runtime.
