import dataclasses
import datetime as dt
from pathlib import Path

from tests.mockttc import rt_alerts_body
from ttc_mcp.adapters.detour_feed import parse_detours
from ttc_mcp.adapters.gtfsrt_alerts import parse_rt_alerts
from ttc_mcp.adapters.ttc_alerts import AlertList, parse_alerts
from ttc_mcp.domain import detours
from ttc_mcp.domain.arrivals import stop_row
from ttc_mcp.domain.disruptions import Disruptions
from ttc_mcp.domain.trains import rail_platforms
from ttc_mcp.static.schedule import TZ

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "advisories"
NOW_DAY = dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ)
NOW_NIGHT = dt.datetime(2026, 10, 6, 23, 30, tzinfo=TZ)


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
    assert [s["service"] for s in day.station_service("43276", ["1"])] == ["reduced"] and night.station_service("43276", ["1"])[0]["service"] == "suspended"
    assert day.station_service("14572", ["1"]) == [{"line": "1", "service": "normal", "disruption": None}]
    assert day.station_service("7129", ["1"])[0]["service"] == "reduced"
    ym = next(p for p in rail_platforms(conn, "43276") if p["stop_id"] == "13791")
    assert day.platform_closed_now(ym) is None and night.platform_closed_now(ym)["source"] == "gtfsrt alerts" and night.platform_closed_now(ym)["alert_id"] == "77538"
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
    assert s["reduced_speed_zones"]["count"] == 1 and s["reduced_speed_zones"]["zones"][0]["stations"] == ["York Mills", "Sheppard-Yonge"]
    assert s["elevators_out"] == {"St George": ["1Y1L"]} and s["escalators_out"] == {"Coxwell": ["11D3E"]}
