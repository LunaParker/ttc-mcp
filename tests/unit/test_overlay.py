import dataclasses
import datetime as dt
import sqlite3
from array import array
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.test_disruptions import NOW_DAY, NOW_NIGHT, build
from ttc_mcp import overlay
from ttc_mcp.adapters.bustime_rt import StopUpdate, TripUpdateRec
from ttc_mcp.adapters.detour_feed import parse_detours
from ttc_mcp.domain import planner, raptor
from ttc_mcp.static import schedule


def _tt():
    """Pattern 1 over stops A, B, C with two trips 600 s apart; pattern 2 over B, D with one trip."""
    g1 = raptor.Group(1, "1", [array("i", [0, 600]), array("i", [100, 700]), array("i", [200, 800])], array("q", [11, 12]))
    g2 = raptor.Group(2, "1", [array("i", [150]), array("i", [250])], array("q", [21]))
    return raptor.Timetable("T", {1: ["A", "B", "C"], 2: ["B", "D"]}, {1: {"route": "r1", "direction_id": "0", "headsign": "x", "mode": "bus"},
                                                                      2: {"route": "r2", "direction_id": "0", "headsign": "y", "mode": "bus"}}, {1: [g1], 2: [g2]}, {},
                            pattern_seqs={1: [1, 2, 3], 2: [1, 2]})


def _loop_tt():
    """Pattern 7 visits B twice (A B C B D, stop_sequence 1 to 5) with one trip."""
    g = raptor.Group(7, "1", [array("i", [t]) for t in (0, 100, 200, 300, 400)], array("q", [71]))
    return raptor.Timetable("L", {7: ["A", "B", "C", "B", "D"]}, {7: {"route": "r7", "direction_id": "0", "headsign": "z", "mode": "bus"}}, {7: [g]}, {},
                            pattern_seqs={7: [1, 2, 3, 4, 5]})


def _slot(tt):
    def slot(trip_id):
        for pid, gs in tt.groups.items():
            for gi, g in enumerate(gs):
                if int(trip_id) in g.trip_ids:
                    return pid, gi, list(g.trip_ids).index(int(trip_id))
        return None
    return slot


def rec(trip, *ups):
    return TripUpdateRec(str(trip), "r1", None, "SCHEDULED", tuple(StopUpdate(s, None, t, rel) for s, t, rel in ups))


def srec(trip, *ups):
    return TripUpdateRec(str(trip), "r1", None, "SCHEDULED", tuple(StopUpdate(s, q, t, rel) for s, q, t, rel in ups))


def test_an_update_moves_the_stop_and_propagates_its_delay_to_later_stops():
    tt = _tt()
    out = overlay.patch_groups(tt, [rec(11, ("B", 1000 + 160, "SCHEDULED"))], lambda s: s, 1000, _slot(tt))
    g = out[1][0]
    assert [list(c) for c in g.cols] == [[0, 600], [160, 700], [260, 800]] and g.realtime == frozenset({0}) and not g.skip and list(g.trip_ids) == [11, 12]
    assert 2 not in out


def test_a_skipped_stop_goes_on_the_mask_and_no_data_changes_nothing():
    tt = _tt()
    out = overlay.patch_groups(tt, [rec(12, ("B", None, "SKIPPED"), ("C", None, "NO_DATA"))], lambda s: s, 1000, _slot(tt))
    g = out[1][0]
    assert g.skip == frozenset({(1, 1)}) and [list(c) for c in g.cols] == [[0, 600], [100, 700], [200, 800]] and g.realtime == frozenset({1})


def test_an_update_that_makes_a_trip_overtake_the_next_splits_the_group():
    tt = _tt()
    out = overlay.patch_groups(tt, [rec(11, ("B", 1000 + 750, "SCHEDULED"))], lambda s: s, 1000, _slot(tt))
    assert [list(g.trip_ids) for g in out[1]] == [[11], [12]] and [list(c) for c in out[1][0].cols] == [[0], [750], [850]]
    assert out[1][0].realtime == frozenset({0}) and out[1][1].realtime == frozenset()


