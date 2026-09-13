# TTC network MCP server: specification v0.3

**Date:** 2026-09-13 | **Status:** decisions a to g accepted; ready to implement | **Supersedes:** v0.2 (kept as `ttc-mcp-spec-v0.2.md`; v0.1 as `ttc-mcp-spec-v0.1.md`)
**Companions:** `ttc-transit-data-sources-investigation.md` (source survey), `ttc-detours-tripmodifications-addendum.md` (detours), `ttc_transit_probe.py` (reference behaviour for most tools)

**What changed in v0.3:** the server no longer polls. Every real-time and advisory source is fetched on demand through a TTL cache with single-flight coalescing and a last-known-good fallback (decision g, section 6.6). The static GTFS keeps one scheduled check per day. Arrivals now use the cheapest per-stop endpoints; the 3-second `modified_update` feed is demoted to parity tests.

**Deployment model:** single-user, self-hosted (the maintainer, or another developer on their own machine). The priority is the least bandwidth and the fewest requests to the TTC's servers, not throughput for many users. Latency of a few hundred milliseconds per tool call is acceptable.

## 1. Purpose and principles

The server exists so that Claude (and any MCP client) can reason about the TTC network as it is right now, not as it was when a dataset was pasted in.

1. **Nothing baked in.** Static data is resolved through the City of Toronto CKAN API and re-downloaded only when the resource ETag changes.
2. **Fetch on demand, never in the background.** No poller runs while nobody is asking. The only scheduled work is one `HEAD` per day per static package. Repeated questions inside a feed's update interval are served from a TTL cache.
3. **Public stop numbers are the canonical identifier** in every tool input and output. Other ID spaces stay inside the server.
4. **Every answer carries provenance and freshness**: source, feed timestamp, when it was fetched, whether it came live, from the TTL cache, or from the last-known-good fallback, and a staleness verdict.
5. **Undocumented endpoints are fragile**: one adapter each, defensive parsing, "unavailable" in the output rather than a crashed tool.
6. **Be a polite client**: a real User-Agent on every request (four TTC hosts return 403 to the default `Python-urllib` agent), conditional requests where supported, single-flight so concurrent tool calls never fetch the same thing twice.

## 2. Architecture

```
 static layer (SQLite/DuckDB, rebuilt per board period; one scheduled HEAD per day)
   CKAN package_show -> merged GTFS zip   -> stops (hierarchy) / routes / trips / stop_times / calendar / shapes / pathways / levels
                     -> surface GTFS zip  -> crosswalk: stop_code <-> surface stop_id
   derived: stop_directions, route_patterns (with stop_sequence), pattern_shapes, route_service_hours, station_children,
            elevator_units (unit code -> nodes), route_series, route_service_level (ttc.ca, refreshed with the static build)

 on-demand fetch layer (TTL cache + single-flight + last-known-good; nothing runs unprompted)
   source                                              size    fetch   TTL      used by
   bustime.ttc.ca/bustime/map/getStopPredictions.jsp   1 KB    0.07 s  30 s/stop   arrivals (minutes, vehicle id, direction)
   bustime.ttc.ca/gtfsrt/vehicles (protobuf)           90 KB   0.06 s  30 s     crowding on arrivals, vehicles
   bustime.ttc.ca/gtfsrt/trips (protobuf)              568 KB  0.10 s  30 s     trip ids for detour flags (lazy), route-wide questions
   ntas.ttc.ca/api/ntas/get-next-train-time/{platform} <1 KB   0.2 s   10 s/platform  next_trains
   gtfsrt.ttc.ca/trips/subway (protobuf)               12 KB   0.06 s  30 s     next_trains epochs (optional)
   alerts.ttc.ca/api/alerts/list (ETag -> 304)         51 KB   0.07 s  25 s     alerts, accessibility, stop/station enrichment
   gtfsrt.ttc.ca/alerts/{all,accessibility} (protobuf) 40 KB   0.15 s  60 s     cause/effect, platform ids
   gtfsrt.ttc.ca/trips/detour (protobuf)               163 KB  0.10 s  5 min    detours, detoured-route set, detour flags on arrivals
   www.ttc.ca/ttcapi/routedetail/getallroutesandstopsalerts  80 KB 0.43 s 10 min  service_changes, detour reasons
   gtfsrt.ttc.ca/trips/modified_update                 574 KB  3.0 s   --       parity tests only (server-side generation cost)

 tool API (MCP)
```

### 2.1 Static layer

