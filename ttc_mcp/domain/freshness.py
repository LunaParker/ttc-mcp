from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from ttc_mcp.cache import CACHE_RANK
from ttc_mcp.static.schedule import TZ

VERDICT_RANK = {"fresh": 0, "stale": 1, "unavailable": 2}


def worst(verdicts: Iterable[str]) -> str:
    vs = list(verdicts)
    return max(vs, key=VERDICT_RANK.__getitem__) if vs else "unavailable"


def worst_cache(caches: Iterable[str]) -> str | None:
    cs = list(caches)
    return max(cs, key=CACHE_RANK.__getitem__) if cs else None


def iso(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, TZ).isoformat(timespec="seconds")
