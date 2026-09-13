# TTC network data for an MCP server: stops, routes, real-time arrivals, and advisories

**Investigation date:** 2026-09-13 (every endpoint below was exercised live that day)
**Requested for:** the `ttc-mcp` project
**Companion script:** `scripts/ttc_transit_probe.py` (run with `uv run`; see section 9)

## 0. Summary

- **Stops (bus, streetcar, subway, LRT) with coordinates, directional, dynamic:** use the City of Toronto's **Merged GTFS** dataset, resolved through the CKAN API. It has 12,002 stop records: 9,512 physical stops/platforms, 114 station parents, 250 station entrances, and 2,126 pathway nodes, all with lat/lon where they exist. A physical stop is inherently directional (eastbound and westbound are different stop IDs with different coordinates), and joining `stop_times -> trips` tells you which route, direction, and headsign call at each one. Subway and LRT platforms are stops like any other ("Bloor Station - Northbound Platform"), grouped under `parent_station`.
- **Real-time arrivals at a stop:** the official **GTFS-RT TripUpdates** feed (`bustime.ttc.ca/gtfsrt/trips`) plus the undocumented per-stop XML the TTC's own TrackTTC page uses (`getStopPredictions.jsp`). Both were verified against each other for the Cherry St at Front St East stop (identical vehicles and minutes). For subway and LRT platforms, **NTAS** (`ntas.ttc.ca`) returns next trains per platform, and a **subway GTFS-RT TripUpdates** feed exists on `gtfsrt.ttc.ca` since June 2026. Subway real-time is therefore accessible after all.
- **Routes, stops along them both ways, and when they run:** all from the same GTFS (`routes`, `trips`, `stop_times`, `calendar`). Blue Night 300-series routes are cleanly separable from the day network by trip start times; the ttc.ca route API additionally labels every route with an official service level ("Blue Night Network", "Express Network", "Regular service", "Community Bus").
- **Advisories mapped to stations and stops:** `alerts.ttc.ca/api/alerts/list` returns planned closures, detours, elevator outages, and **Reduced Speed Zones**, each with the affected station or stop IDs in the same ID space as the merged GTFS. The "College to St Clair, service ends early" example is literally the first record in today's response, with the six station IDs between them.
- **The one big trap:** the TTC publishes three overlapping static GTFS feeds with two different stop-ID spaces, and the GTFS-RT feeds use the internal IDs of the *Surface GTFS*, not the public stop numbers. Section 6 has the crosswalk.

## 1. Source inventory (all verified 2026-09-13)

| # | Source | What it gives | Auth | Stop-ID space | Freshness / status |
|---|--------|---------------|------|---------------|--------------------|
| 1 | CKAN package `merged-gtfs-ttc-routes-and-schedules` (`completegtfs.zip`, 84 MB) | All modes, full station hierarchy (`location_type`, `parent_station`, `pathways.txt`, `levels.txt`), `feed_info.txt` | none | public stop number (`stop_id == stop_code`, 42 exceptions) | feed S1000538, valid 2026-09-06 to 2026-10-31, published 2026-09-04; ETag + Last-Modified served |
| 2 | CKAN package `ttc-routes-and-schedules` (`opendata_ttc_schedules.zip`, 36 MB) | The long-standing all-modes feed; 9,402 stops; no hierarchy | none | public stop number | published 2026-08-27 for the same board period |
| 3 | CKAN package `surface-routes-and-schedules-for-bustime` (`SurfaceGTFS.zip`, 83 MB) | Bus + streetcar + Lines 5/6; 9,135 stops; `fare_attributes`, `transfers`, `frequencies` | none | internal 1 to 5 digit `stop_id`, public number in `stop_code` | published 2026-09-03; this is the ID space of the GTFS-RT feeds |
| 4 | GTFS-RT, BusTime host: `https://bustime.ttc.ca/gtfsrt/{vehicles,trips,alerts}` | Surface vehicle positions (with `occupancy_status`), trip updates (~1,700 trips), alerts; protobuf, `?debug=true` for text | none | Surface GTFS internal IDs | official per the City's open-data listing |
| 5 | GTFS-RT, TTC in-house host: `https://gtfsrt.ttc.ca/` | `/alerts/{all,subway,bus,streetcar,stops,accessibility}`, `/trips/{update,subway,detour,modified_update}`, `/vehicles/position`; `?format=text` or `binary`; release notes at `/release-notes` | none | surface feeds: Surface GTFS internal IDs; subway feed and alerts: public platform/station IDs | v3.4.4 (2026-08-12); subway trip updates added 2026-06-10 |
| 6 | NTAS: `https://ntas.ttc.ca/api/ntas/get-next-train-time/{platform_stop_id}` | Next trains at one platform, Lines 1/2/4/5/6 | none | GTFS platform `stop_id` (e.g. 13863) | undocumented; what ttc.ca station widgets call |
| 7 | ttc.ca site API: `https://www.ttc.ca/ttcapi/routedetail/get?id={route_short_name}` | Route metadata + every branch's ordered stops with lat/lon, `parentStation`, `gtfsId`; `getallroutesandstopsalerts` for alerts + board-period service changes | none | `id`/`code` public number; `gtfsId` = Surface internal ID; `parentStation` = station ID | undocumented internal API |
| 8 | Alerts API: `https://alerts.ttc.ca/api/alerts/list` | Planned/unplanned alerts, detours, Reduced Speed Zones, elevator/escalator outages, with station/stop ID lists | none | station IDs (subway), public stop numbers (surface) | undocumented; carries `lastUpdated` and `rszLastUpdated` |
| 9 | BusTime web (TrackTTC) XML: `https://bustime.ttc.ca/bustime/map/*.jsp`, `eta/*.jsp` | Per-stop predictions, routes at a stop, directions + ordered stops per route, route polyline, buses on route | none | public stop number | undocumented but the same Clever Devices product CTA/MTA run for a decade |
| 10 | BusTime Developer API v3: `https://bustime.ttc.ca/bustime/api/v3/*` | `getstops`, `getpredictions`, `getvehicles`, `getpatterns`, `getdetours`, ... (guide PDF at `/bustime/apidoc/docs/DeveloperAPIGuide3_0.pdf`) | **API key required**; TTC's instance has no self-serve sign-up page | public stop number | 10,000 requests/day default per key |
| 11 | UmoIQ / NextBus: `https://retro.umoiq.com/service/publicXMLFeed?a=ttc&command=...` | `routeList` (223 surface routes), `routeConfig` (stops with lat/lon grouped by branch/direction), `predictions`, `vehicleLocations` | none | own `tag`; `stopId` = public number | **officially decommissioned 2025-11-30** per the City's dataset notice, yet still answering with live data today. Zombie: do not build on it |
| 12 | Transitland (`f-dpz8-ttc`, `f-dpz8-ttc~surface`, `~surface~rt`), Mobility Database `mdb-732` | Third-party mirrors and archives (100+ versions back to 2017) | API key (free) | mirrors of 1 and 3 | useful for history, not needed for live |

