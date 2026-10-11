"""Twenty origin-destination pairs across the city for a reviewer to compare with the TTC trip planner (spec validation item 8).

Run: TTC_MCP_SKIP_REFRESH=1 uv run python scripts/validate_connections.py
Exit 0 when at least 18 pairs return an option under 150 typical minutes (known_min for an option with a shuttle leg).
"""
import asyncio
import sys

from fastmcp import Client

from ttc_mcp.server import create_app

PAIRS = [
    ("Finch", "Union"), ("Kipling", "Kennedy"), ("Vaughan Metropolitan Centre", "Bloor-Yonge"),
    ({"lat": 43.6527, "lon": -79.3581}, "Union"),
    ({"lat": 43.6535, "lon": -79.3841}, {"lat": 43.7764, "lon": -79.2578}),
    ({"lat": 43.6465, "lon": -79.4637}, {"lat": 43.6793, "lon": -79.3447}),
    ({"lat": 43.7290, "lon": -79.6066}, "Yorkdale"), ({"lat": 43.6629, "lon": -79.3957}, "Dundas West"),
    ("Sheppard-Yonge", "Don Mills"), ({"lat": 43.6386, "lon": -79.4203}, "St Clair West"),
    ({"lat": 43.6620, "lon": -79.3300}, "Spadina"), ({"lat": 43.6336, "lon": -79.4180}, "Broadview"),
    ("Eglinton", "Mount Dennis"), ({"lat": 43.7808, "lon": -79.1296}, "Kennedy"), ("Finch West", "Humber College"),
    ({"lat": 43.8177, "lon": -79.1859}, "Scarborough Centre"), ({"lat": 43.6710, "lon": -79.2960}, "Queen"),
    ({"lat": 43.7340, "lon": -79.4500}, "Sheppard West"), ({"lat": 43.6780, "lon": -79.4094}, "Bay"),
    ({"lat": 43.6527, "lon": -79.3581}, "Bloor-Yonge"),
]


async def main() -> int:
    ok = shuttles = 0
    async with Client(create_app()) as c:
        for origin, destination in PAIRS:
            r = (await c.call_tool("connections", {"origin": origin, "destination": destination})).data
            label = f"{origin if isinstance(origin, str) else (origin['lat'], origin['lon'])} -> {destination if isinstance(destination, str) else (destination['lat'], destination['lon'])}"
            if "error" in r or not r["options"]:
                print(f"NONE    {label}: {r.get('error', 'no option')}")
                continue
            o = r["options"][0]
            legs = " > ".join(f"{l['route']} ({l['board']['name']} to {l['alight']['name']})" if l["mode"] == "ride" else l["mode"] for l in o["legs"])
            shuttle = o["typical_min"] is None
            mins = o["known_min"] if shuttle else o["typical_min"]
            good = mins < 150
            ok += good
            shuttles += shuttle
            tag = "SHUTTLE" if shuttle else ("OK     " if good else "SLOW   ")
            print(f"{tag} {label}: {mins} min{' known, shuttle leg unknown' if shuttle else ''}, {o['transfers']} transfer(s), {len(r['options'])} options, basis={o.get('basis')}")
            print(f"       {legs}")
    print(f"{ok}/{len(PAIRS)} pairs with an option under 150 min; {shuttles} with a shuttle leg (counted by known_min)")
    return 0 if ok >= 18 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
