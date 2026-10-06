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