Dead or stale things found along the way: `transit.ttc.ca` (referenced in ttc.ca page props) does not resolve in DNS; `ttc-subway-shapefiles` on CKAN dates from 2019; the CKAN `ttc-real-time-next-vehicle-arrival-nvas` package only exists to announce the UmoIQ shutdown.

## 2. Stops, directional, with coordinates

### 2.1 Recommended: Merged GTFS via CKAN

Resolve the download URL dynamically rather than hard-coding it:

```bash
curl -s 'https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id=merged-gtfs-ttc-routes-and-schedules' \
  | jq -r '.result | .last_refreshed, (.resources[] | select(.format=="ZIP") | .url, .last_modified)'
# 2026-09-04 18:03:35
# https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/b811ead4-.../download/completegtfs.zip
```

The resource URL has been stable, and the server returns `ETag` and `Last-Modified`, so a daily `HEAD` request is enough to notice a new board period. The TTC publishes a new feed roughly every six weeks, about a week before the board period starts (this one: published Sept 4, valid Sept 6 to Oct 31). The CKAN metadata says "Quarterly"; ignore that label.

What is inside (`stops.txt`, 12,002 rows):

| `location_type` | count | meaning |
|---|---|---|
| `0` | 9,512 | boarding stops and platforms (buses, streetcars, subway platforms, LRT platforms) |
| `1` | 114 | station parents (e.g. `14572` Bloor-Yonge, `3529` Dundas West) |
| `2` | 250 | station entrances/exits with their own coordinates |
| `3` | 2,126 | generic nodes (stairs, escalators) used by `pathways.txt` |

Columns: `stop_id, stop_code, stop_name, stop_desc, stop_lat, stop_lon, zone_id, stop_url, location_type, parent_station, stop_timezone, wheelchair_boarding, level_id, tts_stop_name`. `wheelchair_boarding` is populated (1 or 2). A few pathway nodes have blank coordinates; everything a rider can stand at has them.

**Direction is a property of the physical stop, then confirmed by the schedule.** An example, straight from the feed:

| stop_id | name | lat, lon | served by (route, direction_id, headsign) |
|---|---|---|---|
| 15461 | Cherry St at Front St East | 43.652840, -79.358092 | 504 dir 0 "East - 504A King towards Distillery"; 304 dir 0; 508 dir 0 "East - 508 Lake Shore towards Distillery"; 504 dir 1 "West - 504 King towards Distillery" (short turns) |
| 15460 | Cherry St at Front St East | 43.652513, -79.357841 | 504 dir 1 "West - 504A King towards Dundas West Station"; 304 dir 1; 508 dir 1 |
| 15510 | Front St East at Cherry St | 43.652436, -79.358636 | bus routes on Front |

Same intersection, same name, three stop IDs, three coordinates, three sets of directions. The join is `stop_times.stop_id -> trips.trip_id -> (route_id, direction_id, trip_headsign)`; over the whole feed that yields 27,938 distinct (stop, route, direction, headsign) combinations across 9,402 served stops. Every stop in `stops.txt` is referenced by at least one trip (no orphans).

