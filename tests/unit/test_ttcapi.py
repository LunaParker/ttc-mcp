import datetime as dt
from pathlib import Path

import pytest

from ttc_mcp.adapters.ttcapi import parse_service_changes
from ttc_mcp.static.schedule import TZ

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "advisories"


def test_parses_service_changes_with_the_route_rules():
    p = parse_service_changes((FIX / "servicechanges.json").read_bytes(), {})
    a, b = p.value
    assert p.entities == 2 and a.routes == ("504", "304") and a.route_type == "Streetcar" and a.category == "Route change"
    assert a.title == "504 King – Temporary route change due to track work"
    assert a.start == dt.datetime(2026, 10, 1, 0, 0, tzinfo=TZ) and a.end == dt.datetime(2026, 10, 31, 4, 0, tzinfo=TZ)
    assert a.url.endswith("504-Temporary-route-change") and a.text.startswith("504 King streetcars")
    assert b.routes == ("101",) and b.text == "" and b.id == "f4d1a81b-0002"


def test_absent_service_changes_key_is_an_empty_list_not_an_error():
    p = parse_service_changes(b'{"lastUpdated": "x", "routeAlerts": [], "accessibilityAlerts": [], "stopAlerts": []}', {})
    assert p.value == [] and p.entities == 0


def test_non_object_raises():
    with pytest.raises(ValueError):
        parse_service_changes(b"[]", {})
