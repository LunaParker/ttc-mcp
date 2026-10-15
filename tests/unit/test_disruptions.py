import dataclasses
import datetime as dt
from pathlib import Path

from tests.mockttc import rt_alerts_body
from ttc_mcp.adapters.detour_feed import parse_detours
from ttc_mcp.adapters.gtfsrt_alerts import parse_rt_alerts
from ttc_mcp.adapters.ttc_alerts import AlertList, parse_alerts
from ttc_mcp.domain import detours
from ttc_mcp.domain.arrivals import stop_row
from ttc_mcp.domain.disruptions import Disruptions, element_key
from ttc_mcp.domain.trains import rail_platforms
from ttc_mcp.static.schedule import TZ

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "advisories"
NOW_DAY = dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ)
NOW_NIGHT = dt.datetime(2026, 10, 6, 23, 30, tzinfo=TZ)
SURFACE_KEYS = {"route", "effect", "source", "alert_id", "detour_id", "direction", "from", "to", "skipped_stops", "reason", "service_dates", "active_now", "text"}


def alerts():
    return parse_alerts((FIX / "alerts.json").read_bytes(), {}).value


def rt():
    return parse_rt_alerts(rt_alerts_body(1791222554), {}).value


def groups(conn):
    return detours.resolve_detours(conn, parse_detours((FIX / "detour.json").read_bytes(), {}).value, dt.date(2026, 10, 6), rt=rt())


def build(conn, now, alert_list=None):
    return Disruptions.build(conn, alert_list or alerts(), rt(), groups(conn), now)


def test_closure_element_resolves_legacy_station_ids_and_windows(conn):
    el = next(e for e in build(conn, NOW_DAY).elements if e.get("alert_id") == "77519")
    assert el["type"] == "closure" and el["stations"] == ["43274", "43275", "43276"] and el["station_names"] == ["Eglinton", "Lawrence", "York Mills"]
    assert el["stops"] == [] and el["route"] == "1" and el["active_now"] is False and el["shuttle"] == "Will Operate" and el["span"] == ["York Mills", "Eglinton"]
    assert el["windows"][0] == ["2026-10-05T23:00-04:00", "2026-10-06T02:30-04:00"] and el["period"][1] == "2026-10-07T23:00-04:00"
    assert next(e for e in build(conn, NOW_NIGHT).elements if e.get("alert_id") == "77519")["active_now"] is True


def test_other_elements_resolve_or_keep_raw_ids(conn):
    by = {e["alert_id"]: e for e in build(conn, NOW_DAY).elements if e["source"] == "alerts.ttc.ca"}
    assert by["77535"]["type"] == "rsz" and by["77535"]["stations"] == ["43276", "7129"] and by["77535"]["rsz"]["reduced_speed_kmh"] == "25" and by["77535"]["active_now"]
    assert by["77513"]["stations"] == ["43269"] and by["77513"]["station_names"] == ["St George"] and by["77513"]["unit_code"] == "1Y1L"
    assert by["76445"]["stations"] == [] and by["76445"]["stops"] == ["4917"] and by["76445"]["station_hint"] == "Coxwell"
    assert by["77545"]["stations"] == [] and len(by["77545"]["stops"]) == 34 and by["77545"]["active_now"] is True and by["77545"]["route_type"] == "Streetcar"
    assert by["77530"]["stops"] == [] and by["77530"]["routes"] == ["13"]
    det = [e for e in build(conn, NOW_DAY).elements if e["source"] == "detour feed"]
    assert [(e["detour_id"], e["stops"], e["active_now"]) for e in det] == [("9002", [], True), ("9001", ["15039", "14994", "14993"], True)]
    assert det[1]["text"].startswith("504 King") and det[1]["leaves_after"]["number"] == "4569" and det[0]["text"] is None