**Subway and LRT.** Lines 1, 2, and 4 are `route_type 1` and appear as 148 platform stops named `"<Station> Station - <Direction> Platform"`, two per station, four at the interchanges (St George, Spadina, Sheppard-Yonge). Line 5 Eglinton (50 platform stops) and Line 6 Finch West (36) are `route_type 0` (tram/light rail). In the legacy Routes and Schedules feed their platforms are named without the hyphen (`"Avenue Station Eastbound Platform"`); in the merged feed they follow the subway convention (`"Avenue Station - Eastbound Platform"`), and the four terminal platforms (Mount Dennis, Kennedy, Humber College, Finch West) are named `"... Station - Subway Platform"`. In the merged feed every platform carries `parent_station`, so "what is the station" is a lookup, not string parsing. Bloor-Yonge (`14572`) has 78 children: four platforms (Line 1 `13863`/`13864` named "Bloor Station", Line 2 `13755`/`13756` named "Yonge Station"), plus entrances and nodes.

**ID quirks.** In this feed `stop_id` equals the public stop number printed on the pole (the number you text to 898882) for all but 42 records, which carry a synthetic `stop_id` (43257 and up) and the public number in `stop_code`. Always keep both columns.

### 2.2 The other two static feeds and why you might still touch them

- **Routes and Schedules** (`ttc-routes-and-schedules`) is the feed most third parties consume. Same stop numbering as the merged feed, but no `parent_station`, no entrances, and the Lines 5/6 naming inconsistency bites harder because you have to derive stations from names. It offers nothing the merged feed lacks.
- **Surface GTFS for BusTime** is required for one thing only: the `stop_code -> stop_id` crosswalk that the GTFS-RT feeds need (section 6). It also carries `fare_attributes.txt`, `transfers.txt`, and a `route_type 4` oddity (route 882 "Operator Shuttles"). Its `feed_version` (S1000538) matches the merged feed, so the two are cut from the same schedule.

### 2.3 Per-route alternative: the ttc.ca route API

`GET https://www.ttc.ca/ttcapi/routedetail/get?id=504` (the `id` is the route short name; internal IDs 404) returns:

- `route`: `shortName`, `longName`, `routeType`, `type` (extended GTFS type: 700 bus, 900 tram, 400 urban rail), `frequency`, `is10MinutesNetwork`, `serviceLevel {name, description}`, `mapUrl` (SVG route map), HTML `description`.
- `routeBranchesWithStops[]`: one entry per branch and direction (for 504: 504A east, 504A west, 504B east, 504B west, plus two merged "all stops" lists), each with `routeBranch {headsign, direction, compassDirection, branchAbbreviation, gtfsShapeId}` and `routeBranchStops[] {id, code, gtfsId, name, latitude, longitude, locationType, parentStation, wheelchairBoarding, sequence}`.

For subway lines (`id=1`, `5`, `6`) it returns platform stops with `parentStation` set (e.g. Finch Station `7758`), and for surface stops `gtfsId` is the Surface GTFS internal ID, which makes this endpoint a second source for the RT crosswalk. There is no "list all routes" endpoint on `ttcapi` that I could find; seed route IDs from GTFS `routes.txt`. Treat the whole thing as an undocumented internal API: no terms, no versioning, Sitecore-backed, and it can change without notice.

### 2.4 Directional stop lists without coordinates: BusTime web

`getDirectionsStopsForRoute.jsp?route=504` returns every direction name (including short turns) with its ordered stops (name + public number); `getStopsForRouteDirection.jsp?route=504&direction=East%20-%20504A%20King%20towards%20Distillery` returns one direction; `getRoutesForStop.jsp?stop=15461` lists routes at a stop. Surface only (a subway platform returns "Stop not found"). No coordinates; join to GTFS by public number.

### 2.5 UmoIQ, for the record

`routeConfig&a=ttc&r=504` still returns 113 stops with lat/lon and five `<direction>` groups (`504_0_504A "East - 504a King towards Distillery"`, ...). The City's dataset page says the service "will officially be decommissioned by November 30, 2025" and directs everyone to GTFS-RT. It has clearly outlived its notice, but the `schedule` command already throws server exceptions, and nothing guarantees tomorrow. Note that its `tag` values are internal (Cherry EB is `15355`), while `stopId` is the public number (`15461`).

## 3. Real-time arrivals at a stop (bus and streetcar)

### 3.1 GTFS-RT TripUpdates (official)

`https://bustime.ttc.ca/gtfsrt/trips` (protobuf, ~580 KB, whole network; append `?debug=true` for the text form). Each entity is a trip with `stop_time_update[] {stop_sequence, stop_id, arrival.time | departure.time}`. To answer "next arrivals at stop 15461" you filter every entity for the stop, which means polling the whole feed on an interval (20 to 30 s is conventional) and indexing by stop in memory. There is no per-stop query.

