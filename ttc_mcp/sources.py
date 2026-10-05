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
ALERTS = Source("alerts.ttc.ca", "alerts", "https://alerts.ttc.ca/api/alerts/list", 25, 3600, 600)
RT_ALERTS = Source("gtfsrt alerts", "rt_alerts", "https://gtfsrt.ttc.ca/alerts/all?format=binary", 60, 3600, 600)
DETOURS = Source("detour feed", "detours", "https://gtfsrt.ttc.ca/trips/detour", 300, 3600, 1800)
SERVICE_CHANGES = Source("ttcapi service changes", "service_changes",
                         "https://www.ttc.ca/ttcapi/routedetail/getallroutesandstopsalerts", 600, 3600, 1800)
TRIPS = Source("bustime trips", "trips", "https://bustime.ttc.ca/gtfsrt/trips", 30, 600, 90)
SOURCES = (STOP_XML, VEHICLES, NTAS, ALERTS, RT_ALERTS, DETOURS, SERVICE_CHANGES, TRIPS)
