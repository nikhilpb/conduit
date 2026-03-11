FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

ARG GWS_VERSION=0.11.1

WORKDIR /app

RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) gws_arch="x86_64" ;; \
      arm64) gws_arch="aarch64" ;; \
      *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/googleworkspace/cli/releases/download/v${GWS_VERSION}/gws-${gws_arch}-unknown-linux-gnu.tar.gz"; \
    python -c "import pathlib, sys, urllib.request; \
pathlib.Path(sys.argv[2]).write_bytes(urllib.request.urlopen(sys.argv[1]).read())" \
      "$url" /tmp/gws.tar.gz; \
    mkdir -p /tmp/gws-extract; \
    tar -xzf /tmp/gws.tar.gz -C /tmp/gws-extract --strip-components=1; \
    install -m 0755 /tmp/gws-extract/gws /usr/local/bin/gws; \
    rm -rf /tmp/gws.tar.gz /tmp/gws-extract

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY adk_agents ./adk_agents
COPY config ./config
COPY DESIGN.md ./DESIGN.md

RUN uv sync --locked --no-dev
RUN mkdir -p /app/data

EXPOSE 18423

CMD ["uv", "run", "conduit-api"]
