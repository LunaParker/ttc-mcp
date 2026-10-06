"""Exercise route_shape, accessibility, routes service levels and connections over an in-process client against the live store.

Run: TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_derived.py
"""
import asyncio
import sys
import time

from fastmcp import Client

from ttc_mcp.server import create_app

CHERRY_FRONT = {"lat": 43.6527, "lon": -79.3581}
VERDICTS = ("yes", "no", "unknown")
SEEN: dict[str, int] = {}  # the unsimplified Line 1 point count, for the simplified check after it


def first_ride(o: dict) -> dict:
    return next(l for l in o["legs"] if l["mode"] == "ride")


CHECKS = [
    ("route_shape", {"route": "504", "direction": "0"}, lambda r: r["polyline"] and r["length_m"] > 9000 and len(r["stops"]) == 36 and r["stops"][-1]["dist_m"] > 9000),
    ("route_shape", {"route": "1", "direction": "0", "format": "geojson"}, lambda r: SEEN.setdefault("line1", r["points"]) > 20),
    ("route_shape", {"route": "1", "direction": "0", "format": "geojson", "simplify_m": 20}, lambda r: r["geometry"]["type"] == "LineString" and 20 < r["points"] < SEEN["line1"]),
    ("accessibility", {"query": "Queen"}, lambda r: any(u["code"] == "9Y1L" for u in r["accessibility"]["units"]) and r["accessibility"]["step_free"]["verdict"] in VERDICTS),
    ("accessibility", {"query": "St George"}, lambda r: any("node_match" in u for u in r["accessibility"]["units"]) and r["accessibility"]["step_free"]["verdict"] in VERDICTS),
    ("routes", {"series": "streetcar"}, lambda r: next(x for x in r["routes"] if x["route"] == "504")["service_level"]["ten_minute_network"] is True),
    ("routes", {"overnight": True}, lambda r: all((x["service_level"] or {}).get("name") == "Blue Night Network" for x in r["routes"])),
    ("connections", {"origin": CHERRY_FRONT, "destination": "Union"}, lambda r: r["options"] and any(o["transfers"] == 0 and [l["route"] for l in o["legs"] if l["mode"] == "ride"] == ["121"] and "next" in first_ride(o) for o in r["options"])),
    ("connections", {"origin": CHERRY_FRONT, "destination": "Union"}, lambda r: any([l["route"] for l in o["legs"] if l["mode"] == "ride"] == ["504", "1"] and "next" in first_ride(o) for o in r["options"])),
    ("connections", {"origin": "Finch", "destination": "Union", "max_transfers": 0}, lambda r: r["options"] and all(l["mode"] in ("ride", "shuttle") for l in r["options"][0]["legs"] if l["mode"] != "walk")),
    ("connections", {"origin": "Kipling", "destination": "Kennedy"}, lambda r: r["options"] and r["options"][0]["typical_min"] < 120),
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
            print(f"{'PASS' if passed else 'FAIL'} {name} {args} {ms:.0f} ms cache={m.get('cache')} {m['staleness']}")
            if name == "connections" and "options" in r:
                for o in r["options"]:
                    legs = " > ".join(l["route"] if l["mode"] == "ride" else l["mode"] for l in o["legs"])
                    print(f"     #{o['rank']} {o['transfers']} transfer(s) {o['typical_min']} min [{legs}] basis={o.get('basis')} disruptions={len(o['disruptions'])}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
