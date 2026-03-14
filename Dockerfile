FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Keep the default pinned, but allow rebuilds against a newer Codex release.
ARG CODEX_VERSION=0.114.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git ripgrep tar \
    && rm -rf /var/lib/apt/lists/*

RUN arch="$(dpkg --print-architecture)" \
    && case "$arch" in \
        amd64) codex_arch="x86_64" ;; \
        arm64) codex_arch="aarch64" ;; \
        *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac \
    && curl -fsSL "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-${codex_arch}-unknown-linux-musl.tar.gz" -o /tmp/codex.tar.gz \
    && tar -xzf /tmp/codex.tar.gz -C /tmp \
    && install -m 0755 "/tmp/codex-${codex_arch}-unknown-linux-musl" /usr/local/bin/codex-real \
    && rm -f /tmp/codex.tar.gz "/tmp/codex-${codex_arch}-unknown-linux-musl"

RUN git config --system --add safe.directory /workspace

COPY docker/codex-wrapper.sh /usr/local/bin/codex
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY adk_agents ./adk_agents
COPY config ./config
COPY DESIGN.md ./DESIGN.md

RUN uv sync --locked --no-dev
RUN chmod 0755 /usr/local/bin/codex \
    && mkdir -p /app/data /workspace

EXPOSE 18423

CMD ["uv", "run", "conduit-api"]
