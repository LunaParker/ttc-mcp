"""Exercise the eight static tools over an in-process MCP client against the local store.

Run: TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_static.py
"""
import asyncio
import datetime as dt
import sys
import time

from fastmcp import Client

from ttc_mcp.server import create_app

CHECKS = [
    ("find_stops", {"query": "cherry st at front"}, lambda r: {"15460", "15461"} <= {s["number"] for s in r["stops"]}),
    ("nearest_stops", {"lat": 43.6527, "lon": -79.3581}, lambda r: r["results"][0]["number"] == "15461"),
    ("station", {"query": "Dundas"}, lambda r: r["station"]["name"] == "TMU"),
    ("station", {"query": "Bloor"}, lambda r: len(r["station"]["platforms"]) == 4),
    ("routes", {"overnight": True}, lambda r: len(r["routes"]) == 35),
    ("route_stops", {"route": "504", "direction": "0"}, lambda r: len(r["patterns"][0]["stops"]) == 36),
    ("service_hours", {"route": "300", "date": dt.date.today().isoformat()}, lambda r: r["runs"] is True),
    ("scheduled_departures", {"stop": "15461", "route": "504"}, lambda r: len(r["departures"]) > 0),
    ("stop", {"number": "13863"}, lambda r: r["stop"]["station_name"] == "Bloor-Yonge"),
]


async def main() -> int:
    failures = 0
    async with Client(create_app()) as c:
        for name, args, ok in CHECKS:
            t = time.perf_counter()
            r = (await c.call_tool(name, args)).data
            ms = (time.perf_counter() - t) * 1000
            passed = "error" not in r and ok(r)
            failures += not passed
            print(f"{'PASS' if passed else 'FAIL'} {name} {args} {ms:.0f} ms feed={r['meta']['static_feed']} {r['meta']['staleness']}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
