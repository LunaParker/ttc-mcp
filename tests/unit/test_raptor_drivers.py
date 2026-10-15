from tests.unit.test_raptor import CHERRY_FRONT, NOW, query, secs
from ttc_mcp.domain import raptor


def test_a_window_adds_later_departures_once_per_route_sequence_ranked_by_arrival(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    q = query(conn, tt, CHERRY_FRONT, "Union", NOW)
    js = raptor.plan_window(q, 30 * 60)
    keys = [raptor.journey_key(q, j) for j in js]
    assert len(js) >= 2 and len(keys) == len(set(keys)) and js == raptor.rank(js)
    assert all(a.arrive <= b.arrive for a, b in zip(js, js[1:], strict=False)) and all(secs(8, 0) <= j.depart <= secs(8, 30) + 600 for j in js)
    every = [j for t in range(q.t0, q.t0 + 30 * 60 + 1, 300) for j in raptor.plan(q, t)]
    for j in js:
        assert j.depart == min(x.depart for x in every if raptor.journey_key(q, x) == raptor.journey_key(q, j))
    assert raptor.plan_window(q, 0) == raptor.rank(raptor.plan(q))


def test_arrive_by_finds_the_latest_departure_that_makes_it(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    q = query(conn, tt, "Finch", "Union", NOW)
    target = secs(9, 0)
    js = raptor.plan_arrive_by(q, target, 30 * 60)
    assert js and all(j.arrive <= target for j in js) and js == raptor.rank(js, latest=True) and js[0].depart >= js[-1].depart
    later = raptor.plan(q, js[0].depart + 60)
    assert not later or all(j.arrive > target for j in later)
    # The fixture's last night trip leaves at 01:20 and the first morning one arrives at 06:01, so 05:30 has nothing in the four hours before it.
    assert raptor.plan_arrive_by(q, secs(5, 30), 30 * 60) == []


def test_rank_puts_estimates_last_and_dedupe_keeps_the_latest_when_asked(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    q = query(conn, tt, "Finch", "Union", NOW)
    a = raptor.plan(q)[0]
    est = raptor.Journey(a.depart - 600, a.arrive - 600, (raptor.Leg("shuttle", "14111", "13815", a.depart - 600, a.arrive - 600, pattern=a.legs[0].pattern, slice_idx=0, basis="estimate"),))
    assert raptor.rank([est, a]) == [a, est]
    b = raptor.plan(q, a.depart + 1)[0]
    assert raptor.dedupe(q, [a, b]) == [a] and raptor.dedupe(q, [a, b], latest=True) == [b]
