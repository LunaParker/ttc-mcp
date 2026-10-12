from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress

from fastmcp import FastMCP

from ttc_mcp import __version__
from ttc_mcp.adapters.ckan import make_client
from ttc_mcp.config import Gate, gate_settings, settings
from ttc_mcp.gate import EmailAllowlist, build_auth
from ttc_mcp.static.bucket import GcsBlobs, pull
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
    "Contains information licensed under the Open Government Licence – Toronto (https://open.toronto.ca/open-data-licence/). "
    "Timetables and the GTFS-Realtime feeds are TTC open data on the City of Toronto's portal; stop and train predictions, alerts.ttc.ca "
    "and ttc.ca route data come from endpoints behind the TTC's website, which publishes no terms specific to them. "
    "Real-time and advisory answers are best effort."
)
RECHECK_S = 24 * 3600
PULL_S = 6 * 3600


def _recheck(cache_dir, user_agent: str) -> str:
    with make_client(user_agent) as client:
        rep = ensure_static(cache_dir, client)
    return f"{rep['reason']} ({rep.get('feed_version')})"


async def _loop(fn: Callable[[], str], seconds: float, label: str) -> None:
    """One sync job on a timer, in a worker thread (ensure_static runs its own event loop); one failure never ends the loop."""
    while True:
        await asyncio.sleep(seconds)
        try:
            print(f"[ttc-mcp] {label}: {await asyncio.to_thread(fn)}", file=sys.stderr)
        except Exception as e:
            print(f"[ttc-mcp] {label} failed: {e}", file=sys.stderr)


def periodic(fn: Callable[[], str], seconds: float, label: str):
    """A FastMCP lifespan that runs fn every `seconds` while the server is up: the stdio CKAN recheck (spec 2.1), the http bucket pull (6.9)."""
    @asynccontextmanager
    async def lifespan(server: FastMCP):
        task = asyncio.create_task(_loop(fn, seconds, label))
        try:
            yield {}
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    return lifespan


def _recheck_lifespan(server: FastMCP):
    s = settings()
    return periodic(lambda: _recheck(s.cache_dir, s.user_agent), RECHECK_S, "static recheck")(server)


def create_app(*, recheck: bool = False, gate: Gate | None = None, puller: Callable[[], str] | None = None, pull_s: float | None = None) -> FastMCP:
    """recheck (stdio with refresh on) keeps checking CKAN while the server runs; puller (http with a bucket) re-pulls the store every
    pull_s (PULL_S by default); gate turns on the Google OAuth gate with its email allowlist (spec 6.9). stdio never passes a gate or a puller."""
    lifespan = _recheck_lifespan if recheck else periodic(puller, PULL_S if pull_s is None else pull_s, "bucket pull") if puller else None
    mcp = FastMCP("ttc-mcp", instructions=INSTRUCTIONS, version=__version__, lifespan=lifespan, auth=build_auth(gate) if gate is not None else None)
    if gate is not None:
        mcp.add_middleware(EmailAllowlist(gate.allowed_emails))
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
    gate = gate_settings() if a.transport == "http" else None
    refresh = not (a.no_refresh or s.skip_refresh or (a.transport == "http" and s.static_bucket))  # a bucket is authoritative (spec 6.9)
    if refresh:
        with make_client(s.user_agent) as client:
            rep = ensure_static(s.cache_dir, client)
            print(f"[ttc-mcp] static store: {rep['reason']} ({rep.get('feed_version')})", file=sys.stderr)
    puller = None
    if a.transport == "http" and s.static_bucket:
        blobs = GcsBlobs(s.static_bucket)

        def puller() -> str:
            m = pull(s.cache_dir, blobs)
            return "manifest " + ", ".join(f.feed_version for f in m.feeds) if m else "bucket has no manifest"
        print(f"[ttc-mcp] bucket pull: {puller()}", file=sys.stderr)
    app = create_app(recheck=refresh and a.transport == "stdio", gate=gate, puller=puller, pull_s=s.pull_s)
    if a.transport == "stdio":
        app.run(transport="stdio", show_banner=False)
    else:
        if gate is None:
            print("[ttc-mcp] http transport has no gate: set GOOGLE_OAUTH_CLIENT_ID to require Google sign-in", file=sys.stderr)
        app.run(transport="http", host=a.host, port=a.port, stateless_http=True, json_response=True, show_banner=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
