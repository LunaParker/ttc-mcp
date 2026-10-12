FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim@sha256:7cf77f594be8042dab6daa9fe326f90962252268b4f120a7f5dccce4d947e6c1

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_SYNC=1 PYTHONUNBUFFERED=1

# Dependencies first, so a code change does not re-resolve them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra cloud --no-install-project

# The project as a wheel, compiled here rather than at every cold start. Rebuilt every time: uv's cache
# keys a local wheel on pyproject.toml, not the code, so the cache mount would reinstall stale code.
COPY README.md LICENSE.md ./
COPY ttc_mcp ./ttc_mcp
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra cloud --no-editable --reinstall-package ttc-mcp

# Spec 6.9: http on $PORT, the store under /data (pulled from the bucket), CKAN left to the Job.
ENV TTC_MCP_TRANSPORT=http TTC_MCP_SKIP_REFRESH=1 TTC_MCP_CACHE=/data PORT=8080
EXPOSE 8080

# The installed script, not `uv run`: a fresh container must not rebuild the project at start.
CMD ["/app/.venv/bin/ttc-mcp"]