def test_updates_for_unknown_trips_other_days_and_unmapped_stops_are_ignored():
    tt = _tt()
    out = overlay.patch_groups(tt, [rec(99, ("B", 1100, "SCHEDULED")), rec(11, ("B", 1000 + 100 + 7 * 3600, "SCHEDULED")), rec(12, ("Z", 1700, "SCHEDULED"))],
                               lambda s: None if s == "Z" else s, 1000, _slot(tt))
    assert out == {}


def test_slices_read_through_the_overlay():
    tt = _tt()
    ov = overlay.Overlay(groups=overlay.patch_groups(tt, [rec(11, ("B", 1160, "SCHEDULED"))], lambda s: s, 1000, _slot(tt)))
    sl = raptor.Slice(tt, frozenset({"1"}), 0, ov)
    assert sl.groups_for(1)[0].cols[1][0] == 160 and sl.groups_for(2) == tt.groups[2] and sl.stops_of(1) == ["A", "B", "C"] and sl.meta_of(2)["route"] == "r2"
    ov.extra[-1], ov.extra_stops[-1], ov.extra_meta[-1] = [], ["B", "Q"], {"route": "x"}
    ov.extra_at.setdefault("B", []).append((-1, 0))
    assert list(sl.patterns_at("B")) == [(1, 1), (2, 0), (-1, 0)] and sl.stops_of(-1) == ["B", "Q"] and sl.groups_for(-1) == [] and sl.meta_of(-1) == {"route": "x"}


@pytest.mark.parametrize("with_seq", [False, True])
def test_a_platform_listed_again_for_the_next_train_stays_out_of_the_first_trains_row(with_seq):
    tt = _tt()
    ups = [("A", 1, 1010), ("B", 2, 1110), ("C", 3, 1210), ("A", 1, 1610), ("B", 2, 1710), ("C", 3, 1810)]
    out = overlay.patch_groups(tt, [srec(11, *((s, q if with_seq else None, t, "SCHEDULED") for s, q, t in ups))], lambda s: s, 1000, _slot(tt))
    assert [list(g.trip_ids) for g in out[1]] == [[11, 12]] and [list(c) for c in out[1][0].cols] == [[10, 600], [110, 700], [210, 800]]


def test_a_stop_sequence_puts_a_loops_prediction_on_the_visit_it_names():
    tt = _loop_tt()
    out = overlay.patch_groups(tt, [srec(71, ("B", 4, 1360, "SCHEDULED"), ("D", 5, 1460, "SCHEDULED"))], lambda s: s, 1000, _slot(tt))
    assert [c[0] for c in out[7][0].cols] == [0, 100, 200, 360, 460]


def test_no_data_returns_the_trip_to_its_schedule_from_that_stop_on():
    tt = _loop_tt()
    out = overlay.patch_groups(tt, [srec(71, ("A", 1, 1060, "SCHEDULED"), ("C", 3, None, "NO_DATA"))], lambda s: s, 1000, _slot(tt))
    assert [c[0] for c in out[7][0].cols] == [60, 160, 200, 300, 400] and out[7][0].realtime == frozenset({0})
    assert overlay.patch_groups(tt, [srec(71, ("C", 3, None, "NO_DATA"))], lambda s: s, 1000, _slot(tt)) == {}


def test_a_canceled_trip_is_masked_at_every_stop():
    tt = _tt()
    out = overlay.patch_groups(tt, [TripUpdateRec("12", "r1", None, "CANCELED", ())], lambda s: s, 1000, _slot(tt))
    g = out[1][0]
    assert g.skip == frozenset({(1, 0), (1, 1), (1, 2)}) and g.realtime == frozenset({1}) and [list(c) for c in g.cols] == [[0, 600], [100, 700], [200, 800]]


def test_a_patch_that_keeps_the_order_rebuilds_the_group_without_re_splitting(monkeypatch):
    tt = _tt()
    monkeypatch.setattr(overlay, "split_overtaking", lambda trips: pytest.fail("re-split an in-order patch"))
    out = overlay.patch_groups(tt, [rec(11, ("B", 1160, "SCHEDULED"))], lambda s: s, 1000, _slot(tt))
    assert [list(c) for c in out[1][0].cols] == [[0, 600], [160, 700], [260, 800]] and list(out[1][0].trip_ids) == [11, 12]


