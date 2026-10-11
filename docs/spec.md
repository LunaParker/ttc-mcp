# TTC network MCP server: specification v0.7

**Date:** 2026-10-11 | **Status:** decisions a to j accepted; phases 1 to 4 implemented and corrected against the code after the 2026-10-11 spec-correctness review | **Supersedes:** v0.6 (history in `docs/spec-history/`)
**Companions (in this repository):** `docs/investigation-2026-09-13.md` (source survey), `docs/detours-addendum.md` (detours), `scripts/ttc_transit_probe.py` (reference behaviour for most tools)

**What changed in v0.7:** the document now describes what the code does, following the spec-correctness review of phases 1 to 4 (2026-10-11, 26 findings, none critical) and the fixes that landed with it. Closures follow the span model: a station strictly inside a closure's span is closed, a span end station is served on the platform facing the open line and is the shuttle terminus, and `next_trains` reports `next_min: []` on a suspended platform because NTAS keeps publishing predictions inside a closure (6.7 item 4, 6.8, 8 item 7). A `connections` option with a shuttle leg reports `typical_min: null` with `known_min`, counts the shuttle as a transfer and ranks after complete options; every disruption names the legs it `affects`; the leg vocabulary gains `transfer` (4.1, 4.4, 6.7 item 5). The step-free verdict is `yes | no | unknown` with a `basis` (4.4, 6.2). `service_status` closures are the shared disruptions element, `reduced_speed_zones` is `{count, zones}` and every `surface` row has one shape (4.4). Source facts corrected: the detour feed is JSON, service changes come from GTFS-RT `MODIFIED_SERVICE` first, `trips/subway` is unused, and alert station names resolve at answer time (2, 2.3, 3, 4.3, 6.4, 6.5). Fetch layer: a failing source is held with exponential backoff and its 403/429 answers counted in `feed_status`, trip ids resolve against every loaded feed, and the stdio server rechecks CKAN every 24 h in a worker thread (2.1, 2.2, 6.9, 8 items 2 and 10). Inputs and outputs recorded as implemented: `connections`, `routes`, `scheduled_departures`, `route_shape` and `alerts.type` names, `meta.cache` adds `static`, `feed_status` rows add `expired`, `detours` carries `entity_id` and a local `fetched_at`, station results carry `id` and `aliases` (4, 4.1, 4.2, 4.4). Section 9 records Python 3.14, the stdio default and the licence wording; section 11 gains the 2026-10-05, 2026-10-06 and 2026-10-11 entries. A same-evening follow-up (2026-10-11) narrowed a station's `platforms` to its rail platforms and moved bus bays and streetcar platforms to `surface_stops`, so the summary and step-free verdict no longer read bus bays flagged 0 (31 of 114 live stations had read `partially accessible`); left the summary `unknown` beside a 0 flag; nulled `first_departure` behind a shuttle leg; dropped route-level alerts from a shuttle-only option; and gave `scheduled_departures` the arrivals stop head (4.1, 4.4, 6.2, 11).

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
 static layer (SQLite, rebuilt per board period by a Cloud Run Job on a daily Cloud Scheduler trigger; published to a GCS bucket; pulled by the service at cold start)
   CKAN package_show -> merged GTFS zip   -> stops (hierarchy) / routes / trips / stop_times / calendar / shapes / pathways / levels
                     -> surface GTFS zip  -> crosswalk: stop_code <-> surface stop_id
   derived: stop_directions, route_patterns (with stop_sequence), pattern_shapes, pattern_run_times (median minutes between
            consecutive stops per pattern), route_service_hours, station_children, station_aliases, transfer_graph
            (stop <-> station membership, stop <-> stop within walking distance), spatial index for nearest_stops,
            elevator_units (unit code -> nodes), route_series, route_service_level (ttc.ca, refreshed with the static build)

 on-demand fetch layer (TTL cache + single-flight + last-known-good; nothing runs unprompted)
   source                                              size    fetch   TTL      used by
   bustime.ttc.ca/bustime/map/getStopPredictions.jsp   1 KB    0.07 s  30 s/stop   arrivals (minutes, vehicle id, direction)
   bustime.ttc.ca/gtfsrt/vehicles (protobuf)           90 KB   0.06 s  30 s     crowding on arrivals, vehicles
   bustime.ttc.ca/gtfsrt/trips (protobuf)              568 KB  0.10 s  30 s     trip ids for detour flags (lazy), route-wide questions
   ntas.ttc.ca/api/ntas/get-next-train-time/{platform} <1 KB   0.2 s   10 s/platform  next_trains
   gtfsrt.ttc.ca/trips/subway (protobuf)               12 KB   0.06 s  --       not used; candidate for the parity suite
   alerts.ttc.ca/api/alerts/list (ETag -> 304)         51 KB   0.07 s  25 s     alerts, accessibility, stop/station enrichment
   gtfsrt.ttc.ca/alerts/{all,accessibility} (protobuf) 40 KB   0.15 s  60 s     cause/effect, platform ids
   gtfsrt.ttc.ca/trips/detour (JSON)                   163 KB  0.10 s  5 min    detours, detoured-route set, detour flags on arrivals
   www.ttc.ca/ttcapi/routedetail/getallroutesandstopsalerts  80 KB 0.43 s 10 min  service_changes, detour reasons
   gtfsrt.ttc.ca/trips/modified_update                 574 KB  3.0 s   --       parity tests only (server-side generation cost)

 tool API (MCP)
