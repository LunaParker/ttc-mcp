# ttc-mcp

An MCP server that lets Claude reason about the TTC network live: stops, stations, routes, arrivals, connections, detours, advisories and accessibility, fetched on demand from the TTC's and the City of Toronto's own endpoints.

- Specification: [docs/spec.md](docs/spec.md) (decisions a to j; on-demand fetch model; Cloud Run hosting)
- Architecture page: [docs/architecture.html](docs/architecture.html)
- Source survey: [docs/investigation-2026-09-13.md](docs/investigation-2026-09-13.md)
- Detour feed addendum: [docs/detours-addendum.md](docs/detours-addendum.md)
- Phase plans: [docs/superpowers/plans/](docs/superpowers/plans/)

## Status

Phase 1 (static core) is implemented: a SQLite store built from the City of Toronto's merged GTFS and the eight static tools (`find_stops`, `nearest_stops`, `stop`, `station`, `routes`, `route_stops`, `service_hours`, `scheduled_departures`). Real-time, advisories, connections and Cloud Run hosting are later phases.

## Quick start

```bash
uv sync                                   # Python 3.14 venv with dev tools
uv run ttc-mcp-build                      # fetch both CKAN packages, build ~/.cache/ttc-mcp/static-<feed>.sqlite (a few minutes)
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_static.py   # nine live checks against the store
uv run ttc-mcp --transport stdio          # MCP server over stdio (checks CKAN on startup unless --no-refresh)
uv run pytest                             # unit suite on the committed mini feed
```

Claude Code, in a project's `.mcp.json`:

```json
{ "mcpServers": { "ttc": { "command": "uv", "args": ["run", "--project", "/absolute/path/to/ttc-mcp", "ttc-mcp", "--transport", "stdio"] } } }
```

Environment: `TTC_MCP_CACHE` (default `~/.cache/ttc-mcp`), `TTC_MCP_UA` (User-Agent; the TTC's hosts reject Python's default), `TTC_MCP_SKIP_REFRESH=1` to skip the startup CKAN check.

## Layout

```
ttc_mcp/adapters/   ckan (more in phase 2)
ttc_mcp/static/     gtfs helpers, schema, streaming build, aliases, manifest, store, schedule, refresh
ttc_mcp/domain/     geo, network
ttc_mcp/tools/      MCP tool registration
ttc_mcp/server.py   FastMCP app + entry point;  ttc_mcp/build.py  the daily build job
scripts/            ttc_transit_probe.py (the original investigation probe), make_fixture.py, smoke_static.py
tests/              unit suite on tests/fixtures/gtfs-mini.zip (routes 1, 5, 121, 300, 400, 504)
.run/               PyCharm run configurations: Tests, Server (stdio), Build static
```
