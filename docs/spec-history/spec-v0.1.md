# TTC network MCP server: specification v0.1

**Date:** 2026-09-13 | **Status:** draft for review | **Companions:** `ttc-transit-data-sources-investigation.md` (source survey), `ttc-detours-tripmodifications-addendum.md` (detours), `ttc_transit_probe.py` (reference behaviour for most tools)

Scope in this version: the core network model (stops, stations, routes, service hours, real-time arrivals, advisories) plus the five additions approved on 2026-09-13: **route geometry (item 1), accessibility state (item 3), crowding (item 4), detours (item 5), service changes and feed status (item 6)**. Every ambiguity found while investigating those five is listed in section 6 with either a resolution backed by data or an open decision.

## 1. Purpose and principles

The server exists so that Claude (and any MCP client) can reason about the TTC network as it is right now, not as it was when a dataset was pasted in. Principles:

1. **Nothing baked in.** Static data is resolved through the City of Toronto CKAN API and re-downloaded when the resource ETag changes; real-time and advisory data is fetched from the TTC's own endpoints on short caches.
2. **Public stop numbers are the canonical identifier** in every tool input and output. Other ID spaces (Surface-GTFS internal ids, station ids, UmoIQ tags) stay internal to the server.
3. **Every answer carries provenance and freshness**: which feed, its header timestamp or Last-Modified, and the static feed version it was joined against.
4. **Undocumented endpoints are treated as fragile**: isolated behind one adapter each, parsed defensively, and reported as "unavailable" rather than crashing a tool.
5. **Be a polite client**: real User-Agent, conditional requests where supported, no polling faster than the data changes (section 4.3).

## 2. Architecture

```
                 +--------------------- static layer (SQLite/DuckDB, rebuilt per board period) ---------------------+
 CKAN package_show  -> merged GTFS zip  -> stops (hierarchy) / routes / trips / stop_times / calendar / shapes / pathways
                    -> surface GTFS zip -> crosswalk: stop_code <-> surface stop_id
                    derived: stop_directions, route_patterns (with stop_sequence), route_service_hours, station_children,
                             pattern_shapes, elevator_nodes (unit codes), route_series
                 +--------------------- real-time layer (in-memory, pollers) ------------------------------------------+
 bustime.ttc.ca/gtfsrt/{trips,vehicles}   every 30 s   (surface; Surface-GTFS ids; occupancy on vehicles)
 gtfsrt.ttc.ca/trips/modified_update      every 30 s   (surface; PUBLIC stop numbers; modifiedTrip links)   <- preferred for arrivals
 gtfsrt.ttc.ca/trips/subway               every 30 s   (subway trip updates; platform ids)
 ntas.ttc.ca/api/ntas/get-next-train-time on demand    (per platform; 10 s cache)
 bustime.ttc.ca/bustime/map/getStopPredictions.jsp  on demand (parity/fallback; public numbers)
                 +--------------------- advisory layer ----------------------------------------------------------------+
 alerts.ttc.ca/api/alerts/list            every 60 s   (closures, detour alerts, RSZ, elevators; station/stop ids)
 gtfsrt.ttc.ca/alerts/{all,accessibility} every 60 s   (structured cause/effect, platform ids)
 gtfsrt.ttc.ca/trips/detour               every 60 s   (TripModifications + Shape)
 www.ttc.ca/ttcapi/routedetail/getallroutesandstopsalerts  every 10 min (serviceChanges)
 www.ttc.ca/ttcapi/routedetail/get?id=R   daily        (serviceLevel labels, branch metadata)
                 +--------------------- tool API (MCP) ------------------------------------------------------------------+
```

### 2.1 Static layer

- Source packages: `merged-gtfs-ttc-routes-and-schedules` (primary) and `surface-routes-and-schedules-for-bustime` (crosswalk only). Resolve URLs with `package_show`; detect change with `HEAD` + ETag; the CKAN API itself is cached 15 minutes upstream (`Cache-Control: max-age=900`, `Age` header seen), so a change can take up to 15 minutes to become visible.
- Rebuild is one streaming pass over `stop_times.txt` (~7.7 M rows, about 2 minutes) producing the derived tables above. Keep the previous feed until the new one's `feed_start_date` arrives; the TTC publishes about a week ahead (this period: published Sept 4 for Sept 6).
- Patterns store `(stop_sequence, stop_id)` pairs, not positions: 9,099 of 137,912 trips do not number stops from 1, and GTFS-RT stop selectors reference `stop_sequence`.

