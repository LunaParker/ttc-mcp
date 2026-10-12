# ttc-mcp

An MCP server that lets Claude reason about the TTC network live: stops, stations, routes, arrivals, connections, detours, advisories and accessibility, fetched on demand from the TTC's and the City of Toronto's own endpoints.

- Specification: [docs/spec.md](docs/spec.md) (decisions a to j; on-demand fetch model; Cloud Run hosting)
- Deploy runbook (phase 5b, the first Cloud Run deploy): [docs/deploy.md](docs/deploy.md)
- Architecture page: [docs/architecture.html](docs/architecture.html)
- Source survey: [docs/investigation-2026-09-13.md](docs/investigation-2026-09-13.md)
- Detour feed addendum: [docs/detours-addendum.md](docs/detours-addendum.md)
- Phase plans: [docs/superpowers/plans/](docs/superpowers/plans/)

## Status

Phase 1 (static core) is implemented: a SQLite store built from the City of Toronto's merged GTFS and the eight static tools (`find_stops`, `nearest_stops`, `stop`, `station`, `routes`, `route_stops`, `service_hours`, `scheduled_departures`).

Phase 2 (real-time core) is implemented: `arrivals`, `next_trains`, `vehicles` and `feed_status` fetch the TTC's per-stop BusTime XML, GTFS-RT vehicle feed and NTAS on demand through a TTL cache (single-flight, last-known-good) and fall back to the timetable with `basis: "schedule"` when a source has nothing. Phase 3 (advisory layer) is implemented: `alerts`, `service_status`, `detours` and `service_changes` read alerts.ttc.ca, the GTFS-RT alert feed, the TripModifications detour feed and ttc.ca's service changes on demand, and every stop, station, route and arrivals answer carries the disruptions that touch it (closures with nightly windows, detoured stops, reduced speed zones, elevator and escalator outages). Phase 4 (derived static) is implemented: `route_shape`, `accessibility` and `connections`, plus route service levels from ttc.ca; the static build now carries shapes, per-stop distances, pathways, levels, units, station membership and a 150 m transfer graph. Phases 1 to 4 passed a spec-correctness review on 2026-10-11 and its fixes landed: closure end stations are served on their open side, suspended platforms report no minutes, shuttle options rank honestly, failing sources back off, trip ids resolve against both loaded feeds, and the stdio server rechecks CKAN every 24 h (spec v0.7). A same-day follow-up moved bus bays out of a station's platforms, so the accessibility summary reads rail platforms only. Phase 5a (the container, 2026-10-11) is implemented: the Google OAuth gate and email allowlist on the http transport (`GOOGLE_OAUTH_CLIENT_ID` turns it on; OAuth state in Firestore or in memory), the store kept in a GCS bucket (`ttc-mcp-build --bucket` pulls it, checks CKAN and pushes a rebuild) and pulled by the http server at boot and every six hours, one read-only SQLite connection per thread with the `connections` planner in a worker thread, the Dockerfile with the image smoke CI runs, and a unit suite that never reaches the network. Phase 5b, the GCP provisioning and the first deploy, follows [docs/deploy.md](docs/deploy.md); nothing is deployed as of 2026-10-11.

## Quick start

```bash
uv sync --extra cloud                     # Python 3.14 venv with dev tools and the cloud extra (Firestore state, GCS); the unit suite's GCS wiring test imports google.api_core
uv run ttc-mcp-build                      # fetch both CKAN packages, build ~/.cache/ttc-mcp/static-<feed>.sqlite (a few minutes)
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_static.py   # nine live checks against the store
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_realtime.py # eight live checks against the TTC's real-time endpoints
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_advisories.py # ten live checks of the advisory tools and propagation
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_derived.py    # eleven live checks of route_shape, accessibility, service levels and connections
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/validate_connections.py # twenty origin-destination pairs to compare with the TTC trip planner; an option with a shuttle leg prints as SHUTTLE with its known_min
uv run pytest tests/integration -v           # boots the real server over HTTP against the live store and endpoints (needs network)
uv run ttc-mcp --transport stdio          # MCP server over stdio (checks CKAN on startup and every 24 h unless --no-refresh)
uv run pytest                             # unit suite on the committed mini feed, offline (see Tests)
```

Claude Code, in a project's `.mcp.json`:

```json
{ "mcpServers": { "ttc": { "command": "uv", "args": ["run", "--project", "/absolute/path/to/ttc-mcp", "ttc-mcp", "--transport", "stdio"] } } }
```

