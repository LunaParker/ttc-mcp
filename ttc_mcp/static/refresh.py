from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

import httpx

from ttc_mcp.adapters import ckan
from ttc_mcp.static.build import build_sqlite
from ttc_mcp.static.manifest import FeedEntry, Manifest
from ttc_mcp.static.schema import SCHEMA_VERSION
from ttc_mcp.static.store import schema_version


def _log(log, msg):
    (log or (lambda m: print(m, file=sys.stderr)))(msg)


def _register(cache_dir: Path, manifest: Manifest, merged_zip: Path, surface_zip: Path | None, *,
              url: str, etag: str | None, surface_etag: str | None, today: dt.date, log) -> dict:
    tmp_out = cache_dir / "static-building.sqlite"
    meta = build_sqlite(merged_zip, tmp_out, surface_zip, source_url=url, source_etag=etag or "",
                        surface_etag=surface_etag or "", log=log)
    final = cache_dir / f"static-{meta.feed_version}.sqlite"
    tmp_out.replace(final)
    manifest.upsert(FeedEntry(meta.feed_version, meta.start, meta.end, final.name, etag, url,
                              time.strftime("%Y-%m-%dT%H:%M:%S%z"), surface_etag))
    manifest.prune(today)
    manifest.save(cache_dir)
    return {"changed": True, "feed_version": meta.feed_version, "reason": "built", "bytes": meta.bytes,
            "start": meta.start, "end": meta.end, "manifest": manifest}


def ensure_static(cache_dir: Path, client: httpx.Client, *, today: dt.date | None = None, force: bool = False,
                  surface: bool = True, log=None) -> dict:
    cache_dir = Path(cache_dir)
    today = today or dt.date.today()
    manifest = Manifest.load(cache_dir)
    merged = ckan.resolve_zip(client, ckan.PKG_MERGED)
    merged_etag = ckan.head_etag(client, merged.url)
    surf = ckan.resolve_zip(client, ckan.PKG_SURFACE) if surface else None
    surf_etag = ckan.head_etag(client, surf.url) if surf else None
    known = next((f for f in manifest.feeds
                  if f.source_url == merged.url and f.etag == merged_etag and f.surface_etag == surf_etag), None)
    if known and not force and merged_etag:
        found = schema_version(cache_dir / known.path)
        if found == SCHEMA_VERSION:
            return {"changed": False, "feed_version": known.feed_version, "reason": "etags unchanged", "manifest": manifest}
        _log(log, f"[refresh] {known.feed_version}: store schema {found} != {SCHEMA_VERSION}; rebuilding")
        reason = "schema changed"
    else:
        reason = None
    dl = cache_dir / "downloads"
    _log(log, f"[refresh] downloading {merged.url} ({merged.size or '?'} bytes)")
    merged_zip, merged_etag = ckan.download(client, merged.url, dl / "merged.zip")
    surface_zip = None
    if surf:
        _log(log, f"[refresh] downloading {surf.url} ({surf.size or '?'} bytes)")
        surface_zip, surf_etag = ckan.download(client, surf.url, dl / "surface.zip")
    rep = _register(cache_dir, manifest, merged_zip, surface_zip, url=merged.url, etag=merged_etag,
                     surface_etag=surf_etag, today=today, log=log)
    rep["reason"] = reason or rep["reason"]
    return rep


def build_from_zips(cache_dir: Path, merged_zip: Path, surface_zip: Path | None, *, today: dt.date | None = None,
                    log=None) -> dict:
    cache_dir = Path(cache_dir)
    return _register(cache_dir, Manifest.load(cache_dir), Path(merged_zip), Path(surface_zip) if surface_zip else None,
                     url=f"file://{Path(merged_zip).resolve()}", etag=None, surface_etag=None,
                     today=today or dt.date.today(), log=log)
