from __future__ import annotations

from dataclasses import dataclass

import httpx

DEFAULT_TIMEOUT = 10.0


class FetchError(RuntimeError):
    pass


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


async def fetch(client: httpx.AsyncClient, url: str, validators: Validators = Validators()) -> Fetched:
    """GET with optional conditional headers; 200 and 304 are answers, everything else is a FetchError."""
    headers = {}
    if validators.etag:
        headers["If-None-Match"] = validators.etag
    if validators.last_modified:
        headers["If-Modified-Since"] = validators.last_modified
    try:
        r = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        raise FetchError(f"{type(e).__name__}: {e}") from e
    if r.status_code not in (200, 304):
        raise FetchError(f"HTTP {r.status_code}")
    return Fetched(r.status_code, r.content, {k.lower(): v for k, v in r.headers.items()})
