import datetime as dt
import shutil

from ttc_mcp.static.manifest import FeedEntry, Manifest
from ttc_mcp.static.store import StaticStore


def _cache(tmp_path, fixture_db, *entries):
    for e in entries:
        shutil.copy(fixture_db, tmp_path / e.path)
    Manifest(list(entries)).save(tmp_path)
    return tmp_path


def entry(version, start, end, path):
    return FeedEntry(feed_version=version, start=start, end=end, path=path, etag='"e"', source_url="u", built_at="t", surface_etag=None)


def test_manifest_round_trips_and_upserts_by_version(tmp_path):
    m = Manifest([])
    m.upsert(entry("S1", "20260906", "20261031", "a.sqlite"))
    m.upsert(entry("S1", "20260906", "20261031", "b.sqlite"))
    m.save(tmp_path)
    loaded = Manifest.load(tmp_path)
    assert [f.path for f in loaded.feeds] == ["b.sqlite"]
    assert Manifest.load(tmp_path / "nowhere").feeds == []


def test_prune_drops_feeds_that_ended_before_yesterday_but_keeps_the_newest(tmp_path):
    m = Manifest([entry("S0", "20260701", "20260905", "o.sqlite"), entry("S1", "20260906", "20261031", "a.sqlite")])
    m.prune(dt.date(2026, 10, 5))
    assert [f.feed_version for f in m.feeds] == ["S1"]
    m2 = Manifest([entry("S0", "20260701", "20260905", "o.sqlite")])
    m2.prune(dt.date(2026, 10, 5))
    assert [f.feed_version for f in m2.feeds] == ["S0"]


def test_store_picks_the_newest_feed_covering_a_date(tmp_path, fixture_db):
    cache = _cache(tmp_path, fixture_db, entry("S1000538", "20260906", "20261031", "s38.sqlite"),
                   entry("S1000542", "20260930", "20261031", "s42.sqlite"))
    store = StaticStore.from_manifest(cache)
    assert store.for_date(dt.date(2026, 9, 15)).version == "S1000538"
    assert store.for_date(dt.date(2026, 10, 5)).version == "S1000542"
    assert store.for_date(dt.date(2026, 12, 1)) is None
    assert store.newest().version == "S1000542"
    store.close()


def test_status_verdicts(tmp_path, fixture_db):
    cache = _cache(tmp_path, fixture_db, entry("S1000542", "20260930", "20261031", "s42.sqlite"))
    store = StaticStore.from_manifest(cache)
    assert store.status(dt.date(2026, 10, 5))["verdict"] == "fresh"
    assert store.status(dt.date(2026, 10, 29))["verdict"] == "expiring"
    assert store.status(dt.date(2026, 11, 2))["verdict"] == "expired"
    store.close()
    assert StaticStore.from_manifest(tmp_path / "empty").status(dt.date(2026, 10, 5))["verdict"] == "unavailable"


def test_status_is_fresh_near_the_end_when_a_newer_feed_is_loaded(tmp_path, fixture_db):
    cache = _cache(tmp_path, fixture_db, entry("S1000542", "20260930", "20261031", "s42.sqlite"),
                   entry("S1000550", "20261101", "20261219", "s50.sqlite"))
    store = StaticStore.from_manifest(cache)
    st = store.status(dt.date(2026, 10, 30))
    assert st["verdict"] == "fresh" and st["newer_seen"] is True and st["active"] == "S1000542"
    store.close()
