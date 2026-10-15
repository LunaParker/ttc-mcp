from array import array

import pytest

from ttc_mcp import overlay
from ttc_mcp.adapters.bustime_rt import StopUpdate, TripUpdateRec
from ttc_mcp.domain import raptor


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
