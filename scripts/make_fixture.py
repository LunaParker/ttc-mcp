#!/usr/bin/env python3
"""Build tests/fixtures/{gtfs-mini,surface-mini}.zip as a deterministic subset of the real TTC feeds.

Usage: uv run python scripts/make_fixture.py MERGED.zip SURFACE.zip [OUT_DIR]
"""
from __future__ import annotations

import csv
import io
import sys
import zipfile
from pathlib import Path

ROUTES = {"1", "5", "121", "300", "400", "504"}
SERVICES = {"1", "3", "4", "4401"}
EXTRA_NAME_PATTERNS = (" - Dundas Station",)
GO_LIMIT = 2


def rows(z, name):
    return list(csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig")))


def write(zout, name, fieldnames, items):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    w.writeheader()
    for it in items:
        w.writerow({k: it.get(k, "") for k in fieldnames})
    zout.writestr(name, buf.getvalue())


def main(merged: Path, surface: Path, out_dir: Path) -> None:
    z = zipfile.ZipFile(merged)
    routes = [r for r in rows(z, "routes.txt") if r["route_id"] in ROUTES]
    trips = [t for t in rows(z, "trips.txt") if t["route_id"] in ROUTES and t["service_id"] in SERVICES]
    trip_ids = {t["trip_id"] for t in trips}
    stop_times, stop_ids = [], set()
    with z.open("stop_times.txt") as f:
        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
        fields_st = reader.fieldnames
        for st in reader:
            if st["trip_id"] in trip_ids:
                stop_times.append(st)
                stop_ids.add(st["stop_id"])
    all_stops = rows(z, "stops.txt")
    by_id = {s["stop_id"]: s for s in all_stops}
    parents = {by_id[s]["parent_station"] for s in stop_ids if by_id[s].get("parent_station")}
    keep = set(stop_ids) | parents | {s["stop_id"] for s in all_stops if s.get("parent_station") in parents}
    keep |= {s["stop_id"] for s in all_stops if any(p in s["stop_name"] for p in EXTRA_NAME_PATTERNS)}
    keep |= set(sorted(s["stop_id"] for s in all_stops if "GO Station" in s["stop_name"])[:GO_LIMIT])
    stops = [s for s in all_stops if s["stop_id"] in keep]
    pathways_all = rows(z, "pathways.txt")
    pathways = [p for p in pathways_all if p["from_stop_id"] in keep and p["to_stop_id"] in keep]
    level_ids = {s.get("level_id") for s in stops if s.get("level_id")}
    levels_all = rows(z, "levels.txt")
    levels = [lv for lv in levels_all if lv["level_id"] in level_ids]
    shape_ids = {t.get("shape_id") for t in trips if t.get("shape_id")}
    with z.open("shapes.txt") as f:
        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
        fields_sh = reader.fieldnames
        shapes = [r for r in reader if r["shape_id"] in shape_ids]
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_dir / "gtfs-mini.zip", "w", zipfile.ZIP_DEFLATED) as zout:
        for name in ("feed_info.txt", "agency.txt", "calendar.txt", "calendar_dates.txt"):
            zout.writestr(name, z.read(name))
        write(zout, "routes.txt", list(routes[0].keys()), routes)
        write(zout, "trips.txt", list(trips[0].keys()), trips)
        write(zout, "stops.txt", list(all_stops[0].keys()), stops)
        write(zout, "stop_times.txt", fields_st, stop_times)
        write(zout, "pathways.txt", list(pathways_all[0].keys()), pathways)
        write(zout, "levels.txt", list(levels_all[0].keys()), levels)
        write(zout, "shapes.txt", fields_sh, shapes)
    codes = {s["stop_code"] for s in stops}
    zs = zipfile.ZipFile(surface)
    srows = [s for s in rows(zs, "stops.txt") if s["stop_code"] in codes]
    with zipfile.ZipFile(out_dir / "surface-mini.zip", "w", zipfile.ZIP_DEFLATED) as zout:
        write(zout, "stops.txt", list(srows[0].keys()), srows)
        zout.writestr("feed_info.txt", zs.read("feed_info.txt"))
    print(f"routes {len(routes)} trips {len(trips)} stop_times {len(stop_times)} stops {len(stops)} "
          f"pathways {len(pathways)} levels {len(levels)} shapes {len(shape_ids)} ({len(shapes)} points) surface stops {len(srows)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) > 3 else Path("tests/fixtures"))