```

### 2.1 Static layer

- **Where it runs (v0.6):** the daily check and the rebuild are a Cloud Run Job (`ttc-mcp-static`), triggered by Cloud Scheduler at 04:30 America/Toronto. The Job resolves both packages, compares ETags with `manifest.json` in the bucket, rebuilds on change into `static-<feed_version>.sqlite`, uploads it, and rewrites the manifest (which lists the active feed and, during a period change, the incoming one). The serving instance never builds anything: at cold start it reads the manifest and downloads the referenced database(s) to `/tmp` (in-memory on Cloud Run, so the memory limit must cover it; the build keeps the file compact, see 6.9). In local stdio mode the same code runs the check in-process: once before the server starts (a rebuild at that point blocks server start, not tool calls) and then every 24 h in a worker thread while the server runs (`create_app(recheck=...)`, enabled from `main` for stdio with refresh on; `--no-refresh` or `TTC_MCP_SKIP_REFRESH` turns both off, and the HTTP transport never rechecks because that is the Job's role). Every check, changed or not, records `checked_at` and the CKAN `last_modified` in `manifest.json`, which `feed_status` shows as `last_check` and `ckan_last_refreshed`.
- Packages: `merged-gtfs-ttc-routes-and-schedules` (primary) and `surface-routes-and-schedules-for-bustime` (crosswalk only). Once a day: `package_show`, then `HEAD` on each zip and compare ETags; download and rebuild only on change (about 84 MB + 83 MB per board period, roughly every six weeks). The CKAN API is cached 15 minutes upstream, so a new feed can take that long to become visible; this does not matter at a daily cadence.
- Rebuild is one streaming pass over `stop_times.txt` (~7.7 M rows; measured 2026-10-05: 13 s on an M4 Pro, 104 MB SQLite per feed, downloads of both zips about 3 s) into a temporary database swapped in atomically. An external build (`ttc-mcp-build`, or the Job) never touches the serving process: tool calls keep using the previous database until `manifest.json` changes on disk, and the store is reopened on the next call. The in-process startup build in stdio mode blocks server start, not tool calls. A server with no database answers static tools with an explicit error (`static store not built yet ...; run ttc-mcp-build`) rather than blocking.
- Patterns store `(stop_sequence, stop_id)` pairs because 9,099 of 137,912 trips do not number stops from 1 and GTFS-RT selectors reference `stop_sequence`.
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

Every output includes `meta: {source, feed_timestamp, fetched_at, cache, static_feed, staleness}` where `cache` is `live | ttl | stale_fallback | static` (`static` when the answer fetched nothing; `feed_timestamp` is then absent) and `staleness` is per section 6.5. Answers that carry disruptions add `meta.advisories` with the snapshot's own freshness. `feed_status` source rows describe a cached entry rather than an answer, so their `cache` may also read `expired` (past its TTL, not asked again yet). Inputs are JSON.

### 4.1 Core

| Tool | Input | Output | Fetches (beyond cache) |
|---|---|---|---|
| `find_stops` | `query` text (stop name, station name or alias, stop number), optional `route`, `direction`, `mode` | stops with number, name, coordinates, wheelchair flag, routes/directions/headsigns served, parent station | none (static) |
| `nearest_stops` | `lat`, `lon` (from the Google Maps MCP or any geocoder), `radius_m` (default 500), `limit` (default 10), `kind` = `any` / `stop` / `station` | stops or stations ordered by distance with walk minutes, routes/directions/headsigns, station membership, and active disruptions | alerts list for the disruption flags |
| `service_status` | none | one-call network picture: subway closures as station spans with shuttle info, suspended or detoured surface routes, reduced speed zones, elevator outages by station, counts and `as_of` | alerts list + detour feed |
| `connections` | `origin` and `destination`, each a stop number, a station id or alias, or `{lat, lon}`; options `max_transfers` (0 or 1, default 1), `walk_m` (1 to 2000, default 400), `depart_at` (ISO, Toronto time when it has no offset; default now), `limit` (1 to 10, default 4) | ranked options with legs (walk / ride / transfer / shuttle), board and alight stops, stop counts, typical minutes (`typical_min: null` with `known_min` when a shuttle leg's minutes are unknown), next departures at the first board stop (live within an hour of now, else timetable), and every disruption touching a leg with the legs it `affects` | arrivals sources for the first leg; alerts and detour feed for the overlay |
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
| `gtfsrt.ttc.ca/trips/subway` | timestamp = request time; not used (candidate for the parity suite) | n/a | n/a |
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

// connections (decision h)
{ "from": {"lat": 43.6527, "lon": -79.3581}, "to": {"station": "15772", "name": "Union"}, "depart_at": "2026-09-13T16:31-04:00",
  "options": [
    { "rank": 1, "transfers": 0, "typical_min": 20, "walk_m": 160, "stops": 12, "disruptions": [], "basis": "realtime",
      "first_departure": {"when": "2026-09-13T16:46", "in_min": 15, "trip_id": "108895010"},
      "legs": [ {"mode": "walk", "to": {"number": "15246", "name": "Mill St at Cherry St West Side"}, "distance_m": 160, "min": 3},
                {"mode": "ride", "route": "121", "direction_id": "1", "headsign": "West - 121 Esplanade-River Towards Union Station",
                 "transit": "bus", "pattern_id": 812,
                 "board": {"number": "15246", "name": "Mill St at Cherry St West Side"},
                 "alight": {"number": "16754", "name": "1 Front St West - Union Station"},
                 "stops": 12, "typical_min": 17, "next": [ {"eta_min": 15, "vehicle_id": "8520", "basis": "realtime", "crowding": {"label": "Not busy"}} ]} ] },
    { "rank": 2, "transfers": 1, "typical_min": null, "known_min": 12, "typical_min_note": "excludes the shuttle leg", "walk_m": 29, "stops": 9,
      "disruptions": [ {"type": "closure", "route": "1", "alert_id": "12345", "span": ["TMU", "Union"], "shuttle": "Will Operate", "affects": [3], "...": "..."} ],
      "legs": [ {"mode": "walk", "to": {"number": "15460", "name": "Cherry St at Front St East"}, "distance_m": 29, "min": 0},
                {"mode": "ride", "route": "504", "direction_id": "1", "headsign": "West - 504A King towards Dundas West Station", "transit": "streetcar",
                 "pattern_id": 6, "board": {"number": "15460"}, "alight": {"number": "15637", "name": "King St West at Yonge St West Side - King Station"},
                 "stops": 9, "typical_min": 12},
                {"mode": "shuttle", "line": "1", "from": {"station": "14586", "name": "King"}, "to": {"station": "15772", "name": "Union"},
                 "typical_min": null, "alert_id": "12345", "note": "subway suspended TMU to Union; shuttle buses operating per alert 12345"} ] } ],
  "meta": {...} }
// transfers counts every vehicle change (ride and shuttle legs minus one); an option with a shuttle leg has typical_min null,
// known_min for its other legs, and ranks after complete options with the same transfers (6.7 item 5). A one-transfer option
// carries {"mode": "transfer", "from": {...}, "to": {...}, "distance_m": 0, "min": 4} between its rides (walk plus the 4-minute penalty).
// affects lists 1-based leg numbers. disruptions entries are the shared element below plus affects.
// first_departure is null when the first vehicle leg is a shuttle (the timetable record belonged to the replaced ride); a route-level
// alert is listed only while a ride on its route remains in the option.

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
5. **`connections`** (the centrepiece). Static search over patterns, then a live and disruption overlay:
   - Origin and destination sets: the stops within `walk_m` (default 400) of each end, or the given stop, or every platform and surface stop belonging to the given station (via `parent_station` and the alias/suffix membership), including bus stops within 150 m of the station record.
   - Direct options: every (route, direction, pattern) serving an origin stop where a destination stop appears later in the same pattern.
   - One-transfer options: for each origin pattern, each downstream stop that is a transfer point (same stop, same station, or within 150 m) to a pattern that reaches a destination stop later. Limited to patterns running on today's service id and within the route's service span at the departure time (Blue Night routes only overnight).
   - Typical minutes per leg from `pattern_run_times` (median scheduled minutes between the two stops), walking legs as above, a fixed 4-minute transfer penalty.
   - Ranking: transfers (every vehicle change, a shuttle included), then complete options before those with an unknown leg, then total typical minutes including walking (`known_min` for the latter), then stop count. Return `limit` options (default 4).
   - Live overlay: `arrivals` for the first ride leg's board stop (cheap per-stop XML; NTAS for platforms); crowding inline.
   - Disruption overlay: drop ride legs that traverse a closed station on a subway line and, when the closure has `shuttleType` "Will Operate", replace them with a `shuttle` leg between the closure's end stations; the shuttle's minutes stay unknown, so the option reports `typical_min: null` with `known_min` for its other legs and a `typical_min_note`, counts the shuttle as a transfer, and ranks after complete options at equal transfers; closed spans are pruned inside the search (the `rideable` predicate), so a transfer route around a closure is found; drop legs whose board or alight stop is skipped by a detour and re-run with the nearest served stop; annotate RSZ (in the ride's direction only, matched on the zone's terminal, then the platform name) and delays without dropping. Every retained option lists the disruptions it touches, each naming the legs it `affects` (1-based).
   - Out of scope: timetable-exact itineraries (a RAPTOR planner over `stop_times` with real-time is a possible v1.x addition); walking routes (straight-line only).
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

**Decision:** the server is a FastMCP 3 application (`fastmcp==3.4.x`) exposing **streamable HTTP** at `/mcp`, with authentication and authorization in-process rather than through a separate proxy: `GoogleProvider` (OAuth with dynamic client registration, `require_authorization_consent="remember"`) plus the `EmailAllowlist` middleware (`ALLOWED_EMAILS=lunaparker33@gmail.com`). The gate runs in-process, not as a proxy in front of a loopback server, because the tools are native Python and there is no third-party backend to wrap. A `--transport stdio` mode remains for local development and for any developer who wants to run it on their own machine.

**Why:** a hosted connector reaches claude.ai, Claude Desktop, iOS and Claude Code through one URL; stdio would confine the server to one machine. The on-demand design (decision g) is what makes hosting cheap: an idle instance makes no requests and can scale to zero.

**Cloud shape:**

| Item | Value |
|---|---|
| Project / region | `$PROJECT_ID` ($PROJECT_NUMBER); **`us-east1`**, an always-free region |
| Service | Cloud Run `ttc-mcp`, `--allow-unauthenticated` (the gate is the app), `--min-instances 0 --max-instances 1`, `--memory 1Gi --cpu 1 --cpu-boost --timeout 300`, execution environment gen2, CPU allocated only during requests |
| URL | `https://ttc-mcp-$PROJECT_NUMBER.us-east1.run.app/mcp` (deterministic; set `BASE_URL` and the OAuth redirect URI `.../auth/callback` before the first deploy) |
| Identity | service account `ttc-mcp-sa`; secrets `ttc-mcp-oauth-client-secret`, `ttc-mcp-jwt-signing-key`, `ttc-mcp-storage-fernet-key` (no backend credential: every TTC and City source is unauthenticated) |
| OAuth state | Firestore `(default)` via `FirestoreStore` with `FirestoreV1KeySanitizationStrategy` + `FirestoreV1CollectionSanitizationStrategy`, wrapped in `PrefixCollectionsWrapper(prefix="ttc")` and Fernet; the sanitization and prefix are mandatory because Claude Code's CIMD client id is a URL shared by every gateway |
| Static store | bucket `gs://ttc-mcp-static-$PROJECT_NUMBER` (us-east1): `manifest.json`, `static-<feed_version>.sqlite`, the two source zips for provenance; Job `ttc-mcp-static` (same image, entrypoint `ttc-mcp-build`), Cloud Scheduler `ttc-mcp-static-daily` at 04:30 America/Toronto; the Job's service account gets `roles/storage.objectAdmin` on that bucket only, the service's gets `roles/storage.objectViewer` |
| Images | Artifact Registry repo `ttc-mcp` (docker, us-east1); `gcloud builds submit` then `gcloud run deploy --image` and `gcloud run jobs deploy` from the same tag |
| Connectors | claude.ai custom connector on the `/mcp` URL (DCR, leave the advanced OAuth fields empty); Claude Code `.mcp.json` entry `{"type": "http", "url": ".../mcp"}` |
| Billing | falls under the project-wide hard cap and `billing-killswitch`; no new budget wiring; expected marginal cost near zero (below) |

