from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import httpx

from ttc_mcp.adapters import bustime_rt, bustime_web, http, ntas
from ttc_mcp.adapters.http import Fetched, Validators
from ttc_mcp.cache import Cache, Hit, SourceUnavailable
from ttc_mcp.domain.freshness import iso
from ttc_mcp.sources import NTAS, SOURCES, STOP_XML, VEHICLES

PROBE_STOP, PROBE_PLATFORM = "15461", "13863"
STATUS_FIELDS = ("age_s", "cache", "feed_timestamp", "signal", "lag_s", "entities", "last_error", "verdict", "hits")


class Realtime:
    """Owns the async client and the cache; every real-time read goes through here (decision g)."""

    def __init__(self, client: httpx.AsyncClient, cache: Cache | None = None, clock: Callable[[], float] = time.time):
        self.client, self.clock = client, clock
        self.cache = cache or Cache(clock)

    def _fetch(self, url: str):
        async def go(v: Validators) -> Fetched:
            return await http.fetch(self.client, url, v)
        return go

    async def stop_predictions(self, stop: str, *, force: bool = False) -> Hit:
        return await self.cache.get(STOP_XML, f"{STOP_XML.key}:{stop}", self._fetch(bustime_web.url_for(stop)),
                                    bustime_web.parse_stop_predictions, force=force)

    async def vehicles(self, *, force: bool = False) -> Hit:
        return await self.cache.get(VEHICLES, VEHICLES.key, self._fetch(VEHICLES.url), bustime_rt.parse_vehicles, force=force)

    async def next_trains(self, platform: str, *, force: bool = False) -> Hit:
        return await self.cache.get(NTAS, f"{NTAS.key}:{platform}", self._fetch(ntas.url_for(platform)),
                                    ntas.parse_next_trains, force=force)

    async def probe(self) -> dict[str, str | None]:
        """One live request per source, bypassing TTLs; errors are reported, never raised."""
        jobs = [self.stop_predictions(PROBE_STOP, force=True), self.vehicles(force=True), self.next_trains(PROBE_PLATFORM, force=True)]
        results = await asyncio.gather(*jobs, return_exceptions=True)
        return {s.name: (str(r) if isinstance(r, Exception) else None) for s, r in zip(SOURCES, results)}

    def status(self) -> list[dict]:
        rows = self.cache.status(self.clock())
        out = []
        for src in SOURCES:
            mine = [r for r in rows if r["source"] == src.name]
            base = {"name": src.name, "url": src.url, "ttl_s": src.ttl, "keys": len(mine)}
            if not mine:
                out.append({**base, "last_fetch": None, "cache": None, "verdict": "unused"})
                continue
            latest = max(mine, key=lambda r: r["last_attempt"] or 0)
            out.append({**base, "last_fetch": iso(latest["last_fetch"]) if latest["last_fetch"] else None,
                        **{k: latest[k] for k in STATUS_FIELDS}})
        return out

    def ntas_coverage(self) -> dict[str, dict[str, int]]:
        cov: dict[str, dict[str, int]] = {}
        for e in self.cache.entries.values():
            if e.source is not NTAS or e.parsed is None:
                continue
            for row in e.parsed.value:
                c = cov.setdefault(row.line, {"platforms": 0, "with_prediction": 0})
                c["platforms"] += 1
                c["with_prediction"] += bool(row.next_min)
        return cov

    async def aclose(self) -> None:
        await self.client.aclose()


__all__ = ["Realtime", "SourceUnavailable", "PROBE_STOP", "PROBE_PLATFORM"]
