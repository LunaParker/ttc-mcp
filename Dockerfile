FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_SYNC=1

# Dependencies first, so a code change does not re-resolve them.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra cloud --no-install-project

COPY ttc_mcp ./ttc_mcp
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra cloud

# Spec 6.9: http on $PORT, the store under /data (pulled from the bucket), CKAN left to the Job.
ENV TTC_MCP_TRANSPORT=http TTC_MCP_SKIP_REFRESH=1 TTC_MCP_CACHE=/data PORT=8080
EXPOSE 8080

# The installed script, not `uv run`: a fresh container must not rebuild the project at start.
CMD ["/app/.venv/bin/ttc-mcp"]
