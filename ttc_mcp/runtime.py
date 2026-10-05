from __future__ import annotations

import datetime as dt
from pathlib import Path

from ttc_mcp.config import settings
from ttc_mcp.static.manifest import NAME as MANIFEST
from ttc_mcp.static.schedule import TZ
from ttc_mcp.static.store import FeedDb, StaticStore

_store: StaticStore | None = None
_store_key: tuple[Path, float] | None = None


class StoreUnavailable(RuntimeError):
    pass


def now() -> dt.datetime:
    return dt.datetime.now(TZ)


def reset() -> None:
    global _store, _store_key
    if _store:
        _store.close()
    _store, _store_key = None, None


def get_store(cache_dir: Path | None = None) -> StaticStore:
    """Open the store from the manifest; reopen when the manifest changes on disk."""
    global _store, _store_key
    cache_dir = Path(cache_dir or settings().cache_dir)
    mpath = cache_dir / MANIFEST
    key = (cache_dir, mpath.stat().st_mtime if mpath.exists() else 0.0)
    if _store is None or _store_key != key:
        if _store:
            _store.close()
        _store, _store_key = StaticStore.from_manifest(cache_dir), key
    return _store


def feed_for(day: dt.date | None = None) -> FeedDb:
    feed = get_store().for_date(day or now().date())
    if feed is None:
        raise StoreUnavailable("static store not built yet or no feed covers today; run ttc-mcp-build")
    return feed


def meta(feed: FeedDb | None, *, source: str = "static") -> dict:
    status = get_store().status(now().date())
    return {"source": source, "fetched_at": now().isoformat(timespec="seconds"), "cache": "static",
            "static_feed": feed.version if feed else None, "staleness": status["verdict"]}