Environment: `TTC_MCP_CACHE` (default `~/.cache/ttc-mcp`), `TTC_MCP_UA` (User-Agent; the TTC's hosts reject Python's default) and `TTC_MCP_SKIP_REFRESH=1` to skip the startup CKAN check are the local ones; the full table is under Cloud Run below. `ttc-mcp-build --no-ttcapi` skips the ttc.ca service-level fetch (one request per route at build time); `--from-zip`/`--surface-zip` build from local GTFS zips; `--bucket` (or `TTC_MCP_STATIC_BUCKET`) pulls the bucket's store before the CKAN check and pushes the build into it after (`--from-zip` never pulls). `--from-zip --bucket` replaces the bucket's manifest with the local one and `--bucket` removes local feed files the bucket's manifest does not list, so point `--bucket` only at a bucket you mean to overwrite.

Upgrading from phase 3 or earlier: a store built before phase 4 has an older schema and is skipped at load (stderr names it). Run `uv run ttc-mcp-build` once to rebuild it, or `ttc-mcp-build --from-zip` for a local feed.

## Docker

The image (`Dockerfile`) is the Cloud Run image, and it runs locally the same way: `ghcr.io/astral-sh/uv:python3.14-bookworm-slim` pinned by digest, `uv sync --frozen --no-dev --extra cloud` with the project installed as a wheel, `ENV TTC_MCP_TRANSPORT=http TTC_MCP_SKIP_REFRESH=1 TTC_MCP_CACHE=/data PORT=8080`, and `/app/.venv/bin/ttc-mcp` as the command: streamable HTTP on `:8080/mcp`, stateless JSON, no CKAN check, no gate unless `GOOGLE_OAUTH_CLIENT_ID` is set. The store lives under `/data`, so mount a volume there and build into it first:

```bash
docker build -t ttc-mcp .
docker volume create ttc-mcp-data
docker run --rm -v ttc-mcp-data:/data ttc-mcp /app/.venv/bin/ttc-mcp-build                  # CKAN download and build into the volume (a few minutes)
docker run --rm -p 8080:8080 -v ttc-mcp-data:/data ttc-mcp                                   # serve it: POST http://127.0.0.1:8080/mcp
docker run --rm -p 8080:8080 -v ttc-mcp-data:/data -e TTC_MCP_NOW=2026-10-06T08:00 ttc-mcp   # the same server with its clock pinned to a day the store covers
scripts/smoke_image.sh                                                                        # what CI runs: build, fixture store in a container, 19 tools, a routes answer from /data
```

`TTC_MCP_NOW` (ISO 8601; a naive value is Toronto time) pins `runtime.now()`, which is read on every call: the smoke sets it so the fixture feed (2026-09-06 to 2026-10-31) answers on any real date, and it reproduces a past day against a store that still covers it. Run the installed scripts (`/app/.venv/bin/ttc-mcp`, `/app/.venv/bin/ttc-mcp-build`), never `python -m ttc_mcp...` from `/app`: the uncompiled source copy there shadows the installed wheel.

## Cloud Run

The hosted shape is spec 6.9: Cloud Run service `ttc-mcp` in `us-east1` (project `$PROJECT_ID`; min 0, max 1, 1 GiB, stateless JSON at `https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app/mcp`) with the Google OAuth gate and email allowlist in-process and its OAuth state in Firestore; the static store is built by the Cloud Run Job `ttc-mcp-static` (Cloud Scheduler `ttc-mcp-static-daily`, 04:30 Toronto) into `gs://ttc-mcp-static-$PROJECT_NUMBER` and pulled by the service at boot and every six hours. [docs/deploy.md](docs/deploy.md) is the paste-and-run runbook for the first deploy (phase 5b). Nothing is deployed as of 2026-10-11. Once it is, Claude Code's `.mcp.json` entry is `{ "mcpServers": { "ttc": { "type": "http", "url": "https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app/mcp" } } }`, and the claude.ai connector takes the same URL with the advanced OAuth fields left empty.

Environment variables the code reads (`grep -rn os.environ ttc_mcp/`):

