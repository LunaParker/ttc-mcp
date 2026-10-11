import httpx
import pytest

from ttc_mcp.adapters import http
from ttc_mcp.adapters.http import FetchError, Validators
from ttc_mcp.sources import ALERTS, DETOURS, NTAS, RT_ALERTS, SERVICE_CHANGES, SOURCES, STOP_XML, TRIPS, VEHICLES


def test_sources_carry_spec_ttls_and_thresholds():
    assert (STOP_XML.ttl, STOP_XML.max_stale, STOP_XML.stale_after) == (30, 600, 90)
    assert (VEHICLES.ttl, VEHICLES.max_stale, VEHICLES.stale_after) == (30, 600, 90)
    assert (NTAS.ttl, NTAS.max_stale, NTAS.stale_after) == (10, 300, 60)
    assert [s.key for s in SOURCES][:3] == ["stop_xml", "vehicles", "ntas"]
    assert STOP_XML.url.format(stop="15461") == "https://bustime.ttc.ca/bustime/map/getStopPredictions.jsp?stop=15461&route=all"
    assert NTAS.url.format(platform="13863") == "https://ntas.ttc.ca/api/ntas/get-next-train-time/13863"
    assert {s.timeout for s in (STOP_XML, VEHICLES, NTAS, TRIPS)} == {10.0} and {s.timeout for s in (ALERTS, RT_ALERTS, DETOURS, SERVICE_CHANGES)} == {5.0}


@pytest.mark.anyio
async def test_fetch_sends_validators_and_lowercases_headers():
    seen = {}

    def handler(req):
        seen.update(req.headers)
        return httpx.Response(200, content=b"ok", headers={"ETag": '"x"', "Date": "Mon, 05 Oct 2026 16:34:08 GMT"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"User-Agent": "ttc-mcp/test"}) as c:
        got = await http.fetch(c, "https://example.test/a", Validators('"e"', "lm"))
    assert (got.status, got.body, got.headers["etag"], got.headers["date"]) == (200, b"ok", '"x"', "Mon, 05 Oct 2026 16:34:08 GMT")
    assert (seen["user-agent"], seen["if-none-match"], seen["if-modified-since"]) == ("ttc-mcp/test", '"e"', "lm")


@pytest.mark.anyio
async def test_fetch_without_validators_sends_no_conditional_headers_and_accepts_304():
    seen = {}

    def handler(req):
        seen.update(req.headers)
        return httpx.Response(304)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        got = await http.fetch(c, "https://example.test/a")
    assert got.status == 304 and got.body == b"" and "if-none-match" not in seen


@pytest.mark.anyio
async def test_fetch_raises_fetch_error_on_403_and_transport_errors():
    def handler(req):
        if req.url.path == "/403":
            return httpx.Response(403)
        raise httpx.ConnectError("refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(FetchError, match="HTTP 403"):
            await http.fetch(c, "https://example.test/403")
        with pytest.raises(FetchError, match="ConnectError"):
            await http.fetch(c, "https://example.test/x")


@pytest.mark.anyio
async def test_make_async_client_sets_user_agent_and_timeout():
    c = http.make_async_client("ttc-mcp/0.1.0")
    try:
        assert c.headers["user-agent"] == "ttc-mcp/0.1.0" and c.timeout.read == 10.0
    finally:
        await c.aclose()


@pytest.mark.anyio
async def test_fetch_applies_a_per_request_timeout_and_keeps_the_clients_default_otherwise():
    seen = {}

    def handler(req):
        seen.update(req.extensions.get("timeout") or {})
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=10.0) as c:
        await http.fetch(c, "https://example.test/a", timeout=5.0)
        assert seen == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}
        await http.fetch(c, "https://example.test/a")
        assert seen["read"] == 10.0


@pytest.mark.anyio
async def test_fetch_errors_carry_the_http_status():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(429))) as c:
        with pytest.raises(FetchError) as info:
            await http.fetch(c, "https://example.test/x")
    assert info.value.status == 429
