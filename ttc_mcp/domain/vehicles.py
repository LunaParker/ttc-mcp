from __future__ import annotations

from ttc_mcp.adapters.bustime_rt import Vehicle, VehicleFeed
from ttc_mcp.domain.crowding import crowding
from ttc_mcp.domain.freshness import iso
from ttc_mcp.static.store import TripLookup, single_feed


def is_rail(conn, route: str) -> bool:
    row = conn.execute("SELECT mode FROM routes WHERE route_id=?", (route,)).fetchone()
    return bool(row) and row["mode"] in ("subway", "lrt")


def _row(conn, v: Vehicle, lookup: TripLookup) -> dict:
    found = lookup(v.trip_id) if v.trip_id else None
    trip = found[1] if found else None
    stop = conn.execute("SELECT stop_code FROM crosswalk WHERE surface_stop_id=?", (v.surface_stop_id,)).fetchone() if v.surface_stop_id else None
    return {"vehicle_id": v.vehicle_id, "route": v.route, "trip_id": v.trip_id,
            "headsign": trip["headsign"] if trip else None, "direction_id": str(trip["direction_id"]) if trip else None,
            "lat": v.lat, "lon": v.lon, "bearing": v.bearing, "speed_mps": v.speed_mps,
            "current_stop": stop["stop_code"] if stop else None, "current_status": v.status,
            "updated_at": iso(v.timestamp) if v.timestamp else None, "crowding": crowding(v.occupancy)}


def vehicles_on(conn, feed: VehicleFeed, *, route: str | None = None, vehicle_id: str | None = None,
                lookup: TripLookup | None = None) -> list[dict]:
    """Rows for a route or one vehicle; trips resolve through lookup (every loaded feed) or conn alone."""
    look = lookup or single_feed(conn)
    if vehicle_id is not None:
        v = feed.vehicles.get(vehicle_id)
        return [_row(conn, v, look)] if v else []
    return [_row(conn, feed.vehicles[vid], look) for vid in feed.by_route.get(route or "", [])]
