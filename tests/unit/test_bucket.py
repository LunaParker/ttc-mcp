import json
from pathlib import Path

import pytest

from ttc_mcp.static import bucket
from ttc_mcp.static.manifest import NAME, FeedEntry, Manifest


def _store(cache: Path, *versions):
    """A cache dir holding one fake sqlite per version plus the manifest naming them (pull and push never open the files)."""
    m = Manifest([], checked_at="2026-10-11T04:30:00-04:00", ckan_last_modified="2026-09-30T14:17:52")
    for i, v in enumerate(versions):
        (cache / f"static-{v}.sqlite").write_bytes(b"x" * (1000 + i))
        m.upsert(FeedEntry(v, "20260901", "20261031", f"static-{v}.sqlite", '"e"', "https://x/merged.zip", "2026-10-11T04:30:00-0400"))
    m.save(cache)
    return m


class Spy:
    """Records every call so a test can prove the order: files before the manifest on pull, manifest last on push."""

    def __init__(self, cache_dir):
        self.inner, self.calls, self.cache_dir = bucket.MemoryBlobs(), [], cache_dir

    def __getattr__(self, name):
        fn = getattr(self.inner, name)

        def call(*a):
            self.calls.append((name, a[0], (self.cache_dir / NAME).exists()))
            return fn(*a)
        return call


def test_push_uploads_the_listed_feeds_then_the_manifest_and_skips_unchanged_files(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _store(cache, "S1", "S2")
    spy = Spy(cache)
    assert bucket.push(cache, spy) == ["static-S1.sqlite", "static-S2.sqlite", NAME]
    assert [c[0] for c in spy.calls if c[0] in ("upload", "write")] == ["upload", "upload", "write"]
    assert json.loads(spy.inner.read(NAME))["feeds"][1]["feed_version"] == "S2" and spy.inner.size("static-S2.sqlite") == 1001
    assert bucket.push(cache, spy) == [NAME]


def test_pull_brings_the_feeds_then_writes_the_manifest_last_and_reuses_same_size_files(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir(), dst.mkdir()
    _store(src, "S1", "S2")
    blobs = bucket.MemoryBlobs()
    bucket.push(src, blobs)
    spy = Spy(dst)
    spy.inner = blobs
    m = bucket.pull(dst, spy)
    assert [f.feed_version for f in m.feeds] == ["S1", "S2"] and Manifest.load(dst).checked_at == "2026-10-11T04:30:00-04:00"
    assert (dst / "static-S2.sqlite").read_bytes() == b"x" * 1001
    downloads = [c for c in spy.calls if c[0] == "download"]
    assert len(downloads) == 2 and not any(c[2] for c in downloads)
    spy.calls.clear()
    bucket.pull(dst, spy)
    assert not any(c[0] == "download" for c in spy.calls)
    (dst / "static-S1.sqlite").write_bytes(b"stale")
    bucket.pull(dst, spy)
    assert (dst / "static-S1.sqlite").read_bytes() == b"x" * 1000


def test_pull_from_an_empty_bucket_leaves_the_cache_alone_and_returns_none(tmp_path):
    assert bucket.pull(tmp_path, bucket.MemoryBlobs()) is None and not (tmp_path / NAME).exists()


def test_a_partial_download_never_becomes_the_file(tmp_path, monkeypatch):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir(), dst.mkdir()
    _store(src, "S1")
    blobs = bucket.MemoryBlobs()
    bucket.push(src, blobs)

    def broken(name, path):
        path.write_bytes(b"half")
        raise OSError("connection reset")
    monkeypatch.setattr(blobs, "download", broken)
    with pytest.raises(OSError):
        bucket.pull(dst, blobs)
    assert not (dst / "static-S1.sqlite").exists() and not (dst / "static-S1.part").exists() and not (dst / NAME).exists()


def test_pull_removes_feed_files_the_new_manifest_no_longer_lists(tmp_path):
    """Cloud Run's disk is instance memory: a feed the Job's manifest dropped must not stay on the serving instance."""
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir(), dst.mkdir()
    _store(src, "S1", "S2")
    blobs = bucket.MemoryBlobs()
    bucket.push(src, blobs)
    bucket.pull(dst, blobs)
    assert (dst / "static-S1.sqlite").exists() and (dst / "static-S2.sqlite").exists()
    m = Manifest.load(src)
    m.feeds = [f for f in m.feeds if f.feed_version == "S2"]
    m.save(src)
    bucket.push(src, blobs)
    m = bucket.pull(dst, blobs)
    assert [f.feed_version for f in m.feeds] == ["S2"] and [f.feed_version for f in Manifest.load(dst).feeds] == ["S2"]
    assert not (dst / "static-S1.sqlite").exists() and (dst / "static-S2.sqlite").read_bytes() == b"x" * 1001
    assert not list(dst.glob("*.part"))


class FakeBlob:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def reload(self):
        from google.api_core.exceptions import NotFound
        if self.name not in self.store:
            raise NotFound(self.name)
        self.size = len(self.store[self.name])

    def download_as_bytes(self):
        return self.store[self.name]

    def upload_from_string(self, data):
        self.store[self.name] = data

    def download_to_filename(self, path):
        Path(path).write_bytes(self.store[self.name])

    def upload_from_filename(self, path):
        self.store[self.name] = Path(path).read_bytes()


class FakeClient:
    def __init__(self):
        self.store = {}

    def bucket(self, name):
        assert name == "ttc-mcp-static-test"
        return self

    def blob(self, name):
        return FakeBlob(self.store, name)


def test_gcs_blobs_drive_the_client_api_for_each_operation(tmp_path):
    g = bucket.GcsBlobs("ttc-mcp-static-test", client=FakeClient())
    assert g.read("manifest.json") is None and g.size("manifest.json") is None and g.download("x", tmp_path / "x") is False
    g.write("manifest.json", b"{}")
    assert g.read("manifest.json") == b"{}" and g.size("manifest.json") == 2
    (tmp_path / "f").write_bytes(b"abc")
    g.upload("static-S1.sqlite", tmp_path / "f")
    assert g.download("static-S1.sqlite", tmp_path / "g") is True and (tmp_path / "g").read_bytes() == b"abc"