### 2.2 Real-time layer

- One poller per feed; results indexed by public stop number, by route, and by vehicle id. Trip labels (route, direction, headsign) come from the static `trips` table; trips absent from it (NEW/ADDED extras, negative ids) are labelled by route only.
- Arrivals use `trips/modified_update` first because it is keyed by public stop numbers and carries detour links; `bustime.ttc.ca/gtfsrt/trips` is the cross-check (same timestamps, Surface ids via the crosswalk).
- Subway/LRT platforms use NTAS on demand plus the subway TripUpdates feed for arrival epochs.

### 2.3 Advisory layer

- `alerts.ttc.ca` is the primary human-facing source (closures, RSZ, elevators, unplanned detours) and carries `lastUpdated`, `rszLastUpdated`, `ETag`, `Last-Modified`, and `Cache-Control: max-age=25`.
- GTFS-RT alert feeds give the same items with `cause`/`effect` and platform-level `informed_entity`.
- Service changes (board-period route changes) exist only in `getallroutesandstopsalerts.serviceChanges`.

## 3. Identifier model

| Concept | Canonical value in tools | Internal joins |
|---|---|---|
| Stop / platform | public stop number (merged GTFS `stop_id`, which equals `stop_code` except 42 records) | Surface internal `stop_id` via `stop_code` for `bustime.ttc.ca` feeds; UmoIQ not used |
| Station | merged GTFS `location_type 1` record: `stop_id` (34 stations carry a synthetic `stop_id` 43265..43298 with the legacy id in `stop_code`) | **alerts reference stations by the legacy id**: 16 of 63 alert ids today only match `stop_code`. Join on `stop_id OR stop_code`. |
| Route | `route_short_name` (`504`, `1`, `300`) | `route_id` is identical in the merged feed |
| Direction | GTFS `direction_id` plus headsign text | ttc.ca `compassDirection`/branch letters are labels only |
| Trip | GTFS `trip_id` | same in all TTC feeds; NEW trips have negative ids and no static row |
| Detour | detour feed entity id (`detour_{n}_{detourId}:{dir}_{dir}_{pattern}_{route}_{dest}`), grouped by `detourId` | `modifiedTrip.modificationsId` on live trips |
| Elevator / escalator | unit code (`12D2L`, `16B4E`) | pathway node names `Elevator_{code}_{level}` where present (section 6.2) |

## 4. Tools

Inputs are JSON; outputs always include `{"source": ..., "as_of": ISO8601, "static_feed": "S1000538"}`.

### 4.1 Core

| Tool | Input | Output | Source |
|---|---|---|---|
| `find_stops` | `query` text, or `near {lat, lon, radius_m}`, optional `route`, `direction`, `mode` | stops with number, name, coordinates, wheelchair flag, routes/directions/headsigns served, parent station | static |
| `stop` | stop number | full stop record incl. station membership and active alerts | static + advisory |
| `station` | station id or name | platforms (with lines/directions), entrances (coordinates, wheelchair), elevators/escalators with live status, accessibility summary (6.2) | static + advisory |
| `routes` | filters: `series`, `overnight`, `type` | list with long name, series, service level (ttc.ca), service hours by day type, overnight share | static + ttc.ca |
| `route_stops` | route, direction, optional `pattern` | ordered stops per pattern with trip counts and headsigns | static |
| `service_hours` | route, date | whether it runs that date (calendar + exceptions), first/last trip | static |
| `arrivals` | stop number, optional route | next vehicles: route, headsign, minutes, epoch, vehicle id, occupancy label, detour link, relationship (SCHEDULED/SKIPPED/NEW) | modified_update (+ NTAS for rail) |
| `next_trains` | platform id or station id | per platform: line, direction text, next trains (minutes) | NTAS + subway TripUpdates |
| `alerts` | route / station / stop / type (`closure`, `rsz`, `detour`, `elevator`) | normalised alerts with resolved stop/station objects, period, severity, direction, RSZ metrics | alerts.ttc.ca + GTFS-RT alerts |
| `feed_status` | none | section 4.4 | all |

### 4.2 Added items

