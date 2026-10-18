from __future__ import annotations

import datetime as dt
import sqlite3
import sys
import time
from pathlib import Path

import httpx

from ttc_mcp.adapters import ckan
from ttc_mcp.adapters.ttc_alerts import AlertList, parse_alerts
from ttc_mcp.config import settings
from ttc_mcp.sources import ALERTS
from ttc_mcp.static.build import build_sqlite
from ttc_mcp.static.checks import run_checks
from ttc_mcp.static.manifest import FeedEntry, Manifest
from ttc_mcp.static.schedule import TZ
from ttc_mcp.static.schema import SCHEMA_VERSION
from ttc_mcp.static.store import schema_version


def _log(log, msg):
    (log or (lambda m: print(m, file=sys.stderr)))(msg)


def _run(coro):
    """asyncio.run when this thread has no loop; otherwise a worker thread with its own loop (a caller inside FastMCP's loop)."""
    import asyncio
    import concurrent.futures
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _loaded_levels(cache_dir: Path, manifest: Manifest) -> dict[str, dict]:
    """The service levels the stores already listed carry, the newest feed's winning; a store that cannot be read gives none."""
    out: dict[str, dict] = {}
    for f in sorted(manifest.feeds, key=lambda f: (f.start, f.feed_version)):
        try:
            conn = sqlite3.connect(f"file:{cache_dir / f.path}?mode=ro", uri=True)
            try:
                rows = conn.execute("SELECT short_name, service_level_name, service_level_desc, frequency, ten_minute, map_url FROM routes "
                                    "WHERE service_level_name IS NOT NULL").fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        out |= {r[0]: {"name": r[1], "description": r[2], "frequency": r[3], "ten_minute": bool(r[4]), "map_url": r[5]} for r in rows}
    return out


def _levels_for(merged_zip: Path, user_agent: str, log, cache_dir: Path | None = None, manifest: Manifest | None = None) -> dict[str, dict]:
    import zipfile

    from ttc_mcp.adapters.http import make_async_client
    from ttc_mcp.adapters.ttcapi import fetch_route_levels
    from ttc_mcp.static import gtfs

    with zipfile.ZipFile(merged_zip) as z:
        names = [r["route_short_name"] for r in gtfs.read_rows(z, "routes.txt")]

    async def go():
        async with make_async_client(user_agent, timeout=10) as client:
            return await fetch_route_levels(client, names, log=log)

    levels = _run(go())
    # ttc.ca's WAF refuses most of a burst from Google Cloud (2026-10-18); a level changes at most once a board period.
    kept = {s: lv for s, lv in _loaded_levels(cache_dir, manifest).items() if s in names and s not in levels} if cache_dir and manifest else {}
    _log(log, f"[refresh] ttc.ca service levels: {len(levels)} of {len(names)} routes" + (f"; {len(kept)} carried over from the loaded store" if kept else ""))
    return levels | kept


def _alerts_for_checks(client: httpx.Client, log) -> AlertList | None:
    """One advisory fetch for the post-build checks; a failure skips the live checks and never fails the build."""
    try:
        r = client.get(ALERTS.url, timeout=ALERTS.timeout)
        r.raise_for_status()
        return parse_alerts(r.content, {}).value
    except Exception as e:
        _log(log, f"[check] alerts.ttc.ca unavailable: {e}")
        return None


def _checked(cache_dir: Path, rep: dict, alerts: AlertList | None, log) -> dict:
    conn = sqlite3.connect(f"file:{cache_dir / f'static-{rep['feed_version']}.sqlite'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rep["checks"] = run_checks(conn, alerts, log=log)
    finally:
        conn.close()
    return rep


def _register(cache_dir: Path, manifest: Manifest, merged_zip: Path, surface_zip: Path | None, *,
              url: str, etag: str | None, surface_etag: str | None, today: dt.date, log, service_levels: dict | None = None) -> dict:
    tmp_out = cache_dir / "static-building.sqlite"
    meta = build_sqlite(merged_zip, tmp_out, surface_zip, source_url=url, source_etag=etag or "",
                        surface_etag=surface_etag or "", log=log, service_levels=service_levels)
    final = cache_dir / f"static-{meta.feed_version}.sqlite"
    tmp_out.replace(final)
    manifest.upsert(FeedEntry(meta.feed_version, meta.start, meta.end, final.name, etag, url,
                              time.strftime("%Y-%m-%dT%H:%M:%S%z"), surface_etag))
    manifest.prune(today)
    manifest.save(cache_dir)
    return {"changed": True, "feed_version": meta.feed_version, "reason": "built", "bytes": meta.bytes,
            "start": meta.start, "end": meta.end, "splits": meta.splits, "unequal_times": meta.unequal_times, "manifest": manifest}


def ensure_static(cache_dir: Path, client: httpx.Client, *, today: dt.date | None = None, force: bool = False,
                  surface: bool = True, log=None, ttcapi: bool = True, checks: bool = True) -> dict:
    cache_dir = Path(cache_dir)
    today = today or dt.date.today()
    manifest = Manifest.load(cache_dir)
    merged = ckan.resolve_zip(client, ckan.PKG_MERGED)
    merged_etag = ckan.head_etag(client, merged.url)
    surf = ckan.resolve_zip(client, ckan.PKG_SURFACE) if surface else None
    surf_etag = ckan.head_etag(client, surf.url) if surf else None
    # Recorded on every run, changed or not: feed_status's evidence that the daily check ran (spec 4.4).
    manifest.checked_at, manifest.ckan_last_modified = dt.datetime.now(TZ).isoformat(timespec="seconds"), merged.last_modified
    known = next((f for f in manifest.feeds
                  if f.source_url == merged.url and f.etag == merged_etag and f.surface_etag == surf_etag), None)
    if known and not force and merged_etag:
        found = schema_version(cache_dir / known.path)
        if found == SCHEMA_VERSION:
            manifest.save(cache_dir)
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
    levels = _levels_for(merged_zip, str(client.headers.get("User-Agent", "")), log, cache_dir, manifest) if ttcapi else None
    rep = _register(cache_dir, manifest, merged_zip, surface_zip, url=merged.url, etag=merged_etag,
                     surface_etag=surf_etag, today=today, log=log, service_levels=levels)
    rep["reason"] = reason or rep["reason"]
    return _checked(cache_dir, rep, _alerts_for_checks(client, log), log) if checks else rep


def build_from_zips(cache_dir: Path, merged_zip: Path, surface_zip: Path | None, *, today: dt.date | None = None,
                    log=None, ttcapi: bool = False, checks: bool = True) -> dict:
    cache_dir = Path(cache_dir)
    manifest = Manifest.load(cache_dir)
    levels = _levels_for(Path(merged_zip), settings().user_agent, log, cache_dir, manifest) if ttcapi else None
    rep = _register(cache_dir, manifest, Path(merged_zip), Path(surface_zip) if surface_zip else None,
                    url=f"file://{Path(merged_zip).resolve()}", etag=None, surface_etag=None,
                    today=today or dt.date.today(), log=log, service_levels=levels)
    return _checked(cache_dir, rep, None, log) if checks else rep
