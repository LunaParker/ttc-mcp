# Addendum: TTC detours as structured data (GTFS-RT TripModifications)

**Date:** 2026-09-13 (evening), follow-up to `ttc-transit-data-sources-investigation.md` item 8.5
**Verdict:** feasible today, and cheaper than I estimated. The TTC's `gtfsrt.ttc.ca` host publishes real GTFS-RT `TripModifications` entities with detour `Shape` polylines, and a companion trip-update feed that (a) is keyed by **public stop numbers**, removing the crosswalk problem, and (b) links live trips back to their detour. The current Python bindings parse all of it.

## 1. The two feeds

| Feed | URL | Form | Content today |
|---|---|---|---|
| Detours | `https://gtfsrt.ttc.ca/trips/detour?format=text` (also `format=binary`, 151 KB protobuf) | protobuf **JSON mapping** even when asked for text (`tripModifications`, `selectedTrips`, camelCase) | 186 entities: 93 `TripModifications` + 93 `Shape`; 14 distinct detours covering 14 routes (112, 300, 354, 169, 304, 307, 306 Bus, 20, 23, 94, 113, 16, 68, 506 Bus) |
| Modified trip updates | `https://gtfsrt.ttc.ca/trips/modified_update?format=text` (binary 583 KB) | protobuf JSON mapping | 1,699 `TripUpdate` entities (the whole surface network), 39 carrying `modifiedTrip` links, 6 `NEW` extra trips, 16 `SKIPPED` stop entries |

Both parse with `gtfs-realtime-bindings` 2.2.0 (`FeedEntity` has `trip_modifications` and `shape`; `TripDescriptor` has `modified_trip`; `ScheduleRelationship` includes `NEW = 8`), from binary via `ParseFromString` and from the JSON via `google.protobuf.json_format.Parse`. Note the spec marks TripModifications as **experimental**, so pin the bindings version and expect field changes.

## 2. Shape of one detour entity

```json
{
  "id": "detour_0_695:0_0_1209_112_Kipling Station via Renforth Station",
  "tripModifications": {
    "selectedTrips": [{"tripIds": ["100079010", "100572010", "... 67 total"],
                       "shapeId": "detour_0_695:0_0_1209_112_Kipling Station via Renforth Station_shape"}],
    "serviceDates": ["20260913", "20260914", "...", "20260920"],
    "modifications": [{"startStopSelector": {"stopSequence": 32},
                       "endStopSelector": {"stopSequence": 38},
                       "propagatedModificationDelay": 0}]
  }
}
```

Observed conventions (93 entities checked):

- **Entity id** encodes `detour_{n}_{detourId}:{dir}_{dir}_{patternId}_{route}_{destination}`. The `detourId` (695 above) groups the entries that belong to one real-world detour; one detour produces one entity per affected stop pattern and direction (route 112's sewer-repair detour is 20 entities, route 300's road-work detour 23).
- **Selectors are by `stopSequence` only** (never `stopId`), and they always fall inside the selected trips' static stop range. Every selected trip in an entity shares the same stops at the selected sequences (111 of 111 modifications), so resolving the first trip is enough. Spans run from 1 to 8 consecutive stops; most are 2, 4, or 6.
- **`replacementStops` is always empty.** Per the spec that means the span is simply removed: the vehicle leaves the regular route after the stop before `startStopSelector` and rejoins at the stop after `endStopSelector`, with no temporary stops declared. The TTC does not publish temporary stop locations in this feed.
- **`serviceDates`** is a rolling window of today plus seven days, matching the spec's "detours within the next week" guidance, so a long detour appears every day with a sliding date list.
- **`Shape` entities** are real detour paths: decoding the 112 polyline gives 317 points and 19.5 km end to end (the static shape is 1,238 points and 20.1 km). Across all entities, 6,496 of 6,700 kept stops lie within 60 m of the detour polyline and 372 of 476 skipped stops lie farther than 60 m from it; the exceptions are the boundary stops where the detour leaves or rejoins the street.
- **No `serviceAlertId`** is populated. The human-readable reason has to be joined from elsewhere (section 4).

## 3. The modified trip-update feed is the better arrivals feed

For the same trip, `trips/update` (a mirror of `bustime.ttc.ca/gtfsrt/trips`) carries stop ids `3751, 6160, 5516...`, and `trips/modified_update` carries `14655, 7275, 4596...`. Surface GTFS says stop_id `3751` has stop_code `14655` ("Sheppard West Station at Bus Bay 3"). Over all 25,380 stop-time updates in the modified feed, 25,300 match public stop numbers in the merged GTFS; the 15,405 that also collide with Surface-internal ids are just 4-digit overlaps. The release notes' "improved stop code to id binding" (v3.4.2, 2026-07-08) is this feed.

