"""gtfsrt.ttc.ca/trips/subway: TripUpdates for routes 1, 2 and 4 keyed by public platform ids (spec 6.8, 6.10); the same protobuf as
bustime's trip feed, so the same parser, with no crosswalk needed. Lines 5 and 6 never appear."""
from __future__ import annotations

from ttc_mcp.adapters.bustime_rt import parse_trips
from ttc_mcp.cache import Parsed


def parse_subway_trips(body: bytes, headers: dict[str, str]) -> Parsed:
    return parse_trips(body, headers)
