# ttc-mcp

Live answers about Toronto's transit network, for Claude and other MCP clients.

ttc-mcp is a [Model Context Protocol](https://modelcontextprotocol.io) server for the Toronto Transit Commission (TTC). It lets an assistant answer the questions riders actually ask: when the next streetcar comes, whether a subway line is closed and where the shuttle buses run, how to get across town around a closure, whether a station is step-free today. It reads the TTC's timetables and live feeds when you ask, says how fresh each answer is, and attaches service disruptions to every answer they touch.

It is an independent, single-user project, not affiliated with or endorsed by the TTC or the City of Toronto.

## Contents

- [What you can ask](#what-you-can-ask)
- [An answer during a closure](#an-answer-during-a-closure)
- [The tools](#the-tools)
- [Design](#design)
- [When service is degraded](#when-service-is-degraded)
- [Running it](#running-it)
- [Configuration](#configuration)
- [Data sources and licences](#data-sources-and-licences)
- [Licence](#licence)
- [Development](#development)
- [Disclaimer](#disclaimer)

## What you can ask

You ask in plain language and the client picks the tools. Some examples:

| Ask | Tools | What comes back |
|---|---|---|
| How do I get from Spadina to Pape? | `connections` | Up to four ranked trips: walking, rides with typical minutes, transfers, the next departures of the first ride, and every disruption on the way with the legs it affects |
| I'm at the Distillery District. How do I get to Union? | a maps tool to geocode, then `connections` | The same, starting from the place's coordinates |
| When's the next 504 at King and Spadina? | `find_stops`, `arrivals` | Predicted minutes with crowding, or timetable minutes and a note when nothing is predicted |
| When's the next train at Bloor-Yonge? | `next_trains` | Minutes for every platform, by line and direction |
| Is anything wrong on the subway? | `service_status` | Closures with their hours and shuttles, reduced speed zones, suspended or detoured surface routes, elevators and escalators out of service |
| Is Queen station step-free right now? | `accessibility` | Platform and entrance flags, each elevator's live status, and a yes, no or unknown verdict with its reason |
| Why is my bus skipping my stop? | `detours`, `route_stops` | The skipped stops, where the bus leaves and rejoins its route, the detour's path and the TTC's reason when it gives one |
| Where are the 505 streetcars? | `vehicles` | Positions, bearing, speed, current stop and crowding |
| Does the 300 Blue Night run tonight, and when is the last bus? | `routes`, `service_hours` | Whether it runs that day, and its first and last trips |
| What leaves stop 15461 after 6 pm? | `scheduled_departures` | Timetable departures, including trips that run past midnight |
| Show me the 7 Bathurst on a map. | `route_shape` | The route's line as an encoded polyline or GeoJSON, with its stops |
| What's changing on the 501 this board period? | `service_changes` | The TTC's route changes for the period, with their dates |
| Is the transit data up to date? | `feed_status` | Each source's age, cache state and failures, and the timetable's dates |

The server doesn't turn addresses into coordinates. Pair it with a maps MCP server, such as Google Maps, and the client geocodes first; `connections` and `nearest_stops` take `{lat, lon}`. The server's instructions tell the client to start trip questions with `connections` and "what's going on" questions with `service_status`.

## An answer during a closure

On Monday 12 October 2026, Line 2 had no service between St George and Broadview from 06:00 to 15:00 for structural maintenance, with shuttle buses running. Asked for the next trains at St George at 13:24, the server answered (abridged: keys removed, values unchanged):

```json
{
  "station": {"id": "43269", "name": "St George"},
  "platforms": [
    {"platform": "13855", "line": "2", "direction_text": "Westbound to Kipling",
     "next_min": [0], "basis": "realtime", "service": "normal"},
    {"platform": "13856", "line": "2", "direction_text": "Eastbound to Kennedy",
     "next_min": [], "ntas_min": [2, 6, 13], "service": "suspended",
     "note": "NTAS keeps publishing predictions inside a closure; this platform is suspended"},
    {"platform": "13857", "line": "1", "direction_text": "Southbound to Finch via Union",
     "next_min": [2, 5, 10], "basis": "realtime", "service": "normal"},
    {"platform": "13858", "line": "1", "direction_text": "Northbound to Vaughan Metropolitan Centre",
     "next_min": [6, 8, 10], "basis": "realtime", "service": "normal"}
  ],
  "disruptions": [
    {"type": "closure", "alert_id": "77820", "span": ["St George", "Broadview"], "active_now": true,
     "period": ["2026-10-12T06:00-04:00", "2026-10-12T15:00-04:00"], "shuttle": "Running",
     "role": "shuttle_terminus",
     "text": "Line 2 Bloor-Danforth: No service between St George and Broadview stations due to structural maintenance. Shuttle buses are running."}
  ],
  "meta": {"source": "ntas", "fetched_at": "2026-10-12T13:24:52-04:00", "cache": "live",
           "staleness": "fresh", "static_feed": "S1000542",
           "advisories": {"cache": "live", "staleness": "fresh", "errors": null}}
}
```

Three things to notice:

- The TTC's train-prediction service (NTAS) was still predicting eastbound trains in 2, 6 and 13 minutes. The closure decides instead: the platform is `suspended`, `next_min` is empty, and NTAS's numbers stay visible as `ntas_min`.
- St George is the end of the closure, so the westbound platform, facing the open part of the line, kept real predictions. The closure is tagged `shuttle_terminus`: this is where the shuttle buses start.
- `meta` says the predictions were fetched live and are fresh, and that the disruption feeds answered.

The same afternoon, `connections` from Spadina to Pape answered: Line 2 to St George, shuttle bus to Broadview, Line 2 to Pape. Its `typical_min` was `null` because the TTC publishes no shuttle times, `known_min` was 4 for the two train rides, the shuttle counted as a transfer, and the closure listed the legs it affects: 1, 2 and 3.

## The tools

Nineteen tools in four groups. Station inputs take an id, a name or a common alias ("Dundas" finds TMU, "Bloor" finds Bloor-Yonge); stop inputs take the public stop number posted at the stop. The output shapes are specified in [docs/spec.md](docs/spec.md) section 4.4.

**Timetable and network**, answered from the TTC's published GTFS on disk:

| Tool | Takes | Answers with |
|---|---|---|
| `find_stops` | a name, stop number or station alias; optional route, direction, mode | Matching stops, platforms and stations, with the routes and headsigns each serves |
| `nearest_stops` | `lat`, `lon`; optional radius (500 m), limit, kind | Nearby stops and stations with walking minutes, routes served and the disruptions touching each (`active_now` says which apply now) |
| `stop` | a stop number | One stop or platform: location, wheelchair flag, routes, station and disruptions |
| `station` | a station id, name or alias | Rail platforms with open or closed status, bus bays and streetcar platforms, entrances, the accessibility summary and service per line |
| `routes` | optional series, mode, `overnight` | Every route with its mode, series (Blue Night, express and community among them) and service hours |
| `route_stops` | route, direction (0 or 1); optional pattern | Ordered stops per stop pattern, with typical minutes and each stop's status: open, closed or skipped by a detour |
| `service_hours` | route; optional date | Whether the route runs that day, and its first and last trips |
| `scheduled_departures` | stop; optional route, time, limit | Timetable departures from a time, including trips past midnight |

**Live**, fetched from the TTC when asked:

| Tool | Takes | Answers with |
|---|---|---|
| `arrivals` | a stop number or rail platform; optional route, limit | Next vehicles with minutes, crowding and detour flags, or timetable minutes with a note |
| `next_trains` | a station or platform | Next trains on every platform, or why a platform has none |
| `vehicles` | a route or fleet number | Bus and streetcar positions, bearing, speed, current stop and crowding |
| `feed_status` | optional `probe` | The age and health of every source and of the timetable; `probe: true` checks each source now |

**Disruptions**:

| Tool | Takes | Answers with |
|---|---|---|
| `alerts` | optional route, station, stop, type | Current alerts in one shape: closures, suspensions, detours, reduced speed zones, delays, elevator and escalator outages |
| `service_status` | nothing | The network picture, and the first call for "what's going on" |
| `detours` | optional route | Active detours with skipped stops, the detour's path, trips affected and the reason |
| `service_changes` | optional route, date | Route changes for the current board period |

**Derived**:

| Tool | Takes | Answers with |
|---|---|---|
| `route_shape` | route, direction; optional pattern, format, simplification, a stop-to-stop span | The route's geometry with its stops |
| `accessibility` | a station or stop | Platform and entrance flags, elevators and escalators with live status, and a step-free verdict with its basis |
| `connections` | origin and destination (a stop, a station or `{lat, lon}`); optional maximum transfers (0 or 1), walking distance, departure time, limit | Ranked direct and one-transfer trips, with next departures and the disruptions each one touches |

Every answer carries `meta`:

- `source`: the feeds that answered.
- `fetched_at`, and `feed_timestamp` when the feed publishes one.
- `cache`: `live` (just fetched), `ttl` (reused within the feed's refresh interval), `stale_fallback` (the source failed and this is its last good answer), `static` (timetable only), or `null` when no live source answered at all.
- `staleness`: `fresh` or `stale` for live data, judged against the feed's own clock; `fresh`, `expiring` or `expired` for the timetable; `unavailable` when there is nothing to judge.
- `static_feed`: the timetable version in use.
- `advisories`: the same for the disruption feeds when the answer includes disruptions, with `errors` naming any feed that failed. The four disruption tools (`alerts`, `service_status`, `detours`, `service_changes`) describe those feeds in `meta` itself and name failed ones in `meta.errors`.

Rows that give times also carry `basis`: `realtime` or `schedule`. `meta` describes the fetch, so a source that answered with no predictions reads `live` and `fresh` while its rows fall back to `basis: schedule`.

## Design

The [specification](docs/spec.md) records each decision with its evidence; section 7 is the summary. The ones that shape what you see:

- **It fetches only when asked.** There is no background polling. A question triggers the requests it needs, each source is cached for its own refresh interval (from 10 seconds for train predictions to 10 minutes for service changes), and simultaneous questions share one request. An idle server sends nothing, which is light on the TTC's servers and lets a hosted copy scale to zero.
- **It says how fresh every answer is.** A stale cached answer is never labelled fresh, and timetable minutes always carry `basis: schedule` (see `meta` above).
- **Disruptions travel with the answer.** Closures, detours, reduced speed zones, delays and elevator outages are attached to the stops, stations, arrivals and trips they affect, and the trip planner never boards, drops off or transfers you at a closed station or at a stop a detour skips, and never puts you on a train across a closed section.
- **Closures follow how the TTC runs them.** Stations inside a closure have no trains. Each end station keeps running on the platform facing the open part of the line, and that is where the shuttle buses start. A train prediction for a closed platform is set aside, not trusted.
- **It doesn't invent numbers.** A shuttle leg's minutes are unknown, so a trip with one reports `typical_min: null` and the minutes it does know. Crowding reads "No info" without data. A step-free verdict is `unknown` when a station's elevators aren't fully mapped. Timetable fallbacks say `basis: schedule`.
- **Timetables come from the TTC's published GTFS**, built into a local SQLite file and rebuilt only when the City of Toronto's portal publishes a new one. During a board-period changeover both timetables stay loaded, so trips from either resolve.
- **Live data comes from the TTC's own feeds**: BusTime for buses and streetcars, NTAS for the subway and LRT, and the TTC's alert and GTFS-Realtime feeds. Every request names the project in its User-Agent, and a failing source is backed off (see below).
- **Lines 5 and 6 are rapid transit** everywhere: stations, platforms, closures and train predictions.
- **Accessibility reads the rail platforms**, not the station's own flag (which the feed sets on every station) and not its bus bays, and takes elevator status from live alerts.
- **No geocoding.** Addresses are a maps tool's job, so the server needs no maps API key.
- **One person, gated when hosted.** A hosted copy sits behind Google sign-in with an email allowlist, and it speaks stateless HTTP so an idle connection never keeps an instance running.
- **Built on [FastMCP](https://gofastmcp.com) 4 and Python 3.14.**

## When service is degraded

Two kinds of trouble: a data source failing, and the transit network itself being disrupted. Either way the server answers what it can and says what it couldn't.

### A data source fails

| Source | Fetched at most every | Last good answer kept for | Called stale after |
|---|---|---|---|
| Bus and streetcar predictions (BusTime) | 30 s | 10 min | 90 s |
| Vehicle positions and trips (BusTime GTFS-Realtime) | 30 s | 10 min | 90 s |
| Train predictions (NTAS) | 10 s | 5 min | 60 s |
| alerts.ttc.ca | 25 s | 1 h | 10 min |
| GTFS-Realtime alerts | 60 s | 1 h | 10 min |
| Detours | 5 min | 1 h | 30 min |
| ttc.ca service changes | 10 min | 1 h | 30 min |

- **A request fails or times out** (after 10 seconds, or 5 for the alert, detour and service-change feeds): the source's last good answer is used, marked `cache: stale_fallback` and `staleness: stale`, for as long as the table allows.
- **Past that window**, live answers fall back to the timetable: `arrivals` and `next_trains` give scheduled minutes with `basis: schedule` and a note naming the failure, and `vehicles` returns no vehicles with a note. A disruption feed that fails is named in `meta.advisories.errors`, or in `meta.errors` for the four disruption tools; the answer still comes, but read "no disruptions" there as unknown, not as all clear.
- **A source that keeps failing is put on hold.** After each failure it isn't asked again for a pause that doubles with every consecutive failure, starting at twice its refresh interval and capped at a quarter of how long its last good answer is kept, so a struggling TTC server isn't hammered. Holds are per request: per stop for bus and streetcar predictions, per platform for train predictions, per feed otherwise. `feed_status` shows each source's failures, when it will next be tried (`retry_after`) and how many HTTP 403 or 429 refusals it has seen; `feed_status` with `probe: true` asks every source once, hold or not.
- **No prediction isn't no service.** An empty answer from NTAS (common on Line 6, and normal late at night) reads `service: no_prediction` with timetable minutes. BusTime's own messages are passed on in the note the same way.
- **Rail lines publish no vehicle positions or crowding**, so `vehicles` answers that the rail lines (1, 2, 4, 5 and 6) are unavailable and points to `next_trains`.

### The network is disrupted

- **A subway or LRT closure.** Platforms inside the closed section, and each end station's platform facing into it, are `suspended`: no minutes, with whatever NTAS still predicts set aside in `ntas_min`, as in the example above. The end stations keep real minutes on the platform facing the open line (`service: normal` in `next_trains`, while `station` reads that line as `reduced`), and the closure is tagged `shuttle_terminus`. A closure that ends at a terminal, or covers one station, closes both platforms there. Trips across the gap become ride, shuttle, ride: `typical_min: null`, `known_min` for the rest, the shuttle counted as a transfer, and the trip ranked after complete trips with the same number of transfers. When the TTC runs no shuttle, `connections` routes around the closure on other lines or says why it found nothing.
- **Planned and nightly closures** carry their windows, and `active_now` says whether one applies at this minute. `connections` with a future `depart_at` plans against the closures in force at that time.
- **A surface detour.** Skipped stops show as `skipped_by_detour` in `route_stops`, arrival rows carry the detour's id, and `connections` never boards you at or drops you off at a skipped stop: it plans from the served stops nearby and says so.
- **Reduced speed zones**, in their direction of travel, **and delays** are attached to the legs of a trip that they affect, and `affects` names those legs.
- **An elevator out of service** shows in `accessibility` with its alert, and the step-free verdict turns to `no`, noting that another elevator may still give a step-free path. Outages announced for later are listed under `upcoming` and leave today's verdict alone.

### The timetable is missing or old

- **No timetable yet:** every tool except `feed_status` answers with an error asking you to run `ttc-mcp-build`; the live and disruption tools need the timetable too, to resolve stops, stations and routes.
- **A timetable built by an older version of the server** is skipped (the server's log names it) and the tools answer with the same error; `ttc-mcp-build` rebuilds it.
- **An ageing timetable:** within three days of its end date, with no newer one loaded, `staleness` reads `expiring`. Once no loaded timetable covers today, the tools answer with the same error, with `staleness: expired`, until a build loads the next one. The TTC publishes each board period's timetable shortly before it starts (in September 2026, two days ahead once and on the day itself once), and the daily check picks it up.
- **The City's portal is unreachable:** a local server's startup check, its 24-hour recheck and the hosted daily Job log the failure and keep the timetable they have. A check gives up connecting after 10 seconds, so the server still starts promptly, and with no timetable on disk yet it starts anyway and its tools say the timetable isn't built yet.
- **Hosted, the bucket can't be read:** a new instance exits at startup and Cloud Run reports the failed start; an empty bucket starts a server whose tools, `feed_status` aside, say the timetable isn't built yet. A failed pull on the six-hour timer keeps the timetable already loaded.

## Running it

### What you need

- [uv](https://docs.astral.sh/uv/), which installs Python 3.14 for you.
- About 400 MB of disk: each timetable is a 116 MB SQLite file, two of them during a board-period changeover, plus the 170 MB of GTFS zips the last build downloaded. Ended timetables stay on disk after a build drops them from `manifest.json`; delete those `static-*.sqlite` files by hand.
- Network access to the City of Toronto's open data portal, to build the timetable, and to the TTC's servers, for live answers.

### Locally, with Claude Code or Claude Desktop

```bash
git clone https://github.com/LunaParker/ttc-mcp.git
cd ttc-mcp
uv sync
uv run ttc-mcp-build
```

The build downloads the TTC's GTFS from the City's portal and builds the timetable into `~/.cache/ttc-mcp`, in about a minute on a fast connection. Then add the server to your client.

Claude Code, for every project (`--scope user`) or only the current one (the default):

```bash
claude mcp add --scope user ttc -- uv run --project /absolute/path/to/ttc-mcp ttc-mcp --transport stdio
```

Claude Desktop, in `claude_desktop_config.json` (on macOS in `~/Library/Application Support/Claude/`, on Windows in `%APPDATA%\Claude\`). Give the full path to `uv` (from `which uv`, or `where uv` on Windows), because the desktop app usually doesn't see your shell's `PATH`:

```json
{
  "mcpServers": {
    "ttc": {
      "command": "/absolute/path/to/uv",
      "args": ["run", "--project", "/absolute/path/to/ttc-mcp", "ttc-mcp", "--transport", "stdio"]
    }
  }
}
```

A local stdio server checks the City's portal when it starts, builds a new timetable first if the TTC has published one (the start waits for it), and checks again every 24 hours while it runs. Building before you connect, as above, keeps the first start quick; if your client gives up waiting on the day a new timetable appears, run `uv run ttc-mcp-build` in a terminal and reconnect. Add `--no-refresh` to the arguments to skip the check, for example offline.

### Locally, over HTTP

```bash
uv run ttc-mcp --transport http --host 127.0.0.1
```

The server runs in the foreground. Then, in another terminal:

```bash
claude mcp add --transport http ttc http://127.0.0.1:8080/mcp
```

The server speaks streamable HTTP at `/mcp` with stateless JSON responses. Like the stdio server it checks the City's portal when it starts, but it doesn't recheck; run `uv run ttc-mcp-build` to refresh the timetable, and the running server picks it up on its next request. Without Google sign-in configured it is open to anyone who can reach it, and it says so when it starts: `--host 127.0.0.1` keeps it on your machine, while the default, `0.0.0.0`, listens on every network interface.

### Docker

The image is the one Cloud Run runs, and building it needs Docker with BuildKit (the default since Docker 23). It serves HTTP on port 8080 and keeps its timetable in `/data`, so give it a volume and build the timetable into it first:

```bash
docker build -t ttc-mcp .
docker volume create ttc-mcp-data
docker run --rm -v ttc-mcp-data:/data ttc-mcp /app/.venv/bin/ttc-mcp-build
docker run --rm -p 127.0.0.1:8080:8080 -v ttc-mcp-data:/data ttc-mcp
```

The server is then at `http://127.0.0.1:8080/mcp`. The container never checks the City's portal by itself: run the build command again to refresh the timetable (it does nothing when the TTC hasn't published a new one), and the running server picks the new one up on its next request. Call the installed commands under `/app/.venv/bin`, not `python -m ttc_mcp...`, which inside `/app` would load the source copy instead of the installed package. `-e TTC_MCP_NOW=2026-10-06T08:00` pins the clock the server uses for timetables and closure windows, for a date a loaded timetable covers; live feeds still answer with today's data.

### Google Cloud Run

The hosted shape, specified in [docs/spec.md](docs/spec.md) section 6.9, uses the same image twice:

- A **Cloud Run service** runs the server over HTTPS behind Google sign-in, scaling between zero and one instance. At startup and every six hours it pulls the timetable from a Cloud Storage bucket; it never contacts the City's portal itself.
- A **Cloud Run Job**, started daily at 04:30 Toronto time by Cloud Scheduler, runs `ttc-mcp-build --bucket`: it pulls the bucket's timetable, checks the City's portal, and builds and pushes a new timetable only when the TTC has published one.
- **Google sign-in** runs inside the server: FastMCP's Google OAuth provider with dynamic client registration, then an email allowlist checked on every request. The OAuth state is kept, encrypted, in Firestore so sign-ins survive scaling to zero, and its three secrets live in Secret Manager.

For one person this is sized to stay within Google Cloud's free tiers (the estimate is in spec 6.9); check your project's billing before you start.

[docs/deploy.md](docs/deploy.md) is the complete runbook, one block of commands per step: billing headroom, APIs, service accounts, the bucket, the secrets, the OAuth client, Firestore, the image, the Job (run once so the bucket has a timetable), the service, the scheduler, then the connectors and the post-deploy checks. It hasn't been run end to end yet. It is written with this project's own values (project, region, names and allowlist), uses `pbpaste` (macOS) to keep secrets out of the terminal, and assumes the project already has an OAuth consent screen; substitute yours.

Once it is deployed, connect a client to `https://<your-service-url>/mcp`:

- **claude.ai:** Settings, Connectors, Add custom connector. Paste the URL, leave the advanced OAuth settings empty, and sign in with an allowlisted Google account; the Claude apps share the connector.
- **Claude Code:** `claude mcp add --transport http ttc https://<your-service-url>/mcp`, then sign in from `/mcp`.

## Configuration

The server reads these environment variables, and `ttc-mcp-build` reads `TTC_MCP_CACHE`, `TTC_MCP_UA` and `TTC_MCP_STATIC_BUCKET`. [CLAUDE.md](CLAUDE.md) has the same table with the module that reads each one.

| Variable | Default | What it does |
|---|---|---|
| `TTC_MCP_TRANSPORT` | `stdio` (the image sets `http`) | The transport when `--transport` isn't given |
| `PORT` | `8080` | The HTTP port when `--port` isn't given; Cloud Run sets it |
| `TTC_MCP_CACHE` | `~/.cache/ttc-mcp` (the image sets `/data`) | Where the timetable files, their `manifest.json` and the build's downloads live |
| `TTC_MCP_UA` | `ttc-mcp/<version> (+https://github.com/LunaParker/ttc-mcp)` | The User-Agent on every request; several TTC servers refuse Python's default. If you run a copy, set your own contact here |
| `TTC_MCP_SKIP_REFRESH` | off (the image sets `1`) | `1`, `true` or `yes` skips the startup portal check and the 24-hour recheck |
| `TTC_MCP_STATIC_BUCKET` | unset | HTTP server: pull the timetable from this Cloud Storage bucket at startup and every `TTC_MCP_PULL_S` seconds, and never check the portal. Build: pull the bucket's timetable first and push the result after. Needs the `cloud` extra (`uv sync --extra cloud`) and Google Cloud credentials |
| `TTC_MCP_PULL_S` | `21600` (6 hours) | How often the HTTP server pulls the bucket; values under 60 are refused |
| `TTC_MCP_NOW` | unset (the Toronto clock) | Pins the clock used for timetable lookups and closure windows to an ISO 8601 time, read as Toronto time when it has no offset; live feeds still answer with today's data |
| `GOOGLE_OAUTH_CLIENT_ID` | unset (no sign-in) | Turns on Google sign-in for the HTTP server; the next four become required, and a half-configured gate refuses to start |
| `GOOGLE_OAUTH_CLIENT_SECRET` | | The OAuth client's secret |
| `BASE_URL` | | The service's public origin; the OAuth redirect URI is `BASE_URL/auth/callback` |
| `JWT_SIGNING_KEY` | | Signs the tokens the server issues |
| `ALLOWED_EMAILS` | | The Google accounts allowed in, comma-separated; an empty list refuses to start |
| `GOOGLE_CLOUD_PROJECT` | unset (OAuth state in memory) | Keeps the OAuth state in this project's Firestore database |
| `STORAGE_ENCRYPTION_KEY` | | The Fernet key that encrypts that state; required with `GOOGLE_CLOUD_PROJECT` |

Command options:

| Command | Option | Effect |
|---|---|---|
| `ttc-mcp` | `--transport stdio\|http` | stdio for a local client, HTTP for a network one |
| | `--host`, `--port` | Where HTTP listens; defaults `0.0.0.0` and `PORT` or 8080 |
| | `--no-refresh` | Don't check the City's portal at startup or every 24 hours |
| `ttc-mcp-build` | `--force` | Rebuild even when the portal's files haven't changed |
| | `--cache DIR` | Build somewhere other than `TTC_MCP_CACHE` |
| | `--from-zip FILE`, `--surface-zip FILE` | Build from GTFS zips on disk instead of the portal |
| | `--no-surface` | Skip the surface feed; vehicle positions then can't be matched to public stop numbers |
| | `--no-ttcapi` | Skip ttc.ca's route service levels (one request per route) |
| | `--no-checks` | Skip the data checks logged after each build |
| | `--bucket NAME` | Pull this bucket's timetable before the portal check and push the result after, as the Job does; with `--from-zip` it pushes without pulling and replaces the bucket's timetable list. Needs the `cloud` extra and Google Cloud credentials |

## Data sources and licences

ttc-mcp reads two kinds of TTC data: open data the TTC publishes on the City of Toronto's portal, and endpoints behind the TTC's own website.

| Data | Source | Kind |
|---|---|---|
| Timetables: routes, stops, schedules, shapes and station layouts | [Merged GTFS - TTC Routes and Schedules](https://open.toronto.ca/dataset/merged-gtfs-ttc-routes-and-schedules/) | Open data |
| Bus and streetcar stop ids used by the live feeds | [Surface Routes and Schedules for BusTime](https://open.toronto.ca/dataset/surface-routes-and-schedules-for-bustime/) | Open data |
| Vehicle positions, crowding and trips | `bustime.ttc.ca/gtfsrt`, listed as [TTC BusTime Real-Time Next Vehicle Arrival (NVAS)](https://open.toronto.ca/dataset/ttc-bustime-real-time-next-vehicle-arrival-nvas/) | Open data |
| Service alerts with closures' nightly windows, and detours | `gtfsrt.ttc.ca`, listed as [TTC GTFS-Realtime (GTFS-RT) Service Disruptions](https://open.toronto.ca/dataset/ttc-gtfs-realtime-gtfs-rt/) | Open data |
| Bus and streetcar predictions at a stop | the BusTime web map, `bustime.ttc.ca/bustime/map/getStopPredictions.jsp` | Behind the TTC's website |
| Subway and LRT predictions | NTAS, `ntas.ttc.ca` | Behind the TTC's website |
| Closures with their spans and shuttles, reduced speed zones, elevator and escalator outages | `alerts.ttc.ca` | Behind the TTC's website |
| Route service levels and service changes | `www.ttc.ca/ttcapi` | Behind the TTC's website |

**Open data.** The TTC publishes the four portal datasets, and their pages on the portal give the [Open Government Licence – Toronto](https://open.toronto.ca/open-data-licence/); the portal's API leaves their licence field unspecified. The licence lets you copy, modify, publish and distribute the information for any lawful purpose, commercial included, on a few conditions: credit the source with the attribution statement below and, where possible, link to the licence; don't suggest that the City or the TTC endorses you or your use; it grants no rights to personal information, third parties' rights, official names, crests, logos or trademarks; the information comes as is, without warranty; and the rights end automatically if you don't meet the conditions. ttc-mcp's attribution, which the server also sends to every client in its instructions, with the licence link:

> Contains information licensed under the Open Government Licence – Toronto.

**Endpoints behind the TTC's website.** Stop predictions, train predictions, alerts.ttc.ca and ttc.ca's route data aren't open data: they are what the TTC's own website and BusTime map call, and the TTC publishes no terms specific to them. Its [website terms of use](https://www.ttc.ca/transparency-and-accountability/policies/web-site-terms-and-conditions-of-use) let you view and copy material from its site for your own personal, non-commercial use if you leave its copyright, trademark and other notices in place; reserve selling or modifying that material, and republishing, reproducing, displaying or distributing any substantial part of it, to the TTC's prior written permission; ask for the source to be acknowledged whenever TTC material is copied or published; and disclaim its accuracy. They don't say whether they reach these endpoints. ttc-mcp calls them on demand, as the website does, and adds a cache, a User-Agent naming the project and backoff when they fail, to answer one person's own trip questions. It is built for that personal, non-commercial use; whether other uses are allowed is for the TTC to say, and the portal lists opendata@ttc.ca as its open-data contact. The TTC can change or withdraw these endpoints at any time, which is why the answers built on them are marked best effort.

This section describes the terms as published on 12 October 2026, and it isn't legal advice.

**Not affiliated.** ttc-mcp is an independent project. It is not affiliated with, endorsed by or supported by the Toronto Transit Commission or the City of Toronto, whose names and marks belong to them.

## Licence

ttc-mcp's code is released under the [MIT License](LICENSE.md). The licence covers the code only: the data it reads stays under its publishers' terms, described above, whoever runs it. The Python packages it depends on keep their own licences: MIT, BSD and Apache 2.0 for most of them, ISC, PSF 2.0, MIT-0 and the Unlicense for a few, and MPL 2.0 for the certifi certificate bundle.

## Development

Contributor documentation lives beside the code:

- [CLAUDE.md](CLAUDE.md): the map of the codebase (architecture, identifiers, design decisions, environment, testing conventions) and how changes land.
- [docs/spec.md](docs/spec.md): the normative specification; when the code and the spec disagree, one of them gets fixed. Section 11 is the dated log of live verifications.
- [docs/data-quirks.md](docs/data-quirks.md): the quirks of the TTC's live data that the code encodes.
- [docs/architecture.html](docs/architecture.html): the architecture page; open it in a browser. It predates the container work, so its hosting and layout notes are out of date.
- [docs/deploy.md](docs/deploy.md): the Cloud Run runbook.
- [docs/investigation-2026-09-13.md](docs/investigation-2026-09-13.md) and [docs/detours-addendum.md](docs/detours-addendum.md): the original survey of the TTC's data sources.

```bash
uv sync --extra cloud                  # dev tools, plus the cloud extra the unit suite needs
uv run pytest                          # the unit suite: offline, on a small fixture timetable
uv run ruff check .                    # lint
uv run pytest tests/integration        # live: boots the server and asks the TTC; needs a timetable (uv run ttc-mcp-build)
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_derived.py   # one of the live smoke scripts in scripts/
scripts/smoke_image.sh                 # the container smoke test (needs Docker)
```

Every pull request runs four CI jobs: the unit suite, lint, a package build and the container smoke test.

## Disclaimer

ttc-mcp comes with no warranty of any kind and makes no claim that its answers are accurate, complete or current. They are built from the TTC's timetables and live feeds, which can be wrong or late: a predicted arrival or train time (NTAS's included), a crowding level, a closure, a detour, an elevator's status or a planned trip may not match what is actually happening. Pay attention to what you see and hear at the stop or station, and to the TTC's own signs, announcements and staff. Don't rely on ttc-mcp in an emergency, or wherever a wrong or missed connection could cause harm. To the extent the law allows, its author accepts no liability for any loss or harm from using it, as the [MIT License](LICENSE.md) also says.
