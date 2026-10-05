from ttc_mcp.domain import freshness


def test_worst_verdict_orders_fresh_stale_unavailable():
    assert freshness.worst(["fresh", "fresh"]) == "fresh"
    assert freshness.worst(["fresh", "stale"]) == "stale"
    assert freshness.worst(["stale", "unavailable"]) == "unavailable"
    assert freshness.worst([]) == "unavailable"


def test_worst_cache_orders_live_ttl_fallback():
    assert freshness.worst_cache(["live", "ttl"]) == "ttl"
    assert freshness.worst_cache(["ttl", "stale_fallback", "live"]) == "stale_fallback"
    assert freshness.worst_cache(["live"]) == "live"
    assert freshness.worst_cache([]) is None


def test_iso_renders_toronto_time():
    assert freshness.iso(1791218048) == "2026-10-05T12:34:08-04:00"
