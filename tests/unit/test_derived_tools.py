import datetime as dt

import pytest
from fastmcp import Client

from tests.mockttc import make_app
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
        assert ride["route"] == "121" and ride["next"] and all({"eta_min", "basis", "crowding"} <= set(n) for n in ride["next"])
        assert first["basis"] == "schedule" and ride["next"][0]["scheduled_at"] >= "2026-10-06T08:00"
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


@pytest.mark.anyio
async def test_connections_need_no_ride_when_both_ends_share_a_stop(app_env):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("connections", {"origin": CHERRY_FRONT, "destination": "15461"})).data
        assert r["options"] == [] and "no ride" in r["note"] and r["to"] == {"stop": "15461", "name": "Cherry St at Front St East"}
        assert r["meta"]["cache"] == "static"
