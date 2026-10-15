"""Exercise route_shape, accessibility, routes service levels and connections over an in-process client against the live store.

Run: TTC_MCP_SKIP_REFRESH=1 uv run python scripts/smoke_derived.py
"""
import asyncio
import datetime as dt
import sys
import time

from fastmcp import Client

from ttc_mcp import runtime
from ttc_mcp.server import create_app

CHERRY_FRONT = {"lat": 43.6527, "lon": -79.3581}
VERDICTS = ("yes", "no", "unknown")
SEEN: dict[str, int] = {}  # the unsimplified Line 1 point count, for the simplified check after it
BY = (runtime.now() + dt.timedelta(minutes=90)).isoformat(timespec="minutes")


def first_ride(o: dict) -> dict:
    return next(l for l in o["legs"] if l["mode"] in ("ride", "shuttle"))


def leads(o: dict) -> bool:
    """The first vehicle is a ride with its trip and next departures; a shuttle-first option carries neither."""
    ride = first_ride(o)
    return ride["mode"] == "shuttle" or bool(ride["trip_id"]) and "next" in ride


def minutes(o: dict) -> int:
    return o["duration_min"]


def planned(ok):
    """ok over the options, which must exist: a depart-at query waits past its window for the first trip, and night routes serve these pairs."""
    return lambda r: bool(r["options"]) and ok(r["options"])


def arrived(ok):
    """ok over the options, or none when nothing arrives by the target (late at night it can sit past the last trip)."""
    return lambda r: ok(r["options"]) if r["options"] else r.get("note", "").startswith("no journey arriving by")


CHECKS = [
    ("route_shape", {"route": "504", "direction": "0"}, lambda r: r["polyline"] and r["length_m"] > 9000 and len(r["stops"]) == 36 and r["stops"][-1]["dist_m"] > 9000),
    ("route_shape", {"route": "1", "direction": "0", "format": "geojson"}, lambda r: SEEN.setdefault("line1", r["points"]) > 20),
    ("route_shape", {"route": "1", "direction": "0", "format": "geojson", "simplify_m": 20}, lambda r: r["geometry"]["type"] == "LineString" and 20 < r["points"] < SEEN["line1"]),
    ("accessibility", {"query": "Queen"}, lambda r: any(u["code"] == "9Y1L" for u in r["accessibility"]["units"]) and r["accessibility"]["step_free"]["verdict"] in VERDICTS),
    ("accessibility", {"query": "St George"}, lambda r: any("node_match" in u for u in r["accessibility"]["units"]) and r["accessibility"]["step_free"]["verdict"] in VERDICTS),
    ("routes", {"series": "streetcar"}, lambda r: next(x for x in r["routes"] if x["route"] == "504")["service_level"]["ten_minute_network"] is True),
    ("routes", {"overnight": True}, lambda r: all((x["service_level"] or {}).get("name") == "Blue Night Network" for x in r["routes"])),
    ("connections", {"origin": CHERRY_FRONT, "destination": "Union"}, planned(lambda os: all(leads(o) for o in os))),
    ("connections", {"origin": CHERRY_FRONT, "destination": "Union", "arrive_by": BY}, arrived(lambda os: all(o["arrive_at"] <= BY and leads(o) for o in os))),
    ("connections", {"origin": "Finch", "destination": "Union", "max_transfers": 0}, planned(lambda os: all(l["mode"] in ("ride", "shuttle") for l in os[0]["legs"] if l["mode"] != "walk"))),
    ("connections", {"origin": "Kipling", "destination": "Kennedy"}, planned(lambda os: minutes(os[0]) < 120)),
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
                    est = " est" if o["estimate"] else ""
                    print(f"     #{o['rank']} {o['depart_at'][11:16]}-{o['arrive_at'][11:16]} {o['duration_min']} min{est} {o['transfers']} transfer(s) [{legs}] basis={o['basis']} disruptions={len(o['disruptions'])}")
                if not r["options"]:
                    print(f"     no options: {r.get('note')}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
