from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
from typing import Literal

from fastmcp import FastMCP

from ttc_mcp import advisory, runtime
from ttc_mcp import overlay as overlay_mod
from ttc_mcp.adapters.bustime_rt import TripFeed
from ttc_mcp.cache import Hit
from ttc_mcp.domain import accessibility as acc
from ttc_mcp.domain import geo, live, planner, raptor, shapes
from ttc_mcp.domain.arrivals import stop_row
from ttc_mcp.domain.disruptions import Disruptions, station_row
from ttc_mcp.realtime import attempt
from ttc_mcp.static import schedule
from ttc_mcp.static.schedule import TZ
from ttc_mcp.tools.realtime_tools import _aguard
from ttc_mcp.tools.static_tools import _guard, _with_advisories

SOURCE = "static timetable + bustime getStopPredictions.jsp + ntas"
SOURCE_LIVE = "static timetable + bustime trips + gtfsrt trips/subway + bustime getStopPredictions.jsp + ntas"
LIVE_WINDOW = dt.timedelta(minutes=60)
WALK_MAX_M, LIMIT_MAX = 2000, 10
CLOSED = "every journey needs a closed section with no shuttle service"


def _input_error(origin, destination, walk_m: int, limit: int, depart_at: str | None, arrive_by: str | None = None,
                 window_min: int = raptor.WINDOW_DEFAULT_MIN) -> str | None:
    """The planner runs synchronously in a worker thread, so every input is bounded before it reaches it."""
    if not 1 <= limit <= LIMIT_MAX:
        return f"limit must be between 1 and {LIMIT_MAX}"
    if not 1 <= walk_m <= WALK_MAX_M:
        return f"walk_m must be between 1 and {WALK_MAX_M}"
    if not 0 <= window_min <= raptor.WINDOW_MAX_MIN:
        return f"window_min must be between 0 and {raptor.WINDOW_MAX_MIN}"
    if depart_at and arrive_by:
        return "give depart_at or arrive_by, not both"
    for which, spec in (("origin", origin), ("destination", destination)):
        try:
            if isinstance(spec, dict):
                float(spec["lat"]), float(spec["lon"])
        except (KeyError, TypeError, ValueError):
            return f"{which} needs numeric lat and lon"
    for name, value, example in (("depart_at", depart_at, "2026-10-06T08:30"), ("arrive_by", arrive_by, "2026-10-06T09:00")):
        if value:
            try:
                dt.datetime.fromisoformat(value)
            except ValueError:
                return f"{name} must be an ISO date-time such as {example}"
    return None


def _around_skipped(conn, disr: Disruptions, e: planner.Endpoint, which: str, walk_m: int, notes: list[str], modeled=frozenset()) -> planner.Endpoint:
    """Spec decision h: a stop number skipped by a detour with no variant pattern (an entity not in modeled) is planned from the served stops within walk_m of it."""
    el = disr.stop_skipped(e.label["stop"], modeled) if "stop" in e.label else None
    if el is None:
        return e
    row = stop_row(conn, e.label["stop"])
    near = planner.resolve_endpoint(conn, {"lat": row["lat"], "lon": row["lon"]}, walk_m=walk_m)
    served = {s: w for s, w in near.stops.items() if disr.stop_skipped(s, modeled) is None}
    if served:
        notes.append(f"{which} stop {e.label['stop']} is skipped by detour {el['detour_id']}; using the served stops within {walk_m} m of it")
    return planner.Endpoint(e.label, served)


async def _trip_feeds(rt, use_live: bool) -> tuple[TripFeed | None, TripFeed | None, list[Hit]]:
    """Both trip-update feeds, only inside the live window; a failed one is simply absent (realtime.attempt)."""
    if not use_live:
        return None, None, []
    (th, _), (sh, _) = await asyncio.gather(attempt(rt.trips()), attempt(rt.subway_trips()))
    return (th.parsed if th else None), (sh.parsed if sh else None), [h for h in (th, sh) if h]


def _slices(store, feed, day: dt.date, make_overlay=None) -> list[raptor.Slice]:
    """Today's feed with today's service ids, then yesterday's with yesterday's on today's clock; a day no feed covers reads the feed in hand.
    Each slice carries its feed's connection on this (the planning) thread, since pattern ids are per feed, and make_overlay(feed, timetable)'s overlay."""
    out = []
    for d, off in ((day, 0), (day - dt.timedelta(days=1), raptor.day_offset(day - dt.timedelta(days=1), day))):
        f = store.for_date(d) or feed
        tt = raptor.timetable_for(f)
        out.append(raptor.Slice(tt, frozenset(schedule.service_ids_on(f.conn, d)), off, make_overlay(f, tt) if make_overlay else None, f.conn))
    return out


