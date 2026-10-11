"""Cross-phase invariants over the live server: one disruption must read the same from the static, real-time and advisory tools."""
import pytest

from tests.integration.live import call, ids


def _norm(title: str) -> str:
    return " ".join((title or "").replace("–", "-").split()).lower()


@pytest.mark.anyio
async def test_a_closure_reads_the_same_from_service_status_station_next_trains_and_arrivals(server_url):
    ss = await call(server_url, "service_status")
    if not ss["closures"]:
        pytest.skip("no subway closure published right now")
    cl = ss["closures"][0]
    line = cl["route"]
    for sid in cl["stations"]:
        st = (await call(server_url, "station", query=sid))["station"]
        assert cl["alert_id"] in ids(st["disruptions"])
        svc = {s["line"]: s for s in st["service"]}[line]
        # A span end station is served on its open side: reduced, with the closure carrying the terminus role (F1).
        terminus = cl["active_now"] and sid in (cl["stations"][0], cl["stations"][-1]) and svc["service"] == "reduced"
        assert svc["service"] in ("reduced", "suspended") and (svc["service"] == "suspended") == (cl["active_now"] and not terminus)
        if cl["active_now"]:
            assert svc["disruption"]["alert_id"] == cl["alert_id"] and (svc["disruption"].get("role") == "shuttle_terminus") == terminus
        trains = await call(server_url, "next_trains", query=sid)
        assert cl["alert_id"] in ids(trains["disruptions"])
        on_line = [p for p in trains["platforms"] if p["line"] == line]
        assert on_line and all((p["service"] == "suspended") == cl["active_now"] for p in on_line if not terminus)
        assert not terminus or any(p["service"] != "suspended" for p in on_line)
        status = {p["number"]: p["status"] for p in st["platforms"]}
        assert all((status[p["platform"]] == "closed") == (p["service"] == "suspended" if cl["active_now"] else False) for p in on_line)
        other_lines = [p for p in trains["platforms"] if p["line"] != line]
        assert all(p["service"] != "suspended" for p in other_lines) or cl["active_now"] is False
        arr = await call(server_url, "arrivals", stop=on_line[0]["platform"])
        assert cl["alert_id"] in ids(arr["disruptions"])


@pytest.mark.anyio
async def test_an_active_detour_is_visible_from_every_phase(server_url):
    d = await call(server_url, "detours")
    live = [(g, x) for g in d["detours"] if g["active_today"] for x in g["directions"] if x["pattern"] != "unknown" and x["skipped"]]
    if not live:
        pytest.skip("no active detour with resolved skipped stops right now")
    g, x = live[0]
    skipped = x["skipped"][0]["number"]
    stop = (await call(server_url, "stop", number=skipped))["stop"]
    assert g["detour_id"] in ids(stop["disruptions"], "detour_id")
    al = await call(server_url, "alerts", stop=skipped, type="detour")
    assert g["detour_id"] in ids(al["alerts"], "detour_id")
    arr = await call(server_url, "arrivals", stop=skipped, route=x["route"])
    assert g["detour_id"] in ids(arr["disruptions"], "detour_id") and all("detour_id" in r for r in arr["arrivals"])
    ss = await call(server_url, "service_status")
    surf = next(s for s in ss["surface"] if s.get("detour_id") == g["detour_id"])
    assert surf["route"] == g["route"] and surf["skipped_stops"] == len(g["skipped_stops"]) and surf["reason"] == (g["reason"] or {}).get("title")
    if x["leaves_after"]:
        board = await call(server_url, "arrivals", stop=x["leaves_after"]["number"], route=x["route"])
        on_route = {gg["detour_id"] for gg in d["detours"] if gg["route"] == x["route"]}
        flagged = {r["detour_id"] for r in board["arrivals"] if r["basis"] == "realtime" and r["detour_id"]}
        assert flagged <= on_route
        if any(r["basis"] == "realtime" for r in board["arrivals"]):
            fs = await call(server_url, "feed_status")
            assert {s["name"]: s for s in fs["sources"]}["bustime trips"]["verdict"] == "fresh"


