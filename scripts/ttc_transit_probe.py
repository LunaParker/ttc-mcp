#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["gtfs-realtime-bindings>=2.0.0"]
# ///
"""
ttc_transit_probe.py -- reference implementation for the 2026-09-13 TTC stops / routes / arrivals / detours investigation.

Everything is fetched live (nothing is baked in):
  * stops + routes -> City of Toronto CKAN API -> "Merged GTFS" zip (all modes, station hierarchy, public stop codes)
  * RT id mapping  -> CKAN -> "Surface GTFS for BusTime" zip (stop_code -> internal stop_id used by bustime GTFS-RT)
  * arrivals       -> BusTime per-stop XML (public code) + GTFS-RT TripUpdates (internal id) + NTAS (rail platforms)
                      or, with --public-feed, gtfsrt.ttc.ca/trips/modified_update which is keyed by public stop numbers
  * detours        -> gtfsrt.ttc.ca/trips/detour (GTFS-RT TripModifications + Shape entities, served as JSON)

Usage:
  uv run ttc_transit_probe.py stops --query "cherry st at front"
  uv run ttc_transit_probe.py stops --route 504 --direction 0
  uv run ttc_transit_probe.py stops --station 14572          # Bloor-Yonge: platforms + entrances + nodes
  uv run ttc_transit_probe.py routes [--overnight | --daytime] [--series blue-night]
  uv run ttc_transit_probe.py route 504                      # ordered stops both ways + service hours
  uv run ttc_transit_probe.py arrivals 15461 [--public-feed] # surface stop (public stop number)
  uv run ttc_transit_probe.py arrivals 13863                 # subway platform (Bloor Stn northbound)
  uv run ttc_transit_probe.py detours [--route 112] [--live] # active detours resolved to skipped stops + reasons
  uv run ttc_transit_probe.py refresh                        # force re-download of both static feeds

Static feeds are cached in ~/.cache/ttc-probe (override with TTC_PROBE_CACHE) and only re-downloaded
when the CKAN resource's ETag changes, so "dynamic" costs one HEAD request per run.
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

CKAN = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_show?id="
PKG_MERGED = "merged-gtfs-ttc-routes-and-schedules"       # all modes; location_type/parent_station populated
PKG_SURFACE = "surface-routes-and-schedules-for-bustime"  # bus + streetcar; stop_id space used by bustime GTFS-RT
BUSTIME_TRIPS_RT = "https://bustime.ttc.ca/gtfsrt/trips"
BUSTIME_STOP_XML = "https://bustime.ttc.ca/bustime/map/getStopPredictions.jsp?stop={code}&route=all"
NTAS = "https://ntas.ttc.ca/api/ntas/get-next-train-time/{platform}"
DETOUR_FEED = "https://gtfsrt.ttc.ca/trips/detour?format=text"            # JSON (protobuf JSON mapping) despite the name
MODIFIED_FEED = "https://gtfsrt.ttc.ca/trips/modified_update?format=text"  # JSON; TripUpdates keyed by PUBLIC stop numbers
SERVICE_CHANGES = "https://www.ttc.ca/ttcapi/routedetail/getallroutesandstopsalerts"
RAIL_LINES = {"1", "2", "4", "5", "6"}  # NTAS covers subway (1/2/4) and the Line 5/6 LRTs
CACHE = Path(os.environ.get("TTC_PROBE_CACHE", Path.home() / ".cache" / "ttc-probe"))
UA = "ttc-transit-probe/0.3"
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


# ----------------------------------------------------------------------------- HTTP helpers
def http(url: str, method: str = "GET", headers: dict | None = None, timeout: int = 180):
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, (b"" if method == "HEAD" else r.read())
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, b""


def ckan_zip_url(package_id: str) -> tuple[str, str | None]:
    """Resolve the current download URL of a CKAN package's ZIP resource (URLs are stable, but never assume)."""
    status, _, body = http(CKAN + package_id, timeout=30)
    if status != 200:
        raise SystemExit(f"CKAN package_show {package_id}: HTTP {status}")
    res = json.loads(body)["result"]
    zips = [r for r in res["resources"] if r.get("format", "").upper() == "ZIP"]
    if not zips:
        raise SystemExit(f"{package_id}: no ZIP resource in CKAN package")
    return zips[0]["url"], zips[0].get("last_modified")


