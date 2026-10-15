# TTC network MCP server: specification v0.8

**Date:** 2026-10-14 | **Status:** decisions a to k accepted; phases 1 to 4 implemented and corrected against the code after the 2026-10-11 spec-correctness review; phase 5a (the container) implemented 2026-10-11, nothing deployed; phase 6 (decision k, timetable-exact connections) designed 2026-10-14 and not yet built | **Supersedes:** v0.7 (kept as `docs/spec-history/spec-v0.7.md`)
**Companions (in this repository):** `docs/investigation-2026-09-13.md` (source survey), `docs/detours-addendum.md` (detours), `scripts/ttc_transit_probe.py` (reference behaviour for most tools)

**What changed in v0.8:** `connections` becomes timetable-exact (decision k, section 6.10, phase 6). The v0.4 pattern search with typical minutes, a 4-minute transfer penalty and a 120-minute departure gate is replaced by RAPTOR over the trips themselves: every ride is a real trip with exact board and alight times, transfers are timed from the pathways graph where it reaches both platforms and from the walk formula elsewhere plus a 2-minute change buffer, and the answer is the Pareto set over arrival time and transfers at one departure instant or across a departure window (`window_min`, default 30), ranked by arrival; `arrive_by` is answered. Surface and subway trip updates (`bustime.ttc.ca/gtfsrt/trips` read fully, `gtfsrt.ttc.ca/trips/subway` adopted) move the trips the planner boards within an hour of now, with `basis` per leg; a closure masks the scan and a running shuttle becomes an estimated pseudo-route (`basis: estimate`, assumptions stated), so every later leg keeps a time; detoured trips run on variant patterns with the published delay, which narrows the skipped-stop rule to the detoured route. The static store gains two tables built by `ttc-mcp-build` (`raptor_groups`, `station_paths`; schema 3) and the server loads them once per feed into an immutable in-memory timetable (+65 MB, 0.1 s). The benchmark that fixed the design (plain Python, p50 6 ms per run, 250 ms p95 budget per call, Cloud Run memory unchanged) is the evidence in 6.10 and the 2026-10-14 entry of section 11. Output contract in 4.4: `depart_at`, `arrive_at`, `duration_min`, `estimate` and `basis` per option, `trip_id`, `depart`, `arrive`, `min` and `typical_min` per ride, `transfer` legs with their real slack, `shuttle` legs with minutes; `known_min`, `typical_min_note` and `first_departure` go. Step-free routing, true range RAPTOR, reverse RAPTOR and NEW trips stay out (6.10). Nothing in phases 1 to 5a changes; phase 5b stays parked.

**What changed in v0.7:** the document now describes what the code does, following the spec-correctness review of phases 1 to 4 (2026-10-11, 26 findings, none critical) and the fixes that landed with it. Closures follow the span model: a station strictly inside a closure's span is closed, a span end station is served on the platform facing the open line and is the shuttle terminus, and `next_trains` reports `next_min: []` on a suspended platform because NTAS keeps publishing predictions inside a closure (6.7 item 4, 6.8, 8 item 7). A `connections` option with a shuttle leg reports `typical_min: null` with `known_min`, counts the shuttle as a transfer and ranks after complete options; every disruption names the legs it `affects`; the leg vocabulary gains `transfer` (4.1, 4.4, 6.7 item 5). The step-free verdict is `yes | no | unknown` with a `basis` (4.4, 6.2). `service_status` closures are the shared disruptions element, `reduced_speed_zones` is `{count, zones}` and every `surface` row has one shape (4.4). Source facts corrected: the detour feed is JSON, service changes come from GTFS-RT `MODIFIED_SERVICE` first, `trips/subway` is unused, and alert station names resolve at answer time (2, 2.3, 3, 4.3, 6.4, 6.5). Fetch layer: a failing source is held with exponential backoff and its 403/429 answers counted in `feed_status`, trip ids resolve against every loaded feed, and the stdio server rechecks CKAN every 24 h in a worker thread (2.1, 2.2, 6.9, 8 items 2 and 10). Inputs and outputs recorded as implemented: `connections`, `routes`, `scheduled_departures`, `route_shape` and `alerts.type` names, `meta.cache` adds `static`, `feed_status` rows add `expired`, `detours` carries `entity_id` and a local `fetched_at`, station results carry `id` and `aliases` (4, 4.1, 4.2, 4.4). Section 9 records Python 3.14, the stdio default and the licence wording; section 11 gains the 2026-10-05, 2026-10-06 and 2026-10-11 entries. The build now ends with the self-checks of section 8 items 3 and 9 (`[check]` lines, `checks` in the build report). A same-evening follow-up (2026-10-11) narrowed a station's `platforms` to its rail platforms and moved bus bays and streetcar platforms to `surface_stops`, so the summary and step-free verdict no longer read bus bays flagged 0 (31 of 114 live stations had read `partially accessible`); left the summary `unknown` beside a 0 flag; nulled `first_departure` behind a shuttle leg; dropped route-level alerts from a shuttle-only option; and gave `scheduled_departures` the arrivals stop head (4.1, 4.4, 6.2, 11). Phase 5a (2026-10-11, the container) landed without a version bump: the Google OAuth gate and email allowlist on the http transport, the store kept in the bucket (`ttc-mcp-build --bucket` pulls it, checks CKAN and pushes a rebuild) and pulled by the http server at boot and every six hours, one read-only SQLite connection per thread with the planner in a worker thread, the Dockerfile with the smoke CI runs, and an offline unit suite; nothing is deployed yet and `docs/deploy.md` is the phase 5b runbook (6.9, 9, 11).

**What changed in v0.6:** transport and hosting. The server speaks **streamable HTTP** and is deployed on **Google Cloud Run** (decision j, section 6.9): a FastMCP 3 application with the Google OAuth gate and email allowlist in-process, stateless JSON responses so idle clients do not keep an instance alive, `us-east1` for the always-free tier, and the daily static check moved out of the process into a Cloud Scheduler-driven Cloud Run Job that publishes the SQLite store to a bucket. Local development keeps a stdio mode.

**What changed in v0.5:** the Line 5 and 6 real-time gap is now designed for, not just described. The static layer gains scheduled departures per stop for the current service day, every arrivals-style answer carries a `basis` of `realtime` or `schedule`, and LRT platforms (and any stop whose real-time source is down) fall back to the schedule with that basis stated (decision i, section 6.8). `vehicles` declares Lines 5 and 6 unavailable, and `feed_status` reports NTAS prediction coverage per line.

**What changed in v0.4:** trip questions. A dry run of "I'm at Cherry St and Front St East, how do I get to Union?" and of a Line 1 closure between TMU and Union showed that the v0.3 tools could answer only if the model composed five or six calls correctly, and that a closure would stay invisible unless the model happened to ask for alerts. v0.4 adds `nearest_stops` (the bridge from a geocode supplied by the Google Maps MCP), `connections` (direct and one-transfer options with live and disruption overlay), `service_status`, disruption propagation into every stop, station, route, arrivals and next-train answer, and a station alias table. Geocoding itself stays outside this server. Details in section 6.7.

**What changed in v0.3:** the server no longer polls. Every real-time and advisory source is fetched on demand through a TTL cache with single-flight coalescing and a last-known-good fallback (decision g, section 6.6). The static GTFS keeps one scheduled check per day. Arrivals now use the cheapest per-stop endpoints; the 3-second `modified_update` feed is demoted to parity tests.

**Deployment model:** single-user, hosted by its maintainer on Cloud Run behind a Google OAuth gate (or run locally over stdio by another developer). The priority is the least bandwidth and the fewest requests to the TTC's servers, and near-zero marginal cloud cost, not throughput for many users. Latency of a few hundred milliseconds per tool call, plus a cold start of a few seconds after idle, is acceptable.

## 1. Purpose and principles

The server exists so that Claude (and any MCP client) can reason about the TTC network as it is right now, not as it was when a dataset was pasted in.

1. **Nothing baked in.** Static data is resolved through the City of Toronto CKAN API and re-downloaded only when the resource ETag changes.
2. **Fetch on demand, never in the background.** No poller runs while nobody is asking. The only scheduled work is one `HEAD` per day per static package, and it runs outside the serving process (a Cloud Run Job; in local stdio mode, a once-a-day worker thread inside the process, section 2.1). Repeated questions inside a feed's update interval are served from a TTL cache.
3. **Public stop numbers are the canonical identifier** in every tool input and output. Other ID spaces stay inside the server.
4. **Every answer carries provenance and freshness**: source, feed timestamp, when it was fetched, whether it came live, from the TTL cache, or from the last-known-good fallback, and a staleness verdict.
5. **Undocumented endpoints are fragile**: one adapter each, defensive parsing, "unavailable" in the output rather than a crashed tool.
6. **Disruptions travel with every answer.** A closed station, a detoured stop, or a suspended line is reported inside the tool that names it (`route_stops`, `arrivals`, `next_trains`, `connections`), not only in `alerts`. The model should never be able to route a rider into a closed station without seeing the closure.
7. **Be a polite client**: a real User-Agent on every request (four TTC hosts return 403 to the default `Python-urllib` agent), conditional requests where supported, single-flight so concurrent tool calls never fetch the same thing twice.

## 2. Architecture

```
 static layer (SQLite, rebuilt per board period by a Cloud Run Job on a daily Cloud Scheduler trigger; published to a GCS bucket; pulled by the service at cold start and every TTC_MCP_PULL_S seconds)
   CKAN package_show -> merged GTFS zip   -> stops (hierarchy) / routes / trips / stop_times / calendar / shapes / pathways / levels
                     -> surface GTFS zip  -> crosswalk: stop_code <-> surface stop_id
   derived: stop_directions, route_patterns (with stop_sequence), pattern_shapes, pattern_run_times (median minutes between
            consecutive stops per pattern), route_service_hours, station_children, station_aliases, transfer_graph
            (stop <-> station membership, stop <-> stop within walking distance), spatial index for nearest_stops,
            elevator_units (unit code -> nodes), route_series, route_service_level (ttc.ca, refreshed with the static build),
            raptor_groups (int32 departure columns per pattern, service and overtaking split; v0.8), station_paths (platform-to-platform
            seconds from the pathways graph; v0.8)

 on-demand fetch layer (TTL cache + single-flight + last-known-good; nothing runs unprompted)
   source                                              size    fetch   TTL      used by
   bustime.ttc.ca/bustime/map/getStopPredictions.jsp   1 KB    0.07 s  30 s/stop   arrivals (minutes, vehicle id, direction)
   bustime.ttc.ca/gtfsrt/vehicles (protobuf)           90 KB   0.06 s  30 s     crowding on arrivals, vehicles
   bustime.ttc.ca/gtfsrt/trips (protobuf)              568 KB  0.10 s  30 s     trip ids for detour flags (lazy); surface trip updates for connections (v0.8)
   ntas.ttc.ca/api/ntas/get-next-train-time/{platform} <1 KB   0.2 s   10 s/platform  next_trains
   gtfsrt.ttc.ca/trips/subway (protobuf)               12 KB   0.06 s  30 s     subway trip updates for connections (routes 1, 2, 4; v0.8)
   alerts.ttc.ca/api/alerts/list (ETag -> 304)         51 KB   0.07 s  25 s     alerts, accessibility, stop/station enrichment
   gtfsrt.ttc.ca/alerts/{all,accessibility} (protobuf) 40 KB   0.15 s  60 s     cause/effect, platform ids
   gtfsrt.ttc.ca/trips/detour (JSON)                   163 KB  0.10 s  5 min    detours, detoured-route set, detour flags on arrivals
   www.ttc.ca/ttcapi/routedetail/getallroutesandstopsalerts  80 KB 0.43 s 10 min  service_changes, detour reasons
   gtfsrt.ttc.ca/trips/modified_update                 574 KB  3.0 s   --       parity tests only (server-side generation cost)

 tool API (MCP)
```

### 2.1 Static layer

