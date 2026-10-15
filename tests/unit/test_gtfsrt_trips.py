from pathlib import Path

from ttc_mcp import sources
from ttc_mcp.adapters.gtfsrt_trips import parse_subway_trips

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "gtfsrt" / "subway-trips.pb"


def test_the_subway_feed_parses_into_trip_updates_keyed_by_public_platform_ids():
    parsed = parse_subway_trips(FIX.read_bytes(), {})
    feed = parsed.value
    assert feed.trips and parsed.entities == len(feed.trips) == 55 and parsed.feed_timestamp == feed.timestamp == 1792032994
    routes = {rec.route for rec in feed.trips.values()}
    assert routes == {"1", "2", "4"} and all(rec.updates for rec in feed.trips.values())
    first = next(iter(feed.trips.values())).updates[0]
    assert first.stop_id.isdigit() and len(first.stop_id) == 5 and first.time and first.relationship == "SCHEDULED"
    # the 2026-10-14 capture sets arrival.time and stop_sequence on every update and no departure, all SCHEDULED
    ups = [u for rec in feed.trips.values() for u in rec.updates]
    assert len(ups) == 376 and all(u.seq and u.time and u.relationship == "SCHEDULED" and len(u.stop_id) == 5 for u in ups)


def test_the_source_sits_after_the_surface_trip_feed_with_decision_fs_thresholds():
    s = sources.SUBWAY_TRIPS
    assert (s.key, s.ttl, s.max_stale, s.stale_after, s.url) == ("subway_trips", 30, 600, 60, "https://gtfsrt.ttc.ca/trips/subway?format=binary")
    assert sources.SOURCES.index(s) == sources.SOURCES.index(sources.TRIPS) + 1
