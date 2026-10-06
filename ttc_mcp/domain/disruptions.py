"""The shared disruptions element (spec 4.4) and the lookups every tool uses to propagate it (spec 6.7 item 4)."""
from __future__ import annotations

import datetime as dt

from ttc_mcp.adapters.gtfsrt_alerts import RtAlertFeed
from ttc_mcp.adapters.ttc_alerts import Alert, AlertList
from ttc_mcp.domain.arrivals import rail_lines_at, served_at
from ttc_mcp.domain.freshness import iso
from ttc_mcp.static.aliases import SUFFIX, resolve_station

STATION_KINDS = ("closure", "rsz", "elevator", "escalator")
UNIT_KINDS = ("elevator", "escalator")


def _iso(t: dt.datetime | None) -> str | None:
    return t.isoformat(timespec="minutes") if t else None


def station_row(conn, raw: str):
    return conn.execute("SELECT * FROM stops WHERE location_type=1 AND (stop_id=? OR stop_code=?)", (raw, raw)).fetchone()


def station_by_name(conn, name: str):
    hits = resolve_station(conn, name)
    return hits[0] if hits else None


def _active(a: Alert, now: dt.datetime) -> bool:
    if a.windows:
        return any(s <= now < e for s, e in a.windows)
    return (a.start is None or a.start <= now) and (a.end is None or now < a.end)


def element(conn, a: Alert, now: dt.datetime) -> dict:
    stations, names, stops = [], [], []
    hint = a.header.split(":")[0].strip() if ":" in a.header else None
    if a.kind in STATION_KINDS:
        for raw in a.stop_ids:
            row = station_row(conn, raw)
            if row is None:
                stops.append(raw)
            elif row["stop_id"] not in stations:
                stations.append(row["stop_id"])
                names.append(row["name"])
        if not stations:
            for name in a.station_names or ([hint] if hint else []):
                row = station_by_name(conn, name)
                if row and row["stop_id"] not in stations:
                    stations.append(row["stop_id"])
                    names.append(row["name"])
    else:
        stops = list(a.stop_ids)
    return {"type": a.kind, "source": "alerts.ttc.ca", "alert_id": a.id, "alert_type": a.alert_type,
            "route": a.routes[0] if a.routes else None, "routes": list(a.routes), "route_type": a.route_type,
            "stations": stations, "station_names": names, "station_hint": hint, "stops": stops,
            "span": [a.stop_start, a.stop_end] if a.stop_start or a.stop_end else None, "direction": a.direction,
            "active_now": _active(a, now), "period": [_iso(a.start), _iso(a.end)], "windows": [[_iso(s), _iso(e)] for s, e in a.windows],
            "shuttle": a.shuttle, "severity": a.severity, "cause": a.cause_desc or a.cause, "text": a.header or a.title,
            "url": a.url, "unit_code": a.unit_code, "rsz": a.rsz}


def detour_elements(groups: list[dict]) -> list[dict]:
    out = []
    for g in groups:
        dates = g["service_dates"]
        for d in g["directions"]:
            out.append({"type": "detour", "source": "detour feed", "detour_id": g["detour_id"], "entity_id": d["entity_id"],
                        "route": g["route"], "routes": [g["route"]], "direction_id": d["direction_id"], "headsign": d["headsign"],
                        "pattern": d["pattern"], "stations": [], "station_names": [], "stops": [s["number"] for s in d["skipped"]],
                        "leaves_after": d["leaves_after"], "rejoins_at": d["rejoins_at"], "active_now": g["active_today"],
                        "period": [dates[0] if dates else None, dates[-1] if dates else None],
                        "text": g["reason"]["title"] if g["reason"] else None, "url": g["reason"]["url"] if g["reason"] else None})
    return out


def platform_closures(rt: RtAlertFeed | None, now_epoch: float) -> dict[str, list[dict]]:
    """Per-night NO_SERVICE entities list the closed platform ids (every entity per platform, in feed order);
    REDUCED_SERVICE umbrellas span whole days and are ignored here."""
    out: dict[str, list[dict]] = {}
    for a in (rt.alerts if rt else []):
        if a.effect != "NO_SERVICE" or not a.stops:
            continue
        if (a.start and now_epoch < a.start) or (a.end and now_epoch >= a.end):
            continue
        el = {"type": "closure", "source": "gtfsrt alerts", "alert_id": a.id, "route": a.routes[0] if a.routes else None,
              "routes": list(a.routes), "stations": [], "station_names": [], "stops": [], "platforms": list(a.stops), "active_now": True,
              "period": [iso(a.start) if a.start else None, iso(a.end) if a.end else None], "text": a.header, "url": a.url}
        for pid in a.stops:
            out.setdefault(pid, []).append(el)
    return out


def element_key(el: dict) -> tuple:
    return ("detour", el.get("detour_id")) if el.get("source") == "detour feed" else ("alert", el.get("alert_id"))


def _dedupe(els: list[dict]) -> list[dict]:
    seen, out = set(), []
    for el in els:
        if element_key(el) not in seen:
            seen.add(element_key(el))
            out.append(el)
    return out