def test_station_lookups_and_service_per_line(conn):
    day, night = build(conn, NOW_DAY), build(conn, NOW_NIGHT)
    assert day.station_closed_now("43276") is None and night.station_closed_now("43276")["alert_id"] == "77519"
    assert [s["service"] for s in day.station_service("43276", ["1"])] == ["reduced"] and night.station_service("43275", ["1"])[0]["service"] == "suspended"
    assert day.station_service("14572", ["1"]) == [{"line": "1", "service": "normal", "disruption": None}]
    assert day.station_service("7129", ["1"])[0]["service"] == "reduced"
    lawrence = next(p for p in rail_platforms(conn, "43275") if p["stop_id"] == "13793")
    assert day.platform_closed_now(lawrence) is None and night.platform_closed_now(lawrence)["source"] == "alerts.ttc.ca" and night.platform_closed_now(lawrence)["alert_id"] == "77519"
    assert {e["alert_id"] for e in day.for_station("43276")} == {"77519", "77535"} and day.for_station("14572") == []


def test_stop_propagation_parent_suffix_route_and_detour(conn):
    night = build(conn, NOW_NIGHT)
    assert [e["alert_id"] for e in night.for_stop(stop_row(conn, "14806"))] == ["77519"]
    assert night.route_stop_status(stop_row(conn, "13793")) == "closed" and build(conn, NOW_DAY).route_stop_status(stop_row(conn, "13793")) == "open"
    assert night.route_stop_status(stop_row(conn, "14806")) == "open"
    assert night.stop_skipped("14994")["detour_id"] == "9001" and night.route_stop_status(stop_row(conn, "14994")) == "skipped_by_detour"
    assert [e["detour_id"] for e in night.for_stop(stop_row(conn, "14994"))] == ["9001"]
    assert [e.get("detour_id") for e in night.for_stop(stop_row(conn, "15461"))] == ["9001"]
    platform = night.for_stop(stop_row(conn, "13863"))
    assert [e["alert_id"] for e in platform] == ["77519"]


def test_suffix_rule_inherits_a_closure_of_the_named_station(conn):
    closure = next(a for a in alerts().alerts if a.id == "77519")
    king = dataclasses.replace(closure, stop_ids=("14586",), windows=(), start=None, end=None)
    d = Disruptions.build(conn, AlertList(None, None, [king]), None, [], NOW_DAY)
    assert d.suffix_station("King St West at Yonge St West Side - King Station") == "14586" and d.suffix_station("Cherry St at Front St East") is None
    assert [e["alert_id"] for e in d.for_stop(stop_row(conn, "15637"))] == ["77519"] and d.station_closed_now("14586") is not None


def test_service_status_summary(conn):
    s = build(conn, NOW_DAY).service_status(alerts(), groups(conn))
    assert s["counts"] == {"closures": 1, "closures_active_now": 0, "surface": 4, "rsz": 1, "elevators_out": 1, "escalators_out": 1}
    assert s["closures"][0]["alert_id"] == "77519" and s["as_of"] == "2026-10-05T13:42-04:00" and s["rsz_as_of"] == "2026-10-05T11:20-04:00"
    assert {x["route"] for x in s["surface"]} == {"512", "13", "504", "77"}
    d504 = next(x for x in s["surface"] if x.get("detour_id") == "9001")
    assert (d504["effect"], d504["skipped_stops"], d504["reason"], d504["active_now"]) == ("Detour", 3, "504 King – Temporary route change due to track work", True)
    assert all(set(x) == SURFACE_KEYS for x in s["surface"]), [set(x) for x in s["surface"]]
    def name(n):
        return conn.execute("SELECT name FROM stops WHERE stop_id=?", (n,)).fetchone()[0]

    assert (d504["source"], d504["alert_id"], d504["direction"], d504["from"], d504["to"], d504["text"]) == \
        ("detour feed", None, "East - 504A King towards Distillery", name("4569"), name("4573"), "504 King streetcars will divert via Queen St.")
    assert d504["service_dates"] == ["20261005", "20261007"]
    d77 = next(x for x in s["surface"] if x.get("detour_id") == "9002")
    assert (d77["direction"], d77["from"], d77["to"], d77["skipped_stops"], d77["reason"], d77["text"]) == (None, None, None, 0, None, None)
    s512 = next(x for x in s["surface"] if x["alert_id"] == "77545")
    assert (s512["effect"], s512["source"], s512["detour_id"], s512["direction"], s512["skipped_stops"], s512["reason"], s512["active_now"]) == \
        ("No Service", "alerts.ttc.ca", None, "Both ways", 34, "Collision", True)
    assert s512["service_dates"] is None
    assert (s512["from"], s512["to"]) == ("Gunns Loop at St Clair Ave West", "Lansdowne Ave at St. Clair Ave West South Side") and s512["text"].startswith("512 St Clair: No service")
    a13 = next(x for x in s["surface"] if x["alert_id"] == "77530")
    assert (a13["effect"], a13["direction"], a13["from"], a13["skipped_stops"], a13["reason"]) == ("Detour", "Southbound", "University Ave at Gerrard St West", 0, "Police activity")
    assert s["reduced_speed_zones"]["count"] == 1 and s["reduced_speed_zones"]["zones"][0]["stations"] == ["York Mills", "Sheppard-Yonge"]
    assert s["elevators_out"] == {"St George": ["1Y1L"]} and s["escalators_out"] == {"Coxwell": ["11D3E"]}