| Tool | Input | Output | Notes |
|---|---|---|---|
| `route_shape` (item 1) | route, direction, optional `pattern`, `format` (`polyline` default or `geojson`), optional `simplify_m` | the pattern's canonical shape; `stops_between(a, b)` helper uses `shape_dist_traveled` | 6.1 |
| `accessibility` (item 3) | station or stop | platform-level wheelchair flags, entrance flags, elevator/escalator units with live status and pathway context, step-free verdict | 6.2 |
| `vehicles` (item 4) | route, or `vehicle_id` | position, bearing, speed, current stop, occupancy (TTC label + GTFS enum), trip/headsign | 6.3 |
| `detours` (item 5) | optional route | detours grouped by `detourId`: route/direction/headsign, leave/rejoin stops, skipped stops with coordinates, path polyline, service dates, live trip count, reason + URL | 6.4 |
| `service_changes` (item 6) | optional route / date | board-period changes with routes, dates, category, text, URL | 6.5 |
| `feed_status` (item 6) | none | per-source freshness and health | 6.5 |

### 4.3 Polling and caching

| Source | Observed update behaviour | Poll / cache |
|---|---|---|
| `bustime.ttc.ca/gtfsrt/*`, `gtfsrt.ttc.ca/trips/update`, `/vehicles/position`, `/trips/modified_update` | shared header timestamp; advanced by 30 s between two snapshots 28 s apart; 5 to 7 s behind wall clock | poll every 30 s |
| `gtfsrt.ttc.ca/trips/subway`, `/alerts/*` | header timestamp equals request time (generated on demand) | poll every 30 s (subway), 60 s (alerts) |
| `gtfsrt.ttc.ca/trips/detour` | no header timestamp; identical content across 40 minutes | poll every 60 s, record HTTP `Date` |
| `alerts.ttc.ca/api/alerts/list` | `Cache-Control: public,max-age=25`, `ETag`, `Last-Modified`, `lastUpdated` in body | poll every 60 s with `If-None-Match` |
| `ntas.ttc.ca` | no cache headers | 10 s in-memory cache per platform |
| `www.ttc.ca/ttcapi/*` | `Cache-Control: no-cache` | service changes every 10 min; route detail daily |
| CKAN `package_show` | cached 15 min upstream (`Age` header) | daily, plus `HEAD` on the zip for ETag |

**User-Agent gating (verified):** `alerts.ttc.ca`, `ntas.ttc.ca`, `www.ttc.ca` and `gtfsrt.ttc.ca` return **403 to the default `Python-urllib` User-Agent**; any custom value (`curl/8.7.1`, `ttc-transit-probe/0.3`, a browser string) gets 200. `bustime.ttc.ca` does not filter. The server must always send its own User-Agent.

### 4.4 `feed_status` contents

For each source: name, URL, last successful fetch time, feed header timestamp (or HTTP `Date`/`Last-Modified` when absent), lag in seconds, entity count, last error. For the static layer: `feed_version`, `feed_start_date`/`feed_end_date`, CKAN `last_refreshed`, ETag, days until the current period ends, and whether a newer package has been seen. Staleness rules are an open decision (6.5).

## 5. Data model summary (static)

- `stops`: 12,002 rows (9,512 boarding stops/platforms, 114 stations, 250 entrances, 2,126 pathway nodes); `wheelchair_boarding` populated on all but the 626 blank nodes/entrances.
- `routes`: 235 (3 subway, 19 streetcar/LRT, 213 bus); series by number: rail 5, bus 150, express 28, Blue Night 35, community 6, streetcar 11.
- `trips`: 137,912; `shape_id` on every trip; `wheelchair_accessible` 1 on 120,657 and 2 on 17,255 (all on streetcar routes, see 6.2).
- `shapes`: 1,592 shapes, 1,035,699 points, `shape_dist_traveled` on every point; 26 shapes unused by any trip.
- `pathways`: 4,086 edges (2,933 walkways, 471 stairs, 297 escalators, 116 elevators, 129 fare gates, 140 exit gates); `levels`: 324.
- Patterns: 1,418 distinct stop sequences; 42 have more than one shape; no shape serves two patterns.

## 6. Ambiguities investigated, per item

Each entry: **finding** (with evidence from 2026-09-13), then **resolution** or **open decision**.

### 6.1 Item 1: route geometry