| Variable | Read by | Default | Effect |
|---|---|---|---|
| `TTC_MCP_TRANSPORT` | `server.main` (the `--transport` default) | `stdio`; the image sets `http` | `http` serves streamable HTTP on `--host` and `PORT`; `stdio` never gates and never pulls |
| `PORT` | `server.main` (the `--port` default) | `8080` (the image sets it too; Cloud Run sets it) | http only |
| `TTC_MCP_CACHE` | `config.settings` | `~/.cache/ttc-mcp`; the image sets `/data` | where the store, `manifest.json` and the build's `downloads/` live |
| `TTC_MCP_UA` | `config.settings` | `ttc-mcp/<version> (+https://github.com/LunaParker/ttc-mcp)` | the User-Agent on every request; four TTC hosts 403 Python's default |
| `TTC_MCP_SKIP_REFRESH` | `config.settings` | off; `1`, `true` or `yes` turn it on; the image sets `1` | skips the startup CKAN check on both transports and the stdio 24 h recheck |
| `TTC_MCP_STATIC_BUCKET` | `config.settings`; used by `server.main` (http only) and as the default of `ttc-mcp-build --bucket` | unset | http: pull the store from this bucket at boot and on the timer and never check CKAN; build: pull the bucket's store before the CKAN check, push the build after (`--from-zip` never pulls) |
| `TTC_MCP_PULL_S` | `config.settings` | `21600` (6 h); under 60 refused at startup | the http pull interval |
| `TTC_MCP_NOW` | `runtime.now`, on every call | unset: the Toronto wall clock | ISO 8601, naive means Toronto; pins the clock for the image smoke or to reproduce a day |
| `GOOGLE_OAUTH_CLIENT_ID` | `config.gate_settings`, called from `server.main` for http only | unset: no gate | set: the gate is on and the next four variables are required; a half-configured gate refuses to start |
| `GOOGLE_OAUTH_CLIENT_SECRET` | `config.gate_settings` | required with the gate | passed to `GoogleProvider` (Secret Manager on Cloud Run) |
| `BASE_URL` | `config.gate_settings` | required with the gate | the service's origin without a path (a trailing `/` is stripped); the OAuth redirect URI is `BASE_URL/auth/callback` |
| `JWT_SIGNING_KEY` | `config.gate_settings` | required with the gate | passed to `GoogleProvider` as `jwt_signing_key` (Secret Manager on Cloud Run) |
| `ALLOWED_EMAILS` | `config.gate_settings` (`allowed_emails`) | required with the gate | comma-separated, case-insensitive; an empty list refuses to start |
| `GOOGLE_CLOUD_PROJECT` | `config.gate_settings` | unset: OAuth state in memory | set: OAuth state in that project's default Firestore database, sanitized, collections prefixed `ttc`, Fernet-encrypted (`gate.client_storage` builds it from the `Gate` fields) |
| `STORAGE_ENCRYPTION_KEY` | `config.gate_settings` | required with `GOOGLE_CLOUD_PROJECT` | the Fernet key for that state, used by `gate.client_storage` (Secret Manager on Cloud Run) |

## Tests

`uv run pytest` is the unit suite on the committed mini feed, and it never reaches the network: `tests/unit/conftest.py` lets httpx talk to loopback only, and a test that reaches any other host fails at teardown naming the host (the fetch layer would otherwise swallow the refusal into `staleness: unavailable`). `uv run pytest tests/integration` boots the real server over HTTP and reaches the TTC's hosts, so it stays out of CI. CI runs four jobs: unit (`uv sync --frozen --extra cloud`, `pytest tests/unit`), lint (`ruff check`), build (sdist and wheel, both entry points called from a clean venv) and image (`scripts/smoke_image.sh`).

## Layout

