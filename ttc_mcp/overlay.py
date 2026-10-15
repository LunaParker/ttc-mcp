"""What a connections query sees in front of the immutable timetable: patched groups, variant patterns, shuttle pseudo-routes (spec 6.10)."""
from __future__ import annotations

import datetime as dt
from array import array
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from itertools import accumulate

from ttc_mcp.adapters.bustime_rt import StopUpdate, TripFeed, TripUpdateRec
from ttc_mcp.adapters.detour_feed import DetourEntity, DetourFeed
from ttc_mcp.domain import planner
from ttc_mcp.domain.disruptions import LINE_PLATFORMS, Disruptions
from ttc_mcp.domain.raptor import INF, Group, Headway, Timetable
from ttc_mcp.static.gtfs import RAIL
from ttc_mcp.static.schedule import wall_clock
from ttc_mcp.static.timetable import split_overtaking

# Surface feed: past this an update is another day's run of the trip, since no bus runs two hours late (drifts split after the 2026-10-14 capture).
MAX_DRIFT_S = 2 * 3600
# Subway feed: a train genuinely late or early; the 2026-10-14 capture's trip ids sat in their slot for 5 of 55 trips, the rest far off.
SUBWAY_DRIFT_S = 15 * 60


@dataclass
class Overlay:
    groups: dict[int, list] = field(default_factory=dict)
    extra: dict[int, list] = field(default_factory=dict)
    extra_stops: dict[int, list[str]] = field(default_factory=dict)
    extra_meta: dict[int, dict] = field(default_factory=dict)
    extra_at: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    variants: set[str] = field(default_factory=set)  # detour entity ids whose trips run on a variant pattern

    def groups_for(self, tt: Timetable, pattern: int) -> list:
        if pattern < 0:
            return self.extra[pattern]
        g = self.groups.get(pattern)
        return tt.groups.get(pattern, []) if g is None else g

    def patterns_at(self, tt: Timetable, stop: str) -> list[tuple[int, int]]:
        return [*tt.stop_patterns.get(stop, ()), *self.extra_at.get(stop, ())]

    def stops_of(self, tt: Timetable, pattern: int) -> list[str]:
        return self.extra_stops[pattern] if pattern < 0 else tt.pattern_stops[pattern]

    def meta_of(self, tt: Timetable, pattern: int) -> dict:
        return self.extra_meta[pattern] if pattern < 0 else tt.pattern_meta[pattern]

    def add_variants(self, groups, extra, extra_stops, extra_meta, extra_at, variants) -> None:
        self.groups.update(groups)
        self.variants.update(variants)
        self.add_shuttles(extra, extra_stops, extra_meta, extra_at)

    def add_shuttles(self, extra, extra_stops, extra_meta, extra_at) -> None:
        self.extra.update(extra), self.extra_stops.update(extra_stops), self.extra_meta.update(extra_meta)
        for s, hits in extra_at.items():
            self.extra_at.setdefault(s, []).extend(hits)


CHUNK = 900  # trip ids per IN list, under SQLite's old 999-parameter limit


class Locator:
    """Where trips sit: each trip's pattern and service from batched reads of trips, then its (group, trip) index through one trip id -> index
    map per group of that service; the timetable's own groups keep theirs, any other group's go in the caller's per-build dict."""

    def __init__(self, conn, tt: Timetable, trip_ids: Iterable[str]):
        self.tt, self.where = tt, {}
        ids = list(dict.fromkeys(trip_ids))
        for i in range(0, len(ids), CHUNK):
            part = ids[i:i + CHUNK]
            for tid, pid, sid in conn.execute(f"SELECT trip_id, pattern_id, service_id FROM trips WHERE trip_id IN ({','.join('?' * len(part))})", part):
                self.where[tid] = (pid, sid)
        self._base = {id(g) for gs in tt.groups.values() for g in gs}  # tt holds these groups, so their ids stay theirs
        self._maps: dict[int, tuple[Group, dict[int, int]]] = {}

    def find(self, trip_id: str, groups: list, local: dict | None = None) -> tuple[int, int] | None:
        """(group index, trip index) of a trip id given at construction in this group list; local holds the maps of groups the timetable lacks."""
        w = self.where.get(trip_id)
        if w is None:
            return None
        tid, sid = int(trip_id), w[1]
        for gi, g in enumerate(groups):
            if g.service_id == sid:
                maps = self._maps if id(g) in self._base else local if local is not None else {}
                hit = maps.get(id(g))
                if hit is None:
                    hit = maps[id(g)] = (g, dict(zip(g.trip_ids, range(len(g.trip_ids)), strict=True)))  # held, so a freed group's id is never read
                if (ti := hit[1].get(tid)) is not None:
                    return gi, ti
        return None

    def slot(self, trip_id: str) -> tuple[int, int, int] | None:
        """Timetable.trip_slot's answer, without its query and scan."""
        w = self.where.get(trip_id)
        hit = self.find(trip_id, self.tt.groups.get(w[0], [])) if w else None
        return (w[0], *hit) if hit else None


