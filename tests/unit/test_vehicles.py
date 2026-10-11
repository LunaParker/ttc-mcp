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


def test_a_vehicle_on_a_trip_held_by_the_other_loaded_feed_keeps_its_headsign(two_feeds):
    import datetime as dt

    from ttc_mcp import runtime

    today = dt.date(2026, 10, 6)
    active = runtime.feed_for(today)
    live = parse_vehicles(make_vehicle_feed(TS, [{"id": "4617", "route": "504", "trip": "100677020", "lat": 43.65, "lon": -79.36}]), {}).value
    assert active.version == "S1000542" and vehicles.vehicles_on(active.conn, live, route="504")[0]["headsign"] is None
    row = vehicles.vehicles_on(active.conn, live, route="504", lookup=runtime.trip_lookup(today))[0]
    assert (row["headsign"], row["direction_id"]) == ("East - 504A King towards Distillery", "0")
