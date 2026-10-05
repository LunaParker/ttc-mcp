from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

from ttc_mcp.adapters.http import Fetched, Validators
from ttc_mcp.sources import Source

CACHE_RANK = {"live": 0, "ttl": 1, "stale_fallback": 2}


class SourceUnavailable(RuntimeError):
    def __init__(self, key: str, error: str):
        super().__init__(f"{key}: {error}")
        self.key, self.error = key, error


@dataclass(frozen=True)
class Parsed:
    value: Any
    feed_timestamp: int | None = None
    entities: int | None = None


@dataclass
class Entry:
    source: Source
    key: str
    parsed: Parsed | None = None
    fetched_at: float | None = None
    signal: str = "poll_time"
    signal_ts: float | None = None
    etag: str | None = None
    last_modified: str | None = None
    last_attempt: float | None = None
    last_error: str | None = None
    hits: int = 0


@dataclass(frozen=True)
class Hit:
    source: Source
    key: str
    parsed: Any
    cache: str
    fetched_at: float
    feed_timestamp: int | None
    signal: str
    signal_ts: float
    age_s: float
    error: str | None = None

    def verdict(self, now: float) -> str:
        """Decision f: judged against the feed's own signal; a fallback answer is never fresh."""
        if self.cache == "stale_fallback" or now - self.signal_ts > self.source.stale_after:
            return "stale"
        return "fresh"


Fetch = Callable[[Validators], Awaitable[Fetched]]
Parse = Callable[[bytes, dict[str, str]], Parsed]


def _http_date(headers: dict[str, str], name: str) -> float | None:
    try:
        return parsedate_to_datetime(headers[name]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


class Cache:
    """TTL cache with single-flight, conditional GET and last-known-good; one entry per key (spec 2.2)."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.entries: dict[str, Entry] = {}
        self._inflight: dict[str, asyncio.Future] = {}

    async def get(self, source: Source, key: str, fetch: Fetch, parse: Parse, *, force: bool = False) -> Hit:
        now = self.clock()
        e = self.entries.setdefault(key, Entry(source, key))
        if e.parsed is not None and not force and now - e.fetched_at < source.ttl:
            e.hits += 1
            return self._hit(e, "ttl", now)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._refresh(e, fetch, parse))
            self._inflight[key] = task
            task.add_done_callback(lambda _t: self._inflight.pop(key, None))
        try:
            await task
        except Exception as err:
            if e.parsed is not None and now - e.fetched_at <= source.max_stale:
                return self._hit(e, "stale_fallback", now, error=str(err))
            raise SourceUnavailable(key, str(err)) from err
        return self._hit(e, "live", now)

    async def _refresh(self, e: Entry, fetch: Fetch, parse: Parse) -> None:
        e.last_attempt = self.clock()
        try:
            got = await fetch(Validators(e.etag, e.last_modified))
            if got.status != 304 or e.parsed is None:
                e.parsed = parse(got.body, got.headers)
                e.etag, e.last_modified = got.headers.get("etag"), got.headers.get("last-modified")
        except Exception as err:
            e.last_error = str(err) or type(err).__name__
            raise
        e.fetched_at, e.last_error = self.clock(), None
        e.signal, e.signal_ts = self._signal(e, got.headers)

    @staticmethod
    def _signal(e: Entry, headers: dict[str, str]) -> tuple[str, float]:
        if e.parsed.feed_timestamp:
            return "feed_header", float(e.parsed.feed_timestamp)
        for name, label in (("last-modified", "http_last_modified"), ("date", "http_date")):
            ts = _http_date(headers, name)
            if ts is not None:
                return label, ts
        return "poll_time", e.fetched_at

    @staticmethod
    def _hit(e: Entry, cache: str, now: float, error: str | None = None) -> Hit:
        return Hit(e.source, e.key, e.parsed.value, cache, e.fetched_at, e.parsed.feed_timestamp, e.signal, e.signal_ts,
                   round(now - e.fetched_at, 1), error)

    def status(self, now: float | None = None) -> list[dict]:
        now = self.clock() if now is None else now
        rows = []
        for e in self.entries.values():
            ok = e.parsed is not None
            age = round(now - e.fetched_at, 1) if ok else None
            if ok:
                verdict = "stale" if now - e.signal_ts > e.source.stale_after else "fresh"
            else:
                verdict = "unavailable"
            rows.append({"source": e.source.name, "key": e.key, "last_fetch": e.fetched_at, "last_attempt": e.last_attempt,
                         "age_s": age, "cache": (("ttl" if age < e.source.ttl else "expired") if ok else None),
                         "feed_timestamp": e.parsed.feed_timestamp if ok else None, "signal": e.signal if ok else None,
                         "signal_ts": e.signal_ts if ok else None, "lag_s": round(e.fetched_at - e.signal_ts, 1) if ok else None,
                         "entities": e.parsed.entities if ok else None, "last_error": e.last_error, "verdict": verdict, "hits": e.hits})
        return rows