**Streamable HTTP mode:** stateless, JSON responses (`stateless_http=True`, `json_response=True`): each `tools/call` is one POST and one response, no server-held session and no long-lived SSE stream. This matters twice: with `max-instances 1` every instance can answer any request without sticky sessions, and idle connected clients cannot keep the instance billing. The cache module works unchanged in one process; single-flight is per instance, which with one instance is global.

**Cold start:** container start plus manifest read plus one regional bucket download of the database (measured 2026-10-05: 104 MB per feed without shapes, built in 13 s; patterns, run times and per-stop departures as compact integer rows rather than raw `stop_times`). Expect 3 to 8 seconds on the first call after idle, then warm; `--cpu-boost` helps. If the download proves slow, the alternative is a gen2 GCS volume mount of the bucket with SQLite opened read-only (`immutable=1`), which trades cold-start time for slower first reads per page.

**Cost (free-tier figures to re-verify at deploy time):** Cloud Run's monthly free tier (about 2 million requests, 180,000 vCPU-seconds and 360,000 GiB-seconds) covers a single-user service that bills only during requests plus a two-minute daily Job; a sub-gigabyte bucket sits inside the 5 GB always-free storage in US regions; Cloud Scheduler allows three free jobs; Artifact Registry storage for one image is within its free allowance; Firestore reads for OAuth are negligible. The only items that could bill are egress on large TTC fetches (kilobytes to hundreds of kilobytes per call) and any long-lived instance, which the stateless mode prevents.

