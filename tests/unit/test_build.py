import json
import zipfile

import pytest

from tests.conftest import FIX
from ttc_mcp.static.build import build_sqlite


def test_build_reports_feed_metadata_and_writes_atomically(tmp_path):
    out = tmp_path / "s.sqlite"
    meta = build_sqlite(FIX / "gtfs-mini.zip", out, FIX / "surface-mini.zip")
    assert meta.feed_version == "S1000538" and meta.start == "20260906" and meta.end == "20261031"
    assert out.exists() and not out.with_suffix(".tmp").exists() and meta.bytes == out.stat().st_size
    assert meta.trips > 0 and meta.departures > meta.trips


def test_stops_keep_hierarchy_and_flags(conn):
    s = conn.execute("SELECT * FROM stops WHERE stop_id='13863'").fetchone()
    assert s["name"] == "Bloor Station - Northbound Platform" and s["parent_station"] == "14572" and s["wheelchair"] == 1
    assert conn.execute("SELECT location_type FROM stops WHERE stop_id='14572'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM stops WHERE parent_station='14572' AND location_type=2").fetchone()[0] > 0


def test_orphan_line5_platform_is_attached_to_kennedy_by_name(conn):
    row = conn.execute("SELECT parent_station FROM stops WHERE stop_id='16081'").fetchone()
    parent = conn.execute("SELECT name FROM stops WHERE stop_id=?", (row[0],)).fetchone()
    assert parent["name"] == "Kennedy"


def test_routes_carry_series_mode_and_start_histogram(conn):
    r = {x["route_id"]: x for x in conn.execute("SELECT * FROM routes")}
    assert r["300"]["series"] == "blue-night" and r["5"]["mode"] == "lrt" and r["504"]["mode"] == "streetcar"
    hist = json.loads(r["300"]["start_hist"])
    assert len(hist) == 48 and sum(hist) > 0


def test_patterns_keep_stop_sequence_pairs_and_rank_by_trip_count(conn):
    pats = conn.execute("SELECT * FROM patterns WHERE route_id='504' AND direction_id=0 ORDER BY rank").fetchall()
    assert pats[0]["rank"] == 1 and pats[0]["trip_count"] >= pats[1]["trip_count"]
    stops = json.loads(pats[0]["stops"])
    assert all(isinstance(seq, int) and isinstance(sid, str) for seq, sid in stops)
    assert len(stops) == 36 and "East - 504B King towards Broadview Station" in json.loads(pats[0]["headsigns"])
    run = json.loads(pats[0]["run_times"])
    assert run[0] == 0 and len(run) == 36 and run[-1] > run[1] > 0


def test_departures_are_indexed_by_stop_and_time(conn):
    rows = conn.execute("SELECT trip_id, dep_secs, stop_sequence FROM departures WHERE stop_id=15461 ORDER BY dep_secs LIMIT 3").fetchall()
    assert rows and rows[0]["dep_secs"] < rows[-1]["dep_secs"]
    t = conn.execute("SELECT route_id, headsign FROM trips WHERE trip_id=?", (str(rows[0]["trip_id"]),)).fetchone()
    assert t["route_id"] in ("504", "121") and t["headsign"]


def test_stop_routes_lists_direction_and_headsign_per_stop(conn):
    served = {(r["route_id"], r["direction_id"], r["headsign"]) for r in conn.execute("SELECT * FROM stop_routes WHERE stop_id='15461'")}
    assert ("504", 0, "East - 504A King towards Distillery") in served


def test_crosswalk_maps_public_code_to_surface_id(conn):
    assert conn.execute("SELECT surface_stop_id FROM crosswalk WHERE stop_code='15461'").fetchone()[0] == "4282"


def test_calendar_bitmask_and_exceptions(conn):
    c = {r["service_id"]: r for r in conn.execute("SELECT * FROM calendar")}
    assert c["1"]["days"] == 0b0011111 and c["3"]["days"] == 0b1000000 and c["4401"]["days"] == 0b0001101
    assert conn.execute("SELECT exception_type FROM calendar_dates WHERE service_id='4' AND date='20260907'").fetchone()[0] == 1


def test_build_rejects_non_numeric_ids(tmp_path):
    src = zipfile.ZipFile(FIX / "gtfs-mini.zip")
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zout:
        for n in src.namelist():
            data = src.read(n)
            if n == "stops.txt":
                data = data.replace(b"\n15461,", b"\nX15461,", 1)
            zout.writestr(n, data)
    with pytest.raises(ValueError):
        build_sqlite(bad, tmp_path / "o.sqlite")


def test_feed_info_records_the_schema_version(conn):
    from ttc_mcp.static.schema import SCHEMA_VERSION

    assert conn.execute("SELECT schema_version FROM feed_info").fetchone()[0] == SCHEMA_VERSION


def test_raptor_groups_cover_every_trip_once_with_sorted_columns(conn):
    from ttc_mcp.static.timetable import unpack

    rows = conn.execute("SELECT pattern_id, split, service_id, n_trips, n_stops, times, trip_ids FROM raptor_groups").fetchall()
    assert sum(r["n_trips"] for r in rows) == conn.execute("SELECT count(*) FROM trips").fetchone()[0] and all(r["split"] == 0 for r in rows)
    pid, sid = rows[0]["pattern_id"], rows[0]["service_id"]
    cols, ids = unpack(rows[0]["n_stops"], rows[0]["times"], rows[0]["trip_ids"])
    assert len(cols) == conn.execute("SELECT json_array_length(stops) FROM patterns WHERE pattern_id=?", (pid,)).fetchone()[0]
    assert all(list(c) == sorted(c) for c in cols) and len(ids) == rows[0]["n_trips"]
    assert {str(t) for t in ids} == {r[0] for r in conn.execute("SELECT trip_id FROM trips WHERE pattern_id=? AND service_id=?", (pid, sid))}
    first = conn.execute("SELECT dep_secs FROM departures WHERE trip_id=? ORDER BY stop_sequence LIMIT 1", (ids[0],)).fetchone()[0]
    assert cols[0][0] == first


def test_build_logs_the_index_checks_and_reports_them(tmp_path):
    from ttc_mcp.static.build import build_sqlite

    lines = []
    meta = build_sqlite(FIX / "gtfs-mini.zip", tmp_path / "s.sqlite", FIX / "surface-mini.zip", log=lines.append)
    assert "[check] patterns split for overtaking: 0" in lines and "[check] stop_times with arrival != departure: 0" in lines
    assert (meta.splits, meta.unequal_times) == (0, 0)