def position_of(stops: list[str], stop: str, after: int = 0, seqs: list[int] | None = None, seq: int | None = None) -> int | None:
    """The update's stop_sequence when the pattern holds it at this stop (a loop's second visit), else the first position at or after
    `after` serving the stop; a position before `after` is the next train's repeat under the same trip id (2026-10-14 capture)."""
    if seq is not None and seqs:
        i = seq - seqs[0]
        if not (0 <= i < len(seqs) and seqs[i] == seq):
            i = seqs.index(seq) if seq in seqs else -1
        if i >= 0 and stops[i] == stop:
            return i if i >= after else None
    try:
        return stops.index(stop, after)
    except ValueError:
        return None


def _resplit(pattern: int, service_id: str, trips: list[tuple[int, array]], skip: dict[int, set[int]], realtime: set[int]) -> list[Group]:
    """Groups from (trip id, times) rows, re-split for overtaking, the masks carried by trip id."""
    out = []
    for bucket in split_overtaking(trips):
        ids = array("q", (t for t, _ in bucket))
        cols = [array("i", c) for c in zip(*(times for _, times in bucket), strict=True)]
        by = {t: i for i, t in enumerate(ids)}
        out.append(Group(pattern, service_id, cols, ids, frozenset((by[t], p) for t, ps in skip.items() if t in by for p in ps),
                         frozenset(by[t] for t in realtime if t in by)))
    return out


def patch_groups(tt: Timetable, recs: Iterable[TripUpdateRec], stop_of: Callable[[str], str | None], origin_epoch: float,
                 slot: Callable[[str], tuple[int, int, int] | None], seqs_of: Callable[[int], list[int] | None] | None = None, *,
                 drift: int = MAX_DRIFT_S, base: dict[int, list[Group]] | None = None) -> dict[int, list[Group]]:
    """Replacement group lists, base's first, for every pattern a trip update touches: predictions carried forward until NO_DATA restores the
    schedule, SKIPPED stops and CANCELED trips masked, re-split only when a trip leaves its place; unknown trips and updates past drift ignored."""
    seqs_of = seqs_of or tt.pattern_seqs.get
    touched: dict[tuple[int, int], dict[int, TripUpdateRec]] = {}
    for rec in recs:
        found = slot(rec.trip_id)
        if found:
            pid, gi, ti = found
            touched.setdefault((pid, gi), {})[ti] = rec
    out: dict[int, list[Group]] = dict(base or {})
    for (pid, gi), by_trip in touched.items():
        g, stops, seqs = tt.groups[pid][gi], tt.pattern_stops[pid], seqs_of(pid)
        n_stops, n, sched = len(stops), g.n_trips, g.cols
        moved: dict[int, dict[int, int]] = {}
        skip: set[tuple[int, int]] = set()
        realtime: set[int] = set()
        for ti, rec in by_trip.items():
            if rec.relationship == "CANCELED":
                skip.update((ti, pos) for pos in range(n_stops))
                realtime.add(ti)
                continue
            delays: dict[int, int] = {}
            after, live = 0, False
            for u in rec.updates:
                stop = stop_of(u.stop_id)
                pos = position_of(stops, stop, after, seqs, u.seq) if stop else None
                if pos is None:
                    continue
                after = pos + 1
                if u.relationship == "SKIPPED":
                    skip.add((ti, pos))
                    live = True
                elif u.relationship == "NO_DATA":
                    delays[pos] = 0  # no prediction here: the schedule applies from this stop on
                elif u.time is not None and -drift <= (d := int(u.time - origin_epoch) - sched[pos][ti]) <= drift:
                    delays[pos], live = d, True
            if live:
                realtime.add(ti)
                if any(delays.values()):
                    moved[ti] = delays
        if not realtime:
            continue
        cols = [array("i", c) for c in sched]
        first = {ti: min(delays) for ti, delays in moved.items()}
        in_order = True
        for ti in sorted(moved):
            delays, d = moved[ti], 0
            lo = cols[first[ti] - 1][ti] if first[ti] else -INF
            # The next trip's columns before its own first change are final; from there on its left-hand check covers the pair.
            right = first.get(ti + 1, n_stops) if ti + 1 < n else 0
            for pos in range(first[ti], n_stops):
                c = cols[pos]
                d = delays.get(pos, d)
                v = c[ti] + d
                if v < lo:
                    v = lo  # a ride never arrives before it boards, and has_service reads the last column as the trip's end
                lo = c[ti] = v
                if (ti and c[ti - 1] > v) or (pos < right and v > c[ti + 1]):
                    in_order = False
        if in_order:
            new = [Group(pid, g.service_id, cols, g.trip_ids, frozenset(skip), frozenset(realtime))]
        else:
            ids = [int(t) for t in g.trip_ids]
            by_id: dict[int, set[int]] = {}
            for ti, pos in skip:
                by_id.setdefault(ids[ti], set()).add(pos)
            new = _resplit(pid, g.service_id, [(ids[i], array("i", (c[i] for c in cols))) for i in range(n)], by_id, {ids[ti] for ti in realtime})
        out[pid] = [x for i, x in enumerate(tt.groups[pid]) if i != gi] + new if pid not in out else [x for x in out[pid] if x is not g] + new
    return out


