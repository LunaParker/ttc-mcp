import datetime as dt

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from tests.mockttc import make_app
from ttc_mcp.domain import live
from ttc_mcp.static.schedule import TZ

CHERRY_FRONT = {"lat": 43.6527, "lon": -79.3581}
NIGHT = dt.datetime(2026, 10, 6, 23, 30, tzinfo=TZ)
TOOLS = {"route_shape", "accessibility", "connections"}


@pytest.mark.anyio
async def test_lists_the_derived_tools(app_env):
    async with Client(make_app([])) as c:
        assert {t.name for t in await c.list_tools()} >= TOOLS


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
async def test_connections_from_cherry_front_to_union_with_live_first_leg(app_env):
    calls = []
    async with Client(make_app(calls)) as c:
        r = (await c.call_tool("connections", {"origin": CHERRY_FRONT, "destination": "Union"})).data
        assert r["from"] == CHERRY_FRONT and r["to"] == {"station": "15772", "name": "Union"} and r["depart_at"].startswith("2026-10-06T08:00")
        first = r["options"][0]
        ride = next(l for l in first["legs"] if l["mode"] == "ride")
        assert ride["route"] == "121" and ride["next"] and all({"eta_min", "basis", "crowding", "headsign"} <= set(n) for n in ride["next"])
        assert first["basis"] == "schedule" and ride["next"][0]["scheduled_at"] >= "2026-10-06T08:00"
        assert ride["next_note"] == "BusTime: Stop not found; times are from the timetable" and all("basis" in o for o in r["options"])
        assert first["disruptions"] == [] and r["meta"]["advisories"]["cache"] == "live" and r["meta"]["source"].startswith("static patterns")
        xfer = next(o for o in r["options"] if o["transfers"] == 1)
        rides = [l for l in xfer["legs"] if l["mode"] == "ride"]
        assert [x["route"] for x in rides] == ["504", "1"] and "next" in rides[0] and "next" not in rides[1]
        stop = (await c.call_tool("connections", {"origin": "15461", "destination": "Union", "max_transfers": 1, "limit": 2})).data
        assert len(stop["options"]) <= 2 and all(o["legs"][0]["mode"] != "walk" for o in stop["options"])
        assert "error" in (await c.call_tool("connections", {"origin": "nowhere", "destination": "Union"})).data
    assert any("getStopPredictions" in u for u in calls)


@pytest.mark.anyio
async def test_connections_apply_the_closure_overlay_at_night(app_env, monkeypatch):
    monkeypatch.setattr("ttc_mcp.runtime.now", lambda: NIGHT)
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "Finch", "destination": "Union", "max_transfers": 0})).data
        legs = [l["mode"] for l in r["options"][0]["legs"] if l["mode"] != "walk"]
        assert legs == ["ride", "shuttle", "ride"] and {d["alert_id"] for d in r["options"][0]["disruptions"]} >= {"77519"}
        assert r["options"][0]["typical_min_note"] == "excludes the shuttle leg"
        day = (await c.call_tool("connections", {"origin": "Finch", "destination": "Union", "max_transfers": 0, "depart_at": "2026-10-06T08:00"})).data
        assert [l["mode"] for l in day["options"][0]["legs"] if l["mode"] != "walk"] == ["ride"]
        ride = next(l for l in day["options"][0]["legs"] if l["mode"] == "ride")
        assert day["options"][0]["basis"] == "schedule" and ride["next"] and ride["next"][0]["scheduled_at"] >= "2026-10-06T08:00"
        assert all(n["basis"] == "schedule" for n in ride["next"]) and day["meta"]["cache"] == "static" and day["meta"]["advisories"]["source"]
        shuttle_first = (await c.call_tool("connections", {"origin": "Lawrence", "destination": "Union", "max_transfers": 0})).data["options"][0]
        assert [l["mode"] for l in shuttle_first["legs"]][0] == "shuttle" and shuttle_first["basis"] == "schedule"
        assert not any("next" in l for l in shuttle_first["legs"])


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
        ride = r["options"][0]["legs"][0]
        assert (ride["route"], ride["direction_id"], r["options"][0]["basis"]) == ("504", "1", "schedule")
        assert [(n["basis"], n["headsign"]) for n in ride["next"]] == [("schedule", "West - 504 King towards Distillery")]
        assert ride["next"][0]["scheduled_at"].startswith("2026-10-06T08:04") and ride["next_note"].startswith("no real-time prediction in this direction")
        real = live.arrivals_at

        async def unknown_first(feed, rt, row, **kw):
            rows, hits, source, note = await real(feed, rt, row, **kw)
            rows[0]["direction_id"] = None
            return rows, hits, source, note
        monkeypatch.setattr(live, "arrivals_at", unknown_first)
        r = (await c.call_tool("connections", {"origin": "15461", "destination": "Union"})).data
        ride = r["options"][0]["legs"][0]
        assert [n["vehicle_id"] for n in ride["next"]] == ["4617"] and "next_note" not in ride and r["options"][0]["basis"] == "realtime"
        assert ride["next"][0]["crowding"]["label"] == "Busy" and ride["next"][0]["headsign"] == "East - 504A King towards Distillery"


