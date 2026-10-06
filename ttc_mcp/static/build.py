from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
import time
import zipfile
from array import array
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from ttc_mcp import __version__
from ttc_mcp.domain import geo
from ttc_mcp.static import gtfs
from ttc_mcp.static.aliases import SUFFIX, SUFFIX_RADIUS_M, build_aliases, normalize
from ttc_mcp.static.schema import SCHEMA, SCHEMA_VERSION

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
BATCH = 50_000
UNIT_NAME = re.compile(r"^(Elevator|Escalator|Stairs?)[ _-]+(?:([0-9]{1,2}[A-Z][0-9]{1,2}[A-Z])[ _-]+)?(.+)$")
TRANSFER_M = 150
NEAR_STATION_M = 150


@dataclass(frozen=True)
class FeedMeta:
    feed_version: str
    start: str
    end: str
    trips: int
    stops: int
    patterns: int
    departures: int
    shapes: int
    path: Path
    bytes: int


def _log(log, msg):
    (log or (lambda m: print(m, file=sys.stderr)))(msg)


def _digits(value: str, what: str) -> int:
    if not value.isdigit():
        raise ValueError(f"{what} {value!r} is not numeric; the departures table assumes integer ids")
    return int(value)


def build_sqlite(merged_zip: Path, out_path: Path, surface_zip: Path | None = None, *, source_url: str = "",
                 source_etag: str = "", surface_etag: str = "", log=None, service_levels: dict[str, dict] | None = None) -> FeedMeta:
    tmp = out_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(tmp)
    conn.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY; PRAGMA cache_size=-262144;")
    conn.executescript(SCHEMA)
    t0 = time.monotonic()
    with zipfile.ZipFile(merged_zip) as z:
        info = next(gtfs.read_rows(z, "feed_info.txt"))
        n_stops = _load_stops(conn, z)
        routes = _load_routes(z)
        _load_calendar(conn, z)
        trips = {t["trip_id"]: t for t in gtfs.read_rows(z, "trips.txt")}
        _log(log, f"[build] {info['feed_version']}: {len(trips)} trips, {n_stops} stops; streaming stop_times ...")
        counts = _stream_stop_times(conn, z, trips, routes, service_levels or {})
        counts["shapes"] = _load_shapes(conn, z) if gtfs.has(z, "shapes.txt") else 0
        if gtfs.has(z, "pathways.txt"):
            _load_pathways(conn, z)
        if gtfs.has(z, "levels.txt"):
            _load_levels(conn, z)
    if surface_zip is not None:
        _load_crosswalk(conn, surface_zip)
    _attach_orphan_platforms(conn)
    build_aliases(conn)
    _build_units(conn)
    _build_station_members(conn)
    _build_transfers(conn)
    conn.execute(
        "INSERT INTO feed_info VALUES (?,?,?,?,?,?,?,?,?,?)",
        (info["feed_version"], info["feed_start_date"], info["feed_end_date"], info.get("feed_publisher_name"),
         time.strftime("%Y-%m-%dT%H:%M:%S%z"), source_url, source_etag, surface_etag, __version__, SCHEMA_VERSION))
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    os.replace(tmp, out_path)
    size = out_path.stat().st_size
    _log(log, f"[build] done in {time.monotonic() - t0:.0f}s: {out_path} ({size / 1e6:.1f} MB)")
    return FeedMeta(info["feed_version"], info["feed_start_date"], info["feed_end_date"], len(trips), n_stops,
                    counts["patterns"], counts["departures"], counts["shapes"], out_path, size)


