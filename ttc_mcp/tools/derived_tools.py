from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP

from ttc_mcp.domain import shapes
from ttc_mcp.tools.static_tools import _guard


def register(mcp: FastMCP) -> None:
    @mcp.tool
    def route_shape(route: str, direction: Literal["0", "1"], pattern: int | None = None, format: Literal["polyline", "geojson"] = "polyline",
                    simplify_m: float | None = None, from_stop: str | None = None, to_stop: str | None = None) -> dict:
        """The geometry of a route and direction: the most-used stop pattern's shape (or another pattern by rank) as an encoded
        polyline or GeoJSON LineString, with its stops and their distance along the shape. Optional Douglas-Peucker simplification
        (metres) and a from_stop/to_stop span sliced by shape_dist_traveled."""
        def run(f):
            between = (from_stop, to_stop) if from_stop and to_stop else None
            d = shapes.route_shape(f.conn, route, direction, pattern=pattern, fmt=format, simplify_m=simplify_m, between=between)
            return d if d else {"error": f"no pattern {pattern or 1} for route {route} direction {direction}" + (" or the span is not in order" if between else "")}
        return _guard(run)
