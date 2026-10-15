"""The in-memory timetable and the RAPTOR planner behind connections (spec 6.10, decision k)."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from array import array
from bisect import bisect_left
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import ClassVar

from ttc_mcp.domain.geo import haversine_m, walk_seconds
from ttc_mcp.static.schedule import wall_clock
from ttc_mcp.static.timetable import unpack

INF = 1 << 40


@dataclass(frozen=True)
class Group:
    """One non-overtaking run of trips on a pattern: cols[pos][trip] departure seconds, sorted along the trip axis."""
    pattern: int
    service_id: str
    cols: list[array]
    trip_ids: array
    skip: frozenset[tuple[int, int]] = frozenset()
    realtime: frozenset[int] = frozenset()
    # The latest departure at any position on the group's own day: the scan skips the group once the query instant is past it.
    last: int = field(init=False, compare=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, "last", max((c[-1] for c in self.cols if c), default=-INF))

    @property
    def n_trips(self) -> int:
        return len(self.trip_ids)


@dataclass(frozen=True)
class Headway:
    """A frequency-based pseudo-route (a closure shuttle): wait the full headway, then ride the cumulative seconds."""
    pattern: int
    headway_s: int
    cum: tuple[int, ...]
    service_id: str = "*"
    skip: frozenset = frozenset()
    realtime: frozenset = frozenset()
    last: int = INF  # never spent: the scan's skip reads it

    @property
    def n_trips(self) -> int:
        return 0


class Timetable:
    """Immutable after load, shared by every worker thread of one feed."""

    def __init__(self, version: str, pattern_stops: dict[int, list[str]], pattern_meta: dict[int, dict], groups: dict[int, list[Group]],
                 foot: dict[str, list[tuple[str, int, int, str]]], pattern_seqs: dict[int, list[int]] | None = None):
        self.version, self.pattern_stops, self.pattern_meta, self.groups, self.foot = version, pattern_stops, pattern_meta, groups, foot
        self.pattern_seqs = pattern_seqs or {}  # GTFS stop_sequence per position, shared by every trip of the pattern
        self.stop_patterns: dict[str, list[tuple[int, int]]] = {}
        for pid, stops in pattern_stops.items():
            for pos, sid in enumerate(stops):
                self.stop_patterns.setdefault(sid, []).append((pid, pos))

    @classmethod
    def load(cls, conn, version: str) -> Timetable:
        pattern_stops: dict[int, list[str]] = {}
        for pid, sid in conn.execute("SELECT pattern_id, stop_id FROM pattern_stops ORDER BY pattern_id, pos"):
            pattern_stops.setdefault(pid, []).append(sid)
        meta, seqs = {}, {}
        for pid, route, d, hs, mode, st in conn.execute("SELECT p.pattern_id, p.route_id, p.direction_id, p.headsigns, r.mode, p.stops FROM patterns p JOIN routes r ON r.route_id=p.route_id"):
            h = json.loads(hs)
            meta[pid] = {"route": route, "direction_id": str(d), "headsign": max(h, key=h.get) if h else None, "mode": mode}
            seqs[pid] = [q for q, _ in json.loads(st)]
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
        return cls(version, pattern_stops, meta, groups, {a: [(b, *v) for b, v in d.items()] for a, d in foot.items()}, seqs)

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
    """One service day's view of a timetable on the query day's clock; the overlay (phase 6c) replaces what it sees.
    conn reads this slice's own feed (pattern ids are per feed), on the planning thread."""
    tt: Timetable
    service_ids: frozenset[str]
    offset: int = 0
    overlay: object | None = None
    conn: sqlite3.Connection | None = None

    def patterns_at(self, stop: str) -> Sequence[tuple[int, int]]:
        return self.overlay.patterns_at(self.tt, stop) if self.overlay else self.tt.stop_patterns.get(stop, ())

    def stops_of(self, pattern: int) -> list[str]:
        return self.overlay.stops_of(self.tt, pattern) if self.overlay else self.tt.pattern_stops[pattern]

    def groups_for(self, pattern: int) -> Sequence[Group]:
        return self.overlay.groups_for(self.tt, pattern) if self.overlay else self.tt.groups.get(pattern, ())

    def meta_of(self, pattern: int) -> dict:
        return self.overlay.meta_of(self.tt, pattern) if self.overlay else self.tt.pattern_meta[pattern]

    def spent(self, t: int) -> frozenset[int]:
        """The timetable's patterns with no group on this slice's services departing at or after t on the query day's clock; a pattern the
        timetable does not hold (an overlay's pseudo pattern) is never spent."""
        sids, off = self.service_ids, self.offset
        return frozenset(pid for pid in self.tt.groups if not any(g.service_id in sids and g.last + off >= t for g in self.groups_for(pid)))


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
    closed: Callable[[int, int], Closed] = field(default=lambda si, p: Closed.NONE)
    # Per slice, Slice.spent at the earliest instant any run will use: the drivers fill it and plan never queues those patterns.
    spent: list[frozenset[int]] | None = None
    # A stop's queue candidates ((slice, pattern), position) under spent, shared by the runs of one driver call (its own copy of the query).
    stops_at: dict[str, list[tuple[tuple[int, int], int]]] | None = None