The catch is the ID space. For Cherry St at Front St East eastbound, the feed contains `stop_id: "4282"`, never `"15461"`. `4282` is the Surface GTFS `stop_id` whose `stop_code` is `15461`. I confirmed this across 120 random `(trip_id, stop_sequence)` samples from TripUpdates: 120/120 matched the Surface GTFS `stop_id`, 0/120 matched the merged feed's IDs. Trip IDs likewise exist only in the Surface and merged feeds (not in the legacy Routes and Schedules feed). The same feed is mirrored at `https://gtfsrt.ttc.ca/trips/update` (identical entities, `?format=text|binary`).

`https://bustime.ttc.ca/gtfsrt/vehicles` gives positions (~1,275 vehicles), `bearing`, `speed`, `current_stop_sequence`, `current_status`, and `occupancy_status` (today's feed: 1,019 EMPTY, 221 FEW_SEATS_AVAILABLE, 12 FULL), which is a nice thing to surface as "how full is the next streetcar".

### 3.2 Per-stop XML (what ttc.ca's TrackTTC map uses)

```bash
curl -s 'https://bustime.ttc.ca/bustime/map/getStopPredictions.jsp?stop=15461&route=all'
```

Returns `<stop><id>15461</id><nm>Cherry St at Front St East</nm> ... <pre><pt>21 MIN</pt><fd>East - 504A King towards Distillery</fd><v>4485</v><scheduled>false</scheduled><rn>504</rn>...</pre>...` keyed by the **public** stop number, so no crosswalk. `eta/getStopPredictionsETA.jsp?route=504&stop=15461` is the text-only variant with `<pt>3</pt><pu> MINUTES</pu>` and `<nextbusonroutetime>`. At 15:4x today both returned vehicles 4485 (21 min) and 4636 (25 min), and GTFS-RT computed 21.4 and 25.5 minutes for the same two trips. Same backend, two doors. It is undocumented, but Clever Devices has shipped these JSPs unchanged for over a decade at other agencies.

### 3.3 BusTime Developer API v3

The documented Clever Devices API (`getpredictions?key=...&stpid=15461&format=json`, `getstops?rt=504&dir=...` with lat/lon, `getvehicles`, `getpatterns`, `getdetours`, `getservicebulletins`) is deployed at `bustime.ttc.ca` and answers "No API access key supplied" without a key. The guide says to create an account on the BusTime website and request a key under "My Account > Developer API", but TTC's instance does not expose that portal (`login.jsp` just renders the home page). If a key is wanted, it would have to come from the TTC directly. Default quota is 10,000 requests/day per key.

### 3.4 UmoIQ predictions

`predictions&a=ttc&stopId=15461` works today (by public number) and even returns per-branch `dirTag` and `vehicle`. Same zombie caveat as 2.5.

## 4. Real-time subway and LRT

The starting assumption was that subway arrivals are not public. Two things say otherwise:

1. **NTAS.** Clicking a station on `ttc.ca/routes-and-schedules/1` fires `GET https://ntas.ttc.ca/api/ntas/get-next-train-time/13860` (path parameter = GTFS platform stop ID). Responses:

   ```json
   [{"line":"1","direction":"1","nextTrains":"1, 3, 5","directionText":"Northbound to Finch","stopCode":"13863"}]
   [{"line":"5","direction":"0","nextTrains":"5, 11, 15","directionText":"Eastbound to Kennedy","stopCode":"16218"}]
   ```

   It answers plain `curl` with no headers, returns `[]` for non-rail stops, and an empty `nextTrains` string when nothing is predicted (Line 6 at the moment I tried). The ttc.ca page props list all 114 stations as `StopCode` pairs like `"16218|16219"` (the two platforms), which is exactly `stops.txt` filtered to `parent_station`.

2. **GTFS-RT subway TripUpdates.** `https://gtfsrt.ttc.ca/trips/subway?format=text` had 63 trip entities with per-platform `arrival.time` values, keyed by the public platform IDs (`13824`, `13858`, `13863`...). The release notes date this to v3.4.1 (2026-06-10). There is no subway vehicle-positions feed (positions are "surface routes only").

## 5. Routes: the list, stops both ways, and when they run

Everything comes from the merged GTFS; the probe script's `routes` and `route` commands implement it.

- **The list.** `routes.txt` has 235 routes: 3 subway (`route_type 1`), 19 streetcar/LRT (`0`), 213 bus (`3`). TTC numbering conventions map cleanly: 1 to 6 rail, 7 to 299 regular bus (150 routes), 300 to 399 Blue Night (35), 400 to 499 community bus (6), 500 to 599 streetcar (11), 900 to 999 express (28). `route_color`/`route_text_color` are present.
- **Stops along a route, both directions.** Group `stop_times` by trip, then count identical stop sequences per `(route_id, direction_id)`. Route 504 direction 0 has three headsigns (504A to Distillery, 504B to Broadview Station, short turns) and its dominant pattern is the 36-stop 504B run from Dufferin Gate Loop; the 504A pattern is the 38-stop run from Dundas West. Exposing "patterns with trip counts" rather than one flattened list keeps branches honest. The ttc.ca API (2.3) gives the same thing pre-branched if you prefer one call per route.
- **When they run.** `calendar.txt` in this feed has eight `service_id`s: `1` Mon-Fri, `2` Sat, `3` Sun, `5` Monday-only variants, `4` holidays (via `calendar_dates.txt`; Labour Day Sept 7 swaps service 1 out and 4 in), and community-bus patterns `4401` (Mon/Wed/Thu), `4501` (Tue/Fri), `7001` (Tue/Wed/Thu). Per route and service, the first departure and last arrival give the span. GTFS times run past 24:00 for trips that belong to the previous service day, which is exactly how Blue Night shows up: route 320 Yonge spans `03:41` to `29:19` on weekdays, i.e. roughly 01:30 to 06:00 wall-clock split across two service days. A robust classifier is "share of trips starting between 00:30 and 07:30 wall-clock": all 35 Blue Night routes score 0.96 to 1.00, every day route scores 0.00 to 0.20, and nothing else crosses 0.85. The ttc.ca API's `serviceLevel` is the authoritative human label: for 300/320/385 it reads "Blue Night Network - 30-minute or better service, from approximately 1:30 a.m. to the start of subway service (approximately 6 a.m. on weekdays and Saturdays; 8 a.m. on Sundays)"; 929 is "Express Network"; 400 is "Community Bus - Monday, Wednesday and Thursday"; 29 and 504 are "Regular service - Operates all day, every day, until 1 a.m." with `is10MinutesNetwork: true`.

## 6. The ID crosswalk (read this before writing any join)

| ID | where it lives | example (Cherry/Front EB) | example (Bloor Stn NB platform) |
|---|---|---|---|
| public stop number | merged GTFS `stop_id`/`stop_code`; legacy GTFS `stop_id`; Surface GTFS `stop_code`; ttc.ca `id`/`code`; BusTime web `stop=`; UmoIQ `stopId`; alerts API surface `stops[]`; NTAS path; subway GTFS-RT `stop_id` | `15461` | `13863` |
| Surface internal `stop_id` | Surface GTFS `stop_id`; **all surface GTFS-RT feeds** (`bustime.ttc.ca/gtfsrt/*`, `gtfsrt.ttc.ca/trips/update`, `/vehicles/position`); ttc.ca `gtfsId` | `4282` | n/a |
| station ID | merged GTFS `location_type 1` `stop_id`; platforms' `parent_station`; ttc.ca `parentStation`; alerts API subway `stops[]`/`stopStartId`; ttc.ca station pages | n/a | `14572` (Bloor-Yonge) |
| UmoIQ `tag` | UmoIQ only | `15355` | n/a |
| trip_id | Surface + merged GTFS, GTFS-RT | `108895010` | subway feed uses its own numeric trip IDs |

Coverage of the crosswalk today: 9,094 of 9,135 Surface stop codes exist in the merged feed's `location_type 0` records; the 41 leftovers are Union Station bay stops and a handful of University Ave platform stops present only in the Surface feed. Keep the Surface feed's own rows as a fallback so RT never dead-ends.

## 7. Advisories, closures, detours, elevators, and reduced speed zones

### 7.1 `alerts.ttc.ca/api/alerts/list`

One JSON document, no auth: `{total, lastUpdated, rszLastUpdated, routes[], accessibility[], siteWide, siteWideCustom, generalCustom, stops[], status}`. Today: 20 route alerts (16 Reduced Speed Zone, 2 detours, 1 early closure, 1 no-service) and 17 accessibility alerts.

Every route alert carries: `route`, `routeType` (Subway/Bus/Streetcar), `alertType` (Planned/Unplanned), `activePeriod {start,end}`, `activePeriodGroup` (e.g. `["Current","Weekend"]`), `direction`, `cause`/`causeDescription`, `effect` (GTFS-RT vocabulary: NO_SERVICE, SIGNIFICANT_DELAYS, DETOUR...), `effectDesc`, `severity`, `headerText`, `description`, `url`, and the mapping fields **`stopStart`, `stopEnd`, `stopStartId`, `stopEndId`, `stops[]`, `stopIDList`, `stopNameList`, `stopRouteList`**. Shuttle fields (`shuttleType`, `shuttleStart`, `shuttleEnd`) and RSZ fields (`rszLength`, `distance`, `trackPercent`, `reducedSpeed`, `averageSpeed`, `targetRemoval`) and `childAlerts` round it out.

The example, verbatim from the first record: route `1`, `Subway Closure - Early Closure`, active 2026-09-08 to 2026-09-18, group Current/Weekend, `stopStart` "St Clair", `stopEnd` "College", `stops` `["14589","8623","14572","6965","14590","8135"]`, `direction` "Both ways", `shuttleType` "Will Operate", and the header text "no subway service between St Clair and College stations, starting 11:59 p.m., nightly Monday, September 14 to Friday, September 18". Those six IDs are `location_type 1` station records in the merged GTFS (14572 is Bloor-Yonge), so mapping an alert onto the network is a direct key lookup. Bus detours use public stop numbers in `stops[]` (route 24: ten stops from Victoria Park Ave Loop to Sparks Ave).

**Reduced Speed Zones** are in the same list with `effectDesc: "Reduced Speed Zone"`, `effect: SIGNIFICANT_DELAYS`, a `direction`, a station pair, and open-ended `activePeriod.end`. Today's 16 include "northbound from Wilson to Sheppard West", "southbound from Sheppard West to Wilson", "southbound from Lawrence West to Glencairn" (the Yorkdale-Wilson zone from the request is not in today's list; the neighbouring ones are), "southbound from Queen to King ... while we work on Ontario Line construction", and "westbound from Royal York to Islington". Each RSZ record also quantifies the zone: for Wellesley to College southbound, `direction` "Southbound From Finch", `causeDescription` "Track issue", `rszLength` 183, `distance` 528, `trackPercent` 35, `reducedSpeed` 40, `averageSpeed` 44, `targetRemoval` "Early October" (units are not documented; the numbers read as metres and km/h). `rszLastUpdated` (2026-09-11T07:16Z today) tells you when that sub-list changed. Note the GTFS-RT alert feeds deliberately **exclude** RSZ alerts from the subway and combined feeds (release note 3.2.0), so this JSON is the source for slow zones.

