import dataclasses
import datetime as dt
from pathlib import Path

from ttc_mcp.adapters.ttc_alerts import AlertList, parse_alerts
from ttc_mcp.domain import accessibility as acc
from ttc_mcp.domain.disruptions import Disruptions
from ttc_mcp.static.schedule import TZ

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "advisories"
NOW = dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ)


def alerts():
    return parse_alerts((FIX / "alerts.json").read_bytes(), {}).value


def disruptions(conn, alert_list=None):
    return Disruptions.build(conn, alert_list if alert_list is not None else alerts(), None, [], NOW)


def test_queen_units_group_nodes_by_code_with_levels_and_no_alert(conn):
    r = acc.for_station(conn, acc.resolve(conn, "Queen"), disruptions(conn))
    assert r["station"] == {"id": "14587", "name": "Queen"} and r["summary"] in ("accessible", "partially accessible", "not accessible", "unknown")
    u = next(x for x in r["units"] if x["code"] == "9Y1L")
    assert (u["kind"], u["nodes"], u["node_match"], u["status"], u["alert_id"]) == ("elevator", 3, True, "in_service", None)
    assert u["levels"] == ["Upper Platform Level", "Platform Level", "Concourse Level"]
    assert all(p["platform"] and "wheelchair" in p for p in r["platforms"]) and all("lat" in e for e in r["entrances"])


def test_st_george_reports_the_live_elevator_alert_without_a_node(conn):
    r = acc.for_station(conn, acc.resolve(conn, "St George"), disruptions(conn))
    u = next(x for x in r["units"] if x["code"] == "1Y1L")
    assert (u["node_match"], u["nodes"], u["levels"], u["status"], u["alert_id"]) == (False, 0, [], "out_of_service", "77513")
    assert u["text"].startswith("St George: Elevator 1Y1L")
    assert r["step_free"] == {"verdict": "no", "basis": "alerts", "basis_note": "a step-free path may remain through another elevator"}
    assert r["uncoded_nodes"]["elevator"] >= 1


def test_step_free_is_yes_only_with_coded_elevators_and_no_outage(conn):
    d = disruptions(conn, AlertList(None, None, []))
    queen = acc.for_station(conn, acc.resolve(conn, "Queen"), d)
    assert queen["step_free"]["verdict"] in ("yes", "unknown")
    if queen["uncoded_nodes"]["elevator"] == 0:
        assert queen["step_free"] == {"verdict": "yes", "basis": "nodes"}
    bloor = acc.for_station(conn, acc.resolve(conn, "Bloor-Yonge"), d)
    assert bloor["step_free"] == {"verdict": "unknown", "basis": "uncoded nodes"} and {u["code"] for u in bloor["units"] if u["kind"] == "elevator"} == {"3D2L", "3D1L"}
    closure = next(a for a in alerts().alerts if a.id == "77513")
    out = dataclasses.replace(closure, stop_ids=("14587",), unit_code="9Y1L", header="Queen: Elevator 9Y1L out of service")
    queen_out = acc.for_station(conn, acc.resolve(conn, "Queen"), disruptions(conn, AlertList(None, None, [out])))
    u = next(x for x in queen_out["units"] if x["code"] == "9Y1L")
    assert (u["status"], u["node_match"], u["alert_id"]) == ("out_of_service", True, "77513") and queen_out["step_free"]["verdict"] == "no"


def test_resolve_and_surface_stops(conn):
    assert acc.resolve(conn, "14587")["name"] == "Queen" and acc.resolve(conn, "7462")["name"] == "York Mills" and acc.resolve(conn, "Dundas")["name"] == "TMU"
    stop = acc.resolve(conn, "15461")
    assert stop["location_type"] == 0 and acc.for_stop(conn, stop) == {"stop": {"number": "15461", "name": "Cherry St at Front St East"}, "wheelchair": stop["wheelchair"],
                                                                        "boarding": {0: "unknown", 1: "accessible", 2: "not accessible"}[stop["wheelchair"]]}
    assert acc.resolve(conn, "nowhere") is None


