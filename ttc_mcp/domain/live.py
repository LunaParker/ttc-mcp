"""The arrivals path shared by the arrivals tool and the connections overlay."""
from __future__ import annotations

import asyncio
import datetime as dt

from ttc_mcp.domain import arrivals as arr
from ttc_mcp.realtime import Realtime, attempt

SURFACE = "bustime getStopPredictions.jsp + gtfsrt/vehicles"


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
