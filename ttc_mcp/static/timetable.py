"""Build side of the timetable index (spec 2.1, 6.10): trips packed as int32 columns per pattern, service and overtaking split."""
from __future__ import annotations

from array import array
from collections import defaultdict

Trip = tuple[int, array]


def split_overtaking(trips: list[Trip]) -> list[list[Trip]]:
    """Greedy: each trip joins the first bucket whose last trip is never later than it at any stop, so every column stays sorted."""
    buckets: list[list[Trip]] = []
    for t in sorted(trips, key=lambda t: (t[1][0], t[0])):
        for b in buckets:
            if all(x <= y for x, y in zip(b[-1][1], t[1], strict=True)):
                b.append(t)
                break
        else:
            buckets.append([t])
    return buckets


def pack(bucket: list[Trip]) -> tuple[bytes, bytes]:
    n_stops = len(bucket[0][1])
    cols = b"".join(array("i", (t[1][pos] for t in bucket)).tobytes() for pos in range(n_stops))
    return cols, array("q", (t[0] for t in bucket)).tobytes()


def unpack(n_stops: int, times: bytes, trip_ids: bytes) -> tuple[list[array], array]:
    width = len(times) // n_stops
    cols = []
    for pos in range(n_stops):
        a = array("i")
        a.frombytes(times[pos * width:(pos + 1) * width])
        cols.append(a)
    ids = array("q")
    ids.frombytes(trip_ids)
    return cols, ids


class GroupAccumulator:
    """Fed one trip at a time by the stop_times pass, so the build never holds the rows twice."""

    def __init__(self):
        self._groups: dict[tuple[int, str], list[Trip]] = defaultdict(list)
        self.splits = 0

    def add(self, pattern_id: int, service_id: str, trip_id: int, times: list[int]) -> None:
        self._groups[(pattern_id, service_id)].append((trip_id, array("i", times)))

    def rows(self) -> list[tuple[int, int, str, int, int, bytes, bytes]]:
        out, self.splits = [], 0
        for (pid, sid), trips in sorted(self._groups.items()):
            buckets = split_overtaking(trips)
            self.splits += len(buckets) - 1
            for i, b in enumerate(buckets):
                times, ids = pack(b)
                out.append((pid, i, sid, len(b), len(b[0][1]), times, ids))
        return out