def test_a_trip_patched_ahead_of_the_one_before_it_reorders_the_group():
    tt = _tt()
    out = overlay.patch_groups(tt, [rec(12, ("A", 950, "SCHEDULED"))], lambda s: s, 1000, _slot(tt))
    assert [list(g.trip_ids) for g in out[1]] == [[12, 11]] and [list(c) for c in out[1][0].cols] == [[-50, 0], [50, 100], [150, 200]]
    assert out[1][0].realtime == frozenset({0})


def test_two_patched_trips_that_cross_before_the_later_ones_first_update_split():
    tt = _tt()
    recs = [rec(11, ("A", 1650, "SCHEDULED"), ("C", None, "NO_DATA")), rec(12, ("C", 1900, "SCHEDULED"))]
    out = overlay.patch_groups(tt, recs, lambda s: s, 1000, _slot(tt))
    assert [list(g.trip_ids) for g in out[1]] == [[12], [11]] and [[c[0] for c in g.cols] for g in out[1]] == [[600, 700, 900], [650, 750, 750]]


def test_a_subway_update_past_its_drift_is_ignored_and_one_within_it_applies():
    tt = _tt()
    recs = [rec(11, ("B", 1000 + 100 + 20 * 60, "SCHEDULED")), rec(12, ("B", 1000 + 700 + 10 * 60, "SCHEDULED"))]
    out = overlay.patch_groups(tt, recs, lambda s: s, 1000, _slot(tt), tt.pattern_seqs.get, drift=overlay.SUBWAY_DRIFT_S)
    assert [list(c) for c in out[1][0].cols] == [[0, 600], [100, 1300], [200, 1400]] and out[1][0].realtime == frozenset({1})
    assert overlay.patch_groups(tt, recs[:1], lambda s: s, 1000, _slot(tt))[1][0].cols[1][0] == 1300


def test_a_second_call_chained_through_base_keeps_the_first_calls_replacements():
    tt = _tt()
    first = overlay.patch_groups(tt, [rec(11, ("B", 1160, "SCHEDULED"))], lambda s: s, 1000, _slot(tt))
    both = overlay.patch_groups(tt, [rec(21, ("D", 1310, "SCHEDULED"))], lambda s: s, 1000, _slot(tt), drift=overlay.SUBWAY_DRIFT_S, base=first)
    assert both[1] == first[1] and both[2][0].cols[1][0] == 310 and both[2][0].realtime == frozenset({0}) and 2 not in first


FIX = Path(__file__).resolve().parents[1] / "fixtures" / "advisories"
TEN_FIFTY = dt.datetime(2026, 10, 6, 10, 50, tzinfo=schedule.TZ)


def _entities():
    return parse_detours((FIX / "detour.json").read_bytes(), {}).value.entities


