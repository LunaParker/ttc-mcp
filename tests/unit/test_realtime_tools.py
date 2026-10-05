import datetime as dt
import shutil
import time
from email.utils import formatdate
from pathlib import Path

import httpx
import pytest
from fastmcp import Client

from tests.rtfeeds import make_vehicle_feed
from ttc_mcp import runtime
from ttc_mcp.realtime import Realtime
from ttc_mcp.static.manifest import FeedEntry, Manifest

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "bustime"
NTAS = {"13863": b'[{"line":"1","direction":"1","nextTrains":"0, 4, 5","directionText":"Northbound to Finch","stopCode":"13863"}]',
        "13864": b'[{"line":"1","direction":"0","nextTrains":"","directionText":"Southbound to Vaughan Metropolitan Centre","stopCode":"13864"}]'}
TOOLS = {"arrivals", "next_trains", "vehicles", "feed_status"}


def handler_factory(calls, *, fail=()):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        host, path = req.url.host, req.url.path
        if host in fail:
            return httpx.Response(503)
        if host == "bustime.ttc.ca" and path.endswith("getStopPredictions.jsp"):
            name = "stop-15461.xml" if req.url.params["stop"] == "15461" else "stop-notfound.xml"
            return httpx.Response(200, content=(FIX / name).read_bytes(), headers={"Date": formatdate(usegmt=True)})
        if host == "bustime.ttc.ca" and path == "/gtfsrt/vehicles":
            return httpx.Response(200, content=make_vehicle_feed(int(time.time()) - 20, [
                {"id": "4617", "route": "504", "trip": "100125020", "lat": 43.65, "lon": -79.36, "bearing": 241, "speed": 0.0,
                 "stop_id": "4282", "seq": 12, "status": "INCOMING_AT", "occupancy": "FEW_SEATS_AVAILABLE"},
                {"id": "8520", "route": "121", "trip": "x", "lat": 43.6, "lon": -79.3, "occupancy": "EMPTY"}]))
        if host == "ntas.ttc.ca":
            return httpx.Response(200, content=NTAS.get(path.rsplit("/", 1)[-1], b"[]"))
        return httpx.Response(404)
    return handler


@pytest.fixture
def env(tmp_path, fixture_db, monkeypatch):
    shutil.copy(fixture_db, tmp_path / "static-S1000538.sqlite")
    Manifest([FeedEntry("S1000538", "20260906", "20261031", "static-S1000538.sqlite", '"e"', "u", "t")]).save(tmp_path)
    monkeypatch.setenv("TTC_MCP_CACHE", str(tmp_path))
    monkeypatch.setenv("TTC_MCP_SKIP_REFRESH", "1")
    monkeypatch.setattr(runtime, "now", lambda: dt.datetime(2026, 10, 6, 8, 0, tzinfo=runtime.TZ))
    runtime.reset()
    yield
    runtime.reset()


async def make_app(calls, **kw):
    from ttc_mcp.server import create_app

    runtime.set_realtime(Realtime(httpx.AsyncClient(transport=httpx.MockTransport(handler_factory(calls, **kw)))))
    return create_app()


@pytest.mark.anyio
async def test_lists_the_real_time_tools(env):
    async with Client(await make_app([])) as c:
        assert {t.name for t in await c.list_tools()} >= TOOLS


@pytest.mark.anyio
async def test_arrivals_realtime_rows_with_crowding_and_meta_then_ttl(env):
    calls = []
    async with Client(await make_app(calls)) as c:
        r = (await c.call_tool("arrivals", {"stop": "15461"})).data
        assert r["stop"] == {"number": "15461", "name": "Cherry St at Front St East", "lat": r["stop"]["lat"], "lon": r["stop"]["lon"], "station": None}
        assert [a["eta_min"] for a in r["arrivals"]] == [2, 14, 15, 28] and r["arrivals"][0]["crowding"]["label"] == "Busy"
        assert r["arrivals"][0]["basis"] == "realtime" and r["arrivals"][0]["direction_id"] == "0" and "note" not in r
        m = r["meta"]
        assert (m["source"], m["cache"], m["staleness"], m["static_feed"]) == ("bustime getStopPredictions.jsp + gtfsrt/vehicles", "live", "fresh", "S1000538")
        assert isinstance(m["feed_timestamp"], int) and m["fetched_at"].startswith("20")
        again = (await c.call_tool("arrivals", {"stop": "15461", "limit": 2})).data
        assert again["meta"]["cache"] == "ttl" and len(again["arrivals"]) == 2
    assert len([u for u in calls if "getStopPredictions" in u]) == 1 and len([u for u in calls if "vehicles" in u]) == 1


