from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP

from ttc_mcp.domain import accessibility as acc, shapes
from ttc_mcp.domain.disruptions import station_row
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
                return {"accessibility": acc.for_station(f.conn, station_row(f.conn, row["parent_station"]), snap.disruptions), "queried_platform": row["stop_id"]}
            return {"accessibility": acc.for_stop(f.conn, row)}
        return await _with_advisories(run)
