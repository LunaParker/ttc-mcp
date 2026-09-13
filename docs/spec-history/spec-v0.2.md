# TTC network MCP server: specification v0.2

**Date:** 2026-09-13 | **Status:** decisions a to f accepted; ready to implement | **Supersedes:** v0.1 (kept as `ttc-mcp-spec-v0.1.md`)
**Companions:** `ttc-transit-data-sources-investigation.md` (source survey), `ttc-detours-tripmodifications-addendum.md` (detours), `ttc_transit_probe.py` (reference behaviour for most tools)

Scope: the core network model (stops, stations, routes, service hours, real-time arrivals, advisories) plus route geometry (item 1), accessibility state (item 3), crowding (item 4), detours (item 5), and service changes with feed status (item 6). Everything marked *decided* below is fixed behaviour for v1 of the server; section 8 lists what still has to be validated while implementing.

## 1. Purpose and principles

The server exists so that Claude (and any MCP client) can reason about the TTC network as it is right now, not as it was when a dataset was pasted in.

1. **Nothing baked in.** Static data is resolved through the City of Toronto CKAN API and re-downloaded when the resource ETag changes; real-time and advisory data is fetched from the TTC's own endpoints on short caches.
2. **Public stop numbers are the canonical identifier** in every tool input and output. Other ID spaces (Surface-GTFS internal ids, station legacy ids, UmoIQ tags) stay inside the server.
3. **Every answer carries provenance and freshness**: which feed, its header timestamp or HTTP date, the static feed version it was joined against, and a staleness verdict (section 6.5).
4. **Undocumented endpoints are fragile**: one adapter each, defensive parsing, and "unavailable" in the output rather than a crashed tool.
5. **Be a polite client**: a real User-Agent on every request (four TTC hosts 403 the default `Python-urllib` agent), conditional requests where supported, no polling faster than the data changes (section 4.3).

## 2. Architecture

```
 static layer (SQLite/DuckDB, rebuilt per board period)
   CKAN package_show -> merged GTFS zip   -> stops (hierarchy) / routes / trips / stop_times / calendar / shapes / pathways / levels
                     -> surface GTFS zip  -> crosswalk: stop_code <-> surface stop_id
   derived: stop_directions, route_patterns (with stop_sequence), pattern_shapes, route_service_hours, station_children,
            elevator_units (unit code -> nodes), route_series, route_service_level (from ttc.ca)
 real-time layer (in-memory, pollers)
   gtfsrt.ttc.ca/trips/modified_update     every 30 s   surface TripUpdates keyed by PUBLIC stop numbers; modifiedTrip links   <- arrivals
   bustime.ttc.ca/gtfsrt/vehicles          every 30 s   surface positions + occupancy_status (Surface ids; join by vehicle id)
   bustime.ttc.ca/gtfsrt/trips             every 30 s   parity check only (Surface ids via crosswalk)
   gtfsrt.ttc.ca/trips/subway              every 30 s   subway TripUpdates (platform ids)
   ntas.ttc.ca/api/ntas/get-next-train-time on demand   per platform, 10 s cache
   bustime.ttc.ca/bustime/map/getStopPredictions.jsp on demand  fallback when the poller is down
 advisory layer
   alerts.ttc.ca/api/alerts/list           every 60 s   closures, unplanned detours, RSZ, elevators; station/stop ids
   gtfsrt.ttc.ca/alerts/{all,accessibility} every 60 s  cause/effect, platform-level informed_entity
   gtfsrt.ttc.ca/trips/detour              every 60 s   TripModifications + Shape
   www.ttc.ca/ttcapi/routedetail/getallroutesandstopsalerts  every 10 min  serviceChanges
   www.ttc.ca/ttcapi/routedetail/get?id=R  daily        serviceLevel labels, branch metadata
 tool API (MCP)
```

### 2.1 Static layer

- Packages: `merged-gtfs-ttc-routes-and-schedules` (primary) and `surface-routes-and-schedules-for-bustime` (crosswalk only). Resolve URLs with `package_show`; detect change with `HEAD` + ETag. The CKAN API is cached 15 minutes upstream (`Cache-Control: max-age=900`, `Age` observed), so a new feed can take 15 minutes to become visible.
- Rebuild is one streaming pass over `stop_times.txt` (~7.7 M rows, about 2 minutes). Patterns store `(stop_sequence, stop_id)` pairs because 9,099 of 137,912 trips do not number stops from 1 and GTFS-RT selectors reference `stop_sequence`.
- **Board-period overlap (decided):** keep the outgoing and incoming feeds loaded together from the moment the new package is seen until the old `feed_end_date` passes. Trip ids in real-time feeds resolve against whichever feed contains them; detours whose trips resolve in neither are reported with `pattern: "unknown"` rather than dropped.

