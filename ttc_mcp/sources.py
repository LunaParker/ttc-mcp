from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    """One on-demand source: TTL and stale-if-error window (spec 4.3), staleness threshold (decision f)."""
    name: str
    key: str
    url: str
    ttl: float
    max_stale: float
    stale_after: float


STOP_XML = Source("bustime getStopPredictions.jsp", "stop_xml",
                  "https://bustime.ttc.ca/bustime/map/getStopPredictions.jsp?stop={stop}&route=all", 30, 600, 90)
VEHICLES = Source("bustime vehicles", "vehicles", "https://bustime.ttc.ca/gtfsrt/vehicles", 30, 600, 90)
NTAS = Source("ntas", "ntas", "https://ntas.ttc.ca/api/ntas/get-next-train-time/{platform}", 10, 300, 60)
SOURCES = (STOP_XML, VEHICLES, NTAS)
