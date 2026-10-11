from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import asynccontextmanager, suppress

from fastmcp import FastMCP

from ttc_mcp import __version__
from ttc_mcp.adapters.ckan import make_client
from ttc_mcp.config import settings
from ttc_mcp.static.refresh import ensure_static
from ttc_mcp.tools import advisory_tools, derived_tools, realtime_tools, static_tools

INSTRUCTIONS = (
    "TTC network data, fetched on demand. Public stop numbers identify stops and are directional. Stations resolve by name or alias. "
    "Real-time answers (arrivals, next_trains, vehicles) are fetched when asked, cached for the feed's own interval, and fall back to the "
    "timetable with basis 'schedule' when a source has nothing. Disruptions travel with every answer: stop, station, nearest_stops, "
    "route_stops, arrivals and next_trains carry the closures, detours, reduced speed zones and elevator outages that touch them; call "
    "service_status first for the network picture. For trip questions (how do I get from A to B) call connections with stop numbers, "
    "station names or {lat, lon}: it plans direct and one-transfer rides around closures and detours. route_shape gives a route's "
    "geometry; accessibility gives a station's step-free picture with elevator status. "
    "Every answer carries meta (source, fetched_at, cache, static_feed, staleness). "
    "Contains information licensed under the Open Government Licence – Toronto. "
    "Real-time and advisory answers are best effort, read from the TTC's own site endpoints, none of which publish terms."
)
RECHECK_S = 24 * 3600


def _recheck(cache_dir, user_agent: str) -> dict:
    with make_client(user_agent) as client:
        return ensure_static(cache_dir, client)


async def _recheck_loop() -> None:
    """Local mode (spec 2.1): the CKAN check again every RECHECK_S, in a worker thread because ensure_static runs its own loop."""
    s = settings()
    while True:
        await asyncio.sleep(RECHECK_S)
        try:
            rep = await asyncio.to_thread(_recheck, s.cache_dir, s.user_agent)
            print(f"[ttc-mcp] static recheck: {rep['reason']} ({rep.get('feed_version')})", file=sys.stderr)
        except Exception as e:  # one failed check must not end the loop
            print(f"[ttc-mcp] static recheck failed: {e}", file=sys.stderr)


@asynccontextmanager
async def _recheck_lifespan(server: FastMCP):
    task = asyncio.create_task(_recheck_loop())
    try:
        yield {}
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app(*, recheck: bool = False) -> FastMCP:
    """recheck (stdio with refresh on) keeps checking CKAN while the server runs; HTTP leaves that to the Job (spec 6.9)."""
    mcp = FastMCP("ttc-mcp", instructions=INSTRUCTIONS, version=__version__, lifespan=_recheck_lifespan if recheck else None)
    static_tools.register(mcp)
    realtime_tools.register(mcp)
    advisory_tools.register(mcp)
    derived_tools.register(mcp)
    return mcp


def main(argv=None) -> int:
    s = settings()
    p = argparse.ArgumentParser(prog="ttc-mcp")
    p.add_argument("--transport", choices=["stdio", "http"], default=os.environ.get("TTC_MCP_TRANSPORT", "stdio"))
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    p.add_argument("--no-refresh", action="store_true", help="do not check CKAN for a new feed on startup")
    a = p.parse_args(argv)
    refresh = not (a.no_refresh or s.skip_refresh)
    if refresh:
        with make_client(s.user_agent) as client:
            rep = ensure_static(s.cache_dir, client)
            print(f"[ttc-mcp] static store: {rep['reason']} ({rep.get('feed_version')})", file=sys.stderr)
    app = create_app(recheck=refresh and a.transport == "stdio")
    if a.transport == "stdio":
        app.run(transport="stdio", show_banner=False)
    else:
        app.run(transport="http", host=a.host, port=a.port, stateless_http=True, json_response=True, show_banner=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
