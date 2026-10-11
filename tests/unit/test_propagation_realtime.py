import datetime as dt

import pytest
from fastmcp import Client

from tests.mockttc import make_app
from ttc_mcp.static.schedule import TZ

NIGHT = dt.datetime(2026, 10, 6, 23, 30, tzinfo=TZ)


@pytest.mark.anyio
async def test_arrivals_flag_detoured_vehicles_and_list_disruptions(app_env):
    calls = []
    async with Client(make_app(calls)) as c:
        r = (await c.call_tool("arrivals", {"stop": "15461"})).data
        flags = {a["vehicle_id"]: a["detour_id"] for a in r["arrivals"]}
        assert flags["4617"] == "9001" and flags["4517"] is None
        assert [d["detour_id"] for d in r["disruptions"]] == ["9001"] and r["meta"]["advisories"]["staleness"] == "fresh"
        assert len([u for u in calls if "/gtfsrt/trips" in u]) == 1
        sched = (await c.call_tool("arrivals", {"stop": "15039", "limit": 2})).data
        assert all(a["basis"] == "schedule" and "detour_id" in a for a in sched["arrivals"])
        assert [d["detour_id"] for d in sched["disruptions"]] == ["9001"]
        plain = (await c.call_tool("arrivals", {"stop": "15460", "limit": 1})).data
        assert plain["arrivals"][0]["detour_id"] is None and "disruptions" in plain
    assert len([u for u in calls if "/gtfsrt/trips" in u]) == 1


@pytest.mark.anyio
async def test_next_trains_report_suspended_platforms_inside_a_closure(app_env, monkeypatch):
    async with Client(make_app([])) as c:
        day = (await c.call_tool("next_trains", {"query": "York Mills"})).data
        assert {p["platform"]: p["service"] for p in day["platforms"]} == {"13791": "normal", "13792": "no_prediction"}
        assert {d["alert_id"] for d in day["disruptions"]} == {"77519", "77535"} and not any("disruption" in p for p in day["platforms"])
    monkeypatch.setattr("ttc_mcp.runtime.now", lambda: NIGHT)
    async with Client(make_app([])) as c:
        night = (await c.call_tool("next_trains", {"query": "York Mills"})).data
        by = {p["platform"]: p for p in night["platforms"]}
        assert (by["13791"]["service"], by["13791"]["next_min"], by["13791"]["basis"]) == ("normal", [3, 9], "realtime") and "disruption" not in by["13791"]
        assert by["13792"]["service"] == "suspended" and (by["13792"]["disruption"]["alert_id"], by["13792"]["disruption"]["role"]) == ("77519", "shuttle_terminus")
        assert next(d for d in night["disruptions"] if d["alert_id"] == "77519")["role"] == "shuttle_terminus"
        assert night["meta"]["advisories"]["cache"] == "live" and night["meta"]["source"].startswith("ntas")
        bloor = (await c.call_tool("next_trains", {"query": "13863"})).data
        assert bloor["platforms"][0]["service"] == "normal" and bloor["disruptions"] == []
        lawrence = (await c.call_tool("next_trains", {"query": "Lawrence"})).data
        assert all(p["service"] == "suspended" and p["disruption"]["alert_id"] == "77519" and "role" not in p["disruption"] for p in lawrence["platforms"])


@pytest.mark.anyio
async def test_suspended_platforms_report_no_minutes_live_or_timetable(app_env, monkeypatch):
    """NTAS keeps publishing predictions inside a closure (spec 8 item 7): they stay visible as ntas_min, never as next_min."""
    monkeypatch.setattr("ttc_mcp.runtime.now", lambda: NIGHT)
    async with Client(make_app([])) as c:
        lawrence = (await c.call_tool("next_trains", {"query": "Lawrence"})).data
        by = {p["platform"]: p for p in lawrence["platforms"]}
        sb, nb = by["13793"], by["13794"]
        # Spec 4.4's suspended platform: next_min [], service, disruption and no basis; the timetable is never consulted for it.
        assert (sb["service"], sb["next_min"], sb["ntas_min"], sb["source"]) == ("suspended", [], [1, 4, 8], "ntas") and "basis" not in sb
        assert sb["note"] == "NTAS keeps publishing predictions inside a closure; this platform is suspended" and "scheduled_next_min" not in sb
        assert (nb["service"], nb["next_min"], nb["ntas_min"], nb["source"]) == ("suspended", [], [], "ntas") and "basis" not in nb
        assert nb["note"] == sb["note"] and "scheduled_next_min" not in nb and nb["disruption"]["alert_id"] == "77519"
        assert lawrence["meta"]["source"] == "ntas"
        ym = (await c.call_tool("next_trains", {"query": "York Mills"})).data
        assert {p["platform"]: p["service"] for p in ym["platforms"]} == {"13791": "normal", "13792": "suspended"} and ym["meta"]["source"] == "ntas"
        arr = (await c.call_tool("arrivals", {"stop": "13793"})).data
        assert arr["arrivals"] == [] and arr["note"] == sb["note"] and "77519" in {d["alert_id"] for d in arr["disruptions"]}
        assert arr["meta"]["source"] == "ntas" and arr["meta"]["cache"] == "ttl"
        served = (await c.call_tool("arrivals", {"stop": "13791"})).data
        assert [a["eta_min"] for a in served["arrivals"]] == [3, 9] and "note" not in served