- Packages: `merged-gtfs-ttc-routes-and-schedules` (primary) and `surface-routes-and-schedules-for-bustime` (crosswalk only). Once a day: `package_show`, then `HEAD` on each zip and compare ETags; download and rebuild only on change (about 84 MB + 83 MB per board period, roughly every six weeks). The CKAN API is cached 15 minutes upstream, so a new feed can take that long to become visible; this does not matter at a daily cadence.
- Rebuild is one streaming pass over `stop_times.txt` (~7.7 M rows, about 2 minutes) into a temporary database swapped in atomically. It runs in the background; tool calls keep using the previous database until the swap. A first-ever start with no database is the one case where a tool call must wait, and it says so.
- Patterns store `(stop_sequence, stop_id)` pairs because 9,099 of 137,912 trips do not number stops from 1 and GTFS-RT selectors reference `stop_sequence`.
- **Board-period overlap (decided):** keep the outgoing and incoming feeds loaded together from the moment the new package is seen until the old `feed_end_date` passes. Trip ids resolve against whichever feed contains them; detours whose trips resolve in neither are reported with `pattern: "unknown"`.
- The ttc.ca route-detail call (`serviceLevel`, branch metadata, 235 requests) runs once per static rebuild, not daily.

### 2.2 On-demand fetch layer

Each source has one adapter returning typed records plus a `SourceStatus`. Adapters go through a shared cache with these rules:

- **TTL cache.** A response is reused for the source's TTL (table above), which equals the feed's own update interval, so a burst of tool calls costs one fetch and never sees older data than a poller would.
- **Single-flight.** If a fetch for a source is in progress, other callers await that same fetch instead of starting another (an `asyncio.Lock` or future per cache key).
- **Last-known-good.** The last successful response is kept beyond its TTL. When a fetch fails (timeout, 5xx, 403, parse error), the tool answers from it with `meta.cache = "stale_fallback"` and its real age, up to a maximum stale age (10 minutes for real-time sources, 1 hour for advisories); past that the tool reports `unavailable`.
- **Conditional requests.** `alerts.ttc.ca` honours `If-None-Match` (a 304 costs 0.05 s and no body); the CKAN zips expose ETags for the daily `HEAD`. The GTFS-RT hosts send no validators, so their fetches are unconditional.
- **Cache keys** include parameters for per-stop and per-platform endpoints (`getStopPredictions:15461`, `ntas:13863`).

**Arrivals path (least bandwidth).** For a bus or streetcar stop: the per-stop XML (1 KB) gives minutes, direction and vehicle id; the vehicle feed (90 KB, shared across all stops for 30 s) gives crowding by vehicle id. The detour flag is lazy: the detour feed (163 KB, 5 min TTL) yields the set of routes with an active detour; only when the stop's route is in that set does the server fetch `bustime.ttc.ca/gtfsrt/trips` (568 KB, 30 s TTL) to map vehicle id to trip id and test membership in the detour's `selectedTrips`. For a rail platform: NTAS per platform (<1 KB). Typical arrivals call: about 91 KB and 0.15 s; worst case with a detoured route: about 820 KB and 0.3 s.

**Why `modified_update` is out of the hot path.** It is the only feed keyed by public stop numbers, but it takes 3 seconds to generate server-side regardless of format (574 KB protobuf in 2.97 s versus 568 KB from `bustime.ttc.ca` in 0.10 s). Everything it offered is obtainable elsewhere: public-number keying via the per-stop XML or the crosswalk, detour links via the detour feed's `selectedTrips`. It remains in the nightly parity test.

### 2.3 Advisory layer

- `alerts.ttc.ca` is primary (closures, RSZ, elevators with unit codes, unplanned detours); GTFS-RT alerts supply `cause`/`effect` and platform ids; service changes exist only in `getallroutesandstopsalerts.serviceChanges`. All fetched on demand with the TTLs above.
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

## 4. Tools

Every output includes `meta: {source, feed_timestamp, fetched_at, cache, static_feed, staleness}` where `cache` is `live | ttl | stale_fallback` and `staleness` is per section 6.5. Inputs are JSON.

### 4.1 Core