**Threat model:** the server holds no third-party credential; the only secrets are its own gate's. Read-only public data behind the email gate is the safe class of connector. Nothing in the tools can act on the TTC or on the user's accounts.

**Local mode:** `ttc-mcp --transport stdio` uses the same code with the static check in-process (once before the server starts, when a needed rebuild blocks server start rather than tool calls, then every 24 h in a worker thread while running; `--no-refresh` turns both off) and the cache directory from `TTC_MCP_CACHE`; the Google gate is off in stdio mode. An external `ttc-mcp-build` is picked up on the next tool call because the store reopens when `manifest.json` changes.

**Validation (adds to section 8):** (14) confirm every TTC host answers from Cloud Run's us-east1 egress IPs with the server's User-Agent, since none publish access rules; (15) measure cold start with the real database size and pick download versus volume mount; (16) verify the connector from claude.ai, Claude Code and iOS after the first deploy.

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
| j | Transport and hosting | FastMCP 3 streamable HTTP (stateless JSON) with in-process Google OAuth gate + email allowlist; Cloud Run `ttc-mcp` in us-east1, min 0 / max 1; static store built by a daily Cloud Run Job into a GCS bucket and pulled at cold start; stdio mode for local use |

## 8. To validate during implementation

1. **`EMPTY` as default.** Sample the vehicle feed at a weekday peak (08:00 to 09:00): if buses spread across the three values while streetcars stay `EMPTY`, downgrade streetcar `EMPTY` to `No info`.
2. **Board-period transition.** Implementable now (dual-feed lookup, 2.1); first change due about 2026-10-31. Watch it: confirm the dual-feed window resolves every live trip id and every detour trip id.
3. **Elevator node coverage.** After each static rebuild, log the share of active alert codes that match a node; if it drops below the current 10 of 15, widen the fallback text parsing.
4. **Detour feed behaviour on change.** Capture the feed before and after a detour ends to confirm entities disappear rather than linger with past `serviceDates`.
5. **`modified_update` generation time.** Re-measure monthly; if it drops under 0.5 s it becomes a candidate arrivals source again (public numbers, no crosswalk).
6. **TTL tuning.** Log cache hit rates per source for the first weeks; if bursts routinely exceed a TTL (many `live` fetches within one tick), lengthen that TTL toward the feed's true interval, never shorter.
7. **NTAS during a closure.** Validated 2026-10-11, result in 6.8: NTAS keeps publishing minutes at closed platforms during a closure (Old Mill eastbound "1", Jane westbound "1, 4, 8" inside the Kipling to Jane closure), so suspended platforms are reported from the closure, never from NTAS. (The original plan was to capture College and Bloor-Yonge during the September College to St Clair closure.)
8. **Connections quality.** 19 of 20 pairs under 150 min on 2026-10-11 (`scripts/validate_connections.py`; the one miss had no stop within 400 m of its origin); the TTC planner comparison of each top option is pending. Then tune `walk_m`, the 150 m transfer radius (the four stations without nearby surface stops need the membership rule), and the transfer penalty.
9. **Alias completeness.** After each static rebuild, list surface-stop suffixes and alert station names that resolve to no station; add manual aliases as needed and keep GO stations external.
10. **Rate behaviour.** None of the undocumented hosts publish limits. Backoff implemented 2026-10-11 (2.2): an exponential hold per failing source, 403 and 429 counted per source in `feed_status`; single-flight already prevents self-inflicted bursts. Still to watch: whether any host ever answers 403 or 429 to the project User-Agent.