VARIANT_BASE = -1000


def _without(g: Group, drop: set[int]) -> Group:
    keep = [i for i in range(g.n_trips) if i not in drop]
    by = {old: new for new, old in enumerate(keep)}
    return Group(g.pattern, g.service_id, [array("i", (c[i] for i in keep)) for c in g.cols], array("q", (g.trip_ids[i] for i in keep)),
                 frozenset((by[t], p) for t, p in g.skip if t in by), frozenset(by[t] for t in g.realtime if t in by))


def detour_variants(conn, tt: Timetable, entities: Iterable[DetourEntity], day: dt.date, base: dict[int, list[Group]] | None = None, *,
                    locate: Locator | None = None):
    """Per entity active on `day` whose trips resolve, a variant pattern over its kept positions holding exactly its trips, which leave their groups
    (spec 6.10); returns the overlay's five maps and the ids of the entities whose variant holds a trip. locate must know the entities' trip ids."""
    ents = list(entities)
    locate = locate or Locator(conn, tt, [t for e in ents for t in e.trip_ids])
    groups: dict[int, list[Group]] = dict(base or {})
    extra, extra_stops, extra_meta, extra_at, variants, local = {}, {}, {}, {}, set(), {}
    key = day.strftime("%Y%m%d")
    for n, e in enumerate(x for x in ents if key in x.service_dates):
        slots = [(t, s) for t in dict.fromkeys(e.trip_ids) if (s := locate.slot(t))]
        if not slots or len({s[0] for _, s in slots}) != 1:
            continue
        pid = slots[0][1][0]
        current = groups.get(pid, tt.groups[pid])
        # A real-time patch may have re-split this pattern's groups, so find each trip in the list in force, not by trip_slot's index.
        located = [hit for t, _ in slots if (hit := locate.find(t, current, local))]
        seqs, stops = tt.pattern_seqs[pid], tt.pattern_stops[pid]
        skipped, delay_after = set(), {}
        for start, end, delay in e.spans:
            span = [i for i, seq in enumerate(seqs) if start <= seq <= end]
            skipped.update(span)
            if span:
                delay_after[max(span)] = delay_after.get(max(span), 0) + delay
        keep = [i for i in range(len(stops)) if i not in skipped]
        if not keep:
            continue
        kept_at = {p: k for k, p in enumerate(keep)}
        # GTFS-RT: each span's delay adds to the earlier ones as the trip advances; a patched trip's times already say when it runs.
        added = list(accumulate(delay_after.get(pos, 0) for pos in range(len(stops))))
        kept_added = [added[p] for p in keep]
        vid = VARIANT_BASE - n
        by_group: dict[int, list[int]] = {}
        for gi, ti in located:
            by_group.setdefault(gi, []).append(ti)
        variant: list[Group] = []
        for gi, tis in by_group.items():
            g = current[gi]
            rows, masked, live, sel, cols = [], {}, set(), set(tis), [g.cols[p] for p in keep]
            for t, p in g.skip:
                if t in sel and p in kept_at:
                    masked.setdefault(int(g.trip_ids[t]), set()).add(kept_at[p])
            for ti in tis:
                tid = int(g.trip_ids[ti])
                if ti in g.realtime:
                    rows.append((tid, array("i", [c[ti] for c in cols])))
                    live.add(tid)
                else:
                    rows.append((tid, array("i", [c[ti] + a for c, a in zip(cols, kept_added, strict=True)])))
            variant += _resplit(vid, g.service_id, rows, masked, live)
            current = [(_without(x, sel) if i == gi else x) for i, x in enumerate(current)]
        # A group the detour took every trip of goes: has_service reads a group's last column.
        groups[pid] = [x for x in current if x.n_trips]
        extra[vid], extra_stops[vid] = variant, [stops[i] for i in keep]
        extra_meta[vid] = {**tt.pattern_meta[pid], "base_pattern": pid, "positions": keep, "detour_id": e.detour_id}
        for pos, s in enumerate(extra_stops[vid]):
            extra_at.setdefault(s, []).append((vid, pos))
        if variant:
            variants.add(e.id)
    return groups, extra, extra_stops, extra_meta, extra_at, variants


