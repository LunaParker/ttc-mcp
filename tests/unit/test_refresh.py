import datetime as dt
import sqlite3

import httpx
import pytest

from tests.conftest import FIX
from ttc_mcp.static.manifest import Manifest
from ttc_mcp.static.refresh import build_from_zips, ensure_static

MERGED = FIX / "gtfs-mini.zip"
SURFACE = FIX / "surface-mini.zip"


def client(etag_merged='"m1"', etag_surface='"s1"', calls=None):
    def handler(req):
        if calls is not None:
            calls.append((req.method, str(req.url)))
        if req.url.path.endswith("/package_show"):
            pid = req.url.params["id"]
            url = "https://x/merged.zip" if "merged" in pid else "https://x/surface.zip"
            return httpx.Response(200, json={"result": {"resources": [{"format": "ZIP", "url": url, "last_modified": "2026-09-30T14:17:52"}]}})
        etag = etag_merged if "merged" in req.url.path else etag_surface
        if req.method == "HEAD":
            return httpx.Response(200, headers={"ETag": etag})
        body = (MERGED if "merged" in req.url.path else SURFACE).read_bytes()
        return httpx.Response(200, content=body, headers={"ETag": etag})

    return httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": "t/1"})


def test_first_run_downloads_builds_and_writes_manifest(tmp_path):
    calls = []
    rep = ensure_static(tmp_path, client(calls=calls), today=dt.date(2026, 10, 5), ttcapi=False)
    assert rep["changed"] and rep["feed_version"] == "S1000538"
    m = Manifest.load(tmp_path)
    assert [f.feed_version for f in m.feeds] == ["S1000538"] and (tmp_path / m.feeds[0].path).exists()
    assert m.feeds[0].etag == '"m1"' and m.feeds[0].surface_etag == '"s1"'
    assert any(c[0] == "GET" and c[1].endswith("merged.zip") for c in calls)


def test_second_run_with_same_etags_does_nothing(tmp_path):
    ensure_static(tmp_path, client(), today=dt.date(2026, 10, 5), ttcapi=False)
    calls = []
    rep = ensure_static(tmp_path, client(calls=calls), today=dt.date(2026, 10, 5), ttcapi=False)
    assert rep["changed"] is False and rep["reason"] == "etags unchanged"
    assert not any(c[0] == "GET" and c[1].endswith(".zip") for c in calls)


def test_changed_etag_rebuilds_and_force_rebuilds(tmp_path):
    ensure_static(tmp_path, client(), today=dt.date(2026, 10, 5), ttcapi=False)
    assert ensure_static(tmp_path, client(etag_merged='"m2"'), today=dt.date(2026, 10, 5), ttcapi=False)["changed"] is True
    assert ensure_static(tmp_path, client(etag_merged='"m2"'), today=dt.date(2026, 10, 5), force=True, ttcapi=False)["changed"] is True


def test_build_from_zips_adds_a_feed_without_network(tmp_path):
    rep = build_from_zips(tmp_path, MERGED, SURFACE, today=dt.date(2026, 10, 5))
    assert rep["changed"] and Manifest.load(tmp_path).feeds[0].feed_version == "S1000538"


def test_store_built_with_an_older_schema_is_rebuilt_even_when_etags_match(tmp_path):
    ensure_static(tmp_path, client(), today=dt.date(2026, 10, 5), ttcapi=False)
    store = tmp_path / Manifest.load(tmp_path).feeds[0].path
    db = sqlite3.connect(store)
    db.execute("ALTER TABLE feed_info DROP COLUMN schema_version")
    db.commit()
    db.close()
    calls = []
    rep = ensure_static(tmp_path, client(calls=calls), today=dt.date(2026, 10, 5), ttcapi=False)
    assert rep["changed"] is True and rep["reason"] == "schema changed"
    assert any(c[0] == "GET" and c[1].endswith("merged.zip") for c in calls)
    from ttc_mcp.static.schema import SCHEMA_VERSION
    from ttc_mcp.static.store import schema_version

    assert schema_version(store) == SCHEMA_VERSION
    rep = ensure_static(tmp_path, client(), today=dt.date(2026, 10, 5), ttcapi=False)
    assert rep["changed"] is False and rep["reason"] == "etags unchanged"


def test_ensure_static_fetches_service_levels_with_the_sync_clients_user_agent(tmp_path, monkeypatch):
    agents, msgs = [], []
    level = {"serviceLevel": {"name": "Regular service", "description": "d"}, "is10MinutesNetwork": True, "frequency": "Frequent",
             "routeType": 900, "mapUrl": "u"}

    def fake_async_client(user_agent, timeout=10.0):
        agents.append(user_agent)

        def handler(req):
            return httpx.Response(200, json={"route": level}) if req.url.params.get("id") == "504" else httpx.Response(404)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": user_agent})

    monkeypatch.setattr("ttc_mcp.adapters.http.make_async_client", fake_async_client)
    rep = ensure_static(tmp_path, client(), today=dt.date(2026, 10, 5), log=msgs.append)
    db = sqlite3.connect(tmp_path / Manifest.load(tmp_path).feeds[0].path)
    row = db.execute("SELECT service_level_name, ten_minute, map_url FROM routes WHERE short_name='504'").fetchone()
    db.close()
    assert rep["changed"] and agents == ["t/1"] and row == ("Regular service", 1, "u")
    assert "[refresh] ttc.ca service levels: 1 of 6 routes" in msgs


