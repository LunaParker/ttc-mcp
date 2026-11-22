# When service is degraded

Two kinds of trouble: a data source failing, and the transit network itself being disrupted. Either way the server answers what it can and says what it couldn't.

## Reading an answer

Every answer carries `meta`:

- `source`: the feeds that answered.
- `fetched_at`, and `feed_timestamp` when the feed publishes one.
- `cache`: `live` (just fetched), `ttl` (reused within the feed's refresh interval), `stale_fallback` (the source failed and this is its last good answer), `static` (timetable only), or `null` when no live source answered at all.
- `staleness`: `fresh` or `stale` for live data, judged against the feed's own clock; `fresh`, `expiring` or `expired` for the timetable; `unavailable` when there is nothing to judge.
- `static_feed`: the timetable version in use.
- `advisories`: the same for the disruption feeds when the answer includes disruptions, with `errors` naming any feed that failed. The four disruption tools (`alerts`, `service_status`, `detours`, `service_changes`) describe those feeds in `meta` itself and name failed ones in `meta.errors`.

Rows that give times also carry `basis`: `realtime` or `schedule`, and `estimate` on a `connections` shuttle leg. In `connections` a ride is `realtime` when a live trip update moved its trip, and the trip as a whole carries its first ride's `basis`. `meta` describes the fetch, so a source that answered with no predictions reads `live` and `fresh` while its rows fall back to `basis: schedule`.

## A data source fails

| Source | Fetched at most every | Last good answer kept for | Called stale after |
|---|---|---|---|
| Bus and streetcar predictions (BusTime) | 30 s | 10 min | 90 s |
| Vehicle positions and trip updates (BusTime GTFS-Realtime) | 30 s | 10 min | 90 s |
| Subway trip updates (GTFS-Realtime) | 30 s | 10 min | 60 s |
| Train predictions (NTAS) | 10 s | 5 min | 60 s |
| alerts.ttc.ca | 25 s | 1 h | 10 min |
| GTFS-Realtime alerts | 60 s | 1 h | 10 min |
| Detours | 5 min | 1 h | 30 min |
| ttc.ca service changes | 10 min | 1 h | 30 min |

- **A request fails or times out** (after 10 seconds, or 5 for the alert, detour and service-change feeds): the source's last good answer is used, marked `cache: stale_fallback` and `staleness: stale`, for as long as the table allows.
- **Past that window**, live answers fall back to the timetable: `arrivals` and `next_trains` give scheduled minutes with `basis: schedule` and a note naming the failure, and `vehicles` returns no vehicles with a note. A disruption feed that fails is named in `meta.advisories.errors`, or in `meta.errors` for the four disruption tools; the answer still comes, but read "no disruptions" there as unknown, not as all clear.
- **A trip-update feed fails:** `connections` plans that part of the network on the timetable, its rides read `basis: schedule`, and `meta.source` names only the feeds that answered. The trip-update feeds are fetched only for questions within an hour of now.
- **A source that keeps failing is put on hold.** After each failure it isn't asked again for a pause that doubles with every consecutive failure, starting at twice its refresh interval and capped at a quarter of how long its last good answer is kept, so a struggling TTC server isn't hammered. Holds are per request: per stop for bus and streetcar predictions, per platform for train predictions, per feed otherwise. `feed_status` shows each source's failures, when it will next be tried (`retry_after`) and how many HTTP 403 or 429 refusals it has seen; `feed_status` with `probe: true` asks every source once, hold or not.
- **No prediction isn't no service.** An empty answer from NTAS (common on Line 6, and normal late at night) reads `service: no_prediction` with timetable minutes. BusTime's own messages are passed on in the note the same way.
- **Rail lines publish no vehicle positions or crowding**, so `vehicles` answers that the rail lines (1, 2, 4, 5 and 6) are unavailable and points to `next_trains`.

## The network is disrupted

- **A subway or LRT closure.** Platforms inside the closed section, and each end station's platform facing into it, are `suspended`: no minutes, with whatever NTAS still predicts set aside in `ntas_min`, as in the README's [St George example](../README.md#an-answer-during-a-closure). The end stations keep real minutes on the platform facing the open line (`service: normal` in `next_trains`, while `station` reads that line as `reduced`), and the closure is tagged `shuttle_terminus`. A closure that ends at a terminal, or covers one station, closes both platforms there. Trips across the gap become ride, shuttle, ride with the shuttle estimated and flagged: the shuttle leg's minutes come from an assumed headway and bus speed that its note states, the trip carries `estimate: true`, and it ranks after every trip that needs no estimate. The shuttle stop is placed at one platform of each station, so a trip can show a walk between two platforms of an end station: that walk stands for reaching the shuttle buses. When the TTC runs no shuttle, `connections` routes around the closure on other lines or says why it found nothing.
- **Planned and nightly closures** carry their windows, and `active_now` says whether one applies at this minute. `connections` with a future `depart_at` or an `arrive_by` plans against the closures in force at the time you give. A departure window or an arrive-by search can span an hour or more, so when a closure on a line one of the trips rides starts or ends inside it, the answer's note names the closure and the time: trips on the far side of that moment were still planned with the closure as it stood at the time you gave.
- **A surface detour.** Skipped stops show as `skipped_by_detour` in `route_stops` and arrival rows carry the detour's id. In `connections` the detoured route's trips skip them with the published delay; other routes keep serving the stop. When a detour can't be matched to its route's trips, `connections` avoids its skipped stops altogether: it plans from the served stops nearby and says so.
- **Reduced speed zones**, in their direction of travel, **and delays** are attached to the legs of a trip that they affect, and `affects` names those legs.
- **An elevator out of service** shows in `accessibility` with its alert, and the step-free verdict turns to `no`, noting that another elevator may still give a step-free path. Outages announced for later are listed under `upcoming` and leave today's verdict alone.

### A trip across a closure

`connections` plans across a closure on the shuttle buses. The unit suite's fixtures carry a real nightly Line 1 closure between York Mills and Eglinton from 23:00, with shuttle buses (alert 77519, captured 2026-10-05). Asked at 23:30 for a trip from Finch to Union, the server answers (abridged: keys removed, values unchanged):

```json
{
  "from": {"station": "43277", "name": "Finch"}, "to": {"station": "15772", "name": "Union"},
  "depart_at": "2026-10-06T23:30-04:00", "window_min": 30,
  "options": [
    {"rank": 1, "transfers": 2, "depart_at": "2026-10-06T23:33-04:00", "arrive_at": "2026-10-07T00:27-04:00",
     "duration_min": 54, "basis": "schedule", "estimate": true,
     "legs": [
       {"mode": "ride", "route": "1", "trip_id": "134929439",
        "board": {"number": "14111", "name": "Finch Station - Southbound Platform"},
        "alight": {"number": "13792", "name": "York Mills Station - Southbound Platform"},
        "depart": "2026-10-06T23:33-04:00", "arrive": "2026-10-06T23:40-04:00", "min": 7, "basis": "schedule"},
       {"mode": "transfer", "from": {"number": "13792"}, "to": {"number": "13791", "name": "York Mills Station - Northbound Platform"},
        "distance_m": 134, "via": "pathways", "depart": "2026-10-06T23:40-04:00", "arrive": "2026-10-06T23:48-04:00", "min": 8},
       {"mode": "shuttle", "line": "1", "from": {"station": "43276", "name": "York Mills"}, "to": {"station": "43274", "name": "Eglinton"},
        "depart": "2026-10-06T23:48-04:00", "arrive": "2026-10-07T00:08-04:00", "min": 20, "basis": "estimate", "alert_id": "77519",
        "note": "assumed 5-minute headway and 15 km/h over 4.4 km of track with a 1-minute stop per station; the TTC publishes no shuttle timetable (alert 77519)"},
       {"mode": "transfer", "from": {"number": "13795"}, "to": {"number": "13795", "name": "Eglinton Station - Southbound Platform"},
        "distance_m": 0, "via": "walk", "depart": "2026-10-07T00:08-04:00", "arrive": "2026-10-07T00:11-04:00", "min": 3},
       {"mode": "ride", "route": "1", "trip_id": "134929444",
        "board": {"number": "13795"}, "alight": {"number": "13815", "name": "Union Station - Northbound Platform Towards Vaughan Metropolitan Centre"},
        "depart": "2026-10-07T00:11-04:00", "arrive": "2026-10-07T00:27-04:00", "min": 16, "basis": "schedule"}
     ],
     "disruptions": [
       {"type": "rsz", "alert_id": "77535", "span": ["Sheppard-Yonge", "York Mills"], "affects": [1]},
       {"type": "closure", "alert_id": "77519", "span": ["York Mills", "Eglinton"], "active_now": true, "shuttle": "Will Operate", "affects": [3]}
     ]}
  ]
}
```

- The two train rides are real trips with their timetable times. The shuttle leg's 20 minutes are an estimate: its note says what it assumes, `basis: estimate` marks the leg, and `estimate: true` the trip, which would rank after any trip that needs no estimate.
- The shuttle's stop is placed at a platform of each station, so the walk from the southbound to the northbound platform at York Mills stands for reaching the shuttle buses. The 8 minutes between its clocks cover the 104-second walk, the two-minute change buffer and a full five-minute wait for a bus.
- The closure names the leg it affects, 3, the shuttle; the reduced speed zone between Sheppard-Yonge and York Mills names the first train ride.

## The timetable is missing or old

- **No timetable yet:** every tool except `feed_status` answers with an error asking you to run `ttc-mcp-build`; the live and disruption tools need the timetable too, to resolve stops, stations and routes.
- **A timetable built by an older version of the server** is skipped (the server's log names it) and the tools answer with the same error; `ttc-mcp-build` rebuilds it, and so does a local stdio server's startup check. The trip planner's index, added in October 2026, is one such change.
- **An ageing timetable:** within three days of its end date, with no newer one loaded, `staleness` reads `expiring`. Once no loaded timetable covers today, the tools answer with the same error, with `staleness: expired`, until a build loads the next one. The TTC publishes each board period's timetable shortly before it starts (in September 2026, two days ahead once and on the day itself once), and the daily check picks it up.
- **The City's portal is unreachable:** a local server's startup check, its 24-hour recheck and the hosted daily Job log the failure and keep the timetable they have. A check gives up connecting after 10 seconds, so the server still starts promptly, and with no timetable on disk yet it starts anyway and its tools say the timetable isn't built yet.
- **Hosted, the bucket can't be read:** a new instance exits at startup and Cloud Run reports the failed start; an empty bucket starts a server whose tools, `feed_status` aside, say the timetable isn't built yet. A failed pull on the six-hour timer keeps the timetable already loaded.
