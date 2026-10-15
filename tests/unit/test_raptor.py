import dataclasses
import datetime as dt
import threading

from ttc_mcp.domain import geo, planner, raptor
from ttc_mcp.static import schedule
from ttc_mcp.static.manifest import Manifest
from ttc_mcp.static.schedule import TZ

NOW = dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ)
CHERRY_FRONT = {"lat": 43.6527, "lon": -79.3581}


def test_walk_seconds_follows_the_walk_minutes_pace():
    assert geo.walk_seconds(98) == 96 and geo.walk_seconds(0) == 0 and geo.walk_minutes(400) == 6 and geo.walk_seconds(400) == 390


def test_timetable_loads_every_group_with_sorted_columns_and_indexes_stops(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    assert tt.version == "S1000538" and sum(len(g) for g in tt.groups.values()) == conn.execute("SELECT count(*) FROM raptor_groups").fetchone()[0]
    assert any(p == 3 for p, _ in tt.stop_patterns["15246"])
    assert len(tt.pattern_stops[3]) == 17 and (tt.pattern_meta[3]["route"], tt.pattern_meta[3]["direction_id"], tt.pattern_meta[3]["mode"]) == ("121", "1", "bus")
    g = tt.groups[3][0]
    assert g.pattern == 3 and len(g.cols) == 17 and all(list(c) == sorted(c) for c in g.cols) and g.n_trips == len(g.trip_ids) and not g.skip and not g.realtime


def test_trip_slot_finds_the_trip_and_its_departure_in_the_column(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    pattern, gi, ti = tt.trip_slot(conn, "22650020")
    pos = tt.pattern_stops[3].index("15246")
    assert pattern == 3 and tt.groups[3][gi].trip_ids[ti] == 22650020 and tt.groups[3][gi].cols[pos][ti] == 8 * 3600 + 11 * 60 + 12
    assert tt.trip_slot(conn, "999999999") is None


def test_footpaths_join_transfers_and_station_members_and_prefer_pathway_seconds(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    by = {to: (secs, m, via) for to, secs, m, via in tt.foot["15637"]}
    assert by["13814"] == (96, 98, "walk") and by["13813"] == (geo.walk_seconds(76), 76, "walk") and by["15640"] == (geo.walk_seconds(93), 93, "walk")
    bloor = {to: (secs, m, via) for to, secs, m, via in tt.foot["13863"]}
    assert bloor["13864"] == (56, 119, "pathways")
    union = {to: (secs, m, via) for to, secs, m, via in tt.foot["13815"]}
    assert union["16754"] == (geo.walk_seconds(304), 304, "walk") and union["13816"][2] in ("pathways", "walk")
    assert all(secs > 0 and m > 0 for to, secs, m, via in tt.foot["13815"] if to != "13815")


def test_sibling_platforms_walk_zero_only_when_the_feed_puts_them_at_one_point(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    xy = {sid: (lat, lon) for sid, lat, lon in conn.execute("SELECT stop_id, lat, lon FROM stops")}
    parents: dict[str, list[str]] = {}
    for st, sid in conn.execute("SELECT station_id, stop_id FROM station_members WHERE how='parent'"):
        parents.setdefault(st, []).append(sid)
    pairs = [(a, b) for mem in parents.values() for a in mem for b in mem if a != b]
    foot = {a: {to: (secs, m) for to, secs, m, via in edges} for a, edges in tt.foot.items()}
    assert pairs and not [p for p in pairs if p[1] not in foot.get(p[0], {})]
    assert not [p for p in pairs if foot[p[0]][p[1]][0] == 0 and foot[p[0]][p[1]][1] != 0]
    assert not [p for p in pairs if foot[p[0]][p[1]][1] == 0 and xy[p[0]] != xy[p[1]]]


def test_timetable_for_caches_per_feed_and_reset_drops_it(app_env):
    feed = app_env.feed_for()
    a = raptor.timetable_for(feed)
    seen = []
    t = threading.Thread(target=lambda: seen.append(raptor.timetable_for(feed)))
    t.start()
    t.join()
    assert seen[0] is a and raptor.timetable_for(feed) is a
    raptor.reset()
    assert raptor.timetable_for(feed) is not a
    app_env.reset()
    assert not raptor._cache


def test_get_store_reopen_on_a_feed_change_drops_the_timetable_cache(app_env):
    raptor.timetable_for(app_env.feed_for())
    assert raptor._cache
    cache = app_env.settings().cache_dir
    m = Manifest.load(cache)
    m.upsert(dataclasses.replace(m.feeds[0], etag='"republished"'))
    m.save(cache)
    app_env.get_store()
    assert not raptor._cache


def secs(h, m, s=0):
    return h * 3600 + m * 60 + s


def query(conn, tt, origin, dest, when, **kw):
    day = when.date()
    t0 = int((when - schedule.wall_clock(day, 0)).total_seconds())
    yday = day - dt.timedelta(days=1)
    slices = [raptor.Slice(tt, frozenset(schedule.service_ids_on(conn, day))),
              raptor.Slice(tt, frozenset(schedule.service_ids_on(conn, yday)), raptor.day_offset(yday, day))]
    eo, ed = planner.resolve_endpoint(conn, origin), planner.resolve_endpoint(conn, dest)
    ends = [{s: (geo.walk_seconds(m), m) for s, m in e.stops.items()} for e in (eo, ed)]
    return raptor.Query(ends[0], ends[1], t0, slices, **kw)


def trip_of(q, leg):
    g = q.slices[leg.slice_idx].groups_for(leg.pattern)[leg.group]
    return g.trip_ids[leg.trip]


def test_day_offset_is_a_day_in_seconds_except_across_a_clock_change():
    assert raptor.day_offset(dt.date(2026, 10, 5), dt.date(2026, 10, 6)) == -86400
    assert raptor.day_offset(dt.date(2026, 10, 31), dt.date(2026, 11, 1)) == -90000  # DST ends on 2026-11-01


def test_closed_spans_block_the_interior_keep_the_ends_and_run_through_a_lone_station():
    c = raptor.Closed.from_spans([(3, 5)])
    assert (c.no_board, c.no_alight, c.drop_after) == (frozenset({3, 4}), frozenset({4, 5}), frozenset({3}))
    one = raptor.Closed.from_spans([(4, 4)])
    assert (one.no_board, one.no_alight, one.drop_after) == (frozenset({4}), frozenset({4}), frozenset())
    assert raptor.Closed.NONE.no_board == frozenset() and raptor.Closed.from_spans([]) == raptor.Closed.NONE


def test_a_direct_ride_boards_a_real_trip_with_exact_times(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    q = query(conn, tt, "Finch", "Union", NOW)
    js = raptor.plan(q)
    assert len(js) == 1 and [l.kind for l in js[0].legs] == ["ride"]
    ride = js[0].legs[0]
    assert (ride.frm, ride.to, ride.depart, ride.arrive, ride.basis) == ("14111", "13815", secs(8, 2, 12), secs(8, 32, 17), "schedule")
    assert trip_of(q, ride) == 134929157 and js[0].transfers == 0 and js[0].walk_m == 0 and not js[0].estimate
    assert (js[0].depart, js[0].arrive) == (secs(8, 2, 12), secs(8, 32, 17))


def test_late_evening_and_after_midnight_board_the_previous_service_days_trips(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    night = raptor.plan(query(conn, tt, "Finch", "Union", dt.datetime(2026, 10, 6, 23, 30, tzinfo=TZ)))[0]
    assert (night.legs[0].depart, night.legs[0].arrive, night.legs[0].slice_idx) == (secs(23, 33, 22), secs(24, 2, 47), 0)
    q = query(conn, tt, "Finch", "Union", dt.datetime(2026, 10, 7, 0, 30, tzinfo=TZ))
    small = raptor.plan(q)[0]
    assert (small.legs[0].depart, small.legs[0].arrive, small.legs[0].slice_idx) == (secs(0, 33, 22), secs(1, 2, 47), 1) and trip_of(q, small.legs[0]) == 134929451


def test_a_loop_pattern_rides_forward_one_stop(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    q = query(conn, tt, "11641", "15607", dt.datetime(2026, 10, 7, 10, 0, tzinfo=TZ))
    ride = raptor.plan(q)[0].legs[0]
    assert (ride.board_pos, ride.alight_pos, ride.depart, ride.arrive) == (34, 35, secs(10, 12, 54), secs(10, 13, 40)) and trip_of(q, ride) == 134929012


def test_cherry_front_to_union_is_a_pareto_set_with_a_timed_transfer(conn, monkeypatch):
    tt = raptor.Timetable.load(conn, "S1000538")
    q = query(conn, tt, CHERRY_FRONT, "Union", NOW)
    js = raptor.plan(q)
    assert [j.transfers for j in js] == sorted(j.transfers for j in js) and all(a.arrive > b.arrive for a, b in zip(js, js[1:], strict=False))
    direct = next(j for j in js if j.transfers == 0)
    assert q.slices[0].meta_of(direct.rides[0].pattern)["route"] == "121" and direct.arrive <= secs(8, 30) and direct.legs[0].kind == "walk"
    two = next(j for j in js if j.transfers == 1)
    first, second = two.rides
    assert (q.slices[0].meta_of(first.pattern)["route"], first.frm, first.to, first.depart, first.arrive) == ("504", "4144", "15637", secs(8, 6, 9), secs(8, 16, 21))
    assert trip_of(q, first) == 107845020 and second.frm == "13814" and second.to == "13815"
    walk = two.legs[two.legs.index(first) + 1]
    assert (walk.kind, walk.frm, walk.to, walk.metres, walk.via) == ("walk", "15637", "13814", 98, "walk")
    # The 2-minute change buffer: the 08:18:17 train leaves 20 seconds after the 96-second walk ends, so the planner takes the 08:20:47 one.
    assert second.depart - first.arrive >= 96 + raptor.CHANGE_S and trip_of(q, second) != 134929152
    monkeypatch.setattr(raptor, "CHANGE_S", 0)
    relaxed = next(j for j in raptor.plan(q) if j.transfers == 1)
    assert trip_of(q, relaxed.rides[1]) == 134929152 and relaxed.arrive < two.arrive
    assert raptor.journey_key(q, two) == (("504", "4144", "15637"), ("1", "13814", "13815"))


def test_max_transfers_and_unusable_stops_bound_the_search(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    only = raptor.plan(query(conn, tt, CHERRY_FRONT, "Union", NOW, max_transfers=0))
    assert len(only) == 1 and only[0].transfers == 0
    js = raptor.plan(query(conn, tt, CHERRY_FRONT, "Union", NOW, usable=lambda s: s != "4144"))
    assert js and not any("4144" in (l.frm, l.to) for j in js for l in j.legs)
    assert raptor.plan(query(conn, tt, "Finch", "Union", NOW, usable=lambda s: s != "14111")) == []


def test_closed_spans_cut_the_scan_at_the_ends_of_a_closure(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    cut = raptor.Closed.from_spans([(3, 5)])  # Line 1 southbound: York Mills, Lawrence, Eglinton
    closed = lambda pid: cut if pid == 54 else raptor.Closed.NONE  # noqa: E731
    assert raptor.plan(query(conn, tt, "Finch", "Union", NOW, max_transfers=0, closed=closed)) == []
    to_york_mills = raptor.plan(query(conn, tt, "Finch", "York Mills", NOW, max_transfers=0, closed=closed))
    assert to_york_mills and to_york_mills[0].legs[-1].to == "13792"
    from_eglinton = raptor.plan(query(conn, tt, "Eglinton", "Union", NOW, max_transfers=0, closed=closed))
    assert from_eglinton and from_eglinton[0].rides[0].frm == "13795"
    assert raptor.plan(query(conn, tt, "Lawrence", "Union", NOW, max_transfers=0, closed=closed)) == []
    lone = lambda pid: raptor.Closed.from_spans([(4, 4)]) if pid == 54 else raptor.Closed.NONE  # noqa: E731
    assert raptor.plan(query(conn, tt, "Finch", "Union", NOW, max_transfers=0, closed=lone)) and raptor.plan(query(conn, tt, "Finch", "Lawrence", NOW, max_transfers=0, closed=lone)) == []


def test_no_service_means_no_journey(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    q = query(conn, tt, "Finch", "Union", NOW)
    q.slices = [raptor.Slice(tt, frozenset()), raptor.Slice(tt, frozenset(), -86400)]
    assert raptor.plan(q) == []
