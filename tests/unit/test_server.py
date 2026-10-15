import asyncio

import pytest
from fastmcp import Client

from tests.mockttc import make_app
from ttc_mcp import runtime

TOOLS = {"find_stops", "nearest_stops", "stop", "station", "routes", "route_stops", "service_hours", "scheduled_departures", "arrivals", "next_trains", "vehicles", "feed_status", "alerts", "service_status", "detours", "service_changes", "route_shape", "accessibility", "connections"}


@pytest.fixture
def app(app_env):
    """The server over app_env's store with tests.mockttc's TTC client installed; app_env's teardown resets the runtime."""
    return make_app([])


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
        st = (await c.call_tool("station", {"query": "York Mills"})).data["station"]
        assert all(p["status"] == "open" for p in st["platforms"]) and st["surface_stops"] and all(b["status"] == "open" for b in st["surface_stops"])
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
    assert "timetable-exact" in INSTRUCTIONS and "arrival time" in INSTRUCTIONS and "one-transfer" not in INSTRUCTIONS


@pytest.mark.anyio
async def test_the_recheck_lifespan_runs_ensure_static_on_the_interval_and_stops_cleanly(app_env, monkeypatch, capsys):
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
    # The loop is cancelled with the server; a check already running in its worker thread finishes on its own first.
    await asyncio.sleep(0.05)
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
    async with Client(make_app([])) as c:
        near = (await c.call_tool("nearest_stops", {"lat": 43.6527, "lon": -79.3581, "radius_m": 2500, "kind": "station", "limit": 10})).data["results"]
        union = next(r for r in near if r["name"] == "Union")
        assert (union["id"], union["number"], union["aliases"], union["kind"], union["lines"]) == ("15772", "15772", ["union"], "station", ["1"])
        found = (await c.call_tool("find_stops", {"query": "Union"})).data["stops"]
        st = next(s for s in found if s["kind"] == "station")
        assert (st["id"], st["number"], st["aliases"]) == ("15772", "15772", ["union"])
        assert all(not {"id", "aliases"} & s.keys() for s in found if s["kind"] != "station") and len(found) > 1


@pytest.mark.anyio
async def test_scheduled_departures_resolve_a_stop_code_and_reject_an_unknown_stop(app, tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "static-S1000538.sqlite")
    db.execute("INSERT INTO stops (stop_id, stop_code, name, lat, lon, location_type) VALUES ('99407', '407', 'Test St at 407 Stop', 43.7, -79.4, 0)")
    db.execute("INSERT INTO departures (stop_id, dep_secs, trip_id, stop_sequence) SELECT 99407, dep_secs, trip_id, stop_sequence FROM departures WHERE stop_id=15461")
    db.commit()
    db.close()
    async with Client(app) as c:
        by_code = (await c.call_tool("scheduled_departures", {"stop": "407", "limit": 3})).data
        by_id = (await c.call_tool("scheduled_departures", {"stop": "99407", "limit": 3})).data
        assert len(by_code["departures"]) == 3 and by_code["departures"] == by_id["departures"]
        assert by_code["stop"] == {"number": "99407", "name": "Test St at 407 Stop", "lat": 43.7, "lon": -79.4, "station": None}
        r = (await c.call_tool("scheduled_departures", {"stop": "nowhere"})).data
        assert r["error"] == "stop nowhere not found" and "departures" not in r and r["meta"]["static_feed"] == "S1000538"


def test_instructions_carry_the_licence_attribution_and_the_best_effort_wording():
    """OGL-Toronto asks for its attribution and, where possible, a link; only the website endpoints lack published terms."""
    from ttc_mcp.server import INSTRUCTIONS

    assert "Contains information licensed under the Open Government Licence – Toronto (https://open.toronto.ca/open-data-licence/)" in INSTRUCTIONS
    assert "Timetables and the GTFS-Realtime feeds are TTC open data on the City of Toronto's portal" in INSTRUCTIONS
    assert "endpoints behind the TTC's website, which publishes no terms specific to them" in INSTRUCTIONS
    assert "Real-time and advisory answers are best effort." in INSTRUCTIONS and "none of which publish terms" not in INSTRUCTIONS


@pytest.mark.anyio
async def test_live_tool_descriptions_say_best_effort_and_the_occupancy_scope(app):
    async with Client(app) as c:
        desc = {t.name: t.description for t in await c.list_tools()}
    for name in ("arrivals", "next_trains", "vehicles", "alerts", "service_status", "detours", "service_changes", "connections"):
        assert "best effort" in desc[name], name
    assert "BusTime GTFS-Realtime feed" in desc["vehicles"]
    for name in ("vehicles", "arrivals"):
        assert "historical occupancy" in desc[name] and "out of scope" in desc[name], name
    assert "best effort" not in desc["routes"]


@pytest.mark.anyio
async def test_the_pull_lifespan_runs_the_puller_on_the_interval_and_stops_cleanly(app_env, monkeypatch, capsys):
    from ttc_mcp import server
    calls = []
    monkeypatch.setattr(server, "PULL_S", 0.01)
    async with Client(server.create_app(puller=lambda: calls.append(1) or "pulled")) as c:
        await asyncio.sleep(0.08)
        assert (await c.call_tool("routes", {})).data["routes"]
    await asyncio.sleep(0.05)  # a pull already in its worker thread finishes on its own first
    n = len(calls)
    assert n >= 2 and "[ttc-mcp] bucket pull: pulled" in capsys.readouterr().err
    await asyncio.sleep(0.05)
    assert len(calls) == n, "the loop must stop with the server"