### 2.2 Real-time layer

- One poller per feed; results indexed by public stop number, route, and vehicle id.
- Arrivals come from `trips/modified_update` (public stop numbers, `modifiedTrip` links, `SKIPPED`/`NO_DATA` per stop). `bustime.ttc.ca/gtfsrt/trips` is only a parity check in tests and a fallback if the modified feed fails.
- Trip labels (route, direction, headsign, pattern) come from the static `trips` table; trips without a static row (`NEW`, negative ids) are labelled by route plus their own first and last stop names.
- Occupancy is joined from the vehicle feed by `vehicle.id` (present on 1,697 of 1,699 trip updates).

### 2.3 Advisory layer

- `alerts.ttc.ca` is primary (closures, RSZ, elevators with unit codes, unplanned detours); GTFS-RT alerts supply `cause`/`effect` and platform ids; service changes exist only in `getallroutesandstopsalerts.serviceChanges`.
- Station references in alerts are resolved with `stop_id OR stop_code` (section 3).

## 3. Identifier model

| Concept | Canonical value in tools | Internal joins |
|---|---|---|
| Stop / platform | public stop number (merged GTFS `stop_id`, equal to `stop_code` except 42 records) | Surface internal `stop_id` via `stop_code` for `bustime.ttc.ca` feeds |
| Station | merged GTFS `location_type 1` `stop_id` (34 stations carry a synthetic 43265..43298 with the legacy id in `stop_code`) | alerts use the legacy id for those 34: join `stop_id OR stop_code` (63/63 alert ids resolve) |
| Route | `route_short_name` (`504`, `1`, `300`) | equals `route_id` in the merged feed |
| Direction | `direction_id` plus headsign text | ttc.ca branch letters are labels only |
| Trip | GTFS `trip_id` | identical across TTC feeds; `NEW` trips have negative ids, no static row |
| Detour | `detourId` (grouping key) with member entity ids `detour_{n}_{detourId}:{dir}_{dir}_{pattern}_{route}_{dest}` | `modifiedTrip.modificationsId` on live trips |
| Elevator / escalator | unit code (`12D2L`, `16B4E`) | pathway nodes named `Elevator_{code}_{level}` where present |

## 4. Tools

Every output includes `meta: {source, as_of, static_feed, staleness}` (staleness per section 6.5). Inputs are JSON.

### 4.1 Core

| Tool | Input | Output |
|---|---|---|
| `find_stops` | `query` text, or `near {lat, lon, radius_m}`, optional `route`, `direction`, `mode` | stops with number, name, coordinates, wheelchair flag, routes/directions/headsigns served, parent station |
| `stop` | stop number | full record incl. station membership and active alerts |
| `station` | station id or name | platforms (lines/directions), entrances, elevators/escalators with live status, accessibility summary |
| `routes` | filters `series`, `overnight`, `type` | long name, series, ttc.ca service level, service hours by day type, overnight share |
| `route_stops` | route, direction, optional `pattern` | ordered stops per pattern with trip counts and headsigns |
| `service_hours` | route, date | runs that date? (calendar + exceptions), first/last trip |
| `arrivals` | stop number, optional route, `limit` | next vehicles with crowding inline (decision d) |
| `next_trains` | platform id or station id | per platform: line, direction text, next trains (minutes) |
| `alerts` | route / station / stop / `type` in {closure, rsz, detour, elevator, other} | normalised alerts with resolved stop/station objects |
| `feed_status` | none | per-source freshness and health with verdicts (decision f) |

### 4.2 Added items

| Tool | Input | Output |
|---|---|---|
| `route_shape` | route, direction, optional `pattern`, `format` = `polyline` (default, decision a) or `geojson`, optional `simplify_m` | canonical or requested pattern shape; `stops_between {from, to}` helper via `shape_dist_traveled` |
| `accessibility` | station or stop | platform flags, entrance flags, units with live status and pathway context, station summary (decision b), no trip-level flag (decision c) |
| `vehicles` | route or `vehicle_id` | position, bearing, speed, current stop, occupancy label + enum + confidence, trip/headsign |
| `detours` | optional route | grouped by `detourId`; reason may be `null` (decision e) |
| `service_changes` | optional route / date | board-period changes with parsed routes, dates, category, text, URL |