- **Where it runs (v0.6):** the daily check and the rebuild are a Cloud Run Job (`ttc-mcp-static`), triggered by Cloud Scheduler at 04:30 America/Toronto. The Job resolves both packages, compares ETags with `manifest.json` in the bucket, rebuilds on change into `static-<feed_version>.sqlite`, uploads it, and rewrites the manifest (which lists the active feed and, during a period change, the incoming one). The serving instance never builds anything: at cold start it reads the manifest and downloads the referenced database(s) into its cache directory (`TTC_MCP_CACHE`, `/data` in the image; Cloud Run's filesystem is in memory, so the memory limit must cover it; the build keeps the file compact, see 6.9), and pulls again every `TTC_MCP_PULL_S` seconds while it stays warm (6.9). In local stdio mode the same code runs the check in-process: once before the server starts (a rebuild at that point blocks server start, not tool calls; a check that fails, the portal unreachable or answering with an error, is logged and the server starts on the store it has, `server._startup_check`, and the CKAN client gives up connecting after 10 s) and then every 24 h in a worker thread while the server runs (`create_app(recheck=...)`, enabled from `main` for stdio with refresh on; `--no-refresh` or `TTC_MCP_SKIP_REFRESH` turns both off, and the HTTP transport never rechecks because that is the Job's role). Every check, changed or not, records `checked_at` and the CKAN `last_modified` in `manifest.json`, which `feed_status` shows as `last_check` and `ckan_last_refreshed`.
- Packages: `merged-gtfs-ttc-routes-and-schedules` (primary) and `surface-routes-and-schedules-for-bustime` (crosswalk only). Once a day: `package_show`, then `HEAD` on each zip and compare ETags; download and rebuild only on change (about 84 MB + 83 MB per board period, roughly every six weeks). The CKAN API is cached 15 minutes upstream, so a new feed can take that long to become visible; this does not matter at a daily cadence.
- Rebuild is one streaming pass over `stop_times.txt` (~7.7 M rows; measured 2026-10-05: 13 s on an M4 Pro, 104 MB SQLite per feed, downloads of both zips about 3 s) into a temporary database swapped in atomically. An external build (`ttc-mcp-build`, or the Job) never touches the serving process: tool calls keep using the previous database until `manifest.json` changes on disk, and the store is reopened on the next call. The in-process startup build in stdio mode blocks server start, not tool calls. A server with no database answers static tools with an explicit error (`static store not built yet ...; run ttc-mcp-build`) rather than blocking.
- Patterns store `(stop_sequence, stop_id)` pairs because 9,099 of 137,912 trips do not number stops from 1 and GTFS-RT selectors reference `stop_sequence`.
- **Timetable index (v0.8, schema 3):** the same streaming pass packs every trip's departure seconds into its `(pattern_id, service_id)` group as it goes, then sorts each group by first departure, splits it greedily into runs where no trip overtakes another (7 splits on S1000542, the 300 Blue Night the worst) and writes one `raptor_groups` row per run: `n_stops` int32 columns, one per pattern position with the trips in order, plus the trip ids (20 MB on the live feed). A Dijkstra over each station's pathway nodes writes `station_paths` (platform to platform, seconds; `traversal_s` where set, else `length_m` at 1 m/s, else 30 s an edge; fare-gate edges excluded; 63 stations, 136 rail platforms today). The build logs `[check] patterns split for overtaking: n` and `[check] stop_times with arrival != departure: n` (0 on every TTC feed seen, which is why one time per stop is enough). The server never computes the index: it loads the blobs once per feed on the first `connections` call (0.1 s, +65 MB) and keeps them for the feed's lifetime (6.10).
- **Board-period overlap (decided):** keep the outgoing and incoming feeds loaded together from the moment the new package is seen until the old `feed_end_date` passes. When two feeds cover the same date (the Sept 30 revision S1000542 overlapped S1000538 to the same end date), the one with the later start, then the later version, wins. Trip ids resolve against whichever loaded feed contains them (implemented: `StaticStore.lookup_trip` tries today's feed first, then every other loaded feed, and `detours`, `vehicles`, `arrivals` and the advisory snapshot resolve through it); detours whose trips resolve in neither are reported with `pattern: "unknown"`. The first period change, due about 2026-10-31, is validation item 2.
- The ttc.ca route-detail call (`serviceLevel`, branch metadata, 235 requests) runs once per static rebuild, not daily.

### 2.2 On-demand fetch layer

Each source has one adapter returning typed records plus a `SourceStatus`. Adapters go through a shared cache with these rules:

- **TTL cache.** A response is reused for the source's TTL (table above), which equals the feed's own update interval, so a burst of tool calls costs one fetch and never sees older data than a poller would.
- **Single-flight.** If a fetch for a source is in progress, other callers await that same fetch instead of starting another (an `asyncio.Lock` or future per cache key).
- **Last-known-good.** The last successful response is kept beyond its TTL. When a fetch fails (timeout, 5xx, 403, parse error), the tool answers from it with `meta.cache = "stale_fallback"` and its real age, up to a maximum stale age (10 minutes for real-time sources, 1 hour for advisories); past that the tool reports `unavailable`.
- **Backoff (implemented 2026-10-11, principle 7).** A failed fetch holds its cache entry for `min(ttl x 2^failures, max_stale / 4)` seconds; inside the hold the entry answers from last-known-good (`stale_fallback`, with `"<error>; retry in N s"`) or raises `unavailable` without a request, and a success clears the hold. 403 and 429 answers are counted per source and shown by `feed_status` (`failures`, `retry_after`, `status_counts`); `probe: true` bypasses the hold. Requests time out at 5 s for the advisory sources and 10 s for the real-time ones.
- **Conditional requests.** `alerts.ttc.ca` honours `If-None-Match` (a 304 costs 0.05 s and no body); the CKAN zips expose ETags for the daily `HEAD`. The GTFS-RT hosts send no validators, so their fetches are unconditional.
- **Cache keys** include parameters for per-stop and per-platform endpoints (`getStopPredictions:15461`, `ntas:13863`).

**Arrivals path (least bandwidth).** For a bus or streetcar stop: the per-stop XML (1 KB) gives minutes, direction and vehicle id; the vehicle feed (90 KB, shared across all stops for 30 s) gives crowding by vehicle id. The detour flag is lazy: the detour feed (163 KB, 5 min TTL) yields the set of routes with an active detour; only when the stop's route is in that set does the server fetch `bustime.ttc.ca/gtfsrt/trips` (568 KB, 30 s TTL) to map vehicle id to trip id and test membership in the detour's `selectedTrips`. For a rail platform: NTAS per platform (<1 KB). Typical arrivals call: about 91 KB and 0.15 s; worst case with a detoured route: about 820 KB and 0.3 s.

**Why `modified_update` is out of the hot path.** It is the only feed keyed by public stop numbers, but it takes 3 seconds to generate server-side regardless of format (574 KB protobuf in 2.97 s versus 568 KB from `bustime.ttc.ca` in 0.10 s). Everything it offered is obtainable elsewhere: public-number keying via the per-stop XML or the crosswalk, detour links via the detour feed's `selectedTrips`. It remains in the nightly parity test.

### 2.3 Advisory layer

- `alerts.ttc.ca` is primary (closures, RSZ, elevators with unit codes, unplanned detours); GTFS-RT alerts supply `cause`/`effect` and platform ids; service changes are read from GTFS-RT `MODIFIED_SERVICE` alerts first, with `getallroutesandstopsalerts.serviceChanges` rows merged in when that key is present (it has been absent since October 2026). All fetched on demand with the TTLs above.
- Station references in alerts are resolved with `stop_id OR stop_code` (section 3).

## 3. Identifier model

| Concept | Canonical value in tools | Internal joins |
|---|---|---|
| Stop / platform | public stop number (merged GTFS `stop_id`, equal to `stop_code` except 42 records) | Surface internal `stop_id` via `stop_code` for `bustime.ttc.ca` protobuf feeds |
| Station | merged GTFS `location_type 1` `stop_id` (34 stations carry a synthetic 43265..43298 with the legacy id in `stop_code`) | alerts use the legacy id for those 34: join `stop_id OR stop_code` (63/63 alert ids resolve) |
| Route | `route_short_name` (`504`, `1`, `300`) | equals `route_id` in the merged feed |
| Direction | `direction_id` plus headsign text | ttc.ca branch letters are labels only |
| Trip | GTFS `trip_id` | identical across TTC feeds; `NEW` trips have negative ids and no static row |
| Vehicle | `vehicle.id` (fleet number, e.g. `4502`) | present on trip updates (1,697/1,699), vehicle positions, and the per-stop XML (`<v>`) |
| Detour | `detourId` (grouping key) with member entity ids `detour_{n}_{detourId}:{dir}_{dir}_{pattern}_{route}_{dest}` | `selectedTrips.tripIds` for membership |
| Elevator / escalator | unit code (`12D2L`, `16B4E`) | pathway nodes named `Elevator_{code}_{level}` where present |
| Station alias | any name a rider or a TTC feed uses (`TMU`, `Dundas`, `Bloor`, `Yonge`, `Sheppard`, `St. Clair`) | `station_aliases` table built at build time from station names, platform prefixes, surface-stop suffixes and a short manual list (section 6.7); alert `stopStart`/`stopEnd` names resolve at answer time through `resolve_station` (station names, aliases, then a `LIKE` fallback on station names), since a static build cannot see live alerts |

## 4. Tools

Every output includes `meta: {source, feed_timestamp, fetched_at, cache, static_feed, staleness}` where `cache` is `live | ttl | stale_fallback | static` (`static` when the answer fetched nothing; `feed_timestamp` is then absent), or null with `staleness: unavailable` when a live answer had no source answer at all (`vehicles` with its feed down, `arrivals` with both BusTime sources down) and `staleness` is per section 6.5. Answers that carry disruptions add `meta.advisories` with the snapshot's own freshness. `feed_status` source rows describe a cached entry rather than an answer, so their `cache` may also read `expired` (past its TTL, not asked again yet). Inputs are JSON.

### 4.1 Core

| Tool | Input | Output | Fetches (beyond cache) |
|---|---|---|---|
| `find_stops` | `query` text (stop name, station name or alias, stop number), optional `route`, `direction`, `mode` | stops with number, name, coordinates, wheelchair flag, routes/directions/headsigns served, parent station | none (static) |
| `nearest_stops` | `lat`, `lon` (from the Google Maps MCP or any geocoder), `radius_m` (default 500), `limit` (default 10), `kind` = `any` / `stop` / `station` | stops or stations ordered by distance with walk minutes, routes/directions/headsigns, station membership, and active disruptions | alerts list for the disruption flags |
| `service_status` | none | one-call network picture: subway closures as station spans with shuttle info, suspended or detoured surface routes, reduced speed zones, elevator outages by station, counts and `as_of` | alerts list + detour feed |
| `connections` | `origin` and `destination`, each a stop number, a station id or alias, or `{lat, lon}`; options `max_transfers` (0 to 3, default 3), `walk_m` (1 to 2000, default 400), `depart_at` (ISO, Toronto time when it has no offset; default now) or `arrive_by` (ISO; giving both is an error), `window_min` (0 to 60, default 30), `limit` (1 to 10, default 4) | the Pareto-optimal journeys over the timetable (decision k): legs (walk / ride / transfer / shuttle) with exact `depart` and `arrive` on every leg, the trip ridden, `typical_min` beside each ride's exact minutes, `basis` per ride (`realtime` / `schedule` / `estimate`), door-to-door `duration_min`, `estimate` when a shuttle leg was assumed, next departures at the first board stop (live within an hour of now, else timetable), every disruption touching a leg with the legs it `affects`, and a `note` saying why a list is empty | the two trip-update feeds within an hour of now; arrivals sources for the first ride; alerts and detour feed for the overlays |
| `stop` | stop number | full record incl. station membership and `disruptions[]` (closures on the station named by the stop, detours skipping it, route-level alerts) | alerts list (51 KB / 304) + detour feed |
| `station` | station id, name or alias | rail platforms and `surface_stops` (bus bays, streetcar platforms), each with status, entrances, units with live status, accessibility summary from the rail platforms, `service` per line (`normal` / `suspended` / `reduced` with alert and shuttle info) | alerts list |
| `routes` | filters `series`, `overnight`, `mode` | long name, series, service level, service hours by day type, overnight share | none |
| `route_stops` | route, direction, optional `pattern` | ordered stops per pattern with trip counts and headsigns; each stop carries `status` (`open` / `closed` / `skipped_by_detour`) | alerts list + detour feed |
| `service_hours` | route, date | runs that date? (calendar + exceptions), first/last trip | none |
| `scheduled_departures` | stop number (or stop code) or platform id, optional route, `at` time (ISO, default now), `limit` | the resolved stop (number, name, coordinates, station) and timetable departures for the current service day (handles times past 24:00 and the 00:00 to 04:00 window belonging to the previous service day), with trip, headsign and pattern | none (static) |
| `arrivals` | stop number, optional route, `limit` | next vehicles with crowding inline (decision d) and detour flag, plus `disruptions[]` for the stop's routes and for the station named by the stop; each row carries `basis` = `realtime` or `schedule`, and scheduled departures fill in whenever no real-time prediction exists (decision i) | per-stop XML + vehicles (or NTAS for rail platforms); detour feed; trips only if the route is detoured; alerts list; static `stop_times` for the schedule basis |
| `next_trains` | platform id, station id or alias | per platform: line, direction text, next trains (minutes) with `basis`, and `service` = `normal` / `suspended` (with the closure alert and shuttle info; `next_min: []`, no `basis`, what NTAS said in `ntas_min`) / `no_prediction` (with `scheduled_next_min` from the timetable); a served end platform of a closure stays `normal` with its real minutes | NTAS per platform + alerts list + static `stop_times` |
| `alerts` | route / station / stop / `type` in {closure, suspension, detour, rsz, elevator, escalator, delay, other} | normalised alerts with resolved stop/station objects | alerts list + GTFS-RT alerts |
| `feed_status` | optional `probe: true` | per-source cache state and freshness; with `probe`, one live request per source | none, or one per source |

### 4.2 Added items

| Tool | Input | Output | Fetches |
|---|---|---|---|
| `route_shape` | route, direction, optional `pattern`, `format` = `polyline` (default, decision a) or `geojson`, optional `simplify_m`, optional `from_stop` and `to_stop` (both or neither) | canonical or requested pattern shape; the `from_stop`/`to_stop` span sliced by `shape_dist_traveled` | none |
| `accessibility` | station or stop | platform flags, entrance flags, units with live status and pathway context, station summary (decision b); trip flag ignored (decision c) | alerts list (+ GTFS-RT accessibility for platform ids) |
| `vehicles` | route or `vehicle_id` | position, bearing, speed, current stop, occupancy label + enum + confidence, trip/headsign; for routes 1, 2, 4, 5, 6 returns `unavailable` with the reason (no positions are published for rail) | vehicles (+ trips for headsigns when the trip is not static) |
| `detours` | optional route | grouped by `detourId`; reason may be `null` (decision e) | detour feed + service changes |
| `service_changes` | optional route / date | board-period changes with parsed routes, dates, category, text, URL | service changes |

### 4.3 TTLs, request budget, and conditional fetching

| Source | Observed behaviour | TTL | Stale-if-error max |
|---|---|---|---|
| `getStopPredictions.jsp` (per stop) | TTC's own map endpoint; predictions refresh with the BusTime tick | 30 s per stop | 10 min |
| `bustime.ttc.ca/gtfsrt/vehicles`, `/trips` | header timestamp advances 30 s per tick, 5 to 7 s behind wall clock | 30 s | 10 min |
| `gtfsrt.ttc.ca/trips/subway` | timestamp = request time; routes 1, 2 and 4 only, public platform ids (6.8) | 30 s (v0.8; stale after 60 s, decision f) | 10 min |
| `ntas.ttc.ca` (per platform) | no cache headers | 10 s | 5 min |
| `alerts.ttc.ca/api/alerts/list` | `max-age=25`, `ETag`, `Last-Modified` | 25 s, then conditional GET | 1 h |
| `gtfsrt.ttc.ca/alerts/*` | timestamp = request time | 60 s | 1 h |
| `gtfsrt.ttc.ca/trips/detour` | served as JSON; no header timestamp; stable across 40 min | 5 min; freshness signal = HTTP `Date` | 1 h |
| `www.ttc.ca/ttcapi/*` service changes | `no-cache` | 10 min | 1 h |
| ttc.ca route detail | static-ish | refreshed per static rebuild | n/a |
| CKAN `package_show` + zip `HEAD` | API cached 15 min upstream; zips have ETags | once per day | n/a |

Request budget compared with v0.2 polling: polling cost about 14,000 requests and 4 GB per day whether or not anyone asked anything; on demand, a day with 50 questions costs on the order of 50 to 150 requests and 10 to 30 MB, plus one `HEAD` per package per day and the two zips per board period.

**User-Agent (decided):** send `ttc-mcp/<version> (+contact)` on every request. Verified: `alerts.ttc.ca`, `ntas.ttc.ca`, `www.ttc.ca`, `gtfsrt.ttc.ca` return 403 to `Python-urllib`; any custom string returns 200; `bustime.ttc.ca` does not filter.

### 4.4 Output schemas (normative)

```jsonc
// arrivals (decision d: crowding inline; detour flag lazy)
{ "stop": {"number": "15461", "name": "Cherry St at Front St East", "lat": 43.65284, "lon": -79.358092},
  "arrivals": [
    { "route": "504", "direction_id": "0", "headsign": "East - 504A King towards Distillery",
      "eta_min": 11, "vehicle_id": "4475", "basis": "realtime", "scheduled_only": false,
      "detour_id": null,                       // detourId when the vehicle's trip is in a detour's selectedTrips
      "crowding": {"label": "Not busy", "gtfs": "EMPTY", "confidence": "low"} } ],
  "meta": {"source": "bustime getStopPredictions.jsp + gtfsrt/vehicles", "feed_timestamp": 1789331032,
           "fetched_at": "2026-09-13T20:24:00Z", "cache": "live", "static_feed": "S1000538", "staleness": "fresh"} }

// next_trains
{ "station": {"id": "14572", "name": "Bloor-Yonge"},
  "platforms": [ {"platform": "13863", "name": "Bloor Station - Northbound Platform", "line": "1",
                  "direction_text": "Northbound to Finch", "next_min": [1, 3, 5], "source": "ntas"} ],
  "meta": {...} }

// route_shape (decision a)
{ "route": "504", "direction_id": "0", "pattern": 1, "headsign": "East - 504B King towards Broadview Station",
  "shape_id": "shp-504-04", "trips_using": 485, "format": "polyline", "polyline": "a}wiG...", "length_m": 20112,
  "stops": [ {"seq": 1, "number": "2032", "dist_m": 0} ], "meta": {...} }
// with format=geojson: "geometry": {"type": "LineString", "coordinates": [[lon, lat], ...]}

// accessibility (decisions b, c)
{ "station": {"id": "14572", "name": "Bloor-Yonge"},
  "summary": "accessible",                  // accessible | partially accessible | not accessible | unknown
  "platforms": [ {"platform": "13863", "name": "...", "wheelchair": 1} ],   // rail platforms only; bus bays are the station's surface_stops
  "entrances": [ {"id": "100254", "name": "Main Entrance", "lat": 0, "lon": 0, "wheelchair": 1} ],
  "units": [ {"code": "9Y1L", "kind": "elevator", "levels": ["Concourse Level", "Platform Level"],
              "status": "out_of_service", "alert_id": "76084", "text": "Queen: Elevator 9Y1L out of service between ...",
              "node_match": true} ],
  "uncoded_nodes": {"elevator": 0, "escalator": 0, "stair": 0}, "upcoming": [],
  "step_free": {"verdict": "yes", "basis": "nodes"}, "meta": {...} }
// step_free (6.2): verdict yes | no | unknown; basis nodes | alerts | platforms | platforms unknown | no elevator nodes | uncoded nodes;
// no/alerts carries basis_note "a step-free path may remain through another elevator"

// vehicles
{ "vehicles": [ {"vehicle_id": "4502", "route": "504", "trip_id": "108895010", "headsign": "...",
                 "lat": 43.65, "lon": -79.36, "bearing": 241, "speed_mps": 0.0,
                 "current_stop": "15461", "current_status": "INCOMING_AT",
                 "crowding": {"label": "Not busy", "gtfs": "EMPTY", "confidence": "low"}} ], "meta": {...} }

// detours (decision e)
{ "detours": [ { "detour_id": "695", "route": "112",
                 "reason": {"title": "112 West Mall - Temporary route change due to sewer repairs",
                            "period": ["2026-06-09T07:00", "2026-10-01T07:00"], "url": "https://www.ttc.ca/..."},   // or null
                 "service_dates": ["20260913", "20260920"],
                 "directions": [ { "direction_id": "0", "headsign": "South - 112 West Mall towards Kipling Station via Renforth Station",
                                   "leaves_after": {"number": "8067", "name": "Renforth Dr at Eglinton Ave West South Side"},
                                   "rejoins_at": {"number": "8641", "name": "Wellesworth Dr at Faversham Cres"},
                                   "skipped": [ {"number": "7773", "name": "Gentian Dr at Sagamore Cres", "lat": 0, "lon": 0} ],
                                   "path_polyline": "...", "path_length_m": 19476,
                                   "trips_affected": 67, "entity_id": "detour_0_695:0_0_1209_112_..." } ] } ],
  "meta": {"source": "gtfsrt.ttc.ca/trips/detour", "feed_timestamp": null, "fetched_at": "2026-09-13T16:31:00-04:00", "cache": "ttl", ...} }
// one entity per direction, so entity_id is singular; fetched_at is the local fetch time and the HTTP Date is the feed's
// http_date freshness signal (feed_status), which drives the staleness verdict

// nearest_stops (input from a geocoder; this server does not geocode)
{ "origin": {"lat": 43.6527, "lon": -79.3581},
  "results": [ {"number": "15460", "name": "Cherry St at Front St East", "distance_m": 29, "walk_min": 0,
                "kind": "stop", "station": null,
                "served": [ {"route": "504", "direction_id": "1", "headsign": "West - 504A King towards Dundas West Station"} ],
                "disruptions": [] },
               {"id": "15772", "number": "15772", "name": "Union", "aliases": ["union"], "distance_m": 1914, "walk_min": 31, "kind": "station",   // number kept one release for compatibility
                "lines": ["1"], "disruptions": [ {"type": "closure", "route": "1", "span": ["TMU", "Union"], "alert_id": "..."} ] } ],
  "meta": {...} }

// service_status (closures are the shared disruptions[] element below, active ones first)
{ "as_of": "2026-09-13T16:31", "rsz_as_of": "2026-09-13T06:00",
  "closures": [ {"type": "closure", "source": "alerts.ttc.ca", "alert_id": "75803", "alert_type": "Planned", "route": "1", "routes": ["1"],
                 "stations": ["43272", "14590", "14591", "14572", "14588", "14589"],
                 "station_names": ["St Clair", "Summerhill", "Rosedale", "Bloor-Yonge", "Wellesley", "College"],
                 "span": ["St Clair", "College"],                    // the alert's stopStart and stopEnd names
                 "direction": "Both ways", "windows": [["2026-09-14T23:59-04:00", "2026-09-15T03:30-04:00"], "..."],
                 "active_now": false, "shuttle": "Will Operate", "text": "Line 1 Yonge-University: There will be no subway service ...", "url": "..."} ],
  "surface": [ // one shape for alert-derived and detour-feed rows
               {"route": "52D", "effect": "No Service", "source": "alerts.ttc.ca", "alert_id": "77810", "detour_id": null, "direction": "Westbound",
                "from": "...", "to": "...", "skipped_stops": 34, "reason": "Collision", "service_dates": null, "active_now": true, "text": "..."},
               {"route": "112", "effect": "Detour", "source": "detour feed", "alert_id": null, "detour_id": "695",
                "direction": "South - 112 West Mall towards Kipling Station via Renforth Station",   // "Both ways" when the group has both directions
                "from": "Renforth Dr at Eglinton Ave West South Side", "to": "Wellesworth Dr at Faversham Cres", "skipped_stops": 7,
                "reason": "112 West Mall - Temporary route change due to sewer repairs", "service_dates": ["20260913", "20260920"],
                "active_now": true, "text": "..."} ],
  "reduced_speed_zones": {"count": 16, "zones": [ {"route": "1", "direction": "Southbound From Finch", "span": ["...", "..."], "stations": ["..."],
                                                   "text": "...", "reduced_speed_kmh": 25, "length_m": 0, "track_percent": 0, "average_speed_kmh": 0,
                                                   "distance_m": 0, "target_removal": "..."} ]},
  "elevators_out": {"Woodbine": ["12D2L"], "Queen": ["9Y1L"]}, "escalators_out": {},
  "counts": {"closures": 1, "closures_active_now": 0, "surface": 2, "rsz": 16, "elevators_out": 2, "escalators_out": 0},
  "meta": {...} }

// connections (decisions h and k; v0.8)
{ "from": {"lat": 43.6527, "lon": -79.3581}, "to": {"station": "15772", "name": "Union"},
  "depart_at": "2026-10-14T08:00-04:00", "window_min": 30,     // or "arrive_by": "2026-10-14T09:00-04:00" (never both)
  "options": [
    { "rank": 1, "transfers": 1, "depart_at": "2026-10-14T08:04-04:00", "arrive_at": "2026-10-14T08:17-04:00", "duration_min": 13,
      "walk_m": 90, "stops": 9, "basis": "realtime", "estimate": false, "disruptions": [],
      "legs": [ {"mode": "walk", "to": {"number": "4144", "name": "..."}, "distance_m": 55, "min": 1,
                 "depart": "2026-10-14T08:04-04:00", "arrive": "2026-10-14T08:05-04:00"},
                {"mode": "ride", "route": "503", "direction_id": "1", "headsign": "West - 503 Kingston Rd towards ...", "transit": "streetcar",
                 "pattern_id": 41, "trip_id": "109012345", "board": {"number": "4144", "name": "..."},
                 "alight": {"number": "15637", "name": "King St West at Yonge St West Side - King Station"}, "stops": 8,
                 "depart": "2026-10-14T08:05-04:00", "arrive": "2026-10-14T08:13-04:00", "min": 8, "typical_min": 9, "basis": "realtime",
                 "next": [ {"eta_min": 5, "vehicle_id": "4475", "basis": "realtime", "crowding": {"label": "Not busy"}, "headsign": "..."} ]},
                {"mode": "transfer", "from": {"number": "15637", "name": "..."}, "to": {"number": "13814", "name": "King Station - Southbound Platform"},
                 "distance_m": 35, "walk_min": 1, "via": "walk", "depart": "2026-10-14T08:13-04:00", "arrive": "2026-10-14T08:15-04:00", "min": 2},
                {"mode": "ride", "route": "1", "direction_id": "1", "headsign": "...", "transit": "subway", "pattern_id": 2, "trip_id": "109020001",
                 "board": {"number": "13814", "name": "..."}, "alight": {"number": "13815", "name": "Union Station - Northbound Platform"}, "stops": 1,
                 "depart": "2026-10-14T08:15-04:00", "arrive": "2026-10-14T08:17-04:00", "min": 2, "typical_min": 2, "basis": "realtime"} ] },
    { "rank": 2, "transfers": 0, "depart_at": "2026-10-14T08:08-04:00", "arrive_at": "2026-10-14T08:28-04:00", "duration_min": 20,
      "walk_m": 160, "stops": 12, "basis": "schedule", "estimate": false, "disruptions": [],
      "legs": [ {"mode": "walk", "to": {"number": "15246", "name": "Mill St at Cherry St West Side"}, "distance_m": 160, "min": 3, "depart": "...", "arrive": "..."},
                {"mode": "ride", "route": "121", "direction_id": "1", "headsign": "West - 121 Esplanade-River Towards Union Station", "transit": "bus",
                 "pattern_id": 812, "trip_id": "109030002", "board": {"number": "15246", "name": "..."}, "alight": {"number": "16754", "name": "1 Front St West - Union Station"},
                 "stops": 12, "depart": "2026-10-14T08:11-04:00", "arrive": "2026-10-14T08:28-04:00", "min": 17, "typical_min": 17, "basis": "schedule",
                 "next": [ {"eta_min": 11, "vehicle_id": null, "basis": "schedule", "scheduled_at": "2026-10-14T08:11", "headsign": "..."} ]} ] },
    { "rank": 3, "transfers": 1, "depart_at": "...", "arrive_at": "...", "duration_min": 23, "walk_m": 59, "stops": 9, "basis": "schedule", "estimate": true,
      "disruptions": [ {"type": "closure", "route": "1", "alert_id": "12345", "span": ["TMU", "Union"], "shuttle": "Will Operate", "affects": [4], "...": "..."} ],
      "legs": [ {"mode": "walk", "to": {"number": "15460", "name": "Cherry St at Front St East"}, "distance_m": 29, "min": 0, "depart": "...", "arrive": "..."},
                {"mode": "ride", "route": "504", "direction_id": "1", "headsign": "West - 504A King towards Dundas West Station", "transit": "streetcar",
                 "pattern_id": 6, "trip_id": "109040003", "board": {"number": "15460"}, "alight": {"number": "15637", "name": "King St West at Yonge St West Side - King Station"},
                 "stops": 9, "depart": "...", "arrive": "...", "min": 12, "typical_min": 12, "basis": "schedule"},
                {"mode": "transfer", "from": {"number": "15637", "name": "..."}, "to": {"number": "13814", "name": "King Station - Southbound Platform"},
                 "distance_m": 30, "walk_min": 0, "via": "walk", "depart": "...", "arrive": "...", "min": 2},
                {"mode": "shuttle", "line": "1", "from": {"station": "14586", "name": "King"}, "to": {"station": "15772", "name": "Union"},
                 "depart": "...", "arrive": "...", "min": 9, "basis": "estimate", "alert_id": "12345",
                 "note": "assumed 5-minute headway and 15 km/h over 1.2 km of track with a 1-minute stop per station; the TTC publishes no shuttle timetable (alert 12345)"} ] } ],
  "meta": {...} }
// options are the journeys found at depart_at and at each step of window_min (every five minutes, or every ten when window_min is over 30), one per route
// sequence and board/alight stops (the earliest departure kept), with dominated journeys removed (a journey with more transfers must arrive earlier); ranked by
// arrive_at, then transfers, then walk_m, with estimate: true last. With arrive_by the head carries arrive_by instead, the options
// are the latest departures that arrive on time, the first within five minutes of the latest that does (6.10), ranked by depart_at
// descending, then transfers. transfers counts every vehicle
// change (ride and shuttle legs minus one). Every leg carries depart and arrive (ISO, minutes, Toronto offset); a transfer's min is
// the real slack between alighting and boarding (walk or pathway seconds plus the 2-minute change buffer, via "pathways" or "walk");
// a shuttle's min is the estimate of 6.10, never a timetable. basis per ride: realtime (its trip has a trip update), schedule, or
// estimate (a shuttle leg); the option's basis is its first ride's, and estimate is true when any leg is estimated. The first ride's
// next rows are the upcoming departures in its direction from depart_at (live within an hour of now, else timetable); affects lists
// 1-based leg numbers; disruptions entries are the shared element below plus affects; a route-level alert is listed only while a ride
// on its route remains in the option. An empty options list carries a note: no journey with up to max_transfers transfers departing
// in the window; every journey needs a closed section with no shuttle; or no service at the origin or destination stops that day.
// note is present only when there is something to say; the skipped-stop substitution note (decision h) stays.

// disruptions[] element (shared by stop, station, nearest_stops, route_stops, arrivals, next_trains, alerts, connections, service_status)
{ "type": "closure | suspension | detour | rsz | elevator | escalator | delay | other", "source": "alerts.ttc.ca | gtfsrt alerts | detour feed",
  "alert_id": "75803", "route": "1", "routes": ["1"], "stations": ["14588", "14587", "14586", "15772"], "station_names": ["TMU", "Dundas", "King", "Union"],
  "stops": [], "span": ["TMU", "Union"], "direction": "Both ways",
  "active_now": true, "period": ["...", "..."], "windows": [["...", "..."]], "shuttle": "Will Operate", "text": "...", "url": "..." }
// Detour-feed elements carry detour_id and entity_id instead of alert_id; RSZ elements add rsz; unit outages add unit_code.
// The copy attached to a served end station of an active closure (station, next_trains, stop, nearest_stops, alerts stop=) adds
// "role": "shuttle_terminus" (6.7 item 4); the snapshot's own element never carries it.

// next_trains: schedule fallback on an LRT platform (decision i)
{ "station": {"id": "16815", "name": "Driftwood"},
  "platforms": [ {"platform": "16317", "name": "Driftwood Station - Eastbound Platform", "line": "6", "direction_text": "Eastbound to Finch West",
                  "next_min": [], "basis": "schedule", "service": "no_prediction", "scheduled_next_min": [4, 12, 20],
                  "note": "NTAS returned no prediction for this platform; times are from the timetable"} ],
  "meta": {"source": "ntas + static stop_times", ...} }

// next_trains with propagation (a suspended platform carries no basis and no scheduled_next_min; what NTAS said stays visible as ntas_min)
{ "station": {"id": "14586", "name": "King"},
  "platforms": [ {"platform": "13814", "name": "King Station - Southbound Platform", "line": "1", "direction_text": "Southbound to Vaughan Metropolitan Centre via Union",
                  "next_min": [], "ntas_min": [1, 4, 8], "service": "suspended", "source": "ntas",
                  "note": "NTAS keeps publishing predictions inside a closure; this platform is suspended",
                  "disruption": {"type": "closure", "alert_id": "...", "span": ["TMU", "Union"], "shuttle": "Will Operate", "...": "..."}} ],
  "disruptions": [ {"type": "closure", "...": "..."} ], "meta": {"source": "ntas", ...} }
// At a span end station (Union here, if the closure ran TMU to Union with Line 1 open beyond) the platform facing the open line keeps
// service "normal" with its real next_min, and the station's disruption copy carries "role": "shuttle_terminus".

// service_changes
{ "changes": [ {"id": "f4d1a81b-...", "routes": ["101"], "route_type": "Bus", "title": "101 Downsview Park - Temporary route change ...",
                "effective": ["2026-05-02T00:00:00Z", "2026-09-30T04:00:00Z"], "text": "...", "url": "https://www.ttc.ca/..."} ], "meta": {...} }

// feed_status (decisions f, g)
{ "static": {"feed_version": "S1000538", "start": "20260906", "end": "20261031", "ckan_last_refreshed": "2026-09-04 18:03:35",
             "etag": "\"1788553243.713-...\"", "last_check": "2026-09-13T04:00:00Z", "days_remaining": 48,
             "newer_package_seen": false, "verdict": "fresh"},
  "sources": [ {"name": "bustime vehicles", "url": "...", "last_fetch": "2026-09-13T20:24:00Z", "cache": "ttl", "age_s": 12,
                "feed_timestamp": 1789331032, "signal": "feed_header", "lag_s": 5, "entities": 1275, "last_error": null, "verdict": "fresh",
                "hits": 3, "failures": 0, "retry_after": null, "status_counts": {}},   // cache: ttl | expired; retry_after set while a backoff hold lasts
               {"name": "detour feed", "last_fetch": null, "cache": null, "verdict": "unused"} ],   // never fetched this session
  "meta": {...} }
```

## 5. Data model summary (static, feed S1000538)

- `stops` 12,002 (9,512 boarding stops/platforms, 114 stations, 250 entrances, 2,126 pathway nodes); `routes` 235; `trips` 137,912; `shapes` 1,592 (1,035,699 points, `shape_dist_traveled` complete, 26 unused); `pathways` 4,086 edges (116 elevator edges); `levels` 324; patterns 1,418 (42 with more than one shape; no shape spans two patterns).
- v0.8 (S1000542, measured 2026-10-14): `departures` 4,344,069 rows over 8 service ids, 1,251,578 on a weekday; `raptor_groups` 4,123 rows (4,116 pattern and service groups plus 7 overtaking splits; 20 MB; the largest group 189 trips, the longest pattern 132 stops); 5 patterns visit a stop twice; `arrival_time` equals `departure_time` on all 7.7 M `stop_times` rows; `station_paths` covers 63 stations (its row count is recorded at the phase 6a gate).

## 6. Decisions and their evidence, per item

### 6.1 Route geometry (item 1)

- **Canonical shape (decided):** per route-direction, the shape with the most trips; other patterns' shapes on request. Basis: 1 to 41 shapes per route-direction, each shape belongs to exactly one headsign, shapes refine patterns.
- **Output format (decision a):** encoded polyline by default, GeoJSON `LineString` when `format: "geojson"`; optional `simplify_m` Douglas-Peucker. Basis: median 558 points per shape, max 3,071; the detour feed already speaks encoded polylines.
- **Stops between (decided):** by `shape_dist_traveled` from `stop_times`, never by projection.
- **BusTime `getRoutePoints.jsp` (decided):** not used; documented as a fallback pattern-geometry source for surface routes.
- Rail lines are covered (Line 1 canonical shape 633 points).

### 6.2 Accessibility (item 3)

- **Station summary (decision b):** computed from the rail platforms only: a station child served by a subway or LRT route (`kind: platform`). Bus bays and streetcar platforms under a station carry 0 and are its `surface_stops`, never platforms (counting them had read 31 of 114 stations `partially accessible`, 2026-10-11). `accessible` when every platform is 1, `not accessible` when every platform is 2, `partially accessible` when platforms flagged 1 and 2 both exist, `unknown` otherwise (no platform, or a 0 flag beside flags of one value, since 0 proves nothing). The station record's own flag (1 on all 114 stations) is never used. Basis: platforms flagged 2 today are College, Islington, King, Museum, Old Mill and Spadina's Line 1 pair; live split 103 accessible, 5 not accessible, 1 partially accessible (Spadina), 5 unknown (the former Line 3 stations).
- **Stop flags (decided):** raw `wheelchair_boarding` with GTFS meaning; 0 means unknown, never accessible (1,340 surface stops are 2, 194 are 0).
- **Trip flag (decision c):** `trips.wheelchair_accessible` is ignored. Basis: it is 2 on all 17,255 trips of the twelve streetcar routes despite a fully low-floor fleet.
- **Units (decided):** join alert unit codes to pathway nodes named `Elevator_{code}_{level}` / `Escalator_{code}_{level}` when present (95 elevator and 462 escalator coded nodes; 58 of 67 stations have coded nodes; 10 of 15 real alert codes matched today), else fall back to the alert `headerText`; output carries `node_match`.
- **Step-free verdict (decided; reworded v0.7):** `verdict` is `yes | no | unknown` with a `basis`. `yes` (`basis: "nodes"`) means every platform is flagged 1, every elevator node at the station is coded and none is out of service. `no` means at least one elevator is out of service (`basis: "alerts"`, with `basis_note` that a step-free path may remain through another elevator; the pathways-graph walk an exact path verdict needs is deferred) or a platform is flagged 2 (`basis: "platforms"`). `unknown` means a platform flag is 0 or no platform is recorded (`platforms unknown`), the station has no elevator nodes (`no elevator nodes`) or some are uncoded (`uncoded nodes`). An outage is checked first, then the platform flags, then the nodes; the verdict reads the platform flags themselves, not the summary, so a 0 flag never counts as accessible. Line 5/6 alerts use `ELV02-B` style codes that do not appear in node names.
- **Sources (decided):** `alerts.ttc.ca` accessibility list is primary; GTFS-RT `alerts/accessibility` supplies platform ids; ttc.ca duplicates are not fetched. Station ids join on `stop_id OR stop_code`.

### 6.3 Crowding (item 4)

- **Vocabulary (decided):** `EMPTY -> Not busy`, `FEW_SEATS_AVAILABLE -> Busy`, `FULL -> Very busy`, absent -> `No info`; return `label`, `gtfs`, `confidence`. Basis: the feed uses only those three values (841/206/14 of 1,253 reporting vehicles; 22 without the field; streetcars included) and ttc.ca's legend is exactly those four labels; the next 504 at Cherry St showed "Not Busy" while reporting `EMPTY`.
- **Confidence (decided):** `low` for `EMPTY` until section 8 item 1 is settled (all 25 vehicles on 504 and 155 of 162 streetcars reported `EMPTY` at 16:31), `high` for `FEW_SEATS_AVAILABLE`/`FULL`, `none` for `No info`.
- **Inline on arrivals (decision d):** every arrival row carries `crowding` via the vehicle-id join against the 90 KB vehicle feed.
- **Historical occupancy (decided):** out of scope; tool descriptions say so.

### 6.4 Detours (item 5)

- **Parsing (decided; corrected v0.7):** the host serves the detour feed as JSON and the adapter parses JSON only; no protobuf branch exists (add one only if the host switches). `gtfs-realtime-bindings` serves the protobuf feeds (vehicles, trips, GTFS-RT alerts).
- **Freshness (decided; corrected v0.7):** the HTTP `Date` is the feed's freshness signal (`http_date`, driving the staleness verdict); `meta.fetched_at` is the local fetch time. TTL 5 minutes.
- **Grouping (decided):** by `detourId`; directions listed inside; entity ids kept.
- **Reason (decision e):** joined from `serviceChanges` by route and date overlap (12 of 14 today); `reason: null` when nothing matches; never suppressed.
- **Membership on arrivals (decided, v0.3):** `selectedTrips.tripIds` from the detour feed, tested against the vehicle's trip id from `bustime.ttc.ca/gtfsrt/trips`, fetched only when the stop's route is in the detoured-route set. `modified_update`'s `modifiedTrip` link is no longer used.
- **Replacement buses (decided):** `306 Bus`/`506 Bus` entities are reported under route 306/506 with their "Replacement Bus" headsigns.
- **NEW trips (decided):** labelled by route plus first/last stop names.
- **Period boundary (decided; implemented 2026-10-11):** dual-feed loading per 2.1, trip ids resolved against every loaded feed through `StaticStore.lookup_trip`; unresolved trips reported as `pattern: "unknown"`.

### 6.5 Service changes and feed status (item 6)

- **Service changes parsing (decided):** split `route` on `|`; when blank (6 of 46), extract the leading route number from `title`; use `routeType` (Subway appears there, never in `categoryName`); ignore `isDetour` (always false); dates from `effectiveStartDate`/`effectiveEndDate`.
- **GTFS-RT `MODIFIED_SERVICE` alerts (decided; corrected v0.7):** they are the canonical service-changes list today (ttc.ca's `serviceChanges` key has been absent since October 2026); ttc.ca rows are merged in when the key reappears, matched by normalised title. Detour reasons likewise read GTFS-RT first, then ttc.ca, joined by route and date overlap.
- **Freshness signals (decided):** `feed_status` reports, per source, the best signal and its type: `feed_header` (surface feeds share one timestamp ticking every 30 s with 5 to 7 s lag; subway and alert feeds stamp on request), `http_last_modified` (`alerts.ttc.ca`), `http_date` (detour feed, NTAS), `poll_time` otherwise. In on-demand mode a source that has never been fetched this session reports `unused`; `probe: true` performs one live request per source (about 1.2 MB total, or less with 304s) to fill the table.
- **Staleness thresholds (decision f):** evaluated at answer time against the feed timestamp: `stale` past 90 s (surface real-time), 60 s (subway TripUpdates and NTAS), 10 min (alerts), 30 min (detours); static is `expiring` within 3 days of `feed_end_date` with no newer package seen and `expired` after it. A `stale_fallback` answer is always at least `stale`.

### 6.6 Fetch model (decision g, v0.3)

- **Decision:** on-demand fetching through a TTL cache with single-flight and last-known-good; no background pollers; one scheduled `HEAD` per static package per day.
- **Why:** the server is single-user and self-hosted, so the priority is the least bandwidth and the fewest requests to the TTC, not throughput. Polling at v0.2's schedule would have cost about 14,000 requests and 4 GB per day regardless of use.
- **Measured cost of on-demand answers (2026-09-13, two runs each):**

| Endpoint | Size | Fetch | Parse |
|---|---|---|---|
| BusTime per-stop XML | 1 KB | 0.06 to 0.07 s | 0.1 ms |
| NTAS (one platform) | <1 KB | 0.18 to 0.25 s | 0 ms |
| `bustime.ttc.ca/gtfsrt/vehicles` | 90 KB | 0.06 s | 0.2 ms |
| `bustime.ttc.ca/gtfsrt/trips` | 568 KB | 0.09 to 0.10 s | 1 ms |
| `gtfsrt.ttc.ca/trips/modified_update` (protobuf / JSON) | 574 KB / 6 MB | 2.97 s / 3.2 s | 2 ms / 26 ms |
| `gtfsrt.ttc.ca/trips/subway` | 12 KB | 0.05 to 0.08 s | 0.1 ms |
| `alerts.ttc.ca` list (304 on conditional GET) | 51 KB | 0.07 s (0.05 s) | 0.7 ms |
| `gtfsrt.ttc.ca/alerts/all` | 40 KB | 0.15 s | 0.1 ms |
| `gtfsrt.ttc.ca/trips/detour` | 163 KB | 0.10 s | 0.2 ms |
| ttcapi service changes | 80 KB | 0.43 s | 0.4 ms |

- **Consequences:** a typical `arrivals` call completes in 0.1 to 0.3 s; freshness improves (no poll interval on top of the feed's 5 to 7 s lag); the two things a poller gave, burst absorption and last-known-good, are recovered by the TTL cache and the stale-if-error fallback.
- **Trade-off accepted:** the first call after a quiet period pays a full fetch; concurrent users of one server instance share its cache but there is no cross-instance sharing.

### 6.7 Trip questions and disruption propagation (decision h, v0.4)

**Dry run that motivated this (2026-09-13, live data).** Origin Cherry St and Front St East (43.6527, -79.3581), destination Union.

- Nearest boarding stops: Cherry St at Front St East westbound 15460 at 29 m (504A/304/508 towards Dundas West), eastbound 15461 at 16 m, Front St E at Cherry 15510 at 52 m (121 eastbound, 202), Mill St at Cherry 15246 at 160 m (121 westbound "Towards Union Station", 202 westbound).
- Routes that reach Union from there: the 121 westbound ends at "1 Front St West - Union Station" (stop 16754), twelve stops from Mill St at Cherry, a one-seat ride; the 504A westbound reaches "King St West at Yonge St West Side - King Station" in nine stops, then one Line 1 stop or a 600 m walk. Live at the time: 121 in 15 and 24 min, 504A in 15 min.
- Union appears in the data three ways: surface stops with the suffix "- Union Station" (16754, 246, 247), "Union Station Streetcar Platform" (10935, routes 310/509/510), and Line 1 platforms 13815/13816 under station 15772, both of which are named "Northbound" because Union sits on the loop.
- A Line 1 closure is fully described in the sources. alerts.ttc.ca gives route 1, `stopStart`/`stopEnd`, the station ids in the span (for TMU to Union: 14588, 14587, 14586, 15772), `shuttleType`, `direction` "Both ways", `alertType` Planned/Unplanned, and header text. GTFS-RT gives platform ids and one entity per active window (a nightly closure appears as five entities with real start and end epochs, 23:59 to 03:30 each night, plus one umbrella entity). `effect` is inconsistent across sources (alerts.ttc.ca marks the early closure `REDUCED_SERVICE`, GTFS-RT marks it `NO_SERVICE`), so closures are classified by `effectDesc`/`headerText` and the station list, never by `effect` alone.
- Naming: the station is "TMU" (14588) with platforms "TMU Station - ...", but the four bus stops at Yonge and Dundas still say "Dundas Station". Across the feed, 106 surface stops carry a "- X Station" suffix over 51 distinct X; the ones with no station record of that name are Dundas (TMU), Bloor and Yonge (Bloor-Yonge), Sheppard (Sheppard-Yonge), "St. Clair" and "St. Andrew" (punctuation), McCowan Rt and Kennedy RT (Line 3 remnants), and a set of GO stations (Eglinton GO, Weston GO, Agincourt GO, ...) that are not TTC stations. Platform prefixes also differ from station names for Bloor-Yonge ("Bloor Station", "Yonge Station") and Kennedy ("Kennedy RT").
- Transfer graph: 418 surface stops lie within 150 m of a station record; only Vaughan Metropolitan Centre, St Clair West, Highway 407 and York University have none that close (their bus terminals are farther or below ground), so station membership by parent/suffix has to complement the distance rule. There are 1,418 stop patterns averaging 33 stops, so a one-transfer search is small.
- With the v0.3 tools, `route_stops 1`, `arrivals 15460` and `next_trains King` (an empty NTAS list) would all have let the model route a rider into a closed King station; only `alerts` or `station` would have shown the closure.

**Decisions (h, accepted 2026-09-13):**

1. **Geocoding stays outside the server.** Coordinates come from the Google Maps MCP (or any geocoder); `nearest_stops` is the bridge.
2. **`nearest_stops`** returns boarding stops and stations by straight-line distance (haversine) with `walk_min` = distance x 1.3 / 80 m per minute, default radius 500 m and 10 results, `kind` filter, and `disruptions[]` per result. Stations are returned as their station record with lines served; stops carry their served routes/directions/headsigns.
3. **`service_status`** is the first call for any trip or "what's going on" question. It is built from one alerts fetch plus the cached detour feed: closures as station spans (station ids resolved through the alias table, `active_now` computed from the GTFS-RT per-window entities when present, else the alerts.ttc.ca period), surface suspensions and detours, RSZ count, elevators out by station.
4. **Disruption propagation.** A shared `disruptions[]` element is attached to `stop`, `station`, `route_stops` (as per-stop `status`), `arrivals`, `next_trains` and `connections`. Rules (span model, corrected v0.7): a station strictly inside an active closure's span is `closed` (`service: "suspended"`, both platforms closed); a span end station is served by trains on the platform facing the open side and is the shuttle terminus (`service: "reduced"` per line, its disruption copy carrying `role: "shuttle_terminus"`, the platform facing the closed section closed); a terminal end with nothing beyond it, and a one-station closure, are closed on both platforms. The TTC's station list is the shuttle span inclusive of its ends and GTFS-RT lists every platform of every listed station, so the span rule (does a pattern of the line continue from the platform to a station outside the span?) decides, not the listing. A stop is `skipped_by_detour` when an active detour's skipped span contains it; a surface stop whose name ends in "- X Station" inherits X's closure as a disruption (so arrivals at "King St West at Yonge St - King Station" mention that King is closed); `next_trains` reports `service: "suspended"` with `next_min: []` on a suspended platform whatever NTAS says (its numbers move to `ntas_min`, no `basis`, no timetable), `no_prediction` when NTAS returns nothing and no closure is known, `normal` otherwise; `arrivals` on a suspended platform answers no rows with the same note. An empty NTAS list is never presented as "no trains".
5. **`connections`** (the centrepiece). Static search over patterns, then a live and disruption overlay. *v0.8: the search below is replaced by the timetable-exact engine of 6.10 (decision k); the endpoint sets, the live `next` rows, the disruption semantics and the annotations carry over, and the bullets marked v0.8 say what changed.*
   - Origin and destination sets: the stops within `walk_m` (default 400) of each end, or the given stop, or every platform and surface stop belonging to the given station (via `parent_station` and the alias/suffix membership), including bus stops within 150 m of the station record.
   - Direct options: every (route, direction, pattern) serving an origin stop where a destination stop appears later in the same pattern.
   - One-transfer options: for each origin pattern, each downstream stop that is a transfer point (same stop, same station, or within 150 m) to a pattern that reaches a destination stop later. Limited to patterns running on today's service id and within the route's service span at the departure time (Blue Night routes only overnight).
   - Typical minutes per leg from `pattern_run_times` (median scheduled minutes between the two stops), walking legs as above, a fixed 4-minute transfer penalty. *v0.8: exact minutes from the trip ridden, `typical_min` kept beside them; a transfer is the walk or pathway seconds plus a 2-minute change buffer (6.10).*
   - Ranking: transfers (every vehicle change, a shuttle included), then complete options before those with an unknown leg, then total typical minutes including walking (`known_min` for the latter), then stop count. Return `limit` options (default 4). *v0.8: arrival, then transfers, then walking, estimates last; the list is the Pareto set over arrival and transfers (6.10).*
   - Live overlay: `arrivals` for the first ride leg's board stop (cheap per-stop XML; NTAS for platforms); crowding inline.
   - Disruption overlay: drop ride legs that traverse a closed station on a subway line and, when the closure has `shuttleType` "Will Operate", replace them with a `shuttle` leg between the closure's end stations; the shuttle's minutes stay unknown, so the option reports `typical_min: null` with `known_min` for its other legs and a `typical_min_note`, counts the shuttle as a transfer, and ranks after complete options at equal transfers; closed spans are pruned inside the search (the `rideable` predicate), so a transfer route around a closure is found; drop legs whose board or alight stop is skipped by a detour and re-run with the nearest served stop; annotate RSZ (in the ride's direction only, matched on the zone's terminal, then the platform name) and delays without dropping. Every retained option lists the disruptions it touches, each naming the legs it `affects` (1-based). *v0.8: closed positions mask the scan itself, the shuttle is an estimated pseudo-route with minutes (`basis: estimate`), and a detour-skipped stop is unreachable by the detoured route's trips rather than by every route, since each affected route publishes its own TripModifications entity; the annotations and `affects` are unchanged (6.10).*
   - Out of scope: walking routes (straight-line only). Timetable-exact itineraries were out of scope until v0.8; decision k (6.10) brings them in.
6. **Station aliases.** `station_aliases` maps every station id to: its name; its platform name prefixes; the X of every "- X Station" surface-stop suffix within 300 m of the station; and a manual list: Dundas -> TMU, Bloor and Yonge -> Bloor-Yonge, Sheppard -> Sheppard-Yonge, "St. Clair" -> St Clair, "St. Andrew" -> St Andrew, VMC -> Vaughan Metropolitan Centre, Kennedy RT -> Kennedy. GO-station suffixes are recorded as `external: "GO"` and never resolve to a TTC station. Alert `stopStart`/`stopEnd` names are not in the table: a static build cannot see live alerts, so they resolve at answer time through `resolve_station` (station names, aliases, then a `LIKE` fallback on station names), and validation item 9 checks that none fall through. `find_stops`, `station`, `next_trains` and `connections` accept any alias.

### 6.8 Lines 5 and 6 (Eglinton and Finch West LRT): coverage audit, 2026-09-13 21:00

**Static (merged GTFS):** covered. Line 5 has 50 platforms under 25 station records, Line 6 has 36 under 18; all platforms are `wheelchair_boarding 1`; the stations carry entrances (19 and 7) and pathway nodes (121 and 71, including 28 and 12 elevator nodes). Interchanges share one station record with the subway, so the transfer graph needs nothing special: Eglinton (43274) holds Line 1 and Line 5 platforms plus nine bus routes, Cedarvale (43298) Lines 1 and 5, Kennedy (43295) Lines 2 and 5, Finch West (15205) Lines 1 and 6. Service spans are normal all-day service (Line 5 05:40 to 02:09, Line 6 04:55 to 02:06 on weekdays). Two quirks: `route_type` is 0 (tram), so mode must come from the explicit rail-line list {1, 2, 4, 5, 6} rather than from GTFS; and one Line 5 platform, `16081` "Kennedy Station - Subway Platform", has no `parent_station` (the four terminal platforms are named "- Subway Platform"; the other three do have parents). The elevator and escalator nodes at these stations use a different code scheme (`Elevator_ELVF_Street`, `Elevator_E3_Concourse`, `Escalator_ESC09_Upper`) from the subway (`12D2L`), and the one live Line 5 elevator alert today (Laird, `ELV02-B`) matches none of them, so unit joins fall back to alert text on both lines.

**Real-time:** partial, and the gap is real.
- NTAS covers Line 5 fully (every platform tested returned two or three upcoming trains) and Line 6 only in part: at 21:00 on a Sunday, with Line 6 in service until 02:06, only the Finch West terminal platform returned predictions; Albion, Driftwood, Westmore, Emery and Humber College all returned an empty `nextTrains` string. Under the propagation rules this is reported as `no_prediction`, never as "no trains".
- The subway TripUpdates feed carries only routes 1, 2 and 4 (37, 22 and 4 entities today); there are no trip updates for Lines 5 or 6.
- The surface GTFS-RT feeds carry no route 5 or 6 vehicles or trips, so there are no positions and no crowding for the LRTs; `arrivals` on an LRT platform reports crowding as `No info` and `vehicles` returns nothing for routes 5 and 6.
- The BusTime per-stop XML rejects LRT platforms ("Stop not found"), so `arrivals` for a Line 5/6 platform is NTAS-only.
- **NTAS during a closure (validated 2026-10-11, item 7; subway, but the rule is shared):** NTAS keeps publishing minutes at closed platforms. Inside the Line 2 Kipling to Jane closure it answered Old Mill eastbound (13780) `"1"` and Jane westbound `"1, 4, 8"` towards the closed section, neither an empty list nor an error. Suspended platforms are therefore reported from the closure, never from NTAS: `next_min: []`, what NTAS said in `ntas_min`, a note, no `basis`.

**Alerts:** no Line 5 or 6 route alert existed today to observe. The `gtfsrt.ttc.ca` host has no LRT alert feed (only subway, bus, streetcar, stops, accessibility), the ttc.ca route API types both lines as 400 (urban rail, same as Line 1), and alerts.ttc.ca `routeType` values seen so far are Subway, Bus, Streetcar and Elevator. Which feed and label a Line 5 closure will use is unknown until one occurs.

**Decision i (v0.5): scheduled fallback.** The static layer keeps `stop_times` queryable per stop for the current service day (a `stop_times` table indexed by stop and departure time in the same database; times past 24:00 are kept as-is and the 00:00 to 04:00 window is served from the previous service day as well). `arrivals` and `next_trains` first try the real-time source for the stop (per-stop XML or NTAS); when it returns nothing, is stale beyond the fallback window, or is unavailable, they answer from the timetable with `basis: "schedule"`, a `note`, and `meta.source` naming both. Scheduled rows never carry crowding or vehicle ids. `connections` applies the same rule to first-leg departures and states the basis per option. `feed_status` records, per rail line, how many platforms were queried in the session and how many returned predictions, so a dead NTAS line is visible. For Line 6 today this means every answer would have been timetable-based except at Finch West, and the answer says so instead of returning an empty list.

**Decisions (accepted with h):** Lines 5 and 6 are treated as rail lines everywhere: included in `service_status` closures, `next_trains`, `nearest_stops kind=station`, the station summary, and the shuttle-leg logic in `connections`; `mode` is reported as `lrt`. `connections` uses static run times for LRT legs and NTAS for the first-leg departure. The orphan platform 16081 is attached to Kennedy (43295) by a manual rule until the feed fixes it.

**Validation items:** (11) sample NTAS for all 18 Line 6 stations at several times over a week to establish whether the empty results are time-of-day, station-specific, or a feed that is not yet live; (12) when the first Line 5 or 6 alert appears, record which alerts.ttc.ca `routeType` and which `gtfsrt.ttc.ca` feed carry it and add the rule; (13) check each static rebuild for platforms without `parent_station`.

### 6.9 Transport and hosting (decision j, v0.6)

**Decision:** the server is a FastMCP application (`fastmcp>=4.1,<5` since 2026-10-11) exposing **streamable HTTP** at `/mcp`, with authentication and authorization in-process rather than through a separate proxy: `GoogleProvider` (OAuth with dynamic client registration, `require_authorization_consent="remember"`) plus the `EmailAllowlist` middleware (`ALLOWED_EMAILS=lunaparker33@gmail.com`). The gate runs in-process, not as a proxy in front of a loopback server, because the tools are native Python and there is no third-party backend to wrap. A `--transport stdio` mode remains for local development and for any developer who wants to run it on their own machine.

**Why:** a hosted connector reaches claude.ai, Claude Desktop, iOS and Claude Code through one URL; stdio would confine the server to one machine. The on-demand design (decision g) is what makes hosting cheap: an idle instance makes no requests and can scale to zero.

**Cloud shape:**

| Item | Value |
|---|---|
| Project / region | `$PROJECT_ID` ($PROJECT_NUMBER); **`us-east1`**, an always-free region |
| Service | Cloud Run `ttc-mcp`, `--allow-unauthenticated` (the gate is the app), `--min-instances 0 --max-instances 1`, `--memory 1Gi --cpu 1 --cpu-boost --timeout 300`, execution environment gen2, CPU allocated only during requests |
| URL | `https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app/mcp` (deterministic; set `BASE_URL` and the OAuth redirect URI `.../auth/callback` before the first deploy) |
| Identity | two service accounts: `ttc-mcp-sa` for the service (`roles/storage.objectViewer` on the bucket, the three secrets, Firestore) and `ttc-mcp-job-sa` for the Job's runtime and as the Scheduler's identity (`roles/storage.objectAdmin` on the bucket, `roles/run.invoker` on the Job); secrets `ttc-mcp-oauth-client-secret`, `ttc-mcp-jwt-signing-key`, `ttc-mcp-storage-fernet-key` (no backend credential: every TTC and City source is unauthenticated) |
| OAuth state | Firestore `(default)` via `FirestoreStore` with `FirestoreV1KeySanitizationStrategy` + `FirestoreV1CollectionSanitizationStrategy`, wrapped in `PrefixCollectionsWrapper(prefix="ttc")` and Fernet; the sanitization and prefix are mandatory because Claude Code's CIMD client id is a URL shared by every gateway |
| Static store | bucket `gs://ttc-mcp-static-$PROJECT_NUMBER` (us-east1): `manifest.json`, `static-<feed_version>.sqlite`; Job `ttc-mcp-static` (same image, entrypoint `ttc-mcp-build`), Cloud Scheduler `ttc-mcp-static-daily` at 04:30 America/Toronto; the Job's service account gets `roles/storage.objectAdmin` on that bucket only, the service's gets `roles/storage.objectViewer` |
| Images | Artifact Registry repo `ttc-mcp` (docker, us-east1); `gcloud builds submit` then `gcloud run deploy --image` and `gcloud run jobs deploy` from the same tag |
| Connectors | claude.ai custom connector on the `/mcp` URL (DCR, leave the advanced OAuth fields empty); Claude Code `.mcp.json` entry `{"type": "http", "url": ".../mcp"}` |
| Billing | falls under the project-wide hard cap and `billing-killswitch`; no new budget wiring; expected marginal cost near zero (below) |

**Streamable HTTP mode:** stateless, JSON responses (`stateless_http=True`, `json_response=True`): each `tools/call` is one POST and one response, no server-held session and no long-lived SSE stream. This matters twice: with `max-instances 1` every instance can answer any request without sticky sessions, and idle connected clients cannot keep the instance billing. The cache module works unchanged in one process; single-flight is per instance, which with one instance is global.

**Cold start:** container start plus manifest read plus one regional bucket download of the database (measured 2026-10-05: 104 MB per feed without shapes, built in 13 s; patterns, run times and per-stop departures as compact integer rows rather than raw `stop_times`). Expect 3 to 8 seconds on the first call after idle, then warm; `--cpu-boost` helps. If the download proves slow, the alternative is a gen2 GCS volume mount of the bucket with SQLite opened read-only (`immutable=1`), which trades cold-start time for slower first reads per page. Implemented 2026-10-11: the pull runs before the server listens (`server.main`), and a warm instance pulls again every `TTC_MCP_PULL_S` seconds (default 6 h) in a worker thread (`server.periodic`), so it follows the daily Job within hours without a restart; with a bucket the http transport never runs the CKAN check, whatever `TTC_MCP_SKIP_REFRESH` says.

**Cost (free-tier figures to re-verify at deploy time):** Cloud Run's monthly free tier (about 2 million requests, 180,000 vCPU-seconds and 360,000 GiB-seconds) covers a single-user service that bills only during requests plus a two-minute daily Job; a sub-gigabyte bucket sits inside the 5 GB always-free storage in US regions; Cloud Scheduler allows three free jobs; Artifact Registry storage for one image is within its free allowance; Firestore reads for OAuth are negligible. The only items that could bill are egress on large TTC fetches (kilobytes to hundreds of kilobytes per call) and any long-lived instance, which the stateless mode prevents.

**Threat model:** the server holds no third-party credential; the only secrets are its own gate's. Read-only public data behind the email gate is the safe class of connector. Nothing in the tools can act on the TTC or on the user's accounts.

**Local mode:** `ttc-mcp --transport stdio` uses the same code with the static check in-process (once before the server starts, when a needed rebuild blocks server start rather than tool calls and a failed check is logged while the server starts on the store it has, then every 24 h in a worker thread while running; `--no-refresh` turns both off) and the cache directory from `TTC_MCP_CACHE`; the Google gate is off in stdio mode. An external `ttc-mcp-build` is picked up on the next tool call because the store reopens when `manifest.json` changes.

**Implemented (phase 5a, 2026-10-11):** the gate is on whenever `GOOGLE_OAUTH_CLIENT_ID` is set on the http transport (`server.main` reads the gate settings only for `--transport http`; stdio never gates) and refuses to start when `GOOGLE_OAUTH_CLIENT_SECRET`, `BASE_URL`, `JWT_SIGNING_KEY` or `ALLOWED_EMAILS` is missing or the allowlist is empty (`config.gate_settings`); `EmailAllowlist` admits a verified email on the list and answers anything else with a JSON-RPC authorization error rather than a 500 (`gate.py`). OAuth state is Firestore when `GOOGLE_CLOUD_PROJECT` and `STORAGE_ENCRYPTION_KEY` are set (`FirestoreStore` with the two sanitization strategies, `PrefixCollectionsWrapper(prefix="ttc")`, `FernetEncryptionWrapper`; a project without a key refuses to start) and FastMCP's in-memory default otherwise (`gate.client_storage`). `ttc-mcp-build --bucket`, or `TTC_MCP_STATIC_BUCKET`, is the Job: on the CKAN path it first pulls the bucket's manifest and the feeds it lists into the cache directory, because a Job starts with an empty disk and the ETag comparison and the overlap pruning must see the bucket's state (a failed pull stops the Job; `--from-zip` never pulls), then checks CKAN and rebuilds on change, then pushes the feeds the manifest lists, same-size files skipped, and the manifest last (`static/bucket.push`). The report carries `pulled` (the bucket's feed versions, `[]` on a first run) beside `pushed`, so a second execution with nothing new on CKAN reports `etags unchanged` and pushes `manifest.json` only, and during a board-period overlap the manifest keeps both feeds. The source zips stay in the Job's filesystem and are not pushed. The http server with `TTC_MCP_STATIC_BUCKET` pulls before it listens (a bucket failure at boot stops the start rather than serving an empty store) and again every `TTC_MCP_PULL_S` seconds, default 6 h and under 60 s refused, in a worker thread (`server.periodic`): an addition to "pulled at cold start", so a warm instance follows the daily Job within hours. A pull downloads each feed into a `.part` file, writes the manifest after the feeds and removes `static-*.sqlite` files the new manifest no longer lists (`static/bucket.pull`); the store reopens on the next tool call. With a bucket the http transport never runs the CKAN check, whatever `TTC_MCP_SKIP_REFRESH` says; without one it keeps the startup check unless skipped, and only stdio runs the 24 h recheck (`server.main`). Each thread opens its own read-only connection per feed (`mode=ro&immutable=1`, closed when the thread exits or the store closes; `static/store.FeedDb.conn`), the `connections` planner runs in a worker thread on that thread's connection (`tools/derived_tools._plan`), and the ttc.ca service-level fetch works from inside a running loop (`static/refresh._run`). The image, its smoke and the CI job are in section 9; `docs/deploy.md` is the phase 5b runbook, and nothing is deployed as of 2026-10-11.

Environment variables the code reads (`grep -rn os.environ ttc_mcp/`; the gate variables are read through the mapping `config.gate_settings` takes, `os.environ` by default):

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

**Validation (adds to section 8; items 14 to 16 are pending phase 5b, the first deploy, and `docs/deploy.md` steps 12 to 14 carry them; nothing is deployed as of 2026-10-11):** (14) confirm every TTC host answers from Cloud Run's us-east1 egress IPs with the server's User-Agent, since none publish access rules; (15) measure cold start with the real database size and pick download versus volume mount; (16) verify the connector from claude.ai, Claude Code and iOS after the first deploy.

### 6.10 Timetable-exact connections (decision k, v0.8)

**Decision (k, accepted 2026-10-14):** `connections` plans over the timetable with RAPTOR (Delling, Pajor and Werneck's round-based public transit routing) instead of the v0.4 pattern search. Every ride is a real trip with exact board and alight times; a transfer is timed; the answer is the Pareto set over arrival time and transfers at one departure instant or across a departure window, ranked by arrival; `arrive_by` is answered through the same forward engine. Real-time trip updates for surface and subway move the trips the planner boards within an hour of now. A closure masks the scan, a running shuttle becomes an estimated pseudo-route, detoured trips run on variant patterns. The endpoint sets, the span rule, the leg annotations, the live `next` rows, the worker-thread shape and the fetch model are unchanged from decision h. The static store carries the index (built by `ttc-mcp-build`, schema 3) and the server loads it once per feed. Step-free routing (McRAPTOR or a mask), true range RAPTOR, reverse RAPTOR, NEW trips (negative ids, no static row) and walking routes are not part of this decision.

**Why.** The pattern search could not promise a connection: typical minutes, a 4-minute penalty and a 120-minute departure gate meant the model could not answer "when do I arrive", "what if I leave at 17:30" or "how do I get there by 9", and it found rides on routes with no service that day (the three stops inside High Park are served only by the seasonal 203, whose service ids are Saturday, Sunday and the Thanksgiving Monday; `validate_connections.py` had found a journey there because it ran on a Saturday). A timetable-exact planner answers those questions and strictly dominates the pattern search, and the benchmark below showed that plain Python is fast enough and the index small enough that there is no case for keeping a second engine beside it, a mode, or a twentieth tool.

**Evidence (benchmark, 2026-10-14, live S1000542, plain Python with `array('i')` columns and `bisect`, M4 Pro):**

| What | Measured |
|---|---|
| Index build from the store's rows | 2.5 s (1.6 s reading SQLite); 4,123 groups, 7 overtaking splits |
| Index size | 18.5 MB of int32 columns; 20.2 MB as a blob table in SQLite |
| Build peak RSS, holding 4.34 M rows in tuples | +590 MB (the reason the build packs trips as it streams, 2.1) |
| Serving footprint, a fresh process loading blobs into arrays | +65 MB RSS per feed, 0.10 s |
| Depart-at query, 4 rounds, 20 pairs x 4 times of day (08:00, 17:30, 23:30, 01:00) | p50 6 ms, p95 17 ms, max 30 ms |
| Same, 6 rounds | p50 6 ms, p95 19 ms |
| 60-minute window as 13 independent runs at 5-minute steps | p50 70 ms, p95 214 ms, max 344 ms |
| After-midnight queries boarding the previous service day's 25:xx trips | correct, same latency |

Facts the design rests on, from the same store: `arrival_time` equals `departure_time` on all 7.7 M `stop_times` rows, so one time per position loses nothing (the build checks it on every rebuild); 7 of 4,116 (pattern, service) groups have overtaking trips, 8 adjacent pairs, the 300 Blue Night the worst with 2; 5 patterns visit a stop twice, which positions handle; the pathways graph carries `traversal_s` on 3,193 rail edges but touches only 136 of 234 rail platforms, 63 stations fully, so pathway times exist at the interchanges and nowhere else. Endpoint resolution costs 0.2 ms a pair.

**Latency budget (agreed 2026-10-14):** 250 ms at the 95th percentile for the planner core on the live store, for a depart-at query, a window up to 60 minutes, or an arrive-by query, overlays included, measured by `scripts/bench_raptor.py` and recorded in section 11 at each phase gate; the TTC fetches for the live overlay sit outside it, as they do today. numpy or another layout is reached for only if that script says so.

**Index.** Section 2.1 describes the build: `raptor_groups` (one row per pattern, service id and overtaking split: `n_stops` int32 columns of departure seconds, trips in order, plus the trip ids) and `station_paths` (platform-to-platform seconds from a Dijkstra over the pathway nodes, fare gates excluded). The server's `Timetable` object per `FeedDb` is built lazily on the first `connections` call from those rows and kept for the feed's lifetime: group columns, `stop -> [(pattern, pos)]`, pattern stop lists and metadata, and footpaths from `transfers` and `station_members` at the walk formula (`m x 1.3 / 80` minutes over the straight-line distance between the two stops, so two platforms of one station are never a 0 m walk), overridden in seconds by `station_paths` where a pair exists. It is immutable after load, so worker threads share it without locks; two feeds in a board-period overlap mean two timetables (about 130 MB). A query at time T on day D takes two slices: D's feed with D's service ids at offset 0, and D-1's feed with D-1's service ids at the offset `wall_clock(D-1, 0) - wall_clock(D, 0)` from `static.schedule`, so the service day running past midnight and a DST change (the 2026-11-01 feed boundary) are right; labels are keyed by public stop number, not by a per-index position, so the two slices may come from different feeds.

**Engine (`domain/raptor.py`).** Inputs: origin and destination stop sets with walk seconds, a departure instant, the day slices, `max_transfers` 0 to 3 (rounds = transfers + 1), a `usable(stop)` predicate (stops that never get a label), per-pattern closed positions for rail (a scan drops its trip when it reaches one and never boards there, so a train that turns back at a served end station alights there and goes no further; `planner._closed_positions` supplies them), and the overlay below. Standard RAPTOR with marked stops: boarding by `bisect` on the position's column (the no-overtaking property the build guarantees), the 2-minute change buffer added when boarding after a ride, footpaths applied once per round, target pruning against the best arrival plus walk. For each round the best label at a destination stop is walked back into legs (walk, ride with trip id and times, transfer, shuttle); dominated rounds are dropped, so one instant yields one journey per transfer count at most. The plan runs in the worker thread (`asyncio.to_thread`) as today, and `_input_error` bounds every input before it reaches the thread.

**Query types.** `depart_at` with `window_min` (0 to 60, default 30): the query repeats every five minutes across the window, or every ten when `window_min` is over 30 (the benchmark called for the longer step on 2026-10-14); journeys are deduped on their route sequence and board/alight stops with the earliest departure kept, so the list stays route-diverse and the first ride's `next` rows carry the later departures; ranked by arrival, then transfers, then walking, estimates last; `window_min: 0` is the pure Pareto set at one instant. `arrive_by` (exclusive with `depart_at`): a binary search for the latest departure whose earliest arrival is on time (seven runs over the four hours before it, to within five minutes), then a 30-minute window stepping back from that departure every 15 minutes (the fallback agreed in advance; the five-minute search resolution was added when the benchmark of 2026-10-14 still missed the budget), same dedupe, ranked latest departure first, then transfers; about ten runs. The first option therefore leaves within five minutes of the latest departure that makes it, not always at it.

**Overlays (`ttc_mcp/overlay.py`, memoised like `advisory.py`).** The `Timetable` never changes; a per-query `Overlay` sits in front of it with patched copies of groups, extra groups with their own stop lists and an extra `stop -> [(pattern, pos)]` map, and a `(group, trip, pos)` skip mask; the scan asks it for a pattern's groups and a stop's patterns. Real-time patches are memoised on the two trip-feed fetch times (30 s TTLs); variants, shuttles and masks on the advisory snapshot key (already per minute).
- *Real-time patches.* `adapters/bustime_rt.parse_trips` keeps each trip's `stop_time_update` rows (surface stop id to public number through the crosswalk, sequence, time, `SKIPPED` / `NO_DATA`); the new `adapters/gtfsrt_trips.py` parses `gtfsrt.ttc.ca/trips/subway?format=binary` (public platform ids, routes 1, 2 and 4) into the same record, behind a `SUBWAY_TRIPS` Source (TTL 30 s, stale window 10 min, threshold 60 s, decision f). Patching a group: updated positions take the predicted time (epoch to service-day seconds through `wall_clock`), the last delay propagates to the trip's later positions, skipped positions go on the mask, then the group is re-split for overtaking so `bisect` stays valid. A ride is `basis: realtime` when its trip has an update, else `schedule`; trips with no static row are ignored. Patches apply only within `LIVE_WINDOW` (an hour of now); outside it nothing is fetched and `meta` stays static. Lines 5 and 6 have no trip updates (6.8) and stay on schedule with `basis: schedule`.
- *Detour variants.* For each resolved detour entity (`detours.resolve_entity` gives the pattern and the skipped sequences): a variant group with the skipped positions removed and the `propagatedModificationDelay` added beyond the span, holding exactly the trips in `selectedTrips`, which leave their original group. A skipped stop is therefore unreachable by the detoured route's trips, not by every route (each affected route publishes its own entity); an unresolved entity (`pattern: "unknown"`) falls back to the stop-level mask of decision h.
- *Shuttle pseudo-route.* Each active closure with a shuttle that runs (`shuttleType` "Will Operate" or "Running") becomes a headway-based pattern over its stations in line order, both directions, keyed on one rail platform of the closed line per station (the lowest platform id) so station-member footpaths connect it to the trains: wait = the full headway, hop time = the rail distance between consecutive stations (`pattern_stops.dist_m` of the line's rank-1 pattern) at the assumed bus speed plus the dwell. The leg is `mode: shuttle, basis: estimate` with the alert id and a note stating the assumptions; the option carries `estimate: true` and ranks last.

**Contract.** Section 4.4 is normative. Beyond it: `max_transfers` defaults to 3 because rounds are cheap and dominated journeys never surface; `depart_at` and `arrive_by` together are an input error; a window of 0 is allowed; the three empty-answer notes are found in order (a run with the closure mask off tells a closure from an empty timetable, as the fallback search does today, and a service-id check at the origin and destination stops tells "no service that day"); `meta.source` names the trip feeds when they were fetched.

**Constants (all in `domain/raptor.py` and `ttc_mcp/overlay.py`, each with the reason beside it):**

| Constant | Value | Why |
|---|---|---|
| `CHANGE_S` | 120 | a vehicle change is never promised on less than two minutes of slack, on top of the walk or pathway time |
| walk formula | `m x 1.3 / 80` min | decision h's pace, kept so `nearest_stops` and the planner agree |
| `WINDOW_DEFAULT_MIN`, `WINDOW_MAX_MIN`, `WINDOW_STEP_MIN` | 30, 60, 5 | fills `limit` with route-diverse journeys; the 5-minute step holds up to 30 minutes, while 13 runs over 60 minutes measured p95 273 ms on the live store (2026-10-14), over the budget |
| `WINDOW_LONG_STEP_MIN`, `ARRIVE_BY_STEP_MIN` | 10, 15 | the whole window steps every ten minutes when `window_min` is over 30, and arrive-by's window every 15: at 5-minute steps the live store measured p95 273 ms for a 60-minute window and 322 ms for arrive-by (2026-10-14) |
| `ARRIVE_BY_SEARCH_H`, `ARRIVE_BY_RESOLUTION_S` | 4, 300 | no TTC journey takes four hours; six halvings reach five minutes (eight reached the minute and kept arrive-by over the 250 ms budget, 2026-10-14) |
| `SHUTTLE_HEADWAY_MIN` | 5 | a closure shuttle fleet runs frequently; the full headway is charged, not half |
| `SHUTTLE_KMH`, `SHUTTLE_DWELL_MIN` | 15, 1 | surface buses on a subway corridor in traffic, stopping at every closed station; validation item 19 checks it against a real shuttle |
| `LIVE_WINDOW` | 60 min | decision h's window for live first-leg rows, now also for trip-update patches |
| `MAX_TRANSFERS` | 3 | four rides is the most a Toronto journey needs; the benchmark showed six rounds cost no more than four |

**Consequences.** Cloud Run memory stays at 1 GiB (worst case in an overlap: `/data` 2 x 136 MB, two timetables 2 x 65 MB, the process about 150 MB); the build packs trips as it streams, so the Job's peak grows by about 60 MB over the schema-2 build (622 MB against 562 MB on the live feed, 2026-10-14) and the runbook's 2 GiB fallback stands for a Job that was already close to 1 GiB; the bucket grows 20 MB a store and a cold-start pull by 0.1 to 0.2 s; the marginal cloud cost is nil inside Cloud Run's free tier (a month of this single-user service plus the daily Job would cost about $0.10 with no free tier at all). `SCHEMA_VERSION` becomes 3 and the existing version check skips and names an old store. `known_min`, `typical_min_note` and `first_departure` leave the contract; the `rideable` predicate becomes the closed-position list; `validate_connections.py` and the derived smoke move to the new contract. Effort in section 10.

**Validation items** (added to section 8): 17 the benchmark at every phase 6 gate; 18 the comparison set (today's answers captured before the switch, the Google Maps transit directions after); 19 the first real closure shuttle, its estimated minutes against what the TTC's buses did; 20 the overtaking split count and the arrival-equals-departure check on every rebuild.

## 7. Decision record

| # | Decision | Accepted behaviour (2026-09-13) |
|---|---|---|
| a | `route_shape` format | encoded polyline default; `format: geojson` and `simplify_m` optional |
| b | "Accessible station" | derived from platforms/entrances: accessible / partially accessible / not accessible / unknown |
| c | `trips.wheelchair_accessible` | ignored |
| d | Crowding on `arrivals` | inline per row: TTC label + GTFS enum + confidence |
| e | Detours without a matching reason | shown with `reason: null` |
| f | Staleness thresholds | 90 s / 60 s / 10 min / 30 min real-time; 3-day expiring window for static |
| g | Fetch model | on demand with TTL cache, single-flight, last-known-good; static checked once a day; no pollers |
| h | Trip questions and disruptions (incl. Lines 5/6 as rail, section 6.8) | `nearest_stops` bridge from an external geocoder; `connections` (direct + one transfer, live and disruption overlay, shuttle legs); `service_status`; disruptions propagated into every answer; station alias table; geocoding out of scope |
| i | Real-time gaps (Lines 5/6, outages) | scheduled departures from `stop_times` as the fallback; every arrivals-style row carries `basis` realtime/schedule; `vehicles` declares rail unavailable; `feed_status` reports NTAS coverage per line |
| j | Transport and hosting | FastMCP 4 streamable HTTP (stateless JSON) with in-process Google OAuth gate + email allowlist; Cloud Run `ttc-mcp` in us-east1, min 0 / max 1; static store built by a daily Cloud Run Job into a GCS bucket and pulled at cold start and every 6 h; stdio mode for local use |
| k | Timetable-exact connections (2026-10-14, section 6.10) | RAPTOR over the trips replaces the pattern search inside `connections`: exact times per leg, Pareto set over arrival and transfers ranked by arrival, `window_min` and `arrive_by` through one forward engine; surface and subway trip updates patch the trips within an hour of now; closures mask the scan, a running shuttle is an estimated pseudo-route, detoured trips run on variant patterns; the index is built into the store (schema 3) and loaded once per feed; 250 ms p95 budget; step-free, range and reverse RAPTOR deferred |

## 8. To validate during implementation

1. **`EMPTY` as default.** Sample the vehicle feed at a weekday peak (08:00 to 09:00): if buses spread across the three values while streetcars stay `EMPTY`, downgrade streetcar `EMPTY` to `No info`.
2. **Board-period transition.** Implementable now (dual-feed lookup, 2.1); first change due about 2026-10-31. Watch it: confirm the dual-feed window resolves every live trip id and every detour trip id.
3. **Elevator node coverage.** Implemented 2026-10-11 (`static/checks.py`): every rebuild logs `[check] unit codes in alerts matching a coded node: m of n` with the unmatched codes and stations, and the build report carries `checks.unit_codes`. On 2026-10-11: 10 of 18 (unmatched: Kennedy 16D2E and ELV01-A, Summerhill 15Y1L, Eglinton ELV02-B, Bloor-Yonge 13Y1L, Don Mills 61P3E, Kipling 16B1L, Sheppard-Yonge 21Y8E; the `ELV` codes are Line 5's). If the share keeps falling, widen the fallback text parsing.
4. **Detour feed behaviour on change.** Capture the feed before and after a detour ends to confirm entities disappear rather than linger with past `serviceDates`.
5. **`modified_update` generation time.** Re-measure monthly; if it drops under 0.5 s it becomes a candidate arrivals source again (public numbers, no crosswalk).
6. **TTL tuning.** Log cache hit rates per source for the first weeks; if bursts routinely exceed a TTL (many `live` fetches within one tick), lengthen that TTL toward the feed's true interval, never shorter.
7. **NTAS during a closure.** Validated 2026-10-11, result in 6.8: NTAS keeps publishing minutes at closed platforms during a closure (Old Mill eastbound "1", Jane westbound "1, 4, 8" inside the Kipling to Jane closure), so suspended platforms are reported from the closure, never from NTAS. (The original plan was to capture College and Bloor-Yonge during the September College to St Clair closure.)
8. **Connections quality.** 19 of 20 pairs under 150 min on 2026-10-11 (`scripts/validate_connections.py`; the one miss had no stop within 400 m of its origin); the TTC planner comparison of each top option is pending. Then tune `walk_m`, the 150 m transfer radius (the four stations without nearby surface stops need the membership rule), and the transfer penalty.
9. **Alias completeness.** Implemented 2026-10-11 (`static/checks.py`): every rebuild logs `[check] surface-stop suffixes with no station` (suffixes with no station within 300 m, GO suffixes excluded) and `[check] alert stations unresolved` (station ids on station alerts and station names on rail alerts that resolve to nothing); `ttc-mcp-build --no-checks` skips them and a failed alerts fetch skips the live two. On 2026-10-11: 3 suffix stops, all external stations without a GO suffix (Guildwood, Viscount at Pearson), and 0 unresolved alert stations. Add manual aliases as needed and keep GO stations external.
10. **Rate behaviour.** None of the undocumented hosts publish limits. Backoff implemented 2026-10-11 (2.2): an exponential hold per failing source, 403 and 429 counted per source in `feed_status`; single-flight already prevents self-inflicted bursts. Still to watch: whether any host ever answers 403 or 429 to the project User-Agent.
17. **Planner latency (v0.8).** `scripts/bench_raptor.py` at each phase 6 gate on the live store: p95 under 250 ms in every mode (instant, 30- and 60-minute windows, arrive-by), the figures and RSS recorded in section 11. The 2026-10-14 design benchmark is the baseline (6.10).
18. **Connections comparison (v0.8, succeeds item 8's pending half).** Today's answers for the 20 pairs captured at a pinned `TTC_MCP_NOW` before the engine switch and compared with the new `duration_min` after it; then each pair's top journey against Google Maps transit directions at the same departure time (one Directions call a pair through the Maps MCP connector), differences explained or fixed.
19. **Shuttle estimate.** At the first real closure with a running shuttle, compare the estimated shuttle minutes (5-minute headway, 15 km/h, 1-minute dwell) with the buses' actual times on the per-stop XML, and retune the constants if they are off by more than a third.
20. **Index self-checks.** Every rebuild logs `[check] patterns split for overtaking: n` and `[check] stop_times with arrival != departure: n`; a non-zero second count means one time per position no longer suffices and the index needs an arrival column.

## 9. Implementation notes

- **Adapters:** one module per source (`ckan`, `gtfs_static`, `bustime_rt`, `bustime_web`, `gtfsrt_ttc`, `ntas`, `ttc_alerts`, `ttcapi`), each returning typed records plus a `SourceStatus`. Tools compose adapters through the cache and never touch HTTP directly.
- **Cache module:** `Entry {key, parsed, fetched_at, signal, signal_ts, etag, last_modified, last_attempt, last_error, hits, failures, retry_after, status_counts}`; `get(source, key, fetch, parse)` implements TTL, single-flight (one in-flight future per key), conditional GET when a validator exists, stale-if-error with the age reported back to the tool, and the backoff hold of 2.2. Memory footprint is small: the largest cached body is the 568 KB trip feed.
- **Connections planner (v0.8, decision k; the v0.4 note it replaces is in `docs/spec-history/spec-v0.7.md`):** `static/timetable.py` packs the groups and runs the station Dijkstra at build time; `domain/raptor.py` loads the `Timetable` per feed and holds the scan, journey extraction and the window and arrive-by drivers; `ttc_mcp/overlay.py` builds and memoises the per-query overlay (real-time patches, detour variants, shuttle pseudo-routes, masks); `domain/planner.py` keeps `resolve_endpoint`, the closure helpers (`_closed_positions`), the RSZ and detour annotations and `_touch`; `tools/derived_tools.connections` maps the call, bounds the inputs, runs the plan in the worker thread and attaches `next` rows and `meta`. Expected cost: single-digit milliseconds a run, tens for a window, plus the fetches the live overlay makes.
- **Static build:** streaming, single pass, ~2 minutes, in the Cloud Run Job; write to a temporary database, upload, then rewrite the manifest; the manifest keeps the previous database listed for the overlap window; a service that starts with no manifest yet answers static tools with an explicit "static store not built yet" error rather than blocking.
- **Entry points:** `ttc-mcp` (server; `--transport http|stdio`; the default comes from `TTC_MCP_TRANSPORT` and is stdio locally, http on `$PORT` in the container, whose image sets `TTC_MCP_TRANSPORT=http`) and `ttc-mcp-build` (the Job; `--bucket`, or `TTC_MCP_STATIC_BUCKET`, pulls the bucket's store before the CKAN check and pushes the build after). `pyproject` requires 3.13+, `.python-version` is 3.14.
- **Container (phase 5a, 2026-10-11):** the Dockerfile exists: `ghcr.io/astral-sh/uv:python3.14-bookworm-slim` pinned by digest (the `docker` entry in `.github/dependabot.yml` watches it), `uv sync --frozen --no-dev --extra cloud` in two layers (the dependencies, then the project as a non-editable wheel reinstalled on every build because uv's cache keys a local wheel on `pyproject.toml`, not the code), two `ENV` lines, the first for uv and Python (`UV_COMPILE_BYTECODE`, `UV_LINK_MODE`, `UV_NO_SYNC=1`, `PYTHONUNBUFFERED=1`) and the second `ENV TTC_MCP_TRANSPORT=http TTC_MCP_SKIP_REFRESH=1 TTC_MCP_CACHE=/data PORT=8080`, and `CMD /app/.venv/bin/ttc-mcp`, so a fresh container never rebuilds the project at start. The Job runs `/app/.venv/bin/ttc-mcp-build --bucket ...` from the same image, never `python -m` from `/app`, where the uncompiled source copy shadows the wheel. One image, two commands. `scripts/smoke_image.sh` (CI's `image` job) builds the image, checks `sqlite3.threadsafety` is 3, builds the fixture store inside a container, serves it with `TTC_MCP_NOW` pinned inside the fixture window and asserts the 19 tools exactly and a `routes` answer from `/data`.
- **Parity tests (nightly, live):** arrivals at 15461 from the per-stop XML vs crosswalked `bustime` protobuf vs `modified_update` (same vehicles and minutes within 1 min); NTAS vs `trips/subway` for 13863; detour skipped stops vs live omitted stops on a linked trip; Blue Night classifier still flags 35 of 35.
- **Licensing (implemented 2026-10-11, corrected 2026-10-12):** the code is MIT (`LICENSE.md`; `license = "MIT"` in `pyproject.toml`, the file shipped in the wheel and the image). The two GTFS packages and the two GTFS-RT hosts (`bustime.ttc.ca/gtfsrt`, `gtfsrt.ttc.ca`) are TTC datasets on the City's portal, whose dataset pages give the Open Government Licence - Toronto; the stop-prediction JSP, NTAS, `alerts.ttc.ca` and `www.ttc.ca/ttcapi` sit behind the TTC's website, which publishes no terms specific to them (its website terms of use cover its web pages: personal, non-commercial viewing and copying with notices kept, modification and substantial reuse reserved, the source acknowledged; they don't say whether they reach these endpoints). The `vehicles` description names its source as the BusTime GTFS-Realtime feed. The server instructions (`INSTRUCTIONS`, the MCP `instructions` text) end with the attribution, the licence link, that split, and "Real-time and advisory answers are best effort." The README's "Data sources and licences" is the user-facing account. The descriptions of `arrivals`, `next_trains`, `vehicles`, `alerts`, `service_status`, `detours`, `service_changes` and `connections` carry a best-effort clause; `arrivals` and `vehicles` say historical occupancy is out of scope.

## 10. Effort

Core server: ~3 days solo / ~1.5 days with Claude; the on-demand model removes the poller supervision but adds the cache module, so the total is unchanged. Cloud deployment following the existing gateway recipe (OAuth client, secrets, service account, bucket, Job, Scheduler, first deploy, connector re-adds): ~4 h solo / ~2 h with Claude, most of it console clicks the recipe already documents. Additions: item 1 ~2 h / ~1 h; item 3 ~1 h / ~30 min (plus ~1.5 days / ~half a day for an exact step-free verdict beyond coded-node cases); item 4 ~1 h / ~30 min; item 5 ~3 to 4 h / ~1.5 h; item 6 ~2 h / ~1 h. Five additions without step-free routing: about **1.5 days solo, ~5 hours with Claude**. v0.4 additions: `connections` ~1 day / ~3 to 4 h; `nearest_stops` ~1 h / ~30 min; `service_status` plus propagation ~3 h / ~1.5 h; alias table ~1.5 h / ~45 min. v0.5: `scheduled_departures` and the schedule basis ~2 h / ~1 h (the `stop_times` table already exists in the static build). About **2 days solo, ~7 to 8 hours with Claude** on top of the earlier items. v0.8 (phase 6, decision k): the index and schema bump ~1 day / ~2 h; the engine, drivers and contract ~1.5 days / ~4 h; the overlays (two adapters, patches, variants, the shuttle) ~1.5 days / ~5 h; verification, the comparison and the docs ~half a day / ~2 h: about **4 to 5 days solo, ~13 hours with Claude**.

## 11. Verification log

### 2026-09-13 (source survey)

- Geometry: 137,912/137,912 trips with shapes; 1,592 shapes; 1,418 patterns; 42 multi-shape patterns; BusTime 504 patterns 4 with 36 to 39 embedded stops.
- Accessibility: station flag 1 on 114/114; platforms flagged 2 include Old Mill, Islington, College, King, Museum; 95/462/755 coded elevator/escalator/stair nodes; 10 of 15 alert codes matched; 63/63 alert station ids resolved via `stop_id OR stop_code`; streetcar trips 17,255 flagged 2.
- Crowding: 1,253/1,275 vehicles with status (841 EMPTY, 206 FEW_SEATS_AVAILABLE, 14 FULL); ttc.ca legend captured live; vehicle 4502 `EMPTY` while its arrival showed "Not Busy".
- Detours: 93 modification entities, 93 shapes, 14 detours; 2,523/2,523 selected trips in the static feed; 39/39 live links resolved; polylines validated against kept/skipped stops.
- Service changes: 46 records; `route` pipe-separated on multi-route entries, blank on 6; 34/40 alert-title matches.
- Freshness: surface timestamps advanced 30 s between snapshots 28 s apart with 5 to 7 s lag; alerts API `max-age=25`; CKAN `Age: 447`.
- Fetch costs: table in 6.6; `alerts.ttc.ca` conditional GET returned 304 in 0.05 s.
- User-Agent: 403 from `alerts`, `ntas`, `www`, `gtfsrt` hosts for `Python-urllib`; 200 for every custom string.
- Trip dry run: nearest stops to Cherry St and Front St East as listed in 6.7; 121 westbound ends at stop 16754 "1 Front St West - Union Station"; live arrivals 121 in 15/24 min, 504A in 15 min; closure alert shape and per-night GTFS-RT entities as described; 106 suffix stops over 51 names; 418 surface stops within 150 m of a station, 4 stations with none; 1,418 patterns averaging 33 stops.

### 2026-10-05 (phase 1 to 3 gates)

- Static build, live: 13 s per feed, 104 MB SQLite per feed before shapes (schema 1), both zips downloaded in about 3 s; two-feed manifest (S1000538 and S1000542, the Sept 30 revision winning for 2026-10-05).
- Phase gates: unit suite green; `smoke_static.py` (nine checks), `smoke_realtime.py` (eight) and `smoke_advisories.py` (ten) PASS against the live store and the TTC; the live integration suite over streamable HTTP green, including the cross-phase closure invariant; Driftwood (Line 6) answering `no_prediction` with timetable minutes, as decision i expects.
- Station closures made line-aware: a Line 1 closure at Eglinton leaves the Line 5 platform open.

### 2026-10-06 (phase 4 gate)

- Both live stores rebuilt to schema 2: 116 MB each (S1000538 116,088,832 bytes, S1000542 116,252,672), under the 200 MB target; 235 of 235 route service levels fetched from ttc.ca at build time.
- `smoke_derived.py` (eleven checks) PASS: 504B polyline with 36 stops and distances, simplified Line 1 GeoJSON with fewer points, Queen's coded units (`9Y1L`) with a verdict, St George's live elevator alert with `node_match: false`, service levels on 504 and 300, the Cherry and Front to Union options with live `next` rows (the seasonal 202 legitimately outranks the direct 121 in the evening); the integration suite gained the derived checks and stayed green; `validate_connections.py` added for item 8.

### 2026-10-11 (spec-correctness review, live, with the Line 2 Kipling to Jane closure active)

- Suites: 296 unit tests at `cf6ac69`, 334 after the review's fixes; 20 live integration tests, no skips; all four smoke scripts PASS; `validate_connections.py` 19 of 20 pairs under 150 min (one origin had no stop within 400 m), TTC planner comparison pending.
- Store probe (S1000542, schema 2): 12,001 stops (9,511 boarding, 114 stations, 250 entrances, 2,126 nodes), 235 routes, 137,910 trips, 1,416 patterns, 1,590 shapes with 1,033,606 points, 4,083 pathways, 324 levels, 95 coded elevator nodes; zero orphan platforms (16081 attached to Kennedy 43295); 42 rows with `stop_id != stop_code` (34 stations, 8 boarding stops).
- Closure end stations: alert 77814 lists Kipling, Islington, Royal York, Old Mill and Jane; GTFS-RT 77814 lists both Jane platforms; raw NTAS predicted an eastbound train at Jane (13777) and Runnymede was `normal`, so the TTC's list is the shuttle span inclusive of its ends. After the fix: Kipling (terminal end) `suspended` on both platforms; Royal York and Old Mill `suspended`; Jane `reduced` with `role: shuttle_terminus`, eastbound 13777 `normal` (`next_min [2]`), westbound 13778 `suspended` (`ntas_min [0, 4, 5]`).
- NTAS ghost predictions inside the closure (item 7): Old Mill eastbound 13780 `"1"`, Jane westbound `"1, 4, 8"` towards the closed Kipling.
- `connections Kipling -> Jane` after the fix: the 19-minute bus first (0 transfers, complete), the shuttle-only option second with `typical_min: null`, `known_min 0`, `affects [1]`; `Finch -> Union` lists only the southbound reduced speed zones.
- Live fetch layer: arrivals 246 ms first call, 2 ms from TTL; surface feeds `feed_header` lag 7 s; `alerts.ttc.ca` answers with `http_last_modified`; `connections` 6 to 212 ms per call; ttc.ca `serviceChanges` empty while GTFS-RT carries 23 `MODIFIED_SERVICE` entities; `detours` resolves 60 of 60 entities.
- Follow-up the same evening: the merged feed lists bus bays and streetcar platforms as station children with `wheelchair` 0; counted as platforms they had read 31 of 114 stations `partially accessible` with `step_free` `platforms unknown` (Eglinton, Finch and York Mills among them). With platforms limited to rail-served children the live split is 103 accessible, 5 not accessible (College, Islington, King, Museum, Old Mill), 1 partially accessible (Spadina) and 5 unknown (the former Line 3 stations).
- Build-time checks (8 items 3 and 9) first run on the live store: suffixes with no station 3 (130 Westlake Rd - Guildwood Station; Toronto Pearson Airport - Viscount Station, twice), alert stations unresolved 0 of 41 alerts, unit codes matching a coded node 10 of 18.
- FastMCP 3.4.8 to 4.1.0 and pydantic 2.13.5 to 2.14.0 (Dependabot PR #5, merged after phase 5a): the unit suite (388, gate tests included), the live suite (20), the image smoke and the four CI jobs passed without a code change. FastMCP 4 is built on MCP SDK v2 and the 2026-07-28 protocol, whose modern requests are sessionless (any replica can answer them, which fits the stateless single-instance shape); older clients negotiate the previous protocol per connection. Sampling and roots are gone from the server API; this server never used them.

### 2026-10-11 (phase 5a: the container, local)

- Image: `ghcr.io/astral-sh/uv:python3.14-bookworm-slim` pinned at `sha256:7cf77f59...`, which `docker buildx imagetools inspect` shows is the multi-arch index (linux/amd64 and linux/arm64); CPython 3.14.2, SQLite 3.40.1 with `sqlite3.threadsafety` 3 (serialized); 471 MB on disk, 108 MB content; the project layer rebuilds in about 0.6 s, a build without the layer cache in 6.3 s.
- `scripts/smoke_image.sh`: the fixture store built inside a container (`S1000538`, 4,258 trips, 2,536 stops, 5.3 MB), the server pinned to `TTC_MCP_NOW=2026-10-06T08:00`, the 19 tools exactly, `routes` serving 6 routes from `/data`; 2.8 s with cached layers, 7.7 s with the project layers rebuilt. It fails closed: an empty `/data` fails on the `routes` error, a half-configured gate on "exited before it answered", a clock outside the fixture window on `routes`.
- Found by the smoke: with `--no-editable` and the uv cache mount, uv reinstalled a project wheel keyed on `pyproject.toml` rather than the code, so an image could ship stale code without an error; `--reinstall-package ttc-mcp` on the project sync fixed it.
- Unit suite: 386 tests (three of them for the Job's bucket pull), offline (three `test_server.py` tests had reached alerts.ttc.ca and gtfsrt.ttc.ca through a fixture with a real client; `tests/unit/conftest.py` now fails any non-loopback request, naming the host). CI jobs: unit (with the cloud extra), lint, build, image.
- Nothing deployed; validation items 14 to 16 wait for phase 5b (`docs/deploy.md`).

### 2026-10-12 (licences and the README, live)

- Licences: the City's CKAN API lists all four TTC datasets this server reads (`merged-gtfs-ttc-routes-and-schedules`, `surface-routes-and-schedules-for-bustime`, `ttc-bustime-real-time-next-vehicle-arrival-nvas` for `bustime.ttc.ca/gtfsrt`, and `ttc-gtfs-realtime-gtfs-rt` for `gtfsrt.ttc.ca`) with `license_id: notspecified`, while their dataset pages on open.toronto.ca give "Open Government Licence - Toronto" linked to `https://open.toronto.ca/open-data-licence/` (OGL-Toronto 1.0; attribution "Contains information licensed under the Open Government Licence – Toronto", a link where possible, non-endorsement, no rights to names, logos or trademarks). Transitland's feed registry records the TTC's GTFS and BusTime GTFS-RT feeds under the same licence. No portal dataset covers NTAS, `alerts.ttc.ca`, `www.ttc.ca/ttcapi` or the BusTime stop-prediction JSP; the TTC's "Web Site Terms and Conditions of Use" allow viewing and copying for personal, non-commercial use and reserve republication, without saying whether they reach those endpoints. The instructions' earlier "none of which publish terms" was wrong for the two GTFS-RT hosts and is corrected.
- With the City's portal unreachable (a dead proxy), `ttc-mcp --transport stdio` exits with code 1 in under a second (`httpx.ConnectError` from the startup `ensure_static`), although a usable store is on disk; `--no-refresh` or `TTC_MCP_SKIP_REFRESH=1` starts it. The 24-hour recheck and the Job already survive the same failure. Fixed the same day: the startup check logs the failure and the server starts on the store on disk (through the same dead proxy it connected in 0.4 s and `find_stops` answered from S1000542), a server with no store starts and answers "not built yet", and the CKAN client's connect timeout is 10 s (reads keep 120 s) so a black-holed portal can't stall a start.
- The README's Docker flow, run as written against the real portal: the store built inside the container in 40 s including the download (S1000542, 116.3 MB), served on `127.0.0.1`, and `next_trains` answered live.
- Line 2 closed St George to Broadview 06:00 to 15:00 (alert 77820, shuttle "Running"): at 13:24 St George eastbound read `suspended` with NTAS still predicting 2, 6 and 13 minutes (`ntas_min`), westbound kept live minutes, and `connections` Spadina to Pape answered ride, shuttle, ride with `typical_min: null`, `known_min` 4 and 2 transfers. The README uses this answer as its example.

### 2026-10-14 (phase 6 design: the RAPTOR benchmark, local)

- Store facts (S1000542): 4,344,069 `departures` rows over 8 service ids, 1,251,578 on a weekday; `arrival_time` equals `departure_time` on all 7.7 M `stop_times` rows of the merged feed; 7 of 4,116 (pattern, service) groups have overtaking trips (8 adjacent pairs; the 300 Blue Night 2, then one each on 414, 300, 97, 51, 45 and 16); 5 patterns visit a stop twice; 3,193 rail pathway edges carry `traversal_s`, 136 of 234 rail platforms touch an edge, 63 stations have every rail platform connected; Wednesday service ids are 1, 4401 and 7001.
- Benchmark (throwaway script, plain Python, `array('i')` columns and `bisect`, M4 Pro): index built from the store in 2.5 s (1.6 s reading SQLite), 4,123 groups after 7 splits, 18.5 MB of columns, 20.2 MB as a blob table; build peak +590 MB RSS when every row is held in tuples; a fresh process loading the blobs: +65 MB RSS in 0.10 s. Queries over the 20 `validate_connections.py` pairs at 08:00, 17:30, 23:30 and 01:00: p50 6 ms, p95 17 ms, max 30 ms at 4 rounds; p50 6 ms, p95 19 ms at 6 rounds; a 60-minute window as 13 runs p50 70 ms, p95 214 ms, max 344 ms; 18 of 20 pairs found at every time, the two misses being the origin with no stop within 400 m and the High Park origin whose only route (the 203) runs on weekends and the Thanksgiving Monday. Sanity: Finch to Union 32 min on one Line 1 trip; Kipling to Kennedy 54 min; Vaughan Metropolitan Centre to Bloor-Yonge 40 min via St George; the 01:00 queries boarded the previous service day's 25:xx trips.
- Decided from it: plain Python, arrays never Python ints, the index built by `ttc-mcp-build` into the store and loaded once per feed, a 250 ms p95 budget, Cloud Run memory unchanged at 1 GiB (worst case about 550 MB in a board-period overlap); the marginal cloud cost is nil inside the free tier. Cloud Run list prices read the same day: $0.000018 a vCPU-second and $0.000002 a GiB-second after the August 2026 refresh, with 180,000 vCPU-seconds and 360,000 GiB-seconds free a month.

### 2026-10-14 (phase 6a gate: the index in the live store, local)

- Build of S1000542 from the cached zips on schema 3 (`ttc-mcp-build --from-zip`, no ttc.ca): 18 s and a 622 MB peak, against 17 s and 562 MB for the schema-2 build of the same zips on `main` measured the same way; the index costs about 60 MB of build memory, which is the streaming pack working. `[check] patterns split for overtaking: 7`, `[check] stop_times with arrival != departure: 0`; `raptor_groups` 4,123 rows with 17.4 MB of columns; `station_paths` 2,900 rows (66 stations with two or more platforms on the fixture, 63 live stations with every rail platform on the graph); the file is 139.0 MB against 116.3 MB.
- The server loads the timetable in 0.09 s and 66 MB (4,123 groups, footpaths from 9,178 stops). Footpaths measure a member pair as the straight-line distance between the two stops (the recorded 0 m of a platform to its station record had produced 0 m walks between platforms); pathway seconds override the walk formula at 2,002 of the fixture's 3,460 platform pairs (Bloor-Yonge 13863 to 13864: 56 s over 119 m); a pair the feed places at one point (Kennedy's Line 5 platforms, Vaughan Metropolitan Centre, Highway 407, Mount Dennis) stays a 0 m walk, the 2-minute change buffer being the cost of the change.
- Today's `connections` answers for the 20 validation pairs captured with the pattern search at `TTC_MCP_NOW=2026-10-14T08:00` (`docs/superpowers/plans/2026-10-14-phase-6-before.json`): 18 of 20 with an option, the two misses being the origin with no stop within 400 m and the High Park origin, whose 203 does not run on a Wednesday but which the pattern search still gates only on a departure within 120 minutes. Unit suite 410 passed; the maintainer's own store stays on schema 2 until the next `ttc-mcp-build`.

### 2026-10-14 (phase 6b gate: the engine on the live store, local)

- `scripts/bench_raptor.py` on S1000542 (schema 3), 19 resolvable pairs at 08:00, 17:30, 23:30 and 01:00 on the next day's timetable (Thursday 2026-10-15 and the night after), p95 by the lower-rank method over 76 queries, wall clock 22:51 Toronto: instant p50 5.9 / p95 27.6 / max 142.9 ms; window 30 (seven runs) p50 30.5 / p95 126.7 / max 290.0 ms; window 60 (seven runs at the 10-minute step) p50 30.6 / p95 154.1 / max 250.7 ms; arrive-by (seven search runs to five minutes, three step-back runs) p50 55.7 / p95 209.3 / max 384.1 ms; RSS 110 to 185 MB; PASS at the 250 ms p95 budget, the same on a clock pinned to 05:00. The High Park pair answers `no service` on a weekday, counted and not a failure.
- What it took, in order: the first run of the committed script failed in three modes (instant p50 19.5 ms, window 60 p95 522, arrive-by 635); reading closures from the timetable and querying positions only for a closable line, skipping groups whose last trip is gone before the position loop and hoisting the loop's locals brought instant to the design spike's 6 ms (p95 31) with every answer byte-identical over a 912-query grid; pruning spent patterns from each run's queue, the 10-minute step for windows over 30 minutes and the 15-minute arrive-by step-back took window 60 to 143 and arrive-by to 274; the five-minute arrive-by resolution took arrive-by to 207. Instant and 30-minute-window answers never changed; a 60-minute window or an arrive-by lists fewer later route sequences than at the finer steps, and an arrive-by's first option departs within five minutes of the latest that makes it (59 of 216 grid answers lead with a train one to four minutes earlier than before).
- The 20 pairs against the pattern search's capture of 08:00 on 2026-10-14: every pair with an option still has one (`lost 0`), door-to-door minutes within two to ten of the typical minutes (the difference is real waiting and the two-minute change buffer: Finch to Union 30 against 28, Vaughan Metropolitan Centre to Bloor-Yonge 43 against 50, Dundas West 32 against 22), Cherry and Front to Union now the 503 and Line 1 in 19 minutes, the High Park pair `no service at the origin stops on 2026-10-14` instead of a journey the 203 would not run. Unit suite 401 passed.
- Design corrections the build forced, recorded in the plan's ledger as rulings: footpaths relax one hop from a sorted snapshot of each round's ride arrivals (the first engine was set-order dependent across processes); each timetable slice reads its own feed's connection (one connection for both slices misreads pattern ids in a board-period overlap); the engine's `next` rows and their liveness are taken at each option's own departure; a window never caps departures (a 02:00 question answers the 06:23 bus); minutes printed on a leg are the difference of the printed clocks, walk minutes the walk formula; arrive-by is floored at now.
