import pytest
from google.protobuf.message import DecodeError

from tests.rtfeeds import make_trip_feed, make_vehicle_feed
from ttc_mcp.adapters.bustime_rt import Vehicle, parse_trips, parse_vehicles

TS = 1791218028


def test_parses_vehicles_by_id_and_route():
    body = make_vehicle_feed(TS, [
        {"id": "4617", "route": "504", "trip": "100125020", "lat": 43.65, "lon": -79.36, "bearing": 241, "speed": 2.2,
         "stop_id": "4282", "seq": 12, "status": "INCOMING_AT", "occupancy": "FEW_SEATS_AVAILABLE", "ts": TS - 16},
        {"id": "9999", "lat": 43.7, "lon": -79.4}])
    p = parse_vehicles(body, {})
    feed = p.value
    assert (p.feed_timestamp, p.entities, feed.timestamp) == (TS, 2, TS)
    assert feed.vehicles["4617"] == Vehicle("4617", "504", "100125020", 43.65, -79.36, 241.0, 2.2, "4282", 12,
                                            "INCOMING_AT", "FEW_SEATS_AVAILABLE", TS - 16)
    assert feed.by_route == {"504": ["4617"]}
    idle = feed.vehicles["9999"]
    assert (idle.route, idle.trip_id, idle.occupancy, idle.status, idle.bearing, idle.speed_mps, idle.surface_stop_id) == (
        None, None, None, None, None, None, None)


def test_explicit_empty_is_kept_as_empty_not_missing():
    feed = parse_vehicles(make_vehicle_feed(TS, [{"id": "1", "route": "7", "lat": 43.0, "lon": -79.0, "occupancy": "EMPTY"}]), {}).value
    assert feed.vehicles["1"].occupancy == "EMPTY"


def test_garbage_raises_decode_error():
    with pytest.raises(DecodeError):
        parse_vehicles(b"\xff\xff\xff not a feed", {})


def test_parses_trip_feed_vehicle_and_route_maps():
    body = make_trip_feed(TS, [{"trip": "100677020", "route": "504", "vehicle": "4617", "stops": [("4282", 12, TS + 300)]},
                              {"trip": "-5", "route": "7", "relationship": "ADDED"}, {"trip": "x", "route": "121", "vehicle": "8520"}])
    p = parse_trips(body, {})
    assert (p.feed_timestamp, p.entities, p.value.timestamp) == (TS, 3, TS)
    assert p.value.vehicle_trip == {"4617": "100677020", "8520": "x"}
    assert p.value.trip_route == {"100677020": "504", "-5": "7", "x": "121"}
