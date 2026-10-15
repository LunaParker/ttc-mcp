"""Live integration of phases 1 and 2: the real server over streamable HTTP, the local store, the TTC's endpoints.

Run: uv run pytest tests/integration -v   (needs ~/.cache/ttc-mcp from ttc-mcp-build and network access; not part of the unit suite)
"""
import datetime as dt

import pytest
from fastmcp import Client

from tests.integration.live import call

CHERRY_FRONT = {"lat": 43.6527, "lon": -79.3581}
STATIC = {"find_stops", "nearest_stops", "stop", "station", "routes", "route_stops", "service_hours", "scheduled_departures"}
REALTIME = {"arrivals", "next_trains", "vehicles", "feed_status"}


@pytest.mark.anyio
async def test_server_lists_both_phases_tools_and_nothing_is_fetched_before_asked(fresh_server_url):
    async with Client(fresh_server_url) as c:
        names = {t.name for t in await c.list_tools()}
    assert names >= STATIC | REALTIME
    fs = await call(fresh_server_url, "feed_status")
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
    # NEW trips carry negative ids and have no static row (spec 3): 6 of 28 504 vehicles at 01:20 on 2026-10-15.
    with_trip = [v for v in veh["vehicles"] if v["trip_id"] and not v["trip_id"].startswith("-")]
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
        for sid, name in zip(cl["stations"], cl["station_names"], strict=True):
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
    assert len(fs["sources"]) == 9 and {s["name"]: s["verdict"] for s in fs["sources"]}["alerts.ttc.ca"] == "fresh"


@pytest.mark.anyio
async def test_route_shape_matches_route_stops_and_the_store(server_url):
    rs = await call(server_url, "route_stops", route="504", direction="0")
    sh = await call(server_url, "route_shape", route="504", direction="0")
    assert [s["number"] for s in sh["stops"]] == [s["number"] for s in rs["patterns"][0]["stops"]]
    assert sh["length_m"] > 9000 and sh["stops"][-1]["dist_m"] <= sh["length_m"] + 100 and sh["polyline"]
    g = await call(server_url, "route_shape", route="1", direction="0", format="geojson", simplify_m=25)
    assert g["geometry"]["type"] == "LineString" and g["points"] < 874


@pytest.mark.anyio
async def test_accessibility_agrees_with_station_and_alerts(server_url):
    units = [a for a in (await call(server_url, "alerts", type="elevator"))["alerts"] if a["stations"]]
    if not units:
        pytest.skip("no elevator outage with a resolvable station right now")
    a = units[0]
    acc = (await call(server_url, "accessibility", query=a["stations"][0]))["accessibility"]
    u = next(x for x in acc["units"] if x["code"] == a["unit_code"])
    assert u["status"] == "out_of_service" and u["alert_id"] == a["alert_id"] and acc["step_free"]["verdict"] == "no"
    st = (await call(server_url, "station", query=a["stations"][0]))["station"]
    assert acc["summary"] == st["wheelchair_summary"] and {p["platform"] for p in acc["platforms"]} == {p["number"] for p in st["platforms"]}


@pytest.mark.anyio
async def test_connections_use_live_arrivals_and_the_advisory_snapshot(server_url):
    r = await call(server_url, "connections", origin=CHERRY_FRONT, destination="Union")
    assert r["meta"]["advisories"]["errors"] is None
    if not r["options"]:
        # Late at night a pair can have no trip at all; the planner then says why.
        assert r["note"].startswith(("no service", "no journey", "every journey needs a closed section")), r["note"]
        return
    first = next(o for o in r["options"] if next(l for l in o["legs"] if l["mode"] in ("ride", "shuttle"))["mode"] == "ride")
    ride = next(l for l in first["legs"] if l["mode"] == "ride")
    assert ride["trip_id"] and ride["next"] and first["basis"] in ("realtime", "schedule", "estimate")
    arr = await call(server_url, "arrivals", stop=ride["board"]["number"], route=ride["route"], limit=10)
    # The next rows start at the option's own departure, so the first live one is among the stop's cached predictions.
    live_next = [n["eta_min"] for n in ride["next"] if n["basis"] == "realtime"]
    assert live_next[:1] == [] or live_next[0] in [a["eta_min"] for a in arr["arrivals"]] or arr["meta"]["cache"] == "live"
    fs = await call(server_url, "feed_status")
    assert fs["static"]["feed_version"] == r["meta"]["static_feed"]
