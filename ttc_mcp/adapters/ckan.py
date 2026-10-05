from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

CKAN_BASE = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action"
PKG_MERGED = "merged-gtfs-ttc-routes-and-schedules"
PKG_SURFACE = "surface-routes-and-schedules-for-bustime"


class CkanError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResourceRef:
    url: str
    last_modified: str | None
    size: int | None


def make_client(user_agent: str, timeout: float = 120.0) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True)


def resolve_zip(client: httpx.Client, package_id: str) -> ResourceRef:
    r = client.get(f"{CKAN_BASE}/package_show", params={"id": package_id})
    r.raise_for_status()
    for res in r.json().get("result", {}).get("resources", []):
        if str(res.get("format", "")).upper() == "ZIP":
            return ResourceRef(res["url"], res.get("last_modified"), res.get("size"))
    raise CkanError(f"{package_id}: no ZIP resource in CKAN package")


def head_etag(client: httpx.Client, url: str) -> str | None:
    r = client.head(url)
    r.raise_for_status()
    return r.headers.get("ETag")


def download(client: httpx.Client, url: str, dest: Path) -> tuple[Path, str | None]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with client.stream("GET", url) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
        etag = r.headers.get("ETag")
    tmp.replace(dest)
    return dest, etag
