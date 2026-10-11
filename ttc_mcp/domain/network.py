from __future__ import annotations

import json

from ttc_mcp.domain.geo import bbox, haversine_m, walk_minutes
from ttc_mcp.static.aliases import resolve_station
from ttc_mcp.static.schedule import day_label, gtfs_hhmm

KINDS = {1: "station", 2: "entrance", 3: "node"}
OVERNIGHT_SHARE = 0.85


def _kind(row) -> str:
    if row["location_type"] in KINDS:
        return KINDS[row["location_type"]]
    return "platform" if row["parent_station"] else "stop"


def _served(conn, stop_id: str) -> list[dict]:
    return [{"route": r["route_id"], "direction_id": str(r["direction_id"]), "headsign": r["headsign"], "trips": r["trip_count"]}
            for r in conn.execute("SELECT * FROM stop_routes WHERE stop_id=? ORDER BY route_id, direction_id, trip_count DESC", (stop_id,))]


def _lines(conn, station_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT sr.route_id FROM stop_routes sr JOIN stops s ON s.stop_id=sr.stop_id JOIN routes r ON r.route_id=sr.route_id "
        "WHERE s.parent_station=? AND r.mode IN ('subway','lrt') ORDER BY sr.route_id", (station_id,)).fetchall()
    return [r[0] for r in rows]


def _aliases(conn, station_id: str) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT alias FROM station_aliases WHERE station_id=? ORDER BY alias", (station_id,))]


def stop_record(conn, row) -> dict:
    """A stop, platform or station as the list tools return it; a station's canonical key is its id (number kept one release)."""
    rec = {"number": row["stop_id"], "name": row["name"], "lat": row["lat"], "lon": row["lon"], "wheelchair": row["wheelchair"],
           "kind": _kind(row), "station": row["parent_station"] or None}
    if rec["kind"] in ("stop", "platform"):
        rec["served"] = _served(conn, row["stop_id"])
    if rec["kind"] == "station":
        rec.update(id=row["stop_id"], aliases=_aliases(conn, row["stop_id"]), lines=_lines(conn, row["stop_id"]))
    return rec


def _modes(conn) -> dict:
    return {r[0]: r[1] for r in conn.execute("SELECT route_id, mode FROM routes")}


def _matches_filters(rec: dict, route, direction, mode, modes: dict) -> bool:
    if not (route or direction or mode):
        return True
    for s in rec.get("served", []):
        if route and s["route"] != route:
            continue
        if direction and s["direction_id"] != direction:
            continue
        if mode and modes.get(s["route"]) != mode:
            continue
        return True
    return False


def find_stops(conn, query: str, *, route=None, direction=None, mode=None, limit: int = 25) -> list[dict]:
    q = query.strip()
    rows = []
    if q.isdigit():
        rows = conn.execute("SELECT * FROM stops WHERE stop_id=? OR stop_code=?", (q, q)).fetchall()
    if not rows:
        for st in resolve_station(conn, q):
            rows.append(st)
            rows += conn.execute("SELECT * FROM stops WHERE parent_station=? AND location_type=0 ORDER BY name", (st["stop_id"],)).fetchall()
    if not rows:
        rows = conn.execute("SELECT * FROM stops WHERE location_type IN (0,1) AND name LIKE ? COLLATE NOCASE "
                            "ORDER BY location_type DESC, name LIMIT ?", (f"%{q}%", limit * 4)).fetchall()
    modes = _modes(conn) if (route or direction or mode) else {}
    out = []
    for r in rows:
        rec = stop_record(conn, r)
        if rec["kind"] == "station" or _matches_filters(rec, route, direction, mode, modes):
            out.append(rec)
    return out[:limit]


def nearest_stops(conn, lat: float, lon: float, *, radius_m: float = 500, limit: int = 10, kind: str = "any") -> list[dict]:
    lo, hi, wlo, whi = bbox(lat, lon, radius_m)
    types = {"any": (0, 1), "stop": (0,), "station": (1,)}[kind]
    marks = ",".join("?" * len(types))
    rows = conn.execute(f"SELECT * FROM stops WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? AND location_type IN ({marks})",
                        (lo, hi, wlo, whi, *types)).fetchall()
    scored = sorted(((haversine_m(lat, lon, r["lat"], r["lon"]), r) for r in rows), key=lambda x: x[0])
    out = []
    for d, r in scored:
        if d > radius_m:
            break
        rec = stop_record(conn, r)
        rec.update(distance_m=round(d), walk_min=walk_minutes(d))
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def stop_detail(conn, number: str) -> dict | None:
    row = conn.execute("SELECT * FROM stops WHERE stop_id=? OR stop_code=?", (number, number)).fetchone()
    if row is None:
        return None
    rec = stop_record(conn, row)
    if rec["station"]:
        rec["station_name"] = conn.execute("SELECT name FROM stops WHERE stop_id=?", (rec["station"],)).fetchone()[0]
    return rec


