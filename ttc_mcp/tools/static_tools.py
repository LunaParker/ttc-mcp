from __future__ import annotations

import datetime as dt
from typing import Literal

from fastmcp import FastMCP

from ttc_mcp import runtime
from ttc_mcp.domain import network
from ttc_mcp.static import schedule


def _guard(fn):
    """Run fn(feed) for today's feed; turn a missing store into a readable error payload."""
    try:
        feed = runtime.feed_for()
    except runtime.StoreUnavailable as e:
        return {"error": str(e), "meta": runtime.meta(None)}
    return {**fn(feed), "meta": runtime.meta(feed)}


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def find_stops(query: str, route: str | None = None, direction: Literal["0", "1"] | None = None,
                   mode: Literal["bus", "streetcar", "subway", "lrt"] | None = None, limit: int = 25) -> dict:
        """Find TTC stops, platforms or stations by name, stop number, station name or alias (e.g. "Dundas" resolves to TMU).
        Stops are directional: the two sides of an intersection are different stop numbers with different headsigns."""
        return _guard(lambda f: {"stops": network.find_stops(f.conn, query, route=route, direction=direction, mode=mode, limit=limit)})

    @mcp.tool
    def nearest_stops(lat: float, lon: float, radius_m: int = 500, limit: int = 10,
                      kind: Literal["any", "stop", "station"] = "any") -> dict:
        """Stops and stations nearest to a coordinate (geocode addresses with a maps tool first), with walk minutes and the routes, directions and headsigns served."""
        return _guard(lambda f: {"origin": {"lat": lat, "lon": lon},
                                 "results": network.nearest_stops(f.conn, lat, lon, radius_m=radius_m, limit=limit, kind=kind)})

    @mcp.tool
    def stop(number: str) -> dict:
        """One stop or platform by public stop number: name, coordinates, wheelchair flag, routes/directions/headsigns served, station membership."""
        def run(f):
            d = network.stop_detail(f.conn, number)
            return {"stop": d} if d else {"error": f"stop {number} not found"}
        return _guard(run)

    @mcp.tool
    def station(query: str) -> dict:
        """A subway or LRT station by id, name or alias: platforms with lines, entrances, accessibility summary."""
        def run(f):
            d = network.station_detail(f.conn, query)
            return {"station": d} if d else {"error": f"station {query!r} not found"}
        return _guard(run)

    @mcp.tool
    def routes(series: str | None = None, overnight: bool | None = None, mode: str | None = None) -> dict:
        """All TTC routes with mode (subway, lrt, streetcar, bus), series (rail, bus, express, blue-night, community, streetcar),
        service hours by day type and overnight share. overnight=true lists the Blue Night network."""
        return _guard(lambda f: {"routes": network.list_routes(f.conn, series=series, overnight=overnight, mode=mode)})

    @mcp.tool
    def route_stops(route: str, direction: Literal["0", "1"], pattern: int | None = None) -> dict:
        """Ordered stops for a route and direction, grouped by stop pattern (branches, short turns) with trip counts, headsigns
        and typical minutes from the first stop."""
        def run(f):
            d = network.route_stops(f.conn, route, direction, pattern)
            return d if d else {"error": f"route {route} not found"}
        return _guard(run)

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
        return _guard(lambda f: {"stop": stop, "from": when.isoformat(timespec="seconds"), "basis": "schedule",
                                 "departures": schedule.departures_at(f.conn, stop, when, limit=limit, route=route)})
