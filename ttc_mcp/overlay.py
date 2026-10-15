"""What a connections query sees in front of the immutable timetable: patched groups, variant patterns, shuttle pseudo-routes (spec 6.10)."""
from __future__ import annotations

import datetime as dt
from array import array
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ttc_mcp.adapters.bustime_rt import TripUpdateRec
from ttc_mcp.adapters.detour_feed import DetourEntity
from ttc_mcp.domain.raptor import INF, Group, Timetable
from ttc_mcp.static.timetable import split_overtaking

MAX_DRIFT_S = 2 * 3600  # surface feed: past this an update is another day's run of the trip, since no bus runs two hours late (drifts split after the 2026-10-14 capture)
SUBWAY_DRIFT_S = 15 * 60  # subway feed: a train genuinely late or early; the 2026-10-14 capture's trip ids sat in their slot for 5 of 55 trips, the rest far off


@dataclass
class Overlay:
    groups: dict[int, list] = field(default_factory=dict)
    extra: dict[int, list] = field(default_factory=dict)
    extra_stops: dict[int, list[str]] = field(default_factory=dict)
    extra_meta: dict[int, dict] = field(default_factory=dict)
    extra_at: dict[str, list[tuple[int, int]]] = field(default_factory=dict)

    def groups_for(self, tt: Timetable, pattern: int) -> list:
        return self.extra[pattern] if pattern < 0 else self.groups.get(pattern, tt.groups.get(pattern, []))

    def patterns_at(self, tt: Timetable, stop: str) -> list[tuple[int, int]]:
        return [*tt.stop_patterns.get(stop, ()), *self.extra_at.get(stop, ())]

    def stops_of(self, tt: Timetable, pattern: int) -> list[str]:
        return self.extra_stops[pattern] if pattern < 0 else tt.pattern_stops[pattern]

    def meta_of(self, tt: Timetable, pattern: int) -> dict:
        return self.extra_meta[pattern] if pattern < 0 else tt.pattern_meta[pattern]

    def add_variants(self, groups, extra, extra_stops, extra_meta, extra_at) -> None:
        self.groups.update(groups), self.extra.update(extra), self.extra_stops.update(extra_stops), self.extra_meta.update(extra_meta)
        for s, hits in extra_at.items():
            self.extra_at.setdefault(s, []).extend(hits)


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
        cols = [array("i", (times[pos] for _, times in bucket)) for pos in range(len(bucket[0][1]))]
        by = {t: i for i, t in enumerate(ids)}
        out.append(Group(pattern, service_id, cols, ids, frozenset((by[t], p) for t, ps in skip.items() if t in by for p in ps), frozenset(by[t] for t in realtime if t in by)))
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


def detour_variants(conn, tt: Timetable, entities: Iterable[DetourEntity], day: dt.date, base: dict[int, list[Group]] | None = None):
    """Per entity active on `day` whose trips resolve: a pseudo pattern over the kept positions holding exactly its trips, the propagated
    delay added beyond each span unless a patch predicted the trip, its masks and realtime mark kept; those trips leave their groups (spec 6.10)."""
    groups: dict[int, list[Group]] = dict(base or {})
    extra, extra_stops, extra_meta, extra_at = {}, {}, {}, {}
    key = day.strftime("%Y%m%d")
    for n, e in enumerate(x for x in entities if key in x.service_dates):
        slots = [(t, s) for t in dict.fromkeys(e.trip_ids) if (s := tt.trip_slot(conn, t))]
        if not slots or len({s[0] for _, s in slots}) != 1:
            continue
        pid = slots[0][1][0]
        current = groups.get(pid, tt.groups[pid])
        # A real-time patch may have re-split this pattern's groups, so find each trip in the list in force, not by trip_slot's index.
        located = [(i, list(g.trip_ids).index(int(t))) for t, _ in slots for i, g in enumerate(current) if int(t) in g.trip_ids]
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
        vid = VARIANT_BASE - n
        by_group: dict[int, list[int]] = {}
        for gi, ti in located:
            by_group.setdefault(gi, []).append(ti)
        variant: list[Group] = []
        for gi, tis in by_group.items():
            g = current[gi]
            rows, masked, live, sel = [], {}, set(), set(tis)
            for t, p in g.skip:
                if t in sel and p in kept_at:
                    masked.setdefault(int(g.trip_ids[t]), set()).add(kept_at[p])
            for ti in tis:
                tid, predicted, delay, times = int(g.trip_ids[ti]), ti in g.realtime, 0, []
                for pos in range(len(stops)):
                    # GTFS-RT: each span's delay adds to the earlier ones as the trip advances; a patched trip's times already say when it runs.
                    delay += 0 if predicted else delay_after.get(pos, 0)
                    if pos not in skipped:
                        times.append(g.cols[pos][ti] + delay)
                rows.append((tid, array("i", times)))
                if predicted:
                    live.add(tid)
            variant += _resplit(vid, g.service_id, rows, masked, live)
            current = [(_without(x, sel) if i == gi else x) for i, x in enumerate(current)]
        groups[pid] = current
        extra[vid], extra_stops[vid] = variant, [stops[i] for i in keep]
        extra_meta[vid] = {**tt.pattern_meta[pid], "base_pattern": pid, "positions": keep, "detour_id": e.detour_id}
        for pos, s in enumerate(extra_stops[vid]):
            extra_at.setdefault(s, []).append((vid, pos))
    return groups, extra, extra_stops, extra_meta, extra_at
