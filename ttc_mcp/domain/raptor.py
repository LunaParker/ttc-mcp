"""The in-memory timetable and the RAPTOR planner behind connections (spec 6.10, decision k)."""
from __future__ import annotations

import datetime as dt
import json
import threading
from array import array
from bisect import bisect_left
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from ttc_mcp.domain.geo import haversine_m, walk_seconds
from ttc_mcp.static.schedule import wall_clock
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
        coords = {sid: (lat, lon) for sid, lat, lon in conn.execute("SELECT stop_id, lat, lon FROM stops WHERE lat IS NOT NULL AND lon IS NOT NULL")}
        members: dict[str, list[str]] = {}
        for st, sid in conn.execute("SELECT station_id, stop_id FROM station_members"):
            members.setdefault(st, []).append(sid)
        for mem in members.values():
            for a in mem:
                for b in mem:
                    if a != b and a in coords and b in coords:
                        m = round(haversine_m(*coords[a], *coords[b]))
                        if b not in foot.get(a, {}) or walk_seconds(m) < foot[a][b][0]:
                            foot.setdefault(a, {})[b] = (walk_seconds(m), m, "walk")
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


INF = 1 << 40
CHANGE_S = 120  # a vehicle change is never promised on less than two minutes of slack (spec 6.10)
MAX_TRANSFERS = 3


def day_offset(from_day: dt.date, to_day: dt.date) -> int:
    return int((wall_clock(from_day, 0) - wall_clock(to_day, 0)).total_seconds())


@dataclass(frozen=True)
class Closed:
    """A rail pattern under closures: positions no trip boards at, none alights at, and the ones a trip is dropped after (6.7 item 4)."""
    NONE: ClassVar[Closed]
    no_board: frozenset[int]
    no_alight: frozenset[int]
    drop_after: frozenset[int]

    @classmethod
    def from_spans(cls, spans: list[tuple[int, int]]) -> Closed:
        nb, na, drop = set(), set(), set()
        for first, last in spans:
            if first == last:
                nb.add(first), na.add(first)
            else:
                nb.update(range(first, last)), na.update(range(first + 1, last + 1)), drop.add(first)
        return cls(frozenset(nb), frozenset(na), frozenset(drop))


Closed.NONE = Closed(frozenset(), frozenset(), frozenset())


@dataclass(frozen=True)
class Slice:
    """One service day's view of a timetable on the query day's clock; the overlay (phase 6c) replaces what it sees."""
    tt: Timetable
    service_ids: frozenset[str]
    offset: int = 0
    overlay: object | None = None

    def patterns_at(self, stop: str) -> Sequence[tuple[int, int]]:
        return self.tt.stop_patterns.get(stop, ())

    def stops_of(self, pattern: int) -> list[str]:
        return self.tt.pattern_stops[pattern]

    def groups_for(self, pattern: int) -> Sequence[Group]:
        return self.tt.groups.get(pattern, ())

    def meta_of(self, pattern: int) -> dict:
        return self.tt.pattern_meta[pattern]


@dataclass(frozen=True)
class Leg:
    kind: str
    frm: str | None
    to: str | None
    depart: int
    arrive: int
    pattern: int | None = None
    slice_idx: int | None = None
    group: int | None = None
    trip: int | None = None
    board_pos: int | None = None
    alight_pos: int | None = None
    metres: int = 0
    via: str | None = None
    basis: str | None = None


@dataclass(frozen=True)
class Journey:
    depart: int
    arrive: int
    legs: tuple[Leg, ...]

    @property
    def rides(self) -> list[Leg]:
        return [l for l in self.legs if l.kind in ("ride", "shuttle")]

    @property
    def transfers(self) -> int:
        return len(self.rides) - 1

    @property
    def walk_m(self) -> int:
        return sum(l.metres for l in self.legs if l.kind == "walk")

    @property
    def estimate(self) -> bool:
        return any(l.basis == "estimate" for l in self.legs)


@dataclass
class Query:
    origin: dict[str, tuple[int, int]]
    dest: dict[str, tuple[int, int]]
    t0: int
    slices: list[Slice]
    max_transfers: int = MAX_TRANSFERS
    usable: Callable[[str], bool] = field(default=lambda s: True)
    closed: Callable[[int], Closed] = field(default=lambda p: Closed.NONE)


