# ttc-mcp

An MCP server that lets Claude reason about the TTC network live: stops, stations, routes, arrivals, connections, detours, advisories and accessibility, fetched on demand from the TTC's and the City of Toronto's own endpoints.

- Specification: [docs/spec.md](docs/spec.md) (decisions a to j; on-demand fetch model; Cloud Run hosting)
- Architecture page: [docs/architecture.html](docs/architecture.html)
- Source survey: [docs/investigation-2026-09-13.md](docs/investigation-2026-09-13.md)
- Detour feed addendum: [docs/detours-addendum.md](docs/detours-addendum.md)
- Phase plans: [docs/superpowers/plans/](docs/superpowers/plans/)

## Status

Phase 1 (static core) is implemented: a SQLite store built from the City of Toronto's merged GTFS and the eight static tools (`find_stops`, `nearest_stops`, `stop`, `station`, `routes`, `route_stops`, `service_hours`, `scheduled_departures`).

Phase 2 (real-time core) is implemented: `arrivals`, `next_trains`, `vehicles` and `feed_status` fetch the TTC's per-stop BusTime XML, GTFS-RT vehicle feed and NTAS on demand through a TTL cache (single-flight, last-known-good) and fall back to the timetable with `basis: "schedule"` when a source has nothing. Phase 3 (advisory layer) is implemented: `alerts`, `service_status`, `detours` and `service_changes` read alerts.ttc.ca, the GTFS-RT alert feed, the TripModifications detour feed and ttc.ca's service changes on demand, and every stop, station, route and arrivals answer carries the disruptions that touch it (closures with nightly windows, detoured stops, reduced speed zones, elevator and escalator outages). Phase 4 (derived static) is implemented: `route_shape`, `accessibility` and `connections`, plus route service levels from ttc.ca; the static build now carries shapes, per-stop distances, pathways, levels, units, station membership and a 150 m transfer graph. Cloud Run hosting is a later phase.

## Quick start

```bash
uv sync                                   # Python 3.14 venv with dev tools
uv run ttc-mcp-build                      # fetch both CKAN packages, build ~/.cache/ttc-mcp/static-<feed>.sqlite (a few minutes)
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_static.py   # nine live checks against the store
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_realtime.py # eight live checks against the TTC's real-time endpoints
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_advisories.py # ten live checks of the advisory tools and propagation
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_derived.py    # ten live checks of route_shape, accessibility, service levels and connections
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/validate_connections.py # twenty origin-destination pairs to compare with the TTC trip planner
uv run pytest tests/integration -v           # boots the real server over HTTP against the live store and endpoints (needs network)
uv run ttc-mcp --transport stdio          # MCP server over stdio (checks CKAN on startup unless --no-refresh)
uv run pytest                             # unit suite on the committed mini feed
```

Claude Code, in a project's `.mcp.json`:

```json
{ "mcpServers": { "ttc": { "command": "uv", "args": ["run", "--project", "/absolute/path/to/ttc-mcp", "ttc-mcp", "--transport", "stdio"] } } }
```

Environment: `TTC_MCP_CACHE` (default `~/.cache/ttc-mcp`), `TTC_MCP_UA` (User-Agent; the TTC's hosts reject Python's default), `TTC_MCP_SKIP_REFRESH=1` to skip the startup CKAN check. `ttc-mcp-build --no-ttcapi` skips the ttc.ca service-level fetch (one request per route at build time); `--from-zip`/`--surface-zip` build from local GTFS zips.

## Layout

```
ttc_mcp/adapters/   ckan, http (async client), bustime_web (per-stop XML), bustime_rt (vehicles, trips), ntas, ttc_alerts, gtfsrt_alerts, detour_feed, ttcapi
ttc_mcp/sources.py  per-source TTL, stale-if-error window and staleness threshold
ttc_mcp/cache.py    TTL cache with single-flight, conditional GET and last-known-good
ttc_mcp/realtime.py the facade the tools call: client + cache, probe, per-source status
ttc_mcp/advisory.py the per-answer disruptions snapshot (alerts + GTFS-RT alerts + detour feed, built once per feed state)
ttc_mcp/static/     gtfs helpers, schema, streaming build, aliases, manifest, store, schedule, refresh
ttc_mcp/domain/     geo, network, crowding, freshness, arrivals, trains, vehicles, detours, service_changes, disruptions, shapes, accessibility, planner, live
ttc_mcp/tools/      MCP tool registration (static_tools, realtime_tools, advisory_tools, derived_tools)
ttc_mcp/server.py   FastMCP app + entry point;  ttc_mcp/build.py  the daily build job
scripts/            ttc_transit_probe.py (the original investigation probe), make_fixture.py, smoke_static.py, smoke_realtime.py, smoke_advisories.py, smoke_derived.py, validate_connections.py
tests/              unit suite on tests/fixtures/gtfs-mini.zip (routes 1, 5, 121, 300, 400, 504), captured BusTime XML and advisory fixtures; tests/integration boots the real server
.run/               PyCharm run configurations: Tests, Server (stdio), Build static
```

## Gotchas the code encodes (verified live 2026-10-05)

- `alerts.ttc.ca` stamps Toronto local time with a `Z` suffix; `childAlerts` start times match the GTFS-RT per-night entity epochs only when read as local. Year 1 (`0001-01-01`) means open-ended.
- `getallroutesandstopsalerts` no longer carries a `serviceChanges` key; the route changes are GTFS-RT `MODIFIED_SERVICE` entities, so `service_changes` reads those first and merges ttc.ca rows when they reappear.
- Closures publish their nightly windows as `childAlerts`, whose ids are the GTFS-RT `NO_SERVICE` entities listing the closed platform ids. `active_now` and `suspended` come from those; the `REDUCED_SERVICE` umbrella spans whole days and is never read as "closed now".
- `gtfsrt.ttc.ca/alerts/all` answers JSON unless `?format=binary`; `gtfsrt.ttc.ca/trips/detour` is JSON with no header timestamp (its signal is the HTTP `Date`).
- BusTime's per-stop XML writes direction text with double spaces ("504A  King"); headsign matching collapses whitespace. The vehicle feed reports an explicit `EMPTY` on most vehicles, hence the low-confidence "Not busy".
