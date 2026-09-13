# TTC network MCP server: specification v0.6

**Date:** 2026-09-13 | **Status:** decisions a to j accepted; ready to implement | **Supersedes:** v0.5 (history in `docs/spec-history/`)
**Companions (in this repository):** `docs/investigation-2026-09-13.md` (source survey), `docs/detours-addendum.md` (detours), `scripts/ttc_transit_probe.py` (reference behaviour for most tools)

**What changed in v0.6:** transport and hosting. The server speaks **streamable HTTP** and is deployed on **Google Cloud Run** (decision j, section 6.9): a FastMCP 3 application with the Google OAuth gate and email allowlist in-process, stateless JSON responses so idle clients do not keep an instance alive, `us-east1` for the always-free tier, and the daily static check moved out of the process into a Cloud Scheduler-driven Cloud Run Job that publishes the SQLite store to a bucket. Local development keeps a stdio mode.

**What changed in v0.5:** the Line 5 and 6 real-time gap is now designed for, not just described. The static layer gains scheduled departures per stop for the current service day, every arrivals-style answer carries a `basis` of `realtime` or `schedule`, and LRT platforms (and any stop whose real-time source is down) fall back to the schedule with that basis stated (decision i, section 6.8). `vehicles` declares Lines 5 and 6 unavailable, and `feed_status` reports NTAS prediction coverage per line.

**What changed in v0.4:** trip questions. A dry run of "I'm at Cherry St and Front St East, how do I get to Union?" and of a Line 1 closure between TMU and Union showed that the v0.3 tools could answer only if the model composed five or six calls correctly, and that a closure would stay invisible unless the model happened to ask for alerts. v0.4 adds `nearest_stops` (the bridge from a geocode supplied by the Google Maps MCP), `connections` (direct and one-transfer options with live and disruption overlay), `service_status`, disruption propagation into every stop, station, route, arrivals and next-train answer, and a station alias table. Geocoding itself stays outside this server. Details in section 6.7.

**What changed in v0.3:** the server no longer polls. Every real-time and advisory source is fetched on demand through a TTL cache with single-flight coalescing and a last-known-good fallback (decision g, section 6.6). The static GTFS keeps one scheduled check per day. Arrivals now use the cheapest per-stop endpoints; the 3-second `modified_update` feed is demoted to parity tests.

**Deployment model:** single-user, hosted by its maintainer on Cloud Run behind a Google OAuth gate (or run locally over stdio by another developer). The priority is the least bandwidth and the fewest requests to the TTC's servers, and near-zero marginal cloud cost, not throughput for many users. Latency of a few hundred milliseconds per tool call, plus a cold start of a few seconds after idle, is acceptable.

## 1. Purpose and principles

The server exists so that Claude (and any MCP client) can reason about the TTC network as it is right now, not as it was when a dataset was pasted in.

1. **Nothing baked in.** Static data is resolved through the City of Toronto CKAN API and re-downloaded only when the resource ETag changes.
2. **Fetch on demand, never in the background.** No poller runs while nobody is asking. The only scheduled work is one `HEAD` per day per static package, and it runs outside the serving process (a Cloud Run Job). Repeated questions inside a feed's update interval are served from a TTL cache.
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
   gtfsrt.ttc.ca/trips/subway (protobuf)               12 KB   0.06 s  30 s     next_trains epochs (optional)
   alerts.ttc.ca/api/alerts/list (ETag -> 304)         51 KB   0.07 s  25 s     alerts, accessibility, stop/station enrichment
   gtfsrt.ttc.ca/alerts/{all,accessibility} (protobuf) 40 KB   0.15 s  60 s     cause/effect, platform ids
   gtfsrt.ttc.ca/trips/detour (protobuf)               163 KB  0.10 s  5 min    detours, detoured-route set, detour flags on arrivals
   www.ttc.ca/ttcapi/routedetail/getallroutesandstopsalerts  80 KB 0.43 s 10 min  service_changes, detour reasons
   gtfsrt.ttc.ca/trips/modified_update                 574 KB  3.0 s   --       parity tests only (server-side generation cost)

 tool API (MCP)
