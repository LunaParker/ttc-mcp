from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from ttc_mcp.cache import Parsed
from ttc_mcp.sources import STOP_XML

ETA = re.compile(r"(\d+)\s*MIN", re.I)


@dataclass(frozen=True)
class Prediction:
    route: str
    direction_text: str
    eta_text: str
    eta_min: int | None
    vehicle_id: str | None
    scheduled_only: bool


@dataclass(frozen=True)
class StopPredictions:
    stop: str
    name: str
    message: str | None
    predictions: list[Prediction]


def url_for(stop: str) -> str:
    return STOP_XML.url.format(stop=stop)


def eta_minutes(text: str) -> int | None:
    t = text.strip().upper()
    if t == "DUE":
        return 0
    m = ETA.search(t)
    return int(m.group(1)) if m else None


def _text(el: ET.Element, tag: str) -> str:
    return " ".join((el.findtext(tag) or "").split())


def parse_stop_predictions(body: bytes, headers: dict[str, str]) -> Parsed:
    """The TrackTTC map XML: <stop><pre><pt>9 MIN</pt><fd>..</fd><v>..</v><scheduled>..</scheduled><rn>..</rn></pre>...</stop>."""
    root = ET.fromstring(body)
    preds = [Prediction(_text(p, "rn"), _text(p, "fd"), _text(p, "pt"), eta_minutes(_text(p, "pt")),
                        _text(p, "v") or None, _text(p, "scheduled").lower() == "true") for p in root.iter("pre")]
    message = _text(root, "noPredictionMessage") or None
    if root.tag != "stop":
        message = " ".join("".join(root.itertext()).split()) or f"unexpected <{root.tag}> response"
    return Parsed(StopPredictions(_text(root, "id"), _text(root, "nm"), message, preds), None, len(preds))