def fetch_zip(package_id: str, force: bool = False) -> tuple[Path, bool]:
    """Return (path, changed). Re-downloads only when the resource ETag differs from the cached one."""
    CACHE.mkdir(parents=True, exist_ok=True)
    url, ckan_mtime = ckan_zip_url(package_id)
    zpath, meta = CACHE / f"{package_id}.zip", CACHE / f"{package_id}.meta.json"
    _, h, _ = http(url, method="HEAD", timeout=30)
    etag = h.get("etag")
    if zpath.exists() and meta.exists() and not force:
        cached = json.loads(meta.read_text())
        if etag and cached.get("etag") == etag:
            return zpath, False
    print(f"[fetch] downloading {package_id} (CKAN last_modified {ckan_mtime}) ...", file=sys.stderr)
    status, h, body = http(url)
    if status != 200:
        raise SystemExit(f"{package_id}: HTTP {status} from {url}")
    zpath.write_bytes(body)
    meta.write_text(json.dumps({"etag": h.get("etag"), "last_modified": h.get("last-modified"), "url": url,
                                "ckan_last_modified": ckan_mtime, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}))
    return zpath, True


def rows(z: zipfile.ZipFile, name: str):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))


def secs(hms: str) -> int:
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def hhmm(x: int) -> str:
    return f"{x // 3600:02d}:{(x % 3600) // 60:02d}"


def fmt_ll(s: dict) -> str:
    return f"{s['lat']:.6f} {s['lon']:.6f}" if s.get("lat") is not None else "(no coordinates)"


def overnight_share(r: dict, window=(1, 15)) -> float:
    """Share of a route's trips that start between 00:30 and 07:30 (bins 1..14 of the half-hour histogram).
    Blue Night routes score ~1.0; all-day routes score ~0.1 (their first trips only)."""
    h = r["start_hist"]
    return sum(h[window[0]:window[1]]) / sum(h) if sum(h) else 0.0


def series_of(short: str) -> str:
    """TTC route-number conventions (a heuristic, not data; ttc.ca's serviceLevel is the authoritative label)."""
    try:
        n = int(short)
    except ValueError:
        return "other"
    if n < 7:
        return "rail"
    if 300 <= n < 400:
        return "blue-night"
    if 400 <= n < 500:
        return "community"
    if 500 <= n < 600:
        return "streetcar"
    if 900 <= n < 1000:
        return "express"
    return "bus"


def decode_polyline(s: str) -> list[tuple[float, float]]:
    """Google encoded polyline -> [(lat, lon)], as used by GTFS-RT Shape entities."""
    idx = lat = lng = 0
    out: list[tuple[float, float]] = []
    while idx < len(s):
        for which in (0, 1):
            res = shift = 0
            while True:
                b = ord(s[idx]) - 63
                idx += 1
                res |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(res >> 1) if res & 1 else res >> 1
            if which == 0:
                lat += delta
            else:
                lng += delta
        out.append((lat / 1e5, lng / 1e5))
    return out


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


