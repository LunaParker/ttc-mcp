from __future__ import annotations

import json
from array import array

from ttc_mcp.domain.geo import decode_polyline, encode_polyline, simplify, slice_by_dist


def _pattern(conn, route: str, direction: str, rank: int):
    return conn.execute("SELECT p.*, r.short_name AS route FROM patterns p JOIN routes r ON r.route_id=p.route_id "
                        "WHERE (r.short_name=? OR r.route_id=?) AND p.direction_id=? AND p.rank=?", (route, route, int(direction), rank)).fetchone()


def _span(rows: list[dict], frm: str, to: str, where: str) -> tuple[int, int]:
    """Positions of the first visit of frm and the first visit of to after it; ValueError says why there is none."""
    a = next((i for i, s in enumerate(rows) if s["number"] == frm), None)
    if a is None:
        raise ValueError(f"stop {frm} is not on {where}")
    b = next((i for i, s in enumerate(rows) if i > a and s["number"] == to), None)
    if b is None:
        if any(s["number"] == to for s in rows):
            raise ValueError(f"stop {to} is not after stop {frm} on {where}")
        raise ValueError(f"stop {to} is not on {where}")
    for i in (a, b):
        if rows[i]["dist_m"] is None:
            raise ValueError(f"no shape distance for stop {rows[i]['number']} on {where}")
    return a, b


def route_shape(conn, route: str, direction: str, *, pattern: int | None = None, fmt: str = "polyline",
                simplify_m: float | None = None, between: tuple[str, str] | None = None) -> dict | None:
    """Decision a: the pattern's shape as a polyline (or GeoJSON), stops with shape_dist_traveled, optional span between two stops.
    None when the pattern does not exist; ValueError when the span cannot be formed (the message says why)."""
    p = _pattern(conn, route, direction, pattern or 1)
    if p is None:
        return None
    stops, dists = json.loads(p["stops"]), json.loads(p["dists"])
    names = {r[0]: r[1] for r in conn.execute(f"SELECT stop_id, name FROM stops WHERE stop_id IN ({','.join('?' * len(stops))})", [s for _, s in stops])}
    rows = [{"seq": seq, "number": sid, "name": names.get(sid), "dist_m": d} for (seq, sid), d in zip(stops, dists)]
    headsigns = json.loads(p["headsigns"])
    out = {"route": p["route"], "direction_id": str(p["direction_id"]), "pattern": p["rank"], "pattern_id": p["pattern_id"],
           "headsign": max(headsigns, key=headsigns.get) if headsigns else None, "shape_id": p["shape_id"], "trips_using": p["trip_count"],
           "format": fmt, "stops": rows}
    span = None
    if between:
        a, b = _span(rows, between[0], between[1], f"pattern {p['rank']} of route {p['route']} direction {p['direction_id']}")
        span = (rows[a]["dist_m"], rows[b]["dist_m"])
        out["stops"] = rows[a:b + 1]
        out["between"] = {"from": between[0], "to": between[1], "length_m": round(span[1] - span[0], 1)}
    shape = conn.execute("SELECT * FROM shapes WHERE shape_id=?", (p["shape_id"],)).fetchone() if p["shape_id"] else None
    if shape is None:
        return {**out, "shape_id": None, "polyline": None, "length_m": None, "points": 0, "note": "no shape for this pattern in the feed"}
    pts = decode_polyline(shape["polyline"])
    if span:
        pd = array("f")
        pd.frombytes(shape["dists"])
        pts = slice_by_dist(pts, pd, *span)
        if len(pts) < 2:
            out["note"] = "the span covers fewer than two shape points"
    if simplify_m:
        pts = simplify(pts, simplify_m)
    out.update(length_m=shape["length_m"], points=len(pts))
    if fmt == "geojson":
        out["geometry"] = {"type": "LineString", "coordinates": [[lon, lat] for lat, lon in pts]}
    else:
        out["polyline"] = encode_polyline(pts)
    return out