def test_station_closure_only_closes_platforms_of_the_closed_line(conn):
    night = build(conn, NOW_NIGHT)
    assert night.route_stop_status(stop_row(conn, "13796")) == "closed" and night.route_stop_status(stop_row(conn, "13795")) == "open"
    # The Line 1 RT entity 77538 lists Line 5's 16073 too; another line's listing leaves the platform open.
    assert night.route_stop_status(stop_row(conn, "16073")) == "open"
    assert night.route_stop_status(stop_row(conn, "16074")) == "open"
    assert night.station_closed_now("43274", "5") is None and night.station_closed_now("43274", "1")["alert_id"] == "77519"


def _rt_with(alerts_):
    return dataclasses.replace(rt(), alerts=alerts_)


def test_an_rt_listing_naming_no_routes_closes_the_platform_for_every_line(conn):
    feed = rt()
    unlabelled = _rt_with([dataclasses.replace(a, routes=()) if a.id == "77538" else a for a in feed.alerts])
    night = Disruptions.build(conn, alerts(), unlabelled, groups(conn), NOW_NIGHT)
    assert night.route_stop_status(stop_row(conn, "16073")) == "closed" and night.platform_closed_now(stop_row(conn, "16073"))["alert_id"] == "77538"


def test_another_lines_entity_listed_first_does_not_hide_the_platforms_own_closure(conn):
    feed = rt()
    real = next(a for a in feed.alerts if a.id == "77538")
    first = _rt_with([dataclasses.replace(real, id="77538-line2", routes=("2",))] + feed.alerts)
    night = Disruptions.build(conn, AlertList(None, None, []), first, [], NOW_NIGHT)
    lawrence_sb = stop_row(conn, "13793")
    assert night.route_stop_status(lawrence_sb) == "closed" and night.platform_closed_now(lawrence_sb)["alert_id"] == "77538"
    only_line2 = _rt_with([dataclasses.replace(real, routes=("2",))])
    assert Disruptions.build(conn, AlertList(None, None, []), only_line2, [], NOW_NIGHT).route_stop_status(lawrence_sb) == "open"


def test_element_key_tolerates_missing_keys():
    assert element_key({"source": "detour feed", "detour_id": "9001"}) == ("detour", "9001")
    assert element_key({"source": "gtfsrt alerts", "alert_id": "77538"}) == ("alert", "77538") and element_key({}) == ("alert", None)


def closure_copy(conn, *, rt_feed=True, **changes):
    closure = next(a for a in alerts().alerts if a.id == "77519")
    return Disruptions.build(conn, AlertList(None, None, [dataclasses.replace(closure, **changes)]), rt() if rt_feed else None, groups(conn), NOW_NIGHT)


