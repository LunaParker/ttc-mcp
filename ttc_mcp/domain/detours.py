from __future__ import annotations

import datetime as dt
import json

from ttc_mcp.adapters.detour_feed import DetourEntity, DetourFeed
from ttc_mcp.adapters.gtfsrt_alerts import RtAlertFeed
from ttc_mcp.adapters.ttcapi import ServiceChange
from ttc_mcp.domain.freshness import iso
from ttc_mcp.domain.geo import decode_polyline, path_length_m
from ttc_mcp.static.schedule import TZ


def day_bounds(day: dt.date) -> tuple[float, float]:
    start = dt.datetime.combine(day, dt.time(), tzinfo=TZ)
    return start.timestamp(), (start + dt.timedelta(days=1)).timestamp()


def overlaps(start, end, day: dt.date) -> bool:
    """start/end as epochs or aware datetimes (None = open); true when the span touches the calendar day."""
    lo, hi = day_bounds(day)
    s = start.timestamp() if isinstance(start, dt.datetime) else start
    e = end.timestamp() if isinstance(end, dt.datetime) else end
    return (s is None or s < hi) and (e is None or e >= lo)


def _stop(conn, sid: str) -> dict:
    r = conn.execute("SELECT name, lat, lon FROM stops WHERE stop_id=?", (sid,)).fetchone()
    return {"number": sid, "name": r["name"] if r else None, "lat": r["lat"] if r else None, "lon": r["lon"] if r else None}


def resolve_entity(conn, e: DetourEntity, shapes: dict[str, str]) -> dict:
    """Map the entity's first trip onto its static pattern; stop_sequence selectors become skipped stops with neighbours."""
    trip = conn.execute("SELECT route_id, direction_id, headsign, pattern_id FROM trips WHERE trip_id=?", (e.trip_ids[0],)).fetchone() if e.trip_ids else None
    poly = shapes.get(e.shape_id or "", "")
    pts = decode_polyline(poly) if poly else []
    base = {"entity_id": e.id, "detour_id": e.detour_id, "route": e.route, "route_label": e.route_label, "direction_id": e.direction_id,
            "destination": e.destination, "trips_affected": len(e.trip_ids), "service_dates": list(e.service_dates),
            "spans": [list(s) for s in e.spans], "path_polyline": poly or None, "path_length_m": round(path_length_m(pts)) if pts else None}
    if trip is None:
        return {**base, "pattern": "unknown", "headsign": None, "skipped": [], "leaves_after": None, "rejoins_at": None}
    pat = conn.execute("SELECT stops FROM patterns WHERE pattern_id=?", (trip["pattern_id"],)).fetchone()
    seq_to_stop = {seq: sid for seq, sid in json.loads(pat["stops"])} if pat else {}
    skipped, leaves, rejoins = [], None, None
    for start, end, _delay in e.spans:
        skipped.extend(_stop(conn, seq_to_stop[k]) for k in range(start, end + 1) if k in seq_to_stop)
        if leaves is None and (start - 1) in seq_to_stop:
            leaves = _stop(conn, seq_to_stop[start - 1])
        if (end + 1) in seq_to_stop:
            rejoins = _stop(conn, seq_to_stop[end + 1])
    return {**base, "pattern": trip["pattern_id"], "route": trip["route_id"], "direction_id": str(trip["direction_id"]),
            "headsign": trip["headsign"], "skipped": skipped, "leaves_after": leaves, "rejoins_at": rejoins}


def reasons_for(route: str, day: dt.date, rt: RtAlertFeed | None, changes: list[ServiceChange] | None) -> dict | None:
    """GTFS-RT MODIFIED_SERVICE first (always published), ttcapi second; route plus date overlap (spec 6.4, decision e)."""
    for a in (rt.alerts if rt else []):
        if a.effect == "MODIFIED_SERVICE" and route in a.routes and overlaps(a.start, a.end, day):
            return {"title": a.header, "text": a.description or None, "url": a.url, "source": "gtfsrt alerts",
                    "period": [iso(a.start) if a.start else None, iso(a.end) if a.end else None]}
    for c in changes or []:
        if route in c.routes and overlaps(c.start, c.end, day):
            return {"title": c.title, "text": c.text or None, "url": c.url, "source": "ttcapi",
                    "period": [c.start.isoformat() if c.start else None, c.end.isoformat() if c.end else None]}
    return None


def resolve_detours(conn, feed: DetourFeed, today: dt.date, *, rt: RtAlertFeed | None = None,
                    changes: list[ServiceChange] | None = None, route: str | None = None) -> list[dict]:
    groups: dict[str, dict] = {}
    key = today.strftime("%Y%m%d")
    for e in feed.entities:
        r = resolve_entity(conn, e, feed.shapes)
        if route and r["route"] != route:
            continue
        g = groups.setdefault(e.detour_id, {"detour_id": e.detour_id, "route": r["route"], "route_label": r["route_label"],
                                            "reason": reasons_for(r["route"], today, rt, changes), "service_dates": [],
                                            "active_today": False, "directions": []})
        g["directions"].append(r)
        g["service_dates"] = sorted(set(g["service_dates"]) | set(r["service_dates"]))
        g["active_today"] = g["active_today"] or key in r["service_dates"]
    for g in groups.values():
        g["service_dates"] = [g["service_dates"][0], g["service_dates"][-1]] if g["service_dates"] else []
        g["skipped_stops"] = sorted({s["number"] for d in g["directions"] for s in d["skipped"]})
    return sorted(groups.values(), key=lambda g: (0, int(g["route"])) if g["route"].isdigit() else (1, g["route"]))


def trip_detour_map(feed: DetourFeed) -> dict[str, str]:
    return {t: e.detour_id for e in feed.entities for t in e.trip_ids}
