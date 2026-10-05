from __future__ import annotations

from dataclasses import dataclass

from google.transit import gtfs_realtime_pb2 as pb

from ttc_mcp.cache import Parsed


@dataclass(frozen=True)
class RtAlert:
    id: str
    effect: str
    cause: str
    routes: tuple[str, ...]
    stops: tuple[str, ...]
    start: int | None
    end: int | None
    header: str
    description: str
    url: str | None


@dataclass(frozen=True)
class RtAlertFeed:
    timestamp: int
    alerts: list[RtAlert]


def _text(ts) -> str:
    return ts.translation[0].text if ts.translation else ""


def parse_rt_alerts(body: bytes, headers: dict[str, str]) -> Parsed:
    """gtfsrt.ttc.ca/alerts/all?format=binary: closures per night with platform ids, MODIFIED_SERVICE route changes, accessibility."""
    feed = pb.FeedMessage()
    feed.ParseFromString(body)
    out = []
    for ent in feed.entity:
        if not ent.HasField("alert"):
            continue
        a = ent.alert
        p = a.active_period[0] if a.active_period else None
        routes = tuple(dict.fromkeys(ie.route_id for ie in a.informed_entity if ie.route_id))
        stops = tuple(dict.fromkeys(ie.stop_id for ie in a.informed_entity if ie.stop_id))
        out.append(RtAlert(ent.id, pb.Alert.Effect.Name(a.effect), pb.Alert.Cause.Name(a.cause), routes, stops,
                           (p.start or None) if p else None, (p.end or None) if p else None,
                           _text(a.header_text), _text(a.description_text), _text(a.url) or None))
    return Parsed(RtAlertFeed(feed.header.timestamp, out), feed.header.timestamp or None, len(out))
