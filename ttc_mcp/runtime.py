from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from ttc_mcp import overlay
from ttc_mcp.adapters.http import make_async_client
from ttc_mcp.cache import Hit
from ttc_mcp.config import settings
from ttc_mcp.domain import freshness, raptor
from ttc_mcp.realtime import Realtime
from ttc_mcp.static.manifest import Manifest
from ttc_mcp.static.schedule import TZ
from ttc_mcp.static.store import FeedDb, StaticStore, TripLookup

_store: StaticStore | None = None
_store_key: tuple | None = None
_realtime: Realtime | None = None


class StoreUnavailable(RuntimeError):
    pass


def now() -> dt.datetime:
    """Toronto wall clock, unless TTC_MCP_NOW (ISO 8601, naive means Toronto) pins it for smoke tests or to reproduce a day.
    Read on every call so a test can set and unset it."""
    pinned = os.environ.get("TTC_MCP_NOW")
    if not pinned:
        return dt.datetime.now(TZ)
    t = dt.datetime.fromisoformat(pinned)
    return t.replace(tzinfo=TZ) if t.tzinfo is None else t.astimezone(TZ)


def reset() -> None:
    global _store, _store_key, _realtime
    if _store:
        _store.close()
    _store, _store_key, _realtime = None, None, None
    raptor.reset()
    overlay.reset()


def get_store(cache_dir: Path | None = None) -> StaticStore:
    """Open the store from the manifest; reopen only when its feeds change. A check that saves nothing but checked_at
    must not close connections a tool call in flight still holds."""
    global _store, _store_key
    cache_dir = Path(cache_dir or settings().cache_dir)
    m = Manifest.load(cache_dir)
    key = (cache_dir, tuple((f.feed_version, f.path, f.etag, f.surface_etag, f.built_at) for f in m.feeds))
    if _store is None or _store_key != key:
        if _store:
            _store.close()
        # The overlay memo holds timetables per feed version, so a retired feed would outlive the store through it.
        raptor.reset(), overlay.reset()
        _store, _store_key = StaticStore.from_manifest(cache_dir), key
    _store.checked_at, _store.ckan_last_modified = m.checked_at, m.ckan_last_modified
    return _store


def feed_for(day: dt.date | None = None) -> FeedDb:
    feed = get_store().for_date(day or now().date())
    if feed is None:
        raise StoreUnavailable("static store not built yet, no feed covers today, or a store with an old schema is skipped; run ttc-mcp-build")
    return feed


def trip_lookup(today: dt.date | None = None) -> TripLookup:
    """The domain's trip resolver: (connection, row) from whichever loaded feed holds the trip, today's first (spec 2.1)."""
    store, day = get_store(), today or now().date()

    def look(trip_id: str):
        hit = store.lookup_trip(trip_id, day)
        return (hit[0].conn, hit[1]) if hit else None
    return look


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
