from tests.rtfeeds import make_vehicle_feed
from ttc_mcp.adapters.bustime_rt import parse_vehicles
from ttc_mcp.domain import vehicles

TS = 1791218028


def feed():
    return parse_vehicles(make_vehicle_feed(TS, [
        {"id": "4617", "route": "504", "trip": "100125020", "lat": 43.65, "lon": -79.36, "bearing": 241, "speed": 0.0,
         "stop_id": "4282", "seq": 12, "status": "INCOMING_AT", "occupancy": "FEW_SEATS_AVAILABLE", "ts": TS - 16},
        {"id": "8520", "route": "121", "trip": "x", "lat": 43.6, "lon": -79.3, "occupancy": "EMPTY"},
        {"id": "9999", "lat": 43.7, "lon": -79.4}]), {}).value


def test_vehicles_on_route_resolve_stop_and_headsign_from_the_static_store(conn):
    rows = vehicles.vehicles_on(conn, feed(), route="504")
    assert rows == [{"vehicle_id": "4617", "route": "504", "trip_id": "100125020", "headsign": "West - 504A King towards Dundas West Station",
                     "direction_id": "1", "lat": 43.65, "lon": -79.36, "bearing": 241.0, "speed_mps": 0.0, "current_stop": "15461",
                     "current_status": "INCOMING_AT", "updated_at": "2026-10-05T12:33:32-04:00",
                     "crowding": {"label": "Busy", "gtfs": "FEW_SEATS_AVAILABLE", "confidence": "high"}}]


def test_vehicle_by_id_with_unknown_trip_and_no_stop(conn):
    row = vehicles.vehicles_on(conn, feed(), vehicle_id="8520")[0]
    assert (row["headsign"], row["direction_id"], row["current_stop"], row["current_status"], row["crowding"]["label"]) == (None, None, None, None, "Not busy")
    assert vehicles.vehicles_on(conn, feed(), vehicle_id="nope") == [] and vehicles.vehicles_on(conn, feed(), route="7") == []


def test_is_rail(conn):
    assert vehicles.is_rail(conn, "1") and vehicles.is_rail(conn, "5") and not vehicles.is_rail(conn, "504") and not vehicles.is_rail(conn, "999")
