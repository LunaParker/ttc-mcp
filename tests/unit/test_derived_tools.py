import datetime as dt

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.mockttc import make_app
from ttc_mcp.domain import live, planner, raptor
from ttc_mcp.static.schedule import TZ

CHERRY_FRONT = {"lat": 43.6527, "lon": -79.3581}
NIGHT = dt.datetime(2026, 10, 6, 23, 30, tzinfo=TZ)
TOOLS = {"route_shape", "accessibility", "connections"}
OPTION_KEYS = {"rank", "transfers", "depart_at", "arrive_at", "duration_min", "walk_m", "stops", "basis", "estimate", "disruptions", "legs"}
RIDE_KEYS = {"route", "direction_id", "headsign", "transit", "pattern_id", "trip_id", "board", "alight", "stops", "depart", "arrive", "min", "typical_min", "basis"}


def rides(o):
    return [l for l in o["legs"] if l["mode"] == "ride"]


def minutes_between(a: str, b: str) -> int:
    return int((dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).total_seconds() // 60)


def no_variants(monkeypatch):
    """The variant builder drops every entity, so detour 9001 falls back to decision h's stop-level mask."""
    from ttc_mcp import overlay
    real = overlay.detour_variants
    monkeypatch.setattr(overlay, "detour_variants", lambda conn, tt, entities, day, **kw: real(conn, tt, [], day, **kw))
    overlay.reset()


@pytest.mark.anyio
async def test_lists_the_derived_tools(app_env):
    async with Client(make_app([])) as c:
        tools = {t.name: t for t in await c.list_tools()}
    assert set(tools) >= TOOLS and "upcoming" in tools["accessibility"].description and "queried_entrance" in tools["accessibility"].description


@pytest.mark.anyio
async def test_route_shape_and_accessibility_tools(app_env):
    async with Client(make_app([])) as c:
        s = (await c.call_tool("route_shape", {"route": "504", "direction": "0"})).data
        assert s["shape_id"] == "shp-504-04" and len(s["stops"]) == 36 and s["meta"]["cache"] == "static"
        g = (await c.call_tool("route_shape", {"route": "504", "direction": "0", "pattern": 2, "from_stop": "4569", "to_stop": "4573", "format": "geojson"})).data
        assert g["geometry"]["type"] == "LineString" and [x["number"] for x in g["stops"]] == ["4569", "15039", "14994", "14993", "4573"]
        assert "error" in (await c.call_tool("route_shape", {"route": "504", "direction": "0", "pattern": 9})).data
        q = (await c.call_tool("accessibility", {"query": "Queen"})).data["accessibility"]
        assert any(u["code"] == "9Y1L" and u["status"] == "in_service" for u in q["units"]) and q["step_free"]["verdict"] in ("yes", "unknown")
        sg = (await c.call_tool("accessibility", {"query": "St George"})).data
        assert any(u["code"] == "1Y1L" and u["node_match"] is False and u["alert_id"] == "77513" for u in sg["accessibility"]["units"])
        # The Queen call fetched the advisories; this second call reads them inside the 25 s alerts TTL.
        assert sg["accessibility"]["step_free"]["verdict"] == "no" and sg["meta"]["advisories"]["cache"] == "ttl"
        assert (await c.call_tool("accessibility", {"query": "13863"})).data["queried_platform"] == "13863"
        assert (await c.call_tool("accessibility", {"query": "15461"})).data["accessibility"]["stop"]["number"] == "15461"


@pytest.mark.anyio
async def test_connections_answer_the_contract_from_cherry_front_to_union(app_env):
    calls = []
    async with Client(make_app(calls)) as c:
        r = (await c.call_tool("connections", {"origin": CHERRY_FRONT, "destination": "Union"})).data
    assert r["from"] == CHERRY_FRONT and r["to"] == {"station": "15772", "name": "Union"} and r["depart_at"] == "2026-10-06T08:00-04:00" and r["window_min"] == 30
    assert [o["rank"] for o in r["options"]] == list(range(1, len(r["options"]) + 1)) and 2 <= len(r["options"]) <= 4 and "note" not in r
    for o in r["options"]:
        assert set(o) == OPTION_KEYS and o["transfers"] == len([l for l in o["legs"] if l["mode"] in ("ride", "shuttle")]) - 1
        assert o["estimate"] is False and o["basis"] == "schedule" and o["duration_min"] == minutes_between(o["depart_at"], o["arrive_at"])
        assert all(l["depart"] <= l["arrive"] for l in o["legs"]) and all(a["arrive"] <= b["depart"] for a, b in zip(o["legs"], o["legs"][1:], strict=False))
        assert all(RIDE_KEYS <= set(l) and "board_pos" not in l and "alight_pos" not in l and "slice" not in l for l in rides(o))
        assert all({"mode", "distance_m", "min", "depart", "arrive"} <= set(l) for l in o["legs"] if l["mode"] == "walk")
    assert [o["arrive_at"] for o in r["options"]] == sorted(o["arrive_at"] for o in r["options"])
    direct = next(o for o in r["options"] if [l["route"] for l in rides(o)] == ["121"])
    assert direct["transfers"] == 0 and direct["legs"][0]["mode"] == "walk" and rides(direct)[0]["transit"] == "bus"
    two = next(o for o in r["options"] if [l["route"] for l in rides(o)] == ["504", "1"])
    first, second = rides(two)
    assert (first["board"]["number"], first["trip_id"], first["depart"], first["arrive"], first["stops"]) == ("4144", "107845020", "2026-10-06T08:06-04:00", "2026-10-06T08:16-04:00", 6)
    assert "next" in first and first["next_note"] == "BusTime: Stop not found; times are from the timetable" and "next" not in second
    xfer = next(l for l in two["legs"] if l["mode"] == "transfer")
    assert set(xfer) == {"mode", "from", "to", "distance_m", "walk_min", "via", "depart", "arrive", "min"}
    assert (xfer["from"]["number"], xfer["to"]["number"], xfer["distance_m"], xfer["walk_min"], xfer["via"]) == ("15637", "13814", 98, 2, "walk") and xfer["min"] >= 3
    assert (second["board"]["number"], second["alight"]["number"], second["stops"], second["transit"]) == ("13814", "13815", 1, "subway")
    assert r["meta"]["cache"] == "live" and r["meta"]["source"].startswith("static timetable") and r["meta"]["advisories"]["cache"] == "live"
    assert any("getStopPredictions" in u for u in calls)


@pytest.mark.anyio
async def test_connections_from_a_stop_start_with_a_ride_and_honour_limit(app_env):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "15461", "destination": "Union", "limit": 2})).data
    assert 1 <= len(r["options"]) <= 2 and all(o["legs"][0]["mode"] == "ride" and o["walk_m"] >= 0 for o in r["options"])
    assert r["from"] == {"stop": "15461", "name": "Cherry St at Front St East"}