**Accessibility**: each record names the station (`stops: ["3194"]` = Woodbine), the `elevatorCode`/`escalatorCode` (`12D2L`), the routes affected (`"2,91,92,93,300"`), and `headerText` such as "Elevator 12D2L out of service between concourse and Line 2 westbound platform".

### 7.2 GTFS-RT alerts (`gtfsrt.ttc.ca/alerts/*`)

The same closures in standard form: `cause: MAINTENANCE`, `effect: NO_SERVICE`, `active_period {start,end}` epochs, `header_text`, `description_text`, `url`, and `informed_entity[] {route_id, stop_id}` listing the **platform** stop IDs (13808, 13807, 13805, 13806, 13863, 13864, 13804...). The combined feed had 67 entities today (40 MODIFIED_SERVICE, 17 ACCESSIBILITY_ISSUE, 6 NO_SERVICE, 2 DETOUR, 1 REDUCED_SERVICE). `/alerts/stops` is for winter-readiness inactive stops (empty right now). `/trips/detour` is a JSON `TripModifications` feed (186 entities today) listing, per detour, the affected `tripIds`, `serviceDates`, and `modifications` with start/end stop-sequence selectors, which is the machine-readable form of bus detours. See the separate addendum `ttc-detours-tripmodifications-addendum.md` for the full dissection, including the finding that `/trips/modified_update` is keyed by public stop numbers.

