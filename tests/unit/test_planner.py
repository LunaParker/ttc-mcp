import datetime as dt

from ttc_mcp.domain import planner
from ttc_mcp.static.schedule import TZ

NOW = dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ)
NIGHT_EARLY = dt.datetime(2026, 10, 6, 2, 0, tzinfo=TZ)
CHERRY_FRONT = {"lat": 43.6527, "lon": -79.3581}


def test_endpoint_from_coordinates_lists_stops_within_walk(conn):
    e = planner.resolve_endpoint(conn, CHERRY_FRONT)
    assert e.label == CHERRY_FRONT and {"15461", "15460", "15510", "15246"} <= set(e.stops)
    assert e.stops["15461"] == 16 and e.stops["15246"] == 160 and max(e.stops.values()) <= 400
    assert set(planner.resolve_endpoint(conn, CHERRY_FRONT, walk_m=40).stops) == {"15461", "15460"}


def test_endpoint_from_stop_station_code_and_alias(conn):
    s = planner.resolve_endpoint(conn, "15461")
    assert s.label == {"stop": "15461", "name": "Cherry St at Front St East"} and s.stops == {"15461": 0}
    u = planner.resolve_endpoint(conn, "Union")
    assert u.label == {"station": "15772", "name": "Union"} and {"13815", "13816", "10935", "16754", "246"} <= set(u.stops)
    assert u.stops["13815"] == 0 and u.stops["16754"] > 0
    assert planner.resolve_endpoint(conn, "15772").label["station"] == "15772" and planner.resolve_endpoint(conn, "7462").label["name"] == "York Mills"
    assert planner.resolve_endpoint(conn, "Dundas").label["name"] == "TMU" and planner.resolve_endpoint(conn, "nowhere") is None


def test_pattern_indexes_and_transfer_neighbourhood(conn):
    at = planner.patterns_at(conn, ["15246", "15461"])
    assert {p for p, _, _ in at["15246"]} == {3, 37} and all(secs >= 0 for _, _, secs in at["15461"]) and planner.patterns_at(conn, []) == {}
    rows = planner.pattern_rows(conn, 3)
    assert rows[0][0] == 0 and any(s == "16754" for _, s, _ in rows) and rows[-1][2] > rows[0][2]
    m = planner.pattern_meta(conn, 3)
    assert (m["pattern_id"], m["route"], m["direction_id"], m["mode"]) == (3, "121", "1", "bus") and m["headsign"].startswith("West - 121")
    t = planner.transfer_stops(conn, "15637")
    assert t["15637"] == 0 and {"13813", "13814", "15638"} <= set(t) and t["13813"] == 0
    assert planner.transfer_stops(conn, "15460")["15461"] == 42 and planner.stop_names(conn, ["15461"]) == {"15461": "Cherry St at Front St East"}


def test_next_departure_respects_the_horizon(conn):
    d = planner.next_departure(conn, "15246", 3, NOW)
    assert d and d["route"] == "121" and d["pattern_id"] == 3 and 0 <= d["in_min"] <= 120
    pid = conn.execute("SELECT pattern_id FROM patterns WHERE route_id='300' AND direction_id=0 AND rank=1").fetchone()[0]
    first_stop = planner.pattern_rows(conn, pid)[0][1]
    assert planner.next_departure(conn, first_stop, pid, NOW) is None
    assert planner.next_departure(conn, first_stop, pid, NIGHT_EARLY)["route"] == "300"