def test_future_elevator_outage_is_upcoming_not_out_of_service(conn):
    closure = next(a for a in alerts().alerts if a.id == "77513")
    assert acc.for_station(conn, acc.resolve(conn, "St George"), disruptions(conn, AlertList(None, None, [closure])))["upcoming"] == []
    start, end = NOW + dt.timedelta(days=3), NOW + dt.timedelta(days=3, hours=6)
    future = dataclasses.replace(closure, start=start, end=end, windows=())
    r = acc.for_station(conn, acc.resolve(conn, "St George"), disruptions(conn, AlertList(None, None, [future])))
    assert not [u for u in r["units"] if u["code"] == "1Y1L"]
    period = [start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes")]
    assert r["upcoming"] == [{"alert_id": "77513", "kind": "elevator", "code": "1Y1L", "period": period, "text": future.header}]
    # St George's summary is partially accessible in the fixture (two unflagged platforms), which decides before its uncoded nodes.
    assert r["step_free"] == {"verdict": "no", "basis": "platforms"}


def test_a_digit_query_is_a_station_id_then_a_stop_before_any_name(conn, fixture_db, tmp_path):
    import shutil
    import sqlite3

    assert acc.resolve(conn, "43274")["name"] == "Eglinton" and acc.resolve(conn, "407")["name"] == "Highway 407"
    shutil.copy(fixture_db, tmp_path / "s.sqlite")
    c = sqlite3.connect(tmp_path / "s.sqlite")
    c.row_factory = sqlite3.Row
    c.execute("INSERT INTO stops (stop_id, stop_code, name, lat, lon, location_type) VALUES ('99407', '407', 'Test St at 407 Stop', 43.7, -79.4, 0)")
    row = acc.resolve(c, "407")
    assert (row["stop_id"], row["location_type"]) == ("99407", 0) and acc.resolve(c, "Highway 407")["stop_id"] == "15208"
    c.close()


def _station_copy(fixture_db, tmp_path, sql: str):
    import shutil
    import sqlite3

    shutil.copy(fixture_db, tmp_path / "s.sqlite")
    c = sqlite3.connect(tmp_path / "s.sqlite")
    c.row_factory = sqlite3.Row
    c.execute(sql)
    c.commit()
    return c


def test_step_free_yes_needs_an_accessible_summary_and_no_from_an_alert_notes_a_possible_other_path(conn, fixture_db, tmp_path):
    quiet = AlertList(None, None, [])
    queen = acc.for_station(conn, acc.resolve(conn, "Queen"), disruptions(conn, quiet))
    assert (queen["summary"], queen["step_free"]) == ("accessible", {"verdict": "yes", "basis": "nodes"})
    flagged = _station_copy(fixture_db, tmp_path, "UPDATE stops SET wheelchair=2 WHERE parent_station='14587' AND location_type=0")
    r = acc.for_station(flagged, acc.resolve(flagged, "Queen"), disruptions(flagged, quiet))
    assert (r["summary"], r["step_free"]) == ("not accessible", {"verdict": "no", "basis": "platforms"})
    assert next(u for u in r["units"] if u["code"] == "9Y1L")["status"] == "in_service"
    flagged.execute("UPDATE stops SET wheelchair=1 WHERE stop_id='13811'")
    r = acc.for_station(flagged, acc.resolve(flagged, "Queen"), disruptions(flagged, quiet))
    assert (r["summary"], r["step_free"]) == ("partially accessible", {"verdict": "no", "basis": "platforms"})
    flagged.execute("UPDATE stops SET wheelchair=0 WHERE parent_station='14587' AND location_type=0")
    r = acc.for_station(flagged, acc.resolve(flagged, "Queen"), disruptions(flagged, quiet))
    assert (r["summary"], r["step_free"]) == ("unknown", {"verdict": "unknown", "basis": "platforms unknown"})
    flagged.close()
    out = dataclasses.replace(next(a for a in alerts().alerts if a.id == "77513"), stop_ids=("14587",), unit_code="9Y1L")
    r = acc.for_station(conn, acc.resolve(conn, "Queen"), disruptions(conn, AlertList(None, None, [out])))
    assert r["step_free"] == {"verdict": "no", "basis": "alerts", "basis_note": "a step-free path may remain through another elevator"}
    st_george = acc.for_station(conn, acc.resolve(conn, "St George"), disruptions(conn))
    assert st_george["summary"] == "partially accessible" and st_george["step_free"]["basis"] == "alerts"
    assert acc.for_station(conn, acc.resolve(conn, "St George"), disruptions(conn, quiet))["step_free"] == {"verdict": "no", "basis": "platforms"}
    assert acc.for_station(conn, acc.resolve(conn, "King"), disruptions(conn, quiet))["step_free"] == {"verdict": "no", "basis": "platforms"}
