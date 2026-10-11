from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from ttc_mcp.cache import Parsed
from ttc_mcp.static.schedule import TZ

KINDS = ("closure", "suspension", "detour", "rsz", "elevator", "escalator", "delay", "other")
RAIL_LINES = ("1", "2", "4", "5", "6")
RAIL_TYPES = ("Subway", "LRT", "Light Rail")
RSZ_FIELDS = {"reducedSpeed": "reduced_speed_kmh", "rszLength": "length_m", "trackPercent": "track_percent",
              "averageSpeed": "average_speed_kmh", "distance": "distance_m", "targetRemoval": "target_removal"}


@dataclass(frozen=True)
class Alert:
    id: str
    kind: str
    alert_type: str
    routes: tuple[str, ...]
    route_type: str
    effect: str
    effect_desc: str
    direction: str | None
    stop_start: str | None
    stop_end: str | None
    stop_ids: tuple[str, ...]
    station_names: tuple[str, ...]
    shuttle: str | None
    start: dt.datetime | None
    end: dt.datetime | None
    windows: tuple[tuple[dt.datetime, dt.datetime], ...]
    severity: str | None
    cause: str | None
    cause_desc: str | None
    header: str
    title: str
    url: str | None
    unit_code: str | None
    rsz: dict | None
    last_updated: dt.datetime | None


@dataclass(frozen=True)
class AlertList:
    last_updated: dt.datetime | None
    rsz_last_updated: dt.datetime | None
    alerts: list[Alert]


def local_time(s: str | None) -> dt.datetime | None:
    """alerts.ttc.ca stamps Toronto local time with a Z suffix (verified against GTFS-RT epochs); year 1 means open-ended."""
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(s.rstrip("Z"))
    except ValueError:
        return None
    return None if t.year <= 1 else t.replace(tzinfo=TZ)


def classify(route_type: str, effect: str, effect_desc: str, header: str, routes: tuple[str, ...] = ()) -> str:
    """Closures come from effectDesc, header text and the line (a rail route number or routeType, so Lines 5 and 6 count
    whatever label alerts.ttc.ca picks), never from effect alone (spec 6.7, 6.8); a non-RSZ SIGNIFICANT_DELAYS is a delay."""
    if route_type == "Elevator":
        return "elevator"
    if route_type == "Escalator":
        return "escalator"
    if effect_desc == "Reduced Speed Zone":
        return "rsz"
    rail = route_type in RAIL_TYPES or any(r in RAIL_LINES for r in routes)
    if rail and (effect_desc.startswith("Subway Closure") or "no subway service" in header.lower()
                 or effect_desc == "No Service" or effect == "NO_SERVICE"):
        return "closure"
    if effect == "DETOUR" or effect_desc == "Detour":
        return "detour"
    if effect == "NO_SERVICE" or effect_desc == "No Service":
        return "suspension"
    if effect == "SIGNIFICANT_DELAYS":
        return "delay"
    return "other"


def _alert(x: dict) -> Alert:
    routes = tuple(r.strip() for r in (x.get("route") or "").split(",") if r.strip())
    kind = classify(x.get("routeType") or "", x.get("effect") or "", x.get("effectDesc") or "", x.get("headerText") or "", routes)
    period = x.get("activePeriod") or {}
    windows = []
    for c in x.get("childAlerts") or []:
        s, e = local_time(c.get("startTime")), local_time(c.get("endTime"))
        if s and e:
            windows.append((s, e))
    rsz = {v: x.get(k) for k, v in RSZ_FIELDS.items()} if kind == "rsz" else None
    return Alert(str(x.get("id")), kind, x.get("alertType") or "", routes, x.get("routeType") or "",
                 x.get("effect") or "", x.get("effectDesc") or "", x.get("direction") or None,
                 x.get("stopStart") or None, x.get("stopEnd") or None, tuple(str(s) for s in x.get("stops") or []),
                 tuple(str(s) for s in x.get("stopIDList") or []), x.get("shuttleType") or None,
                 local_time(period.get("start")), local_time(period.get("end")), tuple(windows), x.get("severity") or None,
                 x.get("cause") or None, x.get("causeDescription") or None, x.get("headerText") or "", x.get("title") or "",
                 x.get("url") or None, x.get("elevatorCode") or x.get("escalatorCode") or None, rsz, local_time(x.get("lastUpdated")))


def parse_alerts(body: bytes, headers: dict[str, str]) -> Parsed:
    doc = json.loads(body)
    if not isinstance(doc, dict) or "routes" not in doc:
        raise ValueError("alerts.ttc.ca: unexpected document")
    alerts = [_alert(x) for x in (doc.get("routes") or []) + (doc.get("accessibility") or [])]
    return Parsed(AlertList(local_time(doc.get("lastUpdated")), local_time(doc.get("rszLastUpdated")), alerts), None, len(alerts))
