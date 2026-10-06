from __future__ import annotations

import datetime as dt
import sqlite3
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Toronto")
UTC = dt.timezone.utc
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def day_label(days: int) -> str:
    return ",".join(n for i, n in enumerate(DAY_NAMES) if days & (1 << i))


def service_ids_on(conn: sqlite3.Connection, day: dt.date) -> set[str]:
    ymd = day.strftime("%Y%m%d")
    bit = 1 << day.weekday()
    ids = {r[0] for r in conn.execute(
        "SELECT service_id FROM calendar WHERE start_date<=? AND end_date>=? AND (days & ?)!=0", (ymd, ymd, bit))}
    for sid, exc in conn.execute("SELECT service_id, exception_type FROM calendar_dates WHERE date=?", (ymd,)):
        (ids.add if exc == 1 else ids.discard)(sid)
    return ids


def wall_clock(day: dt.date, secs: int) -> dt.datetime:
    """GTFS times count elapsed seconds from noon minus 12 h, so do the arithmetic in UTC."""
    noon = dt.datetime.combine(day, dt.time(12), tzinfo=TZ).astimezone(UTC)
    return (noon - dt.timedelta(hours=12) + dt.timedelta(seconds=secs)).astimezone(TZ)


def gtfs_hhmm(secs: int) -> str:
    return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}"


def departures_at(conn: sqlite3.Connection, stop_id: str, at: dt.datetime, *, limit: int = 10, route: str | None = None,
                  pattern_id: int | None = None, horizon_min: int | None = None) -> list[dict]:
    """Departures from `at`, soonest first; horizon_min keeps those leaving within that many whole minutes."""
    at = at.astimezone(TZ)
    out = []
    for day in (at.date(), at.date() - dt.timedelta(days=1)):
        sids = service_ids_on(conn, day)
        if not sids:
            continue
        since = int((at - wall_clock(day, 0)).total_seconds())
        marks = ",".join("?" * len(sids))
        sql = ("SELECT d.trip_id, d.dep_secs, d.stop_sequence, t.route_id, t.direction_id, t.headsign, t.pattern_id "
               "FROM departures d JOIN trips t ON t.trip_id = CAST(d.trip_id AS TEXT) "
               f"WHERE d.stop_id=? AND d.dep_secs>=? AND t.service_id IN ({marks})"
               + (" AND d.dep_secs<=?" if horizon_min is not None else "")
               + (" AND t.route_id=?" if route else "") + (" AND t.pattern_id=?" if pattern_id is not None else "")
               + " ORDER BY d.dep_secs LIMIT ?")
        # The SQL bound is a minute loose (sub-second clocks); in_min below is exact.
        params = ([int(stop_id), since, *sids] + ([since + (horizon_min + 1) * 60] if horizon_min is not None else [])
                  + ([route] if route else []) + ([pattern_id] if pattern_id is not None else []) + [limit])
        for r in conn.execute(sql, params):
            when = wall_clock(day, r["dep_secs"])
            if horizon_min is not None and (when - at).total_seconds() // 60 > horizon_min:
                continue
            out.append({"trip_id": str(r["trip_id"]), "route": r["route_id"], "direction_id": str(r["direction_id"]),
                        "headsign": r["headsign"], "pattern_id": r["pattern_id"], "stop_sequence": r["stop_sequence"],
                        "service_day": day.isoformat(), "dep_secs": r["dep_secs"], "gtfs_time": gtfs_hhmm(r["dep_secs"]),
                        "when": when.isoformat(), "in_min": int((when - at).total_seconds() // 60)})
    out.sort(key=lambda r: r["when"])
    return out[:limit]


def service_hours(conn: sqlite3.Connection, route: str, day: dt.date) -> dict:
    none = {"runs": False, "service_ids": [], "first": None, "last": None, "first_at": None, "last_at": None, "trips": 0}
    sids = sorted(service_ids_on(conn, day))
    if not sids:
        return none
    marks = ",".join("?" * len(sids))
    first, last, n = conn.execute(
        f"SELECT min(start_secs), max(end_secs), count(*) FROM trips WHERE route_id=? AND service_id IN ({marks})",
        [route, *sids]).fetchone()
    if not n:
        return {**none, "service_ids": sids}
    return {"runs": True, "service_ids": sids, "first": gtfs_hhmm(first), "last": gtfs_hhmm(last),
            "first_at": wall_clock(day, first).isoformat(), "last_at": wall_clock(day, last).isoformat(), "trips": n}
