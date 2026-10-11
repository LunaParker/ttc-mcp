import datetime as dt
import shutil

import pytest
from fastmcp import Client

from ttc_mcp import runtime
from ttc_mcp.static.manifest import FeedEntry, Manifest

TOOLS = {"find_stops", "nearest_stops", "stop", "station", "routes", "route_stops", "service_hours", "scheduled_departures", "arrivals", "next_trains", "vehicles", "feed_status", "alerts", "service_status", "detours", "service_changes", "route_shape", "accessibility", "connections"}


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


@pytest.mark.anyio
async def test_accessibility_names_the_queried_child_by_kind(app, monkeypatch):
    async with Client(app) as c:
        r = (await c.call_tool("accessibility", {"query": "13812"})).data
        assert r["accessibility"]["station"]["id"] == "14587" and r["queried_platform"] == "13812" and "queried_entrance" not in r
        r = (await c.call_tool("accessibility", {"query": "101264"})).data
        assert r["accessibility"]["station"]["id"] == "14587" and r["queried_entrance"] == "101264" and "queried_platform" not in r
        r = (await c.call_tool("accessibility", {"query": "101303"})).data
        assert r["accessibility"]["station"]["id"] == "14587" and not {"queried_platform", "queried_entrance"} & r.keys()
        monkeypatch.setattr("ttc_mcp.tools.derived_tools.station_row", lambda conn, raw: None)
        assert (await c.call_tool("accessibility", {"query": "13812"})).data["error"] == "stop 13812 has no station record"


def test_instructions_point_trip_questions_at_connections_and_name_the_derived_tools():
    from ttc_mcp.server import INSTRUCTIONS

    assert "connections" in INSTRUCTIONS and "route_shape" in INSTRUCTIONS and "accessibility" in INSTRUCTIONS and "trip" in INSTRUCTIONS


@pytest.mark.anyio
async def test_the_recheck_lifespan_runs_ensure_static_on_the_interval_and_stops_cleanly(app_env, monkeypatch, capsys):
    import asyncio
    from pathlib import Path

    from ttc_mcp import server

    calls = []

    def fake_ensure(cache_dir, client):
        calls.append((Path(cache_dir), client.headers["user-agent"]))
        return {"changed": False, "feed_version": "S1000538", "reason": "etags unchanged"}

    monkeypatch.setattr(server, "RECHECK_S", 0.01)
    monkeypatch.setattr(server, "ensure_static", fake_ensure)
    async with Client(server.create_app(recheck=True)) as c:
        await c.list_tools()
        await asyncio.sleep(0.15)
    n = len(calls)
    assert n >= 2 and calls[0][0] == Path(runtime.settings().cache_dir) and calls[0][1].startswith("ttc-mcp/")
    await asyncio.sleep(0.05)
    assert len(calls) == n, "the loop must stop with the server"
    assert "[ttc-mcp] static recheck: etags unchanged (S1000538)" in capsys.readouterr().err
    async with Client(server.create_app()) as c:
        await c.list_tools()
        await asyncio.sleep(0.05)
    assert len(calls) == n, "no recheck unless asked (http, --no-refresh, TTC_MCP_SKIP_REFRESH)"


@pytest.mark.anyio
async def test_a_failing_recheck_is_logged_and_the_loop_goes_on(app_env, monkeypatch, capsys):
    import asyncio

    from ttc_mcp import server

    calls = []

    def fake_ensure(cache_dir, client):
        calls.append(1)
        raise RuntimeError("ckan down")

    monkeypatch.setattr(server, "RECHECK_S", 0.01)
    monkeypatch.setattr(server, "ensure_static", fake_ensure)
    async with Client(server.create_app(recheck=True)) as c:
        await asyncio.sleep(0.1)
        assert "error" not in (await c.call_tool("routes", {"overnight": True})).data
    assert len(calls) >= 2 and "[ttc-mcp] static recheck failed: ckan down" in capsys.readouterr().err


@pytest.mark.anyio
async def test_station_results_carry_id_and_aliases_beside_number(app_env):
    from tests.mockttc import make_app

    async with Client(make_app([])) as c:
        near = (await c.call_tool("nearest_stops", {"lat": 43.6527, "lon": -79.3581, "radius_m": 2500, "kind": "station", "limit": 10})).data["results"]
        union = next(r for r in near if r["name"] == "Union")
        assert (union["id"], union["number"], union["aliases"], union["kind"], union["lines"]) == ("15772", "15772", ["union"], "station", ["1"])
        found = (await c.call_tool("find_stops", {"query": "Union"})).data["stops"]
        st = next(s for s in found if s["kind"] == "station")
        assert (st["id"], st["number"], st["aliases"]) == ("15772", "15772", ["union"])
        assert all(not {"id", "aliases"} & s.keys() for s in found if s["kind"] != "station") and len(found) > 1
