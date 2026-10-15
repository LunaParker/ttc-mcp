"""Endpoints, closure spans and the contract options around the RAPTOR engine (spec 6.7 item 5, decision h)."""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from functools import partial

from ttc_mcp.domain import raptor
from ttc_mcp.domain.arrivals import stop_row
from ttc_mcp.domain.disruptions import UNIT_KINDS, Disruptions, element_key, station_by_name, station_row
from ttc_mcp.domain.geo import bbox, haversine_m, walk_minutes
from ttc_mcp.static.schedule import wall_clock

WALK_M = 400
SHUTTLE_RUNS = ("will operate", "running", "operating")
RAIL = ("subway", "lrt")
DIRECTION_WORDS = ("northbound", "southbound", "eastbound", "westbound")
# The terminal an RSZ's direction text names ("Southbound From Finch", "Eastbound To Kennedy"); the last To/From wins.
TERMINAL = re.compile(r"^.*\b(to|from)\s+(\S.*?)\s*$", re.IGNORECASE)


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


def pattern_rows(conn, pattern_id: int) -> list[tuple[int, str, int]]:
    return [(r["pos"], r["stop_id"], r["secs"]) for r in conn.execute("SELECT pos, stop_id, secs FROM pattern_stops WHERE pattern_id=? ORDER BY pos", (pattern_id,))]


def pattern_meta(conn, pattern_id: int) -> dict:
    r = conn.execute("SELECT p.route_id, p.direction_id, p.headsigns, r.mode FROM patterns p JOIN routes r ON r.route_id=p.route_id WHERE p.pattern_id=?",
                     (pattern_id,)).fetchone()
    hs = json.loads(r["headsigns"])
    return {"pattern_id": pattern_id, "route": r["route_id"], "direction_id": str(r["direction_id"]),
            "headsign": max(hs, key=hs.get) if hs else None, "mode": r["mode"]}


def stop_names(conn, ids) -> dict[str, str]:
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    return {r[0]: r[1] for r in conn.execute(f"SELECT stop_id, name FROM stops WHERE stop_id IN ({','.join('?' * len(ids))})", ids)}


def _station_of(conn, stop_id: str) -> dict:
    r = conn.execute("SELECT p.stop_id, p.name FROM stops s JOIN stops p ON p.stop_id=s.parent_station WHERE s.stop_id=?", (stop_id,)).fetchone()
    return {"station": r["stop_id"], "name": r["name"]} if r else {"station": None, "name": None}


def _shuttle_runs(el: dict) -> bool:
    return str(el.get("shuttle") or "").strip().lower() in SHUTTLE_RUNS


def _platforms(conn, ids: list[str]) -> list[tuple[str | None, str]]:
    """(parent station, name) of the stops, in the given order."""
    if not ids:
        return []
    rows = {r[0]: (r[1], r[2]) for r in conn.execute(f"SELECT stop_id, parent_station, name FROM stops WHERE stop_id IN ({','.join('?' * len(ids))})", ids)}
    return [rows[sid] for sid in ids if sid in rows]


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().replace("-", " ").replace(".", "").split())


def _headsign_names(headsign: str | None, name: str) -> bool:
    """The headsign points at the terminal: the name is a prefix of the station after 'towards' ('... towards Finch Station')."""
    h, x = _norm(headsign), _norm(name)
    tail = h.split(" towards ")[-1]
    return bool(x) and (tail.startswith(x) or f" {x}" in f" {h}")


def _rsz_touches(el: dict, leg: dict, platforms: list[tuple[str | None, str]]) -> bool:
    """The zone names a traversed station and runs the leg's way: by its terminal when the text says "To X" (the headsign names X)
    or "From X" (it does not), else by its direction word in that platform's name; "Both ways" or no direction always does."""
    at = [name for station, name in platforms if station in el["stations"]]
    if not at:
        return False
    text = el.get("direction") or ""
    if m := TERMINAL.match(text):
        named = _headsign_names(leg["headsign"], m.group(2))
        return named if m.group(1).lower() == "to" else not named
    word = next((w for w in DIRECTION_WORDS if w in text.lower()), None)
    return word is None or any(word in name.lower() for name in at)


def _closure_extent(conn, el: dict, pattern_id: int) -> tuple[int, int] | None:
    """Lowest and highest pattern positions of the closure's stations; None when it names none on the pattern."""
    ids = list(el.get("stations") or [])
    if not ids:
        return None
    r = conn.execute(f"SELECT MIN(ps.pos), MAX(ps.pos) FROM pattern_stops ps JOIN stops s ON s.stop_id=ps.stop_id "
                     f"WHERE ps.pattern_id=? AND s.parent_station IN ({','.join('?' * len(ids))})", (pattern_id, *ids)).fetchone()
    return (r[0], r[1]) if r[0] is not None else None