1. **Which shape is "the route"?** A route-direction has 1 to 41 shapes (136 route-directions have one, 115 two, the rest more; Line 1 has 26 southbound and 36 northbound because of short turns). Each shape belongs to exactly one headsign, and shapes refine stop patterns (42 patterns carry more than one shape; no shape spans two patterns). *Resolution:* the canonical shape of a route-direction is the shape with the most trips; `route_shape` returns it by default and can return any pattern's shape on request. Do not union shapes; branches would render as a tangle.
2. **Rail lines?** All five rail routes have shapes (Line 1 canonical shape: 633 points). *Resolved.*
3. **Point density and size.** Median 558 points per shape, max 3,071; `shapes.txt` is 46 MB. *Resolution:* store as encoded polylines; offer `simplify_m` (Douglas-Peucker) for map rendering. *Open decision (a):* default output format for tools, encoded polyline (compact, what the detour feed uses) or GeoJSON LineString (verbose, universally understood). Recommendation: encoded polyline by default, `format: geojson` on request.
4. **"Stops between A and B" needs distance along the shape.** `shape_dist_traveled` is populated on every shape point and every `stop_times` row, so this is a lookup, not a projection. *Resolved.*
5. **BusTime's `getRoutePoints.jsp`** returns 4 patterns for 504, each with the stop points embedded (`<pt><bs>` with stop number and name) and a `dtr` (detour) flag that was false on all points. It duplicates GTFS for surface routes and adds nothing for rail. *Resolution:* not used, except as a fallback source of pattern geometry if a future feed drops `shapes.txt`.
6. **Detour geometry** comes from the detour feed's `Shape` entities (separate ids, `..._shape` suffix) and is served by `detours`, not `route_shape`. *Resolved.*

### 6.2 Item 3: accessibility state

1. **The station-level flag is meaningless.** All 114 `location_type 1` records have `wheelchair_boarding = 1`, including stations that are not accessible. The truth is at platform level: platforms flagged 2 today include Old Mill, Islington, College, King, Museum (and the Dundas West streetcar outer platform). *Resolution:* derive station accessibility from its platforms and entrances. *Open decision (b):* the definition of "accessible station": all platforms flagged 1 (strict) or any platform flagged 1 (lenient). Recommendation: report per platform and let the summary say "partially accessible" when they differ.
2. **Surface stops.** 1,340 boarding stops are flagged 2 (not accessible), 194 are 0 (unknown). *Resolution:* expose the raw flag with the GTFS meaning; treat 0 as unknown, never as accessible.
3. **Entrances.** 164 accessible, 84 unknown, 2 not accessible; names are often generic ("Entrance/Exit"). *Resolution:* identify entrances by coordinates and parent station; use the name when it is specific.
4. **Trips.** Every trip on the twelve streetcar routes (301, 304, 305, 306, 310, 312, 501, 503, 504, 505, 506, 507) is flagged `wheelchair_accessible = 2`; the fleet has been fully low-floor since 2019, so the flag reflects stop conditions or stale data, not the vehicles. *Open decision (c):* ignore `trips.wheelchair_accessible` entirely, or surface it with a warning. Recommendation: ignore it and rely on stop-level flags plus elevator status.
5. **Elevator and escalator units.** Pathway nodes carry unit codes in their names for 95 elevator nodes (47 distinct elevators), 462 escalator nodes (227 distinct), and 755 stair nodes; 814 nodes are uncoded, 58 of 67 stations have at least one coded node, and 22 stations have elevator nodes with no code. Of today's 15 real alert codes, 10 match a node exactly (`12D2L` Woodbine, `9Y1L` Queen, `4Y1L` St Patrick, `16B4E`/`16B6E` Kipling, ...); `15D2L` (Warden), `16D2E`, `21Y3E` and the Line 5/6 style `ELV02-B`/`ELV03-C` do not. Two alerts carry junk codes (`6`, `8`). *Resolution:* join alert code to node when it exists (gives level and pathway context), otherwise fall back to the alert's `headerText`, which always names the station and the span ("between concourse and Line 2 westbound platform"). A "step-free path available" verdict is therefore best-effort: exact where nodes are coded, textual elsewhere.
6. **Three alert sources for the same outages.** `alerts.ttc.ca` accessibility (17 today: station id, unit code, affected routes, planned/unplanned); ttc.ca `getallroutesandstopsalerts` (11 route alerts "Out of service" + 6 accessibility alerts = the same 17, split differently); GTFS-RT `alerts/accessibility` (19 entities, `informed_entity` = platform stop ids, `effect ACCESSIBILITY_ISSUE`). *Resolution:* `alerts.ttc.ca` is primary (it has the unit code); GTFS-RT supplies platform ids; ttc.ca is not needed.
7. **Station ids in alerts are legacy ids** for 34 stations (section 3). *Resolution:* join on `stop_id OR stop_code`; verified 63/63 alert ids resolve that way.

