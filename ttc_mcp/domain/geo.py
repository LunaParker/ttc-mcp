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