### 4.3 Polling and caching

| Source | Observed behaviour | Poll / cache |
|---|---|---|
| bustime + gtfsrt surface feeds (`trips`, `vehicles`, `trips/update`, `vehicles/position`, `trips/modified_update`) | shared header timestamp advancing 30 s per tick, 5 to 7 s behind wall clock | every 30 s |
| `gtfsrt.ttc.ca/trips/subway`, `/alerts/*` | timestamp = request time | 30 s (subway), 60 s (alerts) |
| `gtfsrt.ttc.ca/trips/detour` | no header timestamp; stable across 40 min | 60 s; record HTTP `Date` |
| `alerts.ttc.ca/api/alerts/list` | `max-age=25`, `ETag`, `Last-Modified`, body `lastUpdated`/`rszLastUpdated` | 60 s with `If-None-Match` |
| `ntas.ttc.ca` | no cache headers | 10 s per platform |
| `www.ttc.ca/ttcapi/*` | `no-cache` | service changes 10 min; route detail daily |
| CKAN `package_show` + zip `HEAD` | API cached 15 min upstream; zip has ETag | daily |

**User-Agent (decided):** send `ttc-mcp/<version> (+contact)` on every request. Verified: `alerts.ttc.ca`, `ntas.ttc.ca`, `www.ttc.ca`, `gtfsrt.ttc.ca` return 403 to `Python-urllib`; any custom string returns 200; `bustime.ttc.ca` does not filter.

### 4.4 Output schemas (normative)

```jsonc
// arrivals (decision d: crowding inline)
{ "stop": {"number": "15461", "name": "Cherry St at Front St East", "lat": 43.65284, "lon": -79.358092},
  "arrivals": [
    { "route": "504", "direction_id": "0", "headsign": "East - 504A King towards Distillery",
      "eta_min": 11.8, "eta_epoch": 1789330752, "vehicle_id": "4475",
      "relationship": "SCHEDULED",            // SCHEDULED | SKIPPED | NO_DATA | NEW
      "detour_id": null,                       // detourId when trip.modifiedTrip is present
      "crowding": {"label": "Not busy", "gtfs": "EMPTY", "confidence": "low"}  // see 6.3
    } ],
  "meta": {"source": "gtfsrt.ttc.ca/trips/modified_update", "as_of": "2026-09-13T20:24:00Z", "static_feed": "S1000538", "staleness": "fresh"} }

// next_trains
{ "station": {"id": "14572", "name": "Bloor-Yonge"},
  "platforms": [ {"platform": "13863", "name": "Bloor Station - Northbound Platform", "line": "1",
                  "direction_text": "Northbound to Finch", "next_min": [1, 3, 5], "source": "ntas"} ],
  "meta": {...} }

// route_shape (decision a: polyline default)
{ "route": "504", "direction_id": "0", "pattern": 1, "headsign": "East - 504B King towards Broadview Station",
  "shape_id": "shp-504-04", "trips_using": 485, "format": "polyline", "polyline": "a}wiG...", "length_m": 20112,
  "stops": [ {"seq": 1, "number": "2032", "dist_m": 0}, ... ], "meta": {...} }
// with format=geojson: "geometry": {"type": "LineString", "coordinates": [[lon, lat], ...]}

// accessibility (decisions b, c)
{ "station": {"id": "14572", "name": "Bloor-Yonge"},
  "summary": "accessible",                  // accessible | partially accessible | not accessible | unknown
  "platforms": [ {"platform": "13863", "name": "...", "wheelchair": 1} ],
  "entrances": [ {"id": "100254", "name": "Main Entrance", "lat": ..., "lon": ..., "wheelchair": 1} ],
  "units": [ {"code": "9Y1L", "kind": "elevator", "levels": ["Concourse Level", "Platform Level"],
              "status": "out_of_service", "alert_id": "76084", "text": "Queen: Elevator 9Y1L out of service between ...",
              "node_match": true} ],
  "step_free": {"verdict": "unknown", "basis": "text"},   // exact when all units on the path are coded nodes
  "meta": {...} }

// vehicles
{ "vehicles": [ {"vehicle_id": "4502", "route": "504", "trip_id": "108895010", "headsign": "...",
                 "lat": 43.65, "lon": -79.36, "bearing": 241, "speed_mps": 0.0,
                 "current_stop": "15461", "current_status": "INCOMING_AT",
                 "crowding": {"label": "Not busy", "gtfs": "EMPTY", "confidence": "low"}} ], "meta": {...} }

// detours (decision e: reason may be null)
{ "detours": [ { "detour_id": "695", "route": "112", "reason": {"title": "112 West Mall - Temporary route change due to sewer repairs",
                 "period": ["2026-06-09T07:00", "2026-10-01T07:00"], "url": "https://www.ttc.ca/..."},   // or null
                 "service_dates": ["20260913", "...", "20260920"],
                 "directions": [ { "direction_id": "0", "headsign": "South - 112 West Mall towards Kipling Station via Renforth Station",
                                   "leaves_after": {"number": "8067", "name": "Renforth Dr at Eglinton Ave West South Side"},
                                   "rejoins_at": {"number": "8641", "name": "Wellesworth Dr at Faversham Cres"},
                                   "skipped": [ {"number": "7773", "name": "Gentian Dr at Sagamore Cres", "lat": ..., "lon": ...} ],
                                   "path_polyline": "...", "path_length_m": 19476,
                                   "trips_affected": 67, "trips_live": 3, "entity_ids": ["detour_0_695:0_0_1209_112_..."] } ] } ],
  "meta": {"source": "gtfsrt.ttc.ca/trips/detour", "as_of": "<HTTP Date>", ...} }

// service_changes
{ "changes": [ {"id": "f4d1a81b-...", "routes": ["101"], "route_type": "Bus", "title": "101 Downsview Park - Temporary route change ...",
                "effective": ["2026-05-02T00:00:00Z", "2026-09-30T04:00:00Z"], "text": "...", "url": "https://www.ttc.ca/..."} ], "meta": {...} }

// feed_status (decision f)
{ "static": {"feed_version": "S1000538", "start": "20260906", "end": "20261031", "ckan_last_refreshed": "2026-09-04 18:03:35",
             "etag": "\"1788553243.713-...\"", "days_remaining": 48, "newer_package_seen": false, "verdict": "fresh"},
  "sources": [ {"name": "modified_update", "url": "...", "last_fetch": "...", "feed_timestamp": 1789331032, "signal": "feed_header",
                "lag_s": 5, "entities": 1699, "last_error": null, "verdict": "fresh"} ], "meta": {...} }
```

