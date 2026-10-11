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


@pytest.mark.anyio
async def test_a_failure_holds_the_entry_serving_the_stale_body_until_the_hold_ends():
    clock = Clock()
    cache = Cache(clock)
    fetch = fetcher([b"good", RuntimeError("boom"), b"again"])
    await cache.get(SRC, "k", fetch, parse)
    clock.t += 31
    failed_at = clock()
    first = await cache.get(SRC, "k", fetch, parse)
    assert (first.cache, first.error, len(fetch.calls)) == ("stale_fallback", "boom", 2)
    e = cache.entries["k"]
    assert (e.failures, e.retry_after) == (1, failed_at + 60)   # min(ttl * 2, max_stale / 4)
    for _ in range(3):
        clock.t += 1
        held = await cache.get(SRC, "k", fetch, parse)
        assert (held.cache, held.parsed, held.error) == ("stale_fallback", "good", f"boom; retry in {round(e.retry_after - clock())} s")
    assert len(fetch.calls) == 2
    clock.t = e.retry_after
    h = await cache.get(SRC, "k", fetch, parse)
    assert (h.cache, h.parsed, len(fetch.calls), e.failures, e.retry_after) == ("live", "again", 3, 0, None)


@pytest.mark.anyio
async def test_calls_inside_the_hold_make_no_request_and_the_hold_grows_to_a_quarter_of_max_stale():
    clock = Clock()
    cache, fetch = Cache(clock), fetcher([RuntimeError("down")])
    with pytest.raises(SourceUnavailable, match="down"):
        await cache.get(SRC, "k", fetch, parse)
    for _ in range(3):
        clock.t += 5
        with pytest.raises(SourceUnavailable, match="down; retry in [45][0-9] s"):
            await cache.get(SRC, "k", fetch, parse)
    assert len(fetch.calls) == 1
    e = cache.entries["k"]
    for expected in (120, 150, 150):   # doubling, then the cap of max_stale / 4
        clock.t = e.retry_after
        with pytest.raises(SourceUnavailable):
            await cache.get(SRC, "k", fetch, parse)
        assert e.retry_after == clock() + expected
    assert (e.failures, len(fetch.calls)) == (4, 4)


@pytest.mark.anyio
async def test_force_bypasses_the_hold_and_403s_are_counted():
    from ttc_mcp.adapters.http import FetchError

    clock = Clock()
    cache, fetch = Cache(clock), fetcher([FetchError("HTTP 403", 403), FetchError("HTTP 429", 429), RuntimeError("x"), b"ok"])
    for _ in range(3):
        with pytest.raises(SourceUnavailable):
            await cache.get(SRC, "k", fetch, parse, force=True)
    e = cache.entries["k"]
    assert (e.failures, e.status_counts, len(fetch.calls)) == (3, {"403": 1, "429": 1}, 3)
    row = cache.status()[0]
    assert (row["failures"], row["status_counts"], row["retry_after"]) == (3, {"403": 1, "429": 1}, e.retry_after)
    h = await cache.get(SRC, "k", fetch, parse, force=True)
    assert (h.cache, h.parsed, e.failures, e.retry_after, cache.status()[0]["retry_after"]) == ("live", "ok", 0, None, None)
    assert e.status_counts == {"403": 1, "429": 1}
