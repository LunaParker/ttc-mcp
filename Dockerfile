FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first, so a code change does not re-resolve them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra cloud --no-install-project

COPY ttc_mcp ./ttc_mcp
RUN uv sync --frozen --no-dev --extra cloud

# Spec 6.9: http on $PORT, the store under /data (pulled from the bucket), CKAN left to the Job.
ENV TTC_MCP_TRANSPORT=http TTC_MCP_SKIP_REFRESH=1 TTC_MCP_CACHE=/data PORT=8080
EXPOSE 8080

CMD ["uv", "run", "--no-dev", "ttc-mcp"]