# ----------------------------------------------------------------------------- static index
def build_index(force: bool = False) -> dict:
    """
    One streaming pass over the merged GTFS producing:
      stops[stop_id]  = {code, name, lat, lon, location_type, parent, wheelchair,
                         served: [[route_short, direction_id, headsign, route_type], ...]}
      trips[trip_id]  = [route_short, direction_id, headsign, pattern_index]   (labels for GTFS-RT rows)
      routes[short]   = {long, type, series, start_hist, service: {day_label: {start, end, trips}},
                         directions: {dir: {headsigns: {...}, patterns: [{trips, headsigns, stops: [[seq, stop_id], ...]}]}}}
    A physical stop is directional by construction: the eastbound and westbound stops at the same intersection
    are different stop_ids with different coordinates; 'served' says which route/direction/headsign call there.
    Patterns keep the trip's own stop_sequence numbers so GTFS-RT stop selectors resolve exactly.
    """
    zpath, changed = fetch_zip(PKG_MERGED, force)
    idx_path = CACHE / "stop_index.json"
    if idx_path.exists() and not changed and not force:
        return json.loads(idx_path.read_text())
    print("[index] building index from stop_times.txt (about 7.7M rows, ~2 min) ...", file=sys.stderr)
    z = zipfile.ZipFile(zpath)
    routes = {r["route_id"]: r for r in rows(z, "routes.txt")}
    cal = {c["service_id"]: c for c in rows(z, "calendar.txt")}
    trips = {t["trip_id"]: (t["route_id"], t["service_id"], t["direction_id"], t["trip_headsign"])
             for t in rows(z, "trips.txt")}

    served: dict[str, set] = defaultdict(set)
    patterns: dict[tuple, Counter] = defaultdict(Counter)          # (route_id, dir) -> Counter{pattern}
    pattern_headsign: dict[tuple, Counter] = defaultdict(Counter)  # (route_id, dir, pattern) -> Counter{headsign}
    pattern_intern: dict[tuple, tuple] = {}                        # share one tuple object per distinct pattern
    trip_pattern: dict[str, tuple] = {}
    span: dict[tuple, list] = defaultdict(lambda: [10**9, -1, 0])  # (route_id, service_id) -> [start, end, trips]
    starts: dict[str, list] = defaultdict(list)                    # route_id -> trip start times

    cur, cur_rows = None, []

    def flush():
        if cur is None or not cur_rows:
            return
        cur_rows.sort(key=lambda r: r[0])
        r, s, d, h = trips[cur]
        pat = tuple((x[0], x[1]) for x in cur_rows)                # ((stop_sequence, stop_id), ...)
        pat = pattern_intern.setdefault(pat, pat)
        patterns[(r, d)][pat] += 1
        pattern_headsign[(r, d, pat)][h] += 1
        trip_pattern[cur] = pat
        t0, t1 = cur_rows[0][2], cur_rows[-1][2]
        v = span[(r, s)]
        v[0], v[1], v[2] = min(v[0], t0), max(v[1], t1), v[2] + 1
        starts[r].append(t0)

    for st in rows(z, "stop_times.txt"):  # TTC's file is grouped by trip_id (verified), so stream it
        tid = st["trip_id"]
        if tid != cur:
            flush()
            cur, cur_rows = tid, []
        served[st["stop_id"]].add(trips[tid][0:1] + trips[tid][2:4])
        cur_rows.append((int(st["stop_sequence"]), st["stop_id"], secs(st["departure_time"] or st["arrival_time"])))
    flush()

    stops = {}
    for s in rows(z, "stops.txt"):
        stops[s["stop_id"]] = {
            "code": s["stop_code"], "name": s["stop_name"],
            "lat": float(s["stop_lat"]) if s["stop_lat"] else None, "lon": float(s["stop_lon"]) if s["stop_lon"] else None,
            "location_type": s.get("location_type") or "0", "parent": s.get("parent_station", ""),
            "wheelchair": s.get("wheelchair_boarding", ""),
            "served": sorted([routes[r]["route_short_name"], d, h, routes[r]["route_type"]]
                             for r, d, h in served.get(s["stop_id"], ())),
        }

    def day_label(service_id: str) -> str:
        c = cal.get(service_id)
        if not c:
            return f"service {service_id} (calendar_dates only)"
        on = [d[:3].title() for d in DAYS if c[d] == "1"]
        return ",".join(on) if on else f"service {service_id} (exceptions only)"

    route_out, pattern_index = {}, {}
    for rid, r in routes.items():
        hist = [0] * 48  # half-hour bins of trip start times, modulo 24h
        for t in starts.get(rid, []):
            hist[(t % 86400) // 1800] += 1
        dirs = {}
        for (r2, d), cnt in patterns.items():
            if r2 != rid:
                continue
            pats = []
            for i, (p, n) in enumerate(cnt.most_common()):
                pattern_index[(rid, d, p)] = i
                pats.append({"trips": n, "headsigns": dict(pattern_headsign[(rid, d, p)]), "stops": [list(x) for x in p]})
            dirs[d] = {"headsigns": dict(sum((Counter(pattern_headsign[(rid, d, p)]) for p in cnt), Counter())),
                       "patterns": pats}
        route_out[r["route_short_name"]] = {
            "route_id": rid, "long": r["route_long_name"], "type": r["route_type"],
            "series": series_of(r["route_short_name"]), "start_hist": hist,
            "service": {day_label(s): {"start": hhmm(a), "end": hhmm(b), "trips": n}
                        for (r2, s), (a, b, n) in span.items() if r2 == rid},
            "directions": dirs,
        }

    feed_info = next(rows(z, "feed_info.txt"), {})
    out = {"feed": feed_info, "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "stops": stops, "routes": route_out,
           "trips": {tid: [routes[r]["route_short_name"], d, h, pattern_index.get((r, d, trip_pattern.get(tid)), -1)]
                     for tid, (r, s, d, h) in trips.items()}}
    idx_path.write_text(json.dumps(out))
    return out


def surface_code_map(force: bool = False) -> dict[str, str]:
    """public stop_code -> internal stop_id used by bustime.ttc.ca GTFS-RT (from the Surface GTFS)."""
    zpath, changed = fetch_zip(PKG_SURFACE, force)
    p = CACHE / "surface_code_map.json"
    if p.exists() and not changed and not force:
        return json.loads(p.read_text())
    m = {s["stop_code"]: s["stop_id"] for s in rows(zipfile.ZipFile(zpath), "stops.txt")}
    p.write_text(json.dumps(m))
    return m


# ----------------------------------------------------------------------------- real-time
def arrivals_bustime_xml(code: str) -> list[dict]:
    """Undocumented but public: the XML the TTC's own TrackTTC map page uses. Keyed by public stop number."""
    status, _, body = http(BUSTIME_STOP_XML.format(code=code), timeout=30)
    if status != 200 or not body.strip():
        return []
    root = ET.fromstring(body)
    if root.tag == "error":
        return []
    return [{"route": (p.findtext("rn") or "").strip(), "direction": (p.findtext("fd") or "").strip(),
             "eta": (p.findtext("pt") or "").strip(), "vehicle": p.findtext("v"),
             "scheduled_only": (p.findtext("scheduled") or "").strip() == "true"} for p in root.iter("pre")]


def arrivals_gtfs_rt(code: str, index: dict, code_map: dict[str, str]) -> list[dict]:
    """Official GTFS-RT TripUpdates (protobuf). NB: stop_ids here are the Surface-GTFS internal ids."""
    from google.transit import gtfs_realtime_pb2  # installed by uv from the script header
    sid = code_map.get(code)
    if not sid:
        return []
    status, _, body = http(BUSTIME_TRIPS_RT, timeout=60)
    if status != 200:
        return []
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(body)
    now, out = time.time(), []
    for ent in feed.entity:
        if not ent.HasField("trip_update"):
            continue
        tu = ent.trip_update
        for stu in tu.stop_time_update:
            if stu.stop_id != sid:
                continue
            t = stu.arrival.time if stu.HasField("arrival") else stu.departure.time
            label = index["trips"].get(tu.trip.trip_id)
            out.append({"route": tu.trip.route_id, "headsign": label[2] if label else "(trip not in static feed)",
                        "eta_min": round((t - now) / 60, 1), "vehicle": tu.vehicle.id or None,
                        "trip_id": tu.trip.trip_id, "rt_stop_id": sid})
    return sorted(out, key=lambda r: r["eta_min"])


def arrivals_public_feed(code: str, index: dict) -> list[dict]:
    """gtfsrt.ttc.ca/trips/modified_update: the same TripUpdates re-keyed by PUBLIC stop numbers (no crosswalk),
    plus ModifiedTripSelector links to active detours. Served as protobuf-JSON; parse with json_format if you
    want typed access, or plain json as here."""
    status, _, body = http(MODIFIED_FEED, timeout=90)
    if status != 200:
        return []
    now, out = time.time(), []
    for ent in json.loads(body).get("entity", []):
        tu = ent.get("tripUpdate", {})
        trip = tu.get("trip", {})
        for stu in tu.get("stopTimeUpdate", []):
            if stu.get("stopId") != code:
                continue
            t = int((stu.get("arrival") or stu.get("departure") or {}).get("time", 0))
            label = index["trips"].get(trip.get("tripId", ""))
            out.append({"route": trip.get("routeId"), "headsign": label[2] if label else "(trip not in static feed)",
                        "eta_min": round((t - now) / 60, 1), "vehicle": (tu.get("vehicle") or {}).get("id"),
                        "trip_id": trip.get("tripId"), "relationship": stu.get("scheduleRelationship"),
                        "detour": (trip.get("modifiedTrip") or {}).get("modificationsId")})
    return sorted(out, key=lambda r: r["eta_min"])


def arrivals_ntas(platform_id: str) -> list[dict]:
    """Next Train Arrival System used by ttc.ca station pages. Path param = GTFS platform stop id."""
    status, _, body = http(NTAS.format(platform=platform_id), timeout=30)
    return json.loads(body or b"[]") if status == 200 else []


# ----------------------------------------------------------------------------- CLI
def cmd_stops(args, index):
    stops = index["stops"]
    q = (args.query or "").lower()
    hits = []
    for sid, s in stops.items():
        if args.station:
            if sid != args.station and s["parent"] != args.station:
                continue
        elif s["location_type"] != "0":
            continue  # station parents / entrances / pathway nodes only appear via --station
        if q and q not in s["name"].lower():
            continue
        if args.route and not any(r == args.route and (args.direction is None or d == args.direction)
                                  for r, d, _, _ in s["served"]):
            continue
        hits.append((sid, s))
    if args.json:
        print(json.dumps(dict(hits), indent=1))
        return
    fi = index["feed"]
    print(f"feed {fi.get('feed_version')} {fi.get('feed_start_date')}..{fi.get('feed_end_date')} | {len(hits)} stop(s)")
    for sid, s in hits:
        kind = {"0": "stop", "1": "STATION", "2": "entrance", "3": "node"}[s["location_type"]]
        print(f"{sid:>7} {kind:8} {s['name']:<58} {fmt_ll(s)}" + (f"  parent={s['parent']}" if s["parent"] else ""))
        for r, d, h, _ in s["served"]:
            if args.route is None or r == args.route:
                print(f"           {r:>4} dir {d}  {h}")


def cmd_routes(args, index):
    rs = index["routes"]
    keys = sorted(rs, key=lambda k: (0, int(k)) if k.isdigit() else (1, k))
    n = 0
    for k in keys:
        r = rs[k]
        share = overnight_share(r)
        night = share >= 0.85
        if args.overnight and not night:
            continue
        if args.daytime and night:
            continue
        if args.series and r["series"] != args.series:
            continue
        n += 1
        svc = "; ".join(f"{d} {v['start']}-{v['end']}" for d, v in r["service"].items())
        print(f"{k:>4} {r['long']:<34} {r['series']:<10} {'OVERNIGHT' if night else 'daytime ':9} "
              f"night-share {share:.2f} | {svc}")
    print(f"{n} route(s)")


def cmd_route(args, index):
    r = index["routes"].get(args.route)
    if not r:
        raise SystemExit(f"route {args.route} not in feed")
    print(f"{args.route} {r['long']}  (gtfs route_type {r['type']}, series {r['series']}, "
          f"overnight share {overnight_share(r):.2f})")
    for d, v in r["service"].items():
        print(f"   runs {d}: {v['start']}-{v['end']}  ({v['trips']} trips)")
    for d in sorted(r["directions"]):
        info = r["directions"][d]
        print(f"-- direction {d}: headsigns {info['headsigns']}")
        for i, p in enumerate(info["patterns"][: args.patterns]):
            print(f"   pattern {i + 1}: {p['trips']} trips, {len(p['stops'])} stops, headsigns {p['headsigns']}")
            for seq, sid in p["stops"]:
                s = index["stops"][sid]
                print(f"      {seq:>3} {sid:>6}  {s['name']:<58} {fmt_ll(s)}")


def cmd_arrivals(args, index):
    s = index["stops"].get(args.stop)
    if not s:
        raise SystemExit(f"stop {args.stop} not in the merged GTFS")
    print(f"{args.stop}  {s['name']}  ({fmt_ll(s)})")
    routes_here = {r for r, _, _, _ in s["served"]}
    if routes_here & RAIL_LINES:
        print("-- NTAS (next train):")
        for row in arrivals_ntas(args.stop):
            print(f"   Line {row['line']} {row['directionText']}: next trains in [{row['nextTrains']}] min")
        return
    if args.public_feed:
        print("-- gtfsrt.ttc.ca/trips/modified_update (public stop numbers, no crosswalk):")
        for row in arrivals_public_feed(args.stop, index):
            print(f"   {row['route']:>4} {row['eta_min']:>6.1f} min  {row['headsign']}  veh {row['vehicle']}  "
                  f"({row['relationship']}{', on detour ' + row['detour'][:40] if row['detour'] else ''})")
        return
    print("-- BusTime XML (getStopPredictions.jsp):")
    for row in arrivals_bustime_xml(args.stop):
        print(f"   {row['route']:>4} {row['eta']:>7}  {row['direction']}  veh {row['vehicle']}"
              + ("  (scheduled, no vehicle)" if row["scheduled_only"] else ""))
    print("-- GTFS-RT TripUpdates (bustime.ttc.ca/gtfsrt/trips):")
    code_map = surface_code_map()
    for row in arrivals_gtfs_rt(args.stop, index, code_map):
        print(f"   {row['route']:>4} {row['eta_min']:>6.1f} min  {row['headsign']}  veh {row['vehicle']}  "
              f"(rt stop_id {row['rt_stop_id']}, trip {row['trip_id']})")


def cmd_detours(args, index):
    """Resolve every active TripModifications entity into route, direction, skipped stops, path, dates, reason."""
    status, _, body = http(DETOUR_FEED, timeout=60)
    if status != 200:
        raise SystemExit(f"detour feed: HTTP {status}")
    ents = json.loads(body).get("entity", [])
    shapes = {e["shape"]["shapeId"]: e["shape"]["encodedPolyline"] for e in ents if "shape" in e}
    mods = [e for e in ents if "tripModifications" in e]
    changes = []
    if not args.no_reasons:
        st, _, b = http(SERVICE_CHANGES, timeout=30)
        if st == 200:
            changes = json.loads(b).get("serviceChanges", [])
    live: Counter = Counter()
    if args.live:
        st, _, b = http(MODIFIED_FEED, timeout=90)
        if st == 200:
            for e in json.loads(b).get("entity", []):
                mt = e.get("tripUpdate", {}).get("trip", {}).get("modifiedTrip")
                if mt:
                    live[mt.get("modificationsId")] += 1
    stops = index["stops"]

    def name(sid):
        return f"{sid} {stops[sid]['name']}" if sid in stops else str(sid)

    n = 0
    for e in mods:
        tm = e["tripModifications"]
        sel = tm["selectedTrips"][0]
        tids = sel.get("tripIds", [])
        label = index["trips"].get(tids[0]) if tids else None
        if not label or label[3] < 0:
            print(f"== {e['id']}: selected trips not in the current static feed; skipped")
            continue
        route, d, headsign, pidx = label
        if args.route and route != args.route:
            continue
        n += 1
        pat = index["routes"][route]["directions"][d]["patterns"][pidx]
        seq_to_stop = {seq: sid for seq, sid in pat["stops"]}
        dates = tm.get("serviceDates", [])
        print(f"== {e['id']}")
        print(f"   route {route} dir {d}  {headsign}")
        print(f"   {len(tids)} scheduled trips affected; service dates {dates[0] if dates else '?'}..{dates[-1] if dates else '?'}"
              + (f"; {live.get(e['id'], 0)} of them live right now" if args.live else ""))
        pts = decode_polyline(shapes.get(sel.get("shapeId", ""), ""))
        if pts:
            km = sum(haversine_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) / 1000
            print(f"   detour path: Shape entity with {len(pts)} points, {km:.1f} km end to end")
        for m in tm.get("modifications", []):
            a, b = m["startStopSelector"]["stopSequence"], m["endStopSelector"]["stopSequence"]
            print(f"   skips stop sequences {a}..{b}: leaves the regular route after [{name(seq_to_stop.get(a - 1))}]"
                  f" and rejoins at [{name(seq_to_stop.get(b + 1))}]; replacement stops given: {len(m.get('replacementStops', []))};"
                  f" propagated delay {m.get('propagatedModificationDelay', 0)} s")
            for k in range(a, b + 1):
                sid = seq_to_stop.get(k)
                s = stops.get(sid)
                if s:
                    print(f"      SKIP seq {k:>3} {sid:>6} {s['name']:<58} {fmt_ll(s)}")
        for c in changes:
            if route in (c.get("route") or "").split(","):
                print(f"   reason (ttc.ca service change): {html.unescape(c.get('title') or '')} "
                      f"[{(c.get('effectiveDateTitle') or '').strip()}] {c.get('url') or ''}")
    print(f"{n} detour entit{'y' if n == 1 else 'ies'} ({len(mods)} in feed, {len(shapes)} shapes)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("stops", help="list directional stops (merged GTFS)")
    p.add_argument("--route", help="route short name, e.g. 504")
    p.add_argument("--direction", choices=["0", "1"], help="GTFS direction_id")
    p.add_argument("--query", help="case-insensitive substring of the stop name")
    p.add_argument("--station", help="station stop_id: show its platforms/entrances")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("routes", help="list every route with series + service hours")
    p.add_argument("--overnight", action="store_true", help="only routes whose trips run overnight (Blue Night)")
    p.add_argument("--daytime", action="store_true")
    p.add_argument("--series", choices=["rail", "streetcar", "bus", "express", "blue-night", "community", "other"])
    p = sub.add_parser("route", help="ordered stops in both directions for one route")
    p.add_argument("route")
    p.add_argument("--patterns", type=int, default=1, help="how many stop patterns per direction to print")
    p = sub.add_parser("arrivals", help="real-time arrivals at a public stop number / platform id")
    p.add_argument("stop")
    p.add_argument("--public-feed", action="store_true", help="use gtfsrt.ttc.ca/trips/modified_update (public stop ids)")
    p = sub.add_parser("detours", help="active detours (GTFS-RT TripModifications) resolved against the static feed")
    p.add_argument("--route", help="only this route short name")
    p.add_argument("--live", action="store_true", help="also count trips currently linked to each detour")
    p.add_argument("--no-reasons", action="store_true", help="skip the ttc.ca service-changes lookup")
    sub.add_parser("refresh", help="force re-download of both static feeds and rebuild caches")
    args = ap.parse_args()
    if args.cmd == "refresh":
        build_index(force=True)
        surface_code_map(force=True)
        print("refreshed", CACHE)
        return
    index = build_index()
    {"stops": cmd_stops, "routes": cmd_routes, "route": cmd_route, "arrivals": cmd_arrivals,
     "detours": cmd_detours}[args.cmd](args, index)


if __name__ == "__main__":
    main()
