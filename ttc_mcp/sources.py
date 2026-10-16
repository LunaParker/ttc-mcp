from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    """One on-demand source: TTL and stale-if-error window (spec 4.3), staleness threshold (decision f), request timeout."""
    name: str
    key: str
    url: str
    ttl: float
    max_stale: float
    stale_after: float
    timeout: float = 10.0


STOP_XML = Source("bustime getStopPredictions.jsp", "stop_xml",
                  "https://bustime.ttc.ca/bustime/map/getStopPredictions.jsp?stop={stop}&route=all", 30, 600, 90)
VEHICLES = Source("bustime vehicles", "vehicles", "https://bustime.ttc.ca/gtfsrt/vehicles", 30, 600, 90)
NTAS = Source("ntas", "ntas", "https://ntas.ttc.ca/api/ntas/get-next-train-time/{platform}", 10, 300, 60)
ALERTS = Source("alerts.ttc.ca", "alerts", "https://alerts.ttc.ca/api/alerts/list", 25, 3600, 600, timeout=5.0)
RT_ALERTS = Source("gtfsrt alerts", "rt_alerts", "https://gtfsrt.ttc.ca/alerts/all?format=binary", 60, 3600, 600, timeout=5.0)
DETOURS = Source("detour feed", "detours", "https://gtfsrt.ttc.ca/trips/detour", 300, 3600, 1800, timeout=5.0)
SERVICE_CHANGES = Source("ttcapi service changes", "service_changes",
                         "https://www.ttc.ca/ttcapi/routedetail/getallroutesandstopsalerts", 600, 3600, 1800, timeout=5.0)
TRIPS = Source("bustime trips", "trips", "https://bustime.ttc.ca/gtfsrt/trips", 30, 600, 90)
SUBWAY_TRIPS = Source("gtfsrt trips/subway", "subway_trips", "https://gtfsrt.ttc.ca/trips/subway?format=binary", 30, 600, 60)
SOURCES = (STOP_XML, VEHICLES, NTAS, ALERTS, RT_ALERTS, DETOURS, SERVICE_CHANGES, TRIPS, SUBWAY_TRIPS)
