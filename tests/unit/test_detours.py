import datetime as dt
from pathlib import Path

from tests.rtfeeds import make_alert_feed
from ttc_mcp.adapters.detour_feed import parse_detours
from ttc_mcp.adapters.gtfsrt_alerts import parse_rt_alerts
from ttc_mcp.adapters.ttcapi import parse_service_changes
from ttc_mcp.domain import detours

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "advisories"
TODAY = dt.date(2026, 10, 6)


def feed():
    return parse_detours((FIX / "detour.json").read_bytes(), {}).value


def rt():
    return parse_rt_alerts(make_alert_feed(1791222554, [
        {"id": "100", "effect": "MODIFIED_SERVICE", "routes": ["504"], "start": 1790000000, "end": 1793000000,
         "header": "504 King – Temporary route change due to track work", "description": "divert via Queen", "url": "https://www.ttc.ca/sc/504"}]), {}).value


def changes():
    return parse_service_changes((FIX / "servicechanges.json").read_bytes(), {}).value


def test_resolves_skipped_stops_and_neighbours_from_the_pattern(conn):
    r = detours.resolve_entity(conn, feed().entities[0], feed().shapes)
    assert (r["pattern"], r["route"], r["direction_id"], r["headsign"]) == (6, "504", "0", "East - 504A King towards Distillery")
    assert [s["number"] for s in r["skipped"]] == ["15039", "14994", "14993"]
    assert r["skipped"][0]["name"] == "Roncesvalles Ave at Howard Park Ave South Side" and r["skipped"][0]["lat"] == 43.650939
    assert r["leaves_after"]["number"] == "4569" and r["rejoins_at"]["number"] == "4573" and r["trips_affected"] == 3
    assert r["path_length_m"] == 4577 and r["path_polyline"].startswith("wfwiGturcN") and r["spans"] == [[3, 5, 0]]


def test_unknown_trip_is_reported_not_dropped(conn):
    r = detours.resolve_entity(conn, feed().entities[1], feed().shapes)
    assert r["pattern"] == "unknown" and r["skipped"] == [] and r["route"] == "77" and r["headsign"] is None
    assert r["leaves_after"] is None and r["path_length_m"] > 0


def test_groups_by_detour_id_with_reason_and_dates(conn):
    groups = detours.resolve_detours(conn, feed(), TODAY, rt=rt())
    assert [g["detour_id"] for g in groups] == ["9002", "9001"]
    g = groups[1]
    assert g["route"] == "504" and g["active_today"] and g["service_dates"] == ["20261005", "20261007"]
    assert g["skipped_stops"] == ["14993", "14994", "15039"] and len(g["directions"]) == 1
    assert g["reason"]["title"].startswith("504 King") and g["reason"]["source"] == "gtfsrt alerts" and g["reason"]["url"].endswith("/504")
    assert groups[0]["reason"] is None and groups[0]["directions"][0]["pattern"] == "unknown" and groups[0]["active_today"]
    assert [g["detour_id"] for g in detours.resolve_detours(conn, feed(), TODAY, route="504")] == ["9001"]
    assert not any(g["active_today"] for g in detours.resolve_detours(conn, feed(), dt.date(2026, 10, 9)))


def test_reason_falls_back_to_ttcapi_and_respects_dates():
    assert detours.reasons_for("504", TODAY, None, changes())["source"] == "ttcapi"
    assert detours.reasons_for("101", TODAY, None, changes()) is None
    assert detours.reasons_for("101", dt.date(2026, 10, 1), None, changes())["title"].startswith("101 Downsview Park")
    assert detours.reasons_for("504", dt.date(2026, 11, 20), rt(), None) is None
    assert detours.reasons_for("504", TODAY, rt(), changes())["source"] == "gtfsrt alerts"


def test_trip_detour_map():
    assert detours.trip_detour_map(feed()) == {"100677020": "9001", "100895020": "9001", "101100020": "9001", "999999999": "9002"}


def test_a_trip_held_by_the_other_loaded_feed_resolves_its_pattern_and_skipped_stops(two_feeds):
    from ttc_mcp import runtime

    active = runtime.feed_for(TODAY)
    alone = detours.resolve_entity(active.conn, feed().entities[0], feed().shapes)
    assert (active.version, alone["pattern"], alone["skipped"]) == ("S1000542", "unknown", [])
    r = detours.resolve_entity(active.conn, feed().entities[0], feed().shapes, lookup=runtime.trip_lookup(TODAY))
    assert (r["pattern"], r["headsign"], [s["number"] for s in r["skipped"]]) == (6, "East - 504A King towards Distillery", ["15039", "14994", "14993"])
    assert (r["leaves_after"]["number"], r["rejoins_at"]["number"]) == ("4569", "4573")
    g = next(g for g in detours.resolve_detours(active.conn, feed(), TODAY, lookup=runtime.trip_lookup(TODAY)) if g["detour_id"] == "9001")
    assert g["skipped_stops"] == ["14993", "14994", "15039"]
