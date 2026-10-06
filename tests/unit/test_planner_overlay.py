import dataclasses
import datetime as dt

from tests.unit.test_disruptions import NOW_DAY, NOW_NIGHT, alerts, build, groups, rt
from ttc_mcp.adapters.ttc_alerts import AlertList
from ttc_mcp.domain import planner
from ttc_mcp.domain.disruptions import Disruptions


def finch_union(conn, now):
    return planner.search(conn, planner.resolve_endpoint(conn, "Finch"), planner.resolve_endpoint(conn, "Union"), now=now, max_transfers=0)


def test_night_closure_splits_the_line_1_ride_into_ride_shuttle_ride(conn):
    opts = finch_union(conn, NOW_NIGHT)
    assert opts and [l["mode"] for l in opts[0]["legs"] if l["mode"] != "walk"] == ["ride"]
    over = planner.overlay(conn, build(conn, NOW_NIGHT), opts)
    legs = [l for l in over[0]["legs"] if l["mode"] != "walk"]
    assert [l["mode"] for l in legs] == ["ride", "shuttle", "ride"]
    assert legs[0]["alight"]["name"].startswith("York Mills") and legs[2]["board"]["name"].startswith("Eglinton")
    sh = legs[1]
    assert (sh["line"], sh["from"]["name"], sh["to"]["name"], sh["typical_min"], sh["alert_id"]) == ("1", "York Mills", "Eglinton", None, "77519")
    assert "shuttle" in sh["note"].lower() and {d["alert_id"] for d in over[0]["disruptions"]} >= {"77519", "77535"}
    assert over[0]["typical_min"] == sum(l["min"] if "min" in l else l["typical_min"] for l in over[0]["legs"] if l["mode"] != "shuttle")
    assert over[0]["rank"] == 1 and legs[0]["stops"] + legs[2]["stops"] < sum(l["stops"] for l in opts[0]["legs"] if l["mode"] == "ride")


def test_day_closure_not_active_leaves_the_ride_whole_but_notes_the_rsz(conn):
    over = planner.overlay(conn, build(conn, NOW_DAY), finch_union(conn, NOW_DAY))
    assert [l["mode"] for l in over[0]["legs"] if l["mode"] != "walk"] == ["ride"]
    assert {d["alert_id"] for d in over[0]["disruptions"]} == {"77535"}


def test_closure_without_shuttle_drops_the_option(conn):
    closure = next(a for a in alerts().alerts if a.id == "77519")
    no_shuttle = AlertList(None, None, [dataclasses.replace(closure, shuttle="Will Not Operate")])
    disr = Disruptions.build(conn, no_shuttle, rt(), groups(conn), NOW_NIGHT)
    assert planner.overlay(conn, disr, finch_union(conn, NOW_NIGHT)) == []


def test_closure_without_a_span_names_the_shuttle_stations(conn):
    closure = next(a for a in alerts().alerts if a.id == "77519")
    no_span = AlertList(None, None, [dataclasses.replace(closure, stop_start=None, stop_end=None)])
    disr = Disruptions.build(conn, no_span, rt(), groups(conn), NOW_NIGHT)
    sh = next(l for l in planner.overlay(conn, disr, finch_union(conn, NOW_NIGHT))[0]["legs"] if l["mode"] == "shuttle")
    assert "York Mills to Eglinton" in sh["note"]

def test_detoured_stops_are_never_used_and_rides_through_them_are_annotated(conn):
    disr = build(conn, NOW_DAY)
    origin = planner.resolve_endpoint(conn, {"lat": 43.650939, "lon": -79.450927})
    assert "15039" in origin.stops
    opts = planner.search(conn, origin, planner.resolve_endpoint(conn, "4582"), now=NOW_DAY, max_transfers=0, usable=lambda s: disr.stop_skipped(s) is None)
    ride = next(l for l in opts[0]["legs"] if l["mode"] == "ride")
    assert ride["route"] == "504" and ride["board"]["number"] not in {"15039", "14994", "14993"}
    over = planner.overlay(conn, disr, opts)
    assert "9001" in {d.get("detour_id") for d in over[0]["disruptions"]} and over[0]["legs"] == opts[0]["legs"]
