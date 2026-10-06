import datetime as dt
import shutil

import pytest
from fastmcp import Client

from ttc_mcp import runtime
from ttc_mcp.static.manifest import FeedEntry, Manifest

TOOLS = {"find_stops", "nearest_stops", "stop", "station", "routes", "route_stops", "service_hours", "scheduled_departures", "arrivals", "next_trains", "vehicles", "feed_status", "alerts", "service_status", "detours", "service_changes", "route_shape"}


@pytest.fixture
def app(tmp_path, fixture_db, monkeypatch):
    shutil.copy(fixture_db, tmp_path / "static-S1000538.sqlite")
    Manifest([FeedEntry("S1000538", "20260906", "20261031", "static-S1000538.sqlite", '"e"', "u", "t")]).save(tmp_path)
    monkeypatch.setenv("TTC_MCP_CACHE", str(tmp_path))
    monkeypatch.setenv("TTC_MCP_SKIP_REFRESH", "1")
    monkeypatch.setattr(runtime, "now", lambda: dt.datetime(2026, 10, 6, 8, 0, tzinfo=runtime.TZ))
    runtime.reset()
    from ttc_mcp.server import create_app

    yield create_app()
    runtime.reset()


@pytest.mark.anyio
async def test_lists_the_eight_static_tools(app):
    async with Client(app) as c:
        assert {t.name for t in await c.list_tools()} >= TOOLS


@pytest.mark.anyio
async def test_find_stops_and_meta(app):
    async with Client(app) as c:
        r = (await c.call_tool("find_stops", {"query": "cherry st at front"})).data
        assert {s["number"] for s in r["stops"]} >= {"15460", "15461"}
        assert r["meta"]["static_feed"] == "S1000538" and r["meta"]["staleness"] == "fresh" and r["meta"]["cache"] == "static"


@pytest.mark.anyio
async def test_station_alias_and_unknown_stop_error(app):
    async with Client(app) as c:
        assert (await c.call_tool("station", {"query": "Dundas"})).data["station"]["name"] == "TMU"
        assert "error" in (await c.call_tool("stop", {"number": "0"})).data


@pytest.mark.anyio
async def test_scheduled_departures_default_to_now(app):
    async with Client(app) as c:
        r = (await c.call_tool("scheduled_departures", {"stop": "15461", "route": "504", "limit": 3})).data
        assert len(r["departures"]) == 3 and r["departures"][0]["when"] >= "2026-10-06T08:00:00-04:00"
        assert r["basis"] == "schedule"


@pytest.mark.anyio
async def test_routes_service_hours_and_route_stops(app):
    async with Client(app) as c:
        assert [x["route"] for x in (await c.call_tool("routes", {"overnight": True})).data["routes"]] == ["300"]
        assert (await c.call_tool("service_hours", {"route": "300", "date": "2026-10-06"})).data["runs"] is True
        assert (await c.call_tool("route_stops", {"route": "504", "direction": "0"})).data["patterns"][0]["rank"] == 1


def test_store_unavailable_reads_as_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TTC_MCP_CACHE", str(tmp_path / "empty"))
    runtime.reset()
    from ttc_mcp.tools.static_tools import _guard

    assert "static store" in _guard(lambda feed: {})["error"]
    runtime.reset()


@pytest.mark.anyio
async def test_route_shape_span_errors_name_the_reason(app):
    async with Client(app) as c:
        r = (await c.call_tool("route_shape", {"route": "504", "direction": "0", "pattern": 2, "from_stop": "4573", "to_stop": "4569"})).data
        assert "not after" in r["error"] and r["meta"]["static_feed"] == "S1000538"
        r = (await c.call_tool("route_shape", {"route": "504", "direction": "0", "pattern": 2, "from_stop": "4569", "to_stop": "0"})).data
        assert "stop 0 is not on" in r["error"]
