import datetime as dt
from pathlib import Path

from tests.rtfeeds import make_vehicle_feed
from ttc_mcp.adapters.bustime_rt import parse_vehicles
from ttc_mcp.adapters.bustime_web import StopPredictions, parse_stop_predictions
from ttc_mcp.adapters.ntas import NextTrains
from ttc_mcp.domain import arrivals
from ttc_mcp.static.schedule import TZ

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "bustime"
NOW = dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ)


def preds():
    return parse_stop_predictions((FIX / "stop-15461.xml").read_bytes(), {}).value


def vehicles():
    return parse_vehicles(make_vehicle_feed(1, [{"id": "4617", "route": "504", "lat": 43.6, "lon": -79.4, "occupancy": "FEW_SEATS_AVAILABLE"}]), {}).value


def test_stop_row_by_number_or_code_and_rail_lines(conn):
    assert arrivals.stop_row(conn, "15461")["name"] == "Cherry St at Front St East"
    assert arrivals.stop_row(conn, "0") is None
    assert arrivals.rail_lines_at(conn, "13863") == ["1"] and arrivals.rail_lines_at(conn, "15461") == []


def test_match_direction_collapses_whitespace_then_falls_back_to_the_only_direction():
    served = {"504": [("0", "East - 504A King towards Distillery"), ("1", "West - 504 King towards Distillery")],
              "121": [("1", "West - 121 Esplanade-River"), ("1", "West - 121 Short Turn")]}
    assert arrivals.match_direction(served, "504", "East - 504A  King towards Distillery") == ("0", "East - 504A King towards Distillery")
    assert arrivals.match_direction(served, "504", "Somewhere  else") == (None, "Somewhere else")
    assert arrivals.match_direction(served, "121", "West - 121 Esplanade-River towards Union") == ("1", "West - 121 Esplanade-River towards Union")
    assert arrivals.match_direction(served, "999", "x") == (None, "x")


def test_surface_rows_join_crowding_by_vehicle_and_sort_by_eta(conn):
    rows = arrivals.surface_rows(conn, "15461", preds(), vehicles())
    assert [r["eta_min"] for r in rows] == [2, 14, 15, 28]
    first = rows[0]
    assert first["route"] == "504" and first["direction_id"] == "0" and first["headsign"] == "East - 504A King towards Distillery"
    assert first["vehicle_id"] == "4617" and first["basis"] == "realtime" and first["scheduled_only"] is False
    assert first["crowding"]["label"] == "Busy" and rows[1]["crowding"] == {"label": "No info", "gtfs": None, "confidence": "none"}
    assert arrivals.surface_rows(conn, "15461", preds(), None)[0]["crowding"]["label"] == "No info"
    assert arrivals.surface_rows(conn, "15461", preds(), vehicles(), route="999") == []


def test_surface_rows_put_unparseable_etas_last(conn):
    sp = StopPredictions("15461", "x", None, [
        preds().predictions[0].__class__("504", "East - 504A King towards Distillery", "DLY", None, "1", False),
        preds().predictions[1]])
    assert [r["eta_min"] for r in arrivals.surface_rows(conn, "15461", sp, None)] == [14, None]


def test_rail_rows_expand_minutes_with_the_platform_direction(conn):
    rows = arrivals.rail_rows(conn, "13863", [NextTrains("13863", "1", "1", "Northbound to Finch", [0, 4, 5])])
    assert [r["eta_min"] for r in rows] == [0, 4, 5]
    assert rows[0] == {"route": "1", "direction_id": "1", "headsign": "Yonge-University Line towards Finch Station",
                       "direction_text": "Northbound to Finch", "eta_min": 0, "vehicle_id": None, "basis": "realtime", "scheduled_only": False}
    assert arrivals.rail_rows(conn, "13863", [NextTrains("13863", "1", "1", "Northbound to Finch", [3])], route="2") == []


def test_schedule_rows_come_from_the_timetable_without_crowding(conn):
    rows = arrivals.schedule_rows(conn, "15461", NOW, limit=3, route="504")
    assert len(rows) == 3 and all(r["basis"] == "schedule" and r["scheduled_only"] and r["vehicle_id"] is None for r in rows)
    assert rows[0]["eta_min"] >= 0 and rows[0]["scheduled_at"] >= "2026-10-06T08:00:00-04:00" and "crowding" not in rows[0]
    assert rows[0]["route"] == "504" and rows[0]["trip_id"].isdigit()


def test_fallback_note_names_the_cause():
    assert "unavailable (HTTP 403)" in arrivals.fallback_note(None, "HTTP 403")
    assert "Stop not found" in arrivals.fallback_note(StopPredictions("1", "", "Stop not found", []), None)
    assert arrivals.fallback_note(StopPredictions("1", "", None, []), None).startswith("no real-time prediction")
