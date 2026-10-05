"""One MockTransport handler serving every TTC endpoint from fixtures, for tool-level tests."""
import time
from email.utils import formatdate
from pathlib import Path

import httpx

from tests.rtfeeds import make_alert_feed, make_trip_feed, make_vehicle_feed

FIX = Path(__file__).resolve().parent / "fixtures"
NTAS = {"13863": b'[{"line":"1","direction":"1","nextTrains":"0, 4, 5","directionText":"Northbound to Finch","stopCode":"13863"}]',
        "13864": b'[{"line":"1","direction":"0","nextTrains":"","directionText":"Southbound to Vaughan Metropolitan Centre","stopCode":"13864"}]'}
LINE1_PLATFORMS = [("1", p) for p in ("16073", "13796", "13794", "13793", "13791", "13792")]
NIGHTS = {"77537": (1791255600, 1791268200), "77538": (1791342000, 1791354600), "77539": (1791428400, 1791441000)}
CLOSURE_TEXT = "Line 1 Yonge-University: There is no subway service between York Mills and Eglinton stations due to planned maintenance work. Shuttle buses are running."


def rt_alerts_body(ts: int) -> bytes:
    nights = [{"id": i, "effect": "NO_SERVICE", "cause": "OTHER_CAUSE", "route_stops": LINE1_PLATFORMS, "start": s, "end": e,
               "header": CLOSURE_TEXT, "url": "https://files.ttc.ca/x.png"} for i, (s, e) in NIGHTS.items()]
    return make_alert_feed(ts, nights + [
        {"id": "77519", "effect": "REDUCED_SERVICE", "cause": "OTHER_CAUSE", "route_stops": LINE1_PLATFORMS, "start": 1791206542, "end": 1791428400,
         "header": "Line 1 Yonge-University: There will be no subway service between York Mills and Eglinton stations, starting 11 p.m., nightly Monday, October 5 to Wednesday, October 7, due to planned maintenance work. Shuttle buses will operate."},
        {"id": "100", "effect": "MODIFIED_SERVICE", "routes": ["504"], "start": 1790000000, "end": 1793000000,
         "header": "504 King – Temporary route change due to track work", "description": "504 King streetcars will divert via Queen St.",
         "url": "https://www.ttc.ca/service-advisories/Service-Changes/504"},
        {"id": "77544", "effect": "ACCESSIBILITY_ISSUE", "stops": ["13852", "13853"], "start": 1791222141, "header": "Spadina: Elevator 2B2L out of service"},
        {"id": "00005", "effect": "NO_EFFECT", "routes": ["5"], "start": 1791194400, "end": 1793505599, "header": "Tell us about your experience riding on Line 5.", "url": "https://survey"}])


def vehicles_body(ts: int) -> bytes:
    return make_vehicle_feed(ts, [
        {"id": "4617", "route": "504", "trip": "100677020", "lat": 43.65, "lon": -79.36, "bearing": 241, "speed": 0.0,
         "stop_id": "4282", "seq": 12, "status": "INCOMING_AT", "occupancy": "FEW_SEATS_AVAILABLE"},
        {"id": "8520", "route": "121", "trip": "x", "lat": 43.6, "lon": -79.3, "occupancy": "EMPTY"}])


def trips_body(ts: int) -> bytes:
    return make_trip_feed(ts, [{"trip": "100677020", "route": "504", "vehicle": "4617", "stops": [("4282", 12, ts + 120)]},
                              {"trip": "x", "route": "121", "vehicle": "8520"}])


def handler_factory(calls: list, *, fail=()):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        host, path, now = req.url.host, req.url.path, int(time.time())
        http_now = formatdate(usegmt=True)
        if host in fail:
            return httpx.Response(503)
        if host == "bustime.ttc.ca":
            if path.endswith("getStopPredictions.jsp"):
                name = "stop-15461.xml" if req.url.params["stop"] == "15461" else "stop-notfound.xml"
                return httpx.Response(200, content=(FIX / "bustime" / name).read_bytes(), headers={"Date": http_now})
            if path == "/gtfsrt/vehicles":
                return httpx.Response(200, content=vehicles_body(now - 20))
            if path == "/gtfsrt/trips":
                return httpx.Response(200, content=trips_body(now - 20))
        if host == "ntas.ttc.ca":
            return httpx.Response(200, content=NTAS.get(path.rsplit("/", 1)[-1], b"[]"))
        if host == "alerts.ttc.ca":
            return httpx.Response(200, content=(FIX / "advisories" / "alerts.json").read_bytes(),
                                  headers={"ETag": 'W/"fixture"', "Last-Modified": http_now, "Date": http_now})
        if host == "gtfsrt.ttc.ca" and path == "/alerts/all":
            return httpx.Response(200, content=rt_alerts_body(now - 10))
        if host == "gtfsrt.ttc.ca" and path == "/trips/detour":
            return httpx.Response(200, content=(FIX / "advisories" / "detour.json").read_bytes(), headers={"Date": http_now})
        if host == "www.ttc.ca":
            return httpx.Response(200, content=(FIX / "advisories" / "servicechanges.json").read_bytes())
        return httpx.Response(404)
    return handler


def make_app(calls: list, **kw):
    from ttc_mcp import runtime
    from ttc_mcp.realtime import Realtime
    from ttc_mcp.server import create_app

    runtime.set_realtime(Realtime(httpx.AsyncClient(transport=httpx.MockTransport(handler_factory(calls, **kw)))))
    return create_app()