## 5. Data model summary (static, feed S1000538)

- `stops` 12,002 (9,512 boarding stops/platforms, 114 stations, 250 entrances, 2,126 pathway nodes); `routes` 235; `trips` 137,912; `shapes` 1,592 (1,035,699 points, `shape_dist_traveled` complete, 26 unused); `pathways` 4,086 edges (116 elevator edges); `levels` 324; patterns 1,418 (42 with more than one shape; no shape spans two patterns).

## 6. Decisions and their evidence, per item

### 6.1 Route geometry (item 1)

- **Canonical shape (decided):** per route-direction, the shape with the most trips; other patterns' shapes on request. Basis: 1 to 41 shapes per route-direction, each shape belongs to exactly one headsign, shapes refine patterns.
- **Output format (decision a, accepted):** encoded polyline by default, GeoJSON `LineString` when `format: "geojson"`; optional `simplify_m` Douglas-Peucker for map use. Basis: median 558 points per shape, max 3,071; the detour feed already speaks encoded polylines.
- **Stops between (decided):** by `shape_dist_traveled` from `stop_times`, never by projection.
- **BusTime `getRoutePoints.jsp` (decided):** not used; documented as a fallback pattern-geometry source for surface routes.
- Rail lines are covered (Line 1 canonical shape 633 points).

### 6.2 Accessibility (item 3)