@pytest.mark.anyio
async def test_connections_need_no_ride_when_both_ends_share_a_stop(app_env):
    calls = []
    async with Client(make_app(calls)) as c:
        r = (await c.call_tool("connections", {"origin": CHERRY_FRONT, "destination": "15461"})).data
        assert r["options"] == [] and "no ride" in r["note"] and r["to"] == {"stop": "15461", "name": "Cherry St at Front St East"}
        assert r["meta"]["cache"] == "static"
    assert not any("alerts.ttc.ca" in u for u in calls)


@pytest.mark.anyio
async def test_connections_keep_next_rows_in_the_leg_direction(app_env, monkeypatch):
    async with Client(make_app([])) as c:
        # The 504 leg from 15461 runs GTFS direction 1; BusTime's East 504A rows there map to direction 0.
        r = (await c.call_tool("connections", {"origin": "15461", "destination": "Union"})).data
        opt = next(o for o in r["options"] if rides(o)[0]["route"] == "504")
        ride = rides(opt)[0]
        assert (ride["direction_id"], ride["basis"], opt["basis"]) == ("1", "schedule", "schedule")
        assert [(n["basis"], n["headsign"]) for n in ride["next"]] == [("schedule", "West - 504 King towards Distillery")]
        assert ride["next"][0]["scheduled_at"].startswith("2026-10-06T08:04") and ride["next_note"].startswith("no real-time prediction in this direction")
        real = live.arrivals_at

        async def unknown_first(feed, rt, row, **kw):
            rows, hits, source, note = await real(feed, rt, row, **kw)
            rows[0]["direction_id"] = None
            return rows, hits, source, note
        monkeypatch.setattr(live, "arrivals_at", unknown_first)
        # Next rows start at the ride's own departure, so plan from 07:45 (the westbound 504 leaves at 07:48) to keep 4617, due at 08:02.
        r = (await c.call_tool("connections", {"origin": "15461", "destination": "Union", "depart_at": "2026-10-06T07:45"})).data
        ride = rides(next(o for o in r["options"] if rides(o)[0]["route"] == "504"))[0]
        assert [n["vehicle_id"] for n in ride["next"]] == ["4617"] and "next_note" not in ride and ride["next"][0]["basis"] == "realtime"
        assert ride["next"][0]["crowding"]["label"] == "Busy" and ride["next"][0]["headsign"] == "East - 504A King towards Distillery"


