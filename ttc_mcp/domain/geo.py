from __future__ import annotations

import bisect
import math


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def walk_minutes(m: float) -> int:
    return round(m * 1.3 / 80)


def bbox(lat: float, lon: float, radius_m: float):
    dlat = radius_m / 111320
    dlon = radius_m / (111320 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon


def decode_polyline(s: str) -> list[tuple[float, float]]:
    """Google encoded polyline to [(lat, lon)], the format of GTFS-RT Shape entities."""
    idx = lat = lon = 0
    out: list[tuple[float, float]] = []
    while idx < len(s):
        for which in (0, 1):
            res = shift = 0
            while True:
                b = ord(s[idx]) - 63
                idx += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(res >> 1) if res & 1 else res >> 1
            if which == 0:
                lat += delta
            else:
                lon += delta
        out.append((lat / 1e5, lon / 1e5))
    return out


def path_length_m(points: list[tuple[float, float]]) -> float:
    return sum(haversine_m(*points[i], *points[i + 1]) for i in range(len(points) - 1))


def encode_polyline(points: list[tuple[float, float]]) -> str:
    out, prev_lat, prev_lon = [], 0, 0
    for lat, lon in points:
        ilat, ilon = round(lat * 1e5), round(lon * 1e5)
        for delta in (ilat - prev_lat, ilon - prev_lon):
            v = ~(delta << 1) if delta < 0 else delta << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


def cumulative_m(points: list[tuple[float, float]]) -> list[float]:
    out = [0.0]
    for i in range(1, len(points)):
        out.append(out[-1] + haversine_m(*points[i - 1], *points[i]))
    return out


def simplify(points: list[tuple[float, float]], tolerance_m: float) -> list[tuple[float, float]]:
    """Douglas-Peucker on a local equirectangular projection; tolerance in metres."""
    if len(points) < 3:
        return list(points)
    lat0 = math.radians(points[0][0])
    xy = [((lon * 111320 * math.cos(lat0)), lat * 111320) for lat, lon in points]

    def dist(p, a, b):
        (px, py), (ax, ay), (bx, by) = p, a, b
        dx, dy = bx - ax, by - ay
        if dx == dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        best, idx = 0.0, -1
        for i in range(a + 1, b):
            d = dist(xy[i], xy[a], xy[b])
            if d > best:
                best, idx = d, i
        if idx >= 0 and best > tolerance_m:
            keep[idx] = True
            stack.extend(((a, idx), (idx, b)))
    return [p for p, k in zip(points, keep) if k]


def slice_by_dist(points: list, dists, d0: float, d1: float) -> list:
    """Points bracketing the span: the last vertex at or before d0 through the first at or after d1."""
    if not points:
        return []
    lo = max(bisect.bisect_right(dists, d0) - 1, 0)
    hi = min(bisect.bisect_left(dists, d1), len(points) - 1)
    return list(points[lo:hi + 1])
