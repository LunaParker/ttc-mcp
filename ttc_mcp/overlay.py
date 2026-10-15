"""What a connections query sees in front of the immutable timetable: patched groups, variant patterns, shuttle pseudo-routes (spec 6.10)."""
from __future__ import annotations

from array import array
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ttc_mcp.adapters.bustime_rt import TripUpdateRec
from ttc_mcp.domain.raptor import Group, Timetable
from ttc_mcp.static.timetable import split_overtaking

MAX_DRIFT_S = 6 * 3600  # a prediction further than this from the scheduled time belongs to another day's instance of the trip


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


def position_of(stops: list[str], stop: str, after: int = 0) -> int | None:
    """The first position at or after `after` serving the stop: a loop visits a stop twice, and the subway feed may list a platform
    again for the next train under the same trip id (2026-10-14 capture), which this keeps out of the first train's row."""
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
                 slot: Callable[[str], tuple[int, int, int] | None]) -> dict[int, list[Group]]:
    """Replacement group lists for every pattern a trip update touches: predicted times in place, the last delay carried to the
    trip's later stops, SKIPPED stops masked, overtaking re-split. Trips without a static row and times from another day are ignored."""
    touched: dict[tuple[int, int], dict[int, TripUpdateRec]] = {}
    for rec in recs:
        found = slot(rec.trip_id)
        if found:
            pid, gi, ti = found
            touched.setdefault((pid, gi), {})[ti] = rec
    out: dict[int, list[Group]] = {}
    for (pid, gi), by_trip in touched.items():
        g, stops = tt.groups[pid][gi], tt.pattern_stops[pid]
        times = {int(g.trip_ids[i]): [g.cols[p][i] for p in range(len(stops))] for i in range(g.n_trips)}
        skip: dict[int, set[int]] = {}
        realtime: set[int] = set()
        for ti, rec in by_trip.items():
            tid, row, after, changed = int(g.trip_ids[ti]), times[int(g.trip_ids[ti])], 0, {}
            for u in rec.updates:
                stop = stop_of(u.stop_id)
                pos = position_of(stops, stop, after) if stop else None
                if pos is None:
                    continue
                after = pos + 1
                if u.relationship == "SKIPPED":
                    skip.setdefault(tid, set()).add(pos)
                    changed[pos] = changed.get(pos, None)
                elif u.relationship != "NO_DATA" and u.time is not None:
                    secs = int(u.time - origin_epoch)
                    if abs(secs - row[pos]) <= MAX_DRIFT_S:
                        changed[pos] = secs - row[pos]
            if not changed and tid not in skip:
                continue
            realtime.add(tid)
            delay = 0
            for pos in range(len(stops)):
                if pos in changed and changed[pos] is not None:
                    delay = changed[pos]
                row[pos] += delay
            for pos in range(1, len(stops)):
                row[pos] = max(row[pos], row[pos - 1])  # a prediction never runs backwards along the trip; Group.last and the spent skip rely on it
        if not realtime:
            continue
        rows = sorted(times.items(), key=lambda kv: kv[1][0])
        new = _resplit(pid, g.service_id, [(t, array("i", r)) for t, r in rows], skip, realtime)
        out[pid] = [x for i, x in enumerate(tt.groups[pid]) if i != gi] + new if pid not in out else [x for x in out[pid] if x is not g] + new
    return out