@pytest.mark.anyio
async def test_connections_take_live_rows_only_within_an_hour_of_the_clock(app_env):
    async with Client(make_app([])) as c:
        near = (await c.call_tool("connections", {"origin": "13863", "destination": "Finch", "depart_at": "2026-10-06T08:30"})).data
        ride = rides(near["options"][0])[0]
        assert near["depart_at"] == "2026-10-06T08:30-04:00" and near["meta"]["cache"] == "live" and near["meta"]["source"].startswith("static timetable")
        # NTAS has trains at 0, 4 and 5 minutes from the 08:00 clock, all before 08:30: the timetable fills in from 08:30.
        assert ride["next"][0]["scheduled_at"] >= "2026-10-06T08:30" and ride["depart"] >= "2026-10-06T08:30"
        assert ride["next_note"] == "no real-time prediction in this direction from the planned departure on; times are from the timetable"
        soon = (await c.call_tool("connections", {"origin": "13863", "destination": "Finch", "depart_at": "2026-10-06T08:03"})).data
        assert [n["eta_min"] for n in rides(soon["options"][0])[0]["next"]] == [4, 5] and rides(soon["options"][0])[0]["next"][0]["basis"] == "realtime"
        far = (await c.call_tool("connections", {"origin": "13863", "destination": "Finch", "depart_at": "2026-10-06T09:01"})).data
        ride = rides(far["options"][0])[0]
        assert ride["next"][0]["scheduled_at"] >= "2026-10-06T09:01" and far["meta"]["cache"] == "static" and far["meta"]["advisories"]["source"]


@pytest.mark.anyio
async def test_connections_answer_arrive_by_latest_departure_first(app_env):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "Finch", "destination": "Union", "arrive_by": "2026-10-06T10:00"})).data
    assert r["arrive_by"] == "2026-10-06T10:00-04:00" and "depart_at" not in r and "window_min" not in r and r["options"]
    assert all(o["arrive_at"] <= "2026-10-06T10:00" for o in r["options"]) and r["options"][0]["depart_at"] >= r["options"][-1]["depart_at"]
    assert [o["rank"] for o in r["options"]] == list(range(1, len(r["options"]) + 1)) and r["meta"]["cache"] == "static"
    assert all(rides(o)[0]["next"][0]["scheduled_at"] >= rides(o)[0]["depart"][:16] for o in r["options"])


@pytest.mark.anyio
async def test_connections_arrive_by_go_live_on_the_options_departure_not_the_target(app_env):
    async with Client(make_app([])) as c:
        # 75 minutes away, but the train from Bloor-Yonge that the search settles on leaves at 08:48 (the last that makes it, 08:51, is within
        # its five minutes), inside the hour.
        r = (await c.call_tool("connections", {"origin": "13863", "destination": "Finch", "arrive_by": "2026-10-06T09:15"})).data
    ride = rides(r["options"][0])[0]
    assert ride["depart"] == "2026-10-06T08:48-04:00" and r["meta"]["cache"] == "live" and r["meta"]["source"].startswith("static timetable")
    assert ride["next"][0]["scheduled_at"] >= "2026-10-06T08:48" and ride["next_note"] == "no real-time prediction in this direction from the planned departure on; times are from the timetable"


@pytest.mark.anyio
async def test_connections_wait_past_the_window_for_the_first_trip(app_env):
    """The window steps the start, not the departures: at 02:00 the first 121 leaves at 06:23, and its next rows start there."""
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "15246", "destination": "Union", "max_transfers": 0, "depart_at": "2026-10-06T02:00"})).data
    ride = rides(r["options"][0])[0]
    assert "note" not in r and (ride["route"], ride["depart"]) == ("121", "2026-10-06T06:23-04:00") and r["options"][0]["depart_at"] == "2026-10-06T06:23-04:00"
    assert ride["next"][0]["scheduled_at"].startswith("2026-10-06T06:23") and "next_note" not in ride and r["meta"]["cache"] == "static"


