from __future__ import annotations

import json
from dataclasses import dataclass

from ttc_mcp.cache import Parsed
from ttc_mcp.sources import NTAS


@dataclass(frozen=True)
class NextTrains:
    platform: str
    line: str
    direction: str
    direction_text: str
    next_min: list[int]


def url_for(platform: str) -> str:
    return NTAS.url.format(platform=platform)


def _minutes(text: str) -> list[int]:
    return [int(x) for x in text.replace(" ", "").split(",") if x.lstrip("-").isdigit()]


def parse_next_trains(body: bytes, headers: dict[str, str]) -> Parsed:
    """NTAS answers [] for non-rail ids and an empty nextTrains string when it has no prediction (gotcha 13)."""
    rows = json.loads(body or b"[]")
    if not isinstance(rows, list):
        raise ValueError("NTAS: expected a JSON list")
    out = [NextTrains(str(r.get("stopCode", "")), str(r.get("line", "")), str(r.get("direction", "")),
                      r.get("directionText") or "", _minutes(str(r.get("nextTrains") or ""))) for r in rows]
    return Parsed(out, None, len(out))
