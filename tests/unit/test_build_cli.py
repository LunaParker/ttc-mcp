import json
from pathlib import Path

from tests.conftest import FIX
from tests.unit.test_refresh import client
from ttc_mcp import build
from ttc_mcp.static import bucket
from ttc_mcp.static.manifest import NAME


def test_build_from_zips_pushes_the_store_when_a_bucket_is_named(tmp_path, monkeypatch, capsys):
    blobs = bucket.MemoryBlobs()
    monkeypatch.setattr(build, "GcsBlobs", lambda name: blobs)
    rc = build.main(["--from-zip", str(FIX / "gtfs-mini.zip"), "--surface-zip", str(FIX / "surface-mini.zip"), "--cache", str(tmp_path),
                     "--bucket", "ttc-mcp-static-test", "--no-checks", "--no-ttcapi"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 0 and out["pushed"] == ["static-S1000538.sqlite", NAME] and json.loads(blobs.read(NAME))["feeds"][0]["feed_version"] == "S1000538"


def test_build_without_a_bucket_pushes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TTC_MCP_STATIC_BUCKET", raising=False)
    monkeypatch.setattr(build, "GcsBlobs", lambda name: (_ for _ in ()).throw(AssertionError("no bucket expected")))
    build.main(["--from-zip", str(FIX / "gtfs-mini.zip"), "--surface-zip", str(FIX / "surface-mini.zip"), "--cache", str(tmp_path), "--no-checks", "--no-ttcapi"])
    assert "pushed" not in json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _report(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _from_zip_into(blobs, cache, monkeypatch, capsys) -> dict:
    monkeypatch.setattr(build, "GcsBlobs", lambda name: blobs)
    build.main(["--from-zip", str(FIX / "gtfs-mini.zip"), "--surface-zip", str(FIX / "surface-mini.zip"), "--cache", str(cache),
                "--bucket", "b", "--no-checks", "--no-ttcapi"])
    return _report(capsys)


def test_build_with_a_bucket_pulls_the_bucket_first_so_a_second_run_is_etags_unchanged(tmp_path, monkeypatch, capsys):
    """A Job execution starts with an empty disk: the bucket's manifest is what CKAN's ETags are compared against."""
    blobs, calls, fresh = bucket.MemoryBlobs(), [], tmp_path / "fresh"
    _from_zip_into(blobs, tmp_path / "src", monkeypatch, capsys)
    doc = json.loads(blobs.read(NAME))
    doc["feeds"][0].update(etag='"m1"', surface_etag='"s1"', source_url="https://x/merged.zip")
    blobs.write(NAME, json.dumps(doc).encode())
    monkeypatch.setattr(build, "make_client", lambda ua: client(calls=calls))
    fresh.mkdir()
    build.main(["--cache", str(fresh), "--bucket", "b", "--no-checks", "--no-ttcapi"])
    out = _report(capsys)
    assert out["reason"] == "etags unchanged" and out["pulled"] == ["S1000538"] and out["pushed"] == [NAME]
    assert (fresh / "static-S1000538.sqlite").exists() and not any(c[0] == "GET" and c[1].endswith(".zip") for c in calls)


def test_build_with_an_empty_bucket_builds_and_pushes_as_a_first_run(tmp_path, monkeypatch, capsys):
    blobs = bucket.MemoryBlobs()
    monkeypatch.setattr(build, "GcsBlobs", lambda name: blobs)
    monkeypatch.setattr(build, "make_client", lambda ua: client())
    build.main(["--cache", str(tmp_path), "--bucket", "b", "--no-checks", "--no-ttcapi"])
    out = _report(capsys)
    assert out["reason"] == "built" and out["pulled"] == [] and out["pushed"] == ["static-S1000538.sqlite", NAME]


class _DownloadSpy(bucket.MemoryBlobs):
    def __init__(self) -> None:
        super().__init__()
        self.downloads: list[str] = []

    def download(self, name: str, path: Path) -> bool:
        self.downloads.append(name)
        return super().download(name, path)


def test_from_zip_never_pulls(tmp_path, monkeypatch, capsys):
    blobs = _DownloadSpy()
    _from_zip_into(blobs, tmp_path / "src", monkeypatch, capsys)
    out = _from_zip_into(blobs, tmp_path / "local", monkeypatch, capsys)
    assert NAME in blobs.objects and blobs.downloads == [] and "pulled" not in out
