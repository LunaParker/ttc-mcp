import datetime as dt

from tests.unit.test_detours import changes, rt
from ttc_mcp.domain.service_changes import merged


def test_merges_gtfsrt_first_then_ttcapi_without_title_duplicates():
    rows = merged(rt(), changes())
    assert [r["source"] for r in rows] == ["gtfsrt alerts", "ttcapi"] and rows[0]["routes"] == ["504"] and rows[1]["routes"] == ["101"]
    assert rows[0]["effective"][0].startswith("2026-09-2") and rows[0]["text"] == "divert via Queen" and rows[0]["route_type"] is None
    assert rows[1]["effective"] == ["2026-09-30T00:00:00-04:00", "2026-10-02T04:00:00-04:00"] and rows[1]["category"] == "Route change"


def test_filters_by_route_and_day():
    assert [r["id"] for r in merged(rt(), changes(), route="504")] == ["100"]
    assert [r["routes"] for r in merged(None, changes(), day=dt.date(2026, 10, 1))] == [["504", "304"], ["101"]]
    assert [r["routes"] for r in merged(None, changes(), day=dt.date(2026, 10, 20))] == [["504", "304"]]
    assert merged(None, None) == []