def _closed_positions(conn, disr: Disruptions, pattern_id: int, route: str) -> list[tuple[int, dict]]:
    """The pattern positions closed now for this line with the closing element: the station's closure (the whole span, so the
    split reaches both ends although an end platform may be served), else the platform's own listing."""
    out = []
    for pos, sid, _ in pattern_rows(conn, pattern_id):
        row = stop_row(conn, sid)
        el = disr.station_closed_now(row["parent_station"], route) or disr.platform_closed_now(row)
        if not el:
            continue
        # Belt and braces: phase 3 already ignores an RT listing that names only other lines.
        if el["source"] == "gtfsrt alerts" and el["routes"] and route not in el["routes"]:
            continue
        out.append((pos, el))
    return out


def _annotations(conn, disr: Disruptions, leg: dict, rows: list) -> list[dict]:
    """What a ride passes through: active zones in its direction on rail; suspensions and detours at the stops of a surface leg."""
    traversed = [r[1] for r in rows[leg["board_pos"]:leg["alight_pos"] + 1]]
    if leg["transit"] in RAIL:
        platforms = _platforms(conn, traversed)
        return [el for el in disr.elements if el["type"] == "rsz" and el["active_now"] and leg["route"] in el["routes"] and _rsz_touches(el, leg, platforms)]
    return [el for sid in traversed for el in disr.by_stop.get(sid, [])
            if el["type"] in ("suspension", "detour") and el["active_now"] and (not el["routes"] or leg["route"] in el["routes"])]


def _touch(found: dict, affects: dict, el: dict, numbers: list[int]) -> None:
    """Record a disruption once per option and widen the legs it affects."""
    key = element_key(el)
    found.setdefault(key, el)
    affects[key] = sorted(set(affects.get(key, ())) | set(numbers))


def closed_for(conn, disr: Disruptions, tt: raptor.Timetable):
    """closed(pattern_id) -> raptor.Closed: each closure's lowest and highest position on a rail pattern is a span (6.7 item 4).
    tt is conn's feed; positions are read only when a closure is active on a rail line at the pattern's stops or GTFS-RT lists a platform."""
    memo: dict[int, raptor.Closed] = {}
    closing = {line for el in disr.elements if el["type"] == "closure" and el["active_now"] for line in el["routes"]}
    meta = tt.pattern_meta

    def closable(pid: int) -> bool:
        if disr.platform_closed:
            return True
        return bool(closing and closing & {meta[p]["route"] for s in tt.pattern_stops[pid] for p, _ in tt.stop_patterns.get(s, ()) if meta[p]["mode"] in RAIL})

    def closed(pid: int) -> raptor.Closed:
        if pid not in memo:
            spans: dict[tuple, list[int]] = {}
            m = meta[pid] if pid >= 0 else None
            if m and m["mode"] in RAIL and closable(pid):
                for pos, el in _closed_positions(conn, disr, pid, m["route"]):
                    spans.setdefault(element_key(el), []).append(pos)
            memo[pid] = raptor.Closed.from_spans([(min(p), max(p)) for p in spans.values()])
        return memo[pid]
    return closed


def _stop(number: str, names: dict) -> dict:
    return {"number": number, "name": names.get(number)}


def _minutes(a: int, b: int) -> int:
    return round((b - a) / 60)


def _span(a: int, b: int) -> int:
    """Minutes between two printed times (seconds truncated), so the numbers a reader adds up agree with the clocks beside them."""
    return b // 60 - a // 60


