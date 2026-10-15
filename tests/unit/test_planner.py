import datetime as dt

from tests.unit.test_disruptions import NOW_NIGHT, build
from tests.unit.test_raptor import query
from ttc_mcp.domain import geo, planner, raptor
from ttc_mcp.domain.disruptions import Disruptions
from ttc_mcp.static.schedule import TZ

NOW = dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ)
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


def test_pattern_rows_meta_and_stop_names(conn):
    rows = planner.pattern_rows(conn, 3)
    assert rows[0][0] == 0 and any(s == "16754" for _, s, _ in rows) and rows[-1][2] > rows[0][2]
    m = planner.pattern_meta(conn, 3)
    assert (m["pattern_id"], m["route"], m["direction_id"], m["mode"]) == (3, "121", "1", "bus") and m["headsign"].startswith("West - 121")
    assert planner.stop_names(conn, ["15461"]) == {"15461": "Cherry St at Front St East"}


def test_closed_for_turns_the_night_closure_into_spans_on_line_1_only(conn):
    closed = planner.closed_for(conn, build(conn, NOW_NIGHT))
    south, north = closed(54), closed(59)
    assert (south.no_board, south.no_alight, south.drop_after) == (frozenset({3, 4}), frozenset({4, 5}), frozenset({3}))
    assert (north.no_board, north.no_alight, north.drop_after) == (frozenset({32, 33}), frozenset({33, 34}), frozenset({32}))
    assert closed(3) == raptor.Closed.NONE and closed(-1) == raptor.Closed.NONE and closed(54) is south
    assert planner.closed_for(conn, build(conn, NOW))(54) == raptor.Closed.NONE


def test_materialize_writes_the_contract_legs_with_exact_and_typical_minutes(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    q = query(conn, tt, CHERRY_FRONT, "Union", NOW)
    two = next(j for j in raptor.plan(q) if j.transfers == 1)
    o = planner.materialize(conn, q, two, NOW.date())
    assert set(o) == {"transfers", "depart_at", "arrive_at", "duration_min", "walk_m", "stops", "basis", "estimate", "legs"}
    assert (o["transfers"], o["basis"], o["estimate"]) == (1, "schedule", False) and o["depart_at"] < o["arrive_at"]
    modes = [l["mode"] for l in o["legs"]]
    assert modes[:3] == ["walk", "ride", "transfer"] and modes.count("ride") == 2
    walk, ride, xfer = o["legs"][:3]
    assert set(walk) == {"mode", "to", "distance_m", "min", "depart", "arrive"} and walk["to"]["number"] == "4144"
    assert (ride["route"], ride["trip_id"], ride["board"]["number"], ride["alight"]["name"], ride["stops"]) == ("504", "107845020", "4144", "King St West at Yonge St West Side - King Station", 6)
    assert (ride["depart"], ride["arrive"], ride["min"], ride["basis"], ride["transit"]) == ("2026-10-06T08:06-04:00", "2026-10-06T08:16-04:00", 10, "schedule", "streetcar")
    assert isinstance(ride["typical_min"], int) and ride["pattern_id"] == 2 and (ride["board_pos"], ride["alight_pos"]) == (two.rides[0].board_pos, two.rides[0].alight_pos)
    assert (xfer["from"]["number"], xfer["to"]["number"], xfer["distance_m"], xfer["walk_min"], xfer["via"]) == ("15637", "13814", 98, 2, "walk")
    assert xfer["depart"] == ride["arrive"] and xfer["arrive"] == o["legs"][3]["depart"] and xfer["min"] >= 3
    assert o["stops"] == sum(l["stops"] for l in o["legs"] if l["mode"] == "ride") and o["walk_m"] == sum(l["distance_m"] for l in o["legs"] if l["mode"] in ("walk", "transfer"))
    assert o["duration_min"] == (dt.datetime.fromisoformat(o["arrive_at"]) - dt.datetime.fromisoformat(o["depart_at"])).total_seconds() // 60


def test_annotate_attaches_zones_delays_and_route_alerts_with_the_legs_they_touch(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    disr = build(conn, NOW)
    q = query(conn, tt, "Finch", "Union", NOW)
    o = planner.materialize(conn, q, raptor.plan(q)[0], NOW.date())
    out = planner.annotate(q, disr, [o])[0]
    ids = {d["alert_id"]: d for d in out["disruptions"]}
    assert "77535" in ids and ids["77535"]["type"] == "rsz" and ids["77535"]["affects"] == [1]
    assert all("affects" in d for d in out["disruptions"]) and "77519" not in ids


def _between(a: str, b: str) -> int:
    return int((dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).total_seconds() // 60)


def test_minutes_agree_with_the_printed_times_and_the_walk_formula(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    seen = set()
    for origin in (CHERRY_FRONT, "Finch", "15461"):
        q = query(conn, tt, origin, "Union", NOW)
        for j in raptor.plan(q) + raptor.plan_window(q, 1800):
            o = planner.materialize(conn, q, j, NOW.date())
            assert o["duration_min"] == _between(o["depart_at"], o["arrive_at"])
            for l in o["legs"]:
                seen.add(l["mode"])
                if l["mode"] == "walk":
                    assert l["min"] == geo.walk_minutes(l["distance_m"])
                else:
                    assert l["min"] == _between(l["depart"], l["arrive"])
                if l["mode"] == "transfer":
                    assert l["walk_min"] == geo.walk_minutes(l["distance_m"]) and l["min"] >= 2
    assert {"walk", "ride", "transfer"} <= seen


def test_affects_numbers_the_leg_in_the_full_leg_list(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    disr, day = build(conn, NOW), NOW.date()
    # Finch to the Cherry St stop: the RSZ ride is leg 1, the transfer leg 2 and the 504 leg 3, which nothing touches.
    q = query(conn, tt, "Finch", "15461", NOW)
    o = planner.materialize(conn, q, next(j for j in raptor.plan(q) if j.transfers == 1), day)
    assert [l["mode"] for l in o["legs"]] == ["ride", "transfer", "ride"]
    assert [(d["alert_id"], d["affects"]) for d in planner.annotate(q, disr, [o])[0]["disruptions"]] == [("77535", [1])]
    # From a point beside Finch the walk is leg 1, so the same zone sits on leg 2.
    q = query(conn, tt, {"lat": 43.7807, "lon": -79.4147}, "Union", NOW)
    o = planner.materialize(conn, q, raptor.plan(q)[0], day)
    assert [l["mode"] for l in o["legs"]] == ["walk", "ride"]
    assert [(d["alert_id"], d["affects"]) for d in planner.annotate(q, disr, [o])[0]["disruptions"]] == [("77535", [2])]
    # A zone on the Line 1 ride that follows a streetcar and a transfer: walk, ride, transfer, ride, so leg 4 (a copy of the RSZ moved to King).
    q = query(conn, tt, CHERRY_FRONT, "Union", NOW)
    o = planner.materialize(conn, q, next(j for j in raptor.plan(q) if j.transfers == 1), day)
    assert [l["mode"] for l in o["legs"]] == ["walk", "ride", "transfer", "ride"]
    rsz = next(e for e in disr.elements if e.get("alert_id") == "77535")
    king = conn.execute("SELECT parent_station FROM stops WHERE stop_id='13814'").fetchone()[0]
    moved = Disruptions(conn, [{**rsz, "alert_id": "T-KING", "stations": [king], "station_names": ["King"]}], {})
    assert [(d["alert_id"], d["affects"]) for d in planner.annotate(q, moved, [o])[0]["disruptions"]] == [("T-KING", [4])]
