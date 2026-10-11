"""Direct and one-transfer trip options over the static patterns (spec 6.7 item 5, decision h)."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from ttc_mcp.domain.arrivals import stop_row
from ttc_mcp.domain.disruptions import UNIT_KINDS, Disruptions, element_key, station_by_name, station_row
from ttc_mcp.domain.geo import bbox, haversine_m, walk_minutes
from ttc_mcp.static import schedule

WALK_M = 400
TRANSFER_M = 150
TRANSFER_PENALTY_MIN = 4
HORIZON_MIN = 120
SHUTTLE_RUNS = ("will operate", "running", "operating")
RAIL = ("subway", "lrt")
DIRECTION_WORDS = ("northbound", "southbound", "eastbound", "westbound")


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
    deps = schedule.departures_at(conn, stop_id, at, limit=1, pattern_id=pattern_id, horizon_min=HORIZON_MIN)
    return deps[0] if deps else None


def _ride(conn, meta: dict, rows: list, a: int, b: int, names: dict) -> dict:
    return {"mode": "ride", "route": meta["route"], "direction_id": meta["direction_id"], "headsign": meta["headsign"], "transit": meta["mode"],
            "pattern_id": meta["pattern_id"], "board": {"number": rows[a][1], "name": names.get(rows[a][1])},
            "alight": {"number": rows[b][1], "name": names.get(rows[b][1])}, "board_pos": a, "alight_pos": b,
            "stops": b - a, "typical_min": round((rows[b][2] - rows[a][2]) / 60)}


def _materialize(conn, cand: dict, metas: dict, rows: dict) -> dict:
    o, walk_o = cand["origin"]
    d, walk_d = cand["dest"]
    ids = [o, d] + [rows[p][i][1] for p, a, b in cand["rides"] for i in (a, b)] + ([cand["transfer"][0], cand["transfer"][1]] if cand["transfer"] else [])
    names = stop_names(conn, ids)
    legs = []
    if walk_o:
        legs.append({"mode": "walk", "to": {"number": o, "name": names.get(o)}, "distance_m": walk_o, "min": walk_minutes(walk_o)})
    for i, (pid, a, b) in enumerate(cand["rides"]):
        if i == 1:
            x, y, walk_xy = cand["transfer"]
            legs.append({"mode": "transfer", "from": {"number": x, "name": names.get(x)}, "to": {"number": y, "name": names.get(y)},
                         "distance_m": walk_xy, "min": walk_minutes(walk_xy) + TRANSFER_PENALTY_MIN})
        legs.append(_ride(conn, metas[pid], rows[pid], a, b, names))
    if walk_d:
        legs.append({"mode": "walk", "from": {"number": d, "name": names.get(d)}, "distance_m": walk_d, "min": walk_minutes(walk_d)})
    total = sum(l["min"] if "min" in l else l["typical_min"] for l in legs)
    return {"transfers": len(cand["rides"]) - 1, "typical_min": total, "walk_m": walk_o + walk_d + (cand["transfer"][2] if cand["transfer"] else 0),
            "stops": sum(b - a for _, a, b in cand["rides"]), "legs": legs}


def search(conn, origin: Endpoint, dest: Endpoint, *, now: dt.datetime, max_transfers: int = 1, limit: int = 4, usable=lambda sid: True,
           rideable=lambda pid, a, b: True) -> list[dict]:
    """Direct options, then one-transfer options through shared stops, stations and 150 m neighbours; scheduled departures gate them.
    Every ride span must be rideable(pattern_id, board_pos, alight_pos) before any pruning, so a closed span is routed around."""
    o_stops = {s: w for s, w in origin.stops.items() if usable(s)}
    d_stops = {s: w for s, w in dest.stops.items() if usable(s)}
    o_idx = patterns_at(conn, o_stops)
    dest_by_pattern: dict[int, list[tuple[int, str]]] = {}
    for sid, hits in patterns_at(conn, d_stops).items():
        for pid, pos, _ in hits:
            dest_by_pattern.setdefault(pid, []).append((pos, sid))
    for v in dest_by_pattern.values():
        v.sort()
    metas, rows, near = {}, {}, {}

    def meta(pid):
        if pid not in metas:
            metas[pid] = pattern_meta(conn, pid)
        return metas[pid]

    def prow(pid):
        if pid not in rows:
            rows[pid] = pattern_rows(conn, pid)
        return rows[pid]

    def nearby(sid):
        if sid not in near:
            near[sid] = transfer_stops(conn, sid)
        return near[sid]

    def first_dest(pid, after):
        return next(((pos, sid) for pos, sid in dest_by_pattern.get(pid, []) if pos > after), None)

    def direct(pid, pos_o):
        hit = first_dest(pid, pos_o)
        return hit if hit and rideable(pid, pos_o, hit[0]) else None

    # A transfer off or onto a pattern the rider could ride straight from the origin is pointless.
    direct_patterns = {pid for o in o_stops for pid, pos_o, _ in o_idx.get(o, []) if direct(pid, pos_o)}
    cands = []
    for o, walk_o in o_stops.items():
        for pid, pos_o, _ in o_idx.get(o, []):
            hit = first_dest(pid, pos_o)
            if hit and rideable(pid, pos_o, hit[0]):
                cands.append({"origin": (o, walk_o), "rides": [(pid, pos_o, hit[0])], "transfer": None, "dest": (hit[1], d_stops[hit[1]])})
            if max_transfers < 1 or pid in direct_patterns:
                continue
            r1 = prow(pid)
            end = hit[0] if hit else len(r1)
            nbr: dict[str, tuple[str, int]] = {}
            for pos_x in range(pos_o + 1, end):
                x = r1[pos_x][1]
                if not usable(x) or not rideable(pid, pos_o, pos_x):
                    continue
                for y, walk_xy in nearby(x).items():
                    if usable(y) and (y not in nbr or walk_xy < nbr[y][1]):
                        nbr[y] = (x, walk_xy)
            y_idx = patterns_at(conn, nbr)
            best: dict[tuple, dict] = {}
            for y, (x, walk_xy) in nbr.items():
                pos_x = next(i for i in range(pos_o + 1, end) if r1[i][1] == x)
                for qid, pos_y, _ in y_idx.get(y, []):
                    if qid == pid or qid in direct_patterns or qid not in dest_by_pattern or meta(qid)["route"] == meta(pid)["route"]:
                        continue
                    hit = first_dest(qid, pos_y)
                    if not hit or not rideable(qid, pos_y, hit[0]):
                        continue
                    cand = {"origin": (o, walk_o), "rides": [(pid, pos_o, pos_x), (qid, pos_y, hit[0])], "transfer": (x, y, walk_xy), "dest": (hit[1], d_stops[hit[1]])}
                    score = (r1[pos_x][2] - r1[pos_o][2]) + walk_minutes(walk_xy) * 60 + (prow(qid)[hit[0]][2] - prow(qid)[pos_y][2])
                    key = (pid, qid)
                    if key not in best or score < best[key][0]:
                        best[key] = (score, cand)
            cands.extend(c for _, c in best.values())
    options, seen = [], {}
    for cand in cands:
        opt = _materialize(conn, cand, {p: meta(p) for p, _, _ in cand["rides"]}, {p: prow(p) for p, _, _ in cand["rides"]})
        first = next(l for l in opt["legs"] if l["mode"] == "ride")
        dep = next_departure(conn, first["board"]["number"], first["pattern_id"], now)
        if dep is None:
            continue
        if opt["transfers"]:
            second = [l for l in opt["legs"] if l["mode"] == "ride"][1]
            at = now + dt.timedelta(minutes=dep["in_min"] + first["typical_min"] + next(l["min"] for l in opt["legs"] if l["mode"] == "transfer"))
            if next_departure(conn, second["board"]["number"], second["pattern_id"], at) is None:
                continue
        opt["first_departure"] = dep
        key = tuple(l["route"] for l in opt["legs"] if l["mode"] == "ride")
        if key not in seen or opt["typical_min"] < seen[key]["typical_min"]:
            seen[key] = opt
    options = sorted(seen.values(), key=lambda o: (o["transfers"], o["typical_min"], o["stops"]))[:limit]
    for i, o in enumerate(options, 1):
        o["rank"] = i
    return options


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


def _rsz_touches(el: dict, platforms: list[tuple[str | None, str]]) -> bool:
    """The zone names a traversed station and its direction word, if any, is in that platform's name (Line 1 headsigns carry none)."""
    at = [name for station, name in platforms if station in el["stations"]]
    word = next((w for w in DIRECTION_WORDS if w in (el.get("direction") or "").lower()), None)
    return bool(at) and (word is None or any(word in name.lower() for name in at))


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