def _scan(q: Query, k: int, si: int, pid: int, pos0: int, prev: dict, cur: dict, best: dict, parent: dict, marked: set, target: list[int]) -> None:
    """One pattern in one slice for round k: ride every group from pos0, boarding the earliest trip the previous round allows."""
    sl = q.slices[si]
    stops, closed = sl.stops_of(pid), q.closed(pid)
    for gi, g in enumerate(sl.groups_for(pid)):
        if g.service_id not in sl.service_ids:
            continue
        t = board = -1
        for pos in range(pos0, len(stops)):
            s = stops[pos]
            if t >= 0:
                if pos not in closed.no_alight and (t, pos) not in g.skip and q.usable(s):
                    arr = g.cols[pos][t] + sl.offset
                    if arr < best.get(s, INF) and arr < target[0]:
                        cur[s] = best[s] = arr
                        parent[s] = ("ride", si, pid, gi, t, board, pos)
                        marked.add(s)
                        if s in q.dest:
                            target[0] = min(target[0], arr + q.dest[s][0])
                if pos in closed.drop_after:
                    t = -1
            p = prev.get(s)
            if p is not None and pos not in closed.no_board and q.usable(s):
                need = p + (CHANGE_S if k > 1 else 0) - sl.offset
                if t < 0 or need <= g.cols[pos][t]:
                    t2 = bisect_left(g.cols[pos], need)
                    while t2 < g.n_trips and (t2, pos) in g.skip:
                        t2 += 1
                    if t2 < g.n_trips and (t < 0 or t2 < t):
                        t, board = t2, pos


def plan(q: Query, t0: int | None = None) -> list[Journey]:
    """Standard RAPTOR with marked stops and target pruning; labels keyed by public stop number across every slice."""
    t0 = q.t0 if t0 is None else t0
    rounds = q.max_transfers + 1
    best: dict[str, int] = {}
    tau: list[dict[str, int]] = [{} for _ in range(rounds + 1)]
    parent: list[dict[str, tuple]] = [{} for _ in range(rounds + 1)]
    rode: list[dict[str, int]] = [{} for _ in range(rounds + 1)]
    rparent: list[dict[str, tuple]] = [{} for _ in range(rounds + 1)]
    marked: set[str] = set()
    for s, (w, m) in q.origin.items():
        if q.usable(s):
            tau[0][s] = best[s] = t0 + w
            parent[0][s] = ("origin", w, m)
            marked.add(s)
    target = [min((t0 + q.origin[s][0] + q.dest[s][0] for s in marked if s in q.dest), default=INF)]
    foot = q.slices[0].tt.foot
    for k in range(1, rounds + 1):
        queue: dict[tuple[int, int], int] = {}
        for s in sorted(marked):
            for si, sl in enumerate(q.slices):
                for pid, pos in sl.patterns_at(s):
                    if pos < queue.get((si, pid), INF):
                        queue[(si, pid)] = pos
        marked = set()
        for (si, pid), pos0 in queue.items():
            _scan(q, k, si, pid, pos0, tau[k - 1], tau[k], best, parent[k], marked, target)
        # Footpaths are one hop from this round's ride arrivals, relaxed in stop order so the answer never depends on the hash seed.
        rode[k], rparent[k] = dict(tau[k]), dict(parent[k])
        for s in sorted(rode[k]):
            for s2, secs, m, via in foot.get(s, ()):
                a = rode[k][s] + secs
                if q.usable(s2) and a < best.get(s2, INF) and a < target[0]:
                    tau[k][s2] = best[s2] = a
                    parent[k][s2] = ("walk", s, secs, m, via)
                    marked.add(s2)
                    if s2 in q.dest:
                        target[0] = min(target[0], a + q.dest[s2][0])
        if not marked:
            break
    out, bound = [], INF
    for k in range(1, rounds + 1):
        hits = [(tau[k][s] + w, s) for s, (w, _) in q.dest.items() if s in tau[k]]
        if not hits or min(hits)[0] >= bound:
            continue
        bound, s = min(hits)
        out.append(_journey(q, tau, parent, rode, rparent, k, s))
    return out