### 7.3 ttc.ca `getallroutesandstopsalerts`

`https://www.ttc.ca/ttcapi/routedetail/getallroutesandstopsalerts` returns `routeAlerts` (elevator outages and closures with `effectDesc`, `headerText`, image `url`), `accessibilityAlerts`, `stopAlerts`, and **`serviceChanges`**: 46 board-period changes today ("101 Downsview Park - Temporary route change due to Rogers Stadium concert road closures", `effectiveDateTitle`, `categoryName`, `route`, `station`, `isDetour`, `contentText`, `url`). That is the "service changes" page as data, and it is not in the alerts API.

## 8. What else a network MCP should expose (suggestions)

Data that exists today and would let a Claude sibling reason about the network rather than just look things up:

1. **Geometry.** `shapes.txt` (46 MB in the merged feed) gives every trip's polyline; BusTime `getRoutePoints.jsp?route=504` gives the same per route with service bulletins attached. Useful for "which stops are between X and Y" and for drawing.
2. **Station internals.** `pathways.txt` and `levels.txt` (merged feed only) plus `location_type 2` entrances with coordinates: nearest entrance, step-free routes, which entrance an elevator alert closes.
3. **Accessibility state.** `wheelchair_boarding` on every stop, `wheelchair_accessible` on trips, and the live elevator/escalator alerts (7.1) keyed by station and unit code.
4. **Crowding.** `occupancy_status` on every surface vehicle in the GTFS-RT vehicle feed.
5. **Detours as structure**, not prose: `gtfsrt.ttc.ca/trips/detour` (TripModifications) and `/trips/modified_update`.
6. **Board-period service changes** from `getallroutesandstopsalerts.serviceChanges`, and the feed metadata itself (`feed_info.feed_version`, start/end dates, CKAN `last_refreshed`) so the model can say how current its knowledge is.
7. **Service-level semantics** from the ttc.ca route API: 10-minute network, frequent vs regular, Blue Night, express, community, plus the route map SVG URL.
8. **Historical reliability.** CKAN publishes monthly `ttc-subway-delay-data`, `ttc-streetcar-delay-data`, `ttc-bus-delay-data`, and `ttc-lrt-delay-data` (incident logs with station, line, delay minutes, and codes), and `street-furniture-transit-shelter` (which stops have shelters). These answer "is this stretch usually late" and "is there a shelter at my stop".
9. **Connections beyond the TTC.** GO Transit and UP Express GTFS from Metrolinx for Union, Kipling, Kennedy and the other interchange points; the Transitland atlas file `feeds/toronto.ca.dmfr.json` lists every feed URL in one place.
10. **Not available publicly** (worth stating so the model does not go looking): subway vehicle positions, PRESTO/fare-gate data, a self-serve BusTime API key, any documented rate limits for the ttc.ca, alerts, NTAS, or BusTime web endpoints.

