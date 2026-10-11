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
    # The shuttle's minutes are unknown, so the option's are too; the boundary counts as a transfer (spec 6.7 item 5, 4.4).
    assert over[0]["typical_min"] is None and over[0]["typical_min_note"] == "excludes the shuttle leg" and over[0]["transfers"] == 2
    assert over[0]["known_min"] == sum(l["min"] if "min" in l else l["typical_min"] for l in over[0]["legs"] if l["mode"] != "shuttle")
    assert over[0]["rank"] == 1 and legs[0]["stops"] + legs[2]["stops"] < sum(l["stops"] for l in opts[0]["legs"] if l["mode"] == "ride")
    assert over[0]["stops"] == legs[0]["stops"] + legs[2]["stops"]


def test_rsz_annotation_respects_the_zone_direction(conn):
    """Platform names carry the direction word (Line 1 headsigns do not): a southbound ride lists southbound and two-way zones only."""
    rsz = next(a for a in alerts().alerts if a.id == "77535")
    zones = [dataclasses.replace(rsz, id=i, direction=d) for i, d in
             (("90001", "Southbound From Finch"), ("90002", "Northbound To Finch"), ("90003", "Both ways"), ("90004", None))]
    disr = Disruptions.build(conn, AlertList(None, None, zones), None, [], NOW_DAY)
    south = planner.overlay(conn, disr, finch_union(conn, NOW_DAY))
    assert {d["alert_id"] for d in south[0]["disruptions"]} == {"90001", "90003", "90004"}
    north = planner.search(conn, planner.resolve_endpoint(conn, "Union"), planner.resolve_endpoint(conn, "Finch"), now=NOW_DAY, max_transfers=0)
    assert {d["alert_id"] for d in planner.overlay(conn, disr, north)[0]["disruptions"]} == {"90002", "90003", "90004"}


def test_rsz_direction_matches_the_terminal_before_the_platform_name(conn):
    """Line 1's University side signs Vaughan-bound trains Northbound: "Southbound From Finch" still means the leg away from Finch.
    The fixture has no Spadina station (its platforms carry no parent), so the zone resolves to St George, which pattern 54 passes."""
    rsz = next(a for a in alerts().alerts if a.id == "77535")
    zones = [dataclasses.replace(rsz, id=i, direction=d, stop_ids=("1581", "13853"), station_names=("St George", "Spadina"),
                                 stop_start="St George", stop_end="Spadina") for i, d in (("90011", "Southbound From Finch"), ("90012", "Northbound To Finch"))]
    disr = Disruptions.build(conn, AlertList(None, None, zones), None, [], NOW_DAY)
    assert [el["stations"] for el in disr.elements] == [["43269"], ["43269"]]
    south = planner.search(conn, planner.resolve_endpoint(conn, "Finch"), planner.resolve_endpoint(conn, "Dupont"), now=NOW_DAY, max_transfers=0)
    ride = next(l for l in south[0]["legs"] if l["mode"] == "ride")
    assert (ride["pattern_id"], ride["headsign"]) == (54, "Yonge-University Line towards Vaughan Metropolitan Centre Station")
    assert {d["alert_id"] for d in planner.overlay(conn, disr, south)[0]["disruptions"]} == {"90011"}
    north = planner.search(conn, planner.resolve_endpoint(conn, "Dupont"), planner.resolve_endpoint(conn, "Finch"), now=NOW_DAY, max_transfers=0)
    assert {d["alert_id"] for d in planner.overlay(conn, disr, north)[0]["disruptions"]} == {"90012"}


def test_day_closure_not_active_leaves_the_ride_whole_but_notes_the_rsz(conn):
    over = planner.overlay(conn, build(conn, NOW_DAY), finch_union(conn, NOW_DAY))
    assert [l["mode"] for l in over[0]["legs"] if l["mode"] != "walk"] == ["ride"]
    assert [(d["alert_id"], d["affects"]) for d in over[0]["disruptions"]] == [("77535", [1])]
    assert over[0]["typical_min"] == sum(l["min"] if "min" in l else l["typical_min"] for l in over[0]["legs"]) and "known_min" not in over[0]


def test_disruptions_name_the_legs_they_touch(conn):
    """The closure touches the ride it cut and the shuttle; the zone touches the ride that passes through it; the snapshot is untouched."""
    disr = build(conn, NOW_NIGHT)
    over = planner.overlay(conn, disr, finch_union(conn, NOW_NIGHT))
    assert [l["mode"] for l in over[0]["legs"]] == ["ride", "shuttle", "ride"]
    assert {d["alert_id"]: d["affects"] for d in over[0]["disruptions"]} == {"77519": [1, 2, 3], "77535": [1]}
    assert not any("affects" in el for el in disr.elements)
    into = planner.overlay(conn, disr, trip(conn, "Finch", "Lawrence"))[0]
    assert [l["mode"] for l in into["legs"]] == ["ride", "shuttle"] and {d["alert_id"]: d["affects"] for d in into["disruptions"]} == {"77519": [1, 2], "77535": [1]}


def test_a_shuttle_option_ranks_after_a_complete_option_at_equal_transfers(conn):
    shuttle = trip(conn, "Finch", "Lawrence")
    cherry_front = planner.resolve_endpoint(conn, {"lat": 43.6527, "lon": -79.3581})
    complete = [o for o in planner.search(conn, cherry_front, planner.resolve_endpoint(conn, "Union"), now=NOW_NIGHT, limit=10) if o["transfers"] == 1][:1]
    assert shuttle and complete
    over = planner.overlay(conn, build(conn, NOW_NIGHT), shuttle + complete)
    assert [(o["rank"], o["transfers"], o["typical_min"] is None) for o in over] == [(1, 1, False), (2, 1, True)]
    assert over[1]["known_min"] < over[0]["typical_min"] and [l["mode"] for l in over[1]["legs"]] == ["ride", "shuttle"]


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


