#!/bin/sh
set -eu

export HOME="${HOME:-/root}"
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

mkdir -p "$CODEX_HOME"

seed_marker="$CODEX_HOME/.api_key_seeded"

if [ -n "${OPENAI_API_KEY:-}" ] && [ ! -f "$seed_marker" ]; then
  printf '%s\n' "$OPENAI_API_KEY" | /usr/local/bin/codex-real login --with-api-key >/dev/null
  touch "$seed_marker"
fi

exec /usr/local/bin/codex-real "$@"