## 9. Reference implementation

`scripts/ttc_transit_probe.py` (PEP 723 header, single dependency `gtfs-realtime-bindings`, run with `uv run`). It caches both static feeds in `~/.cache/ttc-probe`, re-downloads only when the CKAN resource's ETag changes, and builds one index (~16 MB JSON) in a single streaming pass over `stop_times.txt` (about two minutes on the M4 Pro).

```
uv run ttc_transit_probe.py stops --query "cherry st at front"     # directional stops with coordinates + headsigns
uv run ttc_transit_probe.py stops --station 14572                  # a station's platforms, entrances, nodes
uv run ttc_transit_probe.py routes --overnight                     # the 35 Blue Night routes, with service spans
uv run ttc_transit_probe.py route 504 --patterns 2                 # ordered stops both ways, per stop pattern
uv run ttc_transit_probe.py arrivals 15461                         # BusTime XML + GTFS-RT, side by side
uv run ttc_transit_probe.py arrivals 13863                         # NTAS next trains
```

All six commands were run today against the live feeds; the `arrivals 15461` output showed the XML and the protobuf feed agreeing on vehicles 4485 and 4636.

## 10. Suggested architecture for the MCP

- **Static layer (SQLite or DuckDB).** Nightly: `package_show` + `HEAD` on the merged and surface zips; on ETag change, rebuild tables `stops` (with hierarchy), `routes`, `trips`, `stop_times`, `calendar`/`calendar_dates`, `shapes`, `pathways`, plus derived tables `stop_directions`, `route_patterns`, `route_service_hours`, and the crosswalk `stop_code <-> surface_stop_id <-> station_id`. Keep the previous feed until the new one's `feed_start_date` arrives; both are published before they take effect.
- **Real-time layer.** One poller for `bustime.ttc.ca/gtfsrt/trips` and `/vehicles` every 20 to 30 s into an in-memory index keyed by Surface `stop_id` and by route; per-stop tools translate public numbers through the crosswalk. NTAS on demand per platform (cheap, tiny JSON) and `gtfsrt.ttc.ca/trips/subway` on the same poll cadence. Keep `getStopPredictions.jsp` as a parity check or fallback.
- **Advisory layer.** `alerts.ttc.ca/api/alerts/list` every 1 to 5 minutes (watch `lastUpdated`/`rszLastUpdated`), `gtfsrt.ttc.ca/alerts/all` for structured entities, `getallroutesandstopsalerts` daily for service changes. Resolve every `stops[]`/`informed_entity` ID against the static layer so each alert becomes "route + ordered list of stations/stops + direction + period".
- **Tool surface (first cut).** `find_stops`, `stop`, `station`, `routes`, `route_stops`, `service_hours`, `arrivals`, `next_trains`, `vehicles_on_route`, `alerts` (by route/station/stop, including RSZ), `service_changes`, `detours`, `feed_status`.
- **Etiquette.** Identify with a real `User-Agent`, cache aggressively, and poll the undocumented endpoints no harder than the TTC's own pages do. The CKAN data is under the Open Government Licence - Toronto (any lawful use including commercial, attribute with "Contains information licensed under the Open Government Licence - Toronto"); nothing else here has published terms.

## 11. Gotchas, collected

1. Three static feeds, two stop-ID spaces; GTFS-RT speaks the Surface feed's internal IDs. (Section 6.)
2. The legacy Routes and Schedules feed has no station hierarchy; the merged feed does. Use the merged one.
3. Lines 5 and 6 are `route_type 0` (tram) despite being rail lines with stations; in the legacy feed their platform names lack the `" - "` separator (the merged feed has it). Do not parse names or trust `route_type` for mode; use `parent_station` and an explicit rail-line list (1, 2, 4, 5, 6).
4. Interchange stations have four platforms with two different name prefixes ("Bloor Station" and "Yonge Station" under Bloor-Yonge).
5. GTFS times exceed 24:00; Blue Night service straddles two service days. Classify by wall-clock start time, not by service ID.
6. `calendar_dates.txt` swaps holiday services (Labour Day 2026-09-07 used service 4); respect it when answering "does this run today".
7. CKAN `refresh_rate` labels ("Monthly", "Quarterly") are wrong; actual cadence is per board period (~6 weeks), published about a week ahead.
8. 42 merged-feed stops have synthetic `stop_id`s; 41 Surface stops are missing from the merged feed. Keep both feeds' rows.
9. The BusTime `/bustime/api/v3` needs a key TTC does not hand out on the web; the `/bustime/map/*.jsp` endpoints do not.
10. UmoIQ works but is officially dead. The `schedule` command already fails.
11. RSZ alerts are only in `alerts.ttc.ca`, not in the GTFS-RT alert feeds.
12. `ttcapi/routedetail/get` accepts only route short names, and `transit.ttc.ca` referenced in page props does not exist.
13. NTAS returns `[]` for non-rail IDs and an empty `nextTrains` string when it has no prediction; treat both as "no data", not "no trains".
14. The GTFS-RT vehicle feed's `stop_id`/`current_stop_sequence` pairs matched the static feed 108/120 times in my sample; the rest are vehicles between updates. TripUpdates matched 120/120.

