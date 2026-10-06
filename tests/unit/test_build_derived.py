import json
from array import array

from ttc_mcp.domain.geo import decode_polyline


def test_shapes_table_holds_encoded_polylines_with_lengths_and_distances(conn):
    r = conn.execute("SELECT * FROM shapes WHERE shape_id='shp-504-04'").fetchone()
    assert r["points"] == 539 and 9400 < r["length_m"] < 9600
    pts = decode_polyline(r["polyline"])
    assert len(pts) == 539 and abs(pts[0][0] - 43.634401) < 1e-5 and abs(pts[0][1] + 79.425872) < 1e-5
    dists = array("f")
    dists.frombytes(r["dists"])
    assert len(dists) == 539 and dists[0] == 0 and abs(dists[-1] - r["length_m"]) < 50 and all(dists[i] <= dists[i + 1] for i in range(538))
    assert conn.execute("SELECT COUNT(*) FROM shapes").fetchone()[0] == 98


def test_patterns_carry_median_stop_distances_aligned_with_stops(conn):
    p = conn.execute("SELECT stops, dists, run_times FROM patterns WHERE pattern_id=6").fetchone()
    stops, dists, run = json.loads(p["stops"]), json.loads(p["dists"]), json.loads(p["run_times"])
    assert len(dists) == len(stops) == len(run) == 38 and dists[0] == 0
    assert abs(dists[1] - 623.66) < 1 and abs(dists[2] - 869.26) < 1 and abs(dists[-1] - 10411.23) < 1


def test_pattern_stops_index_mirrors_patterns(conn):
    rows = conn.execute("SELECT pos, stop_id, secs, dist_m FROM pattern_stops WHERE pattern_id=6 ORDER BY pos").fetchall()
    assert [(r["pos"], r["stop_id"]) for r in rows[:3]] == [(0, "14665"), (1, "4569"), (2, "15039")]
    assert rows[0]["secs"] == 0 and rows[-1]["secs"] == 3540 and abs(rows[2]["dist_m"] - 869.26) < 1
    hits = conn.execute("SELECT pattern_id, pos FROM pattern_stops WHERE stop_id='15461' ORDER BY pattern_id").fetchall()
    assert {r["pattern_id"] for r in hits} == {6, 9, 22}
    assert conn.execute("SELECT COUNT(*) FROM pattern_stops").fetchone()[0] == conn.execute("SELECT SUM(json_array_length(stops)) FROM patterns").fetchone()[0]


def test_pathways_and_levels_are_loaded(conn):
    assert conn.execute("SELECT COUNT(*) FROM pathways").fetchone()[0] == 2682 and conn.execute("SELECT COUNT(*) FROM levels").fetchone()[0] == 160
    p = conn.execute("SELECT * FROM pathways WHERE pathway_id='201241'").fetchone()
    assert (p["from_stop"], p["to_stop"], p["mode"], p["bidirectional"], p["traversal_s"]) == ("101300", "101277", 5, 1, 20)
    assert conn.execute("SELECT name, level_index FROM levels WHERE level_id='300159'").fetchone()[:2] == ("Concourse Level", -1.0)


def test_units_are_parsed_from_node_names(conn):
    q = conn.execute("SELECT node_id, kind, code, level_name, level_index FROM units WHERE station_id='14587' AND code='9Y1L' ORDER BY node_id").fetchall()
    assert [(r["node_id"], r["kind"], r["level_name"]) for r in q] == [("101277", "elevator", "Concourse Level"), ("101300", "elevator", "Platform Level"), ("101303", "elevator", "Upper Platform Level")]
    assert q[0]["level_index"] == -1.0
    assert conn.execute("SELECT COUNT(*) FROM units WHERE station_id='14572' AND kind='elevator' AND code IS NOT NULL").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM units WHERE station_id='14572' AND kind='elevator' AND code IS NULL").fetchone()[0] >= 3
    assert conn.execute("SELECT COUNT(*) FROM units WHERE code IS NOT NULL").fetchone()[0] == 862 and conn.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 1476
    assert {r[0] for r in conn.execute("SELECT DISTINCT kind FROM units")} <= {"elevator", "escalator", "stair", "other"}


def test_station_members_cover_parent_suffix_and_nearby_stops(conn):
    rows = {r["stop_id"]: (r["how"], r["dist_m"]) for r in conn.execute("SELECT * FROM station_members WHERE station_id='15772'")}
    assert rows["13815"] == ("parent", 0) and rows["10935"] == ("parent", 0)
    assert rows["16754"][0] == "suffix" and rows["246"][0] == "suffix" and 0 < rows["16754"][1] <= 300
    assert all(h in ("parent", "suffix", "near") and (h == "parent") == (d == 0) for h, d in rows.values())
    king = {r["stop_id"]: r["how"] for r in conn.execute("SELECT * FROM station_members WHERE station_id='14586'")}
    assert king["13813"] == "parent" and king["15637"] == "suffix" and king["15638"] == "suffix"
    assert conn.execute("SELECT COUNT(*) FROM station_members WHERE how='near'").fetchone()[0] > 0


def test_transfers_hold_both_directions_within_150_m(conn):
    assert conn.execute("SELECT dist_m FROM transfers WHERE stop_a='15460' AND stop_b='15461'").fetchone()[0] == 42
    assert conn.execute("SELECT dist_m FROM transfers WHERE stop_a='15461' AND stop_b='15460'").fetchone()[0] == 42
    assert conn.execute("SELECT MAX(dist_m) FROM transfers").fetchone()[0] <= 150
    assert conn.execute("SELECT COUNT(*) FROM transfers WHERE stop_a=stop_b").fetchone()[0] == 0
