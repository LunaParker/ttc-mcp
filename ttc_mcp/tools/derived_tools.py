from __future__ import annotations

import asyncio
import datetime as dt
from typing import Literal

from fastmcp import FastMCP

from ttc_mcp import advisory, runtime
from ttc_mcp.domain import accessibility as acc, live, planner, shapes
from ttc_mcp.domain.arrivals import stop_row
from ttc_mcp.domain.disruptions import Disruptions, station_row
from ttc_mcp.static.schedule import TZ
from ttc_mcp.tools.realtime_tools import _aguard
from ttc_mcp.tools.static_tools import _guard, _with_advisories

SOURCE = "static patterns + bustime getStopPredictions.jsp + ntas"
LIVE_WINDOW = dt.timedelta(minutes=60)
WALK_MAX_M, LIMIT_MAX = 2000, 10
CLOSED = "every option is cut by a closure (no shuttle service, or a bypassed boarding or alighting station)"


def _input_error(origin, destination, walk_m: int, limit: int, depart_at: str | None) -> str | None:
    """The planner runs synchronously on the event loop, so walk_m and limit are bounded and bad input never reaches it."""
    if not 1 <= limit <= LIMIT_MAX:
        return f"limit must be between 1 and {LIMIT_MAX}"
    if not 1 <= walk_m <= WALK_MAX_M:
        return f"walk_m must be between 1 and {WALK_MAX_M}"
    for which, spec in (("origin", origin), ("destination", destination)):
        try:
            if isinstance(spec, dict):
                float(spec["lat"]), float(spec["lon"])
        except (KeyError, TypeError, ValueError):
            return f"{which} needs numeric lat and lon"
    if depart_at:
        try:
            dt.datetime.fromisoformat(depart_at)
        except ValueError:
            return "depart_at must be an ISO date-time such as 2026-10-06T08:30"
    return None