@pytest.mark.anyio
async def test_connections_plan_a_utc_time_on_the_toronto_date(app_env):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": CHERRY_FRONT, "destination": "Union", "depart_at": "2026-10-07T01:00Z"})).data
    assert r["depart_at"] == "2026-10-06T21:00-04:00" and r["options"] and r["options"][0]["depart_at"].startswith("2026-10-06T2")


@pytest.mark.anyio
async def test_connections_write_only_the_options_they_return(app_env, monkeypatch):
    written, real = [], planner.materialize
    monkeypatch.setattr(planner, "materialize", lambda *a, **k: written.append(a[3]) or real(*a, **k))
    async with Client(make_app([])) as c:
        one = (await c.call_tool("connections", {"origin": CHERRY_FRONT, "destination": "Union", "limit": 1})).data
        assert len(one["options"]) == len(written) == 1
        written.clear()
        assert len((await c.call_tool("connections", {"origin": CHERRY_FRONT, "destination": "Union"})).data["options"]) == len(written) >= 2


@pytest.mark.anyio
async def test_connections_read_each_slice_through_its_own_feed(two_feeds):
    """Pattern ids are per feed: at 00:30 on 2026-09-30 today's slice is S1000542 and yesterday's S1000538, which here numbers Line 1 southbound 1054."""
    import sqlite3

    db = sqlite3.connect(two_feeds / "s38.sqlite")
    for table in ("patterns", "pattern_stops", "trips", "raptor_groups"):
        db.execute(f"UPDATE {table} SET pattern_id = 1054 WHERE pattern_id = 54")
    db.commit()
    db.close()
    async with Client(make_app([])) as c:
        night = (await c.call_tool("connections", {"origin": "Finch", "destination": "Union", "max_transfers": 0, "depart_at": "2026-09-30T00:30"})).data
        day = (await c.call_tool("connections", {"origin": "Finch", "destination": "Union", "max_transfers": 0, "depart_at": "2026-09-30T08:00"})).data
    late, early = rides(night["options"][0])[0], rides(day["options"][0])[0]
    assert (late["pattern_id"], late["trip_id"], late["depart"]) == (1054, "134929451", "2026-09-30T00:33-04:00") and late["typical_min"] > 20
    assert early["pattern_id"] == 54 and early["typical_min"] > 20 and "slice" not in late and "slice" not in early


@pytest.mark.anyio
async def test_connections_window_zero_and_max_transfers_zero_narrow_the_answer(app_env):
    async with Client(make_app([])) as c:
        instant = (await c.call_tool("connections", {"origin": CHERRY_FRONT, "destination": "Union", "window_min": 0})).data
        direct = (await c.call_tool("connections", {"origin": CHERRY_FRONT, "destination": "Union", "max_transfers": 0})).data
    assert instant["window_min"] == 0 and 1 <= len(instant["options"]) <= 4
    assert all(a["arrive_at"] < b["arrive_at"] or a["transfers"] < b["transfers"] for a, b in zip(instant["options"], instant["options"][1:], strict=False))
    assert direct["options"] and all(o["transfers"] == 0 and [l["route"] for l in rides(o)] == ["121"] for o in direct["options"])


@pytest.mark.anyio
async def test_connections_step_a_long_window_every_10_minutes_and_arrive_by_every_15(app_env, monkeypatch):
    runs, real = [], raptor.plan
    monkeypatch.setattr(raptor, "plan", lambda q, t=None: runs.append(t) or real(q, t))
    steps, real_by = [], raptor.plan_arrive_by
    monkeypatch.setattr(raptor, "plan_arrive_by", lambda *a, **k: steps.append(k.get("step_s")) or real_by(*a, **k))
    async with Client(make_app([])) as c:
        for window, gap in ((60, 600), (30, 300)):
            runs.clear()
            r = (await c.call_tool("connections", {"origin": CHERRY_FRONT, "destination": "Union", "depart_at": "2026-10-06T08:00", "window_min": window})).data
            assert r["options"] and len(runs) == 7 and {b - a for a, b in zip(runs, runs[1:], strict=False)} == {gap}
        assert (await c.call_tool("connections", {"origin": CHERRY_FRONT, "destination": "Union", "arrive_by": "2026-10-06T09:00"})).data["options"]
    assert set(steps) == {900}