| Tool | Input | Output | Fetches (beyond cache) |
|---|---|---|---|
| `find_stops` | `query` text, or `near {lat, lon, radius_m}`, optional `route`, `direction`, `mode` | stops with number, name, coordinates, wheelchair flag, routes/directions/headsigns served, parent station | none (static) |
| `stop` | stop number | full record incl. station membership and active alerts | alerts list (51 KB / 304) |
| `station` | station id or name | platforms, entrances, units with live status, accessibility summary | alerts list |
| `routes` | filters `series`, `overnight`, `type` | long name, series, service level, service hours by day type, overnight share | none |
| `route_stops` | route, direction, optional `pattern` | ordered stops per pattern with trip counts and headsigns | none |
| `service_hours` | route, date | runs that date? (calendar + exceptions), first/last trip | none |
| `arrivals` | stop number, optional route, `limit` | next vehicles with crowding inline (decision d) and detour flag | per-stop XML + vehicles; detour feed; trips only if the route is detoured |
| `next_trains` | platform id or station id | per platform: line, direction text, next trains (minutes) | NTAS per platform (+ subway feed for epochs on request) |
| `alerts` | route / station / stop / `type` in {closure, rsz, detour, elevator, other} | normalised alerts with resolved stop/station objects | alerts list + GTFS-RT alerts |
| `feed_status` | optional `probe: true` | per-source cache state and freshness; with `probe`, one live request per source | none, or one per source |

### 4.2 Added items

| Tool | Input | Output | Fetches |
|---|---|---|---|
| `route_shape` | route, direction, optional `pattern`, `format` = `polyline` (default, decision a) or `geojson`, optional `simplify_m` | canonical or requested pattern shape; `stops_between {from, to}` via `shape_dist_traveled` | none |
| `accessibility` | station or stop | platform flags, entrance flags, units with live status and pathway context, station summary (decision b); trip flag ignored (decision c) | alerts list (+ GTFS-RT accessibility for platform ids) |
| `vehicles` | route or `vehicle_id` | position, bearing, speed, current stop, occupancy label + enum + confidence, trip/headsign | vehicles (+ trips for headsigns when the trip is not static) |
| `detours` | optional route | grouped by `detourId`; reason may be `null` (decision e) | detour feed + service changes |
| `service_changes` | optional route / date | board-period changes with parsed routes, dates, category, text, URL | service changes |

### 4.3 TTLs, request budget, and conditional fetching

| Source | Observed behaviour | TTL | Stale-if-error max |
|---|---|---|---|
| `getStopPredictions.jsp` (per stop) | TTC's own map endpoint; predictions refresh with the BusTime tick | 30 s per stop | 10 min |
| `bustime.ttc.ca/gtfsrt/vehicles`, `/trips` | header timestamp advances 30 s per tick, 5 to 7 s behind wall clock | 30 s | 10 min |
| `gtfsrt.ttc.ca/trips/subway` | timestamp = request time | 30 s | 10 min |
| `ntas.ttc.ca` (per platform) | no cache headers | 10 s | 5 min |
| `alerts.ttc.ca/api/alerts/list` | `max-age=25`, `ETag`, `Last-Modified` | 25 s, then conditional GET | 1 h |
| `gtfsrt.ttc.ca/alerts/*` | timestamp = request time | 60 s | 1 h |
| `gtfsrt.ttc.ca/trips/detour` | no header timestamp; stable across 40 min | 5 min; `as_of` = HTTP `Date` | 1 h |
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
      "eta_min": 11, "vehicle_id": "4475", "scheduled_only": false,
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
  "platforms": [ {"platform": "13863", "name": "...", "wheelchair": 1} ],
  "entrances": [ {"id": "100254", "name": "Main Entrance", "lat": 0, "lon": 0, "wheelchair": 1} ],
  "units": [ {"code": "9Y1L", "kind": "elevator", "levels": ["Concourse Level", "Platform Level"],
              "status": "out_of_service", "alert_id": "76084", "text": "Queen: Elevator 9Y1L out of service between ...",
              "node_match": true} ],
  "step_free": {"verdict": "unknown", "basis": "text"}, "meta": {...} }

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
                                   "trips_affected": 67, "entity_ids": ["detour_0_695:0_0_1209_112_..."] } ] } ],
  "meta": {"source": "gtfsrt.ttc.ca/trips/detour", "feed_timestamp": null, "fetched_at": "<HTTP Date>", "cache": "ttl", ...} }

// service_changes
{ "changes": [ {"id": "f4d1a81b-...", "routes": ["101"], "route_type": "Bus", "title": "101 Downsview Park - Temporary route change ...",
                "effective": ["2026-05-02T00:00:00Z", "2026-09-30T04:00:00Z"], "text": "...", "url": "https://www.ttc.ca/..."} ], "meta": {...} }

