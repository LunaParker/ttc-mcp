from __future__ import annotations

import math
import re
import sqlite3

MANUAL = {"dundas": "TMU", "bloor": "Bloor-Yonge", "yonge": "Bloor-Yonge", "sheppard": "Sheppard-Yonge",
          "st clair": "St Clair", "st andrew": "St Andrew", "vmc": "Vaughan Metropolitan Centre", "kennedy rt": "Kennedy"}
SUFFIX = re.compile(r" - (.+?) Station\b")
SUFFIX_RADIUS_M = 300


def normalize(name: str) -> str:
    s = name.lower().replace("&", "and").replace(".", "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:-8].strip() if s.endswith(" station") else s


def _haversine(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def build_aliases(conn: sqlite3.Connection) -> int:
    stations = conn.execute("SELECT stop_id, name, lat, lon FROM stops WHERE location_type=1").fetchall()
    by_name = {normalize(n): sid for sid, n, _, _ in stations}
    rows: set[tuple[str, str, str, str | None]] = set()
    for sid, name, _, _ in stations:
        rows.add((normalize(name), sid, "station", None))
    for name, parent in conn.execute("SELECT name, parent_station FROM stops WHERE location_type=0 "
                                     "AND parent_station IS NOT NULL AND name LIKE '% Station%'"):
        rows.add((normalize(name.split(" Station")[0]), parent, "platform", None))
    for name, lat, lon in conn.execute("SELECT name, lat, lon FROM stops WHERE location_type=0 "
                                       "AND (parent_station IS NULL OR parent_station='') AND name LIKE '% - % Station%'"):
        m = SUFFIX.search(name)
        if not m or lat is None:
            continue
        x = m.group(1)
        if x.endswith(" GO"):
            rows.add((normalize(x), "", "suffix", "GO"))
            continue
        near = [(_haversine(lat, lon, s[2], s[3]), s[0]) for s in stations if s[2] is not None]
        near = [n for n in near if n[0] <= SUFFIX_RADIUS_M]
        if near:
            rows.add((normalize(x), min(near)[1], "suffix", None))
    for alias, target in MANUAL.items():
        sid = by_name.get(normalize(target))
        if sid:
            rows.add((alias, sid, "manual", None))
    conn.executemany("INSERT OR IGNORE INTO station_aliases VALUES (?,?,?,?)", sorted(rows, key=lambda r: (r[0], r[1], r[2])))
    return len(rows)


def resolve_station(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    q = normalize(query)
    if not q:
        return []
    hits = conn.execute(
        "SELECT s.* FROM station_aliases a JOIN stops s ON s.stop_id=a.station_id "
        "WHERE a.alias=? AND a.external IS NULL GROUP BY s.stop_id "
        "ORDER BY MIN(CASE a.source WHEN 'manual' THEN 0 WHEN 'station' THEN 1 WHEN 'platform' THEN 2 ELSE 3 END), s.name",
        (q,)).fetchall()
    if hits:
        return hits
    if conn.execute("SELECT 1 FROM station_aliases WHERE alias=? AND external IS NOT NULL", (q,)).fetchone():
        return []
    return conn.execute("SELECT * FROM stops WHERE location_type=1 AND name LIKE ? COLLATE NOCASE ORDER BY name",
                        (f"%{query.strip()}%",)).fetchall()
