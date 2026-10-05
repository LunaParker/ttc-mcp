"""Exercise the four real-time tools over an in-process MCP client against the live TTC endpoints.

Run: TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_realtime.py
"""
import asyncio
import sys
import time

from fastmcp import Client

from ttc_mcp.server import create_app

CHECKS = [
    ("arrivals", {"stop": "15461"}, lambda r: r["arrivals"] and "crowding" in r["arrivals"][0] or r["arrivals"][0]["basis"] == "schedule"),
    ("arrivals", {"stop": "15461"}, lambda r: r["meta"]["cache"] == "ttl"),
    ("arrivals", {"stop": "13863"}, lambda r: r["arrivals"] and r["arrivals"][0]["route"] == "1"),
    ("next_trains", {"query": "Bloor"}, lambda r: len(r["platforms"]) == 4 and all("next_min" in p for p in r["platforms"])),
    ("next_trains", {"query": "16317"}, lambda r: r["platforms"][0]["service"] in ("normal", "no_prediction")),
    ("vehicles", {"route": "504"}, lambda r: r["vehicles"] and r["vehicles"][0]["current_stop"] is None or r["vehicles"][0]["current_stop"].isdigit()),
    ("vehicles", {"route": "1"}, lambda r: "unavailable" in r),
    ("feed_status", {"probe": True}, lambda r: all(s["verdict"] == "fresh" for s in r["sources"])),
]


async def main() -> int:
    failures = 0
    async with Client(create_app()) as c:
        for name, args, ok in CHECKS:
            t = time.perf_counter()
            r = (await c.call_tool(name, args)).data
            ms = (time.perf_counter() - t) * 1000
            passed = "error" not in r and bool(ok(r))
            failures += not passed
            m = r["meta"]
            print(f"{'PASS' if passed else 'FAIL'} {name} {args} {ms:.0f} ms cache={m.get('cache')} {m['staleness']} source={m['source']}")
            if name == "feed_status":
                for s in r["sources"]:
                    print(f"     {s['name']}: {s['verdict']} age={s.get('age_s')}s lag={s.get('lag_s')}s entities={s.get('entities')}")
                print(f"     ntas_coverage={r['ntas_coverage']}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
