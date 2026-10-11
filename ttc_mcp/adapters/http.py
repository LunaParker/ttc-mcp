from __future__ import annotations

from dataclasses import dataclass

import httpx

DEFAULT_TIMEOUT = 10.0


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Validators:
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class Fetched:
    status: int
    body: bytes
    headers: dict[str, str]


def make_async_client(user_agent: str, timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True)


async def fetch(client: httpx.AsyncClient, url: str, validators: Validators | None = None, *, timeout: float | None = None) -> Fetched:
    """GET with optional conditional headers and a per-request timeout; 200 and 304 are answers, everything else is a FetchError."""
    validators = validators if validators is not None else Validators()
    headers = {}
    if validators.etag:
        headers["If-None-Match"] = validators.etag
    if validators.last_modified:
        headers["If-Modified-Since"] = validators.last_modified
    try:
        r = await client.get(url, headers=headers, timeout=httpx.USE_CLIENT_DEFAULT if timeout is None else timeout)
    except httpx.HTTPError as e:
        raise FetchError(f"{type(e).__name__}: {e}") from e
    if r.status_code not in (200, 304):
        raise FetchError(f"HTTP {r.status_code}", r.status_code)
    return Fetched(r.status_code, r.content, {k.lower(): v for k, v in r.headers.items()})
