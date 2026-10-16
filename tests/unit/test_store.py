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
    assert st["verdict"] == "fresh" and st["newer_package_seen"] is True and st["active"] == "S1000542"
    store.close()


def test_status_carries_the_manifests_check_time_and_ckan_refresh(tmp_path, fixture_db):
    cache = _cache(tmp_path, fixture_db, entry("S1000542", "20260930", "20261031", "s42.sqlite"))
    m = Manifest.load(cache)
    assert (m.checked_at, m.ckan_last_modified) == (None, None)
    m.checked_at, m.ckan_last_modified = "2026-10-06T03:00:00-04:00", "2026-09-30T14:17:52"
    m.save(cache)
    store = StaticStore.from_manifest(cache)
    st = store.status(dt.date(2026, 10, 5))
    assert (st["last_check"], st["ckan_last_refreshed"], st["newer_package_seen"]) == ("2026-10-06T03:00:00-04:00", "2026-09-30T14:17:52", False)
    assert "newer_seen" not in st
    store.close()


def test_stores_with_another_schema_version_are_skipped(tmp_path, fixture_db):
    import shutil
    import sqlite3

    from ttc_mcp.static.manifest import FeedEntry, Manifest
    from ttc_mcp.static.schema import SCHEMA_VERSION
    from ttc_mcp.static.store import StaticStore, schema_version

    shutil.copy(fixture_db, tmp_path / "new.sqlite")
    shutil.copy(fixture_db, tmp_path / "old.sqlite")
    c = sqlite3.connect(tmp_path / "old.sqlite")
    c.execute("ALTER TABLE feed_info DROP COLUMN schema_version")
    c.commit()
    c.close()
    sqlite3.connect(tmp_path / "empty.sqlite").close()
    Manifest([FeedEntry("S1", "20260906", "20261031", "new.sqlite", None, "u", "t"),
              FeedEntry("S0", "20260801", "20260905", "old.sqlite", None, "u", "t"),
              FeedEntry("SX", "20260101", "20260131", "empty.sqlite", None, "u", "t")]).save(tmp_path)
    assert (schema_version(tmp_path / "new.sqlite"), schema_version(tmp_path / "old.sqlite"), schema_version(tmp_path / "empty.sqlite")) == (SCHEMA_VERSION, 1, 0)
    store = StaticStore.from_manifest(tmp_path)
    assert [f.version for f in store.feeds] == ["S1"]
    store.close()


def test_an_old_or_unreadable_schema_is_skipped_with_a_rebuild_hint(tmp_path, fixture_db, capsys, monkeypatch):
    import sqlite3

    import pytest

    from ttc_mcp import runtime
    from ttc_mcp.static.schema import SCHEMA_VERSION
    from ttc_mcp.static.store import schema_version

    cache = _cache(tmp_path, fixture_db, entry("S0", "20260906", "20261031", "old.sqlite"), entry("SX", "20260906", "20261031", "odd.sqlite"))
    c = sqlite3.connect(cache / "old.sqlite")
    c.execute("ALTER TABLE feed_info DROP COLUMN schema_version")
    c.commit()
    c.close()
    c = sqlite3.connect(cache / "odd.sqlite")
    c.execute("UPDATE feed_info SET schema_version='two'")
    c.commit()
    c.close()
    assert schema_version(cache / "odd.sqlite") == 0
    assert StaticStore.from_manifest(cache).feeds == []
    err = capsys.readouterr().err
    assert f"[store] skipping old.sqlite: its schema 1 is old (this server reads {SCHEMA_VERSION}); the next ttc-mcp-build (or ttc-mcp-build --from-zip for a local feed) rebuilds it" in err
    assert "skipping odd.sqlite: its schema 0 is old" in err
    monkeypatch.setenv("TTC_MCP_CACHE", str(cache))
    runtime.reset()
    with pytest.raises(runtime.StoreUnavailable, match="a store with an old schema is skipped"):
        runtime.feed_for(dt.date(2026, 10, 6))
    runtime.reset()


