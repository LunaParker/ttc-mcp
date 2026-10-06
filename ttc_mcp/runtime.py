from __future__ import annotations

import datetime as dt
from pathlib import Path

from ttc_mcp.adapters.http import make_async_client
from ttc_mcp.cache import Hit
from ttc_mcp.config import settings
from ttc_mcp.domain import freshness
from ttc_mcp.realtime import Realtime
from ttc_mcp.static.manifest import NAME as MANIFEST
from ttc_mcp.static.schedule import TZ
from ttc_mcp.static.store import FeedDb, StaticStore

_store: StaticStore | None = None
_store_key: tuple[Path, float] | None = None
_realtime: Realtime | None = None


class StoreUnavailable(RuntimeError):
    pass


def now() -> dt.datetime:
    return dt.datetime.now(TZ)


def reset() -> None:
    global _store, _store_key, _realtime
    if _store:
        _store.close()
    _store, _store_key, _realtime = None, None, None


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
        raise StoreUnavailable("static store not built yet, no feed covers today, or a store with an old schema is skipped; run ttc-mcp-build")
    return feed


def meta(feed: FeedDb | None, *, source: str = "static") -> dict:
    status = get_store().status(now().date())
    return {"source": source, "fetched_at": now().isoformat(timespec="seconds"), "cache": "static",
            "static_feed": feed.version if feed else None, "staleness": status["verdict"]}


def get_realtime() -> Realtime:
    global _realtime
    if _realtime is None:
        _realtime = Realtime(make_async_client(settings().user_agent))
    return _realtime


def set_realtime(rt: Realtime | None) -> None:
    global _realtime
    _realtime = rt


def meta_rt(feed: FeedDb | None, hits: list[Hit | None], *, source: str) -> dict:
    """Spec 4.4 meta for real-time answers: the worst cache state and verdict across the sources used."""
    used = [h for h in hits if h is not None]
    t = get_realtime().clock()
    return {"source": source,
            "feed_timestamp": next((h.feed_timestamp for h in used if h.feed_timestamp), None),
            "fetched_at": freshness.iso(min(h.fetched_at for h in used)) if used else now().isoformat(timespec="seconds"),
            "cache": freshness.worst_cache(h.cache for h in used),
            "static_feed": feed.version if feed else None,
            "staleness": freshness.worst(h.verdict(t) for h in used) if used else "unavailable"}
