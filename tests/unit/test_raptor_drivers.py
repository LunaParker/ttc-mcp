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


def test_arrive_by_steps_back_to_earlier_route_sequences_latest_departure_first(conn):
    tt = raptor.Timetable.load(conn, "S1000538")
    q = query(conn, tt, CHERRY_FRONT, "Union", NOW)
    target = secs(9, 0)
    js = raptor.plan_arrive_by(q, target, 30 * 60)
    assert len(js) >= 2 and all(j.arrive <= target for j in js) and all(a.depart > b.depart for a, b in zip(js, js[1:], strict=False))
    # The 08:30 bus and subway option only exists in the step-back; ranked by arrival it would lead, so this order is the departure order.
    assert [(j.depart, j.arrive, j.transfers) for j in js] == [(secs(8, 38, 36), secs(8, 56, 30), 0), (secs(8, 32, 28), secs(8, 56, 30), 0), (secs(8, 30, 2), secs(8, 49, 47), 1)]
    assert len({raptor.journey_key(q, j) for j in js}) == len(js)


def synthetic(depart, arrive, rides=1, walk_m=0, basis="schedule"):
    walk = [raptor.Leg("walk", None, "A", depart, depart, metres=walk_m)] if walk_m else []
    return raptor.Journey(depart, arrive, tuple(walk + [raptor.Leg("ride", "A", "B", depart, arrive, basis=basis) for _ in range(rides)]))


def test_rank_orders_by_arrival_then_transfers_then_walking_and_latest_by_departure():
    early, far, near, change = synthetic(50, 800, rides=3, walk_m=900), synthetic(100, 900, walk_m=300), synthetic(200, 900, walk_m=100), synthetic(300, 900, rides=2)
    assert raptor.rank([change, far, early, near]) == [early, near, far, change] == raptor.rank([near, early, change, far])
    a, b, c, d, est = synthetic(500, 900), synthetic(400, 700), synthetic(450, 1000, rides=2), synthetic(500, 950, rides=2), synthetic(900, 950, basis="estimate")
    assert raptor.rank([b, c, d, est, a], latest=True) == [a, d, c, b, est]
    assert raptor.rank([synthetic(500, 900, walk_m=50), a], latest=True) == [a, synthetic(500, 900, walk_m=50)]


def test_arrive_by_floor_never_runs_a_search_or_a_departure_before_it(conn, monkeypatch):
    tt = raptor.Timetable.load(conn, "S1000538")
    q = query(conn, tt, "Finch", "Union", NOW)
    assert [(j.depart, j.arrive) for j in raptor.plan_arrive_by(q, secs(8, 10), 30 * 60)] == [(secs(7, 39, 42), secs(8, 9, 47))]
    seen, real = [], raptor.plan
    monkeypatch.setattr(raptor, "plan", lambda q, t=None: seen.append(t) or real(q, t))
    assert raptor.plan_arrive_by(q, secs(8, 10), 30 * 60, floor=secs(8, 0)) == [] and seen == [secs(8, 0)]
    seen.clear()
    assert raptor.plan_arrive_by(q, secs(8, 10), 30 * 60, floor=secs(8, 10)) == [] and seen == []
    seen.clear()
    js = raptor.plan_arrive_by(query(conn, tt, CHERRY_FRONT, "Union", NOW), secs(9, 0), 30 * 60, floor=secs(8, 35))
    assert min(seen) >= secs(8, 35) and [(j.depart, j.arrive) for j in js] == [(secs(8, 38, 36), secs(8, 56, 30))]
