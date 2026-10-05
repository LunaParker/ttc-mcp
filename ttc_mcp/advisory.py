"""One advisory snapshot per answer: alerts + GTFS-RT alerts + detour feed, normalised once per feed state."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ttc_mcp import runtime
from ttc_mcp.adapters.detour_feed import DetourFeed
from ttc_mcp.adapters.gtfsrt_alerts import RtAlertFeed
from ttc_mcp.adapters.ttc_alerts import AlertList
from ttc_mcp.cache import Hit
from ttc_mcp.domain import detours, freshness
from ttc_mcp.domain.disruptions import Disruptions
from ttc_mcp.realtime import attempt
from ttc_mcp.static.store import FeedDb

SOURCE = "alerts.ttc.ca + gtfsrt alerts + detour feed"
_memo: dict = {}


@dataclass
class Snapshot:
    disruptions: Disruptions
    detours: list[dict]
    trip_detour: dict[str, str]
    alerts: AlertList | None
    rt: RtAlertFeed | None
    detour_feed: DetourFeed | None
    hits: list[Hit]
    errors: dict[str, str]

    def as_of(self) -> str | None:
        return self.alerts.last_updated.isoformat(timespec="seconds") if self.alerts and self.alerts.last_updated else None

    def meta(self) -> dict:
        t = runtime.get_realtime().clock()
        return {"source": SOURCE, "cache": freshness.worst_cache(h.cache for h in self.hits),
                "staleness": freshness.worst(h.verdict(t) for h in self.hits) if self.hits else "unavailable",
                "fetched_at": freshness.iso(min(h.fetched_at for h in self.hits)) if self.hits else None,
                "errors": self.errors or None}


def reset() -> None:
    _memo.clear()


async def snapshot(feed: FeedDb) -> Snapshot:
    rt_, now = runtime.get_realtime(), runtime.now()
    (ah, ae), (rh, re_), (dh, de) = await asyncio.gather(attempt(rt_.alerts()), attempt(rt_.rt_alerts()), attempt(rt_.detours()))
    key = (id(rt_), feed.version, now.replace(second=0, microsecond=0),
           ah.fetched_at if ah else None, rh.fetched_at if rh else None, dh.fetched_at if dh else None)
    if _memo.get("key") != key:
        groups = detours.resolve_detours(feed.conn, dh.parsed, now.date(), rt=rh.parsed if rh else None) if dh else []
        disr = Disruptions.build(feed.conn, ah.parsed if ah else None, rh.parsed if rh else None, groups, now)
        _memo.update(key=key, value=(disr, groups, detours.trip_detour_map(dh.parsed) if dh else {}))
    disr, groups, tmap = _memo["value"]
    errors = {k: v for k, v in (("alerts", ae), ("rt_alerts", re_), ("detours", de)) if v}
    return Snapshot(disr, groups, tmap, ah.parsed if ah else None, rh.parsed if rh else None, dh.parsed if dh else None,
                    [h for h in (ah, rh, dh) if h], errors)