def _load_stops(conn, z) -> int:
    rows = []
    for s in gtfs.read_rows(z, "stops.txt"):
        _digits(s["stop_id"], "stop_id")
        rows.append((s["stop_id"], s.get("stop_code") or s["stop_id"], s["stop_name"],
                     float(s["stop_lat"]) if s.get("stop_lat") else None, float(s["stop_lon"]) if s.get("stop_lon") else None,
                     int(s.get("location_type") or 0), s.get("parent_station") or None,
                     int(s.get("wheelchair_boarding") or 0), s.get("level_id") or None))
    conn.executemany("INSERT INTO stops VALUES (?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def _load_routes(z) -> dict[str, dict]:
    routes = {}
    for r in gtfs.read_rows(z, "routes.txt"):
        rt = int(r["route_type"])
        routes[r["route_id"]] = {**r, "route_type": rt, "series": gtfs.series_of(r["route_short_name"]),
                                 "mode": gtfs.mode_of(rt, r["route_short_name"]), "hist": [0] * 48}
    return routes


def _load_calendar(conn, z):
    rows = []
    for c in gtfs.read_rows(z, "calendar.txt"):
        days = sum(1 << i for i, d in enumerate(DAYS) if c.get(d) == "1")
        rows.append((c["service_id"], days, c["start_date"], c["end_date"]))
    conn.executemany("INSERT INTO calendar VALUES (?,?,?,?)", rows)
    if gtfs.has(z, "calendar_dates.txt"):
        conn.executemany("INSERT OR REPLACE INTO calendar_dates VALUES (?,?,?)",
                         [(d["service_id"], d["date"], int(d["exception_type"])) for d in gtfs.read_rows(z, "calendar_dates.txt")])


class _Pattern:
    __slots__ = ("trip_count", "headsigns", "shapes", "offsets", "dists")

    def __init__(self, n: int):
        self.trip_count = 0
        self.headsigns: Counter = Counter()
        self.shapes: Counter = Counter()
        self.offsets = [array("I") for _ in range(n)]
        self.dists = [array("f") for _ in range(n)]


def _stream_stop_times(conn, z, trips: dict[str, dict], routes: dict[str, dict], levels: dict[str, dict]) -> dict[str, int]:
    pattern_ids: dict[tuple, int] = {}
    patterns: dict[int, _Pattern] = {}
    trip_rows, dep_batch, stop_routes = [], [], Counter()
    seen: set[str] = set()
    state = {"cur": None, "rows": [], "deps": 0}

    def flush():
        cur, rows = state["cur"], state["rows"]
        if cur is None or not rows:
            return
        rows.sort(key=lambda r: r[0])
        t = trips[cur]
        direction = int(t.get("direction_id") or 0)
        key = (t["route_id"], direction, tuple((seq, sid) for seq, sid, _, _ in rows))
        pid = pattern_ids.setdefault(key, len(pattern_ids) + 1)
        acc = patterns.setdefault(pid, _Pattern(len(rows)))
        acc.trip_count += 1
        acc.headsigns[t["trip_headsign"]] += 1
        acc.shapes[t.get("shape_id") or ""] += 1
        start = rows[0][2]
        for i, (_, _, s, d) in enumerate(rows):
            acc.offsets[i].append(s - start)
            if d is not None:
                acc.dists[i].append(d)
        trip_rows.append((cur, t["route_id"], t["service_id"], direction, t["trip_headsign"], pid,
                          t.get("shape_id") or None, start, rows[-1][2]))
        routes[t["route_id"]]["hist"][(start % 86400) // 1800] += 1
        tid = _digits(cur, "trip_id")
        for seq, sid, s, _ in rows:
            dep_batch.append((_digits(sid, "stop_id"), s, tid, seq))
            stop_routes[(sid, t["route_id"], direction, t["trip_headsign"])] += 1
        state["deps"] += len(rows)
        if len(dep_batch) >= BATCH:
            conn.executemany("INSERT INTO departures VALUES (?,?,?,?)", dep_batch)
            dep_batch.clear()

    for st in gtfs.read_rows(z, "stop_times.txt"):
        tid = st["trip_id"]
        if tid != state["cur"]:
            flush()
            if tid in seen:
                raise ValueError("stop_times.txt is not grouped by trip_id; the streaming build requires grouping")
            seen.add(tid)
            state["cur"], state["rows"] = tid, []
        state["rows"].append((int(st["stop_sequence"]), st["stop_id"], gtfs.secs(st.get("departure_time") or st["arrival_time"]),
                              float(st["shape_dist_traveled"]) if st.get("shape_dist_traveled") else None))
    flush()
    if dep_batch:
        conn.executemany("INSERT INTO departures VALUES (?,?,?,?)", dep_batch)
    conn.executemany("INSERT INTO trips VALUES (?,?,?,?,?,?,?,?,?)", trip_rows)
    conn.executemany("INSERT INTO stop_routes VALUES (?,?,?,?,?)", [(*k, n) for k, n in stop_routes.items()])
    ranks: dict[tuple, int] = defaultdict(int)
    prow, ps_rows = [], []
    for key, pid in sorted(pattern_ids.items(), key=lambda kv: (-patterns[kv[1]].trip_count, kv[1])):
        route_id, direction, stops = key
        acc = patterns[pid]
        ranks[(route_id, direction)] += 1
        run = [int(median(o)) if len(o) else 0 for o in acc.offsets]
        dists = [round(median(d), 2) if len(d) else None for d in acc.dists]
        prow.append((pid, route_id, direction, ranks[(route_id, direction)], acc.trip_count,
                     json.dumps(dict(acc.headsigns)), json.dumps([[seq, sid] for seq, sid in stops]), json.dumps(run),
                     json.dumps(dists), acc.shapes.most_common(1)[0][0] or None))
        ps_rows.extend((pid, i, sid, run[i], dists[i]) for i, (seq, sid) in enumerate(stops))
    conn.executemany("INSERT INTO patterns VALUES (?,?,?,?,?,?,?,?,?,?)", prow)
    conn.executemany("INSERT INTO pattern_stops VALUES (?,?,?,?,?)", ps_rows)
    route_rows = []
    for rid, r in routes.items():
        lv = levels.get(r["route_short_name"], {})
        route_rows.append((rid, r["route_short_name"], r.get("route_long_name"), r["route_type"], r.get("route_color"),
                           r.get("route_text_color"), r["series"], r["mode"], json.dumps(r["hist"]), lv.get("name"), lv.get("description"),
                           lv.get("frequency"), (1 if lv.get("ten_minute") else 0) if lv else None, lv.get("map_url")))
    conn.executemany("INSERT INTO routes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", route_rows)
    return {"patterns": len(pattern_ids), "departures": state["deps"]}


def _load_shapes(conn, z) -> int:
    """Shapes as encoded polylines with a per-point distance array; shape_dist_traveled when the feed has it, else haversine."""
    acc: dict[str, list] = defaultdict(list)
    for r in gtfs.read_rows(z, "shapes.txt"):
        acc[r["shape_id"]].append((int(r["shape_pt_sequence"]), float(r["shape_pt_lat"]), float(r["shape_pt_lon"]),
                                   float(r["shape_dist_traveled"]) if r.get("shape_dist_traveled") else None))
    rows = []
    for sid, pts in acc.items():
        pts.sort()
        coords = [(lat, lon) for _, lat, lon, _ in pts]
        dists = [d for _, _, _, d in pts]
        if any(d is None for d in dists):
            dists = geo.cumulative_m(coords)
        rows.append((sid, geo.encode_polyline(coords), round(geo.cumulative_m(coords)[-1], 1), len(coords), array("f", dists).tobytes()))
    conn.executemany("INSERT INTO shapes VALUES (?,?,?,?,?)", rows)
    return len(rows)


def _load_pathways(conn, z):
    rows = [(p["pathway_id"], p["from_stop_id"], p["to_stop_id"], int(p["pathway_mode"] or 0), int(p.get("is_bidirectional") or 0),
             float(p["length"]) if p.get("length") else None, int(float(p["traversal_time"])) if p.get("traversal_time") else None,
             int(p["stair_count"]) if p.get("stair_count") else None) for p in gtfs.read_rows(z, "pathways.txt")]
    conn.executemany("INSERT OR REPLACE INTO pathways VALUES (?,?,?,?,?,?,?,?)", rows)


def _load_levels(conn, z):
    conn.executemany("INSERT OR REPLACE INTO levels VALUES (?,?,?)",
                     [(lv["level_id"], float(lv["level_index"]) if lv.get("level_index") else None, lv.get("level_name"))
                      for lv in gtfs.read_rows(z, "levels.txt")])


def _load_crosswalk(conn, surface_zip: Path):
    with zipfile.ZipFile(surface_zip) as z:
        conn.executemany("INSERT OR REPLACE INTO crosswalk VALUES (?,?)",
                         [(s["stop_code"], s["stop_id"]) for s in gtfs.read_rows(z, "stops.txt") if s.get("stop_code")])


def _attach_orphan_platforms(conn):
    """A platform named '<X> Station - ...' with no parent joins the station named X when exactly one exists."""
    orphans = conn.execute("SELECT stop_id, name FROM stops WHERE location_type=0 AND (parent_station IS NULL OR parent_station='') "
                           "AND name LIKE '% Station - %Platform%'").fetchall()
    for sid, name in orphans:
        hits = conn.execute("SELECT stop_id FROM stops WHERE location_type=1 AND name=?", (name.split(" Station - ")[0],)).fetchall()
        if len(hits) == 1:
            conn.execute("UPDATE stops SET parent_station=? WHERE stop_id=?", (hits[0][0], sid))


def _build_units(conn):
    """Pathway nodes named Elevator_{code}_{level} (and Escalator_, Stairs_) become units; nodes without a code keep code NULL."""
    levels = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT level_id, level_index, name FROM levels")}
    rows = []
    for node_id, name, parent, level_id in conn.execute("SELECT stop_id, name, parent_station, level_id FROM stops WHERE location_type=3"):
        m = UNIT_NAME.match(name or "")
        kind = {"Elevator": "elevator", "Escalator": "escalator", "Stair": "stair", "Stairs": "stair"}.get(m.group(1), "other") if m else "other"
        code, level_name = (m.group(2), m.group(3).strip()) if m else (None, None)
        idx, lname = levels.get(level_id, (None, None))
        rows.append((node_id, parent, kind, code, level_name or lname, idx))
    conn.executemany("INSERT INTO units VALUES (?,?,?,?,?,?)", rows)


def _build_station_members(conn):
    """A station's stops: its children, surface stops whose '- X Station' suffix names it, and stops within 150 m of its record."""
    stations = conn.execute("SELECT stop_id, lat, lon FROM stops WHERE location_type=1").fetchall()
    by_id = {sid: (lat, lon) for sid, lat, lon in stations}
    rows: dict[tuple[str, str], tuple[str, int]] = {}
    for sid, _, _ in stations:
        for (cid,) in conn.execute("SELECT stop_id FROM stops WHERE parent_station=? AND location_type=0", (sid,)):
            rows[(sid, cid)] = ("parent", 0)
    aliased = {r[0]: r[1] for r in conn.execute("SELECT alias, station_id FROM station_aliases WHERE source='suffix' AND external IS NULL")}
    for sid, name, lat, lon in conn.execute("SELECT stop_id, name, lat, lon FROM stops WHERE location_type=0 "
                                            "AND (parent_station IS NULL OR parent_station='') AND name LIKE '% - % Station%'"):
        m = SUFFIX.search(name)
        station_id = aliased.get(normalize(m.group(1))) if m else None
        st = by_id.get(station_id)
        if st is not None and lat is not None and st[0] is not None:
            d = round(geo.haversine_m(lat, lon, st[0], st[1]))
            if d <= SUFFIX_RADIUS_M:
                rows.setdefault((station_id, sid), ("suffix", d))
    for sid, lat, lon in stations:
        if lat is None:
            continue
        lo, hi, wlo, whi = geo.bbox(lat, lon, NEAR_STATION_M)
        for cid, clat, clon in conn.execute("SELECT stop_id, lat, lon FROM stops WHERE location_type=0 AND (parent_station IS NULL OR parent_station='') "
                                            "AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?", (lo, hi, wlo, whi)):
            d = round(geo.haversine_m(clat, clon, lat, lon))
            if d <= NEAR_STATION_M:
                rows.setdefault((sid, cid), ("near", d))
    conn.executemany("INSERT INTO station_members VALUES (?,?,?,?)", [(st, sid, how, d) for (st, sid), (how, d) in rows.items()])


def _build_transfers(conn):
    """Every pair of boarding stops within 150 m, stored in both directions, via a coarse grid."""
    stops = conn.execute("SELECT stop_id, lat, lon FROM stops WHERE location_type=0 AND lat IS NOT NULL").fetchall()
    if not stops:
        return
    # Cells span at least TRANSFER_M on both axes, so every partner sits in the stop's own or an adjacent cell.
    cell_lat = TRANSFER_M / 111320
    cell_lon = TRANSFER_M / (111320 * math.cos(math.radians(max(abs(lat) for _, lat, _ in stops))))
    grid: dict[tuple[int, int], list] = defaultdict(list)
    for sid, lat, lon in stops:
        grid[(math.floor(lat / cell_lat), math.floor(lon / cell_lon))].append((sid, lat, lon))
    rows = []
    for (gx, gy), items in grid.items():
        neigh = [s for dx in (-1, 0, 1) for dy in (-1, 0, 1) for s in grid.get((gx + dx, gy + dy), [])]
        for sid, lat, lon in items:
            for sid2, lat2, lon2 in neigh:
                if sid2 != sid:
                    d = geo.haversine_m(lat, lon, lat2, lon2)
                    if d <= TRANSFER_M:
                        rows.append((sid, sid2, round(d)))
    conn.executemany("INSERT OR REPLACE INTO transfers VALUES (?,?,?)", rows)
