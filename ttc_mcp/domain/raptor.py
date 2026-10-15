"""The in-memory timetable and the RAPTOR planner behind connections (spec 6.10, decision k)."""
from __future__ import annotations

import json
import threading
from array import array
from dataclasses import dataclass
from pathlib import Path

from ttc_mcp.domain.geo import walk_seconds
from ttc_mcp.static.timetable import unpack


@dataclass(frozen=True)
class Group:
    """One non-overtaking run of trips on a pattern: cols[pos][trip] departure seconds, sorted along the trip axis."""
    pattern: int
    service_id: str
    cols: list[array]
    trip_ids: array
    skip: frozenset[tuple[int, int]] = frozenset()
    realtime: frozenset[int] = frozenset()

    @property
    def n_trips(self) -> int:
        return len(self.trip_ids)


class Timetable:
    """Immutable after load, shared by every worker thread of one feed."""

    def __init__(self, version: str, pattern_stops: dict[int, list[str]], pattern_meta: dict[int, dict], groups: dict[int, list[Group]],
                 foot: dict[str, list[tuple[str, int, int, str]]]):
        self.version, self.pattern_stops, self.pattern_meta, self.groups, self.foot = version, pattern_stops, pattern_meta, groups, foot
        self.stop_patterns: dict[str, list[tuple[int, int]]] = {}
        for pid, stops in pattern_stops.items():
            for pos, sid in enumerate(stops):
                self.stop_patterns.setdefault(sid, []).append((pid, pos))

    @classmethod
    def load(cls, conn, version: str) -> Timetable:
        pattern_stops: dict[int, list[str]] = {}
        for pid, sid in conn.execute("SELECT pattern_id, stop_id FROM pattern_stops ORDER BY pattern_id, pos"):
            pattern_stops.setdefault(pid, []).append(sid)
        meta = {}
        for pid, route, d, hs, mode in conn.execute("SELECT p.pattern_id, p.route_id, p.direction_id, p.headsigns, r.mode FROM patterns p JOIN routes r ON r.route_id=p.route_id"):
            h = json.loads(hs)
            meta[pid] = {"route": route, "direction_id": str(d), "headsign": max(h, key=h.get) if h else None, "mode": mode}
        groups: dict[int, list[Group]] = {}
        for pid, sid, n_stops, times, ids in conn.execute("SELECT pattern_id, service_id, n_stops, times, trip_ids FROM raptor_groups ORDER BY pattern_id, service_id, split"):
            cols, trip_ids = unpack(n_stops, times, ids)
            groups.setdefault(pid, []).append(Group(pid, sid, cols, trip_ids))
        foot: dict[str, dict[str, tuple[int, int, str]]] = {}
        for a, b, m in conn.execute("SELECT stop_a, stop_b, dist_m FROM transfers"):
            foot.setdefault(a, {})[b] = (walk_seconds(m), m, "walk")
        members: dict[str, list[tuple[str, int]]] = {}
        for st, sid, m in conn.execute("SELECT station_id, stop_id, dist_m FROM station_members"):
            members.setdefault(st, []).append((sid, m))
        for mem in members.values():
            for a, da in mem:
                for b, db in mem:
                    if a != b and (b not in foot.get(a, {}) or walk_seconds(da + db) < foot[a][b][0]):
                        foot.setdefault(a, {})[b] = (walk_seconds(da + db), da + db, "walk")
        for a, b, secs in conn.execute("SELECT stop_a, stop_b, secs FROM station_paths"):
            metres = foot.get(a, {}).get(b, (0, 0, ""))[1]
            foot.setdefault(a, {})[b] = (secs, metres, "pathways")
        return cls(version, pattern_stops, meta, groups, {a: [(b, *v) for b, v in d.items()] for a, d in foot.items()})

    def trip_slot(self, conn, trip_id: str) -> tuple[int, int, int] | None:
        row = conn.execute("SELECT pattern_id FROM trips WHERE trip_id=?", (trip_id,)).fetchone()
        if row is None:
            return None
        tid = int(trip_id)
        for gi, g in enumerate(self.groups.get(row[0], [])):
            try:
                return row[0], gi, g.trip_ids.index(tid)
            except ValueError:
                continue
        return None


_cache: dict[tuple[str, Path], Timetable] = {}
_lock = threading.Lock()


def timetable_for(feed) -> Timetable:
    """One Timetable per loaded feed, built on the calling thread's connection the first time it is asked for."""
    key = (feed.version, feed.path)
    with _lock:
        tt = _cache.get(key)
        if tt is None:
            tt = _cache[key] = Timetable.load(feed.conn, feed.version)
    return tt


def reset() -> None:
    with _lock:
        _cache.clear()
