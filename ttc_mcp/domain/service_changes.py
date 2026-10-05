from __future__ import annotations

import datetime as dt

from ttc_mcp.adapters.gtfsrt_alerts import RtAlertFeed
from ttc_mcp.adapters.ttcapi import ServiceChange
from ttc_mcp.domain.detours import overlaps
from ttc_mcp.domain.freshness import iso


def _norm(title: str) -> str:
    return " ".join(title.replace("–", "-").split()).lower()


def merged(rt: RtAlertFeed | None, changes: list[ServiceChange] | None, *, route: str | None = None, day: dt.date | None = None) -> list[dict]:
    """GTFS-RT MODIFIED_SERVICE entities are the canonical list today; ttcapi rows are added when their title is new."""
    out, seen, spans = [], set(), []
    for a in (rt.alerts if rt else []):
        if a.effect != "MODIFIED_SERVICE":
            continue
        out.append({"id": a.id, "routes": list(a.routes), "route_type": None, "title": a.header,
                    "effective": [iso(a.start) if a.start else None, iso(a.end) if a.end else None],
                    "text": a.description or None, "url": a.url, "category": None, "source": "gtfsrt alerts"})
        seen.add(_norm(a.header))
        spans.append((a.start, a.end))
    for c in changes or []:
        if _norm(c.title) in seen:
            continue
        out.append({"id": c.id, "routes": list(c.routes), "route_type": c.route_type, "title": c.title,
                    "effective": [c.start.isoformat() if c.start else None, c.end.isoformat() if c.end else None],
                    "text": c.text or None, "url": c.url, "category": c.category, "source": "ttcapi"})
        spans.append((c.start, c.end))
    keep = []
    for rec, (start, end) in zip(out, spans):
        if route and route not in rec["routes"]:
            continue
        if day and not overlaps(start, end, day):
            continue
        keep.append(rec)
    return keep