```

### 2.1 Static layer

- **Where it runs (v0.6):** the daily check and the rebuild are a Cloud Run Job (`ttc-mcp-static`), triggered by Cloud Scheduler at 04:30 America/Toronto. The Job resolves both packages, compares ETags with `manifest.json` in the bucket, rebuilds on change into `static-<feed_version>.sqlite`, uploads it, and rewrites the manifest (which lists the active feed and, during a period change, the incoming one). The serving instance never builds anything: at cold start it reads the manifest and downloads the referenced database(s) to `/tmp` (in-memory on Cloud Run, so the memory limit must cover it; the build keeps the file compact, see 6.9). In local stdio mode the same code runs the check in-process on startup and every 24 h.
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
| Station alias | any name a rider or a TTC feed uses (`TMU`, `Dundas`, `Bloor`, `Yonge`, `Sheppard`, `St. Clair`) | `station_aliases` table built from station names, platform prefixes, surface-stop suffixes, alert `stopStart`/`stopEnd` names, plus a short manual list (section 6.7) |

## 4. Tools

Every output includes `meta: {source, feed_timestamp, fetched_at, cache, static_feed, staleness}` where `cache` is `live | ttl | stale_fallback` and `staleness` is per section 6.5. Inputs are JSON.

### 4.1 Core

| Tool | Input | Output | Fetches (beyond cache) |
|---|---|---|---|
| `find_stops` | `query` text (stop name, station name or alias, stop number), optional `route`, `direction`, `mode` | stops with number, name, coordinates, wheelchair flag, routes/directions/headsigns served, parent station | none (static) |
| `nearest_stops` | `lat`, `lon` (from the Google Maps MCP or any geocoder), `radius_m` (default 500), `limit` (default 10), `kind` = `any` / `stop` / `station` | stops or stations ordered by distance with walk minutes, routes/directions/headsigns, station membership, and active disruptions | alerts list for the disruption flags |
| `service_status` | none | one-call network picture: subway closures as station spans with shuttle info, suspended or detoured surface routes, reduced speed zones, elevator outages by station, counts and `as_of` | alerts list + detour feed |
| `connections` | `from` and `to`, each a stop number, a station id or alias, or `{lat, lon}`; options `max_transfers` (0 or 1, default 1), `walk_m` (default 400), `depart_now` (default true), `limit` (default 4) | ranked options with legs (walk / ride / shuttle), board and alight stops, stop counts, typical minutes, next departures at the first board stop, and every disruption touching a leg | arrivals sources for the first leg; alerts and detour feed for the overlay |
| `stop` | stop number | full record incl. station membership and `disruptions[]` (closures on the station named by the stop, detours skipping it, route-level alerts) | alerts list (51 KB / 304) + detour feed |
| `station` | station id, name or alias | platforms, entrances, units with live status, accessibility summary, `service` per line (`normal` / `suspended` / `reduced` with alert and shuttle info) | alerts list |
| `routes` | filters `series`, `overnight`, `type` | long name, series, service level, service hours by day type, overnight share | none |
| `route_stops` | route, direction, optional `pattern` | ordered stops per pattern with trip counts and headsigns; each stop carries `status` (`open` / `closed` / `skipped_by_detour`) | alerts list + detour feed |
| `service_hours` | route, date | runs that date? (calendar + exceptions), first/last trip | none |
| `scheduled_departures` | stop number or platform id, optional route, `from` time (default now), `limit` | timetable departures for the current service day (handles times past 24:00 and the 00:00 to 04:00 window belonging to the previous service day), with trip, headsign and pattern | none (static) |
| `arrivals` | stop number, optional route, `limit` | next vehicles with crowding inline (decision d) and detour flag, plus `disruptions[]` for the stop's routes and for the station named by the stop; each row carries `basis` = `realtime` or `schedule`, and scheduled departures fill in whenever no real-time prediction exists (decision i) | per-stop XML + vehicles (or NTAS for rail platforms); detour feed; trips only if the route is detoured; alerts list; static `stop_times` for the schedule basis |
| `next_trains` | platform id, station id or alias | per platform: line, direction text, next trains (minutes) with `basis`, and `service` = `normal` / `suspended` (with the closure alert and shuttle info) / `no_prediction` (with `scheduled_next_min` from the timetable) | NTAS per platform (+ subway feed for epochs on request) + alerts list + static `stop_times` |
| `alerts` | route / station / stop / `type` in {closure, rsz, detour, elevator, other} | normalised alerts with resolved stop/station objects | alerts list + GTFS-RT alerts |
| `feed_status` | optional `probe: true` | per-source cache state and freshness; with `probe`, one live request per source | none, or one per source |

### 4.2 Added items

| Tool | Input | Output | Fetches |
|---|---|---|---|
| `route_shape` | route, direction, optional `pattern`, `format` = `polyline` (default, decision a) or `geojson`, optional `simplify_m` | canonical or requested pattern shape; `stops_between {from, to}` via `shape_dist_traveled` | none |
| `accessibility` | station or stop | platform flags, entrance flags, units with live status and pathway context, station summary (decision b); trip flag ignored (decision c) | alerts list (+ GTFS-RT accessibility for platform ids) |
| `vehicles` | route or `vehicle_id` | position, bearing, speed, current stop, occupancy label + enum + confidence, trip/headsign; for routes 1, 2, 4, 5, 6 returns `unavailable` with the reason (no positions are published for rail) | vehicles (+ trips for headsigns when the trip is not static) |
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

// nearest_stops (input from a geocoder; this server does not geocode)
{ "origin": {"lat": 43.6527, "lon": -79.3581},
  "results": [ {"number": "15460", "name": "Cherry St at Front St East", "distance_m": 29, "walk_min": 0,
                "kind": "stop", "station": null,
                "served": [ {"route": "504", "direction_id": "1", "headsign": "West - 504A King towards Dundas West Station"} ],
                "disruptions": [] },
               {"id": "15772", "name": "Union", "aliases": ["Union Station"], "distance_m": 1914, "walk_min": 31, "kind": "station",
                "lines": ["1"], "disruptions": [ {"type": "closure", "line": "1", "span": ["TMU", "Union"], "alert_id": "..."} ] } ],
  "meta": {...} }

// service_status
{ "closures": [ {"line": "1", "name": "Yonge-University", "alert_id": "75803", "alert_type": "Planned",
                 "span": {"from": {"id": "43272", "name": "St Clair"}, "to": {"id": "14589", "name": "College"},
                          "stations": ["St Clair", "Summerhill", "Rosedale", "Bloor-Yonge", "Wellesley", "College"]},
                 "direction": "Both ways", "active": [["2026-09-14T23:59-04:00", "2026-09-15T03:30-04:00"], "..."],
                 "active_now": false, "shuttle": "Will Operate", "text": "Line 1 Yonge-University: There will be no subway service ..."} ],
  "surface": [ {"route": "52D", "effect": "No Service", "direction": "Westbound", "from": "...", "to": "...", "text": "..."},
               {"route": "112", "effect": "Detour", "detour_id": "695", "skipped_stops": 7, "reason": "Temporary route change due to sewer repairs"} ],
  "reduced_speed_zones": 16, "elevators_out": {"Woodbine": ["12D2L"], "Queen": ["9Y1L"]},
  "meta": {...} }

// connections (decision h)
{ "from": {"lat": 43.6527, "lon": -79.3581}, "to": {"station": "15772", "name": "Union"},
  "options": [
    { "rank": 1, "transfers": 0, "typical_min": 20, "walk_m": 160, "disruptions": [],
      "legs": [ {"mode": "walk", "to": {"number": "15246", "name": "Mill St at Cherry St West Side"}, "distance_m": 160, "min": 3},
                {"mode": "ride", "route": "121", "direction_id": "1", "headsign": "West - 121 Esplanade-River Towards Union Station",
                 "board": {"number": "15246", "name": "Mill St at Cherry St West Side"},
                 "alight": {"number": "16754", "name": "1 Front St West - Union Station"},
                 "stops": 12, "typical_min": 17, "next": [ {"eta_min": 15, "vehicle_id": "8520", "crowding": {"label": "Not busy"}} ]} ] },
    { "rank": 2, "transfers": 1, "typical_min": 17, "walk_m": 29,
      "disruptions": [ {"type": "closure", "line": "1", "span": ["TMU", "Union"], "affects": "leg 3", "shuttle": "Will Operate"} ],
      "legs": [ {"mode": "walk", "to": {"number": "15460", "name": "Cherry St at Front St East"}, "distance_m": 29, "min": 0},
                {"mode": "ride", "route": "504", "direction_id": "1", "headsign": "West - 504A King towards Dundas West Station",
                 "board": {"number": "15460"}, "alight": {"number": "15637", "name": "King St West at Yonge St West Side - King Station"}, "stops": 9, "typical_min": 12},
                {"mode": "shuttle", "line": "1", "from": {"station": "14586", "name": "King"}, "to": {"station": "15772", "name": "Union"},
                 "note": "subway suspended TMU to Union; shuttle buses operating per alert 12345"} ] } ],
  "meta": {...} }

// disruptions[] element (shared by stop, station, route_stops, arrivals, next_trains, connections)
{ "type": "closure | suspension | detour | rsz | elevator | delay | other", "source": "alerts.ttc.ca | gtfsrt alerts | detour feed",
  "alert_id": "75803", "route": "1", "stations": ["14588", "14587", "14586", "15772"], "stops": [], "direction": "Both ways",
  "active_now": true, "period": ["...", "..."], "shuttle": "Will Operate", "text": "...", "url": "..." }

// next_trains: schedule fallback on an LRT platform (decision i)
{ "station": {"id": "16815", "name": "Driftwood"},
  "platforms": [ {"platform": "16317", "name": "Driftwood Station - Eastbound Platform", "line": "6", "direction_text": "Eastbound to Finch West",
                  "next_min": [], "basis": "schedule", "service": "no_prediction", "scheduled_next_min": [4, 12, 20],
                  "note": "NTAS returned no prediction for this platform; times are from the timetable"} ],
  "meta": {"source": "ntas + static stop_times", ...} }

// next_trains with propagation
{ "station": {"id": "14586", "name": "King"},
  "platforms": [ {"platform": "13814", "name": "King Station - Southbound Platform", "line": "1", "direction_text": "Southbound to Vaughan Metropolitan Centre via Union",
                  "next_min": [], "service": "suspended", "disruption": {"type": "closure", "alert_id": "...", "span": ["TMU", "Union"], "shuttle": "Will Operate"}} ],
  "meta": {...} }

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
4. **Disruption propagation.** A shared `disruptions[]` element is attached to `stop`, `station`, `route_stops` (as per-stop `status`), `arrivals`, `next_trains` and `connections`. Rules: a station is `closed` when an active closure's station list contains it; a stop is `skipped_by_detour` when an active detour's skipped span contains it; a surface stop whose name ends in "- X Station" inherits X's closure as a disruption (so arrivals at "King St West at Yonge St - King Station" mention that King is closed); `next_trains` reports `service: "suspended"` when the platform's station is closed now, `no_prediction` when NTAS returns nothing and no closure is known, `normal` otherwise. An empty NTAS list is never presented as "no trains".
5. **`connections`** (the centrepiece). Static search over patterns, then a live and disruption overlay:
   - Origin and destination sets: the stops within `walk_m` (default 400) of each end, or the given stop, or every platform and surface stop belonging to the given station (via `parent_station` and the alias/suffix membership), including bus stops within 150 m of the station record.
   - Direct options: every (route, direction, pattern) serving an origin stop where a destination stop appears later in the same pattern.
   - One-transfer options: for each origin pattern, each downstream stop that is a transfer point (same stop, same station, or within 150 m) to a pattern that reaches a destination stop later. Limited to patterns running on today's service id and within the route's service span at the departure time (Blue Night routes only overnight).
   - Typical minutes per leg from `pattern_run_times` (median scheduled minutes between the two stops), walking legs as above, a fixed 4-minute transfer penalty.
   - Ranking: transfers, then total typical minutes including walking, then stop count. Return `limit` options (default 4).
   - Live overlay: `arrivals` for the first ride leg's board stop (cheap per-stop XML; NTAS for platforms); crowding inline.
   - Disruption overlay: drop ride legs that traverse a closed station on a subway line and, when the closure has `shuttleType` "Will Operate", replace them with a `shuttle` leg between the closure's end stations (typical minutes unknown, stated as such); drop legs whose board or alight stop is skipped by a detour and re-run with the nearest served stop; annotate RSZ and delays without dropping. Every retained option lists the disruptions it touches.
   - Out of scope: timetable-exact itineraries (a RAPTOR planner over `stop_times` with real-time is a possible v1.x addition); walking routes (straight-line only).
6. **Station aliases.** `station_aliases` maps every station id to: its name; its platform name prefixes; the X of every "- X Station" surface-stop suffix within 300 m of the station; the `stopStart`/`stopEnd` names seen in alerts; and a manual list: Dundas -> TMU, Bloor and Yonge -> Bloor-Yonge, Sheppard -> Sheppard-Yonge, "St. Clair" -> St Clair, "St. Andrew" -> St Andrew, VMC -> Vaughan Metropolitan Centre, Kennedy RT -> Kennedy. GO-station suffixes are recorded as `external: "GO"` and never resolve to a TTC station. `find_stops`, `station`, `next_trains` and `connections` accept any alias.

### 6.8 Lines 5 and 6 (Eglinton and Finch West LRT) — coverage audit, 2026-09-13 21:00

**Static (merged GTFS):** covered. Line 5 has 50 platforms under 25 station records, Line 6 has 36 under 18; all platforms are `wheelchair_boarding 1`; the stations carry entrances (19 and 7) and pathway nodes (121 and 71, including 28 and 12 elevator nodes). Interchanges share one station record with the subway, so the transfer graph needs nothing special: Eglinton (43274) holds Line 1 and Line 5 platforms plus nine bus routes, Cedarvale (43298) Lines 1 and 5, Kennedy (43295) Lines 2 and 5, Finch West (15205) Lines 1 and 6. Service spans are normal all-day service (Line 5 05:40 to 02:09, Line 6 04:55 to 02:06 on weekdays). Two quirks: `route_type` is 0 (tram), so mode must come from the explicit rail-line list {1, 2, 4, 5, 6} rather than from GTFS; and one Line 5 platform, `16081` "Kennedy Station - Subway Platform", has no `parent_station` (the four terminal platforms are named "- Subway Platform"; the other three do have parents). The elevator and escalator nodes at these stations use a different code scheme (`Elevator_ELVF_Street`, `Elevator_E3_Concourse`, `Escalator_ESC09_Upper`) from the subway (`12D2L`), and the one live Line 5 elevator alert today (Laird, `ELV02-B`) matches none of them, so unit joins fall back to alert text on both lines.

**Real-time:** partial, and the gap is real.
- NTAS covers Line 5 fully (every platform tested returned two or three upcoming trains) and Line 6 only in part: at 21:00 on a Sunday, with Line 6 in service until 02:06, only the Finch West terminal platform returned predictions; Albion, Driftwood, Westmore, Emery and Humber College all returned an empty `nextTrains` string. Under the propagation rules this is reported as `no_prediction`, never as "no trains".
- The subway TripUpdates feed carries only routes 1, 2 and 4 (37, 22 and 4 entities today); there are no trip updates for Lines 5 or 6.
- The surface GTFS-RT feeds carry no route 5 or 6 vehicles or trips, so there are no positions and no crowding for the LRTs; `arrivals` on an LRT platform reports crowding as `No info` and `vehicles` returns nothing for routes 5 and 6.
- The BusTime per-stop XML rejects LRT platforms ("Stop not found"), so `arrivals` for a Line 5/6 platform is NTAS-only.

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

**Cold start:** container start plus manifest read plus one regional bucket download of the database (target under 200 MB: patterns, run times, scheduled departures and shapes as compact rows rather than raw `stop_times`; measured size is a section 8 item). Expect 3 to 8 seconds on the first call after idle, then warm; `--cpu-boost` helps. If the download proves slow, the alternative is a gen2 GCS volume mount of the bucket with SQLite opened read-only (`immutable=1`), which trades cold-start time for slower first reads per page.

**Cost (free-tier figures to re-verify at deploy time):** Cloud Run's monthly free tier (about 2 million requests, 180,000 vCPU-seconds and 360,000 GiB-seconds) covers a single-user service that bills only during requests plus a two-minute daily Job; a sub-gigabyte bucket sits inside the 5 GB always-free storage in US regions; Cloud Scheduler allows three free jobs; Artifact Registry storage for one image is within its free allowance; Firestore reads for OAuth are negligible. The only items that could bill are egress on large TTC fetches (kilobytes to hundreds of kilobytes per call) and any long-lived instance, which the stateless mode prevents.

**Threat model:** the server holds no third-party credential; the only secrets are its own gate's. Read-only public data behind the email gate is the safe class of connector. Nothing in the tools can act on the TTC or on the user's accounts.

**Local mode:** `ttc-mcp --transport stdio` uses the same code with the static check in-process (startup plus every 24 h while running) and the cache directory from `TTC_MCP_CACHE`; the Google gate is off in stdio mode.

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
2. **Board-period transition.** Watch the first period change (late October 2026): confirm the dual-feed window resolves every live trip id and every detour trip id.
3. **Elevator node coverage.** After each static rebuild, log the share of active alert codes that match a node; if it drops below the current 10 of 15, widen the fallback text parsing.
4. **Detour feed behaviour on change.** Capture the feed before and after a detour ends to confirm entities disappear rather than linger with past `serviceDates`.
5. **`modified_update` generation time.** Re-measure monthly; if it drops under 0.5 s it becomes a candidate arrivals source again (public numbers, no crosswalk).
6. **TTL tuning.** Log cache hit rates per source for the first weeks; if bursts routinely exceed a TTL (many `live` fetches within one tick), lengthen that TTL toward the feed's true interval, never shorter.
7. **NTAS during a closure.** The College to St Clair closure runs 23:59 to 03:30 on the nights of Sept 14 to 18; capture NTAS for College (13807/13808) and Bloor-Yonge (13863/13864) during that window to learn whether it returns an empty list, stale minutes, or an error, and set the `suspended` logic accordingly.
8. **Connections quality.** Run 20 origin/destination pairs across the city (downtown, Scarborough, Etobicoke, North York, an overnight case) and compare the top option with the TTC trip planner; tune `walk_m`, the 150 m transfer radius (the four stations without nearby surface stops need the membership rule), and the transfer penalty.
9. **Alias completeness.** After each static rebuild, list surface-stop suffixes and alert station names that resolve to no station; add manual aliases as needed and keep GO stations external.
10. **Rate behaviour.** None of the undocumented hosts publish limits; log 403/429 per host and back off exponentially; single-flight already prevents self-inflicted bursts.

## 9. Implementation notes

- **Adapters:** one module per source (`ckan`, `gtfs_static`, `bustime_rt`, `bustime_web`, `gtfsrt_ttc`, `ntas`, `ttc_alerts`, `ttcapi`), each returning typed records plus a `SourceStatus`. Tools compose adapters through the cache and never touch HTTP directly.
- **Cache module:** `CacheEntry {key, body, parsed, fetched_at, feed_timestamp, etag, last_modified, error}`; `get(key, ttl, fetch, max_stale)` implements TTL, single-flight (one in-flight future per key), conditional GET when a validator exists, and stale-if-error with the age reported back to the tool. Memory footprint is small: the largest cached body is the 568 KB trip feed.
- **Connections planner:** precompute per pattern the ordered stop list with cumulative median minutes; index stop -> [(pattern, position)]; index station -> member stops (parent, suffix, 150 m rule) and stop -> nearby stops (150 m grid). Query: origin set x destination set through the stop index gives direct hits in O(patterns at origin); one-transfer hits enumerate downstream stops of each origin pattern (average 33) and probe the destination index. Apply the running-now filter, score, overlay, and return. Expected cost: tens of milliseconds static plus one arrivals fetch.
- **Static build:** streaming, single pass, ~2 minutes, in the Cloud Run Job; write to a temporary database, upload, then rewrite the manifest; the manifest keeps the previous database listed for the overlap window; a service that starts with no manifest yet answers static tools with an explicit "static store not built yet" error rather than blocking.
- **Entry points:** `ttc-mcp` (server; `--transport http|stdio`, default http on `$PORT`) and `ttc-mcp-build` (the Job). One Dockerfile (`python:3.12-slim`, `uv sync --frozen`), one image, two commands.
- **Parity tests (nightly, live):** arrivals at 15461 from the per-stop XML vs crosswalked `bustime` protobuf vs `modified_update` (same vehicles and minutes within 1 min); NTAS vs `trips/subway` for 13863; detour skipped stops vs live omitted stops on a linked trip; Blue Night classifier still flags 35 of 35.
- **Licensing:** CKAN data under the Open Government Licence - Toronto (attribution string in the server's `about` text); everything else undocumented, so tool descriptions say "best effort, from the TTC's own site endpoints".

## 10. Effort

Core server: ~3 days solo / ~1.5 days with Claude; the on-demand model removes the poller supervision but adds the cache module, so the total is unchanged. Cloud deployment following the existing gateway recipe (OAuth client, secrets, service account, bucket, Job, Scheduler, first deploy, connector re-adds): ~4 h solo / ~2 h with Claude, most of it console clicks the recipe already documents. Additions: item 1 ~2 h / ~1 h; item 3 ~1 h / ~30 min (plus ~1.5 days / ~half a day for an exact step-free verdict beyond coded-node cases); item 4 ~1 h / ~30 min; item 5 ~3 to 4 h / ~1.5 h; item 6 ~2 h / ~1 h. Five additions without step-free routing: about **1.5 days solo, ~5 hours with Claude**. v0.4 additions: `connections` ~1 day / ~3 to 4 h; `nearest_stops` ~1 h / ~30 min; `service_status` plus propagation ~3 h / ~1.5 h; alias table ~1.5 h / ~45 min. v0.5: `scheduled_departures` and the schedule basis ~2 h / ~1 h (the `stop_times` table already exists in the static build). About **2 days solo, ~7 to 8 hours with Claude** on top of the earlier items.

## 11. Verification log (2026-09-13)

- Geometry: 137,912/137,912 trips with shapes; 1,592 shapes; 1,418 patterns; 42 multi-shape patterns; BusTime 504 patterns 4 with 36 to 39 embedded stops.
- Accessibility: station flag 1 on 114/114; platforms flagged 2 include Old Mill, Islington, College, King, Museum; 95/462/755 coded elevator/escalator/stair nodes; 10 of 15 alert codes matched; 63/63 alert station ids resolved via `stop_id OR stop_code`; streetcar trips 17,255 flagged 2.
- Crowding: 1,253/1,275 vehicles with status (841 EMPTY, 206 FEW_SEATS_AVAILABLE, 14 FULL); ttc.ca legend captured live; vehicle 4502 `EMPTY` while its arrival showed "Not Busy".
- Detours: 93 modification entities, 93 shapes, 14 detours; 2,523/2,523 selected trips in the static feed; 39/39 live links resolved; polylines validated against kept/skipped stops.
- Service changes: 46 records; `route` pipe-separated on multi-route entries, blank on 6; 34/40 alert-title matches.
- Freshness: surface timestamps advanced 30 s between snapshots 28 s apart with 5 to 7 s lag; alerts API `max-age=25`; CKAN `Age: 447`.
- Fetch costs: table in 6.6; `alerts.ttc.ca` conditional GET returned 304 in 0.05 s.
- User-Agent: 403 from `alerts`, `ntas`, `www`, `gtfsrt` hosts for `Python-urllib`; 200 for every custom string.
- Trip dry run: nearest stops to Cherry St and Front St East as listed in 6.7; 121 westbound ends at stop 16754 "1 Front St West - Union Station"; live arrivals 121 in 15/24 min, 504A in 15 min; closure alert shape and per-night GTFS-RT entities as described; 106 suffix stops over 51 names; 418 surface stops within 150 m of a station, 4 stations with none; 1,418 patterns averaging 33 stops.