### 6.3 Item 4: crowding

1. **What the feed provides.** 1,253 of 1,275 vehicles carry `occupancy_status`; the only values are `EMPTY` (841), `FEW_SEATS_AVAILABLE` (206) and `FULL` (14); no `occupancy_percentage`, no `congestion_level`, no `NO_DATA_AVAILABLE`. Streetcars report too (155/6/1). *Finding:* the TTC collapses its passenger counters to three levels and omits the field when it has no data.
2. **What the TTC calls them.** The ttc.ca stop page legend is "Real time: Not busy / Busy / Very busy / No info" (and a "Historical" set that no public endpoint exposes). At 16:27 the 22-minute 504 arrival at Cherry St was drawn with the "Not Busy" icon, and at 16:31 the next vehicle there (4502, 21 min) reported `EMPTY` in the vehicle feed, consistent with `EMPTY -> Not busy`. Caveat: at that moment all 25 vehicles on route 504 reported `EMPTY`, and 155 of 162 streetcars network-wide did, so `EMPTY` may also be what the feed emits when a vehicle's counters are not reporting; validate the distribution during a weekday peak before trusting `EMPTY` as a positive reading. *Resolution:* map `EMPTY -> Not busy`, `FEW_SEATS_AVAILABLE -> Busy`, `FULL -> Very busy`, absent -> `No info`, and return both the TTC label and the GTFS enum, with a `confidence` note while the `EMPTY`-as-default question is open. *Open decision (d):* whether `arrivals` should carry the label inline (adds a vehicle-feed join per row) or leave crowding to `vehicles`. Recommendation: inline, it is what riders ask.
3. **Joining a prediction to a vehicle.** TripUpdates carry `vehicle.id` on 1,697 of 1,699 entities and VehiclePositions carry `trip.trip_id` on 1,078 of 1,275, so join on vehicle id from the trip update side. *Resolved.*
4. **Historical occupancy** exists in the TTC's UI but not in any endpoint found. *Resolution:* out of scope; note it in the tool description so the model does not claim it.

### 6.4 Item 5: detours

Full dissection in the addendum. Residual ambiguities:

1. **Experimental spec.** TripModifications and `NEW` are marked experimental by GTFS-RT; `gtfs-realtime-bindings` 2.2.0 parses them today. *Resolution:* pin the bindings, isolate parsing, and fall back to plain JSON parsing of the feed (it is served as protobuf-JSON).
2. **No freshness signal.** The detour feed header has only `gtfsRealtimeVersion`; content was identical across two fetches 40 minutes apart. *Resolution:* record HTTP `Date` and the poll time; poll every 60 s.
3. **Replacement-bus entities** (`306 Bus`, `506 Bus`) select trips whose static route is 306/506 with headsigns like "506B Carlton Replacement Bus towards Spadina Station". *Resolution:* report under the streetcar route with the replacement-bus headsign; no special casing needed.
4. **Trips across a board-period boundary.** All 2,523 selected trip ids exist in the current static feed; when a new period starts, entities may reference trips the server has not loaded yet or has already dropped. *Resolution:* keep both feeds loaded around the boundary (2.1) and label unresolvable trips as "unknown pattern" rather than dropping the detour.
5. **NEW trips** (negative ids) have no static headsign. *Resolution:* label by route and the first/last stop names from their own stop list.
6. **Reasons.** 12 of 14 detours today match a service change by route (113 and 16 do not); `isDetour` is false on every service change, so the join is route plus date overlap. *Open decision (e):* when no reason matches, show the detour without a reason (recommended) or suppress it.
7. **Grouping.** One real-world detour spawns one entity per affected pattern and direction (route 112: 20 entities). *Resolution:* group by `detourId` in tool output, listing directions inside.

