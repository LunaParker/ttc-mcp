# ttc-mcp

MCP server for the TTC network. `docs/spec.md` is normative: when code and spec disagree, fix one and say which. The README's "Gotchas the code encodes" lists the live data facts the code depends on; spec section 11 is the dated verification log.

## What this is

An MCP server that lets Claude (or any MCP client) reason about the TTC network as it is right now: stops, stations, routes, arrivals, connections, detours, advisories and accessibility, fetched on demand from the TTC's and the City of Toronto's own endpoints, never polled. Single-user: the maintainer hosts it on Cloud Run behind a Google OAuth gate, or a developer runs it locally over stdio. Nineteen tools in four layers, one module each under `tools/`: static (`find_stops`, `nearest_stops`, `stop`, `station`, `routes`, `route_stops`, `service_hours`, `scheduled_departures`), real-time (`arrivals`, `next_trains`, `vehicles`, `feed_status`), advisory (`alerts`, `service_status`, `detours`, `service_changes`) and derived (`route_shape`, `accessibility`, `connections`). The spec's map: 1 purpose and principles; 2 architecture (2.1 static layer and board periods, 2.2 on-demand fetch layer, 2.3 advisory sources); 3 identifiers; 4 tools (4.1 core, 4.2 added, 4.3 TTLs, 4.4 normative output schemas); 5 store counts; 6 evidence per decision (6.6 fetch model, 6.7 trip questions and disruptions, 6.8 Lines 5 and 6, 6.9 transport and hosting); 7 the decision record a to j; 8 validation items; 9 implementation notes; 10 effort; 11 the verification log.

## Architecture

