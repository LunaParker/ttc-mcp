import dataclasses

import pytest

from tests.unit.test_disruptions import NOW_DAY, NOW_NIGHT, alerts, build, groups, rt
from ttc_mcp.adapters.ttc_alerts import AlertList
from ttc_mcp.domain import planner
from ttc_mcp.domain.disruptions import Disruptions


def finch_union(conn, now):
    return planner.search(conn, planner.resolve_endpoint(conn, "Finch"), planner.resolve_endpoint(conn, "Union"), now=now, max_transfers=0)


def line5(conn):
    """Cedarvale to Don Valley on pattern 51, through Eglinton's eastbound platform 16073."""
    return planner.search(conn, planner.resolve_endpoint(conn, "16069"), planner.resolve_endpoint(conn, "16077"), now=NOW_NIGHT, max_transfers=0)


def closure_77519(conn, *, rt_feed=True, **changes):
    closure = next(a for a in alerts().alerts if a.id == "77519")
    return Disruptions.build(conn, AlertList(None, None, [dataclasses.replace(closure, **changes)]), rt() if rt_feed else None, groups(conn), NOW_NIGHT)


def king_options(conn, disr):
    """504 King options from beside the 9001 detour's skipped stops, never boarding at one."""
    origin = planner.resolve_endpoint(conn, {"lat": 43.650939, "lon": -79.450927})
    return origin, planner.search(conn, origin, planner.resolve_endpoint(conn, "4582"), now=NOW_DAY, max_transfers=0,
                                  usable=lambda s: disr.stop_skipped(s) is None)


def trip(conn, origin, dest):
    return planner.search(conn, planner.resolve_endpoint(conn, origin), planner.resolve_endpoint(conn, dest), now=NOW_NIGHT, max_transfers=0)


def ride_route(opt):
    return next(l["route"] for l in opt["legs"] if l["mode"] == "ride")


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
    assert over[0]["stops"] == legs[0]["stops"] + legs[2]["stops"]


def test_day_closure_not_active_leaves_the_ride_whole_but_notes_the_rsz(conn):
    over = planner.overlay(conn, build(conn, NOW_DAY), finch_union(conn, NOW_DAY))
    assert [l["mode"] for l in over[0]["legs"] if l["mode"] != "walk"] == ["ride"]
    assert {d["alert_id"] for d in over[0]["disruptions"]} == {"77535"}


def test_closure_without_shuttle_drops_the_option(conn):
    closure = next(a for a in alerts().alerts if a.id == "77519")
    no_shuttle = AlertList(None, None, [dataclasses.replace(closure, shuttle="Will Not Operate")])
    disr = Disruptions.build(conn, no_shuttle, rt(), groups(conn), NOW_NIGHT)
    assert planner.overlay(conn, disr, finch_union(conn, NOW_NIGHT)) == []


@pytest.mark.parametrize("shuttle", ["Running", " operating "])
def test_a_running_shuttle_splits_the_ride_like_will_operate(conn, shuttle):
    expected = planner.overlay(conn, closure_77519(conn, shuttle="Will Operate"), finch_union(conn, NOW_NIGHT))
    over = planner.overlay(conn, closure_77519(conn, shuttle=shuttle), finch_union(conn, NOW_NIGHT))
    assert [l["mode"] for l in over[0]["legs"] if l["mode"] != "walk"] == ["ride", "shuttle", "ride"] and over[0]["legs"] == expected[0]["legs"]


@pytest.mark.parametrize("shuttle", ["Will Not Operate", "Not Running", None])
def test_a_shuttle_that_does_not_run_drops_the_option(conn, shuttle):
    assert planner.overlay(conn, closure_77519(conn, shuttle=shuttle), finch_union(conn, NOW_NIGHT)) == []


@pytest.mark.parametrize("start,end", [(None, None), ("York Mills", None), (None, "Eglinton")])
def test_a_missing_span_end_is_named_from_the_shuttle_stations(conn, start, end):
    disr = closure_77519(conn, stop_start=start, stop_end=end)
    sh = next(l for l in planner.overlay(conn, disr, finch_union(conn, NOW_NIGHT))[0]["legs"] if l["mode"] == "shuttle")
    assert "York Mills to Eglinton" in sh["note"]


def test_an_lrt_shuttle_note_says_lrt_not_subway(conn):
    disr = closure_77519(conn, rt_feed=False, routes=("5",), stop_ids=("16775", "16776"), stop_start="Chaplin", stop_end="Avenue")
    sh = next(l for l in planner.overlay(conn, disr, line5(conn))[0]["legs"] if l["mode"] == "shuttle")
    assert (sh["line"], sh["from"]["name"], sh["to"]["name"]) == ("5", "Chaplin", "Avenue")
    assert sh["note"].startswith("LRT suspended Chaplin to Avenue") and "subway" not in sh["note"].lower()


@pytest.mark.parametrize("shuttle", ["Will Operate", "Will Not Operate"])
def test_a_one_station_closure_inside_the_leg_is_bypassed(conn, shuttle):
    opts = finch_union(conn, NOW_NIGHT)
    over = planner.overlay(conn, closure_77519(conn, rt_feed=False, stop_ids=("2811",), shuttle=shuttle), opts)
    assert over[0]["legs"] == opts[0]["legs"] and "77519" in {d.get("alert_id") for d in over[0]["disruptions"]}