def _closure_verdict(conn, disr: Disruptions, pattern_id: int, route: str, mode: str, a: int, b: int, *,
                     closed: list | None = None) -> tuple[str, dict | None]:
    """The span's verdict: ride (no closure, or one it runs through or turns back at, which annotates), split (ride, shuttle,
    ride) or drop (no shuttle, or a bypassed board or alight station). closed: the pattern's _closed_positions, if known."""
    if mode not in RAIL:
        return "ride", None
    hits = [(pos, el) for pos, el in (_closed_positions(conn, disr, pattern_id, route) if closed is None else closed) if a <= pos <= b]
    if not hits:
        return "ride", None
    (first, el), last = hits[0], hits[-1][0]
    if first == last:
        # A longer closure's end station is served (trains turn back there); any other lone closed station
        # is run through, so a leg boarding or alighting there is dropped.
        ext = _closure_extent(conn, el, pattern_id)
        if ext and ext[0] < ext[1] and first in ext:
            return "ride", el
        return ("ride" if a < first < b else "drop"), el
    return ("split" if _shuttle_runs(el) else "drop"), el


def closure_verdicts(conn, disr: Disruptions):
    """verdict(pattern_id, a, b) for search's rideable predicate, memoising each pattern's line, mode and closed positions."""
    memo: dict[int, tuple[dict, list]] = {}

    def verdict(pid: int, a: int, b: int) -> str:
        if pid not in memo:
            m = pattern_meta(conn, pid)
            memo[pid] = m, (_closed_positions(conn, disr, pid, m["route"]) if m["mode"] in RAIL else [])
        m, closed = memo[pid]
        return _closure_verdict(conn, disr, pid, m["route"], m["mode"], a, b, closed=closed)[0]
    return verdict