- **Station summary (decision b, accepted):** computed from platforms and entrances only; `accessible` when every platform is 1, `not accessible` when every platform is 2, `partially accessible` otherwise, `unknown` when flags are 0/blank. The station record's own flag (1 on all 114 stations) is never used. Basis: platforms flagged 2 today include Old Mill, Islington, College, King, Museum.
- **Stop flags (decided):** raw `wheelchair_boarding` with GTFS meaning; 0 means unknown, never accessible (1,340 surface stops are 2, 194 are 0).
- **Trip flag (decision c, accepted):** `trips.wheelchair_accessible` is ignored. Basis: it is 2 on all 17,255 trips of the twelve streetcar routes despite a fully low-floor fleet.
- **Units (decided):** join alert unit codes to pathway nodes named `Elevator_{code}_{level}` / `Escalator_{code}_{level}` when present (95 elevator and 462 escalator coded nodes; 58 of 67 stations have coded nodes; 10 of 15 real alert codes matched today), else fall back to the alert `headerText`. Output carries `node_match` so the model knows which basis it has.
- **Step-free verdict (decided):** `exact` only when every unit on the path is a coded node with known status; otherwise `unknown` with `basis: "text"`. Line 5/6 alerts use `ELV02-B` style codes that do not appear in node names.
- **Sources (decided):** `alerts.ttc.ca` accessibility list is primary (unit code, routes, planned/unplanned); GTFS-RT `alerts/accessibility` supplies platform ids; ttc.ca duplicates are not fetched. Station ids join on `stop_id OR stop_code`.

### 6.3 Crowding (item 4)

- **Vocabulary (decided):** map `EMPTY -> Not busy`, `FEW_SEATS_AVAILABLE -> Busy`, `FULL -> Very busy`, absent -> `No info`; return `label`, `gtfs`, and `confidence`. Basis: the feed uses only those three values (841/206/14 of 1,253 reporting vehicles; 22 without the field; streetcars included) and ttc.ca's legend is exactly those four labels; the next 504 at Cherry St showed "Not Busy" while reporting `EMPTY`.
- **Confidence (decided):** `low` for `EMPTY` until section 8 item 1 is settled (all 25 vehicles on 504 and 155 of 162 streetcars reported `EMPTY` at 16:31, which smells like a default), `high` for `FEW_SEATS_AVAILABLE`/`FULL`, `none` for `No info`.
- **Inline on arrivals (decision d, accepted):** every arrival row carries `crowding` via the vehicle-id join.
- **Historical occupancy (decided):** out of scope; tool descriptions say so.

### 6.4 Detours (item 5)

- **Parsing (decided):** `gtfs-realtime-bindings >= 2.2.0` pinned, one adapter, JSON fallback (feed is protobuf-JSON).
- **Freshness (decided):** HTTP `Date` recorded as `as_of`; poll every 60 s.
- **Grouping (decided):** by `detourId`; directions listed inside; entity ids kept for traceability.
- **Reason (decision e, accepted):** joined from `serviceChanges` by route and date overlap (12 of 14 today); `reason: null` when nothing matches; detours are never suppressed.
- **Replacement buses (decided):** `306 Bus`/`506 Bus` entities are reported under route 306/506 with their "Replacement Bus" headsigns.
- **NEW trips (decided):** labelled by route plus first/last stop names.
- **Period boundary (decided):** dual-feed loading per 2.1; unresolved trips reported as `pattern: "unknown"`.

### 6.5 Service changes and feed status (item 6)

- **Service changes parsing (decided):** split `route` on `|`; when blank (6 of 46), extract the leading route number from `title`; use `routeType` (Subway appears there, never in `categoryName`); ignore `isDetour` (always false); dates from `effectiveStartDate`/`effectiveEndDate`.
- **GTFS-RT `MODIFIED_SERVICE` alerts (decided):** used only to attach `informed_entity` route ids when titles match (34 of 40 today); service changes remain the canonical list.
- **Freshness signals (decided):** `feed_status` reports, per source, the best signal and its type: `feed_header` (surface feeds share one timestamp ticking every 30 s with 5 to 7 s lag; subway and alert feeds stamp on request), `http_last_modified` (`alerts.ttc.ca`), `http_date` (detour feed, NTAS), `poll_time` otherwise. Static: `feed_version`, dates, CKAN `last_refreshed`, ETag; never the CKAN `refresh_rate` label.
- **Staleness thresholds (decision f, accepted):** `stale` when age exceeds 90 s (surface real-time), 60 s (subway TripUpdates and NTAS), 10 min (alerts), 30 min (detours); static is `expiring` within 3 days of `feed_end_date` with no newer package seen and `expired` after it. Verdicts appear in every tool's `meta.staleness` as `fresh | stale | expiring | expired | unavailable`.

## 7. Decision record

