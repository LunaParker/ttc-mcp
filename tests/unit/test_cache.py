import asyncio
from email.utils import parsedate_to_datetime

import pytest

from ttc_mcp.adapters.http import Fetched, Validators
from ttc_mcp.cache import Cache, Parsed, SourceUnavailable
from ttc_mcp.sources import Source

SRC = Source("test", "t", "https://example.test/{x}", ttl=30, max_stale=600, stale_after=90)
DATE = "Mon, 05 Oct 2026 16:34:08 GMT"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def parse(body, headers):
    return Parsed(body.decode(), None, 1)


def fetcher(responses):
    """Each response is bytes, a (status, body, headers) tuple, or an exception; the last one repeats."""
    calls = []

    async def fetch(v: Validators) -> Fetched:
        calls.append(v)
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        status, body, headers = r if isinstance(r, tuple) else (200, r, {})
        return Fetched(status, body, headers)

    fetch.calls = calls
    return fetch


@pytest.mark.anyio
async def test_ttl_hit_then_refetch_after_ttl():
    clock = Clock()
    cache, fetch = Cache(clock), fetcher([b"a", b"b"])
    first = await cache.get(SRC, "t:1", fetch, parse)
    second = await cache.get(SRC, "t:1", fetch, parse)
    assert (first.cache, second.cache, second.parsed, len(fetch.calls)) == ("live", "ttl", "a", 1)
    clock.t += 31
    third = await cache.get(SRC, "t:1", fetch, parse)
    assert (third.cache, third.parsed, len(fetch.calls)) == ("live", "b", 2)


@pytest.mark.anyio
async def test_single_flight_shares_one_fetch():
    cache, calls = Cache(Clock()), []

    async def slow(v):
        calls.append(v)
        await asyncio.sleep(0.05)
        return Fetched(200, b"x", {})

    a, b = await asyncio.gather(cache.get(SRC, "k", slow, parse), cache.get(SRC, "k", slow, parse))
    assert a.parsed == b.parsed == "x" and a.cache == b.cache == "live" and len(calls) == 1


@pytest.mark.anyio
async def test_stale_fallback_within_max_stale_then_unavailable():
    clock = Clock()
    cache = Cache(clock)
    fetch = fetcher([b"good", RuntimeError("boom")])
    await cache.get(SRC, "k", fetch, parse)
    clock.t += 100
    h = await cache.get(SRC, "k", fetch, parse)
    assert (h.cache, h.parsed, h.error, h.age_s, h.verdict(clock())) == ("stale_fallback", "good", "boom", 100, "stale")
    clock.t += 600
    with pytest.raises(SourceUnavailable, match="boom"):
        await cache.get(SRC, "k", fetch, parse)


@pytest.mark.anyio
async def test_first_failure_is_unavailable_and_parse_errors_count_as_failures():
    with pytest.raises(SourceUnavailable, match="boom"):
        await Cache(Clock()).get(SRC, "k", fetcher([RuntimeError("boom")]), parse)

    def bad_parse(body, headers):
        raise ValueError("garbage")

    with pytest.raises(SourceUnavailable, match="garbage"):
        await Cache(Clock()).get(SRC, "k", fetcher([b"x"]), bad_parse)


@pytest.mark.anyio
async def test_conditional_get_sends_validators_and_keeps_body_on_304():
    clock = Clock()
    cache = Cache(clock)
    fetch = fetcher([(200, b"v1", {"etag": '"e1"', "last-modified": DATE}), (304, b"", {})])
    await cache.get(SRC, "k", fetch, parse)
    clock.t += 31
    h = await cache.get(SRC, "k", fetch, parse)
    assert fetch.calls[0] == Validators() and fetch.calls[1] == Validators('"e1"', DATE)
    assert (h.cache, h.parsed, h.fetched_at) == ("live", "v1", clock())


@pytest.mark.anyio
async def test_signal_prefers_feed_timestamp_then_last_modified_then_date_then_poll_time():
    clock = Clock(1_000_000.0)
    cache = Cache(clock)
    ts = await cache.get(SRC, "a", fetcher([b"x"]), lambda b, h: Parsed(b, 999_000, 1))
    lm = await cache.get(SRC, "b", fetcher([(200, b"x", {"last-modified": DATE, "date": "Mon, 05 Oct 2026 16:35:00 GMT"})]), parse)
    date = await cache.get(SRC, "c", fetcher([(200, b"x", {"date": DATE})]), parse)
    poll = await cache.get(SRC, "d", fetcher([b"x"]), parse)
    epoch = parsedate_to_datetime(DATE).timestamp()
    assert (ts.signal, ts.signal_ts, ts.feed_timestamp) == ("feed_header", 999_000.0, 999_000)
    assert (lm.signal, lm.signal_ts) == ("http_last_modified", epoch)
    assert (date.signal, date.signal_ts) == ("http_date", epoch)
    assert (poll.signal, poll.signal_ts) == ("poll_time", 1_000_000.0)


@pytest.mark.anyio
async def test_verdict_is_stale_past_the_threshold():
    clock = Clock()
    cache = Cache(clock)
    h = await cache.get(SRC, "k", fetcher([b"x"]), parse)
    assert h.verdict(clock() + 89) == "fresh" and h.verdict(clock() + 91) == "stale"


@pytest.mark.anyio
async def test_force_bypasses_ttl():
    cache, fetch = Cache(Clock()), fetcher([b"a", b"b"])
    await cache.get(SRC, "k", fetch, parse)
    h = await cache.get(SRC, "k", fetch, parse, force=True)
    assert (h.parsed, h.cache, len(fetch.calls)) == ("b", "live", 2)


@pytest.mark.anyio
async def test_status_reports_entries_including_failed_ones():
    clock = Clock()
    cache = Cache(clock)
    await cache.get(SRC, "ok", fetcher([b"x"]), parse)
    with pytest.raises(SourceUnavailable):
        await cache.get(SRC, "bad", fetcher([RuntimeError("down")]), parse)
    clock.t += 10
    rows = {r["key"]: r for r in cache.status()}
    ok, bad = rows["ok"], rows["bad"]
    assert (ok["source"], ok["age_s"], ok["cache"], ok["verdict"], ok["entities"], ok["hits"]) == ("test", 10, "ttl", "fresh", 1, 0)
    assert (ok["signal"], ok["lag_s"], ok["last_error"]) == ("poll_time", 0, None)
    assert (bad["last_fetch"], bad["cache"], bad["verdict"], bad["last_error"]) == (None, None, "unavailable", "down")
    clock.t += 30
    assert cache.status()[0]["cache"] == "expired"
