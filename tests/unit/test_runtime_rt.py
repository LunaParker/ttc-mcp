import httpx
import pytest

from ttc_mcp import runtime
from ttc_mcp.cache import Hit
from ttc_mcp.realtime import Realtime
from ttc_mcp.sources import NTAS, STOP_XML, VEHICLES
from ttc_mcp.static.store import FeedDb


def hit(source, cache, fetched_at, feed_ts=None, signal_ts=None):
    return Hit(source, source.key, None, cache, fetched_at, feed_ts, "feed_header" if feed_ts else "poll_time",
               signal_ts or feed_ts or fetched_at, 0.0)


def test_get_realtime_is_lazy_and_reset_drops_it(monkeypatch):
    monkeypatch.setenv("TTC_MCP_UA", "ttc-mcp/test-ua")
    runtime.reset()
    rt = runtime.get_realtime()
    assert rt is runtime.get_realtime() and rt.client.headers["user-agent"] == "ttc-mcp/test-ua"
    runtime.reset()
    assert runtime.get_realtime() is not rt
    runtime.reset()


@pytest.mark.anyio
async def test_set_realtime_replaces_the_singleton():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as c:
        mine = Realtime(c)
        runtime.set_realtime(mine)
        assert runtime.get_realtime() is mine
    runtime.reset()


def test_meta_rt_merges_hits_worst_first():
    runtime.reset()
    now = runtime.get_realtime().clock()
    feed = FeedDb("S1000538", None, None, None, None)
    hits = [hit(STOP_XML, "ttl", now - 20), hit(VEHICLES, "live", now - 1, feed_ts=int(now) - 25)]
    m = runtime.meta_rt(feed, hits, source="bustime getStopPredictions.jsp + gtfsrt/vehicles")
    assert m["source"].startswith("bustime") and m["cache"] == "ttl" and m["staleness"] == "fresh"
    assert m["feed_timestamp"] == int(now) - 25 and m["static_feed"] == "S1000538"
    assert m["fetched_at"][-6:] in ("-04:00", "-05:00")
    stale = runtime.meta_rt(feed, [hit(NTAS, "stale_fallback", now - 200)], source="ntas")
    assert (stale["cache"], stale["staleness"]) == ("stale_fallback", "stale")
    none = runtime.meta_rt(feed, [None, None], source="ntas + static stop_times")
    assert (none["cache"], none["staleness"], none["feed_timestamp"]) == (None, "unavailable", None)
    runtime.reset()