def _plan(feed, store, disr, o, d, when: dt.datetime, max_transfers: int, window_min: int, arrive_by: bool, usable,
          limit: int | None = None, *, walk_m: int = planner.WALK_M, detour_feed=None, trips=None, subway=None) -> tuple[list[dict], str | None]:
    """Runs in a worker thread: it builds the slices' overlays and the query, runs the engine, and writes the first limit options through that
    thread's own connections. A stop skipped by a detour with no variant pattern keeps decision h's mask (an endpoint stop gives way to the served
    stops around it), applied here once the overlays say which entities run on variants; usable (or None) is the caller's own further predicate."""
    conn, day, stamp = feed.conn, when.date(), when.isoformat(timespec="minutes")
    t = int((when - schedule.wall_clock(day, 0)).total_seconds())
    # Arrive-by never asks the rider to have left already: now, on the query day's clock, floors the search.
    floor = int((runtime.now() - schedule.wall_clock(day, 0)).total_seconds())
    if arrive_by and floor >= t:
        return [], f"arrive_by {stamp} is not after now"
    # Both slices read the query day's overlay of their feed, so slices on one feed share it (the memo keeps one per feed version).
    slices = _slices(store, feed, day, lambda f, tt: overlay_mod.build(f, tt, disr, detour_feed, trips, subway, day))
    modeled = frozenset().union(*(sl.overlay.variants for sl in slices))
    masked = frozenset(s for s in disr.by_stop if disr.stop_skipped(s, modeled))
    ok = (lambda s: s not in masked) if usable is None else (lambda s: s not in masked and usable(s)) if masked else usable
    notes: list[str] = []
    o, d = _around_skipped(conn, disr, o, "origin", walk_m, notes, modeled), _around_skipped(conn, disr, d, "destination", walk_m, notes, modeled)
    for which, e in (("origin", o), ("destination", d)):
        if all(s in masked for s in e.stops):
            return [], "; ".join(notes + [f"every stop at the {which} is skipped by a detour"])
    ends = [{s: (geo.walk_seconds(m), m) for s, m in e.stops.items()} for e in (o, d)]
    closers = [planner.closed_for(sl.conn, disr, sl.tt) for sl in slices]
    q = raptor.Query(ends[0], ends[1], t, slices, max_transfers=max_transfers, usable=ok, closed=lambda si, pid: closers[si](pid))
    def run(query: raptor.Query) -> list[raptor.Journey]:
        if arrive_by:
            return raptor.plan_arrive_by(query, t, window_min * 60, step_s=raptor.ARRIVE_BY_STEP_MIN * 60, floor=floor)
        long = window_min > raptor.WINDOW_DEFAULT_MIN
        return raptor.plan_window(query, window_min * 60, (raptor.WINDOW_LONG_STEP_MIN if long else raptor.WINDOW_STEP_MIN) * 60)
    journeys = run(q)[:limit]
    note = None
    if not journeys:
        if not raptor.has_service(q, q.origin):
            note = f"no service at the origin stops on {day.isoformat()}"
        elif not raptor.has_service(q, q.dest):
            note = f"no service at the destination stops on {day.isoformat()}"
        elif run(dataclasses.replace(q, closed=lambda si, pid: raptor.Closed.NONE)):
            note = CLOSED
        elif arrive_by:
            note = f"no journey arriving by {stamp} within {raptor.ARRIVE_BY_SEARCH_H} hours before it"
        else:
            note = f"no journey with up to {max_transfers} transfers from {stamp}"
    options = planner.annotate(q, disr, [planner.materialize(conn, q, j, day) for j in journeys])
    return options, "; ".join(notes + ([note] if note else [])) or None


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
    async def connections(origin: str | dict, destination: str | dict, max_transfers: Literal[0, 1, 2, 3] = 3, walk_m: int = 400,
                          depart_at: str | None = None, arrive_by: str | None = None, window_min: int = raptor.WINDOW_DEFAULT_MIN, limit: int = 4) -> dict:
        """Timetable-exact trips between two places on the TTC: every ride is a real trip with its departure and arrival, transfers are
        timed (walking or station pathways plus a 2-minute change buffer), and the options are the Pareto set over arrival time and
        transfers, ranked by arrival. origin and destination are a stop number, a station id or alias, or {"lat", "lon"} from a geocoder.
        depart_at (ISO, Toronto when it has no offset; default now) with window_min (0 to 60, default 30) returns the journeys found
        every five minutes across the window (every ten when window_min is over 30), one per route sequence; arrive_by instead returns
        the latest departures that arrive on time, latest first, the first within five minutes of the latest that does; give one or the
        other. max_transfers is 0 to 3, walk_m (metres to and from the stops) 1 to 2000, limit 1 to 10.
        Each ride carries basis realtime (its trip has a trip update, applied within an hour of now), schedule, or estimate; the first
        ride lists the next departures in its direction. A closure masks the closed section; when its shuttle runs the shuttle is an
        estimated leg (assumed headway and speed, stated in its note) and the option is flagged estimate: true and ranked last. A
        detoured route's trips skip their stops with the published delay. Every option lists the disruptions it touches with the legs
        they affect, and an empty answer says why. Live departures and disruptions are best effort, from the TTC's own site endpoints."""
        async def run(feed):
            if err := _input_error(origin, destination, walk_m, limit, depart_at, arrive_by, window_min):
                return {"error": err}, [], ""
            conn, rt = feed.conn, runtime.get_realtime()
            when = dt.datetime.fromisoformat(arrive_by or depart_at) if (arrive_by or depart_at) else runtime.now()
            if when.tzinfo is None:
                when = when.replace(tzinfo=TZ)
            when = when.astimezone(TZ)
            ends = {"origin": planner.resolve_endpoint(conn, origin, walk_m=walk_m), "destination": planner.resolve_endpoint(conn, destination, walk_m=walk_m)}
            for which, e in ends.items():
                if e is None:
                    return {"error": f"could not resolve {which}: give a stop number, station name or {{lat, lon}}"}, [], ""
                if not e.stops:
                    return {"error": f"no stop within {walk_m} m of the {which}; raise walk_m or start from a stop"}, [], ""
            o, d = ends["origin"], ends["destination"]
            stamp = when.isoformat(timespec="minutes")
            head = {"from": o.label, "to": d.label, **({"arrive_by": stamp} if arrive_by else {"depart_at": stamp, "window_min": window_min})}
            if o.stops.keys() & d.stops.keys():
                return {**head, "options": [], "note": "origin and destination are within walking distance of the same stop; no ride is needed"}, [], ""
            snap = await advisory.snapshot(feed)
            # The snapshot is evaluated at the clock; a given time re-evaluates its alerts at that time.
            disr = Disruptions.build(lambda: feed.conn, snap.alerts, snap.rt, snap.detours, when) if (depart_at or arrive_by) else snap.disruptions
            # Trip updates move the timetable only near now (spec 6.10): the feeds are fetched when the query instant is within the live window.
            trips, subway, feed_hits = await _trip_feeds(rt, abs(when - runtime.now()) <= LIVE_WINDOW)
            opts, note = await asyncio.to_thread(_plan, feed, runtime.get_store(), disr, o, d, when, max_transfers, window_min, bool(arrive_by), None, limit,
                                                 walk_m=walk_m, detour_feed=snap.detour_feed, trips=trips, subway=subway)
            for i, opt in enumerate(opts, 1):
                opt["rank"] = i
            firsts = [leg for opt in opts if (leg := next((l for l in opt["legs"] if l["mode"] in ("ride", "shuttle")), None)) and leg["mode"] == "ride"]
            # Each first ride lists the departures from its own departure on, live when that is within an hour of now.
            ats = [dt.datetime.fromisoformat(leg["depart"]) for leg in firsts]
            lives = [abs(at - runtime.now()) <= LIVE_WINDOW for at in ats]
            nexts = await asyncio.gather(*(live.next_for_leg(feed, rt, leg, now=at, use_live=li, clock=runtime.now()) for leg, at, li in zip(firsts, ats, lives, strict=True)))
            for leg, (rows, _, nnote) in zip(firsts, nexts, strict=True):
                leg["next"] = rows
                if nnote:
                    leg["next_note"] = nnote
            # Pattern positions, the slice index and a variant's detour id are the planner's own; they served annotate and are not part of the answer.
            for opt in opts:
                for leg in opt["legs"]:
                    leg.pop("board_pos", None), leg.pop("alight_pos", None), leg.pop("slice", None), leg.pop("detour_id", None)
            out = {**head, "options": opts, **({"note": note} if note else {})}
            # The source names the trip feeds only when they were fetched (spec 6.10's contract).
            return out, feed_hits + [h for _, hits, _ in nexts for h in hits], SOURCE_LIVE if feed_hits else SOURCE if any(lives) else "", snap
        return await _aguard(run)