SHUTTLE_BASE = -2000
SHUTTLE_HEADWAY_MIN, SHUTTLE_KMH, SHUTTLE_DWELL_MIN = 5, 15, 1  # spec 6.10's constants, checked by validation item 19


def _line_stations(conn, line: str, stations: list[str]) -> list[tuple[str, str, str, float]]:
    """(station, name, lowest platform of the line, distance along the line's rank-1 pattern) in line order; none when the feed lacks the line.
    A platform with no shape distance (dist_m is nullable) places nothing, so its station drops out."""
    row = conn.execute("SELECT pattern_id FROM patterns WHERE route_id=? AND direction_id=0 AND rank=1", (line,)).fetchone()
    if row is None:
        return []
    dist = {sid: d for sid, d in conn.execute("SELECT stop_id, dist_m FROM pattern_stops WHERE pattern_id=?", (row[0],))}
    out = []
    for sid in stations:
        platforms = sorted(r[0] for r in conn.execute(LINE_PLATFORMS, (sid, line)))
        on_line = [p for p in platforms if dist.get(p) is not None]
        if on_line:
            name = conn.execute("SELECT name FROM stops WHERE stop_id=?", (sid,)).fetchone()[0]
            out.append((sid, name, platforms[0], dist[on_line[0]]))
    return sorted(out, key=lambda x: x[3])


def shuttle_routes(conn, tt: Timetable, disr: Disruptions):
    """Each active closure with a running shuttle: a headway route over its stations in line order, both directions (spec 6.10)."""
    extra, extra_stops, extra_meta, extra_at = {}, {}, {}, {}
    n = 0
    for el in disr.elements:
        if el["type"] != "closure" or not el["active_now"] or not planner._shuttle_runs(el) or len(el["stations"]) < 2:
            continue
        for line in (r for r in el["routes"] if r in RAIL):
            chain = _line_stations(conn, line, el["stations"])
            if len(chain) < 2:
                continue
            km = (chain[-1][3] - chain[0][3]) / 1000
            note = (f"assumed {SHUTTLE_HEADWAY_MIN}-minute headway and {SHUTTLE_KMH} km/h over {km:.1f} km of track "
                    f"with a {SHUTTLE_DWELL_MIN}-minute stop per station; the TTC publishes no shuttle timetable (alert {el['alert_id']})")
            for direction, order in (("0", chain), ("1", chain[::-1])):
                cum, total = [0], 0
                for a, b in zip(order, order[1:], strict=False):
                    total += round(abs(b[3] - a[3]) / (SHUTTLE_KMH * 1000 / 3600) + SHUTTLE_DWELL_MIN * 60)
                    cum.append(total)
                vid = SHUTTLE_BASE - n
                n += 1
                extra[vid] = [Headway(vid, SHUTTLE_HEADWAY_MIN * 60, tuple(cum))]
                extra_stops[vid] = [p for _, _, p, _ in order]
                extra_meta[vid] = {"route": line, "direction_id": direction, "headsign": f"shuttle bus {order[0][1]} to {order[-1][1]}", "mode": "shuttle",
                                   "line": line, "alert_id": el["alert_id"], "note": note,
                                   "station_of": {p: {"station": sid, "name": name} for sid, name, p, _ in order}}
                for pos, p in enumerate(extra_stops[vid]):
                    extra_at.setdefault(p, []).append((vid, pos))
    return extra, extra_stops, extra_meta, extra_at


