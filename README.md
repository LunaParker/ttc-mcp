# ttc-mcp

An MCP server that lets Claude reason about the TTC network live: stops, stations, routes, arrivals, next trains, connections, detours, advisories and accessibility, fetched on demand from the TTC's and the City of Toronto's own endpoints.

- Specification: [docs/spec.md](docs/spec.md) (decisions a to i; on-demand fetch model)
- Source survey: [docs/investigation-2026-09-13.md](docs/investigation-2026-09-13.md)
- Detour feed addendum: [docs/detours-addendum.md](docs/detours-addendum.md)
- Reference probe (run with `uv run scripts/ttc_transit_probe.py --help`): [scripts/ttc_transit_probe.py](scripts/ttc_transit_probe.py)