def test_http_mode_with_a_bucket_pulls_the_store_before_serving(tmp_path, monkeypatch, fixture_db, capsys):
    """The cold start of spec 6.9: with TTC_MCP_STATIC_BUCKET set the manifest and feeds come from the bucket, never from CKAN."""
    import shutil

    from fastmcp import FastMCP

    from ttc_mcp import server
    from ttc_mcp.static import bucket
    from ttc_mcp.static.manifest import FeedEntry, Manifest
    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(fixture_db, src / "static-S1000538.sqlite")
    m = Manifest([FeedEntry("S1000538", "20260901", "20261031", "static-S1000538.sqlite", '"e"', "https://x/merged.zip", "2026-10-11T04:30:00-0400")])
    m.save(src)
    blobs = bucket.MemoryBlobs()
    bucket.push(src, blobs)
    cache = tmp_path / "cache"
    monkeypatch.setenv("TTC_MCP_CACHE", str(cache))
    monkeypatch.setenv("TTC_MCP_STATIC_BUCKET", "ttc-mcp-static-test")
    monkeypatch.delenv("TTC_MCP_SKIP_REFRESH", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.setattr(server, "GcsBlobs", lambda name: blobs)
    monkeypatch.setattr(server, "ensure_static", lambda *args, **kwargs: pytest.fail("CKAN was checked although the bucket is authoritative"))
    created = {}
    real_create = server.create_app

    def spy_create(**kw):
        created.update(kw)
        return real_create(**kw)
    monkeypatch.setattr(server, "create_app", spy_create)
    monkeypatch.setattr(FastMCP, "run", lambda self, *a, **k: None)
    assert server.main(["--transport", "http"]) == 0
    assert Manifest.load(cache).feeds[0].feed_version == "S1000538" and (cache / "static-S1000538.sqlite").exists()
    err = capsys.readouterr().err
    assert "[bucket] pulled static-S1000538.sqlite" in err and "[ttc-mcp] bucket pull: manifest S1000538" in err
    assert created["puller"] is not None and created["puller"]() == "manifest S1000538"


GATE_ENV = {"GOOGLE_OAUTH_CLIENT_ID": "client-id-test", "GOOGLE_OAUTH_CLIENT_SECRET": "secret-test", "BASE_URL": "https://ttc.example",
            "JWT_SIGNING_KEY": "jwt-test", "ALLOWED_EMAILS": "owner@example.com"}
NO_GATE = "[ttc-mcp] http transport has no gate: set GOOGLE_OAUTH_CLIENT_ID to require Google sign-in"


@pytest.mark.parametrize("gated", [False, True])
def test_an_http_start_without_the_gate_says_so(tmp_path, monkeypatch, capsys, gated):
    """Ungated http admits anyone who reaches the port (spec 6.9), so the start names the variable that turns the gate on."""
    from fastmcp import FastMCP

    from ttc_mcp import server
    monkeypatch.setenv("TTC_MCP_CACHE", str(tmp_path))
    monkeypatch.setenv("TTC_MCP_SKIP_REFRESH", "1")
    for k in ("TTC_MCP_STATIC_BUCKET", "GOOGLE_CLOUD_PROJECT", "STORAGE_ENCRYPTION_KEY", *GATE_ENV):
        monkeypatch.delenv(k, raising=False)
    for k, v in (GATE_ENV.items() if gated else ()):
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(FastMCP, "run", lambda self, *a, **k: None)
    assert server.main(["--transport", "http"]) == 0
    assert (NO_GATE in capsys.readouterr().err) is not gated


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_an_unreachable_portal_at_startup_is_logged_and_the_store_on_disk_is_served(app_env, monkeypatch, capsys, transport):
    """Spec 6.9 local mode: a failed startup check must not stop a server whose store is on disk; stdio's recheck tries again later."""
    import httpx
    from fastmcp import FastMCP

    from ttc_mcp import server
    for k in ("TTC_MCP_SKIP_REFRESH", "TTC_MCP_STATIC_BUCKET", *GATE_ENV):
        monkeypatch.delenv(k, raising=False)

    def unreachable(*args, **kwargs):
        raise httpx.ConnectError("[Errno 61] Connection refused")
    monkeypatch.setattr(server, "ensure_static", unreachable)
    created, ran = {}, []
    real_create = server.create_app

    def spy_create(**kw):
        created.update(kw)
        return real_create(**kw)
    monkeypatch.setattr(server, "create_app", spy_create)
    monkeypatch.setattr(FastMCP, "run", lambda self, *a, **k: ran.append(k.get("transport")))
    assert server.main(["--transport", transport]) == 0
    assert ran == [transport] and created["recheck"] is (transport == "stdio")
    err = capsys.readouterr().err
    assert "[ttc-mcp] static check failed (ConnectError: [Errno 61] Connection refused); serving the store on disk: S1000538" in err


def test_an_unreachable_portal_with_no_store_yet_still_starts_and_says_how_to_build(tmp_path, monkeypatch, capsys):
    """Spec 9: a server with no store answers its tools with 'not built yet' rather than failing to start."""
    import httpx
    from fastmcp import FastMCP

    from ttc_mcp import server
    monkeypatch.setenv("TTC_MCP_CACHE", str(tmp_path))
    for k in ("TTC_MCP_SKIP_REFRESH", "TTC_MCP_STATIC_BUCKET"):
        monkeypatch.delenv(k, raising=False)

    def timed_out(*args, **kwargs):
        raise httpx.ConnectTimeout("timed out")
    monkeypatch.setattr(server, "ensure_static", timed_out)
    ran = []
    monkeypatch.setattr(FastMCP, "run", lambda self, *a, **k: ran.append(k.get("transport")))
    assert server.main(["--transport", "stdio"]) == 0 and ran == ["stdio"]
    err = capsys.readouterr().err
    assert "[ttc-mcp] static check failed (ConnectTimeout: timed out); no store on disk yet: tools answer 'not built yet' until ttc-mcp-build succeeds" in err
