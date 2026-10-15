"""The arrivals path shared by the arrivals tool and the connections overlay."""
from __future__ import annotations

import asyncio
import datetime as dt

from ttc_mcp.domain import arrivals as arr
from ttc_mcp.realtime import Realtime, attempt

HORIZON_MIN = 120
SURFACE = "bustime getStopPredictions.jsp + gtfsrt/vehicles"
NEXT_KEYS = ("eta_min", "vehicle_id", "crowding", "basis", "scheduled_at", "headsign")
SCAN = 12  # live rows read before the direction filter, so a two-way stop still yields three
NO_DIRECTION = "no real-time prediction in this direction right now; times are from the timetable"
NONE_LATER = "no real-time prediction in this direction from the planned departure on; times are from the timetable"
NO_SCHEDULE = f"no scheduled departure in this direction within {HORIZON_MIN} minutes"


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


async def next_for_leg(feed, rt: Realtime, leg: dict, *, now: dt.datetime, use_live: bool, clock: dt.datetime | None = None,
                      n: int = 3) -> tuple[list[dict], list, str | None]:
    """The next n departures of a ride leg's route in its direction (unknown directions kept) from now (depart_at): live when
    use_live, dropping vehicles due (clock plus eta_min) before now, else the timetable in that direction within the horizon."""
    conn, sid = feed.conn, leg["board"]["number"]
    rows, hits, note = [], [], None
    if use_live:
        rows, hits, _, note = await arrivals_at(feed, rt, arr.stop_row(conn, sid), route=leg["route"], limit=SCAN, now=now)
        rows = [r for r in rows if r["basis"] == "realtime" and r["direction_id"] in (None, leg["direction_id"])]
        lead = (now - (clock or now)).total_seconds() / 60
        later = [r for r in rows if r["eta_min"] is None or r["eta_min"] >= lead]
        note = note or (None if later else NONE_LATER if rows else NO_DIRECTION)
        rows = later
    if not rows:
        rows = arr.schedule_rows(conn, sid, now, limit=n, route=leg["route"], direction_id=leg["direction_id"], horizon_min=HORIZON_MIN)
        note = note if rows else NO_SCHEDULE
    return [{k: r.get(k) for k in NEXT_KEYS} for r in rows[:n]], hits, note