@pytest.mark.anyio
async def test_connections_explain_an_empty_answer(app_env, monkeypatch):
    async with Client(make_app([])) as c:
        # The 504 at 15461 and Line 1 at Finch both run today, but no single route joins them.
        r = (await c.call_tool("connections", {"origin": "15461", "destination": "Finch", "max_transfers": 0})).data
        assert r["options"] == [] and r["note"] == "no journey with up to 0 transfers from 2026-10-06T08:00-04:00" and "gtfsrt trips/subway" in r["meta"]["source"]
        # Only the 400 serves these two stops, and it runs Mondays, Wednesdays and Thursdays: Monday's trips are over by Tuesday 02:00.
        r = (await c.call_tool("connections", {"origin": "15547", "destination": "15602", "max_transfers": 0, "depart_at": "2026-10-06T02:00"})).data
        assert r["options"] == [] and r["note"] == "no service at the origin stops on 2026-10-06; raise walk_m or start from a stop"
        r = (await c.call_tool("connections", {"origin": "15461", "destination": "15547", "max_transfers": 0, "depart_at": "2026-10-06T02:00"})).data
        assert r["options"] == [] and r["note"] == "no service at the destination stops on 2026-10-06; raise walk_m or start from a stop"
        r = (await c.call_tool("connections", {"origin": "Finch", "destination": "Union", "arrive_by": "2026-10-06T07:30"})).data
        assert r["options"] == [] and r["note"] == "arrive_by 2026-10-06T07:30-04:00 is not after now"
        monkeypatch.setattr("ttc_mcp.runtime.now", lambda: NIGHT)
        monkeypatch.setattr(planner, "_shuttle_runs", lambda el: False)
        r = (await c.call_tool("connections", {"origin": "Finch", "destination": "Union", "max_transfers": 0})).data
        assert r["options"] == [] and r["note"] == "every journey needs a closed section with no shuttle service"
        monkeypatch.setattr("ttc_mcp.runtime.now", lambda: dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ))
        no_variants(monkeypatch)
        roncesvalles = {"lat": 43.650939, "lon": -79.450927}
        r = (await c.call_tool("connections", {"origin": roncesvalles, "destination": "4582", "walk_m": 1})).data
        assert r["options"] == [] and r["note"] == "every stop at the origin is skipped by a detour"
        r = (await c.call_tool("connections", {"origin": "4582", "destination": "15039", "walk_m": 1})).data
        assert r["options"] == [] and r["note"] == "every stop at the destination is skipped by a detour"
        r = (await c.call_tool("connections", {"origin": "Finch", "destination": "Union", "depart_at": "2026-11-15T08:00"})).data
        assert r["options"] == [] and r["note"] == "no service at the origin stops on 2026-11-15; raise walk_m or start from a stop"


@pytest.mark.anyio
async def test_connections_replace_a_detour_skipped_stop_with_the_served_stops_around_it(app_env, monkeypatch):
    no_variants(monkeypatch)
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "15039", "destination": "4582"})).data
    assert r["from"] == {"stop": "15039", "name": "Roncesvalles Ave at Howard Park Ave South Side"}
    assert r["note"] == "origin stop 15039 is skipped by detour 9001; using the served stops within 400 m of it"
    walk = r["options"][0]["legs"][0]
    assert (walk["mode"], walk["to"], walk["distance_m"], walk["min"]) == ("walk", {"number": "4569", "name": "Roncesvalles Ave at Boustead Ave South Side"}, 245, 4)
    assert [(l["route"], l["board"]["number"]) for l in rides(r["options"][0])] == [("504", "4569")]


@pytest.mark.anyio
async def test_connections_reject_what_they_cannot_plan(app_env):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "15461", "destination": "nowhere"})).data
        assert r["error"].startswith("could not resolve destination") and r["meta"]["cache"] == "static"
        far = (await c.call_tool("connections", {"origin": {"lat": 43.0, "lon": -79.0}, "destination": "Union"})).data
        assert far["error"] == "no stop within 400 m of the origin; raise walk_m or start from a stop"
        with pytest.raises(ToolError):
            await c.call_tool("connections", {"origin": "15461", "destination": "Union", "max_transfers": 4})


