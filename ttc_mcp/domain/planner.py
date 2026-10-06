"""Direct and one-transfer trip options over the static patterns (spec 6.7 item 5, decision h)."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from ttc_mcp.domain.arrivals import stop_row
from ttc_mcp.domain.disruptions import station_by_name, station_row
from ttc_mcp.domain.geo import bbox, haversine_m, walk_minutes
from ttc_mcp.static import schedule

WALK_M = 400
TRANSFER_M = 150
TRANSFER_PENALTY_MIN = 4
HORIZON_MIN = 120


@dataclass
class Endpoint:
    label: dict
    stops: dict[str, int]


def resolve_endpoint(conn, spec, *, walk_m: int = WALK_M) -> Endpoint | None:
    """Coordinates give the boarding stops within walk_m; a stop number gives itself; a station gives its members."""
    if isinstance(spec, dict) and "lat" in spec and "lon" in spec:
        lat, lon = float(spec["lat"]), float(spec["lon"])
        lo, hi, wlo, whi = bbox(lat, lon, walk_m)
        stops = {}
        for sid, la, lo_ in conn.execute("SELECT stop_id, lat, lon FROM stops WHERE location_type=0 AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                                         (lo, hi, wlo, whi)):
            d = haversine_m(lat, lon, la, lo_)
            if d <= walk_m:
                stops[sid] = round(d)
        return Endpoint({"lat": lat, "lon": lon}, stops)
    q = str(spec).strip()
    row = stop_row(conn, q)
    if row is not None and row["location_type"] == 0:
        return Endpoint({"stop": row["stop_id"], "name": row["name"]}, {row["stop_id"]: 0})
    station = row if row is not None and row["location_type"] == 1 else (station_row(conn, q) or station_by_name(conn, q))
    if station is None:
        return None
    stops = {r["stop_id"]: r["dist_m"] for r in conn.execute("SELECT stop_id, dist_m FROM station_members WHERE station_id=?", (station["stop_id"],))}
    return Endpoint({"station": station["stop_id"], "name": station["name"]}, stops)


def patterns_at(conn, stops) -> dict[str, list[tuple[int, int, int]]]:
    ids = list(stops)
    out: dict[str, list[tuple[int, int, int]]] = {}
    if not ids:
        return out
    for r in conn.execute(f"SELECT stop_id, pattern_id, pos, secs FROM pattern_stops WHERE stop_id IN ({','.join('?' * len(ids))})", ids):
        out.setdefault(r["stop_id"], []).append((r["pattern_id"], r["pos"], r["secs"]))
    return out


def pattern_rows(conn, pattern_id: int) -> list[tuple[int, str, int]]:
    return [(r["pos"], r["stop_id"], r["secs"]) for r in conn.execute("SELECT pos, stop_id, secs FROM pattern_stops WHERE pattern_id=? ORDER BY pos", (pattern_id,))]


def pattern_meta(conn, pattern_id: int) -> dict:
    r = conn.execute("SELECT p.route_id, p.direction_id, p.headsigns, r.mode FROM patterns p JOIN routes r ON r.route_id=p.route_id WHERE p.pattern_id=?",
                     (pattern_id,)).fetchone()
    hs = json.loads(r["headsigns"])
    return {"pattern_id": pattern_id, "route": r["route_id"], "direction_id": str(r["direction_id"]),
            "headsign": max(hs, key=hs.get) if hs else None, "mode": r["mode"]}


def transfer_stops(conn, stop_id: str) -> dict[str, int]:
    """The stop itself, every stop of a station it belongs to, and stops within 150 m, with walk metres."""
    out = {stop_id: 0}
    for (station,) in conn.execute("SELECT station_id FROM station_members WHERE stop_id=?", (stop_id,)):
        for sid, d in conn.execute("SELECT stop_id, dist_m FROM station_members WHERE station_id=?", (station,)):
            out.setdefault(sid, d)
    for sid, d in conn.execute("SELECT stop_b, dist_m FROM transfers WHERE stop_a=?", (stop_id,)):
        out[sid] = min(out.get(sid, d), d)
    return out


def stop_names(conn, ids) -> dict[str, str]:
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    return {r[0]: r[1] for r in conn.execute(f"SELECT stop_id, name FROM stops WHERE stop_id IN ({','.join('?' * len(ids))})", ids)}


def next_departure(conn, stop_id: str, pattern_id: int, at: dt.datetime) -> dict | None:
    deps = schedule.departures_at(conn, stop_id, at, limit=1, pattern_id=pattern_id)
    return deps[0] if deps and deps[0]["in_min"] <= HORIZON_MIN else None