## Appendix A: endpoint cheat sheet

```bash
# static feed discovery
curl -s 'https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id=merged-gtfs-ttc-routes-and-schedules'
curl -s 'https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id=surface-routes-and-schedules-for-bustime'
curl -sI 'https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/b811ead4-6eaf-4adb-8408-d389fb5a069c/resource/c920e221-7a1c-488b-8c5b-6d8cd4e85eaf/download/completegtfs.zip'

# GTFS-RT (surface, Surface-GTFS internal IDs)
curl -s 'https://bustime.ttc.ca/gtfsrt/trips?debug=true' | head -40
curl -s 'https://bustime.ttc.ca/gtfsrt/vehicles' -o vehicles.pb
curl -s 'https://gtfsrt.ttc.ca/vehicles/position?format=text' | head

# GTFS-RT (subway trip updates + alerts, public IDs)
curl -s 'https://gtfsrt.ttc.ca/trips/subway?format=text' | head -40
curl -s 'https://gtfsrt.ttc.ca/alerts/subway?format=text'
curl -s 'https://gtfsrt.ttc.ca/trips/detour?format=text'      # JSON TripModifications
curl -s 'https://gtfsrt.ttc.ca/release-notes'

# next train / next vehicle
curl -s 'https://ntas.ttc.ca/api/ntas/get-next-train-time/13863'
curl -s 'https://bustime.ttc.ca/bustime/map/getStopPredictions.jsp?stop=15461&route=all'
curl -s 'https://bustime.ttc.ca/bustime/eta/getStopPredictionsETA.jsp?route=504&stop=15461'
curl -s 'https://bustime.ttc.ca/bustime/map/getRoutesForStop.jsp?stop=15461'
curl -s 'https://bustime.ttc.ca/bustime/map/getDirectionsStopsForRoute.jsp?route=504'
curl -s 'https://bustime.ttc.ca/bustime/map/getRoutePoints.jsp?route=504'
curl -s 'https://bustime.ttc.ca/bustime/map/getBusesForRoute.jsp?route=504'

# ttc.ca site API
curl -s 'https://www.ttc.ca/ttcapi/routedetail/get?id=504'
curl -s 'https://www.ttc.ca/ttcapi/routedetail/get?id=1'
curl -s 'https://www.ttc.ca/ttcapi/routedetail/getallroutesandstopsalerts'

# alerts
curl -s 'https://alerts.ttc.ca/api/alerts/list'

# UmoIQ (zombie)
curl -s 'https://retro.umoiq.com/service/publicXMLFeed?command=routeConfig&a=ttc&r=504'
curl -s 'https://retro.umoiq.com/service/publicJSONFeed?command=predictions&a=ttc&stopId=15461'
```

## Appendix B: evidence counts (2026-09-13)

| measurement | value |
|---|---|
| merged GTFS stops / stations / entrances / nodes | 12,002 / 114 / 250 / 2,126 (9,512 boarding stops) |
| merged GTFS routes / trips / stop_times rows | 235 / 137,912 / ~7.7 M |
| legacy GTFS stops / routes / trips / stop_times rows | 9,402 / 236 / 138,036 / 4,346,204 |
| Surface GTFS stops / routes / trips | 9,135 / 230 / 129,273 |
| subway platform stops (Lines 1/2/4) / stations by name | 148 / 71 |
| Line 5 / Line 6 platform stops | 50 / 36 |
| distinct (stop, route, direction, headsign) combinations | 27,938 |
| GTFS-RT trip entities / vehicles | 1,707 to 1,713 / 1,274 to 1,275 |
| subway GTFS-RT trip entities | 63 |
| RT stop_id matches: Surface `stop_id` vs merged `stop_id` (TripUpdates sample) | 120/120 vs 0/120 |
| Surface stop_code present in merged feed | 9,094 / 9,135 |
| alerts.ttc.ca route alerts / RSZ / accessibility | 20 / 16 / 17 |
| GTFS-RT combined alert entities | 67 |
| Blue Night routes flagged by start-time classifier | 35 / 35 (0 false positives) |
| UmoIQ routeList routes | 223 (surface only) |
