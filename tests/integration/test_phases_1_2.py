"""Live integration of phases 1 and 2: the real server over streamable HTTP, the local store, the TTC's endpoints.

Run: uv run pytest tests/integration -v   (needs ~/.cache/ttc-mcp from ttc-mcp-build and network access; not part of the unit suite)
"""
import datetime as dt
import os
import socket
import subprocess
import sys
import time

import pytest
from fastmcp import Client

CHERRY_FRONT = {"lat": 43.6527, "lon": -79.3581}
STATIC = {"find_stops", "nearest_stops", "stop", "station", "routes", "route_stops", "service_hours", "scheduled_departures"}
REALTIME = {"arrivals", "next_trains", "vehicles", "feed_status"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server_url():
    port = _free_port()
    env = {**os.environ, "TTC_MCP_SKIP_REFRESH": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "ttc_mcp.server", "--transport", "http", "--host", "127.0.0.1", "--port", str(port)],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early:\n{proc.stderr.read()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("server did not open its port within 30 s")
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def call(url: str, name: str, **args) -> dict:
    async with Client(url) as c:
        t = time.perf_counter()
        r = (await c.call_tool(name, args)).data
    r["_ms"] = round((time.perf_counter() - t) * 1000)
    assert "error" not in r, (name, args, r.get("error"))
    return r


@pytest.mark.anyio
async def test_server_lists_both_phases_tools_and_nothing_is_fetched_before_asked(server_url):
    async with Client(server_url) as c:
        names = {t.name for t in await c.list_tools()}
    assert names >= STATIC | REALTIME
    fs = await call(server_url, "feed_status")
    assert fs["static"]["verdict"] in ("fresh", "expiring") and fs["static"]["feed_version"].startswith("S")
    assert {s["verdict"] for s in fs["sources"]} == {"unused"}


@pytest.mark.anyio
async def test_nearest_stop_to_cherry_front_has_live_arrivals_consistent_with_its_static_record(server_url):
    near = await call(server_url, "nearest_stops", **CHERRY_FRONT, kind="stop", limit=3)
    first = near["results"][0]
    assert first["number"] == "15461" and first["walk_min"] <= 3
    static = (await call(server_url, "stop", number="15461"))["stop"]
    served = {(s["route"], s["direction_id"]) for s in static["served"]}
    live = await call(server_url, "arrivals", stop="15461")
    assert live["stop"]["name"] == static["name"] and live["_ms"] < 2000 and live["arrivals"]
    for row in live["arrivals"]:
        assert row["basis"] in ("realtime", "schedule")
        assert row["direction_id"] is None or (row["route"], row["direction_id"]) in served
        if row["basis"] == "realtime":
            assert row["crowding"]["label"] in ("Not busy", "Busy", "Very busy", "No info")
    etas = [r["eta_min"] for r in live["arrivals"] if r["eta_min"] is not None]
    assert etas == sorted(etas)


@pytest.mark.anyio
async def test_timetable_and_realtime_answers_honour_the_basis_contract(server_url):
    sched = await call(server_url, "scheduled_departures", stop="15461", route="504", limit=3)
    live = await call(server_url, "arrivals", stop="15461", route="504", limit=3)
    assert sched["basis"] == "schedule" and all(d["route"] == "504" for d in sched["departures"])
    assert live["meta"]["cache"] == "ttl", "second arrivals call within 30 s must be served from the cache"
    if live["arrivals"] and live["arrivals"][0]["basis"] == "schedule":
        assert "note" in live and {r["trip_id"] for r in live["arrivals"]} & {d["trip_id"] for d in sched["departures"]}
    else:
        assert "note" not in live and all(r["vehicle_id"] for r in live["arrivals"] if not r["scheduled_only"])


@pytest.mark.anyio
async def test_station_alias_resolves_the_same_station_and_platforms_in_both_phases(server_url):
    st = (await call(server_url, "station", query="Union"))["station"]
    trains = await call(server_url, "next_trains", query="Union")
    assert trains["station"]["id"] == st["id"] == "15772" and st["lines"] == ["1"]
    rail_platforms = {p["number"] for p in st["platforms"] if any(s["route"] in st["lines"] for s in p["served"])}
    assert {p["platform"] for p in trains["platforms"]} == rail_platforms and len(rail_platforms) == 2
    for p in trains["platforms"]:
        assert p["line"] in st["lines"] and p["service"] in ("normal", "no_prediction")
        assert (p["basis"] == "realtime" and p["next_min"]) or (p["basis"] == "schedule" and "scheduled_next_min" in p)


@pytest.mark.anyio
async def test_live_vehicles_resolve_against_the_static_route(server_url):
    stops_504 = set()
    for direction in ("0", "1"):
        for pat in (await call(server_url, "route_stops", route="504", direction=direction))["patterns"]:
            stops_504 |= {s["number"] for s in pat["stops"]}
    veh = await call(server_url, "vehicles", route="504")
    assert veh["vehicles"], "no 504 vehicles in the feed; is the line running right now?"
    with_trip = [v for v in veh["vehicles"] if v["trip_id"]]
    assert sum(1 for v in with_trip if v["headsign"]) >= 0.8 * len(with_trip), "live trip ids should resolve in the static feed"
    at_stop = [v for v in veh["vehicles"] if v["current_stop"]]
    assert sum(1 for v in at_stop if v["current_stop"] in stops_504) >= 0.8 * len(at_stop), "current stops should lie on a 504 pattern"
    assert veh["meta"]["feed_timestamp"] and veh["meta"]["staleness"] == "fresh"


@pytest.mark.anyio
async def test_line_6_platform_answers_from_the_timetable_when_ntas_is_silent(server_url):
    r = await call(server_url, "next_trains", query="16317")
    p = r["platforms"][0]
    assert p["line"] == "6" and p["service"] in ("normal", "no_prediction")
    if p["service"] == "no_prediction":
        assert p["basis"] == "schedule" and "note" in p and r["meta"]["source"] == "ntas + static stop_times"
    assert (await call(server_url, "service_hours", route="6", date=dt.date.today().isoformat()))["runs"] is True


@pytest.mark.anyio
async def test_feed_status_reflects_everything_this_session_fetched(server_url):
    fs = await call(server_url, "feed_status")
    by = {s["name"]: s for s in fs["sources"]}
    assert by["bustime vehicles"]["verdict"] == "fresh" and by["bustime vehicles"]["entities"] > 500
    assert by["bustime getStopPredictions.jsp"]["keys"] >= 1 and by["ntas"]["keys"] >= 3
    assert fs["ntas_coverage"]["1"]["with_prediction"] >= 1 and "6" in fs["ntas_coverage"]
    overnight = await call(server_url, "routes", overnight=True)
    assert len(overnight["routes"]) == 35 and overnight["meta"]["static_feed"] == fs["static"]["feed_version"]


@pytest.mark.anyio
async def test_service_status_closures_resolve_through_the_static_station_tool(server_url):
    ss = await call(server_url, "service_status")
    assert ss["as_of"] and ss["counts"]["rsz"] >= 0 and ss["meta"]["errors"] is None
    for cl in ss["closures"][:3]:
        for sid, name in zip(cl["stations"], cl["station_names"]):
            assert (await call(server_url, "station", query=sid))["station"]["name"] == name
        assert all(len(w) == 2 for w in cl["windows"])


@pytest.mark.anyio
async def test_detours_resolve_against_static_patterns_and_mark_route_stops(server_url):
    d = await call(server_url, "detours")
    dirs = [x for g in d["detours"] for x in g["directions"]]
    assert d["count"] > 0 and d["resolved"] >= 0.8 * len(dirs)
    live = [x for g in d["detours"] if g["active_today"] for x in g["directions"] if x["pattern"] != "unknown" and x["skipped"]]
    x = live[0]
    rs = await call(server_url, "route_stops", route=x["route"], direction=x["direction_id"])
    status = {s["number"]: s["status"] for p in rs["patterns"] for s in p["stops"]}
    assert any(status.get(s["number"]) == "skipped_by_detour" for s in x["skipped"])


@pytest.mark.anyio
async def test_realtime_answers_carry_advisories(server_url):
    a = await call(server_url, "arrivals", stop="15461")
    assert "disruptions" in a and all("detour_id" in r for r in a["arrivals"]) and a["meta"]["advisories"]["staleness"] in ("fresh", "stale")
    t = await call(server_url, "next_trains", query="York Mills")
    assert all(p["service"] in ("normal", "no_prediction", "suspended") for p in t["platforms"]) and "disruptions" in t
    fs = await call(server_url, "feed_status")
    assert len(fs["sources"]) == 8 and {s["name"]: s["verdict"] for s in fs["sources"]}["alerts.ttc.ca"] == "fresh"