def _around_skipped(conn, disr: Disruptions, e: planner.Endpoint, which: str, walk_m: int, notes: list[str]) -> planner.Endpoint:
    """Spec decision h: a detour-skipped stop number is planned from the served stops within walk_m of it."""
    el = disr.stop_skipped(e.label["stop"]) if "stop" in e.label else None
    if el is None:
        return e
    row = stop_row(conn, e.label["stop"])
    near = planner.resolve_endpoint(conn, {"lat": row["lat"], "lon": row["lon"]}, walk_m=walk_m)
    served = {s: w for s, w in near.stops.items() if disr.stop_skipped(s) is None}
    if served:
        notes.append(f"{which} stop {e.label['stop']} is skipped by detour {el['detour_id']}; using the served stops within {walk_m} m of it")
    return planner.Endpoint(e.label, served)


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def route_shape(route: str, direction: Literal["0", "1"], pattern: int | None = None, format: Literal["polyline", "geojson"] = "polyline",
                    simplify_m: float | None = None, from_stop: str | None = None, to_stop: str | None = None) -> dict:
        """The geometry of a route and direction: the most-used stop pattern's shape (or another pattern by rank) as an encoded
        polyline or GeoJSON LineString, with its stops and their distance along the shape. Optional Douglas-Peucker simplification
        (metres) and a from_stop/to_stop span sliced by shape_dist_traveled (the first visit of each when a stop repeats)."""
        def run(f):
            if bool(from_stop) != bool(to_stop):
                return {"error": "give both from_stop and to_stop for a span, or neither"}
            between = (from_stop, to_stop) if from_stop and to_stop else None
            try:
                d = shapes.route_shape(f.conn, route, direction, pattern=pattern, fmt=format, simplify_m=simplify_m, between=between)
            except ValueError as e:
                return {"error": str(e)}
            return d if d else {"error": f"no pattern {pattern or 1} for route {route} direction {direction}"}
        return _guard(run)

    @mcp.tool
    async def accessibility(query: str) -> dict:
        """Accessibility of a station (id, name or alias) or a stop: platform and entrance wheelchair flags, the station summary,
        elevator and escalator units with their levels and live status (alerts joined to pathway nodes by unit code; codes without
        a node are reported with node_match false), uncoded node counts and a step-free verdict with its basis. Outages announced
        but not yet active are listed under upcoming and leave the units and the verdict alone. A platform or entrance id answers
        for its station, with queried_platform or queried_entrance naming what was asked."""
        def run(f, snap):
            row = acc.resolve(f.conn, query)
            if row is None:
                return {"error": f"{query!r} is not a station or stop"}
            if row["location_type"] == 1:
                return {"accessibility": acc.for_station(f.conn, row, snap.disruptions)}
            if row["parent_station"]:
                parent = station_row(f.conn, row["parent_station"])
                if parent is None:
                    return {"error": f"stop {query} has no station record"}
                out = {"accessibility": acc.for_station(f.conn, parent, snap.disruptions)}
                if row["location_type"] == 0:
                    out["queried_platform"] = row["stop_id"]
                elif row["location_type"] == 2:
                    out["queried_entrance"] = row["stop_id"]
                return out
            return {"accessibility": acc.for_stop(f.conn, row)}
        return await _with_advisories(run)

    @mcp.tool
    async def connections(origin: str | dict, destination: str | dict, max_transfers: Literal[0, 1] = 1, walk_m: int = 400,
                          depart_at: str | None = None, limit: int = 4) -> dict:
        """Trip options between two places on the TTC: direct and one-transfer rides over the scheduled stop patterns with typical
        minutes, walking legs and a transfer penalty, ranked by transfers then time. origin and destination are a stop number, a
        station id or alias, or {"lat", "lon"} from a geocoder; depart_at is ISO, Toronto time when it has no offset (default now);
        walk_m (metres to and from the stops) is 1 to 2000 and limit 1 to 10.
        The first leg, when it is a ride, carries next departures in its direction: live (basis realtime, or schedule with next_note)
        when depart_at is within an hour of now, else from the timetable. Closures cut rail legs into ride, shuttle, ride: the
        shuttle's minutes are unknown, so such an option has typical_min null with known_min for its other legs and ranks after
        complete options with the same transfers; every vehicle change counts as a transfer, and max_transfers bounds only the
        search over scheduled patterns, so a ride, shuttle, ride option reports 2 transfers under max_transfers=1.
        Detour-skipped stops are never used (a skipped stop number plans from the served stops within walk_m of it, with a note);
        every option lists the disruptions it touches, including route-level delays, each naming the legs it affects, and an
        empty answer says why. Typical minutes come from the timetable, not from live data."""
        async def run(feed):
            if err := _input_error(origin, destination, walk_m, limit, depart_at):
                return {"error": err}, [], ""
            conn, rt = feed.conn, runtime.get_realtime()
            now = dt.datetime.fromisoformat(depart_at) if depart_at else runtime.now()
            if now.tzinfo is None:
                now = now.replace(tzinfo=TZ)
            ends = {"origin": planner.resolve_endpoint(conn, origin, walk_m=walk_m), "destination": planner.resolve_endpoint(conn, destination, walk_m=walk_m)}
            for which, e in ends.items():
                if e is None:
                    return {"error": f"could not resolve {which}: give a stop number, station name or {{lat, lon}}"}, [], ""
                if not e.stops:
                    return {"error": f"no stop within {walk_m} m of the {which}; raise walk_m or start from a stop"}, [], ""
            o, d = ends["origin"], ends["destination"]
            head = {"from": o.label, "to": d.label, "depart_at": now.isoformat(timespec="minutes")}
            if o.stops.keys() & d.stops.keys():
                return {**head, "options": [], "note": "origin and destination are within walking distance of the same stop; no ride is needed"}, [], ""
            snap = await advisory.snapshot(feed)
            # The snapshot is evaluated at the clock; a given depart_at re-evaluates its alerts at that time.
            disr = Disruptions.build(conn, snap.alerts, snap.rt, snap.detours, now) if depart_at else snap.disruptions
            usable, verdict = (lambda sid: disr.stop_skipped(sid) is None), planner.closure_verdicts(conn, disr)
            notes: list[str] = []
            o, d = _around_skipped(conn, disr, o, "origin", walk_m, notes), _around_skipped(conn, disr, d, "destination", walk_m, notes)
            for which, e in (("origin", o), ("destination", d)):
                if not any(usable(s) for s in e.stops):
                    return {**head, "options": [], "note": "; ".join(notes + [f"every stop at the {which} is skipped by a detour"])}, [], "", snap
            found = planner.search(conn, o, d, now=now, max_transfers=max_transfers, limit=limit * 3, usable=usable,
                                   rideable=lambda pid, a, b: verdict(pid, a, b) != "drop")
            opts = planner.overlay(conn, disr, found)[:limit]
            use_live = depart_at is None or abs(now - runtime.now()) <= LIVE_WINDOW
            rides = [(opt, leg) for opt in opts if (leg := next((l for l in opt["legs"] if l["mode"] != "walk"), None)) and leg["mode"] == "ride"]
            nexts = await asyncio.gather(*(live.next_for_leg(feed, rt, leg, now=now, use_live=use_live, clock=runtime.now()) for _, leg in rides))
            for opt in opts:
                opt["basis"] = "schedule"
            for (opt, leg), (rows, _, note) in zip(rides, nexts):
                leg["next"] = rows
                if note:
                    leg["next_note"] = note
                opt["basis"] = rows[0]["basis"] if rows else "schedule"
            # Pattern positions index a private table and the departure record is the timetable's; neither is part of the answer.
            for opt in opts:
                for leg in opt["legs"]:
                    for key in ("board_pos", "alight_pos"):
                        leg.pop(key, None)
                opt["first_departure"] = {k: opt["first_departure"][k] for k in ("when", "in_min", "trip_id")}
            # Closed spans are pruned inside search; an unconstrained search tells a closure from an empty timetable.
            if not opts and (found or planner.search(conn, o, d, now=now, max_transfers=max_transfers, limit=1, usable=usable)):
                notes.append(CLOSED)
            elif not opts:
                trip = "direct" if max_transfers == 0 else "direct or one-transfer"
                notes.append(f"no {trip} trip with a scheduled departure within {planner.HORIZON_MIN} minutes")
            out = {**head, "options": opts, **({"note": "; ".join(notes)} if notes else {})}
            # Only a live fetch earns real-time meta; timetable-only answers keep static meta plus advisories.
            return out, [h for _, hits, _ in nexts for h in hits], SOURCE if use_live and rides else "", snap
        return await _aguard(run)
