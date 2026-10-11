import datetime as dt
import json
from pathlib import Path

import pytest

from ttc_mcp.adapters.ttc_alerts import classify, local_time, parse_alerts
from ttc_mcp.static.schedule import TZ

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "advisories"


def load():
    return parse_alerts((FIX / "alerts.json").read_bytes(), {})


def test_local_time_treats_the_z_suffix_as_toronto_and_year_one_as_open():
    assert local_time("2026-10-05T23:00:00Z") == dt.datetime(2026, 10, 5, 23, 0, tzinfo=TZ)
    assert local_time("2026-10-05T13:42:32.99Z") == dt.datetime(2026, 10, 5, 13, 42, 32, 990000, tzinfo=TZ)
    assert local_time("0001-01-01T00:00:00Z") is None and local_time(None) is None and local_time("garbage") is None


def test_parses_six_alerts_with_kinds():
    p = load()
    assert p.entities == 6 and p.feed_timestamp is None
    assert p.value.last_updated == dt.datetime(2026, 10, 5, 13, 42, 32, 990000, tzinfo=TZ)
    assert {a.id: a.kind for a in p.value.alerts} == {"77519": "closure", "77545": "suspension", "77530": "detour", "77535": "rsz",
                                                       "77513": "elevator", "76445": "escalator"}


def test_closure_carries_stations_windows_and_shuttle():
    c = next(a for a in load().value.alerts if a.id == "77519")
    assert (c.routes, c.route_type, c.alert_type, c.direction, c.shuttle) == (("1",), "Subway", "Planned", "Both ways", "Will Operate")
    assert c.stop_ids == ("7671", "2811", "7462") and c.station_names == ("Eglinton", "Lawrence", "York Mills")
    assert (c.stop_start, c.stop_end) == ("York Mills", "Eglinton")
    assert c.windows[0] == (dt.datetime(2026, 10, 5, 23, 0, tzinfo=TZ), dt.datetime(2026, 10, 6, 2, 30, tzinfo=TZ)) and len(c.windows) == 3
    assert c.end == dt.datetime(2026, 10, 7, 23, 0, tzinfo=TZ) and c.header.startswith("Line 1 Yonge-University:") and c.url.endswith(".png")


def test_rsz_fields_suspension_stops_and_unit_codes():
    by = {a.id: a for a in load().value.alerts}
    assert by["77535"].rsz == {"reduced_speed_kmh": "25", "length_m": "161", "track_percent": "8", "average_speed_kmh": "56",
                               "distance_m": "1957", "target_removal": "TBD"}
    assert by["77535"].direction == "Southbound From Finch" and by["77535"].stop_ids == ("7462", "7129") and by["77535"].windows == ()
    assert by["77545"].end is None and len(by["77545"].stop_ids) == 34 and by["77545"].cause_desc == "Collision"
    assert (by["77513"].unit_code, by["77513"].stop_ids, by["77513"].routes[:2], by["77513"].rsz) == ("1Y1L", ("1581",), ("1", "2"), None)
    assert by["76445"].unit_code == "11D3E" and by["76445"].end == dt.datetime(2026, 12, 31, 23, 59, tzinfo=TZ)
    assert by["77530"].stop_ids == () and by["77530"].kind == "detour" and by["77530"].stop_start == "University Ave at Gerrard St West"


def test_classify_never_trusts_effect_alone():
    assert classify("Subway", "REDUCED_SERVICE", "Subway Closure - Early Closure", "x") == "closure"
    assert classify("Subway", "NO_SERVICE", "No Service", "Line 2: There is no subway service between Kipling and Jane") == "closure"
    assert classify("Streetcar", "NO_SERVICE", "No Service", "512 St Clair: No service between ...") == "suspension"
    assert classify("Bus", "NO_SERVICE", "No Service", "47 Lansdowne: No service between ...", ("47",)) == "suspension"
    # Lines 5 and 6 are rail whatever label alerts.ttc.ca picks (spec 6.8).
    assert classify("Light Rail", "NO_SERVICE", "No Service", "Line 5 Eglinton: No service between ...", ("5",)) == "closure"
    assert classify("LRT", "REDUCED_SERVICE", "Subway Closure - Early Closure", "x", ("6",)) == "closure"
    assert classify("Bus", "NO_SERVICE", "No Service", "x", ("5",)) == "closure"
    assert classify("Bus", "SIGNIFICANT_DELAYS", "Delay", "...") == "other"
    assert classify("Subway", "SIGNIFICANT_DELAYS", "Reduced Speed Zone", "...") == "rsz"
    assert classify("Elevator", "ACCESSIBILITY_ISSUE", "Out of service", "...") == "elevator"
    assert classify("Bus", "DETOUR", "Detour", "...") == "detour"


def test_unexpected_document_raises():
    with pytest.raises(ValueError):
        parse_alerts(b'{"status": "error"}', {})


def test_a_line_5_closure_classifies_as_a_closure_whatever_its_route_type():
    doc = json.loads((FIX / "alerts.json").read_bytes())
    line5 = {**next(x for x in doc["routes"] if str(x["id"]) == "77519"), "id": 90519, "route": "5", "routeType": "Light Rail",
             "effect": "NO_SERVICE", "effectDesc": "No Service", "headerText": "Line 5 Eglinton: There is no service between Chaplin and Avenue stations."}
    bus = {**line5, "id": 90520, "route": "32", "routeType": "Bus", "headerText": "32 Eglinton West: No service between ..."}
    by = {a.id: a for a in parse_alerts(json.dumps({**doc, "routes": [line5, bus]}).encode(), {}).value.alerts}
    assert (by["90519"].kind, by["90519"].routes, by["90519"].route_type) == ("closure", ("5",), "Light Rail") and by["90520"].kind == "suspension"
