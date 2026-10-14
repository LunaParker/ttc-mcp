import sqlite3

from ttc_mcp.static import timetable
from ttc_mcp.static.schema import SCHEMA


def _graph():
    """Station S: platforms A, B, C, an unconnected bay D; nodes N1, N2, N3; fare gate G. Pathway columns: from, to, mode, bidirectional, length_m, traversal_s."""
    c = sqlite3.connect(":memory:")
    c.executescript(SCHEMA)
    c.executemany("INSERT INTO stops (stop_id, name, location_type, parent_station) VALUES (?,?,?,?)",
                  [("S", "S", 1, None), ("A", "A", 0, "S"), ("B", "B", 0, "S"), ("C", "C", 0, "S"), ("D", "D", 0, "S"),
                   ("N1", "n", 3, "S"), ("N2", "n", 3, "S"), ("N3", "n", 3, "S"), ("G", "gate", 3, "S")])
    c.executemany("INSERT INTO pathways VALUES (?,?,?,?,?,?,?,?)",
                  [("1", "A", "N1", 1, 1, 8.0, 10, None), ("2", "N1", "B", 1, 1, 20.0, None, None), ("3", "N1", "N2", 1, 1, None, None, None),
                   ("4", "N2", "C", 1, 1, 4.0, 5, None), ("5", "A", "G", 6, 1, 1.0, 1, None), ("6", "G", "B", 7, 1, 1.0, 1, None),
                   ("7", "C", "N3", 1, 0, 3.0, 4, None), ("8", "N3", "A", 1, 0, 3.0, 4, None)])
    return c


def test_edge_seconds_prefer_traversal_then_length_at_a_metre_a_second_then_thirty():
    assert (timetable.edge_seconds(8.0, 10), timetable.edge_seconds(20.0, None), timetable.edge_seconds(None, None)) == (10, 20.0, 30)


def test_station_paths_walk_the_graph_both_ways_skip_fare_gates_and_honour_one_way_edges():
    rows = {(a, b): s for a, b, s in timetable.station_paths(_graph())}
    assert rows == {("A", "B"): 30, ("B", "A"): 30, ("A", "C"): 45, ("C", "A"): 8, ("B", "C"): 55, ("C", "B"): 38}


def test_the_fixture_store_has_paths_between_both_platforms_at_bloor_yonge_and_king(conn):
    secs = {(a, b): s for a, b, s in conn.execute("SELECT stop_a, stop_b, secs FROM station_paths WHERE stop_a IN ('13863','13864','13813','13814')")}
    assert {("13863", "13864"), ("13864", "13863"), ("13813", "13814"), ("13814", "13813")} <= set(secs)
    assert all(20 <= s <= 600 for s in secs.values())
