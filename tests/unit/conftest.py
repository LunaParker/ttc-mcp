import httpx
import pytest

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Unit tests never reach the network: a real transport to any non-loopback host raises, and the test fails by name
    at teardown even when the fetch layer swallowed the raise (cache.py turns every error into SourceUnavailable)."""
    reached: list[str] = []
    real_async, real_sync = httpx.AsyncHTTPTransport.handle_async_request, httpx.HTTPTransport.handle_request

    def refuse(request: httpx.Request) -> RuntimeError:
        reached.append(str(request.url))
        return RuntimeError(f"unit test reached the network: {request.url}")

    async def guarded_async(self, request):
        if request.url.host in LOOPBACK:
            return await real_async(self, request)
        raise refuse(request)

    def guarded_sync(self, request):
        if request.url.host in LOOPBACK:
            return real_sync(self, request)
        raise refuse(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", guarded_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guarded_sync)
    yield
    if reached:
        pytest.fail("unit test reached the network: " + ", ".join(sorted({httpx.URL(u).host for u in reached})))