## 9. Implementation notes

- **Adapters:** one module per source (`ckan`, `gtfs_static`, `bustime_rt`, `bustime_web`, `gtfsrt_ttc`, `ntas`, `ttc_alerts`, `ttcapi`), each returning typed records plus a `SourceStatus`. Tools compose adapters through the cache and never touch HTTP directly.
- **Cache module:** `Entry {key, parsed, fetched_at, signal, signal_ts, etag, last_modified, last_attempt, last_error, hits, failures, retry_after, status_counts}`; `get(source, key, fetch, parse)` implements TTL, single-flight (one in-flight future per key), conditional GET when a validator exists, stale-if-error with the age reported back to the tool, and the backoff hold of 2.2. Memory footprint is small: the largest cached body is the 568 KB trip feed.
- **Connections planner:** precompute per pattern the ordered stop list with cumulative median minutes; index stop -> [(pattern, position)]; index station -> member stops (parent, suffix, 150 m rule) and stop -> nearby stops (150 m grid). Query: origin set x destination set through the stop index gives direct hits in O(patterns at origin); one-transfer hits enumerate downstream stops of each origin pattern (average 33) and probe the destination index. Apply the running-now filter, score, overlay, and return. Expected cost: tens of milliseconds static plus one arrivals fetch.
- **Static build:** streaming, single pass, ~2 minutes, in the Cloud Run Job; write to a temporary database, upload, then rewrite the manifest; the manifest keeps the previous database listed for the overlap window; a service that starts with no manifest yet answers static tools with an explicit "static store not built yet" error rather than blocking.
- **Entry points:** `ttc-mcp` (server; `--transport http|stdio`; the default comes from `TTC_MCP_TRANSPORT` and is stdio locally, http on `$PORT` in the container, whose image sets `TTC_MCP_TRANSPORT=http`) and `ttc-mcp-build` (the Job). One Dockerfile (`python:3.14-slim`, `uv sync --frozen`; `pyproject` requires 3.13+, `.python-version` is 3.14), one image, two commands. The Dockerfile is phase 5 work and does not exist yet.
- **Parity tests (nightly, live):** arrivals at 15461 from the per-stop XML vs crosswalked `bustime` protobuf vs `modified_update` (same vehicles and minutes within 1 min); NTAS vs `trips/subway` for 13863; detour skipped stops vs live omitted stops on a linked trip; Blue Night classifier still flags 35 of 35.
- **Licensing (implemented 2026-10-11):** CKAN data under the Open Government Licence - Toronto; the attribution string ends the server instructions (`INSTRUCTIONS`, the MCP `instructions` text), followed by "Real-time and advisory answers are best effort, read from the TTC's own site endpoints, none of which publish terms." The descriptions of `arrivals`, `next_trains`, `vehicles`, `alerts`, `service_status`, `detours`, `service_changes` and `connections` carry a best-effort clause; `arrivals` and `vehicles` say historical occupancy is out of scope.

## 10. Effort

Core server: ~3 days solo / ~1.5 days with Claude; the on-demand model removes the poller supervision but adds the cache module, so the total is unchanged. Cloud deployment following the existing gateway recipe (OAuth client, secrets, service account, bucket, Job, Scheduler, first deploy, connector re-adds): ~4 h solo / ~2 h with Claude, most of it console clicks the recipe already documents. Additions: item 1 ~2 h / ~1 h; item 3 ~1 h / ~30 min (plus ~1.5 days / ~half a day for an exact step-free verdict beyond coded-node cases); item 4 ~1 h / ~30 min; item 5 ~3 to 4 h / ~1.5 h; item 6 ~2 h / ~1 h. Five additions without step-free routing: about **1.5 days solo, ~5 hours with Claude**. v0.4 additions: `connections` ~1 day / ~3 to 4 h; `nearest_stops` ~1 h / ~30 min; `service_status` plus propagation ~3 h / ~1.5 h; alias table ~1.5 h / ~45 min. v0.5: `scheduled_departures` and the schedule basis ~2 h / ~1 h (the `stop_times` table already exists in the static build). About **2 days solo, ~7 to 8 hours with Claude** on top of the earlier items.

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