def test_the_line_5_ride_through_eglinton_ignores_the_line_1_listing_of_its_platform(conn):
    opts = line5(conn)
    ride = next(l for l in opts[0]["legs"] if l["mode"] == "ride")
    assert (ride["route"], ride["pattern_id"], ride["board_pos"], ride["alight_pos"]) == ("5", 51, 5, 14)
    over = planner.overlay(conn, build(conn, NOW_NIGHT), opts)
    assert over[0]["legs"] == opts[0]["legs"] and "77538" not in {d.get("alert_id") for d in over[0]["disruptions"]}


def test_a_line_5_ride_boarding_at_the_line_1_listed_platform_is_kept_whole(conn):
    opts = trip(conn, "16073", "16077")
    ride = next(l for l in opts[0]["legs"] if l["mode"] == "ride")
    assert (ride["route"], ride["board"]["number"]) == ("5", "16073")
    over = planner.overlay(conn, build(conn, NOW_NIGHT), opts)
    assert over and over[0]["legs"] == opts[0]["legs"] and "77538" not in {d.get("alert_id") for d in over[0]["disruptions"]}


def test_a_line_1_listing_still_closes_line_1_platforms(conn):
    """With the closure on York Mills only, Lawrence is closed by the RT entity alone (routes include "1")."""
    legs = [l for l in planner.overlay(conn, closure_77519(conn, stop_ids=("7462",)), finch_union(conn, NOW_NIGHT))[0]["legs"] if l["mode"] != "walk"]
    assert [l["mode"] for l in legs] == ["ride", "shuttle", "ride"] and (legs[1]["from"]["name"], legs[1]["to"]["name"]) == ("York Mills", "Lawrence")


@pytest.mark.parametrize("origin,dest", [("Finch", "Lawrence"), ("Lawrence", "Union")])
def test_a_one_station_closure_at_the_board_or_alight_stop_drops_the_option(conn, origin, dest):
    opts = planner.search(conn, planner.resolve_endpoint(conn, origin), planner.resolve_endpoint(conn, dest), now=NOW_NIGHT, max_transfers=0)
    assert opts and planner.overlay(conn, closure_77519(conn, rt_feed=False, stop_ids=("2811",)), opts) == []


@pytest.mark.parametrize("origin,dest", [("Finch", "York Mills"), ("Eglinton", "Union"), ("York Mills", "Finch"), ("Union", "Eglinton")])
def test_a_ride_to_or_from_an_end_station_of_the_closure_is_kept_whole(conn, origin, dest):
    opts = trip(conn, origin, dest)
    assert opts and ride_route(opts[0]) == "1"
    over = planner.overlay(conn, build(conn, NOW_NIGHT), opts)
    assert over and over[0]["legs"] == opts[0]["legs"] and "77519" in {d.get("alert_id") for d in over[0]["disruptions"]}


@pytest.mark.parametrize("origin,dest,modes", [("York Mills", "Union", ["shuttle", "ride"]), ("Finch", "Eglinton", ["ride", "shuttle"]),
                                               ("Finch", "Lawrence", ["ride", "shuttle"])])
def test_a_ride_into_the_closed_block_is_still_split(conn, origin, dest, modes):
    over = planner.overlay(conn, build(conn, NOW_NIGHT), trip(conn, origin, dest))
    assert [l["mode"] for l in over[0]["legs"] if l["mode"] != "walk"] == modes


def test_a_two_station_closure_with_no_interior_still_gets_a_shuttle(conn):
    disr = closure_77519(conn, rt_feed=False, stop_ids=("7462", "2811"))
    legs = [l for l in planner.overlay(conn, disr, finch_union(conn, NOW_NIGHT))[0]["legs"] if l["mode"] != "walk"]
    assert [l["mode"] for l in legs] == ["ride", "shuttle", "ride"]
    assert legs[0]["alight"]["name"].startswith("York Mills") and legs[2]["board"]["name"].startswith("Lawrence")
    assert (legs[1]["from"]["name"], legs[1]["to"]["name"]) == ("York Mills", "Lawrence")


def test_ranks_are_renumbered_after_a_reorder_and_a_drop(conn):
    opts = finch_union(conn, NOW_NIGHT) + line5(conn)
    for i, o in enumerate(opts, 1):
        o["rank"] = i
    assert [(o["rank"], ride_route(o)) for o in planner.overlay(conn, build(conn, NOW_NIGHT), opts)] == [(1, "5"), (2, "1")]
    assert [(o["rank"], ride_route(o)) for o in planner.overlay(conn, closure_77519(conn, shuttle="Will Not Operate"), opts)] == [(1, "5")]


def test_detoured_stops_are_never_used_and_rides_through_them_are_annotated(conn):
    disr = build(conn, NOW_DAY)
    origin, opts = king_options(conn, disr)
    assert "15039" in origin.stops
    ride = next(l for l in opts[0]["legs"] if l["mode"] == "ride")
    assert ride["route"] == "504" and ride["board"]["number"] not in {"15039", "14994", "14993"}
    over = planner.overlay(conn, disr, opts)
    assert "9001" in {d.get("detour_id") for d in over[0]["disruptions"]} and over[0]["legs"] == opts[0]["legs"]


@pytest.mark.parametrize("routes,annotated", [(["505"], False), ([], True)])
def test_surface_annotation_needs_the_ride_route_or_no_route(conn, routes, annotated):
    disr = build(conn, NOW_DAY)
    _, opts = king_options(conn, disr)
    detour = next(el for el in disr.elements if el.get("detour_id") == "9001")
    over = planner.overlay(conn, Disruptions(conn, [detour | {"routes": routes}], {}), opts)
    assert ("9001" in {d.get("detour_id") for d in over[0]["disruptions"]}) is annotated
