import datetime as dt

from tests.unit.test_disruptions import NOW_NIGHT, build
from tests.unit.test_raptor import query
from ttc_mcp.domain import geo, planner, raptor
from ttc_mcp.domain.disruptions import Disruptions
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
    # Just after midnight pattern 3 leaves 15246 at 24:11 and pattern 37 at 25:11.
    midnight = dt.datetime(2026, 10, 6, 0, 0, tzinfo=TZ)
    assert planner.next_departure(conn, "15246", 37, midnight)["gtfs_time"] == "25:11" and planner.next_departure(conn, "15246", 3, midnight)["pattern_id"] == 3


def _rides(option):
    return [l for l in option["legs"] if l["mode"] == "ride"]


def test_direct_and_one_transfer_options_from_cherry_front_to_union(conn):
    opts = planner.search(conn, planner.resolve_endpoint(conn, CHERRY_FRONT), planner.resolve_endpoint(conn, "Union"), now=NOW)
    assert opts and [o["rank"] for o in opts] == list(range(1, len(opts) + 1)) and len(opts) <= 4
    first = opts[0]
    assert first["transfers"] == 0 and _rides(first)[0]["route"] == "121" and _rides(first)[0]["board"]["number"] == "6604"
    assert _rides(first)[0]["alight"]["number"] == "16754" and 8 <= _rides(first)[0]["stops"] <= 16 and _rides(first)[0]["transit"] == "bus"
    assert first["legs"][0] == {"mode": "walk", "to": {"number": "6604", "name": "Mill St at Trinity St"}, "distance_m": 239, "min": 4}
    assert first["typical_min"] == sum(l["min"] if "min" in l else l["typical_min"] for l in first["legs"]) and first["walk_m"] >= 239
    assert first["first_departure"]["route"] == "121" and 0 <= first["first_departure"]["in_min"] <= 120
    xfer = next(o for o in opts if o["transfers"] == 1)
    assert xfer["rank"] == 2 and all("121" not in [r["route"] for r in _rides(o)] for o in opts if o["transfers"] == 1)
    assert [r["route"] for r in _rides(xfer)] == ["504", "1"] and _rides(xfer)[1]["alight"]["number"] in {"13815", "13816"}
    assert _rides(xfer)[0]["alight"]["number"] in {"15637", "15638"} and _rides(xfer)[1]["transit"] == "subway"
    t = next(l for l in xfer["legs"] if l["mode"] == "transfer")
    assert t["min"] >= planner.TRANSFER_PENALTY_MIN and t["from"]["number"] in {"15637", "15638"} and t["to"]["number"] in {"13813", "13814"}


def test_limit_max_transfers_and_dedup(conn):
    direct = planner.search(conn, planner.resolve_endpoint(conn, CHERRY_FRONT), planner.resolve_endpoint(conn, "Union"), now=NOW, max_transfers=0, limit=2)
    assert 1 <= len(direct) <= 2 and all(o["transfers"] == 0 for o in direct)
    keys = [tuple(r["route"] for r in _rides(o)) for o in planner.search(
        conn, planner.resolve_endpoint(conn, CHERRY_FRONT), planner.resolve_endpoint(conn, "Union"), now=NOW, limit=10)]
    assert len(keys) == len(set(keys))


def test_overnight_route_only_appears_overnight(conn):
    # The fixture's rank-1 eastbound 300 pattern is a two-stop airport hop; take the first one long enough.
    pid = conn.execute("SELECT pattern_id FROM patterns WHERE route_id='300' AND direction_id=0 AND json_array_length(stops) > 12 "
                       "ORDER BY rank LIMIT 1").fetchone()[0]
    rows = planner.pattern_rows(conn, pid)
    a, b = rows[2][1], rows[12][1]
    day = planner.search(conn, planner.resolve_endpoint(conn, a), planner.resolve_endpoint(conn, b), now=NOW, max_transfers=0)
    night = planner.search(conn, planner.resolve_endpoint(conn, a), planner.resolve_endpoint(conn, b), now=NIGHT_EARLY, max_transfers=0)
    assert day == [] and night and _rides(night[0])[0]["route"] == "300" and night[0]["legs"][0]["mode"] == "ride"


def test_unusable_stops_are_skipped(conn):
    def usable(sid):
        return sid != "15246"

    opts = planner.search(conn, planner.resolve_endpoint(conn, CHERRY_FRONT), planner.resolve_endpoint(conn, "Union"), now=NOW, usable=usable)
    assert all(_rides(o)[0]["board"]["number"] != "15246" for o in opts)
    banned = {"15246", "15637"}
    opts = planner.search(conn, planner.resolve_endpoint(conn, CHERRY_FRONT), planner.resolve_endpoint(conn, "Union"), now=NOW, limit=10,
                          usable=lambda sid: sid not in banned)
    touched = {l[k]["number"] for o in opts for l in o["legs"] for k in ("board", "alight", "from", "to") if k in l}
    assert opts and not touched & banned


