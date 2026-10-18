from __future__ import annotations

import asyncio
import datetime as dt
import html
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass

import httpx

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


ROUTE_DETAIL = "https://www.ttc.ca/ttcapi/routedetail/get"


def _level(doc: dict) -> dict | None:
    route = doc.get("route") if isinstance(doc, dict) else None
    if not isinstance(route, dict):
        return None
    level = route.get("serviceLevel")
    level = level if isinstance(level, dict) else {}
    return {"name": level.get("name") or None, "description": level.get("description") or None, "frequency": route.get("frequency") or None,
            "ten_minute": bool(route.get("is10MinutesNetwork")), "map_url": route.get("mapUrl") or None, "route_type": route.get("routeType")}


async def fetch_route_levels(client: httpx.AsyncClient, short_names: list[str], *, concurrency: int = 8, deadline: float = 60,
                             log=None) -> dict[str, dict]:
    """One ttc.ca route-detail request per route; failures are logged and skipped, and one overall deadline returns what
    arrived in time, so a build never depends on ttc.ca."""
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, dict] = {}
    failed: Counter[int] = Counter()
    say = log or (lambda m: print(m, file=sys.stderr))

    async def one(short: str) -> None:
        async with sem:
            try:
                r = await client.get(ROUTE_DETAIL, params={"id": short})
                if r.status_code != 200:
                    failed[r.status_code] += 1
                level = _level(r.json()) if r.status_code == 200 else None
            except Exception as e:
                level = None
                say(f"[ttcapi] {short}: {type(e).__name__}: {e}")
            if level and level["name"]:
                out[short] = level

    try:
        async with asyncio.timeout(deadline):
            await asyncio.gather(*(one(s) for s in short_names))
    except TimeoutError:
        say(f"[ttcapi] deadline of {deadline:g} s reached; {len(out)} of {len(short_names)} routes fetched")
    if failed:
        by = ", ".join(f"HTTP {code} x{n}" for code, n in sorted(failed.items(), key=lambda kv: (-kv[1], kv[0])))
        say(f"[ttcapi] {failed.total()} of {len(short_names)} route details failed: {by}")
    return out
