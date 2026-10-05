import pytest
from google.protobuf.message import DecodeError

from tests.rtfeeds import make_alert_feed
from ttc_mcp.adapters.gtfsrt_alerts import parse_rt_alerts

TS = 1791222554


def feed():
    return make_alert_feed(TS, [
        {"id": "77537", "effect": "NO_SERVICE", "cause": "OTHER_CAUSE", "route_stops": [("1", "13791"), ("1", "13792"), ("1", "13793")],
         "start": 1791255600, "end": 1791268200, "url": "https://files.ttc.ca/x.png",
         "header": "Line 1 Yonge-University: There is no subway service between York Mills and Eglinton stations due to planned maintenance work. Shuttle buses are running."},
        {"id": "100", "effect": "MODIFIED_SERVICE", "routes": ["504"], "start": 1790000000, "end": 1793000000,
         "header": "504 King – Temporary route change due to track work", "description": "504 King streetcars will divert via Queen St.",
         "url": "https://www.ttc.ca/service-advisories/Service-Changes/504"},
        {"id": "77544", "effect": "ACCESSIBILITY_ISSUE", "stops": ["13852", "13853"], "start": 1791222141, "header": "Spadina: Elevator 2B2L out of service"},
        {"id": "00005", "effect": "NO_EFFECT", "routes": ["5"], "start": 1791194400, "end": 1793505599, "header": "Tell us about your experience riding on Line 5.", "url": "https://survey"}])


def test_parses_entities_with_routes_stops_periods_and_text():
    p = parse_rt_alerts(feed(), {})
    by = {a.id: a for a in p.value.alerts}
    assert (p.feed_timestamp, p.entities, p.value.timestamp) == (TS, 4, TS)
    assert by["77537"].effect == "NO_SERVICE" and by["77537"].routes == ("1",) and by["77537"].stops == ("13791", "13792", "13793")
    assert (by["77537"].start, by["77537"].end, by["77537"].cause) == (1791255600, 1791268200, "OTHER_CAUSE")
    assert by["100"].routes == ("504",) and by["100"].stops == () and by["100"].description.startswith("504 King streetcars") and by["100"].url.endswith("/504")
    assert by["77544"].end is None and by["77544"].url is None and by["77544"].stops == ("13852", "13853") and by["77544"].description == ""


def test_garbage_raises():
    with pytest.raises(DecodeError):
        parse_rt_alerts(b"\xff\xff nope", {})