def test_detour_9001_becomes_a_variant_pattern_without_its_skipped_stops(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    groups, extra, stops, meta, at, variants = overlay.detour_variants(conn, tt, _entities(), dt.date(2026, 10, 6))
    (pid, variant), = extra.items()
    assert variants == {"detour_0_9001:0_0_6_504_Distillery"}
    assert pid == -1000 and stops[pid] == [s for s in tt.pattern_stops[6] if s not in ("15039", "14994", "14993")]
    assert meta[pid]["base_pattern"] == 6 and meta[pid]["route"] == "504" and meta[pid]["detour_id"] == "9001" and meta[pid]["positions"][:3] == [0, 1, 5]
    assert sorted(int(t) for g in variant for t in g.trip_ids) == [100677020, 100895020, 101100020] and all(len(g.cols) == 35 for g in variant)
    assert sum(g.n_trips for g in groups[6]) == sum(g.n_trips for g in tt.groups[6]) - 3 and (pid, 0) in at["14665"] and "14994" not in at
    assert not any(int(t) in (100677020, 100895020, 101100020) for g in groups[6] for t in g.trip_ids)


def test_a_variant_keeps_its_trips_times_beyond_the_span_and_adds_the_published_delay(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    _, extra, stops, _, _, _ = overlay.detour_variants(conn, tt, _entities(), dt.date(2026, 10, 6))
    g = next(g for g in extra[-1000] if 100677020 in g.trip_ids)
    i = list(g.trip_ids).index(100677020)
    assert g.cols[stops[-1000].index("4569")][i] == 39518 and g.cols[stops[-1000].index("4573")][i] == 39917  # 10:58:38 and 11:05:17, the delay is 0


def test_a_detour_outside_its_service_dates_or_with_unknown_trips_makes_no_variant(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    groups, extra, *_ = overlay.detour_variants(conn, tt, _entities(), dt.date(2026, 10, 9))
    assert groups == {} and extra == {}
    only_9002 = [e for e in _entities() if e.detour_id == "9002"]
    assert overlay.detour_variants(conn, tt, only_9002, dt.date(2026, 10, 6))[1] == {} and overlay.detour_variants(conn, tt, only_9002, dt.date(2026, 10, 6))[5] == set()


def test_the_planner_boards_the_next_undetoured_trip_to_a_skipped_stop(conn):
    from tests.unit.test_raptor import query
    tt = raptor.Timetable.load(conn, "S1000538")
    ov = overlay.Overlay()
    ov.add_variants(*overlay.detour_variants(conn, tt, _entities(), dt.date(2026, 10, 6)))
    assert ov.variants == {"detour_0_9001:0_0_6_504_Distillery"}
    q = query(conn, tt, "4569", "14994", TEN_FIFTY)
    plain = raptor.plan(q)[0].legs[0]
    q.slices = [raptor.Slice(s.tt, s.service_ids, s.offset, ov, s.conn) for s in q.slices]
    detoured = raptor.plan(q)[0].legs[0]
    assert (plain.depart, detoured.depart) == (10 * 3600 + 58 * 60 + 38, 11 * 3600 + 8 * 60 + 38)
    through = next(j for j in raptor.plan(query(conn, tt, "4569", "4573", TEN_FIFTY)) if j.transfers == 0)
    q2 = query(conn, tt, "4569", "4573", TEN_FIFTY)
    q2.slices = [raptor.Slice(s.tt, s.service_ids, s.offset, ov, s.conn) for s in q2.slices]
    variant = next(j for j in raptor.plan(q2) if j.transfers == 0).legs[0]
    assert variant.pattern == -1000 and (variant.alight_pos - variant.board_pos, through.legs[0].alight_pos - through.legs[0].board_pos) == (1, 4)
    assert q2.slices[0].groups_for(-1000)[variant.group].trip_ids[variant.trip] == 100677020


def test_the_delays_of_two_spans_accumulate_as_the_trip_advances(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    e = dataclasses.replace(next(e for e in _entities() if e.detour_id == "9001"), spans=((3, 3, 60), (7, 7, 30)))
    _, extra, _, meta, _, _ = overlay.detour_variants(conn, tt, [e], dt.date(2026, 10, 6))
    _, gi, ti = tt.trip_slot(conn, "100677020")
    sched, g = [c[ti] for c in tt.groups[6][gi].cols], next(g for g in extra[-1000] if 100677020 in g.trip_ids)
    i = list(g.trip_ids).index(100677020)
    assert meta[-1000]["positions"][:7] == [0, 1, 3, 4, 5, 7, 8]
    assert [c[i] for c in g.cols] == [sched[p] + (60 if 2 < p < 6 else 90 if p > 6 else 0) for p in meta[-1000]["positions"]]  # GTFS-RT: delays accumulate


@pytest.mark.parametrize("canceled", [False, True])
def test_a_variant_chained_on_a_patched_group_keeps_what_the_patch_established(conn, canceled):
    tt = raptor.Timetable.load(conn, "S1000538")
    origin = schedule.wall_clock(dt.date(2026, 10, 6), 0).timestamp()
    r = TripUpdateRec("100677020", "504", None, "CANCELED", ()) if canceled else rec(100677020, ("4569", origin + 39518 + 120, "SCHEDULED"))
    patched = overlay.patch_groups(tt, [r], lambda s: s, origin, lambda t: tt.trip_slot(conn, t))
    e = dataclasses.replace(next(e for e in _entities() if e.detour_id == "9001"), spans=((3, 5, 300),))
    _, extra, stops, _, _, _ = overlay.detour_variants(conn, tt, [e], dt.date(2026, 10, 6), base=patched)
    at = stops[-1000].index
    g, other = (next(g for g in extra[-1000] if t in g.trip_ids) for t in (100677020, 100895020))
    i, j = list(g.trip_ids).index(100677020), list(other.trip_ids).index(100895020)
    assert i in g.realtime and j not in other.realtime and other.cols[at("4573")][j] == 70457 + 300  # an unpredicted trip takes the detour delay
    if canceled:
        assert {p for t, p in g.skip if t == i} == set(range(35))
    else:
        assert (g.cols[at("4569")][i], g.cols[at("4573")][i]) == (39518 + 120, 39917 + 120) and not g.skip  # the prediction, no delay on top


def test_a_trip_an_entity_lists_twice_rides_the_variant_once(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    e = dataclasses.replace(next(e for e in _entities() if e.detour_id == "9001"), trip_ids=("100677020", "100677020"))
    groups, extra, *_ = overlay.detour_variants(conn, tt, [e], dt.date(2026, 10, 6))
    assert [int(t) for g in extra[-1000] for t in g.trip_ids] == [100677020] and sum(g.n_trips for g in groups[6]) == sum(g.n_trips for g in tt.groups[6]) - 1


def test_a_group_the_detour_takes_every_trip_of_leaves_the_pattern(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    small = min(tt.groups[6], key=lambda g: g.n_trips)
    e = dataclasses.replace(next(e for e in _entities() if e.detour_id == "9001"), trip_ids=tuple(str(t) for t in small.trip_ids))
    groups, extra, *_ = overlay.detour_variants(conn, tt, [e], dt.date(2026, 10, 6))
    assert len(groups[6]) == len(tt.groups[6]) - 1 and all(g.n_trips for g in groups[6]) and sum(g.n_trips for g in extra[-1000]) == small.n_trips


def test_the_night_closure_becomes_two_shuttle_pseudo_routes_over_its_stations_in_line_order(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    extra, stops, meta, at = overlay.shuttle_routes(conn, tt, build(conn, NOW_NIGHT))
    assert set(extra) == {-2000, -2001} and stops[-2000] == ["13791", "13793", "13795"] and stops[-2001] == ["13795", "13793", "13791"]
    h = extra[-2000][0]
    assert isinstance(h, raptor.Headway) and h.headway_s == 300 and h.cum == (0, 547, 1166) and extra[-2001][0].cum == (0, 619, 1166)
    m = meta[-2000]
    assert (m["line"], m["alert_id"], m["mode"], m["station_of"]["13795"]) == ("1", "77519", "shuttle", {"station": "43274", "name": "Eglinton"})
    assert m["note"] == "assumed 5-minute headway and 15 km/h over 4.4 km of track with a 1-minute stop per station; the TTC publishes no shuttle timetable (alert 77519)"
    assert at["13793"] == [(-2000, 1), (-2001, 1)]
    assert overlay.shuttle_routes(conn, tt, build(conn, NOW_DAY))[0] == {}


def test_a_platform_without_a_shape_distance_drops_out_of_the_shuttle_chain():
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE patterns (pattern_id INTEGER, route_id TEXT, direction_id INTEGER, rank INTEGER);
        CREATE TABLE pattern_stops (pattern_id INTEGER, stop_id TEXT, dist_m REAL);
        CREATE TABLE stops (stop_id TEXT, name TEXT, parent_station TEXT, location_type INTEGER);
        CREATE TABLE stop_routes (stop_id TEXT, route_id TEXT);
        INSERT INTO patterns VALUES (1, '1', 0, 1);
        INSERT INTO pattern_stops VALUES (1, 'a1', 100), (1, 'b1', NULL), (1, 'c1', 2100);
        INSERT INTO stops VALUES ('A', 'Alpha', NULL, 1), ('B', 'Beta', NULL, 1), ('C', 'Gamma', NULL, 1), ('a1', 'Alpha P', 'A', 0), ('b1', 'Beta P', 'B', 0), ('c1', 'Gamma P', 'C', 0);
        INSERT INTO stop_routes VALUES ('a1', '1'), ('b1', '1'), ('c1', '1');
    """)
    el = {"type": "closure", "active_now": True, "shuttle": "Will Operate", "stations": ["A", "B", "C"], "routes": ["1"], "alert_id": "x"}
    extra, stops, meta, _ = overlay.shuttle_routes(db, None, SimpleNamespace(elements=[el]))
    assert stops == {-2000: ["a1", "c1"], -2001: ["c1", "a1"]} and extra[-2000][0].cum == (0, 540) and "over 2.0 km of track" in meta[-2000]["note"]


def _night(conn, origin, dest):
    """A query at NOW_NIGHT under closure 77519 with its shuttle pseudo-routes on both day slices."""
    from tests.unit.test_raptor import query
    tt, disr = raptor.Timetable.load(conn, "S1000538"), build(conn, NOW_NIGHT)
    ov, closer = overlay.Overlay(), planner.closed_for(conn, disr, tt)
    ov.add_shuttles(*overlay.shuttle_routes(conn, tt, disr))
    q = query(conn, tt, origin, dest, NOW_NIGHT, closed=lambda si, pid: closer(pid))
    q.slices = [raptor.Slice(s.tt, s.service_ids, s.offset, ov, s.conn) for s in q.slices]
    return q


def test_a_journey_from_inside_the_closure_starts_on_the_shuttle_and_departs_at_the_query_instant(conn):
    q = _night(conn, "Lawrence", "Union")
    j = raptor.plan(q)[0]
    shuttle, ride = j.rides
    assert j.legs[0] == shuttle and (shuttle.kind, shuttle.pattern, shuttle.frm, shuttle.to, ride.frm) == ("shuttle", -2000, "13793", "13795", "13795")
    # The first boarding takes no change buffer, so the shuttle leaves a full headway after the origin label and the option keeps that wait.
    assert j.depart == q.t0 and shuttle.depart == q.t0 + q.origin["13793"][0] + 300 and shuttle.arrive == shuttle.depart + 619
    assert ride.depart >= shuttle.arrive + raptor.CHANGE_S and planner.materialize(conn, q, j, NOW_NIGHT.date())["depart_at"] == "2026-10-06T23:30-04:00"


def test_the_reverse_shuttle_carries_a_northbound_journey_on_its_own_cumulative_seconds(conn):
    to_lawrence = raptor.plan(_night(conn, "Eglinton", "Lawrence"))[0]
    assert [(l.kind, l.pattern, l.frm, l.to, l.arrive - l.depart) for l in to_lawrence.legs] == [("shuttle", -2001, "13795", "13793", 619)]
    q = _night(conn, "Eglinton", "Finch")
    j = raptor.plan(q)[0]
    shuttle, ride = j.rides
    assert (shuttle.pattern, shuttle.frm, shuttle.to, shuttle.depart, shuttle.arrive - shuttle.depart) == (-2001, "13795", "13791", q.t0 + 300, 1166)
    assert ride.frm == "13791" and ride.depart >= shuttle.arrive + raptor.CHANGE_S and j.depart == q.t0


def test_build_memoises_per_feed_day_and_inputs_and_reset_clears_it(app_env, conn):
    from tests.unit.test_disruptions import build as disr_at
    feed = app_env.feed_for()
    tt = raptor.timetable_for(feed)
    disr = disr_at(conn, NOW_NIGHT)
    a = overlay.build(feed, tt, disr, None, None, None, dt.date(2026, 10, 6))
    assert overlay.build(feed, tt, disr, None, None, None, dt.date(2026, 10, 6)) is a and set(a.extra) == {-2000, -2001} and a.groups == {}
    b = overlay.build(feed, tt, disr, None, None, None, dt.date(2026, 10, 5))
    assert b is not a
    overlay.reset()
    assert overlay.build(feed, tt, disr, None, None, None, dt.date(2026, 10, 6)) is not a


def test_public_updates_map_surface_stop_ids_through_the_crosswalk(conn):
    from tests.rtfeeds import make_trip_feed
    from ttc_mcp.adapters.bustime_rt import parse_trips
    surface = conn.execute("SELECT surface_stop_id FROM crosswalk WHERE stop_code='15637'").fetchone()[0]
    feed = parse_trips(make_trip_feed(1, [{"trip": "107845020", "route": "504", "stops": [(surface, 10, 1000), ("nope", 11, 2000)]}]), {}).value
    recs = overlay.public_updates(conn, feed)
    assert [(u.stop_id, u.time) for u in recs[0].updates] == [("15637", 1000)] and overlay.public_updates(conn, None) == []


def test_the_locator_answers_what_trip_slot_and_the_list_scan_answer(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    ids = ["100677020", "22650020", "134929152", "999999999", "nope"]
    loc = overlay.Locator(conn, tt, [*ids, *(t for e in _entities() for t in e.trip_ids)])
    assert [loc.slot(t) for t in ids] == [tt.trip_slot(conn, t) for t in ids] and loc.slot("100677020") is not None
    day = dt.date(2026, 10, 6)
    origin = schedule.wall_clock(day, 0).timestamp()
    patched = overlay.patch_groups(tt, [TripUpdateRec("100895020", "504", None, "CANCELED", ())], lambda s: s, origin, loc.slot)
    assert patched == overlay.patch_groups(tt, [TripUpdateRec("100895020", "504", None, "CANCELED", ())], lambda s: s, origin, lambda t: tt.trip_slot(conn, t))
    assert overlay.detour_variants(conn, tt, _entities(), day, base=patched, locate=loc) == overlay.detour_variants(conn, tt, _entities(), day, base=patched)


def test_build_chains_surface_and_subway_patches_and_puts_a_predicted_detoured_trip_on_its_variant(app_env, conn):
    from tests.rtfeeds import make_trip_feed
    from tests.unit.test_disruptions import build as disr_at
    from ttc_mcp.adapters.bustime_rt import parse_trips
    feed, day = app_env.feed_for(), dt.date(2026, 10, 6)
    tt, origin = raptor.timetable_for(feed), int(schedule.wall_clock(day, 0).timestamp())
    at_4569 = conn.execute("SELECT surface_stop_id FROM crosswalk WHERE stop_code='4569'").fetchone()[0]
    surface = parse_trips(make_trip_feed(1, [{"trip": "100677020", "route": "504", "stops": [(at_4569, 2, origin + 39518 + 120)]}]), {}).value
    subway = parse_trips(make_trip_feed(1, [{"trip": "134929152", "route": "1", "stops": [("13814", 16, origin + 8 * 3600 + 19 * 60)]}]), {}).value
    detours = parse_detours((FIX / "detour.json").read_bytes(), {}).value
    ov = overlay.build(feed, tt, disr_at(conn, NOW_DAY), detours, surface, subway, day)
    g = next(g for g in ov.extra[-1000] if 100677020 in g.trip_ids)
    i = list(g.trip_ids).index(100677020)
    assert i in g.realtime and g.cols[ov.extra_stops[-1000].index("4569")][i] == 39518 + 120 and ov.variants == {"detour_0_9001:0_0_6_504_Distillery"}
    assert not any(100677020 in x.trip_ids for x in ov.groups[6])
    pid = tt.trip_slot(conn, "134929152")[0]
    sub = next(x for x in ov.groups[pid] if 134929152 in x.trip_ids)
    j = list(sub.trip_ids).index(134929152)
    assert j in sub.realtime and sub.cols[tt.pattern_stops[pid].index("13814")][j] == 8 * 3600 + 19 * 60 and set(ov.extra) == {-1000}