def station_detail(conn, query: str) -> dict | None:
    hits = resolve_station(conn, query)
    if not hits:
        row = conn.execute("SELECT * FROM stops WHERE stop_id=? AND location_type=1", (query.strip(),)).fetchone()
        if row is None:
            return None
        hits = [row]
    st = hits[0]
    sid = st["stop_id"]
    kids = conn.execute("SELECT * FROM stops WHERE parent_station=? ORDER BY location_type, name", (sid,)).fetchall()
    platforms = [stop_record(conn, k) for k in kids if k["location_type"] == 0]
    entrances = [{"number": k["stop_id"], "name": k["name"], "lat": k["lat"], "lon": k["lon"], "wheelchair": k["wheelchair"]}
                 for k in kids if k["location_type"] == 2]
    flags = {p["wheelchair"] for p in platforms}
    if not flags or flags <= {0}:
        summary = "unknown"
    elif flags == {1}:
        summary = "accessible"
    elif flags == {2}:
        summary = "not accessible"
    else:
        summary = "partially accessible"
    return {"id": sid, "name": st["name"], "aliases": _aliases(conn, sid), "lat": st["lat"], "lon": st["lon"], "lines": _lines(conn, sid),
            "platforms": platforms, "entrances": entrances, "node_count": sum(1 for k in kids if k["location_type"] == 3),
            "wheelchair_summary": summary, "candidates": [h["name"] for h in hits[1:]]}


def overnight_share(hist: list[int]) -> float:
    total = sum(hist)
    return round(sum(hist[1:15]) / total, 3) if total else 0.0


def _service_spans(conn, route_id: str) -> dict:
    cal = {r["service_id"]: r for r in conn.execute("SELECT * FROM calendar")}
    out = {}
    for sid, first, last, n in conn.execute(
            "SELECT service_id, min(start_secs), max(end_secs), count(*) FROM trips WHERE route_id=? GROUP BY service_id", (route_id,)):
        label = day_label(cal[sid]["days"]) if sid in cal and cal[sid]["days"] else f"service {sid}"
        out[label] = {"first": gtfs_hhmm(first), "last": gtfs_hhmm(last), "trips": n}
    return out


def list_routes(conn, *, series=None, overnight=None, mode=None) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM routes ORDER BY CASE WHEN short_name GLOB '[0-9]*' THEN CAST(short_name AS INTEGER) ELSE 99999 END, short_name"):
        share = overnight_share(json.loads(r["start_hist"]))
        night = share >= OVERNIGHT_SHARE
        if series and r["series"] != series:
            continue
        if overnight is not None and night != overnight:
            continue
        if mode and r["mode"] != mode:
            continue
        level = None
        if r["service_level_name"]:
            level = {"name": r["service_level_name"], "description": r["service_level_desc"], "frequency": r["frequency"],
                     "ten_minute_network": bool(r["ten_minute"]), "map_url": r["map_url"]}
        out.append({"route": r["short_name"], "long_name": r["long_name"], "mode": r["mode"], "series": r["series"],
                    "overnight_share": share, "overnight": night, "service": _service_spans(conn, r["route_id"]), "service_level": level})
    return out


def route_stops(conn, route: str, direction: str, pattern: int | None = None) -> dict | None:
    r = conn.execute("SELECT * FROM routes WHERE short_name=? OR route_id=?", (route, route)).fetchone()
    if r is None:
        return None
    rows = conn.execute("SELECT * FROM patterns WHERE route_id=? AND direction_id=? ORDER BY rank", (r["route_id"], int(direction))).fetchall()
    if pattern is not None:
        rows = [p for p in rows if p["rank"] == pattern]
    pats, headsigns = [], {}
    for p in rows:
        hs = json.loads(p["headsigns"])
        for k, v in hs.items():
            headsigns[k] = headsigns.get(k, 0) + v
        run = json.loads(p["run_times"])
        stops = []
        for i, (seq, sid) in enumerate(json.loads(p["stops"])):
            s = conn.execute("SELECT name, lat, lon FROM stops WHERE stop_id=?", (sid,)).fetchone()
            stops.append({"seq": seq, "number": sid, "name": s["name"] if s else None, "lat": s["lat"] if s else None,
                          "lon": s["lon"] if s else None, "typical_min": round(run[i] / 60)})
        pats.append({"rank": p["rank"], "trips": p["trip_count"], "headsigns": hs, "stops": stops, "shape_id": p["shape_id"]})
    return {"route": r["short_name"], "long_name": r["long_name"], "mode": r["mode"], "direction_id": direction,
            "headsigns": headsigns, "patterns": pats}
