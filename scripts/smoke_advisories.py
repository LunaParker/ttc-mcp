"""Exercise the advisory tools and the propagation over an in-process MCP client against the live TTC endpoints.

Run: TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_advisories.py
"""
import asyncio
import sys
import time

from fastmcp import Client

from ttc_mcp.server import create_app

CHECKS = [
    ("service_status", {}, lambda r: isinstance(r["closures"], list) and r["as_of"] and "elevators_out" in r and all(c["station_names"] for c in r["closures"])),
    ("service_status", {}, lambda r: r["meta"]["cache"] == "ttl"),
    ("alerts", {"type": "rsz"}, lambda r: all(a["type"] == "rsz" and a["station_objects"] for a in r["alerts"])),
    ("detours", {}, lambda r: r["count"] > 0 and r["resolved"] >= 0.8 * r["entities"] and all(d["path_length_m"] for g in r["detours"] for d in g["directions"] if d["pattern"] != "unknown")),
    ("service_changes", {}, lambda r: r["count"] > 0 and all(c["routes"] and c["title"] for c in r["changes"])),
    ("station", {"query": "York Mills"}, lambda r: all(s["service"] in ("normal", "reduced", "suspended") for s in r["station"]["service"]) and all("status" in p for p in r["station"]["platforms"])),
    ("next_trains", {"query": "York Mills"}, lambda r: all(p["service"] in ("normal", "no_prediction", "suspended") for p in r["platforms"]) and "disruptions" in r),
    ("arrivals", {"stop": "15461"}, lambda r: "disruptions" in r and all("detour_id" in a for a in r["arrivals"])),
    ("feed_status", {}, lambda r: len(r["sources"]) == 9),
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
            print(f"{'PASS' if passed else 'FAIL'} {name} {args} {ms:.0f} ms cache={m.get('cache')} {m['staleness']} errors={m.get('errors') or (m.get('advisories') or {}).get('errors')}")
            if name == "service_status" and ms > 100:
                print(f"     counts={r['counts']} closures={[(c['route'], c['span'], c['active_now']) for c in r['closures']]}")
            if name == "detours":
                print(f"     groups={r['count']} entities={r['entities']} resolved={r['resolved']}")
        d = (await c.call_tool("detours", {})).data
        live = [(g, x) for g in d["detours"] if g["active_today"] for x in g["directions"] if x["pattern"] != "unknown" and x["skipped"]]
        if live:
            g, x = live[0]
            rs = (await c.call_tool("route_stops", {"route": x["route"], "direction": x["direction_id"]})).data
            status = {s["number"]: s["status"] for p in rs["patterns"] for s in p["stops"]}
            hit = [s["number"] for s in x["skipped"] if status.get(s["number"]) == "skipped_by_detour"]
            passed = bool(hit)
            failures += not passed
            print(f"{'PASS' if passed else 'FAIL'} route_stops {x['route']} dir {x['direction_id']}: {len(hit)}/{len(x['skipped'])} skipped stops of detour {g['detour_id']} marked")
        fs = (await c.call_tool("feed_status", {})).data
        for s in fs["sources"]:
            print(f"     {s['name']}: {s['verdict']} age={s.get('age_s')}s signal={s.get('signal')} entities={s.get('entities')}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
