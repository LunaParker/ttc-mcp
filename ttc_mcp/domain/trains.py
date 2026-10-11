from __future__ import annotations

import datetime as dt

from ttc_mcp.adapters.ntas import NextTrains
from ttc_mcp.domain.arrivals import rail_lines_at, served_at, stop_row
from ttc_mcp.static import schedule
from ttc_mcp.static.aliases import resolve_station

RAIL_PLATFORMS = ("SELECT s.* FROM stops s WHERE s.parent_station=? AND s.location_type=0 AND EXISTS ("
                  "SELECT 1 FROM stop_routes sr JOIN routes r ON r.route_id=sr.route_id "
                  "WHERE sr.stop_id=s.stop_id AND r.mode IN ('subway','lrt')) ORDER BY s.stop_id")
SUSPENDED_NOTE = "NTAS keeps publishing predictions inside a closure; this platform is suspended"


def rail_platforms(conn, station_id: str) -> list:
    return conn.execute(RAIL_PLATFORMS, (station_id,)).fetchall()


def resolve(conn, query: str):
    """A platform id, a station id, or any station alias -> (station row, rail platform rows)."""
    row = stop_row(conn, query.strip())
    if row is not None and row["location_type"] == 0:
        if row["parent_station"] and rail_lines_at(conn, row["stop_id"]):
            return conn.execute("SELECT * FROM stops WHERE stop_id=?", (row["parent_station"],)).fetchone(), [row]
        return None
    station = row if row is not None and row["location_type"] == 1 else next(iter(resolve_station(conn, query)), None)
    if station is None:
        return None
    platforms = rail_platforms(conn, station["stop_id"])
    return (station, platforms) if platforms else None


def platform_entry(conn, platform, trains: list[NextTrains] | None, error: str | None, now: dt.datetime, *, limit: int = 3) -> dict:
    pid = platform["stop_id"]
    lines = rail_lines_at(conn, pid)
    line = lines[0] if lines else None
    d, h = (served_at(conn, pid).get(line) or [(None, None)])[0]
    base = {"platform": pid, "name": platform["name"], "line": line, "direction_id": d, "headsign": h}
    t = next((t for t in trains or [] if t.line == line), None) or (trains[0] if trains else None)
    if t and t.next_min:
        return {**base, "direction_text": t.direction_text, "next_min": t.next_min, "basis": "realtime", "service": "normal", "source": "ntas"}
    sched = [r["in_min"] for r in schedule.departures_at(conn, pid, now, limit=limit, route=line)]
    note = (f"NTAS unavailable ({error}); times are from the timetable" if error
            else "NTAS returned no prediction for this platform; times are from the timetable")
    return {**base, "direction_text": t.direction_text if t else None, "next_min": [], "basis": "schedule", "service": "no_prediction",
            "scheduled_next_min": sched, "note": note, "source": "ntas + static stop_times"}


def suspended(entry: dict, closure: dict) -> dict:
    """A suspended platform's entry: no minutes, live or timetable (NTAS predicts at closed platforms, spec 8 item 7); what
    NTAS said stays visible as ntas_min and basis stays as it was."""
    rest = {k: v for k, v in entry.items() if k != "scheduled_next_min"}
    return {**rest, "next_min": [], "ntas_min": entry["next_min"], "service": "suspended", "disruption": closure, "note": SUSPENDED_NOTE}
