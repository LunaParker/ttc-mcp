from __future__ import annotations

import asyncio

from fastmcp import FastMCP

from ttc_mcp import advisory, runtime
from ttc_mcp.domain import arrivals as arr
from ttc_mcp.domain import live, trains
from ttc_mcp.domain import vehicles as veh
from ttc_mcp.realtime import attempt as _try


async def _aguard(fn):
    try:
        feed = runtime.feed_for()
    except runtime.StoreUnavailable as e:
        return {"error": str(e), "meta": runtime.meta(None)}
    result = await fn(feed)
    payload, hits, source = result[:3]
    snap = result[3] if len(result) > 3 else None
    if "error" in payload or not source:
        return {**payload, "meta": runtime.meta(feed)}
    meta = runtime.meta_rt(feed, hits, source=source)
    if snap is not None:
        meta["advisories"] = snap.meta()
    return {**payload, "meta": meta}


def register(mcp: FastMCP) -> None:
    @mcp.tool
    async def arrivals(stop: str, route: str | None = None, limit: int = 10) -> dict:
        """Next vehicles at a bus or streetcar stop (BusTime predictions, crowding from the vehicle feed) or next trains at a rail
        platform (NTAS). Rows carry basis realtime or schedule: when no real-time prediction exists the timetable fills in and a note says so.
        Every row carries detour_id (the vehicle's trip is on a detour) and the answer lists the stop's disruptions."""
        async def run(feed):
            conn, rt, now = feed.conn, runtime.get_realtime(), runtime.now()
            row = arr.stop_row(conn, stop)
            if row is None:
                return {"error": f"stop {stop} not found"}, [], ""
            if row["location_type"] != 0:
                return {"error": f"{stop} is a station, not a stop; use next_trains or a platform number"}, [], ""
            sid = row["stop_id"]
            head = {"stop": {"number": sid, "name": row["name"], "lat": row["lat"], "lon": row["lon"], "station": row["parent_station"] or None}}
            rows, hits, source, note = await live.arrivals_at(feed, rt, row, route=route, limit=limit, now=now)
            snap = await advisory.snapshot(feed)
            active = {g["route"] for g in snap.detours if g["active_today"]}
            vehicle_trip = {}
            if active & set(arr.served_at(conn, sid)) and any(r["basis"] == "realtime" for r in rows):
                thit, _ = await _try(rt.trips())
                if thit:
                    hits.append(thit)
                    vehicle_trip = thit.parsed.vehicle_trip
            for r in rows:
                trip = vehicle_trip.get(r["vehicle_id"] or "") if r["basis"] == "realtime" else r.get("trip_id")
                r["detour_id"] = snap.trip_detour.get(trip or "")
            return {**head, "arrivals": rows[:limit], "disruptions": snap.disruptions.for_stop(row),
                    **({"note": note} if note else {})}, hits, source, snap
        return await _aguard(run)

    @mcp.tool
    async def next_trains(query: str) -> dict:
        """Next trains at every rail platform of a station (id, name or alias such as "Dundas" or "Bloor") or at one platform id,
        from the TTC's NTAS. A platform with no prediction falls back to the timetable (service no_prediction); an empty NTAS answer never means no trains.
        A platform inside an active closure is reported as service suspended with the closure, whatever NTAS says."""
        async def run(feed):
            conn, rt, now = feed.conn, runtime.get_realtime(), runtime.now()
            res = trains.resolve(conn, query)
            if res is None:
                return {"error": f"{query!r} is not a rail station or platform"}, [], ""
            station, platforms = res
            results = await asyncio.gather(*(_try(rt.next_trains(p["stop_id"])) for p in platforms))
            entries = [trains.platform_entry(conn, p, h.parsed if h else None, err, now) for p, (h, err) in zip(platforms, results)]
            snap = await advisory.snapshot(feed)
            for p, e in zip(platforms, entries):
                closed = snap.disruptions.platform_closed_now(p)
                if closed:
                    e["service"], e["disruption"] = "suspended", closed
            source = "ntas" + (" + static stop_times" if any(e["basis"] == "schedule" for e in entries) else "")
            return {"station": {"id": station["stop_id"], "name": station["name"]}, "platforms": entries,
                    "disruptions": snap.disruptions.for_station(station["stop_id"])}, [h for h, _ in results if h], source, snap
        return await _aguard(run)

    @mcp.tool
    async def vehicles(route: str | None = None, vehicle_id: str | None = None) -> dict:
        """Live positions of the buses and streetcars on a route, or one vehicle by fleet number: coordinates, bearing, speed, current stop
        (public number), headsign and crowding. Rail lines (1, 2, 4, 5, 6) publish no positions and answer unavailable; use next_trains."""
        async def run(feed):
            if not (route or vehicle_id):
                return {"error": "give a route or a vehicle_id"}, [], ""
            if route and veh.is_rail(feed.conn, route):
                return {"route": route, "vehicles": [], "unavailable": "the TTC publishes no vehicle positions for rail lines; use next_trains"}, [], ""
            hit, err = await _try(runtime.get_realtime().vehicles())
            if hit is None:
                return {"route": route, "vehicle_id": vehicle_id, "vehicles": [], "note": f"vehicle feed unavailable ({err})"}, [], "bustime vehicles"
            return {"route": route, "vehicle_id": vehicle_id,
                    "vehicles": veh.vehicles_on(feed.conn, hit.parsed, route=route, vehicle_id=vehicle_id)}, [hit], "bustime vehicles"
        return await _aguard(run)

    @mcp.tool
    async def feed_status(probe: bool = False) -> dict:
        """Freshness of the static feed and of every real-time source as cached in this process (TTL state, age, feed timestamp,
        last error, NTAS coverage per line). probe=true makes one live request per source."""
        rt = runtime.get_realtime()
        probed = await rt.probe() if probe else None
        store, today = runtime.get_store(), runtime.now().date()
        st, active = store.status(today), store.for_date(today)
        info = dict(active.conn.execute("SELECT * FROM feed_info").fetchone()) if active else {}
        static = {"feed_version": st["active"], "start": info.get("feed_start_date"), "end": info.get("feed_end_date"),
                  "etag": info.get("source_etag"), "built_at": info.get("built_at"), "days_remaining": st["days_remaining"],
                  "newer_seen": st["newer_seen"], "verdict": st["verdict"], "feeds": st["feeds"]}
        return {"static": static, "sources": rt.status(), "ntas_coverage": rt.ntas_coverage(),
                **({"probed": probed} if probed is not None else {}), "meta": runtime.meta(active)}
