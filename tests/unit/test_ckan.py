import httpx
import pytest

from ttc_mcp.adapters import ckan


def fake(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": "t/1"})


def test_resolve_zip_returns_the_zip_resource():
    def handler(req):
        assert req.url.path.endswith("/package_show")
        assert req.url.params["id"] == "merged-gtfs-ttc-routes-and-schedules"
        return httpx.Response(200, json={"result": {"resources": [
            {"format": "web", "url": "https://x/doc"},
            {"format": "ZIP", "url": "https://x/completegtfs.zip", "last_modified": "2026-09-30T14:17:52", "size": 84337545}]}})

    ref = ckan.resolve_zip(fake(handler), "merged-gtfs-ttc-routes-and-schedules")
    assert ref.url == "https://x/completegtfs.zip"
    assert ref.last_modified == "2026-09-30T14:17:52" and ref.size == 84337545


def test_resolve_zip_raises_when_no_zip():
    with pytest.raises(ckan.CkanError):
        ckan.resolve_zip(fake(lambda r: httpx.Response(200, json={"result": {"resources": []}})), "x")


def test_head_etag_reads_the_header_and_tolerates_absence():
    assert ckan.head_etag(fake(lambda r: httpx.Response(200, headers={"ETag": '"abc"'})), "https://x/a.zip") == '"abc"'
    assert ckan.head_etag(fake(lambda r: httpx.Response(200)), "https://x/a.zip") is None


def test_download_streams_to_dest_and_returns_etag(tmp_path):
    body = b"PK\x05\x06" + b"\0" * 18

    def handler(req):
        assert req.headers["User-Agent"] == "t/1"
        return httpx.Response(200, content=body, headers={"ETag": '"zip1"'})

    dest, etag = ckan.download(fake(handler), "https://x/a.zip", tmp_path / "a.zip")
    assert dest.read_bytes() == body and etag == '"zip1"'
    assert not (tmp_path / "a.zip.part").exists()


def test_make_client_sets_user_agent():
    c = ckan.make_client("ttc-mcp/0.1")
    assert c.headers["User-Agent"] == "ttc-mcp/0.1"


def test_the_client_gives_up_connecting_in_seconds_but_waits_for_big_downloads():
    """An unreachable portal should fail a check quickly; the 80 MB zips still get their two minutes per read."""
    with ckan.make_client("ttc-mcp/0.1") as c:
        assert (c.timeout.connect, c.timeout.read, c.timeout.write) == (10.0, 120.0, 120.0)