def _journey(q: Query, tau: list[dict], parent: list[dict], rode: list[dict], rparent: list[dict], k: int, s: str) -> Journey:
    """Walk the parents back from s; a walk continues from the ride that reached its start, and the origin walk ends as the first ride leaves."""
    legs: list[Leg] = []
    w, m = q.dest[s]
    if w or m:
        legs.append(Leg("walk", s, None, tau[k][s], tau[k][s] + w, metres=m, via="walk"))
    p = parent[k][s]
    while True:
        if p[0] == "origin":
            if p[1] or p[2]:
                legs.append(Leg("walk", None, s, legs[-1].depart - p[1], legs[-1].depart, metres=p[2], via="walk"))
            break
        if p[0] == "ride":
            _, si, pid, gi, t, board, pos = p
            sl = q.slices[si]
            g, stops = sl.groups_for(pid)[gi], sl.stops_of(pid)
            legs.append(Leg("ride", stops[board], stops[pos], g.cols[board][t] + sl.offset, g.cols[pos][t] + sl.offset, pattern=pid, slice_idx=si,
                            group=gi, trip=t, board_pos=board, alight_pos=pos, basis="realtime" if t in g.realtime else "schedule"))
            s, k = stops[board], k - 1
            p = parent[k][s]
        else:
            _, frm, secs, m, via = p
            legs.append(Leg("walk", frm, s, rode[k][frm], rode[k][frm] + secs, metres=m, via=via))
            s, p = frm, rparent[k][frm]
    legs.reverse()
    merged: list[Leg] = []
    for l in legs:
        if merged and l.kind == "walk" and merged[-1].kind == "walk":
            a = merged.pop()
            l = Leg("walk", a.frm, l.to, a.depart, l.arrive, metres=a.metres + l.metres, via="walk" if "walk" in (a.via, l.via) else l.via)
        merged.append(l)
    return Journey(merged[0].depart, merged[-1].arrive, tuple(merged))


def journey_key(q: Query, j: Journey) -> tuple:
    """What makes two journeys the same option: the route sequence with its board and alight stops."""
    return tuple((q.slices[l.slice_idx].meta_of(l.pattern)["route"], l.frm, l.to) for l in j.rides)


WINDOW_DEFAULT_MIN, WINDOW_MAX_MIN, WINDOW_STEP_MIN = 30, 60, 5
ARRIVE_BY_SEARCH_H = 4


def dedupe(q: Query, journeys: list[Journey], *, latest: bool = False) -> list[Journey]:
    kept: dict[tuple, Journey] = {}
    for j in journeys:
        key = journey_key(q, j)
        if key not in kept or (j.depart > kept[key].depart if latest else j.depart < kept[key].depart):
            kept[key] = j
    return list(kept.values())


def rank(journeys: list[Journey], *, latest: bool = False) -> list[Journey]:
    """Arrival first, then transfers, then walking, estimates last; with latest, the latest departure first (arrive-by)."""
    return sorted(journeys, key=lambda j: (j.estimate, -j.depart if latest else j.arrive, j.transfers, j.walk_m))


def plan_window(q: Query, window_s: int, step_s: int = WINDOW_STEP_MIN * 60) -> list[Journey]:
    found = [j for t in range(q.t0, q.t0 + window_s + 1, step_s) for j in plan(q, t)]
    return rank(dedupe(q, found))


def _earliest(q: Query, t: int) -> int:
    js = plan(q, t)
    return min(j.arrive for j in js) if js else INF


def plan_arrive_by(q: Query, target: int, window_s: int, step_s: int = WINDOW_STEP_MIN * 60, floor: int | None = None) -> list[Journey]:
    """The latest on-time departure by bisection to the minute, then a window stepping back from it; no run starts before floor."""
    lo, hi = target - ARRIVE_BY_SEARCH_H * 3600, target
    if floor is not None:
        lo = max(lo, floor)
    if lo >= target or _earliest(q, lo) > target:
        return []
    while hi - lo > 60:
        mid = (lo + hi) // 2
        if _earliest(q, mid) <= target:
            lo = mid
        else:
            hi = mid
    stop = lo - window_s if floor is None else max(lo - window_s, floor)
    found = [j for t in range(lo, stop - 1, -step_s) for j in plan(q, t) if j.arrive <= target]
    return rank(dedupe(q, found, latest=True), latest=True)


def has_service(q: Query, stops) -> bool:
    """Any group at these stops that runs on one of the query's days."""
    return any(g.service_id in sl.service_ids for s in stops for sl in q.slices for pid, _ in sl.patterns_at(s) for g in sl.groups_for(pid))
