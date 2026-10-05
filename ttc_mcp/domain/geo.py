from __future__ import annotations

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
