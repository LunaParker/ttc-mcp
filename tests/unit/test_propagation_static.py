import datetime as dt

import pytest
from fastmcp import Client

from tests.mockttc import make_app
from ttc_mcp.static.schedule import TZ

NIGHT = dt.datetime(2026, 10, 6, 23, 30, tzinfo=TZ)


@pytest.mark.anyio
async def test_stop_and_station_carry_disruptions_and_service(app_env):
    async with Client(make_app([])) as c:
        bay = (await c.call_tool("stop", {"number": "14806"})).data
        assert [d["alert_id"] for d in bay["stop"]["disruptions"]] == ["77519"]
        assert bay["meta"]["cache"] == "static" and bay["meta"]["advisories"]["cache"] == "live" and bay["meta"]["advisories"]["errors"] is None
        st = (await c.call_tool("station", {"query": "York Mills"})).data["station"]
        assert [(s["line"], s["service"], s["disruption"]["alert_id"]) for s in st["service"]] == [("1", "reduced", "77519")]
        assert {d["alert_id"] for d in st["disruptions"]} == {"77519", "77535"} and {p["status"] for p in st["platforms"]} == {"open"}
        sg = (await c.call_tool("station", {"query": "St George"})).data["station"]
        assert [d["unit_code"] for d in sg["disruptions"]] == ["1Y1L"] and sg["service"][0]["service"] == "normal"
        assert "disruptions" in (await c.call_tool("stop", {"number": "14572"})).data["stop"]


@pytest.mark.anyio
async def test_station_is_suspended_inside_a_closure_window(app_env, monkeypatch):
    monkeypatch.setattr(app_env, "now", lambda: NIGHT)
    async with Client(make_app([])) as c:
        st = (await c.call_tool("station", {"query": "York Mills"})).data["station"]
        assert st["service"][0]["service"] == "suspended" and st["service"][0]["disruption"]["active_now"] is True
        status = {p["number"]: p["status"] for p in st["platforms"]}
        assert status["13791"] == status["13792"] == "closed" and status["14806"] == "open"


@pytest.mark.anyio
async def test_route_stops_and_nearest_stops_mark_detoured_stops(app_env):
    async with Client(make_app([])) as c:
        rs = (await c.call_tool("route_stops", {"route": "504", "direction": "0", "pattern": 2})).data
        status = {s["number"]: s["status"] for s in rs["patterns"][0]["stops"]}
        assert {status["15039"], status["14994"], status["14993"]} == {"skipped_by_detour"} and status["4569"] == "open" and status["4573"] == "open"
        near = (await c.call_tool("nearest_stops", {"lat": 43.648885, "lon": -79.450121, "kind": "stop", "limit": 2})).data
        assert near["results"][0]["number"] == "14994" and [d["detour_id"] for d in near["results"][0]["disruptions"]] == ["9001"]
        assert near["meta"]["advisories"]["staleness"] == "fresh"


@pytest.mark.anyio
async def test_static_tools_still_answer_when_advisories_are_down(app_env):
    async with Client(make_app([], fail=("alerts.ttc.ca", "gtfsrt.ttc.ca"))) as c:
        st = (await c.call_tool("station", {"query": "York Mills"})).data
        assert st["station"]["disruptions"] == [] and st["station"]["service"] == [{"line": "1", "service": "normal", "disruption": None}]
        assert st["meta"]["advisories"]["staleness"] == "unavailable" and set(st["meta"]["advisories"]["errors"]) == {"alerts", "rt_alerts", "detours"}
