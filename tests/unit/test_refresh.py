import datetime as dt

import httpx

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
    rep = ensure_static(tmp_path, client(calls=calls), today=dt.date(2026, 10, 5))
    assert rep["changed"] and rep["feed_version"] == "S1000538"
    m = Manifest.load(tmp_path)
    assert [f.feed_version for f in m.feeds] == ["S1000538"] and (tmp_path / m.feeds[0].path).exists()
    assert m.feeds[0].etag == '"m1"' and m.feeds[0].surface_etag == '"s1"'
    assert any(c[0] == "GET" and c[1].endswith("merged.zip") for c in calls)


def test_second_run_with_same_etags_does_nothing(tmp_path):
    ensure_static(tmp_path, client(), today=dt.date(2026, 10, 5))
    calls = []
    rep = ensure_static(tmp_path, client(calls=calls), today=dt.date(2026, 10, 5))
    assert rep["changed"] is False and rep["reason"] == "etags unchanged"
    assert not any(c[0] == "GET" and c[1].endswith(".zip") for c in calls)


def test_changed_etag_rebuilds_and_force_rebuilds(tmp_path):
    ensure_static(tmp_path, client(), today=dt.date(2026, 10, 5))
    assert ensure_static(tmp_path, client(etag_merged='"m2"'), today=dt.date(2026, 10, 5))["changed"] is True
    assert ensure_static(tmp_path, client(etag_merged='"m2"'), today=dt.date(2026, 10, 5), force=True)["changed"] is True


def test_build_from_zips_adds_a_feed_without_network(tmp_path):
    rep = build_from_zips(tmp_path, MERGED, SURFACE, today=dt.date(2026, 10, 5))
    assert rep["changed"] and Manifest.load(tmp_path).feeds[0].feed_version == "S1000538"
