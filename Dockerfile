FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

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
