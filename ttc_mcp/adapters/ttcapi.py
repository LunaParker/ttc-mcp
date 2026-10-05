from __future__ import annotations

import datetime as dt
import html
import json
import re
from dataclasses import dataclass

from ttc_mcp.adapters.ttc_alerts import local_time
from ttc_mcp.cache import Parsed

LEADING_ROUTE = re.compile(r"^\s*(\d{1,3}[A-Z]?)\b")


@dataclass(frozen=True)
class ServiceChange:
    id: str
    routes: tuple[str, ...]
    route_type: str | None
    title: str
    start: dt.datetime | None
    end: dt.datetime | None
    text: str
    url: str | None
    category: str | None


def _routes(raw: str | None, title: str) -> tuple[str, ...]:
    """Spec 6.5: split on '|', and when the field is blank take the leading route number from the title."""
    parts = [r.strip() for r in re.split(r"[|,]", raw or "") if r.strip()]
    if parts:
        return tuple(parts)
    m = LEADING_ROUTE.match(title)
    return (m.group(1),) if m else ()


def parse_service_changes(body: bytes, headers: dict[str, str]) -> Parsed:
    doc = json.loads(body)
    if not isinstance(doc, dict):
        raise ValueError("ttcapi: unexpected document")
    out = []
    for c in doc.get("serviceChanges") or []:
        title = html.unescape(c.get("title") or "")
        out.append(ServiceChange(str(c.get("id")), _routes(c.get("route"), title), c.get("routeType") or None, title,
                                 local_time(c.get("effectiveStartDate")), local_time(c.get("effectiveEndDate")),
                                 html.unescape(c.get("contentText") or ""), c.get("url") or None, c.get("categoryName") or None))
    return Parsed(out, None, len(out))
