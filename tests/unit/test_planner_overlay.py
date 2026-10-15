import dataclasses

import pytest

from tests.unit.test_disruptions import NOW_DAY, alerts, build, groups, rt
from tests.unit.test_raptor import query
from ttc_mcp.adapters.ttc_alerts import AlertList
from ttc_mcp.domain import planner, raptor
from ttc_mcp.domain.disruptions import Disruptions

CHERRY_FRONT = {"lat": 43.6527, "lon": -79.3581}


@pytest.fixture(scope="module")
def tt(conn):
    return raptor.Timetable.load(conn, "S1000538")


def options(conn, tt, origin, dest, *, window_s=0, **kw):
    """Contract options at NOW_DAY as the tool hands them to annotate, before any disruption is attached."""
    q = query(conn, tt, origin, dest, NOW_DAY, **kw)
    return [planner.materialize(conn, q, j, NOW_DAY.date()) for j in raptor.plan_window(q, window_s)]


def ids(opt):
    return {d.get("alert_id") or d.get("detour_id"): d["affects"] for d in opt["disruptions"]}


def king_options(conn, tt, disr):
    """504 King options from beside the 9001 detour's skipped stops, never boarding at one."""
    origin = {"lat": 43.650939, "lon": -79.450927}
    assert "15039" in planner.resolve_endpoint(conn, origin).stops
    return options(conn, tt, origin, "4582", max_transfers=0, usable=lambda s: disr.stop_skipped(s) is None)


def test_rsz_annotation_respects_the_zone_direction(conn, tt):
    """Platform names carry the direction word (Line 1 headsigns do not): a southbound ride lists southbound and two-way zones only."""
    rsz = next(a for a in alerts().alerts if a.id == "77535")
    zones = [dataclasses.replace(rsz, id=i, direction=d) for i, d in
             (("90001", "Southbound From Finch"), ("90002", "Northbound To Finch"), ("90003", "Both ways"), ("90004", None))]
    disr = Disruptions.build(conn, AlertList(None, None, zones), None, [], NOW_DAY)
    south = planner.annotate(conn, disr, options(conn, tt, "Finch", "Union", max_transfers=0))
    assert set(ids(south[0])) == {"90001", "90003", "90004"}
    north = planner.annotate(conn, disr, options(conn, tt, "Union", "Finch", max_transfers=0))
    assert set(ids(north[0])) == {"90002", "90003", "90004"}


def test_rsz_direction_matches_the_terminal_before_the_platform_name(conn, tt):
    """Line 1's University side signs Vaughan-bound trains Northbound: "Southbound From Finch" still means the leg away from Finch.
    The fixture has no Spadina station (its platforms carry no parent), so the zone resolves to St George, which pattern 54 passes."""
    rsz = next(a for a in alerts().alerts if a.id == "77535")
    zones = [dataclasses.replace(rsz, id=i, direction=d, stop_ids=("1581", "13853"), station_names=("St George", "Spadina"),
                                 stop_start="St George", stop_end="Spadina") for i, d in (("90011", "Southbound From Finch"), ("90012", "Northbound To Finch"))]
    disr = Disruptions.build(conn, AlertList(None, None, zones), None, [], NOW_DAY)
    assert [el["stations"] for el in disr.elements] == [["43269"], ["43269"]]
    south = options(conn, tt, "Finch", "Dupont", max_transfers=0)
    ride = next(l for l in south[0]["legs"] if l["mode"] == "ride")
    assert (ride["pattern_id"], ride["headsign"]) == (54, "Yonge-University Line towards Vaughan Metropolitan Centre Station")
    assert set(ids(planner.annotate(conn, disr, south)[0])) == {"90011"}
    north = options(conn, tt, "Dupont", "Finch", max_transfers=0)
    assert set(ids(planner.annotate(conn, disr, north)[0])) == {"90012"}


def test_disruptions_name_the_legs_they_touch(conn, tt):
    """The zone touches the ride that passes through it, on a copy: the snapshot's elements are never mutated."""
    disr = build(conn, NOW_DAY)
    out = planner.annotate(conn, disr, options(conn, tt, "Finch", "Union", max_transfers=0))
    assert [l["mode"] for l in out[0]["legs"]] == ["ride"] and ids(out[0]) == {"77535": [1]}
    assert not any("affects" in el for el in disr.elements)


@pytest.mark.parametrize("kind", ["delay", "other"])
def test_route_level_alerts_annotate_every_ride_leg_of_their_route_once(conn, tt, kind):
    """A Line 1 delay (its own kind, or other) and a stopless 121 detour name no stations or stops; RSZ and unit alerts keep their own rules."""
    base = [a for a in alerts().alerts if a.id != "77530"]
    detour = next(a for a in alerts().alerts if a.id == "77530")
    delay = dataclasses.replace(detour, id="90001", kind=kind, routes=("1",), header="Line 1: Delays of up to 10 minutes")
    later = dataclasses.replace(delay, id="90002", windows=(), start=NOW_DAY.replace(hour=20), end=None)
    disr = Disruptions.build(conn, AlertList(None, None, base + [delay, later, dataclasses.replace(detour, routes=("121",))]), rt(), groups(conn), NOW_DAY)
    over = planner.annotate(conn, disr, options(conn, tt, CHERRY_FRONT, "Union", window_s=1800))
    found = {tuple(l["route"] for l in o["legs"] if l["mode"] == "ride"): [d.get("alert_id") or d.get("detour_id") for d in o["disruptions"]] for o in over}
    assert found[("121",)] == ["77530"] and found[("504", "1")].count("90001") == 1 and "90002" not in found[("504", "1")]
    assert not {"77513", "77535", "77530"} & set(found[("504", "1")])
    xfer = next(o for o in over if [l["route"] for l in o["legs"] if l["mode"] == "ride"] == ["504", "1"])
    line1 = [i for i, l in enumerate(xfer["legs"], 1) if l["mode"] == "ride" and l["route"] == "1"]
    assert ids(xfer)["90001"] == line1


def test_detoured_stops_are_never_used_and_rides_through_them_are_annotated(conn, tt):
    disr = build(conn, NOW_DAY)
    opts = king_options(conn, tt, disr)
    ride = next(l for l in opts[0]["legs"] if l["mode"] == "ride")
    assert ride["route"] == "504" and ride["board"]["number"] not in {"15039", "14994", "14993"}
    assert "9001" in ids(planner.annotate(conn, disr, opts)[0])


@pytest.mark.parametrize("routes,annotated", [(["505"], False), ([], True)])
def test_surface_annotation_needs_the_ride_route_or_no_route(conn, tt, routes, annotated):
    disr = build(conn, NOW_DAY)
    opts = king_options(conn, tt, disr)
    detour = next(el for el in disr.elements if el.get("detour_id") == "9001")
    over = planner.annotate(conn, Disruptions(conn, [detour | {"routes": routes}], {}), opts)
    assert ("9001" in ids(over[0])) is annotated