Consequences:

- `arrivals` for a public stop number can filter `modified_update` directly. The probe script's new `--public-feed` flag does exactly that and returned the same 504 vehicles at Cherry St at Front St East as the BusTime XML and the crosswalked protobuf feed.
- Each live trip on a detour carries `trip.modifiedTrip {modificationsId, affectedTripId}` pointing at the detour entity id, and all 39 such links resolved to an entity in the detour feed.
- Detoured trips' `stopTimeUpdate` lists already omit the skipped stops (route 185 example: 17 static stops, 16 live), so "will this bus stop here" is answerable from the feed alone.
- `NEW` (relationship 8) trips are the negative-id extras that the bustime feed labels `ADDED`; both feeds carry them.

## 4. Attaching a reason

`alerts.ttc.ca` had **no** alerts for any of the 14 detoured routes (it carries closures, RSZ, elevators, and only two unplanned detours today). The reasons live in two other places, both joinable by route (and effective dates):

- `https://www.ttc.ca/ttcapi/routedetail/getallroutesandstopsalerts` → `serviceChanges[]`: "112 West Mall – REVISED - Temporary route change due to sewer repairs (June 9, 2026 - 07:00 AM to October 1, 2026 - 07:00 AM)", "300 Bloor-Danforth – Temporary route change due to road work (August 12 to October 30)", "169 Huntingwood – Temporary service change due to road paving (September 9 to 20)", each with a ttc.ca URL and `contentText`.
- `https://gtfsrt.ttc.ca/alerts/bus?format=text` → the same items as GTFS-RT alerts with `effect: MODIFIED_SERVICE` and `informed_entity.route_id`.

`isDetour` in `serviceChanges` is false for all of these (the TTC calls them "route changes"), so match on route + date, not on that flag.

## 5. Resolution recipe (implemented in `ttc_transit_probe.py detours`)

1. Fetch the detour feed; split entities into `TripModifications` and `Shape`.
2. For each `TripModifications`: take `selectedTrips[0].tripIds[0]`, look up its route, direction, headsign and stop pattern in the static index (the index now stores each pattern as `(stop_sequence, stop_id)` pairs, because 9,099 of 137,912 trips do not number their stops 1..n, so pattern position is not a safe proxy for `stopSequence`).
3. Map `startStopSelector..endStopSelector` onto that pattern: the stops in the span are skipped; the neighbours are the leave/rejoin points.
4. Decode the `Shape` polyline for the detour geometry and length.
5. Join the reason from `serviceChanges` (and/or the bus alert feed) by route and date.
6. Optionally fetch `modified_update` and count live trips whose `modifiedTrip.modificationsId` equals the entity id.

Output for route 112 today: 20 entities under detour 695, each listing the 7 skipped Gentian Dr / Wellesworth Dr / Rangoon Rd / Eringate Dr stops with coordinates, the leave point (Renforth Dr at Eglinton Ave West) and rejoin point (Wellesworth Dr at Faversham Cres), the 19.5 km path, the sliding eight-day window, and the sewer-repair service change with its URL.

## 6. Revised effort

Original estimate for item 5 was about 1 day solo / half a day with me. With the structure now known and the ID-space question settled in the feed's favour: **~3 to 4 hours solo, ~1.5 hours with me** for a `detours` tool in the MCP (fetch, resolve, join reasons, cache 60 s), plus another hour if you want the polyline rendered or matched to stops on a map. The remaining risk is the experimental status of TripModifications: the message layout could change in a future GTFS-RT revision, so isolate the parsing behind one function.

## 7. Endpoint cheat sheet

```bash
curl -s 'https://gtfsrt.ttc.ca/trips/detour?format=text' | jq '.entity[] | select(.tripModifications) | .id'
curl -s 'https://gtfsrt.ttc.ca/trips/detour?format=binary' -o detour.pb
curl -s 'https://gtfsrt.ttc.ca/trips/modified_update?format=text' | jq '.entity[] | select(.tripUpdate.trip.modifiedTrip) | .tripUpdate.trip'
curl -s 'https://www.ttc.ca/ttcapi/routedetail/getallroutesandstopsalerts' | jq '.serviceChanges[] | {route, title, effectiveDateTitle, url}'
uv run scripts/ttc_transit_probe.py detours --route 112 --live
uv run scripts/ttc_transit_probe.py arrivals 15461 --public-feed
```