@pytest.mark.parametrize("args,error", [
    ({"limit": 0}, "limit must be between 1 and 10"), ({"limit": 11}, "limit must be between 1 and 10"),
    ({"walk_m": 0}, "walk_m must be between 1 and 2000"), ({"walk_m": 8000}, "walk_m must be between 1 and 2000"),
    ({"depart_at": "tomorrow at 8"}, "depart_at must be an ISO date-time such as 2026-10-06T08:30"),
    ({"arrive_by": "nine"}, "arrive_by must be an ISO date-time such as 2026-10-06T09:00"),
    ({"depart_at": "2026-10-06T08:00", "arrive_by": "2026-10-06T09:00"}, "give depart_at or arrive_by, not both"),
    ({"window_min": -1}, "window_min must be between 0 and 60"), ({"window_min": 61}, "window_min must be between 0 and 60"),
    ({"origin": {"lat": "north", "lon": -79.36}}, "origin needs numeric lat and lon"),
    ({"origin": {"lat": 43.65}}, "origin needs numeric lat and lon"),
    ({"destination": {"lat": None, "lon": -79.38}}, "destination needs numeric lat and lon")])
@pytest.mark.anyio
async def test_connections_answer_bad_inputs_with_an_error(app_env, args, error):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "15461", "destination": "Union", **args})).data
    assert r["error"] == error and r["meta"]["cache"] == "static"


@pytest.mark.anyio
async def test_connections_route_around_a_closure_without_shuttle_service(app_env, monkeypatch):
    monkeypatch.setattr("ttc_mcp.runtime.now", lambda: NIGHT)
    monkeypatch.setattr(planner, "_shuttle_runs", lambda el: False)
    # Pretend a 121 stop bound for Union is 50 m from Sheppard-Yonge's southbound platform, north of the York Mills to Eglinton closure.
    raptor.timetable_for(app_env.feed_for()).foot.setdefault("13860", []).append(("11169", 39, 50, "walk"))
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "Finch", "destination": "Union"})).data
    assert r["options"] and "note" not in r
    first, second = rides(r["options"][0])[:2]
    assert (first["route"], first["board"]["number"], first["alight"]["number"], r["options"][0]["transfers"]) == ("1", "14111", "13860", 1)
    # Leaving the 121 at The Esplanade at Yonge St and walking 122 m to 16754 beats riding on to it.
    assert (second["route"], second["board"]["number"], second["alight"]["number"]) == ("121", "11169", "12425")
    assert {d["alert_id"] for d in r["options"][0]["disruptions"]} >= {"77535"} and "77519" not in {d.get("alert_id") for d in r["options"][0]["disruptions"]}


@pytest.mark.anyio
async def test_connections_plan_off_the_event_loop_on_the_workers_connection(app_env, monkeypatch):
    """Spec 6.9 carry-over: the plan must not stall the single instance's other requests, and SQLite forbids a connection in two threads."""
    import threading

    seen, used = [], []
    real_plan, real_mat = raptor.plan_window, planner.materialize

    def spy_plan(*a, **k):
        seen.append(threading.get_ident())
        return real_plan(*a, **k)

    def spy_mat(conn, *a, **k):
        used.append((threading.get_ident(), conn))
        return real_mat(conn, *a, **k)
    monkeypatch.setattr(raptor, "plan_window", spy_plan)
    monkeypatch.setattr(planner, "materialize", spy_mat)
    async with Client(make_app([])) as c:
        for extra in ({}, {"depart_at": "2026-10-06T08:00"}):
            assert (await c.call_tool("connections", {"origin": CHERRY_FRONT, "destination": "Union", **extra})).data["options"]
        loop_conn = app_env.feed_for().conn
    assert seen and all(t != threading.get_ident() for t in seen)
    assert used and all(t != threading.get_ident() and conn is not loop_conn for t, conn in used)


@pytest.mark.anyio
async def test_route_shape_needs_both_ends_of_a_span(app_env):
    async with Client(make_app([])) as c:
        for half in ({"from_stop": "4569"}, {"to_stop": "4573"}):
            r = (await c.call_tool("route_shape", {"route": "504", "direction": "0", **half})).data
            assert r["error"] == "give both from_stop and to_stop for a span, or neither"


