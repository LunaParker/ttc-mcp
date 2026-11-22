<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo/ttc-mcp-dark.svg">
    <img src="docs/logo/ttc-mcp.svg" alt="ttc-mcp" width="360">
  </picture>
</h1>

Live answers about Toronto's transit network, for Claude and other MCP clients.

ttc-mcp is a [Model Context Protocol](https://modelcontextprotocol.io) server for the Toronto Transit Commission (TTC). It lets an assistant answer the questions riders ask: when the next streetcar comes, whether a subway line is closed and where the shuttle buses run, how to get across town by a given time and around a closure, whether a station is step-free today. It reads the TTC's timetables and live feeds when you ask, says how fresh each answer is, and attaches service disruptions to every answer they touch.

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
| How do I get from Spadina to Pape? | `connections` | Trips with exact times on every leg, and the disruptions on the way |
| I'm at the Distillery District. How do I get to Union? | a maps tool to geocode, then `connections` | The same, starting from the place's coordinates |
| I have to be at Union by 9. When do I leave Kipling? | `connections` with `arrive_by` | The latest trips that still arrive in time, each with its exact times |
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

`connections` plans across a closure on the shuttle buses, with the shuttle leg's minutes marked as an estimate; [docs/degraded-service.md](docs/degraded-service.md#a-trip-across-a-closure) walks through a trip from Finch to Union across a nightly Line 1 closure.

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
| `connections` | origin and destination (a stop, a station or `{lat, lon}`); optional maximum transfers (0 to 3), walking distance, a departure time (`depart_at`) with a window (`window_min`, 30 minutes by default) or an arrival time (`arrive_by`), limit | Timetable-exact trips: real departures and arrivals on every leg, up to three transfers, a departure window or an arrival time, the next departures of the first ride, and every disruption on the way with the legs it affects |

Every answer carries `meta`, which names the feeds that answered and says how fresh the answer is, and rows that give times carry `basis`: `realtime` or `schedule`. [docs/degraded-service.md](docs/degraded-service.md#reading-an-answer) explains every field.

## Design

The [specification](docs/spec.md) records each decision with its evidence; section 7 is the summary. The ones that shape what you see:

- **It fetches only when asked.** There is no background polling. A question triggers the requests it needs, each source is cached for its own refresh interval (from 10 seconds for train predictions to 10 minutes for service changes), and simultaneous questions share one request. An idle server sends nothing, which is light on the TTC's servers and lets a hosted copy scale to zero.
- **It says how fresh every answer is.** A stale cached answer is never labelled fresh, and timetable minutes always carry `basis: schedule` (see `meta` above).
- **Disruptions travel with the answer.** Closures, detours, reduced speed zones, delays and elevator outages are attached to the stops, stations, arrivals and trips they affect, and the trip planner never boards, drops off or transfers you at a station closed at the time you ask about, never stops a detoured bus at a stop its detour skips, and never puts you on a train across a section closed at that time.
- **Trips are planned on the timetable itself.** `connections` searches the trips the TTC actually runs (the RAPTOR algorithm, over an index built with the timetable and loaded into memory once per timetable), so every ride is a real trip with its own departure and arrival. A change between vehicles costs the walk, timed through the station's mapped passageways where the TTC publishes them, plus a two-minute buffer. The answer is the fastest trip for each number of transfers, from one moment, across a departure window, or by an arrival time. For a question within an hour of now it reads the TTC's live trip updates for buses, streetcars and the subway, so the trips it boards run at their predicted times; Lines 5 and 6 publish no trip updates and stay on the timetable.
- **Closures follow how the TTC runs them.** Each end station keeps the platform facing the open part of the line, and the shuttle buses start there; [The network is disrupted](docs/degraded-service.md#the-network-is-disrupted) has the details.
- **It says which numbers are estimates.** A shuttle leg's minutes are flagged `estimate` (see [An answer during a closure](#an-answer-during-a-closure)), crowding reads "No info" without data, and a step-free verdict is `unknown` when a station's elevators aren't fully mapped.
- **Timetables come from the TTC's published GTFS**, built into a local SQLite file, with the trip planner's index, and rebuilt only when the City of Toronto's portal publishes a new one. During a board-period changeover both timetables stay loaded, so trips from either resolve.
- **Live data comes from the TTC's own feeds**: BusTime for buses and streetcars, NTAS for the subway and LRT, and the TTC's alert and GTFS-Realtime feeds (alerts, detours, and trip updates for buses, streetcars and the subway). Every request names the project in its User-Agent, and a failing source is backed off (see below).
- **Lines 5 and 6 are rapid transit** everywhere: stations, platforms, closures and train predictions.
- **Accessibility reads the rail platforms**, not the station's own flag (which the feed sets on every station) and not its bus bays, and takes elevator status from live alerts.
- **No geocoding.** Addresses are a maps tool's job, so the server needs no maps API key.
- **One person, gated when hosted.** A hosted copy sits behind Google sign-in with an email allowlist, and it speaks stateless HTTP so an idle connection never keeps an instance running.
- **Built on [FastMCP](https://gofastmcp.com) 4 and Python 3.14.**

## When service is degraded

The server answers what it can and says what it couldn't. When a source fails, its last good answer is reused for a while and marked `cache: stale_fallback`; after that, live answers fall back to the timetable with `basis: schedule`, and a source that keeps failing is backed off so the TTC's servers aren't hammered. A disruption feed that fails is named in `meta`, so read "no disruptions" in that answer as unknown, not as all clear. During a closure the closed platforms read `suspended`, and `connections` never boards you, drops you off or transfers you at a closed station: it crosses the gap on the shuttle buses and flags the shuttle leg as an estimate. [docs/degraded-service.md](docs/degraded-service.md) has each source's refresh and fallback windows, how each kind of disruption shows up, and what happens when the timetable is missing or old.

## Running it

### What you need

- [uv](https://docs.astral.sh/uv/), which installs Python 3.14 for you.
- About 450 MB of disk: each timetable is a 139 MB SQLite file, two of them during a board-period changeover, plus the 170 MB of GTFS zips the last build downloaded. Ended timetables stay on disk after a build drops them from `manifest.json`; delete those `static-*.sqlite` files by hand.
- About 70 MB of memory for each loaded timetable's trip planner index, on top of the server itself.
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

### On your own Linux machine

The maintainer's copy runs this way: two systemd user units, the HTTP server on loopback behind Google sign-in and a daily timer that runs `ttc-mcp-build` instead of a cloud Job, published over HTTPS through a Cloudflare Tunnel. Secrets sit in their own files (`GOOGLE_OAUTH_CLIENT_SECRET_FILE`, `JWT_SIGNING_KEY_FILE`), and the OAuth state stays on disk, encrypted, so a restart doesn't sign clients out. [docs/self-host.md](docs/self-host.md) has the unit files, the tunnel, the checks and a note on keeping a WSL distro running.

### Google Cloud Run

The hosted shape, specified in [docs/spec.md](docs/spec.md) section 6.9, uses the same image twice (the maintainer's copy ran this way until 2026-11-01):

- A **Cloud Run service** runs the server over HTTPS behind Google sign-in, scaling between zero and one instance. At startup and every six hours it pulls the timetable from a Cloud Storage bucket; it never contacts the City's portal itself.
- A **Cloud Run Job**, started daily at 04:30 Toronto time by Cloud Scheduler, runs `ttc-mcp-build --bucket`: it pulls the bucket's timetable, checks the City's portal, and builds and pushes a new timetable only when the TTC has published one.
- **Google sign-in** runs inside the server: FastMCP's Google OAuth provider with dynamic client registration, then an email allowlist checked on every request. The OAuth state is kept, encrypted, in Firestore so sign-ins survive scaling to zero, and its three secrets live in Secret Manager.

For one person this is sized to stay within Google Cloud's free tiers (the estimate is in spec 6.9); check your project's billing before you start. [docs/cloud-run.md](docs/cloud-run.md) is the build specification for the hosted copy: what gets provisioned, how it is sized now that the trip planner keeps its index in memory, the order deploys must follow when the timetable format changes, and the checks that say it works.

[docs/deploy.md](docs/deploy.md) is the complete runbook, one block of commands per step: billing headroom, APIs, service accounts, the bucket, the secrets, the OAuth client, Firestore, the image, the Job (run once so the bucket has a timetable), the service, the scheduler, then the connectors and the post-deploy checks. It ran end to end on 2026-10-18, and the fixes that run needed are in it. It is written with this project's names, region and allowlist address, uses `pbpaste` (macOS) to keep secrets out of the terminal, and assumes the project already has an OAuth consent screen; substitute yours.

Once it is deployed, connect a client to `https://<your-service-url>/mcp`:

- **claude.ai:** Settings, Connectors, Add custom connector. Paste the URL, leave the advanced OAuth settings empty, and sign in with an allowlisted Google account; the Claude apps share the connector.
- **Claude Code:** `claude mcp add --transport http ttc https://<your-service-url>/mcp`, then sign in from `/mcp`.

## Configuration

A local copy reads these environment variables; `ttc-mcp-build` reads `TTC_MCP_CACHE` and `TTC_MCP_UA` too. A hosted copy adds the Google sign-in and Cloud Storage variables listed in [docs/self-host.md](docs/self-host.md#variables-for-a-hosted-copy), and [CLAUDE.md](CLAUDE.md) lists every variable with the module that reads it.

| Variable | Default | What it does |
|---|---|---|
| `TTC_MCP_TRANSPORT` | `stdio` (the image sets `http`) | The transport when `--transport` isn't given |
| `PORT` | `8080` | The HTTP port when `--port` isn't given; Cloud Run sets it |
| `TTC_MCP_CACHE` | `~/.cache/ttc-mcp` (the image sets `/data`) | Where the timetable files, their `manifest.json` and the build's downloads live |
| `TTC_MCP_UA` | `ttc-mcp/<version> (+https://github.com/LunaParker/ttc-mcp)` | The User-Agent on every request; several TTC servers refuse Python's default. If you run a copy, set your own contact here |
| `TTC_MCP_SKIP_REFRESH` | off (the image sets `1`) | `1`, `true` or `yes` skips the startup portal check and the 24-hour recheck |
| `TTC_MCP_NOW` | unset (the Toronto clock) | Pins the clock used for timetable lookups and closure windows to an ISO 8601 time, read as Toronto time when it has no offset; live feeds still answer with today's data |

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
| Vehicle positions, crowding and trip updates | `bustime.ttc.ca/gtfsrt`, listed as [TTC BusTime Real-Time Next Vehicle Arrival (NVAS)](https://open.toronto.ca/dataset/ttc-bustime-real-time-next-vehicle-arrival-nvas/) | Open data |
| Service alerts with closures' nightly windows, detours, and subway trip updates | `gtfsrt.ttc.ca`, listed as [TTC GTFS-Realtime (GTFS-RT) Service Disruptions](https://open.toronto.ca/dataset/ttc-gtfs-realtime-gtfs-rt/) | Open data |
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
- [docs/cloud-run.md](docs/cloud-run.md): the Cloud Run build specification the runbook carries out.
- [docs/investigation-2026-09-13.md](docs/investigation-2026-09-13.md) and [docs/detours-addendum.md](docs/detours-addendum.md): the original survey of the TTC's data sources.

```bash
uv sync --extra cloud                  # dev tools, plus the cloud extra the unit suite needs
uv run pytest                          # the unit suite: offline, on a small fixture timetable
uv run ruff check .                    # lint
uv run pytest tests/integration        # live: boots the server and asks the TTC; needs a timetable (uv run ttc-mcp-build)
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_derived.py   # one of the live smoke scripts in scripts/
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/validate_connections.py   # the 20 trips connections is judged on
TTC_MCP_SKIP_REFRESH=1 uv run python scripts/bench_raptor.py   # the trip planner's 250 ms latency budget
scripts/smoke_image.sh                 # the container smoke test (needs Docker)
```

Every pull request runs four CI jobs: the unit suite, lint, a package build and the container smoke test.

## Disclaimer

ttc-mcp comes with no warranty of any kind and makes no claim that its answers are accurate, complete or current. They are built from the TTC's timetables and live feeds, which can be wrong or late: a predicted arrival or train time (NTAS's included), a crowding level, a closure, a detour, an elevator's status or a planned trip may not match what is actually happening. Pay attention to what you see and hear at the stop or station, and to the TTC's own signs, announcements and staff. Don't rely on ttc-mcp in an emergency, or wherever a wrong or missed connection could cause harm. To the extent the law allows, its author accepts no liability for any loss or harm from using it, as the [MIT License](LICENSE.md) also says.