def _levels_from(monkeypatch, answers):
    """ttc.ca's route detail answering the given routes and refusing the rest with 403, as its WAF does Google Cloud's egress."""
    def fake_async_client(user_agent, timeout=10.0):
        def handler(req):
            rid = req.url.params.get("id")
            return httpx.Response(200, json={"route": answers[rid]}) if rid in answers else httpx.Response(403)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": user_agent})

    monkeypatch.setattr("ttc_mcp.adapters.http.make_async_client", fake_async_client)


def _named(name):
    return {"serviceLevel": {"name": name, "description": "d"}, "is10MinutesNetwork": True, "frequency": "Frequent", "routeType": 700, "mapUrl": "u"}


def test_a_rebuild_keeps_the_service_level_of_a_route_ttc_ca_refuses(tmp_path, monkeypatch):
    _levels_from(monkeypatch, {"504": _named("Regular service"), "300": _named("Blue Night Network")})
    ensure_static(tmp_path, client(), today=dt.date(2026, 10, 5), checks=False)
    _levels_from(monkeypatch, {"504": _named("Express service")})
    msgs = []
    ensure_static(tmp_path, client(etag_merged='"m2"'), today=dt.date(2026, 10, 5), checks=False, log=msgs.append)
    db = sqlite3.connect(tmp_path / Manifest.load(tmp_path).feeds[0].path)
    rows = db.execute("SELECT short_name, service_level_name, ten_minute, map_url FROM routes WHERE service_level_name IS NOT NULL ORDER BY short_name").fetchall()
    db.close()
    assert rows == [("300", "Blue Night Network", 1, "u"), ("504", "Express service", 1, "u")]
    assert "[refresh] ttc.ca service levels: 1 of 6 routes; 1 carried over from the loaded store" in msgs


def test_every_run_records_the_check_time_and_ckans_last_modified(tmp_path):
    ensure_static(tmp_path, client(), today=dt.date(2026, 10, 5), ttcapi=False)
    m = Manifest.load(tmp_path)
    assert m.ckan_last_modified == "2026-09-30T14:17:52" and m.checked_at.endswith(("-04:00", "-05:00"))
    first = dt.datetime.fromisoformat(m.checked_at)
    assert abs((dt.datetime.now(first.tzinfo) - first).total_seconds()) < 60
    rep = ensure_static(tmp_path, client(), today=dt.date(2026, 10, 5), ttcapi=False)
    m2 = Manifest.load(tmp_path)
    assert rep["changed"] is False and dt.datetime.fromisoformat(m2.checked_at) >= first and m2.ckan_last_modified == "2026-09-30T14:17:52"
    assert [f.feed_version for f in m2.feeds] == ["S1000538"]
    rep = build_from_zips(tmp_path, MERGED, SURFACE, today=dt.date(2026, 10, 5))
    assert rep["changed"] and Manifest.load(tmp_path).checked_at == m2.checked_at


def test_a_build_runs_the_static_checks_and_the_live_ones_when_alerts_answer(tmp_path):
    """Spec 8 items 3, 9 and 20: every rebuild logs the suffix, alert-station, unit-code and two index checks; an unchanged store runs none."""
    lines = []
    base = client()
    handler = base._transport.handler  # noqa: SLF001

    def routed(req):
        if req.url.host == "alerts.ttc.ca":
            return httpx.Response(200, content=(FIX / "advisories" / "alerts.json").read_bytes())
        return handler(req)
    live = httpx.Client(transport=httpx.MockTransport(routed), headers={"User-Agent": "t/1"})
    rep = ensure_static(tmp_path, live, today=dt.date(2026, 10, 5), ttcapi=False, log=lines.append)
    assert rep["checks"]["suffixes"] == [] and [u["value"] for u in rep["checks"]["alert_stations"]] == ["4917"] and rep["checks"]["unit_codes"]["codes"] == 2
    assert sum(l.startswith("[check]") for l in lines) == 5
    assert "checks" not in ensure_static(tmp_path, live, today=dt.date(2026, 10, 5), ttcapi=False, log=lines.append)
    lines.clear()
    rep = ensure_static(tmp_path, client(etag_merged='"m2"'), today=dt.date(2026, 10, 5), ttcapi=False, log=lines.append)
    assert rep["checks"]["unit_codes"] is None and any("alerts.ttc.ca unavailable" in l for l in lines)
    assert build_from_zips(tmp_path, MERGED, SURFACE, today=dt.date(2026, 10, 5))["checks"]["alert_stations"] is None
    assert "checks" not in ensure_static(tmp_path, client(etag_merged='"m2"'), today=dt.date(2026, 10, 5), ttcapi=False, checks=False, force=True)


@pytest.mark.anyio
async def test_the_service_level_fetch_works_inside_a_running_event_loop(monkeypatch):
    """Spec 6.9 carry-over: _levels_for ran asyncio.run, which raises when a loop is already running in the thread."""
    from ttc_mcp.static import refresh

    def fake_async_client(user_agent, timeout=10.0):
        def handler(req):
            return httpx.Response(200, json={"route": {"serviceLevel": {"name": "Regular"}}})
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": user_agent})
    monkeypatch.setattr("ttc_mcp.adapters.http.make_async_client", fake_async_client)
    levels = refresh._levels_for(MERGED, "t/1", None)
    assert levels and all(v["name"] == "Regular" for v in levels.values())
