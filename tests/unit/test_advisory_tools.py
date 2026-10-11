import pytest
from fastmcp import Client

from tests.mockttc import make_app

TOOLS = {"alerts", "service_status", "detours", "service_changes"}


@pytest.mark.anyio
async def test_lists_the_advisory_tools(app_env):
    async with Client(make_app([])) as c:
        assert {t.name for t in await c.list_tools()} >= TOOLS


@pytest.mark.anyio
async def test_alerts_filters_and_resolves_objects(app_env):
    calls = []
    async with Client(make_app(calls)) as c:
        r = (await c.call_tool("alerts", {})).data
        assert r["counts"] == {"closure": 1, "suspension": 1, "detour": 3, "rsz": 1, "elevator": 1, "escalator": 1} and r["as_of"] == "2026-10-05T13:42:32-04:00"
        rsz = (await c.call_tool("alerts", {"type": "rsz"})).data["alerts"]
        assert len(rsz) == 1 and rsz[0]["station_objects"] == [{"id": "43276", "name": "York Mills"}, {"id": "7129", "name": "Sheppard-Yonge"}]
        assert {a["alert_id"] for a in (await c.call_tool("alerts", {"station": "York Mills"})).data["alerts"]} == {"77519", "77535"}
        sk = (await c.call_tool("alerts", {"stop": "14994"})).data["alerts"]
        assert [a.get("detour_id") for a in sk] == ["9001"] and sk[0]["source"] == "detour feed"
        s512 = (await c.call_tool("alerts", {"route": "512"})).data["alerts"]
        assert len(s512) == 1 and s512[0]["stop_objects"][0] == {"number": "14958", "name": None} and len(s512[0]["stop_objects"]) == 34
        assert "error" in (await c.call_tool("alerts", {"station": "Nowhere"})).data
        m = r["meta"]
        assert (m["cache"], m["staleness"], m["errors"], m["static_feed"]) == ("live", "fresh", None, "S1000538") and m["source"].startswith("alerts.ttc.ca")
    assert len(calls) == 3


@pytest.mark.anyio
async def test_service_status_summarises_the_network(app_env):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("service_status", {})).data
        assert r["counts"] == {"closures": 1, "closures_active_now": 0, "surface": 4, "rsz": 1, "elevators_out": 1, "escalators_out": 1}
        cl = r["closures"][0]
        assert cl["alert_id"] == "77519" and cl["station_names"] == ["Eglinton", "Lawrence", "York Mills"] and cl["shuttle"] == "Will Operate"
        assert cl["active_now"] is False and len(cl["windows"]) == 3
        assert {s["route"] for s in r["surface"]} == {"512", "13", "504", "77"}
        d504 = next(s for s in r["surface"] if s.get("detour_id") == "9001")
        assert (d504["skipped_stops"], d504["active_now"]) == (3, True) and d504["reason"].startswith("504 King")
        assert r["reduced_speed_zones"]["count"] == 1 and r["elevators_out"] == {"St George": ["1Y1L"]} and r["escalators_out"] == {"Coxwell": ["11D3E"]}
        assert r["as_of"] == "2026-10-05T13:42-04:00" and r["meta"]["cache"] == "live"


@pytest.mark.anyio
async def test_detours_and_service_changes_tools(app_env):
    calls = []
    async with Client(make_app(calls)) as c:
        d = (await c.call_tool("detours", {})).data
        assert (d["count"], d["entities"], d["resolved"]) == (2, 2, 1) and [g["detour_id"] for g in d["detours"]] == ["9002", "9001"]
        assert d["detours"][1]["reason"]["source"] == "gtfsrt alerts" and d["detours"][0]["reason"] is None
        assert [g["detour_id"] for g in (await c.call_tool("detours", {"route": "77"})).data["detours"]] == ["9002"]
        assert d["meta"]["source"].startswith("gtfsrt.ttc.ca/trips/detour")
        s = (await c.call_tool("service_changes", {})).data
        assert s["count"] == 2 and [x["source"] for x in s["changes"]] == ["gtfsrt alerts", "ttcapi"]
        assert (await c.call_tool("service_changes", {"route": "101"})).data["count"] == 1
        assert (await c.call_tool("service_changes", {"route": "101", "date": "2026-10-06"})).data["count"] == 0
    assert len([u for u in calls if "www.ttc.ca" in u]) == 1


@pytest.mark.anyio
async def test_advisory_tools_degrade_when_alerts_are_down(app_env):
    async with Client(make_app([], fail=("alerts.ttc.ca",))) as c:
        r = (await c.call_tool("service_status", {})).data
        assert r["closures"] == [] and r["counts"]["surface"] == 2 and r["meta"]["errors"] == {"alerts": "HTTP 503"} and r["meta"]["cache"] == "live"
        s = (await c.call_tool("service_changes", {})).data
        assert s["count"] == 2
    async with Client(make_app([], fail=("www.ttc.ca",))) as c:
        s = (await c.call_tool("service_changes", {})).data
        assert s["count"] == 1 and "ttcapi unavailable" in s["note"]


@pytest.mark.anyio
async def test_alerts_by_stop_keep_the_closure_at_a_served_end_platform(app_env, monkeypatch):
    import datetime as dt

    from ttc_mcp.static.schedule import TZ

    monkeypatch.setattr("ttc_mcp.runtime.now", lambda: dt.datetime(2026, 10, 6, 23, 30, tzinfo=TZ))
    async with Client(make_app([])) as c:
        ym = (await c.call_tool("alerts", {"stop": "13791"})).data["alerts"]
        assert [(a["alert_id"], a.get("role")) for a in ym] == [("77519", "shuttle_terminus")]
        assert [(a["alert_id"], a.get("role")) for a in (await c.call_tool("alerts", {"stop": "13793", "type": "closure"})).data["alerts"]] == [("77519", None)]
        assert (await c.call_tool("alerts", {"stop": "13791", "type": "rsz"})).data["alerts"] == []


@pytest.mark.anyio
async def test_detours_resolve_trips_held_by_the_other_loaded_feed(two_feeds):
    async with Client(make_app([])) as c:
        d = (await c.call_tool("detours", {"route": "504"})).data
        assert d["meta"]["static_feed"] == "S1000542" and d["resolved"] == 1 and d["detours"][0]["directions"][0]["pattern"] == 6
        assert d["detours"][0]["skipped_stops"] == ["14993", "14994", "15039"]
