import pytest
from fastmcp import Client

from tests.mockttc import make_app
from ttc_mcp import runtime
from ttc_mcp.sources import SOURCES

TOOLS = {"arrivals", "next_trains", "vehicles", "feed_status"}
NAMES = [s.name for s in SOURCES]


@pytest.mark.anyio
async def test_lists_the_real_time_tools(app_env):
    async with Client(make_app([])) as c:
        assert {t.name for t in await c.list_tools()} >= TOOLS


@pytest.mark.anyio
async def test_arrivals_realtime_rows_with_crowding_and_meta_then_ttl(app_env):
    calls = []
    async with Client(make_app(calls)) as c:
        r = (await c.call_tool("arrivals", {"stop": "15461"})).data
        assert r["stop"] == {"number": "15461", "name": "Cherry St at Front St East", "lat": r["stop"]["lat"], "lon": r["stop"]["lon"], "station": None}
        assert [a["eta_min"] for a in r["arrivals"]] == [2, 14, 15, 28] and r["arrivals"][0]["crowding"]["label"] == "Busy"
        assert r["arrivals"][0]["basis"] == "realtime" and r["arrivals"][0]["direction_id"] == "0" and "note" not in r
        m = r["meta"]
        assert (m["source"], m["cache"], m["staleness"], m["static_feed"]) == ("bustime getStopPredictions.jsp + gtfsrt/vehicles", "live", "fresh", "S1000538")
        assert isinstance(m["feed_timestamp"], int) and m["fetched_at"].startswith("20")
        again = (await c.call_tool("arrivals", {"stop": "15461", "limit": 2})).data
        assert again["meta"]["cache"] == "ttl" and len(again["arrivals"]) == 2
    assert len([u for u in calls if "getStopPredictions" in u]) == 1 and len([u for u in calls if "gtfsrt/vehicles" in u]) == 1


@pytest.mark.anyio
async def test_arrivals_fall_back_to_the_timetable_when_bustime_has_nothing(app_env):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("arrivals", {"stop": "15460", "limit": 3})).data
        assert len(r["arrivals"]) == 3 and all(a["basis"] == "schedule" for a in r["arrivals"])
        assert r["note"] == "BusTime: Stop not found; times are from the timetable"
        assert r["meta"]["source"].endswith("+ static stop_times") and r["meta"]["cache"] == "live"


@pytest.mark.anyio
async def test_arrivals_when_every_source_is_down_is_schedule_with_unavailable_meta(app_env):
    async with Client(make_app([], fail=("bustime.ttc.ca",))) as c:
        r = (await c.call_tool("arrivals", {"stop": "15461", "route": "504", "limit": 2})).data
        assert all(a["basis"] == "schedule" and a["route"] == "504" for a in r["arrivals"]) and len(r["arrivals"]) == 2
        assert "unavailable (" in r["note"] and r["meta"]["staleness"] == "unavailable" and r["meta"]["cache"] is None


@pytest.mark.anyio
async def test_arrivals_on_a_rail_platform_use_ntas_and_reject_stations(app_env):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("arrivals", {"stop": "13863"})).data
        assert [a["eta_min"] for a in r["arrivals"]] == [0, 4, 5] and r["arrivals"][0]["route"] == "1" and r["meta"]["source"] == "ntas"
        assert "error" in (await c.call_tool("arrivals", {"stop": "14572"})).data
        assert "error" in (await c.call_tool("arrivals", {"stop": "0"})).data


@pytest.mark.anyio
async def test_next_trains_by_alias_mixes_normal_and_no_prediction(app_env):
    calls = []
    async with Client(make_app(calls)) as c:
        r = (await c.call_tool("next_trains", {"query": "Bloor"})).data
        assert r["station"] == {"id": "14572", "name": "Bloor-Yonge"}
        by = {p["platform"]: p for p in r["platforms"]}
        assert by["13863"]["next_min"] == [0, 4, 5] and by["13863"]["service"] == "normal"
        assert by["13864"]["service"] == "no_prediction" and len(by["13864"]["scheduled_next_min"]) == 3
        assert r["meta"]["source"] == "ntas + static stop_times" and r["meta"]["cache"] == "live"
        one = (await c.call_tool("next_trains", {"query": "13863"})).data
        assert [p["platform"] for p in one["platforms"]] == ["13863"] and one["meta"]["source"] == "ntas"
        assert "error" in (await c.call_tool("next_trains", {"query": "15461"})).data
    assert len([u for u in calls if "ntas" in u]) == 2


@pytest.mark.anyio
async def test_vehicles_by_route_and_id_and_rail_unavailable(app_env):
    async with Client(make_app([])) as c:
        r = (await c.call_tool("vehicles", {"route": "504"})).data
        assert len(r["vehicles"]) == 1 and r["vehicles"][0]["current_stop"] == "15461" and r["vehicles"][0]["headsign"].startswith("East - 504A")
        assert r["meta"]["source"] == "bustime vehicles" and r["meta"]["staleness"] == "fresh"
        assert (await c.call_tool("vehicles", {"vehicle_id": "8520"})).data["vehicles"][0]["crowding"]["label"] == "Not busy"
        rail = (await c.call_tool("vehicles", {"route": "1"})).data
        assert rail["vehicles"] == [] and "no vehicle positions" in rail["unavailable"] and rail["meta"]["cache"] == "static"
        assert "error" in (await c.call_tool("vehicles", {})).data


@pytest.mark.anyio
async def test_feed_status_reports_static_and_sources_and_probes(app_env):
    calls = []
    async with Client(make_app(calls)) as c:
        r = (await c.call_tool("feed_status", {})).data
        assert r["static"]["feed_version"] == "S1000538" and r["static"]["verdict"] == "fresh" and r["static"]["end"] == "20261031"
        assert (r["static"]["last_check"], r["static"]["ckan_last_refreshed"], r["static"]["newer_package_seen"]) == (None, None, False)
        assert "newer_seen" not in r["static"]
        assert {s["name"]: s["verdict"] for s in r["sources"]} == {n: "unused" for n in NAMES}
        assert r["ntas_coverage"] == {} and "probed" not in r
        p = (await c.call_tool("feed_status", {"probe": True})).data
        assert p["probed"] == {n: None for n in NAMES}
        assert all(s["verdict"] == "fresh" and s["cache"] == "ttl" for s in p["sources"]) and p["ntas_coverage"] == {"1": {"platforms": 1, "with_prediction": 1}}
    assert len(calls) == 8


def test_feed_status_without_a_store(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("TTC_MCP_CACHE", str(tmp_path / "empty"))
    runtime.reset()
    from ttc_mcp.server import create_app

    async def go():
        async with Client(create_app()) as c:
            return (await c.call_tool("feed_status", {})).data

    r = asyncio.run(go())
    assert r["static"]["verdict"] == "unavailable" and r["static"]["feed_version"] is None and r["meta"]["static_feed"] is None
    runtime.reset()


@pytest.mark.anyio
async def test_vehicles_and_arrivals_resolve_trips_held_by_the_other_loaded_feed(two_feeds):
    async with Client(make_app([])) as c:
        v = (await c.call_tool("vehicles", {"route": "504"})).data
        assert v["meta"]["static_feed"] == "S1000542" and v["vehicles"][0]["headsign"] == "East - 504A King towards Distillery"
        a = (await c.call_tool("arrivals", {"stop": "15461"})).data
        assert a["arrivals"][0]["vehicle_id"] == "4617" and a["arrivals"][0]["detour_id"] == "9001"
        assert [e["detour_id"] for e in a["disruptions"]] == ["9001"]
