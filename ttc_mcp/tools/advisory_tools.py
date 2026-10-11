from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Literal

from fastmcp import FastMCP

from ttc_mcp import advisory, runtime
from ttc_mcp.domain import detours as det
from ttc_mcp.domain import service_changes as sc
from ttc_mcp.domain.arrivals import stop_row
from ttc_mcp.domain.disruptions import element_key, station_by_name, station_row
from ttc_mcp.realtime import attempt

Kind = Literal["closure", "suspension", "detour", "rsz", "elevator", "escalator", "other"]


async def _aguard(fn):
    """fn(feed, snapshot) -> (payload, extra hits, source); meta covers the snapshot's sources plus any extra fetch."""
    try:
        feed = runtime.feed_for()
    except runtime.StoreUnavailable as e:
        return {"error": str(e), "meta": runtime.meta(None)}
    snap = await advisory.snapshot(feed)
    payload, extra, source = await fn(feed, snap)
    if "error" in payload:
        return {**payload, "meta": runtime.meta(feed)}
    meta = runtime.meta_rt(feed, snap.hits + extra, source=source)
    meta["errors"] = snap.errors or None
    return {**payload, "meta": meta}


def _objects(conn, el: dict) -> dict:
    stops = []
    for n in el["stops"][:50]:
        r = conn.execute("SELECT name FROM stops WHERE stop_id=?", (n,)).fetchone()
        stops.append({"number": n, "name": r["name"] if r else None})
    return {**el, "station_objects": [{"id": s, "name": n} for s, n in zip(el["stations"], el["station_names"])], "stop_objects": stops}


def register(mcp: FastMCP) -> None:
    @mcp.tool
    async def alerts(route: str | None = None, station: str | None = None, stop: str | None = None, type: Kind | None = None) -> dict:
        """Current TTC alerts as normalised elements: subway closures (with nightly windows and shuttle info), surface suspensions and
        detours (alerts and the TripModifications feed), reduced speed zones, elevator and escalator outages. Filter by route, station
        (id, name or alias), stop number or type. Stations and stops are resolved to objects; times are America/Toronto."""
        async def run(feed, snap):
            conn, els = feed.conn, snap.disruptions.elements
            if route:
                els = [e for e in els if route in e["routes"]]
            if type:
                els = [e for e in els if e["type"] == type]
            if station:
                row = station_row(conn, station.strip()) or station_by_name(conn, station)
                if row is None:
                    return {"error": f"station {station!r} not found"}, [], advisory.SOURCE
                els = [e for e in els if row["stop_id"] in e["stations"]]
            if stop:
                row = stop_row(conn, stop.strip())
                if row is None:
                    return {"error": f"stop {stop} not found"}, [], advisory.SOURCE
                # The stop's own view (closure copies carry the terminus role), narrowed to what the other filters kept.
                keys = {element_key(e) for e in els}
                els = [e for e in snap.disruptions.for_stop(row) if element_key(e) in keys]
            out = [_objects(conn, e) for e in els]
            return {"alerts": out, "counts": dict(Counter(e["type"] for e in out)), "as_of": snap.as_of()}, [], advisory.SOURCE
        return await _aguard(run)

    @mcp.tool
    async def service_status() -> dict:
        """The first call for any trip or "what's going on" question: subway closures as station spans with windows, active_now and
        shuttle info; suspended or detoured surface routes (with skipped-stop counts and reasons); reduced speed zones; elevators and
        escalators out by station; counts and as_of."""
        async def run(feed, snap):
            return snap.disruptions.service_status(snap.alerts, snap.detours), [], advisory.SOURCE
        return await _aguard(run)

    @mcp.tool
    async def detours(route: str | None = None) -> dict:
        """Active surface detours from the TTC's TripModifications feed, grouped by detour id: per pattern the skipped stops, where the
        vehicle leaves and rejoins the route, the detour path (encoded polyline and length), trips affected, service dates, and the
        reason when a service change matches (null otherwise)."""
        async def run(feed, snap):
            if snap.detour_feed is None:
                return {"detours": [], "count": 0, "note": f"detour feed unavailable ({snap.errors.get('detours')})"}, [], "gtfsrt.ttc.ca/trips/detour"
            chit, _ = await attempt(runtime.get_realtime().service_changes())
            groups = det.resolve_detours(feed.conn, snap.detour_feed, runtime.now().date(), rt=snap.rt,
                                         changes=chit.parsed if chit else None, route=route, lookup=runtime.trip_lookup())
            return {"detours": groups, "count": len(groups), "entities": sum(len(g["directions"]) for g in groups),
                    "resolved": sum(d["pattern"] != "unknown" for g in groups for d in g["directions"])}, \
                ([chit] if chit else []), "gtfsrt.ttc.ca/trips/detour + gtfsrt alerts + ttcapi"
        return await _aguard(run)

    @mcp.tool
    async def service_changes(route: str | None = None, date: str | None = None) -> dict:
        """Board-period route changes: GTFS-RT MODIFIED_SERVICE alerts (title, text, period, url, routes) merged with ttc.ca's
        service-changes list when published. Filter by route and by a date the change must cover."""
        async def run(feed, snap):
            chit, err = await attempt(runtime.get_realtime().service_changes())
            day = dt.date.fromisoformat(date) if date else None
            rows = sc.merged(snap.rt, chit.parsed if chit else None, route=route, day=day)
            payload = {"changes": rows, "count": len(rows)}
            if err:
                payload["note"] = f"ttcapi unavailable ({err}); GTFS-RT only"
            return payload, ([chit] if chit else []), "gtfsrt alerts + ttcapi service changes"
        return await _aguard(run)