@pytest.mark.anyio
async def test_connections_take_live_rows_only_within_an_hour_of_the_clock(app_env):
    async with Client(make_app([])) as c:
        near = (await c.call_tool("connections", {"origin": "13863", "destination": "Finch", "depart_at": "2026-10-06T08:30"})).data
        ride = near["options"][0]["legs"][0]
        assert near["depart_at"] == "2026-10-06T08:30-04:00" and near["meta"]["cache"] == "live" and near["meta"]["source"].startswith("static patterns")
        # NTAS has trains at 0, 4 and 5 minutes from the 08:00 clock, all before 08:30: the timetable fills in from 08:30.
        assert near["options"][0]["basis"] == "schedule" and ride["next"][0]["scheduled_at"] >= "2026-10-06T08:30"
        assert ride["next_note"] == "no real-time prediction in this direction from depart_at on; times are from the timetable"
        soon = (await c.call_tool("connections", {"origin": "13863", "destination": "Finch", "depart_at": "2026-10-06T08:03"})).data
        assert soon["options"][0]["basis"] == "realtime" and [n["eta_min"] for n in soon["options"][0]["legs"][0]["next"]] == [4, 5]
        far = (await c.call_tool("connections", {"origin": "13863", "destination": "Finch", "depart_at": "2026-10-06T09:01"})).data
        ride = far["options"][0]["legs"][0]
        assert far["options"][0]["basis"] == "schedule" and ride["next"][0]["scheduled_at"] >= "2026-10-06T09:01"
        assert far["meta"]["cache"] == "static" and far["meta"]["advisories"]["source"]


@pytest.mark.anyio
async def test_connections_explain_an_empty_answer(app_env, monkeypatch):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "15246", "destination": "Union", "max_transfers": 0, "depart_at": "2026-10-06T02:00"})).data
        assert r["options"] == [] and "within 120 minutes" in r["note"] and r["meta"]["cache"] == "static" and r["meta"]["advisories"]
        monkeypatch.setattr("ttc_mcp.runtime.now", lambda: NIGHT)
        monkeypatch.setattr("ttc_mcp.domain.planner._shuttle_runs", lambda el: False)
        r = (await c.call_tool("connections", {"origin": "Finch", "destination": "Union", "max_transfers": 0})).data
        assert r["options"] == [] and r["note"] == "every option is cut by a closure (no shuttle service, or a bypassed boarding or alighting station)"
        monkeypatch.setattr("ttc_mcp.runtime.now", lambda: dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ))
        roncesvalles = {"lat": 43.650939, "lon": -79.450927}
        r = (await c.call_tool("connections", {"origin": roncesvalles, "destination": "4582", "walk_m": 1})).data
        assert r["options"] == [] and r["note"] == "every stop at the origin is skipped by a detour"
        r = (await c.call_tool("connections", {"origin": "4582", "destination": "15039", "walk_m": 1})).data
        assert r["options"] == [] and r["note"] == "every stop at the destination is skipped by a detour"