@pytest.mark.parametrize("shuttle", ["Will Not Operate", "Not Running", None, 1, ["Running"]])
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


def test_an_rt_listing_without_routes_still_closes_the_platform_for_every_line(conn):
    feed = rt()
    unlabelled = dataclasses.replace(feed, alerts=[dataclasses.replace(a, routes=()) if a.id == "77538" else a for a in feed.alerts])
    disr = Disruptions.build(conn, alerts(), unlabelled, groups(conn), NOW_NIGHT)
    assert planner.overlay(conn, disr, trip(conn, "16073", "16077")) == []
    over = planner.overlay(conn, disr, line5(conn))
    assert over[0]["legs"] == line5(conn)[0]["legs"] and "77538" in {d.get("alert_id") for d in over[0]["disruptions"]}


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


def test_the_northbound_split_spans_the_whole_closure_although_york_mills_northbound_is_served(conn):
    legs = [l for l in planner.overlay(conn, build(conn, NOW_NIGHT), trip(conn, "Union", "Finch"))[0]["legs"] if l["mode"] != "walk"]
    assert [l["mode"] for l in legs] == ["ride", "shuttle", "ride"]
    assert (legs[1]["from"]["name"], legs[1]["to"]["name"]) == ("Eglinton", "York Mills")
    assert legs[0]["alight"]["number"] == "13796" and legs[2]["board"]["number"] == "13791"


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
    assert [(o["rank"], ride_route(o), o["transfers"]) for o in planner.overlay(conn, build(conn, NOW_NIGHT), opts)] == [(1, "5", 0), (2, "1", 2)]
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


def test_the_closure_verdict_of_a_span_on_pattern_54(conn):
    night = build(conn, NOW_NIGHT)

    def v(disr, a, b, mode="subway"):
        verdict, el = planner._closure_verdict(conn, disr, 54, "1", mode, a, b)
        return verdict, el and el.get("alert_id")
    assert v(night, 0, 16) == ("split", "77519") and v(closure_77519(conn, shuttle="Will Not Operate"), 0, 16) == ("drop", "77519")
    assert v(night, 0, 3) == ("ride", "77519") and v(night, 0, 2) == ("ride", None) and v(night, 0, 16, "bus") == ("ride", None)
    # Sheppard-Yonge (2), Lawrence (4) and Eglinton (5): extent 2..5 with York Mills (3) open and Lawrence interior.
    copy = closure_77519(conn, rt_feed=False, stop_ids=("7129", "43275", "43274"))
    assert v(copy, 3, 4) == ("drop", "77519") and v(copy, 0, 2) == ("ride", "77519") and v(copy, 0, 4) == ("split", "77519")


@pytest.mark.parametrize("kind", ["delay", "other"])
def test_route_level_alerts_annotate_every_ride_leg_of_their_route_once(conn, kind):
    """A Line 1 delay (its own kind, or other) and a stopless 121 detour name no stations or stops; RSZ and unit alerts keep their own rules."""
    base = [a for a in alerts().alerts if a.id != "77530"]
    detour = next(a for a in alerts().alerts if a.id == "77530")
    delay = dataclasses.replace(detour, id="90001", kind=kind, routes=("1",), header="Line 1: Delays of up to 10 minutes")
    later = dataclasses.replace(delay, id="90002", windows=(), start=NOW_DAY.replace(hour=20), end=None)
    disr = Disruptions.build(conn, AlertList(None, None, base + [delay, later, dataclasses.replace(detour, routes=("121",))]), rt(), groups(conn), NOW_DAY)
    cherry_front = planner.resolve_endpoint(conn, {"lat": 43.6527, "lon": -79.3581})
    over = planner.overlay(conn, disr, planner.search(conn, cherry_front, planner.resolve_endpoint(conn, "Union"), now=NOW_DAY, limit=10))
    ids = {tuple(l["route"] for l in o["legs"] if l["mode"] == "ride"): [d.get("alert_id") or d.get("detour_id") for d in o["disruptions"]] for o in over}
    assert ids[("121",)] == ["77530"] and ids[("504", "1")].count("90001") == 1 and "90002" not in ids[("504", "1")]
    assert not {"77513", "77535", "77530"} & set(ids[("504", "1")])
    xfer = next(o for o in over if [l["route"] for l in o["legs"] if l["mode"] == "ride"] == ["504", "1"])
    line1 = [i for i, l in enumerate(xfer["legs"], 1) if l["mode"] == "ride" and l["route"] == "1"]
    assert next(d for d in xfer["disruptions"] if d.get("alert_id") == "90001")["affects"] == line1


def test_a_lone_closed_stop_inside_the_extent_is_bypassed_while_an_end_station_is_served(conn):
    """Sheppard-Yonge (2), Lawrence (4) and Eglinton (5) closed, York Mills (3) open: extent 2..5 on pattern 54."""
    disr = closure_77519(conn, rt_feed=False, stop_ids=("7129", "43275", "43274"))
    into = trip(conn, "York Mills", "Lawrence")
    assert into and planner.overlay(conn, disr, into) == []
    whole = trip(conn, "Finch", "Sheppard-Yonge")
    assert planner.overlay(conn, disr, whole)[0]["legs"] == whole[0]["legs"]
