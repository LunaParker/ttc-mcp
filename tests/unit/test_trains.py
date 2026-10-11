import datetime as dt

from ttc_mcp.adapters.ntas import NextTrains
from ttc_mcp.domain import trains
from ttc_mcp.static.schedule import TZ

NOW = dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ)


def test_resolve_station_by_alias_id_and_platform(conn):
    station, platforms = trains.resolve(conn, "Bloor")
    assert station["stop_id"] == "14572" and [p["stop_id"] for p in platforms] == ["13863", "13864"]
    assert [p["stop_id"] for p in trains.resolve(conn, "14572")[1]] == ["13863", "13864"]
    station, platforms = trains.resolve(conn, "13864")
    assert station["name"] == "Bloor-Yonge" and [p["stop_id"] for p in platforms] == ["13864"]
    assert trains.resolve(conn, "Cedarvale")[1][0]["name"].startswith("Cedarvale Station")


def test_resolve_rejects_surface_stops_and_unknown_names(conn):
    assert trains.resolve(conn, "15461") is None and trains.resolve(conn, "nowhere") is None


def test_platform_entry_normal(conn):
    row = next(p for p in trains.rail_platforms(conn, "14572") if p["stop_id"] == "13863")
    e = trains.platform_entry(conn, row, [NextTrains("13863", "1", "1", "Northbound to Finch", [0, 4, 5])], None, NOW)
    assert e == {"platform": "13863", "name": "Bloor Station - Northbound Platform", "line": "1", "direction_id": "1",
                 "headsign": "Yonge-University Line towards Finch Station", "direction_text": "Northbound to Finch",
                 "next_min": [0, 4, 5], "basis": "realtime", "service": "normal", "source": "ntas"}


def test_platform_entry_no_prediction_falls_back_to_the_timetable(conn):
    row = next(p for p in trains.rail_platforms(conn, "14572") if p["stop_id"] == "13864")
    e = trains.platform_entry(conn, row, [NextTrains("13864", "1", "0", "Southbound to Vaughan", [])], None, NOW)
    assert (e["service"], e["basis"], e["next_min"], e["direction_text"], e["source"]) == ("no_prediction", "schedule", [], "Southbound to Vaughan", "ntas + static stop_times")
    assert len(e["scheduled_next_min"]) == 3 and all(isinstance(m, int) and m >= 0 for m in e["scheduled_next_min"])
    assert e["note"].startswith("NTAS returned no prediction")
    err = trains.platform_entry(conn, row, None, "HTTP 503", NOW)
    assert err["service"] == "no_prediction" and "unavailable (HTTP 503)" in err["note"] and err["direction_text"] is None


def test_suspended_entry_keeps_basis_and_what_ntas_said_but_no_minutes():
    live = {"platform": "13793", "next_min": [1, 4, 8], "basis": "realtime", "service": "normal", "source": "ntas"}
    fallback = {"platform": "13794", "next_min": [], "basis": "schedule", "service": "no_prediction", "scheduled_next_min": [2, 9], "note": "x"}
    closure = {"alert_id": "77519"}
    assert trains.suspended(live, closure) == {"platform": "13793", "next_min": [], "ntas_min": [1, 4, 8], "basis": "realtime", "service": "suspended",
                                                "source": "ntas", "disruption": closure, "note": trains.SUSPENDED_NOTE}
    out = trains.suspended(fallback, closure)
    assert (out["next_min"], out["ntas_min"], out["basis"], out["service"]) == ([], [], "schedule", "suspended") and "scheduled_next_min" not in out
    assert live["next_min"] == [1, 4, 8] and fallback["scheduled_next_min"] == [2, 9]