### 6.5 Item 6: service changes and feed status

1. **Service change records.** 46 today, GUID ids, ISO `effectiveStartDate`/`effectiveEndDate` (none open-ended), `lastUpdated`, `contentText` (plain text with stop names), `url`. `route` is pipe-separated for multi-route entries (`20|113`, `149|300`) and blank on 6 records whose route only appears in the title; `categoryName` is only ever Bus or Streetcar while `routeType` also says Subway (17 records), and `station`/`stationName` are always null. *Resolution:* parse `route` on `|`, fall back to a regex on the title for blank routes, use `routeType` not `categoryName`, ignore `isDetour`.
2. **Relationship to GTFS-RT alerts.** 34 of 40 `MODIFIED_SERVICE` alerts match a service change title exactly; the 12 service changes without an alert are Annual Network Plan and seasonal changes. Alert ids are small integers (100, 101, ...) unrelated to the GUIDs. *Resolution:* service changes are the canonical list; use the GTFS-RT alert only to obtain `informed_entity` route ids and active periods when titles match.
3. **Freshness signals differ per source** (table in 4.3): surface feeds share one timestamp updated every 30 s; subway and alert feeds stamp on request; the detour feed has none; NTAS has none; `alerts.ttc.ca` has three; CKAN lags up to 15 minutes. *Resolution:* `feed_status` reports the best available signal per source and its type (feed header, HTTP header, or poll time).
4. **Staleness thresholds** are policy. *Open decision (f):* recommendation: surface real-time is "stale" past 90 s, subway/NTAS past 60 s, alerts past 10 min, detours past 30 min, static "expiring" within 3 days of `feed_end_date` without a newer package seen, "expired" after it.
5. **Board-period metadata.** `feed_info.feed_version` (S1000538) is shared by the merged and surface feeds; the legacy feed has no `feed_info`. CKAN `last_refreshed` (2026-09-04 18:03) and the zip's `Last-Modified` (20:20 UTC) agree to the day. *Resolution:* report `feed_version`, dates, and CKAN `last_refreshed`; never the CKAN `refresh_rate` label, which is wrong.

## 7. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| a | `route_shape` default format | encoded polyline, GeoJSON on request |
| b | Definition of "accessible station" | per-platform truth + "partially accessible" summary |
| c | `trips.wheelchair_accessible` (2 on all streetcar trips) | ignore; rely on stop flags + elevator status |
| d | Crowding label inline on `arrivals` | yes, TTC label + GTFS enum per row |
| e | Detours with no matching reason | show them, reason `null` |
| f | Staleness thresholds in `feed_status` | 90 s / 60 s / 10 min / 30 min / 3 days as in 6.5 |

## 8. Effort (from the 2026-09-13 estimates, revised)

Core server: ~3 days solo / ~1.5 days with Claude. Item 1: ~2 h / ~1 h. Item 3: ~1 h / ~30 min for flags and alerts, plus ~1.5 days / ~half a day if the step-free pathway verdict is included. Item 4: ~1 h / ~30 min. Item 5: ~3 to 4 h / ~1.5 h. Item 6: ~2 h / ~1 h (service changes parsing plus `feed_status`). Total for the five additions without step-free routing: about **1.5 days solo, ~5 hours with Claude**.

## 9. Verification log (2026-09-13)

- Geometry: all 137,912 trips have shapes; 1,592 shapes; 1,418 patterns; 42 multi-shape patterns; BusTime patterns for 504 = 4 with 36 to 39 embedded stops each.
- Accessibility: platform flags listed above; 95/462/755 coded elevator/escalator/stair nodes; 10 of 15 alert codes matched; 63/63 alert station ids resolved via `stop_id OR stop_code`.
- Crowding: 1,253/1,275 vehicles with status; three values only; ttc.ca legend captured from the live stop page.
- Detours: 93 modification entities, 93 shapes, 14 detours; 2,523/2,523 selected trips in static feed; 39/39 live links resolved; polylines validated against kept/skipped stops.
- Service changes: 46 records; 34/40 alert-title matches.
- Freshness: two snapshots 28 s apart showed surface feed timestamps advancing by 30 s with 5 to 7 s lag; alerts API `max-age=25`; CKAN `Age: 447`.
- User-Agent: 403 from four TTC hosts for `Python-urllib`, 200 for every custom string.