def _scan(q: Query, k: int, si: int, pid: int, pos0: int, t0: int, prev: dict, cur: dict, best: dict, parent: dict, marked: set, target: list[int]) -> None:
    """One pattern in one slice for round k: ride every group from pos0, boarding the earliest trip the previous round allows.
    Every label is at or after t0, so a group whose last departure is before it can board nothing and is skipped."""
    sl = q.slices[si]
    stops, closed, off, sids = sl.stops_of(pid), q.closed(si, pid), sl.offset, sl.service_ids
    nb, na, drop = closed.no_board, closed.no_alight, closed.drop_after
    usable, dest, best_get, prev_get = q.usable, q.dest, best.get, prev.get
    wait, n_stops = (CHANGE_S if k > 1 else 0) - off, len(stops)
    for gi, g in enumerate(sl.groups_for(pid)):
        if (g.service_id not in sids and g.service_id != "*") or g.last + off < t0:
            continue
        if isinstance(g, Headway):
            _scan_headway(q, k, si, pid, gi, g, stops, pos0, prev, cur, best, parent, marked, target)
            continue
        cols, skip, n = g.cols, g.skip, len(g.trip_ids)
        t = board = -1
        for pos in range(pos0, n_stops):
            s = stops[pos]
            if t >= 0:
                if pos not in na and (t, pos) not in skip and usable(s):
                    arr = cols[pos][t] + off
                    if arr < best_get(s, INF) and arr < target[0]:
                        cur[s] = best[s] = arr
                        parent[s] = ("ride", si, pid, gi, t, board, pos)
                        marked.add(s)
                        if s in dest:
                            target[0] = min(target[0], arr + dest[s][0])
                if pos in drop:
                    t = -1
            p = prev_get(s)
            if p is not None and pos not in nb and usable(s):
                need = p + wait
                col = cols[pos]
                if t < 0 or need <= col[t]:
                    t2 = bisect_left(col, need)
                    while t2 < n and (t2, pos) in skip:
                        t2 += 1
                    if t2 < n and (t < 0 or t2 < t):
                        t, board = t2, pos


