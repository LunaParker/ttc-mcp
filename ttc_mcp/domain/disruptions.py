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
# The station each pattern of a line continues to after this platform; a terminal platform yields no row.
NEXT_STATIONS = ("SELECT s.parent_station FROM pattern_stops ps JOIN patterns p ON p.pattern_id=ps.pattern_id "
                 "JOIN pattern_stops n ON n.pattern_id=ps.pattern_id AND n.pos=ps.pos+1 LEFT JOIN stops s ON s.stop_id=n.stop_id "
                 "WHERE ps.stop_id=? AND p.route_id=?")
LINE_PLATFORMS = ("SELECT DISTINCT s.stop_id FROM stops s JOIN stop_routes sr ON sr.stop_id=s.stop_id "
                  "WHERE s.parent_station=? AND s.location_type=0 AND sr.route_id=?")


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


def surface_alert(el: dict) -> dict:
    """A service_status.surface row from an alert element: the one shape the detour-feed rows share (spec 4.4)."""
    span = el["span"] or [None, None]
    return {"route": el["route"], "effect": "No Service" if el["type"] == "suspension" else "Detour", "source": el["source"],
            "alert_id": el["alert_id"], "detour_id": None, "direction": el["direction"], "from": span[0], "to": span[1],
            "skipped_stops": len(el["stops"]), "reason": el["cause"], "service_dates": None, "active_now": el["active_now"], "text": el["text"]}


def surface_detour(g: dict) -> dict:
    """A service_status.surface row from a detour group: its first direction's span, both ways when the group has both."""
    d, ids, reason = g["directions"][0], {x["direction_id"] for x in g["directions"]}, g["reason"] or {}
    return {"route": g["route"], "effect": "Detour", "source": "detour feed", "alert_id": None, "detour_id": g["detour_id"],
            "direction": "Both ways" if ids >= {"0", "1"} else d["headsign"], "from": (d["leaves_after"] or {}).get("name"),
            "to": (d["rejoins_at"] or {}).get("name"), "skipped_stops": len(g["skipped_stops"]), "reason": reason.get("title"),
            "service_dates": g["service_dates"], "active_now": g["active_today"], "text": reason.get("text")}


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
        self._served: dict[tuple, bool] = {}
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

    def platform_served(self, stop_id: str, line: str, el: dict) -> bool:
        """Span rule: a platform of a closure's end station is served when a pattern of the line carries on from it to a station
        outside the span. Interior platforms, terminals and one-station closures (trains run through) are not."""
        key = (stop_id, line, el.get("alert_id"))
        if key not in self._served:
            nexts = [r[0] for r in self.conn.execute(NEXT_STATIONS, (stop_id, line))] if len(el["stations"]) > 1 else []
            self._served[key] = any(st not in el["stations"] for st in nexts)
        return self._served[key]

    def shuttle_terminus(self, sid: str, el: dict, line: str | None = None) -> bool:
        lines = [line] if line else el["routes"]
        return any(self.platform_served(p[0], ln, el) for ln in lines for p in self.conn.execute(LINE_PLATFORMS, (sid, ln)))

    def _at(self, el: dict, sid: str) -> dict:
        """The element as attached to a station: a copy with the terminus role at a closure's served end, never the snapshot's dict."""
        if el["type"] == "closure" and el["active_now"] and self.shuttle_terminus(sid, el):
            return {**el, "role": "shuttle_terminus"}
        return el

    def platform_closed_now(self, row) -> dict | None:
        """A platform of a station closed now on one of its lines follows the span rule, whatever GTFS-RT lists; any other rail
        platform is closed when an entity listing it names no routes or one of its lines (they list other lines' platforms too)."""
        lines = rail_lines_at(self.conn, row["stop_id"])
        if not lines:
            return None
        station = row["parent_station"]
        for line in lines:
            el = self.station_closed_now(station, line) if station else None
            if el:
                return None if self.platform_served(row["stop_id"], line, el) else self._at(el, station)
        return next((el for el in self.platform_closed.get(row["stop_id"], []) if not el["routes"] or set(el["routes"]) & set(lines)), None)

    def stop_skipped(self, number: str) -> dict | None:
        return next((el for el in self.by_stop.get(number, []) if el["source"] == "detour feed" and el["active_now"]), None)

    def suffix_station(self, name: str | None) -> str | None:
        m = SUFFIX.search(name or "")
        row = station_by_name(self.conn, m.group(1)) if m else None
        return row["stop_id"] if row else None

    def for_station(self, sid: str) -> list[dict]:
        return [self._at(el, sid) for el in self.by_station.get(sid, [])]

    def for_stop(self, row) -> list[dict]:
        """The stop's own alerts; closures of its station (parent, or the '- X Station' suffix); route-level alerts for its routes."""
        out = list(self.by_stop.get(row["stop_id"], []))
        station = row["parent_station"] or self.suffix_station(row["name"])
        if station:
            out += [self._at(el, station) for el in self.by_station.get(station, []) if el["type"] == "closure"]
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
        """Per line: suspended inside an active closure, reduced at its served end (with the shuttle) or under an RSZ, else normal."""
        out = []
        for line in lines:
            els = [el for el in self.by_station.get(sid, []) if line in el["routes"]]
            closed = next((el for el in els if el["type"] == "closure" and el["active_now"]), None)
            reduced = closed or next((el for el in els if el["type"] in ("closure", "rsz")), None)
            service = "suspended" if closed else ("reduced" if reduced else "normal")
            if closed and self.shuttle_terminus(sid, closed, line):
                service, reduced = "reduced", {**closed, "role": "shuttle_terminus"}
            out.append({"line": line, "service": service, "disruption": reduced})
        return out

    def service_status(self, alerts: AlertList | None, groups: list[dict]) -> dict:
        closures = sorted((el for el in self.elements if el["type"] == "closure"), key=lambda el: (not el["active_now"], el["route"] or ""))
        surface = [surface_alert(el) for el in self.elements if el["type"] in ("suspension", "detour") and el["source"] == "alerts.ttc.ca"]
        surface += [surface_detour(g) for g in groups]
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
