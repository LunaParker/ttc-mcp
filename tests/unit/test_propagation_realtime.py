import datetime as dt

import pytest
from fastmcp import Client

from tests.mockttc import make_app
from ttc_mcp.static.schedule import TZ

NIGHT = dt.datetime(2026, 10, 6, 23, 30, tzinfo=TZ)


@pytest.mark.anyio
async def test_arrivals_flag_detoured_vehicles_and_list_disruptions(app_env):
    calls = []
    async with Client(make_app(calls)) as c:
        r = (await c.call_tool("arrivals", {"stop": "15461"})).data
        flags = {a["vehicle_id"]: a["detour_id"] for a in r["arrivals"]}
        assert flags["4617"] == "9001" and flags["4517"] is None
        assert [d["detour_id"] for d in r["disruptions"]] == ["9001"] and r["meta"]["advisories"]["staleness"] == "fresh"
        assert len([u for u in calls if "/gtfsrt/trips" in u]) == 1
        sched = (await c.call_tool("arrivals", {"stop": "15039", "limit": 2})).data
        assert all(a["basis"] == "schedule" and "detour_id" in a for a in sched["arrivals"])
        assert [d["detour_id"] for d in sched["disruptions"]] == ["9001"]
        plain = (await c.call_tool("arrivals", {"stop": "15460", "limit": 1})).data
        assert plain["arrivals"][0]["detour_id"] is None and "disruptions" in plain
    assert len([u for u in calls if "/gtfsrt/trips" in u]) == 1


@pytest.mark.anyio
async def test_next_trains_report_suspended_platforms_inside_a_closure(app_env, monkeypatch):
    async with Client(make_app([])) as c:
        day = (await c.call_tool("next_trains", {"query": "York Mills"})).data
        assert {p["service"] for p in day["platforms"]} == {"no_prediction"} and {d["alert_id"] for d in day["disruptions"]} == {"77519", "77535"}
        assert "disruption" not in day["platforms"][0]
    monkeypatch.setattr("ttc_mcp.runtime.now", lambda: NIGHT)
    async with Client(make_app([])) as c:
        night = (await c.call_tool("next_trains", {"query": "York Mills"})).data
        assert all(p["service"] == "suspended" and p["disruption"]["alert_id"] == "77538" and p["disruption"]["source"] == "gtfsrt alerts" for p in night["platforms"])
        assert night["meta"]["advisories"]["cache"] == "live" and night["meta"]["source"].startswith("ntas")
        bloor = (await c.call_tool("next_trains", {"query": "13863"})).data
        assert bloor["platforms"][0]["service"] == "normal" and bloor["disruptions"] == []