def _scan_headway(q: Query, k: int, si: int, pid: int, gi: int, g: Headway, stops: list[str], pos0: int, prev: dict, cur: dict, best: dict, parent: dict,
                  marked: set, target: list[int]) -> None:
    """A frequency route: board after the buffer and the full headway, and reboard downstream whenever that leaves before the shuttle ridden passes."""
    cum, dep, board = g.cum, -1, -1
    for pos in range(pos0, len(stops)):
        s = stops[pos]
        if dep >= 0 and q.usable(s):
            arr = dep + cum[pos] - cum[board]
            if arr < best.get(s, INF) and arr < target[0]:
                cur[s] = best[s] = arr
                parent[s] = ("shuttle", si, pid, gi, dep, board, pos)
                marked.add(s)
                if s in q.dest:
                    target[0] = min(target[0], arr + q.dest[s][0])
        p = prev.get(s)
        if p is not None and q.usable(s):
            cand = p + (CHANGE_S if k > 1 else 0) + g.headway_s
            if dep < 0 or cand < dep + cum[pos] - cum[board]:
                dep, board = cand, pos


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
    usable, dest, best_get = q.usable, q.dest, best.get
    for s, (w, m) in q.origin.items():
        if usable(s):
            tau[0][s] = best[s] = t0 + w
            parent[0][s] = ("origin", w, m)
            marked.add(s)
    target = [min((t0 + q.origin[s][0] + dest[s][0] for s in marked if s in dest), default=INF)]
    foot_get = q.slices[0].tt.foot.get
    spent = q.spent or [frozenset()] * len(q.slices)
    ats = [(si, sl.patterns_at, spent[si]) for si, sl in enumerate(q.slices)]
    cands = q.stops_at if q.stops_at is not None else {}
    cands_get = cands.get
    for k in range(1, rounds + 1):
        queue: dict[tuple[int, int], int] = {}
        queue_get = queue.get
        for s in sorted(marked):
            c = cands_get(s)
            if c is None:
                c = cands[s] = [((si, pid), pos) for si, at, dead in ats for pid, pos in at(s) if pid not in dead]
            for key, pos in c:
                if pos < queue_get(key, INF):
                    queue[key] = pos
        marked = set()
        prev, cur, pk = tau[k - 1], tau[k], parent[k]
        for (si, pid), pos0 in queue.items():
            _scan(q, k, si, pid, pos0, t0, prev, cur, best, pk, marked, target)
        # Footpaths are one hop from this round's ride arrivals, relaxed in stop order so the answer never depends on the hash seed.
        r = rode[k] = dict(cur)
        rparent[k] = dict(pk)
        for s in sorted(r):
            rs = r[s]
            for s2, secs, m, via in foot_get(s, ()):
                a = rs + secs
                if usable(s2) and a < best_get(s2, INF) and a < target[0]:
                    cur[s2] = best[s2] = a
                    pk[s2] = ("walk", s, secs, m, via)
                    marked.add(s2)
                    if s2 in dest:
                        target[0] = min(target[0], a + dest[s2][0])
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
        if p[0] == "shuttle":
            _, si, pid, gi, dep, board, pos = p
            sl = q.slices[si]
            g, stops = sl.groups_for(pid)[gi], sl.stops_of(pid)
            legs.append(Leg("shuttle", stops[board], stops[pos], dep, dep + g.cum[pos] - g.cum[board], pattern=pid, slice_idx=si, group=gi,
                            board_pos=board, alight_pos=pos, basis="estimate"))
            s, k = stops[board], k - 1
            p = parent[k][s]
        elif p[0] == "ride":
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
WINDOW_LONG_STEP_MIN = 10  # a window past the default steps every 10 minutes: 13 runs at 5 broke the 250 ms p95 budget on the live store
ARRIVE_BY_STEP_MIN = 15  # arrive-by's window after its search steps every 15 minutes, the spec's fallback: 3 runs instead of 7
ARRIVE_BY_SEARCH_H = 4
ARRIVE_BY_RESOLUTION_S = 300  # six halvings of the four-hour span instead of eight keep arrive-by in budget; a departure within 5 min of the latest


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


def _with_spent(q: Query, t_first: int) -> Query:
    """The query with each slice's spent patterns at t_first, for runs that never start before it."""
    return replace(q, spent=[sl.spent(t_first) for sl in q.slices], stops_at={})


def plan_window(q: Query, window_s: int, step_s: int = WINDOW_STEP_MIN * 60) -> list[Journey]:
    q = _with_spent(q, q.t0)
    found = [j for t in range(q.t0, q.t0 + window_s + 1, step_s) for j in plan(q, t)]
    return rank(dedupe(q, found))


def _earliest(q: Query, t: int) -> int:
    js = plan(q, t)
    return min(j.arrive for j in js) if js else INF


def plan_arrive_by(q: Query, target: int, window_s: int, step_s: int = WINDOW_STEP_MIN * 60, floor: int | None = None) -> list[Journey]:
    """The latest on-time departure by bisection to within ARRIVE_BY_RESOLUTION_S, then a window stepping back from it; no run starts before floor."""
    lo, hi = target - ARRIVE_BY_SEARCH_H * 3600, target
    if floor is not None:
        lo = max(lo, floor)
    if lo >= target:
        return []
    # The search never runs before lo and the window never steps back further than window_s from it, nor below floor.
    q = _with_spent(q, lo - window_s if floor is None else max(lo - window_s, floor))
    if _earliest(q, lo) > target:
        return []
    while hi - lo > ARRIVE_BY_RESOLUTION_S:
        mid = (lo + hi) // 2
        if _earliest(q, mid) <= target:
            lo = mid
        else:
            hi = mid
    stop = lo - window_s if floor is None else max(lo - window_s, floor)
    found = [j for t in range(lo, stop - 1, -step_s) for j in plan(q, t) if j.arrive <= target]
    return rank(dedupe(q, found, latest=True), latest=True)


def has_service(q: Query, stops) -> bool:
    """Any group at these stops that runs on one of the query's days; an earlier day's group only while a trip of it can still be running."""
    return any(g.service_id in sl.service_ids and (sl.offset >= 0 or g.cols[-1][-1] + sl.offset >= q.t0)
               for s in stops for sl in q.slices for pid, _ in sl.patterns_at(s) for g in sl.groups_for(pid))
