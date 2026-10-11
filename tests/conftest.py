import sqlite3
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory) -> Path:
    from ttc_mcp.static.build import build_sqlite

    out = tmp_path_factory.mktemp("static") / "static-mini.sqlite"
    build_sqlite(FIX / "gtfs-mini.zip", out, FIX / "surface-mini.zip", source_url="fixture", source_etag='"fix"')
    return out


@pytest.fixture(scope="session")
def conn(fixture_db):
    c = sqlite3.connect(f"file:{fixture_db}?mode=ro", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def app_env(tmp_path, fixture_db, monkeypatch):
    """A built store in a temp cache dir, refresh off, the clock at 2026-10-06 08:00 Toronto; runtime reset around the test."""
    import datetime as dt
    import shutil

    from ttc_mcp import runtime
    from ttc_mcp.static.manifest import FeedEntry, Manifest

    shutil.copy(fixture_db, tmp_path / "static-S1000538.sqlite")
    Manifest([FeedEntry("S1000538", "20260906", "20261031", "static-S1000538.sqlite", '"e"', "u", "t")]).save(tmp_path)
    monkeypatch.setenv("TTC_MCP_CACHE", str(tmp_path))
    monkeypatch.setenv("TTC_MCP_SKIP_REFRESH", "1")
    monkeypatch.setattr(runtime, "now", lambda: dt.datetime(2026, 10, 6, 8, 0, tzinfo=runtime.TZ))
    runtime.reset()
    yield runtime
    runtime.reset()


@pytest.fixture
def two_feeds(app_env, tmp_path, fixture_db):
    """Two loaded feeds in app_env's cache: S1000542 (active on 2026-10-06) without trip 100677020, S1000538 with it."""
    import shutil
    import sqlite3

    from ttc_mcp.static.manifest import FeedEntry, Manifest

    for name in ("s38.sqlite", "s42.sqlite"):
        shutil.copy(fixture_db, tmp_path / name)
    c = sqlite3.connect(tmp_path / "s42.sqlite")
    c.execute("DELETE FROM trips WHERE trip_id='100677020'")
    c.commit()
    c.close()
    Manifest([FeedEntry("S1000538", "20260906", "20261031", "s38.sqlite", '"e"', "u", "t"),
              FeedEntry("S1000542", "20260930", "20261031", "s42.sqlite", '"e"', "u", "t")]).save(tmp_path)
    app_env.reset()
    return tmp_path