def test_an_end_station_of_a_closure_is_served_on_the_open_side(conn):
    """Span rule: York Mills northbound and Eglinton southbound face the open line; Lawrence and the closed-side platforms do not."""
    night = build(conn, NOW_NIGHT)
    status = {p: night.route_stop_status(stop_row(conn, p)) for p in ("13791", "13792", "13793", "13794", "13795", "13796")}
    assert status == {"13791": "open", "13792": "closed", "13793": "closed", "13794": "closed", "13795": "open", "13796": "closed"}
    assert night.platform_closed_now(stop_row(conn, "13791")) is None
    closed = night.platform_closed_now(stop_row(conn, "13792"))
    assert closed["alert_id"] == "77519" and closed["role"] == "shuttle_terminus"
    assert "role" not in night.platform_closed_now(stop_row(conn, "13793"))
    assert "role" not in next(e for e in night.elements if e.get("alert_id") == "77519")


def test_a_served_end_station_is_reduced_with_the_shuttle_and_carries_the_terminus_role(conn):
    night = build(conn, NOW_NIGHT)
    ym = night.station_service("43276", ["1"])[0]
    assert (ym["service"], ym["disruption"]["alert_id"], ym["disruption"]["shuttle"], ym["disruption"]["role"]) == ("reduced", "77519", "Will Operate", "shuttle_terminus")
    assert [s["service"] for s in night.station_service("43274", ["1", "5"])] == ["reduced", "normal"]
    lawrence = night.station_service("43275", ["1"])[0]
    assert lawrence["service"] == "suspended" and "role" not in lawrence["disruption"]
    assert next(e for e in night.for_station("43276") if e["alert_id"] == "77519")["role"] == "shuttle_terminus"
    assert "role" not in next(e for e in night.for_station("43275") if e["alert_id"] == "77519")
    assert next(e for e in night.for_stop(stop_row(conn, "13791")) if e["alert_id"] == "77519")["role"] == "shuttle_terminus"
    assert next(e for e in night.for_stop(stop_row(conn, "14806")) if e["alert_id"] == "77519")["role"] == "shuttle_terminus"
    assert "role" not in next(e for e in night.for_stop(stop_row(conn, "13794")) if e["alert_id"] == "77519")


def test_a_terminal_end_of_a_closure_is_suspended_on_both_platforms(conn):
    """Finch to North York Centre closed: nothing lies beyond Finch, so both its platforms are suspended; NYC southbound is served."""
    disr = closure_copy(conn, stop_ids=("7758", "14591"), station_names=("Finch", "North York Centre"), stop_start="Finch", stop_end="North York Centre")
    assert disr.station_closed_now("43277", "1")["stations"] == ["43277", "14591"]
    assert {p: disr.route_stop_status(stop_row(conn, p)) for p in ("14111", "14944", "13789", "13790")} == {"14111": "closed", "14944": "closed", "13789": "open", "13790": "closed"}
    assert disr.station_service("43277", ["1"])[0]["service"] == "suspended" and disr.station_service("14591", ["1"])[0]["service"] == "reduced"


def test_a_one_station_closure_suspends_both_platforms_as_the_planner_bypasses_it(conn):
    disr = closure_copy(conn, rt_feed=False, stop_ids=("2811",), station_names=("Lawrence",))
    assert disr.route_stop_status(stop_row(conn, "13793")) == "closed" and disr.route_stop_status(stop_row(conn, "13794")) == "closed"
    assert disr.station_service("43275", ["1"])[0]["service"] == "suspended" and disr.route_stop_status(stop_row(conn, "13792")) == "open"


def test_a_stop_skipped_only_by_an_entity_on_a_variant_pattern_is_not_skipped(conn):
    d = build(conn, NOW_DAY)
    assert d.stop_skipped("14994")["detour_id"] == "9001" and d.stop_skipped("14994", {"detour_0_9001:0_0_6_504_Distillery"}) is None
    assert d.stop_skipped("14994", {"detour_1_9002:1_1_77_77_Nowhere"})["detour_id"] == "9001"
