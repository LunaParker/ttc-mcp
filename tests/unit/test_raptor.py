import dataclasses
import datetime as dt
import threading

from ttc_mcp.domain import geo, raptor
from ttc_mcp.static.manifest import Manifest
from ttc_mcp.static.schedule import TZ

NOW = dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ)


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