def materialize(conn, q: raptor.Query, j: raptor.Journey, day: dt.date) -> dict:
    """A journey as the spec 4.4 option, rank, next and disruptions left to the caller; rides keep base-pattern positions and their
    slice for annotate, since pattern ids are per feed and the two day slices may come from different feeds."""
    def iso(s: int) -> str:
        return wall_clock(day, s).isoformat(timespec="minutes")
    names = stop_names(conn, [x for l in j.legs for x in (l.frm, l.to) if x])
    legs: list[dict] = []
    for i, l in enumerate(j.legs):
        if l.kind == "walk":
            prev = j.legs[i - 1] if i and j.legs[i - 1].kind != "walk" else None
            nxt = j.legs[i + 1] if i + 1 < len(j.legs) and j.legs[i + 1].kind != "walk" else None
            if prev and nxt:
                legs.append({"mode": "transfer", "from": _stop(l.frm, names), "to": _stop(l.to, names), "distance_m": l.metres, "walk_min": walk_minutes(l.metres),
                             "via": l.via, "depart": iso(prev.arrive), "arrive": iso(nxt.depart), "min": _span(prev.arrive, nxt.depart)})
            else:
                end = {"to": _stop(l.to, names)} if nxt else {"from": _stop(l.frm, names)}
                legs.append({"mode": "walk", **end, "distance_m": l.metres, "min": walk_minutes(l.metres), "depart": iso(l.depart), "arrive": iso(l.arrive)})
            continue
        sl = q.slices[l.slice_idx]
        meta = sl.meta_of(l.pattern)
        if legs and legs[-1]["mode"] in ("ride", "shuttle"):
            # Two vehicles at one stop: the change still takes the buffer, and the slack is shown like any other transfer.
            prev = j.legs[i - 1]
            legs.append({"mode": "transfer", "from": _stop(l.frm, names), "to": _stop(l.frm, names), "distance_m": 0, "walk_min": 0, "via": "walk",
                         "depart": iso(prev.arrive), "arrive": iso(l.depart), "min": _span(prev.arrive, l.depart)})
        if l.kind == "shuttle":
            legs.append({"mode": "shuttle", "line": meta["line"], "from": meta["station_of"][l.frm], "to": meta["station_of"][l.to], "depart": iso(l.depart),
                         "arrive": iso(l.arrive), "min": _span(l.depart, l.arrive), "basis": "estimate", "alert_id": meta["alert_id"], "note": meta["note"]})
            continue
        base, positions = meta.get("base_pattern", l.pattern), meta.get("positions")
        board_b, alight_b = (positions[l.board_pos], positions[l.alight_pos]) if positions else (l.board_pos, l.alight_pos)
        rows = pattern_rows(sl.conn or conn, base)
        g = sl.groups_for(l.pattern)[l.group]
        legs.append({"mode": "ride", "route": meta["route"], "direction_id": meta["direction_id"], "headsign": meta["headsign"], "transit": meta["mode"],
                     "pattern_id": base, "trip_id": str(g.trip_ids[l.trip]), "board": _stop(l.frm, names), "alight": _stop(l.to, names),
                     "stops": l.alight_pos - l.board_pos, "depart": iso(l.depart), "arrive": iso(l.arrive), "min": _span(l.depart, l.arrive),
                     "typical_min": _minutes(rows[board_b][2], rows[alight_b][2]), "basis": l.basis, "board_pos": board_b, "alight_pos": alight_b, "slice": l.slice_idx,
                     **({"detour_id": meta["detour_id"]} if meta.get("detour_id") else {})})
    rides = j.rides
    return {"transfers": j.transfers, "depart_at": iso(j.depart), "arrive_at": iso(j.arrive), "duration_min": _span(j.depart, j.arrive), "walk_m": j.walk_m,
            "stops": sum(l["stops"] for l in legs if l["mode"] == "ride"), "basis": rides[0].basis if rides else "schedule", "estimate": j.estimate, "legs": legs}


def annotate(q: raptor.Query, disr: Disruptions, options: list[dict]) -> list[dict]:
    """Decision h's annotations on contract options: zones, delays and route-level alerts on rides, the closure on a shuttle, the detour on a variant ride.
    A ride reads its patterns through its slice's feed, else the disruptions' connection (the feed in hand on this thread)."""
    for opt in options:
        found, affects = {}, {}
        touch = partial(_touch, found, affects)
        for n, leg in enumerate(opt["legs"], 1):
            if leg["mode"] == "ride":
                conn = q.slices[leg["slice"]].conn or disr.conn
                for el in _annotations(conn, disr, leg, pattern_rows(conn, leg["pattern_id"])):
                    touch(el, [n])
                for el in disr.by_route.get(leg["route"], []):
                    if el["active_now"] and not el["stations"] and not el["stops"] and el["type"] not in (*UNIT_KINDS, "rsz"):
                        touch(el, [n])
                if leg.get("detour_id"):
                    for el in disr.elements:
                        if el.get("detour_id") == leg["detour_id"] and el.get("direction_id") == leg["direction_id"]:
                            touch(el, [n])
            elif leg["mode"] == "shuttle":
                for el in disr.elements:
                    if el.get("alert_id") == leg["alert_id"]:
                        touch(el, [n])
        opt["disruptions"] = [{**el, "affects": affects[key]} for key, el in found.items()]
    return options