// feed_status (decisions f, g)
{ "static": {"feed_version": "S1000538", "start": "20260906", "end": "20261031", "ckan_last_refreshed": "2026-09-04 18:03:35",
             "etag": "\"1788553243.713-...\"", "last_check": "2026-09-13T04:00:00Z", "days_remaining": 48,
             "newer_package_seen": false, "verdict": "fresh"},
  "sources": [ {"name": "bustime vehicles", "url": "...", "last_fetch": "2026-09-13T20:24:00Z", "cache": "ttl", "age_s": 12,
                "feed_timestamp": 1789331032, "signal": "feed_header", "lag_s": 5, "entities": 1275, "last_error": null, "verdict": "fresh"},
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

- **Station summary (decision b):** computed from platforms and entrances only; `accessible` when every platform is 1, `not accessible` when every platform is 2, `partially accessible` otherwise, `unknown` when flags are 0/blank. The station record's own flag (1 on all 114 stations) is never used. Basis: platforms flagged 2 today include Old Mill, Islington, College, King, Museum.
- **Stop flags (decided):** raw `wheelchair_boarding` with GTFS meaning; 0 means unknown, never accessible (1,340 surface stops are 2, 194 are 0).
- **Trip flag (decision c):** `trips.wheelchair_accessible` is ignored. Basis: it is 2 on all 17,255 trips of the twelve streetcar routes despite a fully low-floor fleet.
- **Units (decided):** join alert unit codes to pathway nodes named `Elevator_{code}_{level}` / `Escalator_{code}_{level}` when present (95 elevator and 462 escalator coded nodes; 58 of 67 stations have coded nodes; 10 of 15 real alert codes matched today), else fall back to the alert `headerText`; output carries `node_match`.
- **Step-free verdict (decided):** `exact` only when every unit on the path is a coded node with known status; otherwise `unknown` with `basis: "text"`. Line 5/6 alerts use `ELV02-B` style codes that do not appear in node names.
- **Sources (decided):** `alerts.ttc.ca` accessibility list is primary; GTFS-RT `alerts/accessibility` supplies platform ids; ttc.ca duplicates are not fetched. Station ids join on `stop_id OR stop_code`.

### 6.3 Crowding (item 4)

- **Vocabulary (decided):** `EMPTY -> Not busy`, `FEW_SEATS_AVAILABLE -> Busy`, `FULL -> Very busy`, absent -> `No info`; return `label`, `gtfs`, `confidence`. Basis: the feed uses only those three values (841/206/14 of 1,253 reporting vehicles; 22 without the field; streetcars included) and ttc.ca's legend is exactly those four labels; the next 504 at Cherry St showed "Not Busy" while reporting `EMPTY`.
- **Confidence (decided):** `low` for `EMPTY` until section 8 item 1 is settled (all 25 vehicles on 504 and 155 of 162 streetcars reported `EMPTY` at 16:31), `high` for `FEW_SEATS_AVAILABLE`/`FULL`, `none` for `No info`.
- **Inline on arrivals (decision d):** every arrival row carries `crowding` via the vehicle-id join against the 90 KB vehicle feed.
- **Historical occupancy (decided):** out of scope; tool descriptions say so.

### 6.4 Detours (item 5)

- **Parsing (decided):** `gtfs-realtime-bindings >= 2.2.0` pinned, one adapter, JSON fallback.
- **Freshness (decided):** HTTP `Date` recorded as `fetched_at`; TTL 5 minutes.
- **Grouping (decided):** by `detourId`; directions listed inside; entity ids kept.
- **Reason (decision e):** joined from `serviceChanges` by route and date overlap (12 of 14 today); `reason: null` when nothing matches; never suppressed.
- **Membership on arrivals (decided, v0.3):** `selectedTrips.tripIds` from the detour feed, tested against the vehicle's trip id from `bustime.ttc.ca/gtfsrt/trips`, fetched only when the stop's route is in the detoured-route set. `modified_update`'s `modifiedTrip` link is no longer used.
- **Replacement buses (decided):** `306 Bus`/`506 Bus` entities are reported under route 306/506 with their "Replacement Bus" headsigns.
- **NEW trips (decided):** labelled by route plus first/last stop names.
- **Period boundary (decided):** dual-feed loading per 2.1; unresolved trips reported as `pattern: "unknown"`.

### 6.5 Service changes and feed status (item 6)

- **Service changes parsing (decided):** split `route` on `|`; when blank (6 of 46), extract the leading route number from `title`; use `routeType` (Subway appears there, never in `categoryName`); ignore `isDetour` (always false); dates from `effectiveStartDate`/`effectiveEndDate`.
- **GTFS-RT `MODIFIED_SERVICE` alerts (decided):** used only to attach `informed_entity` route ids when titles match (34 of 40 today); service changes remain the canonical list.
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

## 8. To validate during implementation

1. **`EMPTY` as default.** Sample the vehicle feed at a weekday peak (08:00 to 09:00): if buses spread across the three values while streetcars stay `EMPTY`, downgrade streetcar `EMPTY` to `No info`.
2. **Board-period transition.** Watch the first period change (late October 2026): confirm the dual-feed window resolves every live trip id and every detour trip id.
3. **Elevator node coverage.** After each static rebuild, log the share of active alert codes that match a node; if it drops below the current 10 of 15, widen the fallback text parsing.
4. **Detour feed behaviour on change.** Capture the feed before and after a detour ends to confirm entities disappear rather than linger with past `serviceDates`.
5. **`modified_update` generation time.** Re-measure monthly; if it drops under 0.5 s it becomes a candidate arrivals source again (public numbers, no crosswalk).
6. **TTL tuning.** Log cache hit rates per source for the first weeks; if bursts routinely exceed a TTL (many `live` fetches within one tick), lengthen that TTL toward the feed's true interval, never shorter.
7. **Rate behaviour.** None of the undocumented hosts publish limits; log 403/429 per host and back off exponentially; single-flight already prevents self-inflicted bursts.

## 9. Implementation notes

- **Adapters:** one module per source (`ckan`, `gtfs_static`, `bustime_rt`, `bustime_web`, `gtfsrt_ttc`, `ntas`, `ttc_alerts`, `ttcapi`), each returning typed records plus a `SourceStatus`. Tools compose adapters through the cache and never touch HTTP directly.
- **Cache module:** `CacheEntry {key, body, parsed, fetched_at, feed_timestamp, etag, last_modified, error}`; `get(key, ttl, fetch, max_stale)` implements TTL, single-flight (one in-flight future per key), conditional GET when a validator exists, and stale-if-error with the age reported back to the tool. Memory footprint is small: the largest cached body is the 568 KB trip feed.
- **Static build:** streaming, single pass, ~2 minutes; write to a temporary database and swap atomically; keep the previous database for the overlap window; a first-ever start without a database blocks tools with an explicit "building static index" message.
- **Parity tests (nightly, live):** arrivals at 15461 from the per-stop XML vs crosswalked `bustime` protobuf vs `modified_update` (same vehicles and minutes within 1 min); NTAS vs `trips/subway` for 13863; detour skipped stops vs live omitted stops on a linked trip; Blue Night classifier still flags 35 of 35.
- **Licensing:** CKAN data under the Open Government Licence - Toronto (attribution string in the server's `about` text); everything else undocumented, so tool descriptions say "best effort, from the TTC's own site endpoints".

## 10. Effort

Core server: ~3 days solo / ~1.5 days with Claude; the on-demand model removes the poller supervision but adds the cache module, so the total is unchanged. Additions: item 1 ~2 h / ~1 h; item 3 ~1 h / ~30 min (plus ~1.5 days / ~half a day for an exact step-free verdict beyond coded-node cases); item 4 ~1 h / ~30 min; item 5 ~3 to 4 h / ~1.5 h; item 6 ~2 h / ~1 h. Five additions without step-free routing: about **1.5 days solo, ~5 hours with Claude**.

## 11. Verification log (2026-09-13)

- Geometry: 137,912/137,912 trips with shapes; 1,592 shapes; 1,418 patterns; 42 multi-shape patterns; BusTime 504 patterns 4 with 36 to 39 embedded stops.
- Accessibility: station flag 1 on 114/114; platforms flagged 2 include Old Mill, Islington, College, King, Museum; 95/462/755 coded elevator/escalator/stair nodes; 10 of 15 alert codes matched; 63/63 alert station ids resolved via `stop_id OR stop_code`; streetcar trips 17,255 flagged 2.
- Crowding: 1,253/1,275 vehicles with status (841 EMPTY, 206 FEW_SEATS_AVAILABLE, 14 FULL); ttc.ca legend captured live; vehicle 4502 `EMPTY` while its arrival showed "Not Busy".
- Detours: 93 modification entities, 93 shapes, 14 detours; 2,523/2,523 selected trips in the static feed; 39/39 live links resolved; polylines validated against kept/skipped stops.
- Service changes: 46 records; `route` pipe-separated on multi-route entries, blank on 6; 34/40 alert-title matches.
- Freshness: surface timestamps advanced 30 s between snapshots 28 s apart with 5 to 7 s lag; alerts API `max-age=25`; CKAN `Age: 447`.
- Fetch costs: table in 6.6; `alerts.ttc.ca` conditional GET returned 304 in 0.05 s.
- User-Agent: 403 from `alerts`, `ntas`, `www`, `gtfsrt` hosts for `Python-urllib`; 200 for every custom string.