def test_disruptions_query_through_the_calling_threads_connection(fixture_db, conn):
    import threading

    from ttc_mcp.adapters.ttc_alerts import AlertList
    from ttc_mcp.domain.disruptions import Disruptions
    from ttc_mcp.static.store import FeedDb

    feed = FeedDb("S1000538", dt.date(2026, 9, 1), dt.date(2026, 10, 31), fixture_db)
    d = Disruptions.build(lambda: feed.conn, AlertList(None, None, []), None, [], NIGHT)
    seen = {}
    t = threading.Thread(target=lambda: seen.update(disr=d.conn, own=feed.conn))
    t.start()
    t.join()
    assert d.conn is feed.conn and seen["disr"] is seen["own"] and seen["disr"] is not d.conn
    assert Disruptions.build(conn, AlertList(None, None, []), None, [], NIGHT).conn is conn
    feed.close()


@pytest.mark.anyio
async def test_connections_apply_subway_trip_updates_within_the_live_window(app_env):
    calls = []
    async with Client(make_app(calls)) as c:
        # The mock's subway feed says the 08:18 train from King leaves at 08:19 (tests/mockttc.SUBWAY_TRIPS).
        r = (await c.call_tool("connections", {"origin": "13814", "destination": "Union", "depart_at": "2026-10-06T08:18:30", "window_min": 0, "max_transfers": 0})).data
    ride = rides(r["options"][0])[0]
    assert (ride["trip_id"], ride["depart"], ride["arrive"], ride["basis"], r["options"][0]["basis"]) == ("134929152", "2026-10-06T08:19-04:00", "2026-10-06T08:21-04:00", "realtime", "realtime")
    assert "gtfsrt trips/subway" in r["meta"]["source"] and r["meta"]["cache"] == "live" and any(u.endswith("/trips/subway?format=binary") for u in calls)


@pytest.mark.anyio
async def test_connections_apply_surface_trip_updates_through_the_crosswalk(app_env, conn, monkeypatch):
    from tests import mockttc
    surface = conn.execute("SELECT surface_stop_id FROM crosswalk WHERE stop_code='15637'").fetchone()[0]
    monkeypatch.setattr(mockttc, "EXTRA_TRIPS", [{"trip": "107845020", "route": "504", "stops": [(surface, 9, mockttc.PINNED + 18 * 60)]}])
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": CHERRY_FRONT, "destination": "Union"})).data
    two = next(o for o in r["options"] if [l["route"] for l in rides(o)] == ["504", "1"])
    first = rides(two)[0]
    assert (first["trip_id"], first["arrive"], first["basis"], two["basis"]) == ("107845020", "2026-10-06T08:18-04:00", "realtime", "realtime") and first["min"] == 12
    assert next(l for l in two["legs"] if l["mode"] == "transfer")["depart"] == "2026-10-06T08:18-04:00"


@pytest.mark.anyio
async def test_connections_fetch_no_trip_feeds_outside_the_live_window(app_env):
    calls = []
    async with Client(make_app(calls)) as c:
        r = (await c.call_tool("connections", {"origin": "13814", "destination": "Union", "depart_at": "2026-10-06T10:00"})).data
    assert r["options"] and all(l["basis"] == "schedule" for o in r["options"] for l in rides(o)) and r["meta"]["cache"] == "static"
    assert not any("/gtfsrt/trips" in u or "/trips/subway" in u for u in calls)


@pytest.mark.anyio
async def test_connections_cross_a_night_closure_on_an_estimated_shuttle(app_env, monkeypatch):
    monkeypatch.setattr("ttc_mcp.runtime.now", lambda: NIGHT)
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "Finch", "destination": "Union"})).data
    o = r["options"][0]
    assert [l["mode"] for l in o["legs"]] == ["ride", "transfer", "shuttle", "transfer", "ride"] and o["transfers"] == 2 and o["estimate"] is True and o["basis"] == "schedule"
    ride, _, shuttle, _, ride2 = o["legs"]
    assert ride["alight"]["number"] == "13792" and ride2["board"]["number"] == "13795" and ride2["basis"] == "schedule"
    assert set(shuttle) == {"mode", "line", "from", "to", "depart", "arrive", "min", "basis", "alert_id", "note"}
    # 1,166 s from 23:48:46 (the train's 23:40:02 at York Mills, the 104 s pathway, the buffer and the headway) to 00:08:12: the clocks read 20 minutes.
    assert (shuttle["line"], shuttle["from"], shuttle["to"], shuttle["min"], shuttle["basis"], shuttle["alert_id"]) == ("1", {"station": "43276", "name": "York Mills"}, {"station": "43274", "name": "Eglinton"}, 20, "estimate", "77519")
    assert shuttle["note"].startswith("assumed 5-minute headway and 15 km/h over 4.4 km of track")
    assert next(d for d in o["disruptions"] if d.get("alert_id") == "77519")["affects"] == [3] and o["duration_min"] == minutes_between(o["depart_at"], o["arrive_at"])
    assert all(x["estimate"] for x in r["options"]) or r["options"].index(next(x for x in r["options"] if x["estimate"])) > 0