def _split_rail_leg(conn, disr: Disruptions, leg: dict, rows: list, found: dict) -> list[dict] | None:
    """Ride, shuttle, ride around a closed block; a lone closed stop is served or bypassed. None drops the option."""
    lo, hi = leg["board_pos"], leg["alight_pos"]
    closed = _closed_positions(conn, disr, leg["pattern_id"], leg["route"])
    verdict, el = _closure_verdict(conn, disr, leg["pattern_id"], leg["route"], leg["transit"], lo, hi, closed=closed)
    if el:
        found[element_key(el)] = el
    if verdict != "split":
        return [leg] if verdict == "ride" else None
    inside = [pos - lo for pos, _ in closed if lo <= pos <= hi]
    first, last = inside[0], inside[-1]
    names = stop_names(conn, [r[1] for r in rows])
    meta = {"pattern_id": leg["pattern_id"], "route": leg["route"], "direction_id": leg["direction_id"], "headsign": leg["headsign"], "mode": leg["transit"]}
    out = []
    if first > 0:
        out.append(_ride(conn, meta, rows, 0, first, names) | {"board_pos": leg["board_pos"], "alight_pos": leg["board_pos"] + first})
    frm, to = _station_of(conn, rows[first][1]), _station_of(conn, rows[last][1])
    # The alert's stopStart/stopEnd may be missing (span None) or half-set; the shuttle's own stations fill the gaps.
    span = el.get("span") or [None, None]
    a, b = span[0] or frm["name"] or "", span[1] or to["name"] or ""
    out.append({"mode": "shuttle", "line": leg["route"], "from": frm, "to": to, "typical_min": None, "alert_id": el.get("alert_id"),
                "note": f"{'subway' if leg['transit'] == 'subway' else 'LRT'} suspended {a} to {b}; shuttle buses operating per alert {el.get('alert_id')}"})
    if last < len(rows) - 1:
        out.append(_ride(conn, meta, rows, last, len(rows) - 1, names) | {"board_pos": leg["board_pos"] + last, "alight_pos": leg["alight_pos"]})
    return out


def overlay(conn, disr: Disruptions, options: list[dict]) -> list[dict]:
    """Decision h: closures cut rail legs (shuttle when offered, else drop); RSZ, suspensions, detours and route-level alerts annotate."""
    kept = []
    for opt in options:
        legs, found, dropped = [], {}, False
        for leg in opt["legs"]:
            if leg["mode"] != "ride":
                legs.append(leg)
                continue
            rows = pattern_rows(conn, leg["pattern_id"])[leg["board_pos"]:leg["alight_pos"] + 1]
            traversed = [r[1] for r in rows]
            if leg["transit"] in RAIL:
                parts = _split_rail_leg(conn, disr, leg, rows, found)
                if parts is None:
                    dropped = True
                    break
                platforms = _platforms(conn, traversed)
                for el in disr.elements:
                    if el["type"] == "rsz" and el["active_now"] and leg["route"] in el["routes"] and _rsz_touches(el, platforms):
                        found[element_key(el)] = el
                legs.extend(parts)
            else:
                for sid in traversed:
                    for el in disr.by_stop.get(sid, []):
                        if el["type"] in ("suspension", "detour") and el["active_now"] and (not el["routes"] or leg["route"] in el["routes"]):
                            found[element_key(el)] = el
                legs.append(leg)
            # Route-level alerts (delays, stopless detours and suspensions) touch every ride on the route.
            for el in disr.by_route.get(leg["route"], []):
                if el["active_now"] and not el["stations"] and not el["stops"] and el["type"] not in (*UNIT_KINDS, "rsz"):
                    found[element_key(el)] = el
        if dropped:
            continue
        total = sum(l["min"] if "min" in l else (l["typical_min"] or 0) for l in legs)
        kept.append({**opt, "legs": legs, "typical_min": total, "stops": sum(l["stops"] for l in legs if l["mode"] == "ride"),
                     "disruptions": list(found.values()),
                     **({"typical_min_note": "excludes the shuttle leg"} if any(l["mode"] == "shuttle" for l in legs) else {})})
    kept.sort(key=lambda o: (o["transfers"], o["typical_min"], o["stops"]))
    for i, o in enumerate(kept, 1):
        o["rank"] = i
    return kept