- Static path: `adapters/ckan.py` resolves the two CKAN packages and `HEAD`s their ETags; `ttc-mcp-build` (`build.main`, then `static/refresh.ensure_static`) rebuilds on an ETag change through `static/build.build_sqlite` into `static-<feed_version>.sqlite`, listed in `manifest.json` (`static/manifest.py`). `runtime.get_store()` opens a `StaticStore` from the manifest and reopens it only when the listed feeds change; `runtime.feed_for()` picks today's `FeedDb`; a missing store is a `StoreUnavailable` error payload, never a crash.
- Real-time path: one adapter per source under `adapters/` (the README's Layout names them), typed records each; `sources.py` fixes each source's TTL, stale window, staleness threshold and timeout; `cache.Cache.get` does TTL, single-flight (one in-flight future per key), conditional GET, last-known-good within `max_stale` (else `SourceUnavailable`) and the backoff hold with 403/429 counts; `realtime.Realtime` is the facade (one method per source, `probe`, `status`) behind the `runtime.get_realtime()` singleton. `domain/` is pure functions over a connection and the snapshot; `tools/` map MCP calls onto them and never touch HTTP.
- Advisory snapshot: `advisory.snapshot(feed)` gathers alerts, GTFS-RT alerts and the detour feed (`realtime.attempt` turns a failed source into an error string), resolves detour groups and builds `Disruptions` once, memoised in `advisory._memo` on the realtime and store identities, the loaded feed versions, the minute and the three fetch times.
- How a tool answers: static tools wrap `static_tools._guard(fn)` (today's feed plus `runtime.meta`) or `_with_advisories(fn)` (the snapshot too, as `meta.advisories`); real-time tools wrap `realtime_tools._aguard` (fn returns payload, hits, source and optionally the snapshot; `runtime.meta_rt` takes the worst cache state and verdict across the hits); advisory tools wrap `advisory_tools._aguard` (snapshot first, meta over its hits plus any extra fetch). `meta` is spec 4.4 (its field list opens section 4).
- Transports: `server.main` builds `create_app(recheck, gate, puller, pull_s)`. stdio checks CKAN at startup (`ensure_static`; a rebuild blocks start, not tool calls) unless `--no-refresh` or `TTC_MCP_SKIP_REFRESH`, then every 24 h (`RECHECK_S`) in a worker thread through the `server.periodic` lifespan; it never gates. http runs `app.run(transport="http", stateless_http=True, json_response=True)` on `--host 0.0.0.0` and `PORT` at `/mcp`; with `TTC_MCP_STATIC_BUCKET` it pulls the store before it listens (`static/bucket.pull`; a failure stops the start) and again every `TTC_MCP_PULL_S` seconds, and never checks CKAN; without a bucket it keeps the startup check unless skipped and never rechecks.
- Gate (spec 6.9): `gate.build_auth` returns FastMCP's `GoogleProvider` (dynamic client registration, consent remembered); `gate.EmailAllowlist` checks every request for a verified listed email and answers anything else with a JSON-RPC `AuthorizationError`; `gate.client_storage` keeps OAuth state in Firestore (sanitized, prefix `ttc`, Fernet) when `GOOGLE_CLOUD_PROJECT` is set, else in memory. `config.gate_settings` refuses a half-configured gate and is read for http only.
- The static store is one SQLite file per feed under `manifest.json`; two feeds are loaded during a board-period overlap and `StaticStore.lookup_trip` tries both.

## Entities and identifiers

- GTFS `location_type` on `stops`: 0 a boarding stop or platform, 1 a station, 2 an entrance, 3 a pathway node (`domain/network.KINDS`). A rail platform is a station child served by a subway or LRT route (`network.is_rail_platform`, `trains.RAIL_PLATFORMS`); bus bays and streetcar platforms under a station are its `surface_stops`, and `network.station_detail` reads the accessibility summary from the platforms only (decision b).
- The public stop number is the merged GTFS `stop_id` (equal to `stop_code` except 42 rows), canonical in every tool input and output; `arrivals.stop_row` resolves `stop_id OR stop_code`. BusTime's protobuf feeds use the Surface GTFS internal id: the `crosswalk` table (`static/build._load_crosswalk`) maps it to `stop_code`, and `domain/vehicles.py` reads it back. The per-stop XML takes public numbers.
- Stations: 34 carry a synthetic `stop_id` (43265 to 43298) with the legacy id in `stop_code`; alerts.ttc.ca names the legacy id, so `disruptions.station_row` joins `stop_id=? OR stop_code=?` (spec 3). `static/build._attach_orphan_platforms` gives a parentless platform named "<X> Station - ... Platform" to station X when exactly one station has that name; 16081 to Kennedy (Line 5) is today's instance.
- Aliases: `static/aliases.build_aliases` fills `station_aliases` at build time from station names, platform prefixes, "- X Station" surface-stop suffixes within 300 m (`SUFFIX_RADIUS_M`) and the `MANUAL` list (Dundas to TMU and friends); GO suffixes are `external` and never resolve; `resolve_station` ranks manual, station, platform, suffix, then a `LIKE` fallback. A surface stop named "... - X Station" inherits X's closures (`Disruptions.for_stop`).
- Modes: `static/gtfs.mode_of` and `series_of`; Lines 5 and 6 are `lrt` although GTFS types them 0; the rail lines are 1, 2, 4, 5 and 6.
- Feeds and board periods: `manifest.json` lists a `FeedEntry` per feed; `StaticStore.for_date` picks the covering feed with the later start, then version; `lookup_trip` tries today's feed then the others; `Manifest.prune` (only on a build, from `refresh._register`) drops feeds that ended before yesterday. `StaticStore.status` is `expiring` within `EXPIRING_DAYS` (3) of the end date with no newer feed loaded, `expired` after it.
- Patterns: `patterns` (one per route, direction and stop sequence; `rank` 1 is the most-used pattern per route and direction and there is no rank 0, since `static/build` increments before it writes the row) and `pattern_stops` (`pos`, `secs` median from the start, `dist_m` along the shape); `departures` is the per-stop timetable (`static/schedule.departures_at`); `station_members` and `transfers` (150 m) are the planner's graph (`static/schema.py`).
- The disruptions element (spec 4.4) comes from `domain/disruptions.element` (an `Alert`; kinds in `adapters/ttc_alerts.KINDS`: closure, suspension, detour, rsz, elevator, escalator, delay, other) or `detour_elements` (detour groups); `source` is `alerts.ttc.ca`, `detour feed` or `gtfsrt alerts`; `element_key` dedupes. `Disruptions` indexes elements by station, stop and route and holds a connection getter, so a snapshot built on the loop is readable from worker threads.
- Closure span model (spec 6.7 item 4): `Disruptions.station_service`, `platform_served`, `shuttle_terminus`, `_at` and `platform_closed_now` in `domain/disruptions.py`; `trains.suspended`.
- Connections (spec 6.7 item 5): `planner.search` (with its `usable` and `rideable` predicates), `planner.overlay`, `_option`, `_rank` and `_touch` in `domain/planner.py`.
- Disruption semantics (closure spans, served end platforms, suspended platforms, shuttle legs) live in `domain/disruptions.py` and `domain/planner.py` and are specified in spec 6.7. Change them there, never in a tool.

## Design decisions you must not undo

- On-demand fetching, no pollers (decision g, spec 6.6): nothing fetches until a tool asks; the only timers are the stdio CKAN recheck and the http bucket pull. Static data comes only from CKAN by ETag (principle 1) and every request carries the project User-Agent (principle 7).
- The decision record, spec 7: a `route_shape` is an encoded polyline by default (6.1); b station accessibility derives from the rail platforms (6.2); c `trips.wheelchair_accessible` is ignored (6.2); d crowding rides inline on every arrivals row (6.3); e a detour with no matching reason keeps `reason: null` (6.4); f staleness thresholds of 90 s, 60 s, 10 min and 30 min, static `expiring` for 3 days (6.5); g the fetch model (6.6); h trip questions: `nearest_stops` as the bridge from an external geocoder, `connections`, `service_status`, propagation into every answer, the alias table, geocoding out of scope (6.7); i the scheduled fallback with `basis` on every arrivals-style row and NTAS coverage in `feed_status` (6.8); j FastMCP streamable HTTP, stateless JSON, in-process gate, Cloud Run in us-east1, stdio for local use (6.9).
- Python 3.14 through uv (`.python-version`; `pyproject.toml` requires 3.13 or later); `fastmcp>=3.4,<4` (3.4.8 in the lock); the wheel ships `ttc_mcp` only.
- Style: dense one-line statements, ruff `line-length` 160 with `E501` and `E741` off (`l` names a leg in the planner); the formatter is not adopted.
- The store is opened `mode=ro&immutable=1` with one connection per thread (`static/store.open_ro`; the `FeedDb.conn` property on a `threading.local`, closed by a finalizer when the thread exits or by `close`); never pass a connection to another thread, pass `Disruptions.build` a getter.
- The planner runs in a worker thread (`tools/derived_tools._plan` through `asyncio.to_thread`) and builds its `closure_verdicts` on that thread's connection; `_input_error` bounds `walk_m` and `limit` so bad input never reaches it. `static/refresh._run` runs the ttc.ca fetch from inside a running loop the same way.
- Bucket ordering (`static/bucket.py`): `push` uploads the feeds first and `manifest.json` last, and `pull` stages each feed in a `.part` file, writes the manifest after the feeds, then removes unlisted `static-*.sqlite` files, so a reader never sees a manifest naming a feed that has not landed.
- Unit tests stay offline, and the http transport stays stateless JSON so an idle client never keeps an instance billing (spec 6.9).

## Live-data gotchas

The README's "Gotchas the code encodes" is the full list, verified live on 2026-10-05 and 2026-10-11. The five an agent trips on first:

- alerts.ttc.ca stamps Toronto local time with a `Z` suffix: `adapters/ttc_alerts.local_time` strips the `Z` and attaches `America/Toronto`; year 1 means open-ended. Never parse a raw alert stamp as UTC.
- GTFS-RT `NO_SERVICE` entities list every platform of every listed station, other lines' platforms at interchanges included: `Disruptions.platform_closed_now` closes a platform from a listing only when the entity names no routes or one of the platform's own lines; the span rule decides at closure stations.
- NTAS keeps publishing predictions at closed platforms: the closure decides (`trains.suspended`, `SUSPENDED_NOTE`), `next_min: []` with NTAS's numbers in `ntas_min` and no `basis`; an empty NTAS answer is `no_prediction` with timetable minutes, never "no trains".
- ttc.ca's `getallroutesandstopsalerts` has no `serviceChanges` key as of 2026-10-11: `domain/service_changes.py` reads GTFS-RT `MODIFIED_SERVICE` entities first and merges ttc.ca rows by title if the key returns; `adapters/ttcapi.parse_service_changes` tolerates its absence.
- Four TTC hosts answer 403 to Python's default User-Agent: `config.DEFAULT_UA` goes on every client (`adapters/http.make_async_client`, `adapters/ckan.make_client`). The unit suite's MockTransport never sees it, so a live 403 shows only in `feed_status` (`status_counts`, `retry_after`).

## Environment variables

Every variable the code reads (`grep -rn os.environ ttc_mcp/`; the gate reads a mapping defaulting to `os.environ`). `scripts/ttc_transit_probe.py` reads its own `TTC_PROBE_CACHE`.

| Variable | Read by | Default | When it matters |
|---|---|---|---|
| `TTC_MCP_TRANSPORT` | `server.main` (`--transport` default) | `stdio`; the image sets `http` | container; local runs pass `--transport` |
| `PORT` | `server.main` (`--port` default) | `8080`; the image and Cloud Run set it | local http, container |
| `TTC_MCP_CACHE` | `config.settings` | `~/.cache/ttc-mcp`; the image sets `/data` | all: the store, `manifest.json`, the build's `downloads/` |
| `TTC_MCP_UA` | `config.settings` | `ttc-mcp/<version> (+https://github.com/LunaParker/ttc-mcp)` | all; leave unset on Cloud Run |
| `TTC_MCP_SKIP_REFRESH` | `config.settings` | off; `1`, `true`, `yes`; the image sets `1` | local stdio and http: skips the startup check and the 24 h recheck; `ttc-mcp-build` ignores it |
| `TTC_MCP_STATIC_BUCKET` | `config.settings`; `server.main` (http only); the default of `ttc-mcp-build --bucket` | unset | container: pull at boot and on the timer, no CKAN check; Job: pull before the CKAN check, push after (`--from-zip` never pulls); stdio ignores it |
| `TTC_MCP_PULL_S` | `config.settings` | `21600`; under 60 refused at startup | container with a bucket |
| `TTC_MCP_NOW` | `runtime.now`, on every call | unset: the Toronto wall clock | any transport; ISO 8601, naive is Toronto; the image smoke, or reproducing a day |
| `GOOGLE_OAUTH_CLIENT_ID` | `config.gate_settings`, from `server.main` for http only | unset: no gate | container (local http to test the gate); never stdio |
| `GOOGLE_OAUTH_CLIENT_SECRET` | `config.gate_settings` | required with the gate | container, from Secret Manager |
| `BASE_URL` | `config.gate_settings` | required with the gate; a trailing `/` is stripped | container; the redirect URI is `BASE_URL/auth/callback` |
| `JWT_SIGNING_KEY` | `config.gate_settings` | required with the gate | container, from Secret Manager |
| `ALLOWED_EMAILS` | `config.gate_settings` (`config.allowed_emails`) | required with the gate; comma-separated, case-insensitive; empty refuses to start | container |
| `GOOGLE_CLOUD_PROJECT` | `config.gate_settings` | unset: OAuth state in memory | container: state in that project's default Firestore database |
| `STORAGE_ENCRYPTION_KEY` | `config.gate_settings` | required with `GOOGLE_CLOUD_PROJECT` | container, from Secret Manager; the Fernet key for that state |

## Deployment practices

- Local stdio: build a store once (`uv run ttc-mcp-build`), then `uv run ttc-mcp --transport stdio`. Claude Code's `.mcp.json` entry is `{"mcpServers": {"ttc": {"command": "uv", "args": ["run", "--project", "<repo>", "ttc-mcp", "--transport", "stdio"]}}}` (the repo's `.mcp.json` is untracked, git-ignored and exists on the maintainer's machine only).
- Local http: `TTC_MCP_SKIP_REFRESH=1 uv run ttc-mcp --transport http`, then `POST http://127.0.0.1:8080/mcp` with `Accept: application/json, text/event-stream`; no gate unless `GOOGLE_OAUTH_CLIENT_ID` is set. The integration suite boots this shape as a subprocess (`tests/integration/conftest.py`).
- The image (`Dockerfile`): `ghcr.io/astral-sh/uv:python3.14-bookworm-slim` pinned by digest; `uv sync --frozen --no-dev --extra cloud` in two layers, dependencies then the project as a non-editable wheel with `--reinstall-package ttc-mcp`; `ENV TTC_MCP_TRANSPORT=http TTC_MCP_SKIP_REFRESH=1 TTC_MCP_CACHE=/data PORT=8080`; `CMD /app/.venv/bin/ttc-mcp`. One image, two commands: the Job overrides the command with `/app/.venv/bin/ttc-mcp-build --bucket <bucket>`. Never `python -m ttc_mcp...` from `/app`, where the uncompiled source copy shadows the wheel. `scripts/smoke_image.sh` is CI's image job: build, assert `sqlite3.threadsafety` is 3, build the fixture store in a container, serve it with `TTC_MCP_NOW=2026-10-06T08:00`, assert the 19 tools exactly and a `routes` answer from `/data`; it fails closed.
- Cloud Run (spec 6.9; `docs/deploy.md` is the paste-and-run runbook with the values table): project `$PROJECT_ID` ($PROJECT_NUMBER), everything in `us-east1`; service `ttc-mcp` (`--allow-unauthenticated`, the gate is the app; min 0, max 1, 1 GiB, cpu-boost, gen2) at `https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app/mcp`; Job `ttc-mcp-static` from the same image, fired by Cloud Scheduler `ttc-mcp-static-daily` at 04:30 Toronto; bucket `gs://ttc-mcp-static-$PROJECT_NUMBER`; two service accounts, `ttc-mcp-sa` for the service (reads) and `ttc-mcp-job-sa` for the Job and the Scheduler (writes the bucket); secrets `ttc-mcp-oauth-client-secret`, `ttc-mcp-jwt-signing-key`, `ttc-mcp-storage-fernet-key`; images in Artifact Registry repo `ttc-mcp`, one tag per deploy. The Job's filesystem is in memory, so both zips and both stores count against its `--memory`.
- Nothing is deployed as of 2026-10-11. Phase 5b is the runbook's first run; validation items 14 to 16 wait for it. When it lands, update the runbook's first line, spec 6.9 and section 11, and the README's status paragraph.
- Billing: the project's hard-cap budget and `billing-killswitch` cover it, no new budget wiring; the cap is project-wide, so check headroom before provisioning (runbook step 1). Expected marginal cost is near zero.

## Working here

### Commands

- `uv sync --extra cloud` (Python 3.14 from `.python-version`; the extra brings the Firestore state store and GCS, and the unit suite's GCS wiring test imports `google.api_core`). `uv run pytest` runs the unit suite in `tests/unit` on the fixture store built from `tests/fixtures/gtfs-mini.zip`.
- `uv run pytest tests/integration` boots the real server and reaches the TTC's hosts; it is deliberately not in CI.
- `uv run ruff check .` is the lint CI runs. The formatter is not adopted; the dense style is deliberate.
- `uv run ttc-mcp-build` builds the static store into `~/.cache/ttc-mcp` (CKAN download and build, a few minutes with the ttc.ca service-level fetch; `--no-ttcapi` skips it, `--from-zip` builds from a local feed, `--bucket` runs the Job's pull and push). Live smoke scripts live in `scripts/` and run with `TTC_MCP_SKIP_REFRESH=1`; `scripts/smoke_image.sh` needs Docker.
- PyCharm run configurations: `Tests`, `Integration (live)`, `Server (stdio)`, `Build static`.

### How work lands

- Test first: a failing test, then the change. One commit per behaviour, plain subject line, the why in the body, no attribution lines.
- Branch, pull request, merge when CI is green. Pushes and pull requests happen on the maintainer's say-so.
- Comments at most two lines; the why belongs in the commit body. Pull request descriptions: at most one paragraph and five bullets.
- Spec edits ride with the change that needs them; a live finding gets a dated line in section 11.
- CI (`.github/workflows/ci.yml`, pushes to main and pull requests) is four jobs: unit (`uv sync --frozen --extra cloud`, `pytest tests/unit`), lint (`ruff check`), build (sdist and wheel, both entry points called from a clean venv) and image (`scripts/smoke_image.sh`); all four green is the merge rule. Dependabot (`.github/dependabot.yml`) watches GitHub Actions, uv (grouped) and the Docker base image weekly.
- The SDD workflow: phase plans are committed under `docs/superpowers/plans/`; task briefs, reports and ledgers sit under `.superpowers/`, which git ignores (its `sdd/.gitignore` is `*`).

### Testing conventions

- The fixture store: `tests/fixtures/gtfs-mini.zip` and `surface-mini.zip`, cut from the real feeds by `scripts/make_fixture.py`, carry routes 1, 5, 121, 300, 400 and 504 only; Line 2 is absent, so its platforms carry no routes in tests. The feed is S1000538, 2026-09-06 to 2026-10-31. `tests/conftest.py` gives `fixture_db` (session scope, built once), `conn` (a read-only connection on it), `app_env` (a temp cache dir holding the fixture as S1000538, refresh off, `runtime.now` pinned to 2026-10-06 08:00 Toronto, `runtime.reset()` around the test) and `two_feeds` (S1000542 and S1000538 loaded together).
- Advisory fixtures: `tests/fixtures/advisories/alerts.json`, `detour.json` and `servicechanges.json` (captured 2026-10-05), `tests/fixtures/bustime/stop-15461.xml` and `stop-notfound.xml`; `tests/rtfeeds.py` builds the protobuf feeds.
- `tests/mockttc.py`: `handler_factory(calls, fail=())` serves every TTC host from those fixtures through an httpx `MockTransport`, recording each URL in `calls` (hosts in `fail` answer 503); `make_app(calls)` installs that client with `runtime.set_realtime` and returns `create_app()`. `tests/unit/test_server.py`'s `app` fixture is `make_app([])` over `app_env`.
- Clocks: `tests/unit/test_disruptions.py` defines `NOW_DAY` (2026-10-06 08:00) and `NOW_NIGHT` (23:30). The fixture closure 77519 is Line 1 York Mills to Eglinton, nightly from 23:00 on October 5 to 7 with shuttle "Will Operate": inactive at `NOW_DAY`, active at `NOW_NIGHT`; its stations are 43274 Eglinton, 43275 Lawrence and 43276 York Mills, so Lawrence is closed and the ends are served on their open side.
- The network guard: `tests/unit/conftest.py`'s autouse `no_network` lets httpx reach loopback only and fails the test at teardown naming the host, because `cache.py` would otherwise swallow the refusal into `staleness: unavailable`.
- Live: the integration suite boots `python -m ttc_mcp.server --transport http` once per session (`server_url`; `fresh_server_url` boots one per test) against the local store and the TTC, and `tests/integration/live.call` asserts no `error`. Smoke scripts: `smoke_static.py` (9 checks), `smoke_realtime.py` (8), `smoke_advisories.py` (10), `smoke_derived.py` (11) and `validate_connections.py` (20 origin-destination pairs, exit 0 when 18 answer under 150 min). Results go in spec section 11 as dated lines.
- Tool-level tests drive the app through fastmcp's `Client(app)` under `@pytest.mark.anyio`; one behaviour per test.

### Known deferred items

- Expired feeds stay in the manifest until the next build (`Manifest.prune` runs only inside `refresh._register`), and nothing deletes bucket objects: `bucket.pull` removes local `static-*.sqlite` files the manifest dropped, but old objects stay in the bucket.
- `advisory._memo` keys on `id(store)` and `id(rt_)` and holds one entry; a test that swaps the store or the realtime object should call `advisory.reset()`, since ids can be reused after collection.
- The network guard covers httpx only; google-cloud-storage, urllib and raw sockets are unguarded, which is why `tests/unit/test_bucket.py` injects a fake client into `GcsBlobs`.

## Where to look next

- `docs/spec.md` section 11: the dated verification log, with the live counts and what each phase gate proved.
- `docs/architecture.html`: the layer picture, two requests end to end, the package layout and the table of what lives where. It predates phase 5a: the Dockerfile sits at the repo root and the runbook in `docs/`, not under a `cloud/` directory, and its test counts are phase-4 numbers.
- The phase plans under `docs/superpowers/plans/` (phases 1 to 4 dated 2026-10-05, phase 5a dated 2026-10-11).
- `scripts/validate_connections.py` for how `connections` is judged; `docs/investigation-2026-09-13.md`, `docs/detours-addendum.md` and `scripts/ttc_transit_probe.py` for the source survey the adapters came from.