@pytest.mark.anyio
async def test_connections_board_the_next_undetoured_trip_to_a_skipped_stop(app_env):
    async with Client(make_app([])) as c:
        to_skipped = (await c.call_tool("connections", {"origin": "4569", "destination": "14994", "depart_at": "2026-10-06T10:50", "window_min": 0})).data
        through = (await c.call_tool("connections", {"origin": "4569", "destination": "4573", "depart_at": "2026-10-06T10:50", "window_min": 0})).data
    ride = rides(to_skipped["options"][0])[0]
    assert (ride["trip_id"], ride["depart"]) == ("97799020", "2026-10-06T11:08-04:00") and "note" not in to_skipped
    ride = rides(through["options"][0])[0]
    assert (ride["trip_id"], ride["stops"], ride["depart"], ride["arrive"]) == ("100677020", 1, "2026-10-06T10:58-04:00", "2026-10-06T11:05-04:00") and "detour_id" not in ride
    assert next(d for d in through["options"][0]["disruptions"] if d.get("detour_id") == "9001")["affects"] == [1]


@pytest.mark.anyio
async def test_connections_after_midnight_apply_the_prediction_of_yesterdays_late_train(app_env, monkeypatch):
    from tests import mockttc
    monkeypatch.setattr("ttc_mcp.runtime.now", lambda: dt.datetime(2026, 10, 7, 0, 30, tzinfo=TZ))
    # Trip 134929451 runs on Tuesday's service day (service 1) and leaves Finch at 24:33:22, 00:33 on Wednesday: yesterday's train at 00:30.
    late = int(dt.datetime(2026, 10, 7, 0, 36, 22, tzinfo=TZ).timestamp())
    monkeypatch.setattr(mockttc, "SUBWAY_TRIPS", [{"trip": "134929451", "route": "1", "stops": [("14111", 1, late)]}])
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "14111", "destination": "13792", "max_transfers": 0, "window_min": 0})).data
    ride = rides(r["options"][0])[0]
    assert (ride["trip_id"], ride["depart"], ride["arrive"], ride["basis"]) == ("134929451", "2026-10-07T00:36-04:00", "2026-10-07T00:43-04:00", "realtime")


@pytest.mark.anyio
async def test_connections_board_a_variant_backed_origin_stop_on_the_next_undetoured_trip(app_env):
    async with Client(make_app([])) as c:
        # 9001 has a variant, so 15039 keeps its place: the detoured 100677020 would pass at 11:00, the next undetoured trip leaves at 11:10.
        r = (await c.call_tool("connections", {"origin": "15039", "destination": "4573", "depart_at": "2026-10-06T10:55", "window_min": 0, "max_transfers": 0})).data
    ride = r["options"][0]["legs"][0]
    assert "note" not in r and r["from"]["stop"] == "15039" and ride["mode"] == "ride" and ride["board"]["number"] == "15039"
    assert (ride["trip_id"], ride["depart"], ride["arrive"]) == ("97799020", "2026-10-06T11:10-04:00", "2026-10-06T11:15-04:00")


@pytest.mark.anyio
async def test_connections_name_only_the_trip_feeds_that_answered(app_env):
    from ttc_mcp.tools.derived_tools import SOURCE_LIVE
    async with Client(make_app([])) as c:
        both = (await c.call_tool("connections", {"origin": "13814", "destination": "Union"})).data
    async with Client(make_app([], fail=("gtfsrt.ttc.ca",))) as c:
        r = (await c.call_tool("connections", {"origin": "13814", "destination": "Union"})).data
    assert both["meta"]["source"] == SOURCE_LIVE and r["options"] and r["meta"]["source"] == "static timetable + bustime trips + bustime getStopPredictions.jsp + ntas"
