from __future__ import annotations

import argparse
import os
import sys

from fastmcp import FastMCP

from ttc_mcp import __version__
from ttc_mcp.adapters.ckan import make_client
from ttc_mcp.config import settings
from ttc_mcp.static.refresh import ensure_static
from ttc_mcp.tools import realtime_tools, static_tools

INSTRUCTIONS = (
    "TTC network data, fetched on demand. Public stop numbers identify stops and are directional. Stations resolve by name or alias. "
    "Real-time answers (arrivals, next_trains, vehicles) are fetched when asked, cached for the feed's own interval, and fall back to the "
    "timetable with basis 'schedule' when a source has nothing. Every answer carries meta (source, fetched_at, cache, static_feed, staleness)."
)


def create_app() -> FastMCP:
    mcp = FastMCP("ttc-mcp", instructions=INSTRUCTIONS, version=__version__)
    static_tools.register(mcp)
    realtime_tools.register(mcp)
    return mcp


def main(argv=None) -> int:
    s = settings()
    p = argparse.ArgumentParser(prog="ttc-mcp")
    p.add_argument("--transport", choices=["stdio", "http"], default=os.environ.get("TTC_MCP_TRANSPORT", "stdio"))
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    p.add_argument("--no-refresh", action="store_true", help="do not check CKAN for a new feed on startup")
    a = p.parse_args(argv)
    if not (a.no_refresh or s.skip_refresh):
        with make_client(s.user_agent) as client:
            rep = ensure_static(s.cache_dir, client)
            print(f"[ttc-mcp] static store: {rep['reason']} ({rep.get('feed_version')})", file=sys.stderr)
    app = create_app()
    if a.transport == "stdio":
        app.run(transport="stdio", show_banner=False)
    else:
        app.run(transport="http", host=a.host, port=a.port, stateless_http=True, json_response=True, show_banner=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
