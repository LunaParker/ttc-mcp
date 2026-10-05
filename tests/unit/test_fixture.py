import csv
import io
import zipfile
from pathlib import Path

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def rows(z, name):
    return list(csv.DictReader(io.TextIOWrapper(z.open(name), encoding="utf-8-sig")))


def test_mini_feed_has_the_routes_and_stops_the_suite_relies_on():
    z = zipfile.ZipFile(FIX / "gtfs-mini.zip")
    names = {i.filename for i in z.infolist()}
    assert {"feed_info.txt", "calendar.txt", "calendar_dates.txt", "routes.txt", "trips.txt", "stops.txt",
            "stop_times.txt", "pathways.txt", "levels.txt"} <= names
    assert {r["route_id"] for r in rows(z, "routes.txt")} == {"1", "5", "121", "300", "400", "504"}
    stops = {s["stop_id"]: s for s in rows(z, "stops.txt")}
    for sid in ("15460", "15461", "15510", "15246", "13863", "13864", "14572", "14588", "16081"):
        assert sid in stops, sid
    assert any("Dundas Station" in s["stop_name"] for s in stops.values() if s["location_type"] in ("", "0"))
    assert any("GO Station" in s["stop_name"] for s in stops.values())
    assert {t["service_id"] for t in rows(z, "trips.txt")} == {"1", "3", "4", "4401"}


def test_surface_mini_maps_public_codes_to_internal_ids():
    z = zipfile.ZipFile(FIX / "surface-mini.zip")
    by_code = {s["stop_code"]: s["stop_id"] for s in rows(z, "stops.txt")}
    assert by_code["15461"] == "4282"
