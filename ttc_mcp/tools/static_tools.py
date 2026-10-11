from __future__ import annotations

import datetime as dt
from typing import Literal

from fastmcp import FastMCP

from ttc_mcp import advisory, runtime
from ttc_mcp.domain import network
from ttc_mcp.domain.arrivals import stop_row
from ttc_mcp.static import schedule


def _guard(fn):
    """Run fn(feed) for today's feed; turn a missing store into a readable error payload."""
    try:
        feed = runtime.feed_for()
    except runtime.StoreUnavailable as e:
        return {"error": str(e), "meta": runtime.meta(None)}
    return {**fn(feed), "meta": runtime.meta(feed)}


async def _with_advisories(fn):
    """fn(feed, snapshot) -> payload; static meta plus the advisory snapshot's own freshness."""
    try:
        feed = runtime.feed_for()
    except runtime.StoreUnavailable as e:
        return {"error": str(e), "meta": runtime.meta(None)}
    snap = await advisory.snapshot(feed)
    payload = fn(feed, snap)
    if "error" in payload:
        return {**payload, "meta": runtime.meta(feed)}
    return {**payload, "meta": {**runtime.meta(feed), "advisories": snap.meta()}}


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def find_stops(query: str, route: str | None = None, direction: Literal["0", "1"] | None = None,
                   mode: Literal["bus", "streetcar", "subway", "lrt"] | None = None, limit: int = 25) -> dict:
        """Find TTC stops, platforms or stations by name, stop number, station name or alias (e.g. "Dundas" resolves to TMU).
        Stops are directional: the two sides of an intersection are different stop numbers with different headsigns."""
        return _guard(lambda f: {"stops": network.find_stops(f.conn, query, route=route, direction=direction, mode=mode, limit=limit)})

    @mcp.tool
    async def nearest_stops(lat: float, lon: float, radius_m: int = 500, limit: int = 10,
                            kind: Literal["any", "stop", "station"] = "any") -> dict:
        """Stops and stations nearest to a coordinate (geocode addresses with a maps tool first), with walk minutes, the routes,
        directions and headsigns served, and active disruptions per result."""
        def run(f, snap):
            results = network.nearest_stops(f.conn, lat, lon, radius_m=radius_m, limit=limit, kind=kind)
            for r in results:
                r["disruptions"] = (snap.disruptions.for_station(r["number"]) if r["kind"] == "station"
                                    else snap.disruptions.for_stop(stop_row(f.conn, r["number"])))
            return {"origin": {"lat": lat, "lon": lon}, "results": results}
        return await _with_advisories(run)

    @mcp.tool
    async def stop(number: str) -> dict:
        """One stop or platform by public stop number: name, coordinates, wheelchair flag, routes/directions/headsigns served, station
        membership, and the disruptions touching it (its station's closures, detours skipping it, alerts on its routes)."""
        def run(f, snap):
            d = network.stop_detail(f.conn, number)
            if not d:
                return {"error": f"stop {number} not found"}
            row = stop_row(f.conn, d["number"])
            d["disruptions"] = snap.disruptions.for_station(d["number"]) if row["location_type"] == 1 else snap.disruptions.for_stop(row)
            return {"stop": d}
        return await _with_advisories(run)

    @mcp.tool
    async def station(query: str) -> dict:
        """A subway or LRT station by id, name or alias: rail platforms (with open/closed status), surface stops at the station
        (bus bays, streetcar platforms, with status), entrances, accessibility summary from the rail platforms, service per line
        (normal / reduced / suspended with the closure), and every disruption touching the station."""
        def run(f, snap):
            d = network.station_detail(f.conn, query)
            if not d:
                return {"error": f"station {query!r} not found"}
            d["disruptions"] = snap.disruptions.for_station(d["id"])
            d["service"] = snap.disruptions.station_service(d["id"], d["lines"])
            for p in d["platforms"] + d["surface_stops"]:
                p["status"] = snap.disruptions.route_stop_status(stop_row(f.conn, p["number"]))
            return {"station": d}
        return await _with_advisories(run)

    @mcp.tool
    def routes(series: str | None = None, overnight: bool | None = None, mode: str | None = None) -> dict:
        """All TTC routes with mode (subway, lrt, streetcar, bus), series (rail, bus, express, blue-night, community, streetcar),
        service hours by day type and overnight share. overnight=true lists the Blue Night network."""
        return _guard(lambda f: {"routes": network.list_routes(f.conn, series=series, overnight=overnight, mode=mode)})

    @mcp.tool
    async def route_stops(route: str, direction: Literal["0", "1"], pattern: int | None = None) -> dict:
        """Ordered stops for a route and direction, grouped by stop pattern (branches, short turns) with trip counts, headsigns,
        typical minutes from the first stop, and a status per stop: open, closed (station closure) or skipped_by_detour."""
        def run(f, snap):
            d = network.route_stops(f.conn, route, direction, pattern)
            if not d:
                return {"error": f"route {route} not found"}
            for p in d["patterns"]:
                for s in p["stops"]:
                    s["status"] = snap.disruptions.route_stop_status(stop_row(f.conn, s["number"]))
            return d
        return await _with_advisories(run)

    @mcp.tool
    def service_hours(route: str, date: str | None = None) -> dict:
        """Whether a route runs on a date (calendar plus holiday exceptions) and its first and last trip times.
        GTFS times past 24:00 belong to the previous service day."""
        day = dt.date.fromisoformat(date) if date else runtime.now().date()
        return _guard(lambda f: {"route": route, "date": day.isoformat(), **schedule.service_hours(f.conn, route, day)})

    @mcp.tool
    def scheduled_departures(stop: str, route: str | None = None, at: str | None = None, limit: int = 10) -> dict:
        """Timetable departures at a stop from a given time (default now), including previous-service-day trips that run past midnight.
        Schedule only; use arrivals for real time."""
        when = dt.datetime.fromisoformat(at) if at else runtime.now()
        if when.tzinfo is None:
            when = when.replace(tzinfo=schedule.TZ)

        def run(f):
            row = stop_row(f.conn, stop)
            if row is None:
                return {"error": f"stop {stop} not found"}
            return {"stop": stop, "from": when.isoformat(timespec="seconds"), "basis": "schedule",
                    "departures": schedule.departures_at(f.conn, row["stop_id"], when, limit=limit, route=route)}
        return _guard(run)