```
ttc_mcp/adapters/   ckan, http (async client), bustime_web (per-stop XML), bustime_rt (vehicles, trips), ntas, ttc_alerts, gtfsrt_alerts, detour_feed, ttcapi
ttc_mcp/sources.py  per-source TTL, stale-if-error window and staleness threshold
ttc_mcp/cache.py    TTL cache with single-flight, conditional GET and last-known-good
ttc_mcp/realtime.py the facade the tools call: client + cache, probe, per-source status
ttc_mcp/advisory.py the per-answer disruptions snapshot (alerts + GTFS-RT alerts + detour feed, built once per feed state)
ttc_mcp/static/     gtfs helpers, schema, streaming build, aliases, manifest, store (one read-only connection per thread), schedule, refresh, bucket (push and pull of the store)
ttc_mcp/domain/     geo, network, crowding, freshness, arrivals, trains, vehicles, detours, service_changes, disruptions, shapes, accessibility, planner, live
ttc_mcp/tools/      MCP tool registration (static_tools, realtime_tools, advisory_tools, derived_tools)
ttc_mcp/server.py   FastMCP app + entry point (stdio or http; the gate and the bucket pull are wired here);  ttc_mcp/build.py  the daily build job (--bucket pulls, checks CKAN, pushes)
ttc_mcp/config.py   settings from TTC_MCP_* and the gate settings;  ttc_mcp/gate.py  the Google OAuth gate and email allowlist
scripts/            ttc_transit_probe.py (the original investigation probe), make_fixture.py, smoke_static.py, smoke_realtime.py, smoke_advisories.py, smoke_derived.py, validate_connections.py, smoke_image.sh (the container smoke CI runs)
tests/              unit suite on tests/fixtures/gtfs-mini.zip (routes 1, 5, 121, 300, 400, 504), captured BusTime XML and advisory fixtures; tests/integration boots the real server; tests/unit/conftest.py keeps the unit suite off the network
.run/               PyCharm run configurations: Tests, Integration (live), Server (stdio), Build static
Dockerfile          the Cloud Run image (one image: ttc-mcp serves, ttc-mcp-build is the Job);  docs/deploy.md  the phase 5b runbook
```

## Gotchas the code encodes (verified live 2026-10-05 and 2026-10-11)

- `alerts.ttc.ca` stamps Toronto local time with a `Z` suffix; `childAlerts` start times match the GTFS-RT per-night entity epochs only when read as local. Year 1 (`0001-01-01`) means open-ended.
- `getallroutesandstopsalerts` no longer carries a `serviceChanges` key; the route changes are GTFS-RT `MODIFIED_SERVICE` entities, so `service_changes` reads those first and merges ttc.ca rows when they reappear.
- Closures publish their nightly windows as `childAlerts`, whose ids are the GTFS-RT `NO_SERVICE` entities listing the closed platform ids. `active_now` and `suspended` come from those; the `REDUCED_SERVICE` umbrella spans whole days and is never read as "closed now".
- `gtfsrt.ttc.ca/alerts/all` answers JSON unless `?format=binary`; `gtfsrt.ttc.ca/trips/detour` is JSON with no header timestamp (its signal is the HTTP `Date`).
- BusTime's per-stop XML writes direction text with double spaces ("504A  King"); headsign matching collapses whitespace. The vehicle feed reports an explicit `EMPTY` on most vehicles, hence the low-confidence "Not busy".
- Rail `shape_dist_traveled` is in kilometres with the first stop of each trip left blank (surface is in metres); the build picks the unit per shape against its measured length and starts every pattern at 0.
- GTFS-RT `NO_SERVICE` entities list every platform of the affected stations, including other lines' platforms at interchanges (a Line 2 closure lists Line 1's St George and Spadina platforms), so a listing closes a platform only when the entity names no routes or one of the platform's own lines.
- An active closure's `shuttleType` reads "Running" and a planned one "Will Operate"; any other value means no shuttle, and the planner then routes around the closed span or drops the trip.
- NTAS keeps publishing predictions at closed platforms during a closure (Old Mill eastbound "1", Jane westbound "1, 4, 8" inside the Kipling to Jane closure), so a suspended platform is decided by the closure, never by NTAS: `next_min: []`, what NTAS said in `ntas_min`, and a note.
- The TTC's closure station list is the shuttle span inclusive of its ends, and GTFS-RT lists every platform of every listed station; the end stations' open-side platforms are served (trains turn back there), so only the interior is closed and an end station is `reduced` with `role: shuttle_terminus`. A terminal end, or a one-station closure, is closed on both platforms.
- `connections` reports an option with a shuttle leg as `typical_min: null` with `known_min` for its other legs; the shuttle counts as a transfer and the option ranks after complete options with the same transfers.
- The merged feed lists bus bays and streetcar platforms as children of a station with `wheelchair` 0. A platform is a station child served by a subway or LRT route; the rest are the station's `surface_stops`, and the accessibility summary and step-free verdict read the platforms only (counting the bays had read 31 stations partially accessible).
- Every build ends with three `[check]` lines (spec 8 items 3 and 9): surface-stop suffixes that attach to no station, alert station ids and names that resolve to nothing, and how many elevator and escalator codes in today's alerts match a coded pathway node (10 of 18 on 2026-10-11). `--no-checks` skips them; the daily Job's log is where they will be read.
