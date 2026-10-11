import pytest

from tests.mockttc import make_app
from ttc_mcp import advisory, runtime


@pytest.mark.anyio
async def test_snapshot_builds_disruptions_once_per_feed_state(app_env):
    calls = []
    make_app(calls)
    feed = app_env.feed_for()
    s1 = await advisory.snapshot(feed)
    s2 = await advisory.snapshot(feed)
    assert s1.disruptions is s2.disruptions and len(calls) == 3
    assert {e["alert_id"] for e in s1.disruptions.elements if e["source"] == "alerts.ttc.ca"} == {"77519", "77535", "77545", "77530", "77513", "76445"}
    assert [g["detour_id"] for g in s1.detours] == ["9002", "9001"] and s1.trip_detour["100677020"] == "9001"
    assert s1.alerts.last_updated and len(s1.rt.alerts) == 7 and len(s1.detour_feed.entities) == 2 and s1.as_of() == "2026-10-05T13:42:32-04:00"
    m = s1.meta()
    assert (m["cache"], m["staleness"], m["errors"]) == ("live", "fresh", None) and m["source"] == "alerts.ttc.ca + gtfsrt alerts + detour feed"
    assert s2.meta()["cache"] == "ttl" and m["fetched_at"].startswith("20")


@pytest.mark.anyio
async def test_snapshot_degrades_when_sources_fail(app_env):
    make_app([], fail=("alerts.ttc.ca",))
    s = await advisory.snapshot(app_env.feed_for())
    assert s.alerts is None and s.errors == {"alerts": "HTTP 503"} and [g["detour_id"] for g in s.detours] == ["9002", "9001"]
    assert s.disruptions.elements and all(e["source"] == "detour feed" for e in s.disruptions.elements) and s.as_of() is None
    assert s.meta()["errors"] == {"alerts": "HTTP 503"} and s.meta()["cache"] == "live"
    make_app([], fail=("alerts.ttc.ca", "gtfsrt.ttc.ca"))
    s = await advisory.snapshot(app_env.feed_for())
    assert s.hits == [] and s.meta()["staleness"] == "unavailable" and s.meta()["fetched_at"] is None and s.disruptions.elements == []


@pytest.mark.anyio
async def test_snapshot_resolves_detours_across_the_loaded_feeds_and_rebuilds_when_they_change(two_feeds):
    from ttc_mcp.static.manifest import FeedEntry, Manifest

    make_app([])
    feed = runtime.feed_for()
    s1 = await advisory.snapshot(feed)
    g = next(g for g in s1.detours if g["detour_id"] == "9001")
    assert feed.version == "S1000542" and g["skipped_stops"] == ["14993", "14994", "15039"] and g["directions"][0]["pattern"] == 6
    assert s1.disruptions.stop_skipped("14994")["detour_id"] == "9001"
    # Unloading the other feed changes the loaded set: the snapshot is rebuilt and the trip no longer resolves.
    Manifest([FeedEntry("S1000542", "20260930", "20261031", "s42.sqlite", '"e"', "u", "t")]).save(two_feeds)
    s2 = await advisory.snapshot(runtime.feed_for())
    assert s2.disruptions is not s1.disruptions and next(g for g in s2.detours if g["detour_id"] == "9001")["directions"][0]["pattern"] == "unknown"
