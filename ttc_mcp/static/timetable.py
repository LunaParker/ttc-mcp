"""Build side of the timetable index (spec 2.1, 6.10): trips packed as int32 columns per pattern, service and overtaking split."""
from __future__ import annotations

import heapq
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


FARE_GATES = (6, 7)
WALK_MPS, DEFAULT_EDGE_S = 1.0, 30


def edge_seconds(length_m: float | None, traversal_s: int | None) -> float:
    if traversal_s is not None:
        return traversal_s
    return length_m / WALK_MPS if length_m else DEFAULT_EDGE_S


def dijkstra(adj: dict[str, list[tuple[str, float]]], src: str) -> dict[str, float]:
    dist, heap = {src: 0.0}, [(0.0, src)]
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, float("inf")):
            continue
        for v, w in adj.get(u, ()):
            if d + w < dist.get(v, float("inf")):
                dist[v] = d + w
                heapq.heappush(heap, (d + w, v))
    return dist


def station_paths(conn) -> list[tuple[str, str, int]]:
    """Seconds between the boarding stops of a station over its pathway edges; fare gates are skipped so a transfer stays paid."""
    out = []
    for (station,) in conn.execute("SELECT stop_id FROM stops WHERE location_type=1").fetchall():
        adj: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for a, b, mode, bi, length, secs in conn.execute(
                "SELECT p.from_stop, p.to_stop, p.mode, p.bidirectional, p.length_m, p.traversal_s FROM pathways p WHERE p.pathway_id IN ("
                "SELECT p2.pathway_id FROM pathways p2 JOIN stops s ON s.stop_id IN (p2.from_stop, p2.to_stop) WHERE s.parent_station=?)", (station,)):
            if mode in FARE_GATES:
                continue
            w = edge_seconds(length, secs)
            adj[a].append((b, w))
            if bi:
                adj[b].append((a, w))
        boarding = [r[0] for r in conn.execute("SELECT stop_id FROM stops WHERE parent_station=? AND location_type=0", (station,))]
        for src in boarding:
            dist = dijkstra(adj, src)
            out += [(src, dst, round(dist[dst])) for dst in boarding if dst != src and dst in dist]
    return out
