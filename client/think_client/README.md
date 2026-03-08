# Think Client

Thin Flutter client for the Conduit backend.

## Run The Backend

From the repo root:

```bash
uv run conduit-api
```

The backend now binds to `0.0.0.0:18423` by default.

Health check:

```bash
curl http://127.0.0.1:18423/health
```

## Run The Client

If you want the app pre-wired to a backend URL on first launch, pass it with
`--dart-define`.

Examples:

```bash
# Android emulator talking to the host machine
flutter run -d android --dart-define=CONDUIT_SERVER_URL=http://10.0.2.2:18423

# Physical Android device over Tailscale
flutter run -d android --dart-define=CONDUIT_SERVER_URL=http://100.x.y.z:18423

# Desktop local development
flutter run -d macos --dart-define=CONDUIT_SERVER_URL=http://127.0.0.1:18423
```

If you do not pass `CONDUIT_SERVER_URL`, open Settings in the app and enter the
server URL manually.

Important:

- `10.0.2.2` only works on the Android emulator.
- On `macOS` or `Chrome`, use `http://127.0.0.1:18423`.
- `--dart-define` is read at app startup, so after changing it you need a fresh
  `flutter run` or a hot restart, not just hot reload.

## Current Limitations

- Session list and settings still use HTTP.
- Chat uses the WebSocket `/chat` protocol with streamed token updates.
- Reconnect retries are implemented for chat, with in-flight turn replay keyed by `message_id`.
- Voice and image buttons are present but not wired yet.
