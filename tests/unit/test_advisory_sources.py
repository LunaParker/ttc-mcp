import json
from pathlib import Path

from google.transit import gtfs_realtime_pb2 as pb

from tests.rtfeeds import make_alert_feed, make_trip_feed
from ttc_mcp.domain.geo import decode_polyline, path_length_m
from ttc_mcp.sources import ALERTS, DETOURS, RT_ALERTS, SERVICE_CHANGES, SOURCES, TRIPS

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "advisories"


def test_advisory_sources_carry_spec_ttls():
    assert (ALERTS.ttl, ALERTS.max_stale, ALERTS.stale_after) == (25, 3600, 600)
    assert (RT_ALERTS.ttl, RT_ALERTS.max_stale, RT_ALERTS.stale_after) == (60, 3600, 600)
    assert (DETOURS.ttl, DETOURS.max_stale, DETOURS.stale_after) == (300, 3600, 1800)
    assert (SERVICE_CHANGES.ttl, SERVICE_CHANGES.max_stale, SERVICE_CHANGES.stale_after) == (600, 3600, 1800)
    assert (TRIPS.ttl, TRIPS.max_stale, TRIPS.stale_after) == (30, 600, 90)
    assert [s.key for s in SOURCES] == ["stop_xml", "vehicles", "ntas", "alerts", "rt_alerts", "detours", "service_changes", "trips", "subway_trips"]
    assert RT_ALERTS.url.endswith("?format=binary") and ALERTS.url == "https://alerts.ttc.ca/api/alerts/list"


def test_decode_polyline_matches_the_google_example_and_the_fixture_shape():
    pts = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert [tuple(round(x, 5) for x in p) for p in pts] == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    shape = next(e["shape"]["encodedPolyline"] for e in json.loads((FIX / "detour.json").read_text())["entity"] if "shape" in e)
    pts = decode_polyline(shape)
    assert len(pts) == 78 and pts[0] == (43.70556, -79.39947) and round(path_length_m(pts)) == 4577
    assert decode_polyline("") == [] and path_length_m([]) == 0


def test_alert_feed_builder_round_trips():
    body = make_alert_feed(100, [{"id": "a1", "effect": "NO_SERVICE", "cause": "MAINTENANCE", "route_stops": [("1", "13791")], "routes": ["1"],
                                  "stops": ["13792"], "start": 10, "end": 20, "header": "h", "description": "d", "url": "u"}])
    f = pb.FeedMessage()
    f.ParseFromString(body)
    a = f.entity[0].alert
    assert f.header.timestamp == 100 and f.entity[0].id == "a1" and a.effect == pb.Alert.NO_SERVICE and a.cause == pb.Alert.MAINTENANCE
    assert [(ie.route_id, ie.stop_id) for ie in a.informed_entity] == [("1", ""), ("1", "13791"), ("", "13792")]
    assert (a.active_period[0].start, a.active_period[0].end) == (10, 20)
    assert (a.header_text.translation[0].text, a.description_text.translation[0].text, a.url.translation[0].text) == ("h", "d", "u")


def test_trip_feed_builder_round_trips():
    body = make_trip_feed(100, [{"trip": "t1", "route": "504", "vehicle": "4617", "stops": [("4282", 12, 130)]}, {"trip": "-5", "relationship": "ADDED"}])
    f = pb.FeedMessage()
    f.ParseFromString(body)
    tu = f.entity[0].trip_update
    assert (tu.trip.trip_id, tu.trip.route_id, tu.vehicle.id, tu.timestamp) == ("t1", "504", "4617", 100)
    assert (tu.stop_time_update[0].stop_id, tu.stop_time_update[0].stop_sequence, tu.stop_time_update[0].arrival.time) == ("4282", 12, 130)
    assert f.entity[1].trip_update.trip.schedule_relationship == pb.TripDescriptor.ADDED