class Disruptions:
    def __init__(self, conn, elements: list[dict], platform_closed: dict[str, list[dict]]):
        self.conn, self.elements, self.platform_closed = conn, elements, platform_closed
        self.by_station: dict[str, list[dict]] = {}
        self.by_stop: dict[str, list[dict]] = {}
        self.by_route: dict[str, list[dict]] = {}
        for el in elements:
            for sid in el["stations"]:
                self.by_station.setdefault(sid, []).append(el)
            for n in el["stops"]:
                self.by_stop.setdefault(n, []).append(el)
            for r in el["routes"]:
                self.by_route.setdefault(r, []).append(el)

    @classmethod
    def build(cls, conn, alerts: AlertList | None, rt: RtAlertFeed | None, groups: list[dict], now: dt.datetime) -> "Disruptions":
        els = [element(conn, a, now) for a in (alerts.alerts if alerts else [])] + detour_elements(groups)
        return cls(conn, els, platform_closures(rt, now.timestamp()))

    def station_closed_now(self, sid: str, line: str | None = None) -> dict | None:
        return next((el for el in self.by_station.get(sid, []) if el["type"] == "closure" and el["active_now"]
                     and (line is None or line in el["routes"])), None)

    def platform_closed_now(self, row) -> dict | None:
        """Closed when a GTFS-RT entity listing the platform names no routes or one of its lines (entities also list other
        lines' platforms at interchanges), or when its station is closed now on one of the platform's own lines."""
        lines = rail_lines_at(self.conn, row["stop_id"])
        if not lines:
            return None
        listed = next((el for el in self.platform_closed.get(row["stop_id"], []) if not el["routes"] or set(el["routes"]) & set(lines)), None)
        if listed:
            return listed
        if not row["parent_station"]:
            return None
        return next((el for line in lines if (el := self.station_closed_now(row["parent_station"], line))), None)

    def stop_skipped(self, number: str) -> dict | None:
        return next((el for el in self.by_stop.get(number, []) if el["source"] == "detour feed" and el["active_now"]), None)

    def suffix_station(self, name: str | None) -> str | None:
        m = SUFFIX.search(name or "")
        row = station_by_name(self.conn, m.group(1)) if m else None
        return row["stop_id"] if row else None

    def for_station(self, sid: str) -> list[dict]:
        return list(self.by_station.get(sid, []))

    def for_stop(self, row) -> list[dict]:
        """The stop's own alerts; closures of its station (parent, or the '- X Station' suffix); route-level alerts for its routes."""
        out = list(self.by_stop.get(row["stop_id"], []))
        station = row["parent_station"] or self.suffix_station(row["name"])
        if station:
            out += [el for el in self.by_station.get(station, []) if el["type"] == "closure"]
        is_platform = bool(row["parent_station"]) and bool(rail_lines_at(self.conn, row["stop_id"]))
        for route in served_at(self.conn, row["stop_id"]):
            for el in self.by_route.get(route, []):
                if el["type"] in UNIT_KINDS or el["type"] == "rsz":
                    continue
                if not el["stations"] or (is_platform and el["type"] == "closure"):
                    out.append(el)
        return _dedupe(out)

    def route_stop_status(self, row) -> str:
        if self.stop_skipped(row["stop_id"]):
            return "skipped_by_detour"
        return "closed" if self.platform_closed_now(row) else "open"

    def station_service(self, sid: str, lines: list[str]) -> list[dict]:
        out = []
        for line in lines:
            els = [el for el in self.by_station.get(sid, []) if line in el["routes"]]
            closed = next((el for el in els if el["type"] == "closure" and el["active_now"]), None)
            reduced = closed or next((el for el in els if el["type"] in ("closure", "rsz")), None)
            out.append({"line": line, "service": "suspended" if closed else ("reduced" if reduced else "normal"), "disruption": reduced})
        return out

    def service_status(self, alerts: AlertList | None, groups: list[dict]) -> dict:
        closures = sorted((el for el in self.elements if el["type"] == "closure"), key=lambda el: (not el["active_now"], el["route"] or ""))
        surface = [el for el in self.elements if el["type"] in ("suspension", "detour") and el["source"] == "alerts.ttc.ca"]
        surface += [{"route": g["route"], "effect": "Detour", "source": "detour feed", "detour_id": g["detour_id"],
                     "skipped_stops": len(g["skipped_stops"]), "active_now": g["active_today"], "service_dates": g["service_dates"],
                     "reason": g["reason"]["title"] if g["reason"] else None} for g in groups]
        rsz = [el for el in self.elements if el["type"] == "rsz"]
        units: dict[str, dict[str, list]] = {"elevator": {}, "escalator": {}}
        for el in self.elements:
            if el["type"] in units:
                key = el["station_names"][0] if el["station_names"] else (el["station_hint"] or "unknown")
                units[el["type"]].setdefault(key, []).append(el["unit_code"])
        return {"as_of": _iso(alerts.last_updated) if alerts else None, "rsz_as_of": _iso(alerts.rsz_last_updated) if alerts else None,
                "closures": closures, "surface": surface,
                "reduced_speed_zones": {"count": len(rsz), "zones": [{"route": el["route"], "direction": el["direction"], "span": el["span"],
                                                                     "stations": el["station_names"], "text": el["text"], **(el["rsz"] or {})} for el in rsz]},
                "elevators_out": units["elevator"], "escalators_out": units["escalator"],
                "counts": {"closures": len(closures), "closures_active_now": sum(el["active_now"] for el in closures), "surface": len(surface),
                           "rsz": len(rsz), "elevators_out": sum(len(v) for v in units["elevator"].values()),
                           "escalators_out": sum(len(v) for v in units["escalator"].values())}}
