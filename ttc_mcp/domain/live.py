"""The arrivals path shared by the arrivals tool and the connections overlay."""
from __future__ import annotations

import asyncio
import datetime as dt

from ttc_mcp.domain import arrivals as arr
from ttc_mcp.realtime import Realtime, attempt

SURFACE = "bustime getStopPredictions.jsp + gtfsrt/vehicles"
NEXT_KEYS = ("eta_min", "vehicle_id", "crowding", "basis", "scheduled_at", "headsign")
SCAN = 12  # rows read before the direction filter, so a two-way stop still yields three
NO_DIRECTION = "no real-time prediction in this direction right now; times are from the timetable"


async def arrivals_at(feed, rt: Realtime, row, *, route: str | None, limit: int, now: dt.datetime) -> tuple[list[dict], list, str, str | None]:
    conn, sid = feed.conn, row["stop_id"]
    if arr.rail_lines_at(conn, sid):
        hit, err = await attempt(rt.next_trains(sid))
        hits = [hit] if hit else []
        rows = arr.rail_rows(conn, sid, hit.parsed, route=route) if hit else []
        source, preds = "ntas", None
    else:
        (hit, err), (vhit, _) = await asyncio.gather(attempt(rt.stop_predictions(sid)), attempt(rt.vehicles()))
        hits = [h for h in (hit, vhit) if h]
        rows = arr.surface_rows(conn, sid, hit.parsed, vhit.parsed if vhit else None, route=route) if hit else []
        source, preds = SURFACE, hit.parsed if hit else None
    note = None
    if not rows:
        rows = arr.schedule_rows(conn, sid, now, limit=limit, route=route)
        note, source = arr.fallback_note(preds, err), source + " + static stop_times"
    return rows[:limit] if note else rows, hits, source, note


async def next_for_leg(feed, rt: Realtime, leg: dict, *, now: dt.datetime, use_live: bool, n: int = 3) -> tuple[list[dict], list, str | None]:
    """The next n departures of a ride leg's route in its direction (unknown directions kept): live when use_live, else the timetable."""
    conn, sid = feed.conn, leg["board"]["number"]
    rows, hits, note = [], [], None
    if use_live:
        rows, hits, _, note = await arrivals_at(feed, rt, arr.stop_row(conn, sid), route=leg["route"], limit=SCAN, now=now)
        rows = [r for r in rows if r["direction_id"] in (None, leg["direction_id"])]
        note = note or (None if rows else NO_DIRECTION)
    if not rows:
        rows = [r for r in arr.schedule_rows(conn, sid, now, limit=SCAN, route=leg["route"]) if r["direction_id"] == leg["direction_id"]]
    return [{k: r.get(k) for k in NEXT_KEYS} for r in rows[:n]], hits, note