@pytest.mark.anyio
async def test_connections_replace_a_detour_skipped_stop_with_the_served_stops_around_it(app_env):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "15039", "destination": "4582"})).data
    assert r["from"] == {"stop": "15039", "name": "Roncesvalles Ave at Howard Park Ave South Side"}
    assert r["note"] == "origin stop 15039 is skipped by detour 9001; using the served stops within 400 m of it"
    first = r["options"][0]
    assert first["legs"][0] == {"mode": "walk", "to": {"number": "4569", "name": "Roncesvalles Ave at Boustead Ave South Side"}, "distance_m": 245, "min": 4}
    assert [(l["route"], l["board"]["number"]) for l in first["legs"] if l["mode"] == "ride"] == [("504", "4569")]


@pytest.mark.anyio
async def test_connections_reject_what_they_cannot_plan(app_env):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "15461", "destination": "nowhere"})).data
        assert r["error"].startswith("could not resolve destination") and r["meta"]["cache"] == "static"
        far = (await c.call_tool("connections", {"origin": {"lat": 43.0, "lon": -79.0}, "destination": "Union"})).data
        assert far["error"] == "no stop within 400 m of the origin; raise walk_m or start from a stop"
        with pytest.raises(ToolError):
            await c.call_tool("connections", {"origin": "15461", "destination": "Union", "max_transfers": 2})


@pytest.mark.parametrize("args,error", [
    ({"limit": 0}, "limit must be between 1 and 10"), ({"limit": 11}, "limit must be between 1 and 10"),
    ({"walk_m": 0}, "walk_m must be between 1 and 2000"), ({"walk_m": 8000}, "walk_m must be between 1 and 2000"),
    ({"depart_at": "tomorrow at 8"}, "depart_at must be an ISO date-time such as 2026-10-06T08:30"),
    ({"origin": {"lat": "north", "lon": -79.36}}, "origin needs numeric lat and lon"),
    ({"origin": {"lat": 43.65}}, "origin needs numeric lat and lon"),
    ({"destination": {"lat": None, "lon": -79.38}}, "destination needs numeric lat and lon")])
@pytest.mark.anyio
async def test_connections_answer_bad_inputs_with_an_error(app_env, args, error):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "15461", "destination": "Union", **args})).data
    assert r["error"] == error and r["meta"]["cache"] == "static"


@pytest.mark.anyio
async def test_route_shape_needs_both_ends_of_a_span(app_env):
    async with Client(make_app([])) as c:
        for half in ({"from_stop": "4569"}, {"to_stop": "4573"}):
            r = (await c.call_tool("route_shape", {"route": "504", "direction": "0", **half})).data
            assert r["error"] == "give both from_stop and to_stop for a span, or neither"


@pytest.mark.anyio
async def test_connections_route_around_a_closure_without_shuttle_service(app_env, monkeypatch):
    from ttc_mcp.domain import planner
    real = planner.transfer_stops
    monkeypatch.setattr("ttc_mcp.runtime.now", lambda: NIGHT)
    monkeypatch.setattr(planner, "_shuttle_runs", lambda el: False)
    # Pretend a 121 stop bound for Union is 50 m from Sheppard-Yonge, north of the York Mills to Eglinton closure.
    monkeypatch.setattr(planner, "transfer_stops", lambda c, sid: {**real(c, sid), **({"11169": 50} if sid == "13860" else {})})
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": "Finch", "destination": "Union"})).data
    assert r["options"] and "note" not in r
    rides = [l for l in r["options"][0]["legs"] if l["mode"] == "ride"]
    assert [(l["route"], l["board"]["number"], l["alight"]["number"]) for l in rides][0] == ("1", "14111", "13860") and r["options"][0]["transfers"] == 1
    assert (rides[1]["route"], rides[1]["board"]["number"]) == ("121", "11169") and rides[1]["alight"]["number"] in {"16754", "246"}
