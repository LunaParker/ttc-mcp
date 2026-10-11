import dataclasses
import shutil
import sqlite3

from tests.unit.test_disruptions import alerts
from ttc_mcp.adapters.ttc_alerts import AlertList
from ttc_mcp.static import checks


def _copy(fixture_db, tmp_path, *sql):
    path = tmp_path / "checks.sqlite"
    shutil.copy(fixture_db, path)
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    for s in sql:
        c.execute(s)
    c.commit()
    return c


def test_unmatched_suffixes_name_the_surface_stops_whose_station_suffix_attached_nowhere(conn, fixture_db, tmp_path):
    """Spec 8 item 9: a ' - X Station' suffix with no station within 300 m gets no alias, so the stop never joins a station."""
    assert checks.unmatched_suffixes(conn) == []
    c = _copy(fixture_db, tmp_path,
              "INSERT INTO stops (stop_id, stop_code, name, lat, lon, location_type) VALUES ('99001', '99001', 'Test Rd at Nowhere Ave - Nowhere Station', 44.5, -80.5, 0)",
              "INSERT INTO stops (stop_id, stop_code, name, lat, lon, location_type) VALUES ('99002', '99002', 'Test Rd at Far Ave - Far GO Station', 44.5, -80.5, 0)")
    assert checks.unmatched_suffixes(c) == [{"number": "99001", "name": "Test Rd at Nowhere Ave - Nowhere Station", "suffix": "Nowhere"}]


def test_unresolved_alert_stations_cover_rail_names_and_station_alert_ids_only(conn):
    """Names on rail alerts (span ends and the station list) and ids on station alerts must resolve; surface alerts are not stations."""
    by = {a.id: a for a in alerts().alerts}
    # Coxwell is a Line 2 station and the fixture has no Line 2, so the escalator alert's legacy station id resolves nowhere there.
    assert checks.unresolved_alert_stations(conn, alerts()) == [{"alert_id": "76445", "kind": "escalator", "value": "4917"}]
    bad_span = dataclasses.replace(by["77519"], stop_end="Nowhere")
    bad_list = dataclasses.replace(by["77535"], id="90001", station_names=("York Mills", "Nowhere Else"))
    bad_unit = dataclasses.replace(by["77513"], id="90002", stop_ids=("999999",))
    surface = dataclasses.replace(by["77545"], id="90003", stop_start="Nowhere at All")
    out = checks.unresolved_alert_stations(conn, AlertList(None, None, [bad_span, bad_list, bad_unit, surface]))
    assert out == [{"alert_id": "77519", "kind": "closure", "value": "Nowhere"},
                   {"alert_id": "90001", "kind": "rsz", "value": "Nowhere Else"},
                   {"alert_id": "90002", "kind": "elevator", "value": "999999"}]


def test_unit_code_coverage_matches_alert_codes_to_coded_nodes_of_the_same_kind(conn):
    """Spec 8 item 3: St George's elevator nodes are uncoded and Coxwell is not in the fixture, so neither fixture code matches."""
    cov = checks.unit_code_coverage(conn, alerts())
    assert cov == {"codes": 2, "matched": 0, "unmatched": [{"alert_id": "76445", "kind": "escalator", "code": "11D3E", "station": "Coxwell"},
                                                          {"alert_id": "77513", "kind": "elevator", "code": "1Y1L", "station": "St George"}]}
    coded = dataclasses.replace(next(a for a in alerts().alerts if a.id == "76445"), id="90004", unit_code="1Y1E", stop_ids=("43269",))
    cov = checks.unit_code_coverage(conn, AlertList(None, None, [coded]))
    assert (cov["codes"], cov["matched"], cov["unmatched"]) == (1, 1, [])


def test_run_checks_reports_every_section_and_logs_one_line_each(conn):
    lines = []
    rep = checks.run_checks(conn, alerts(), log=lines.append)
    assert set(rep) == {"suffixes", "alert_stations", "unit_codes"} and rep["suffixes"] == [] and [u["value"] for u in rep["alert_stations"]] == ["4917"]
    assert rep["unit_codes"]["codes"] == 2
    assert [l.split(":")[0] for l in lines] == ["[check] surface-stop suffixes with no station", "[check] alert stations unresolved", "[check] unit codes in alerts matching a coded node"]
    assert "0 of 2" in lines[2] and "11D3E (Coxwell)" in lines[2] and lines[1].endswith("1 (4917 [76445])")
    offline = checks.run_checks(conn, None, log=lines.append)
    assert offline["alert_stations"] is None and offline["unit_codes"] is None and lines[-1].endswith("alerts unavailable")