| # | Decision | Accepted behaviour (2026-09-13) |
|---|---|---|
| a | `route_shape` format | encoded polyline default; `format: geojson` and `simplify_m` optional |
| b | "Accessible station" | derived from platforms/entrances: accessible / partially accessible / not accessible / unknown |
| c | `trips.wheelchair_accessible` | ignored |
| d | Crowding on `arrivals` | inline per row: TTC label + GTFS enum + confidence |
| e | Detours without a matching reason | shown with `reason: null` |
| f | Staleness thresholds | 90 s / 60 s / 10 min / 30 min real-time; 3-day expiring window for static |

## 8. To validate during implementation

1. **`EMPTY` as default.** Sample the vehicle feed at a weekday peak (08:00 to 09:00): if buses show a spread across the three values while streetcars stay `EMPTY`, downgrade streetcar `EMPTY` to `No info`.
2. **Board-period transition.** Watch the first period change (late October 2026): confirm the dual-feed window resolves every live trip id and every detour trip id.
3. **Elevator node coverage.** After each static rebuild, log the share of active alert codes that match a node; if it drops below the current 10 of 15, widen the fallback text parsing.
4. **Detour feed behaviour on change.** Capture the feed before and after a detour ends to confirm entities disappear rather than linger with past `serviceDates`.
5. **`modified_update` completeness.** Confirm it stays a superset of `bustime.ttc.ca/gtfsrt/trips` (1,699 vs 1,707 entities today; the difference is timing) with a nightly parity count.
6. **Rate behaviour.** None of the undocumented hosts publish limits; log 403/429 responses per host and back off exponentially.

## 9. Implementation notes

- **Adapters:** one module per source (`ckan`, `gtfs_static`, `bustime_rt`, `gtfsrt_ttc`, `ntas`, `bustime_web`, `ttc_alerts`, `ttcapi`), each returning typed records plus a `SourceStatus`. Tools compose adapters and never touch HTTP directly.
- **Parity tests (nightly, against live data):** arrivals at 15461 from `modified_update` vs `getStopPredictions.jsp` vs crosswalked `bustime` protobuf (same vehicles and minutes within 1 min); NTAS vs `trips/subway` for 13863; detour skipped stops vs live `SKIPPED`/omitted stops on a linked trip; Blue Night classifier still flags 35 of 35.
- **Static build:** streaming, single pass, ~2 minutes; write to a temp database and swap atomically; keep the previous database for the overlap window.
- **Licensing:** CKAN data under the Open Government Licence - Toronto (attribution string in the server's `about` text); everything else undocumented, so tool descriptions say "best effort, from the TTC's own site endpoints".

## 10. Effort

Core server: ~3 days solo / ~1.5 days with Claude. Additions: item 1 ~2 h / ~1 h; item 3 ~1 h / ~30 min (plus ~1.5 days / ~half a day if the exact step-free verdict is pursued beyond the coded-node cases); item 4 ~1 h / ~30 min; item 5 ~3 to 4 h / ~1.5 h; item 6 ~2 h / ~1 h. Five additions without step-free routing: about **1.5 days solo, ~5 hours with Claude**.

## 11. Verification log (2026-09-13)

- Geometry: 137,912/137,912 trips with shapes; 1,592 shapes; 1,418 patterns; 42 multi-shape patterns; BusTime 504 patterns 4 with 36 to 39 embedded stops.
- Accessibility: station flag 1 on 114/114; platforms flagged 2 include Old Mill, Islington, College, King, Museum; 95/462/755 coded elevator/escalator/stair nodes; 10 of 15 alert codes matched; 63/63 alert station ids resolved via `stop_id OR stop_code`; streetcar trips 17,255 flagged 2.
- Crowding: 1,253/1,275 vehicles with status (841 EMPTY, 206 FEW_SEATS_AVAILABLE, 14 FULL); ttc.ca legend captured live; vehicle 4502 `EMPTY` while its arrival showed "Not Busy".
- Detours: 93 modification entities, 93 shapes, 14 detours; 2,523/2,523 selected trips in the static feed; 39/39 live links resolved; polylines validated against kept/skipped stops.
- Service changes: 46 records; `route` pipe-separated on multi-route entries, blank on 6; 34/40 alert-title matches.
- Freshness: surface timestamps advanced 30 s between snapshots 28 s apart with 5 to 7 s lag; alerts API `max-age=25`; CKAN `Age: 447`.
- User-Agent: 403 from `alerts`, `ntas`, `www`, `gtfsrt` hosts for `Python-urllib`; 200 for every custom string.