@pytest.mark.anyio
async def test_arrivals_fall_back_to_the_timetable_when_bustime_has_nothing(env):
    async with Client(await make_app([])) as c:
        r = (await c.call_tool("arrivals", {"stop": "15460", "limit": 3})).data
        assert len(r["arrivals"]) == 3 and all(a["basis"] == "schedule" for a in r["arrivals"])
        assert r["note"] == "BusTime: Stop not found; times are from the timetable"
        assert r["meta"]["source"].endswith("+ static stop_times") and r["meta"]["cache"] == "live"


@pytest.mark.anyio
async def test_arrivals_when_every_source_is_down_is_schedule_with_unavailable_meta(env):
    async with Client(await make_app([], fail=("bustime.ttc.ca",))) as c:
        r = (await c.call_tool("arrivals", {"stop": "15461", "route": "504", "limit": 2})).data
        assert all(a["basis"] == "schedule" and a["route"] == "504" for a in r["arrivals"]) and len(r["arrivals"]) == 2
        assert "unavailable (" in r["note"] and r["meta"]["staleness"] == "unavailable" and r["meta"]["cache"] is None


@pytest.mark.anyio
async def test_arrivals_on_a_rail_platform_use_ntas_and_reject_stations(env):
    async with Client(await make_app([])) as c:
        r = (await c.call_tool("arrivals", {"stop": "13863"})).data
        assert [a["eta_min"] for a in r["arrivals"]] == [0, 4, 5] and r["arrivals"][0]["route"] == "1" and r["meta"]["source"] == "ntas"
        assert "error" in (await c.call_tool("arrivals", {"stop": "14572"})).data
        assert "error" in (await c.call_tool("arrivals", {"stop": "0"})).data


@pytest.mark.anyio
async def test_next_trains_by_alias_mixes_normal_and_no_prediction(env):
    calls = []
    async with Client(await make_app(calls)) as c:
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
async def test_vehicles_by_route_and_id_and_rail_unavailable(env):
    async with Client(await make_app([])) as c:
        r = (await c.call_tool("vehicles", {"route": "504"})).data
        assert len(r["vehicles"]) == 1 and r["vehicles"][0]["current_stop"] == "15461" and r["vehicles"][0]["headsign"].startswith("West - 504A")
        assert r["meta"]["source"] == "bustime vehicles" and r["meta"]["staleness"] == "fresh"
        assert (await c.call_tool("vehicles", {"vehicle_id": "8520"})).data["vehicles"][0]["crowding"]["label"] == "Not busy"
        rail = (await c.call_tool("vehicles", {"route": "1"})).data
        assert rail["vehicles"] == [] and "no vehicle positions" in rail["unavailable"] and rail["meta"]["cache"] == "static"
        assert "error" in (await c.call_tool("vehicles", {})).data


@pytest.mark.anyio
async def test_feed_status_reports_static_and_sources_and_probes(env):
    calls = []
    async with Client(await make_app(calls)) as c:
        r = (await c.call_tool("feed_status", {})).data
        assert r["static"]["feed_version"] == "S1000538" and r["static"]["verdict"] == "fresh" and r["static"]["end"] == "20261031"
        assert {s["name"]: s["verdict"] for s in r["sources"]} == {"bustime getStopPredictions.jsp": "unused", "bustime vehicles": "unused", "ntas": "unused"}
        assert r["ntas_coverage"] == {} and "probed" not in r
        p = (await c.call_tool("feed_status", {"probe": True})).data
        assert p["probed"] == {"bustime getStopPredictions.jsp": None, "bustime vehicles": None, "ntas": None}
        assert all(s["verdict"] == "fresh" and s["cache"] == "ttl" for s in p["sources"]) and p["ntas_coverage"] == {"1": {"platforms": 1, "with_prediction": 1}}
    assert len(calls) == 3


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
