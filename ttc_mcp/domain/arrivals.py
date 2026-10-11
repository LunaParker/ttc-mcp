from __future__ import annotations

import datetime as dt

from ttc_mcp.adapters.bustime_rt import VehicleFeed
from ttc_mcp.adapters.bustime_web import StopPredictions
from ttc_mcp.adapters.ntas import NextTrains
from ttc_mcp.domain.crowding import crowding
from ttc_mcp.static import schedule


def _norm(s: str) -> str:
    return " ".join(s.split())


def stop_row(conn, number: str):
    return conn.execute("SELECT * FROM stops WHERE stop_id=? OR stop_code=?", (number, number)).fetchone()


def rail_lines_at(conn, stop_id: str) -> list[str]:
    rows = conn.execute("SELECT DISTINCT sr.route_id FROM stop_routes sr JOIN routes r ON r.route_id=sr.route_id "
                        "WHERE sr.stop_id=? AND r.mode IN ('subway','lrt') ORDER BY sr.route_id", (stop_id,))
    return [r[0] for r in rows]


def served_at(conn, stop_id: str) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for r in conn.execute("SELECT route_id, direction_id, headsign FROM stop_routes WHERE stop_id=? ORDER BY trip_count DESC", (stop_id,)):
        out.setdefault(r["route_id"], []).append((str(r["direction_id"]), r["headsign"]))
    return out


def match_direction(served: dict, route: str, text: str) -> tuple[str | None, str]:
    """BusTime direction text vs GTFS headsigns at the stop; a one-direction stop needs no text match."""
    options = served.get(route, [])
    for d, h in options:
        if _norm(h).lower() == _norm(text).lower():
            return d, h
    if len({d for d, _ in options}) == 1:
        return options[0][0], _norm(text)
    return None, _norm(text)


def surface_rows(conn, stop_id: str, preds: StopPredictions, vehicles: VehicleFeed | None, *, route: str | None = None) -> list[dict]:
    served, rows = served_at(conn, stop_id), []
    for p in preds.predictions:
        if route and p.route != route:
            continue
        d, h = match_direction(served, p.route, p.direction_text)
        v = vehicles.vehicles.get(p.vehicle_id) if vehicles and p.vehicle_id else None
        rows.append({"route": p.route, "direction_id": d, "headsign": h, "eta_min": p.eta_min, "eta_text": p.eta_text,
                     "vehicle_id": p.vehicle_id, "basis": "realtime", "scheduled_only": p.scheduled_only,
                     "crowding": crowding(v.occupancy if v else None)})
    rows.sort(key=lambda r: (r["eta_min"] is None, r["eta_min"] or 0))
    return rows


def rail_rows(conn, stop_id: str, trains: list[NextTrains], *, route: str | None = None) -> list[dict]:
    """NTAS publishes no occupancy, so every rail row carries crowding No info (spec 6.8)."""
    served, rows = served_at(conn, stop_id), []
    for t in trains:
        if route and t.line != route:
            continue
        d, h = (served.get(t.line) or [(None, t.direction_text)])[0]
        rows.extend({"route": t.line, "direction_id": d, "headsign": h, "direction_text": t.direction_text, "eta_min": m,
                     "vehicle_id": None, "basis": "realtime", "scheduled_only": False, "crowding": crowding(None)} for m in t.next_min)
    rows.sort(key=lambda r: r["eta_min"])
    return rows


def schedule_rows(conn, stop_id: str, now: dt.datetime, *, limit: int, route: str | None = None, direction_id: str | None = None,
                  horizon_min: int | None = None) -> list[dict]:
    """Decision i: timetable rows carry basis schedule and never a vehicle or crowding."""
    return [{"route": d["route"], "direction_id": d["direction_id"], "headsign": d["headsign"], "eta_min": d["in_min"],
             "vehicle_id": None, "basis": "schedule", "scheduled_only": True, "scheduled_at": d["when"], "trip_id": d["trip_id"]}
            for d in schedule.departures_at(conn, stop_id, now, limit=limit, route=route, direction_id=direction_id, horizon_min=horizon_min)]


def fallback_note(preds: StopPredictions | None, error: str | None) -> str:
    if error:
        return f"real-time source unavailable ({error}); times are from the timetable"
    if preds is not None and preds.message:
        return f"BusTime: {preds.message}; times are from the timetable"
    return "no real-time prediction for this stop right now; times are from the timetable"
