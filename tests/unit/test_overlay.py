from array import array

from ttc_mcp import overlay
from ttc_mcp.adapters.bustime_rt import StopUpdate, TripUpdateRec
from ttc_mcp.domain import raptor


def _tt():
    """Pattern 1 over stops A, B, C with two trips 600 s apart; pattern 2 over B, D with one trip."""
    g1 = raptor.Group(1, "1", [array("i", [0, 600]), array("i", [100, 700]), array("i", [200, 800])], array("q", [11, 12]))
    g2 = raptor.Group(2, "1", [array("i", [150]), array("i", [250])], array("q", [21]))
    return raptor.Timetable("T", {1: ["A", "B", "C"], 2: ["B", "D"]}, {1: {"route": "r1", "direction_id": "0", "headsign": "x", "mode": "bus"},
                                                                      2: {"route": "r2", "direction_id": "0", "headsign": "y", "mode": "bus"}}, {1: [g1], 2: [g2]}, {})


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


def test_a_platform_listed_again_for_the_next_train_stays_out_of_the_first_trains_row():
    tt = _tt()
    ups = [("A", 1010, "SCHEDULED"), ("B", 1110, "SCHEDULED"), ("C", 1210, "SCHEDULED"), ("A", 1610, "SCHEDULED"), ("B", 1710, "SCHEDULED"), ("C", 1810, "SCHEDULED")]
    out = overlay.patch_groups(tt, [rec(11, *ups)], lambda s: s, 1000, _slot(tt))
    assert [list(g.trip_ids) for g in out[1]] == [[11, 12]] and [list(c) for c in out[1][0].cols] == [[10, 600], [110, 700], [210, 800]]