@pytest.mark.anyio
async def test_reduced_speed_zones_mark_their_stations_reduced(server_url):
    zones = [a for a in (await call(server_url, "alerts", type="rsz"))["alerts"] if a["stations"]]
    if not zones:
        pytest.skip("no reduced speed zone published right now")
    a = zones[0]
    for sid in a["stations"][:2]:
        st = (await call(server_url, "station", query=sid))["station"]
        assert a["alert_id"] in ids(st["disruptions"])
        svc = next(s for s in st["service"] if s["line"] == a["route"])
        assert svc["service"] in ("reduced", "suspended")
        assert a["alert_id"] in ids((await call(server_url, "alerts", station=sid, type="rsz"))["alerts"])


@pytest.mark.anyio
async def test_elevator_outages_reach_station_nearest_stops_and_service_status(server_url):
    units = [a for a in (await call(server_url, "alerts", type="elevator"))["alerts"] if a["stations"]]
    if not units:
        pytest.skip("no elevator outage with a resolvable station right now")
    a = units[0]
    sid = a["stations"][0]
    st = (await call(server_url, "station", query=sid))["station"]
    assert a["unit_code"] in ids(st["disruptions"], "unit_code")
    near = await call(server_url, "nearest_stops", lat=st["lat"], lon=st["lon"], kind="station", radius_m=150, limit=3)
    me = next(r for r in near["results"] if r["number"] == sid)
    assert a["unit_code"] in ids(me["disruptions"], "unit_code")
    ss = await call(server_url, "service_status")
    assert a["unit_code"] in ss["elevators_out"].get(st["name"], [])


@pytest.mark.anyio
async def test_aliases_resolve_identically_in_static_realtime_and_advisory_tools(server_url):
    for alias in ("Dundas", "Bloor", "VMC"):
        st = (await call(server_url, "station", query=alias))["station"]
        trains = await call(server_url, "next_trains", query=alias)
        al = await call(server_url, "alerts", station=alias)
        assert trains["station"]["id"] == st["id"]
        assert {p["platform"] for p in trains["platforms"]} <= {p["number"] for p in st["platforms"]}
        assert all(st["id"] in a["stations"] for a in al["alerts"])


@pytest.mark.anyio
async def test_detour_reasons_match_service_changes(server_url):
    with_reason = [g for g in (await call(server_url, "detours"))["detours"] if g["reason"]]
    if not with_reason:
        pytest.skip("no detour carries a reason right now")
    for g in with_reason[:3]:
        titles = {_norm(c["title"]) for c in (await call(server_url, "service_changes", route=g["route"]))["changes"]}
        assert _norm(g["reason"]["title"]) in titles


@pytest.mark.anyio
async def test_meta_is_coherent_across_every_phase(server_url):
    answers = [await call(server_url, name, **args) for name, args in (
        ("routes", {"overnight": True}), ("arrivals", {"stop": "15461"}), ("next_trains", {"query": "Union"}),
        ("service_status", {}), ("station", {"query": "Union"}), ("detours", {}))]
    feeds = {a["meta"]["static_feed"] for a in answers}
    assert len(feeds) == 1
    for a in answers:
        assert a["meta"]["staleness"] in ("fresh", "stale", "unavailable")
        if "advisories" in a["meta"]:
            assert a["meta"]["advisories"]["errors"] is None and a["meta"]["advisories"]["cache"] in ("live", "ttl")
    fs = await call(server_url, "feed_status")
    by = {s["name"]: s for s in fs["sources"]}
    assert fs["static"]["feed_version"] in feeds and "1" in fs["ntas_coverage"]
    assert all(by[n]["verdict"] == "fresh" for n in ("alerts.ttc.ca", "gtfsrt alerts", "detour feed", "bustime getStopPredictions.jsp", "ntas"))