def test_lookup_trip_tries_todays_feed_first_then_every_other_loaded_feed(two_feeds):
    store = StaticStore.from_manifest(two_feeds)
    today = dt.date(2026, 10, 6)
    assert [f.version for f in store.ordered(today)] == ["S1000542", "S1000538"]
    f, row = store.lookup_trip("100677020", today)
    assert (f.version, row["route_id"], row["pattern_id"]) == ("S1000538", "504", 6)
    f, row = store.lookup_trip("100125020", today)
    assert (f.version, row["headsign"]) == ("S1000542", "West - 504A King towards Dundas West Station")
    assert store.lookup_trip("nope", today) is None
    store.close()


def test_get_store_keeps_its_connections_over_a_check_only_manifest_save_and_reopens_on_a_feed_change(app_env, fixture_db):
    cache = app_env.settings().cache_dir
    store = app_env.get_store()
    feed = store.for_date(dt.date(2026, 10, 6))
    m = Manifest.load(cache)
    m.checked_at, m.ckan_last_modified = "2026-10-06T03:00:00-04:00", "2026-09-30T14:17:52"
    m.save(cache)
    assert app_env.get_store() is store and feed.conn.execute("SELECT 1").fetchone()[0] == 1
    st = app_env.get_store().status(dt.date(2026, 10, 6))
    assert (st["last_check"], st["ckan_last_refreshed"]) == ("2026-10-06T03:00:00-04:00", "2026-09-30T14:17:52")
    shutil.copy(fixture_db, cache / "s42.sqlite")
    m.upsert(entry("S1000542", "20260930", "20261031", "s42.sqlite"))
    m.save(cache)
    reopened = app_env.get_store()
    assert reopened is not store and [f.version for f in reopened.feeds] == ["S1000538", "S1000542"]
    assert reopened.status(dt.date(2026, 10, 6))["last_check"] == "2026-10-06T03:00:00-04:00"


def test_each_thread_gets_its_own_read_only_connection_and_close_closes_them_all(fixture_db):
    """SQLite in multi-thread mode forbids using one connection from two threads at once; the store hands each thread its own."""
    import sqlite3
    import threading

    import pytest

    from ttc_mcp.static.store import FeedDb

    feed = FeedDb("S1000538", dt.date(2026, 9, 1), dt.date(2026, 10, 31), fixture_db)
    main = feed.conn
    assert feed.conn is main and main.row_factory is sqlite3.Row
    seen = {}

    def worker():
        seen["conn"] = feed.conn
        seen["name"] = feed.conn.execute("SELECT name FROM stops WHERE stop_id='15772'").fetchone()["name"]
    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen["conn"] is not main and seen["name"] == main.execute("SELECT name FROM stops WHERE stop_id='15772'").fetchone()["name"]
    feed.close()
    with pytest.raises(sqlite3.ProgrammingError):
        main.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        seen["conn"].execute("SELECT 1")
    assert feed.conn.execute("SELECT 1").fetchone()[0] == 1
    feed.close()


def test_store_feeds_open_lazily_and_share_nothing_across_threads(tmp_path, fixture_db):
    import threading

    store = StaticStore.from_manifest(_cache(tmp_path, fixture_db, entry("S1000538", "20260901", "20261031", "static-S1000538.sqlite")))
    feed = store.feeds[0]
    conns = []
    threads = [threading.Thread(target=lambda: conns.append(feed.conn)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({id(c) for c in conns}) == 3 and all(c is not feed.conn for c in conns)
    store.close()


def test_a_threads_connection_is_closed_and_forgotten_when_the_thread_exits(fixture_db):
    """anyio stops worker threads idle for 10 s; their connections must not pile up on a long-lived server."""
    import gc
    import sqlite3
    import threading

    import pytest

    from ttc_mcp.static.store import FeedDb

    feed = FeedDb("S1000538", dt.date(2026, 9, 1), dt.date(2026, 10, 31), fixture_db)
    main = feed.conn
    seen = []
    t = threading.Thread(target=lambda: seen.append(feed.conn))
    t.start()
    t.join()
    gc.collect()
    assert seen[0] is not main and feed._opened == [main]
    with pytest.raises(sqlite3.ProgrammingError):
        seen[0].execute("SELECT 1")
    assert main.execute("SELECT 1").fetchone()[0] == 1
    feed.close()
