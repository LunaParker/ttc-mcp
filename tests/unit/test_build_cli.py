import json

from tests.conftest import FIX
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