def test_rides_move_forward_on_loop_patterns(conn, monkeypatch):
    # Route 400 runs Mon, Wed and Thu; pattern 41 visits 15607 at pos 33 and 35, either side of 11641 at pos 34.
    wed = dt.datetime(2026, 10, 7, 10, 0, tzinfo=TZ)
    hop = planner.search(conn, planner.resolve_endpoint(conn, "11641"), planner.resolve_endpoint(conn, "15607"), now=wed)
    assert _rides(hop[0])[0]["route"] == "400" and _rides(hop[0])[0]["stops"] == 1
    # Pretend Line 1's 13835 platform is 50 m from 15607, so a transfer there must alight at pos 35, not the earlier visit.
    real = planner.transfer_stops
    monkeypatch.setattr(planner, "transfer_stops", lambda c, sid: {**real(c, sid), **({"13835": 50} if sid == "15607" else {})})
    opts = planner.search(conn, planner.resolve_endpoint(conn, "11641"), planner.resolve_endpoint(conn, "Union"), now=wed, limit=10)
    loop = [r for o in opts for r in _rides(o)[:1] if r["pattern_id"] == 41]
    assert [(r["board_pos"], r["alight_pos"], r["alight"]["number"]) for r in loop] == [(34, 35, "15607")]
    every = hop + opts + planner.search(conn, planner.resolve_endpoint(conn, CHERRY_FRONT), planner.resolve_endpoint(conn, "Union"), now=NOW, limit=10)
    assert all(r["stops"] > 0 and r["alight_pos"] > r["board_pos"] for o in every for r in _rides(o))


def test_transfers_stop_short_of_the_destination_and_never_leave_a_direct_pattern(conn, monkeypatch):
    union, real = planner.resolve_endpoint(conn, "Union"), planner.transfer_stops
    # Line 1 from Queen reaches Union (pos 16) before St Andrew (pos 17); pretend a 121 stop bound for Union is 50 m from St Andrew.
    monkeypatch.setattr(planner, "transfer_stops", lambda c, sid: {**real(c, sid), **({"13455": 50} if sid == "13817" else {})})
    opts = planner.search(conn, planner.resolve_endpoint(conn, "13811"), union, now=NOW, limit=10)
    assert opts and _rides(opts[0])[0]["route"] == "1" and all(o["transfers"] == 0 for o in opts)
    for o in (o for o in opts if o["transfers"] == 1):
        r, rows = _rides(o)[0], planner.pattern_rows(conn, _rides(o)[0]["pattern_id"])
        assert r["alight_pos"] < next((i for i in range(r["board_pos"] + 1, len(rows)) if rows[i][1] in union.stops), len(rows))
    # Pretend King's southbound platform is 50 m from a 121 stop short of Union; staying on the 121 beats that transfer.
    monkeypatch.setattr(planner, "transfer_stops", lambda c, sid: {**real(c, sid), **({"13814": 50} if sid == "11169" else {})})
    opts = planner.search(conn, planner.resolve_endpoint(conn, CHERRY_FRONT), union, now=NOW, limit=10)
    assert opts and all(_rides(o)[0]["route"] != "121" for o in opts if o["transfers"] == 1)


def test_a_span_that_is_not_rideable_is_no_candidate_so_the_transfer_scan_routes_around_it(conn, monkeypatch):
    finch, union, real = planner.resolve_endpoint(conn, "Finch"), planner.resolve_endpoint(conn, "Union"), planner.transfer_stops
    # Pretend a 121 stop bound for Union is 50 m from Sheppard-Yonge's southbound platform.
    monkeypatch.setattr(planner, "transfer_stops", lambda c, sid: {**real(c, sid), **({"11169": 50} if sid == "13860" else {})})
    assert all(o["transfers"] == 0 for o in planner.search(conn, finch, union, now=NOW, limit=10))
    lawrence = {"13793", "13794"}
    def closed(pid, a, b):
        return not any(sid in lawrence for _, sid, _ in planner.pattern_rows(conn, pid)[a:b + 1])

    opts = planner.search(conn, finch, union, now=NOW, limit=10, rideable=closed)
    assert opts and all(o["transfers"] == 1 for o in opts)
    assert [(r["route"], r["board"]["number"], r["alight"]["number"]) for r in _rides(opts[0])] == [("1", "14111", "13860"), ("121", "11169", "16754")]


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
    out = planner.annotate(conn, disr, [o])[0]
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
    assert [(d["alert_id"], d["affects"]) for d in planner.annotate(conn, disr, [o])[0]["disruptions"]] == [("77535", [1])]
    # From a point beside Finch the walk is leg 1, so the same zone sits on leg 2.
    q = query(conn, tt, {"lat": 43.7807, "lon": -79.4147}, "Union", NOW)
    o = planner.materialize(conn, q, raptor.plan(q)[0], day)
    assert [l["mode"] for l in o["legs"]] == ["walk", "ride"]
    assert [(d["alert_id"], d["affects"]) for d in planner.annotate(conn, disr, [o])[0]["disruptions"]] == [("77535", [2])]
    # A zone on the Line 1 ride that follows a streetcar and a transfer: walk, ride, transfer, ride, so leg 4 (a copy of the RSZ moved to King).
    q = query(conn, tt, CHERRY_FRONT, "Union", NOW)
    o = planner.materialize(conn, q, next(j for j in raptor.plan(q) if j.transfers == 1), day)
    assert [l["mode"] for l in o["legs"]] == ["walk", "ride", "transfer", "ride"]
    rsz = next(e for e in disr.elements if e.get("alert_id") == "77535")
    king = conn.execute("SELECT parent_station FROM stops WHERE stop_id='13814'").fetchone()[0]
    moved = Disruptions(conn, [{**rsz, "alert_id": "T-KING", "stations": [king], "station_names": ["King"]}], {})
    assert [(d["alert_id"], d["affects"]) for d in planner.annotate(conn, moved, [o])[0]["disruptions"]] == [("T-KING", [4])]
