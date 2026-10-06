from __future__ import annotations

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


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def route_shape(route: str, direction: Literal["0", "1"], pattern: int | None = None, format: Literal["polyline", "geojson"] = "polyline",
                    simplify_m: float | None = None, from_stop: str | None = None, to_stop: str | None = None) -> dict:
        """The geometry of a route and direction: the most-used stop pattern's shape (or another pattern by rank) as an encoded
        polyline or GeoJSON LineString, with its stops and their distance along the shape. Optional Douglas-Peucker simplification
        (metres) and a from_stop/to_stop span sliced by shape_dist_traveled (the first visit of each when a stop repeats)."""
        def run(f):
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
        a node are reported with node_match false), uncoded node counts and a step-free verdict with its basis."""
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
    async def connections(origin: str | dict, destination: str | dict, max_transfers: int = 1, walk_m: int = 400,
                          depart_at: str | None = None, limit: int = 4) -> dict:
        """Trip options between two places on the TTC: direct and one-transfer rides over the scheduled stop patterns with typical
        minutes, walking legs and a transfer penalty, ranked by transfers then time. origin and destination are a stop number, a
        station id or alias, or {"lat", "lon"} from a geocoder. The first ride carries live next departures (basis realtime or
        schedule); closures cut rail legs into ride, shuttle, ride; detour-skipped stops are never used; every option lists the
        disruptions it touches. Typical minutes come from the timetable, not from live data."""
        async def run(feed):
            conn, rt = feed.conn, runtime.get_realtime()
            now = dt.datetime.fromisoformat(depart_at) if depart_at else runtime.now()
            if now.tzinfo is None:
                now = now.replace(tzinfo=TZ)
            snap = await advisory.snapshot(feed)
            o, d = planner.resolve_endpoint(conn, origin, walk_m=walk_m), planner.resolve_endpoint(conn, destination, walk_m=walk_m)
            if o is None or d is None:
                return {"error": f"could not resolve {'origin' if o is None else 'destination'}: give a stop number, station name or {{lat, lon}}"}, [], ""
            if o.stops.keys() & d.stops.keys():
                return {"from": o.label, "to": d.label, "depart_at": now.isoformat(timespec="minutes"), "options": [],
                        "note": "origin and destination are within walking distance of the same stop; no ride is needed"}, [], ""
            # The snapshot is evaluated at the clock; a given depart_at re-evaluates its alerts at that time.
            disr = Disruptions.build(conn, snap.alerts, snap.rt, snap.detours, now) if depart_at else snap.disruptions
            opts = planner.search(conn, o, d, now=now, max_transfers=max_transfers, limit=limit * 3,
                                  usable=lambda sid: disr.stop_skipped(sid) is None)
            opts = planner.overlay(conn, disr, opts)[:limit]
            hits = []
            for opt in opts:
                first = next((l for l in opt["legs"] if l["mode"] == "ride"), None)
                if first is None:
                    continue
                rows, h, _, _ = await live.arrivals_at(feed, rt, stop_row(conn, first["board"]["number"]), route=first["route"], limit=3, now=now)
                hits += h
                first["next"] = [{k: r.get(k) for k in ("eta_min", "vehicle_id", "crowding", "basis", "scheduled_at")} for r in rows[:3]]
                opt["basis"] = rows[0]["basis"] if rows else "schedule"
            for i, opt in enumerate(opts, 1):
                opt["rank"] = i
            return {"from": o.label, "to": d.label, "depart_at": now.isoformat(timespec="minutes"), "options": opts}, hits, \
                "static patterns + bustime getStopPredictions.jsp + ntas", snap
        return await _aguard(run)