MEMO_DAYS = 2  # a query reads its own service day and yesterday's
_memo: dict[str, dict[dt.date, tuple]] = {}
_prep: dict[str, tuple] = {}


def reset() -> None:
    _memo.clear(), _prep.clear()


def public_updates(conn, trips: TripFeed | None) -> list[TripUpdateRec]:
    """Surface trip updates with their stop ids read as public numbers through the crosswalk; updates at unmapped stops are dropped, and a
    record left with none goes unless it cancels its trip."""
    if trips is None:
        return []
    to_public = dict(conn.execute("SELECT surface_stop_id, stop_code FROM crosswalk"))
    out = []
    for rec in trips.trips.values():
        ups = tuple(StopUpdate(to_public[u.stop_id], u.seq, u.time, u.relationship) for u in rec.updates if u.stop_id in to_public)
        if ups or rec.relationship == "CANCELED":
            out.append(TripUpdateRec(rec.trip_id, rec.route, rec.vehicle_id, rec.relationship, ups))
    return out


def _prepared(feed, tt: Timetable, trips: TripFeed | None, subway: TripFeed | None, detour_feed: DetourFeed | None):
    """What a fetch gives every service day alike: the crosswalked surface records, the subway records and the locator over their trips and the
    detour feed's; one per feed, holding its inputs so their ids stay theirs."""
    held = (tt, trips, subway, detour_feed)
    key = tuple(map(id, held))
    hit = _prep.get(feed.version)
    if hit and hit[0] == key:
        return hit[2]
    surface, under = public_updates(feed.conn, trips), list(subway.trips.values()) if subway else []
    ids = [*(r.trip_id for r in surface), *(r.trip_id for r in under), *(t for e in (detour_feed.entities if detour_feed else []) for t in e.trip_ids)]
    out = surface, under, Locator(feed.conn, tt, ids)
    _prep[feed.version] = (key, held, out)
    return out


def build(feed, tt: Timetable, disr: Disruptions, detour_feed: DetourFeed | None, trips: TripFeed | None, subway: TripFeed | None, day: dt.date) -> Overlay:
    """One overlay per feed, service day and set of inputs: each day slice reads its own day's, since a trip update matches the run of its own
    service day. The memo keeps the latest per feed and day for MEMO_DAYS days, holding its inputs so their ids stay theirs."""
    held = (tt, disr, trips, subway, detour_feed)
    key = tuple(map(id, held))
    days = _memo.setdefault(feed.version, {})
    hit = days.get(day)
    if hit and hit[0] == key:
        return hit[2]
    surface, under, loc = _prepared(feed, tt, trips, subway, detour_feed)
    stamp, origin = day.strftime("%Y%m%d"), wall_clock(day, 0).timestamp()
    entities = [e for e in (detour_feed.entities if detour_feed else []) if stamp in e.service_dates]
    # Each feed has its own drift (Task 13's ruling), and the feeds' patterns are disjoint, so the subway chains on the surface patches.
    patched = patch_groups(tt, surface, lambda s: s, origin, loc.slot, tt.pattern_seqs.get) if surface else {}
    if under:
        patched = patch_groups(tt, under, lambda s: s, origin, loc.slot, tt.pattern_seqs.get, drift=SUBWAY_DRIFT_S, base=patched)
    ov = Overlay()
    ov.add_variants(*detour_variants(feed.conn, tt, entities, day, base=patched, locate=loc))  # its groups start from the patched ones
    ov.add_shuttles(*shuttle_routes(feed.conn, tt, disr))
    days[day] = (key, held, ov)
    if len(days) > MEMO_DAYS:
        days.pop(min(days), None)
    return ov
