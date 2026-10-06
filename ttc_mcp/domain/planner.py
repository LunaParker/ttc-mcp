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


def search(conn, origin: Endpoint, dest: Endpoint, *, now: dt.datetime, max_transfers: int = 1, limit: int = 4, usable=lambda sid: True) -> list[dict]:
    """Direct options, then one-transfer options through shared stops, stations and 150 m neighbours; scheduled departures gate them."""
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

    # A transfer off or onto a pattern the rider could ride straight from the origin is pointless.
    direct_patterns = {pid for o in o_stops for pid, pos_o, _ in o_idx.get(o, []) if first_dest(pid, pos_o)}
    cands = []
    for o, walk_o in o_stops.items():
        for pid, pos_o, _ in o_idx.get(o, []):
            hit = first_dest(pid, pos_o)
            if hit:
                cands.append({"origin": (o, walk_o), "rides": [(pid, pos_o, hit[0])], "transfer": None, "dest": (hit[1], d_stops[hit[1]])})
            if max_transfers < 1 or pid in direct_patterns:
                continue
            r1 = prow(pid)
            end = hit[0] if hit else len(r1)
            nbr: dict[str, tuple[str, int]] = {}
            for pos_x in range(pos_o + 1, end):
                x = r1[pos_x][1]
                if not usable(x):
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
                    if not hit:
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
