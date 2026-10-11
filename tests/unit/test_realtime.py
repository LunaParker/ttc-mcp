import asyncio
import time

import httpx
import pytest

from tests.rtfeeds import make_vehicle_feed
from ttc_mcp.realtime import Realtime, attempt

XML = b"<stop><id>15461</id><nm>Cherry</nm><pre><pt>2 MIN</pt><fd>East</fd><v>4617</v><scheduled>false</scheduled><rn>504</rn></pre></stop>"
NTAS = {"13863": b'[{"line":"1","direction":"1","nextTrains":"0, 4","directionText":"Northbound to Finch","stopCode":"13863"}]',
        "13864": b'[{"line":"1","direction":"0","nextTrains":"","directionText":"Southbound","stopCode":"13864"}]'}


def make(calls, *, ntas_status=200):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.host == "bustime.ttc.ca" and req.url.path.endswith("getStopPredictions.jsp"):
            return httpx.Response(200, content=XML, headers={"Date": "Mon, 05 Oct 2026 16:34:08 GMT"})
        if req.url.host == "bustime.ttc.ca" and req.url.path == "/gtfsrt/vehicles":
            return httpx.Response(200, content=make_vehicle_feed(int(time.time()) - 20, [{"id": "4617", "route": "504", "lat": 43.6, "lon": -79.4}]))
        if req.url.host == "ntas.ttc.ca":
            return httpx.Response(ntas_status, content=NTAS.get(req.url.path.rsplit("/", 1)[-1], b"[]"))
        return httpx.Response(404)

    return Realtime(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.anyio
async def test_stop_predictions_hit_the_public_number_url_and_are_cached():
    calls = []
    rt = make(calls)
    a = await rt.stop_predictions("15461")
    b = await rt.stop_predictions("15461")
    assert calls == ["https://bustime.ttc.ca/bustime/map/getStopPredictions.jsp?stop=15461&route=all"]
    assert (a.cache, b.cache, a.key, a.parsed.predictions[0].vehicle_id, a.signal) == ("live", "ttl", "stop_xml:15461", "4617", "http_date")
    await rt.aclose()


@pytest.mark.anyio
async def test_status_lists_every_source_unused_then_filled():
    calls = []
    rt = make(calls)
    before = {r["name"]: r for r in rt.status()}
    assert len(before) == 8 and {"bustime getStopPredictions.jsp", "bustime vehicles", "ntas"} <= set(before)
    assert before["ntas"] == {"name": "ntas", "url": "https://ntas.ttc.ca/api/ntas/get-next-train-time/{platform}", "ttl_s": 10,
                              "keys": 0, "last_fetch": None, "cache": None, "verdict": "unused"}
    v = await rt.vehicles()
    row = {r["name"]: r for r in rt.status()}["bustime vehicles"]
    assert (row["keys"], row["cache"], row["verdict"], row["signal"], row["entities"], row["feed_timestamp"]) == (1, "ttl", "fresh", "feed_header", 1, v.feed_timestamp)
    assert row["last_fetch"].startswith("20") and 15 <= row["lag_s"] <= 30 and row["last_error"] is None
    await rt.aclose()


@pytest.mark.anyio
async def test_probe_makes_one_live_request_per_source_and_reports_errors():
    calls = []
    rt = make(calls, ntas_status=403)
    result = await rt.probe()
    assert (result["bustime getStopPredictions.jsp"], result["bustime vehicles"], result["ntas"]) == (None, None, "ntas:13863: HTTP 403")
    assert result["alerts.ttc.ca"] == "alerts: HTTP 404" and len(result) == 8
    assert len(calls) == 8 and any("13863" in c for c in calls) and any("15461" in c for c in calls)
    assert {r["name"]: r["verdict"] for r in rt.status()}["ntas"] == "unavailable"
    await rt.aclose()


@pytest.mark.anyio
async def test_ntas_coverage_counts_platforms_with_predictions_per_line():
    calls = []
    rt = make(calls)
    await rt.next_trains("13863")
    await rt.next_trains("13864")
    assert rt.ntas_coverage() == {"1": {"platforms": 2, "with_prediction": 1}}
    await rt.aclose()


@pytest.mark.anyio
async def test_advisory_fetches_hit_their_urls_and_parse(app_env):
    from tests.mockttc import handler_factory

    calls = []
    rt = Realtime(httpx.AsyncClient(transport=httpx.MockTransport(handler_factory(calls))))
    a, r, d, s, t = await asyncio.gather(rt.alerts(), rt.rt_alerts(), rt.detours(), rt.service_changes(), rt.trips())
    assert len(a.parsed.alerts) == 6 and a.signal == "http_last_modified" and a.cache == "live"
    assert len(r.parsed.alerts) == 7 and r.signal == "feed_header"
    assert len(d.parsed.entities) == 2 and d.signal == "http_date" and len(s.parsed) == 2 and t.parsed.vehicle_trip["4617"] == "100677020"
    assert sorted(c.split("/")[2] for c in calls) == ["alerts.ttc.ca", "bustime.ttc.ca", "gtfsrt.ttc.ca", "gtfsrt.ttc.ca", "www.ttc.ca"]
    second = await rt.alerts()
    assert second.cache == "ttl" and len(calls) == 5
    await rt.aclose()


@pytest.mark.anyio
async def test_status_rows_carry_the_hold_and_the_per_host_status_counts():
    calls = []
    rt = make(calls, ntas_status=403)
    for platform in ("13863", "13864", "13863", "13864", "13863"):
        assert (await attempt(rt.next_trains(platform)))[1] is not None
    row = {r["name"]: r for r in rt.status()}["ntas"]
    assert (row["failures"], row["status_counts"], row["last_error"]) == (1, {"403": 2}, "HTTP 403")
    assert row["retry_after"].startswith("20") and len([c for c in calls if "ntas" in c]) == 2
    await rt.aclose()
