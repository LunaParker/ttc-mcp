import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest

from ttc_mcp.adapters.ttcapi import ROUTE_DETAIL, fetch_route_levels
from ttc_mcp.domain.network import list_routes
from ttc_mcp.static.build import build_sqlite

FIX = Path(__file__).resolve().parents[1] / "fixtures"
LEVELS = {"504": {"serviceLevel": {"name": "Regular service", "description": "Operates all day, every day, until 1 a.m.", "color": "Red"},
                  "is10MinutesNetwork": True, "frequency": "Frequent", "routeType": 900,
                  "mapUrl": "https://cdn.ttc.ca/-/media/Project/TTC/DevProto/Images/Home/Routes-and-Schedules/Route-Maps/504map.svg?rev=0"},
          "300": {"serviceLevel": {"name": "Blue Night Network", "description": "30-minute or better service ...", "color": "Blue"},
                  "is10MinutesNetwork": False, "frequency": "", "routeType": 700, "mapUrl": ""}}


def handler(req: httpx.Request) -> httpx.Response:
    rid = req.url.params["id"]
    if rid == "boom":
        return httpx.Response(500)
    if rid not in LEVELS:
        return httpx.Response(404)
    return httpx.Response(200, json={"route": LEVELS[rid], "routeBranchesWithStops": []})


@pytest.mark.anyio
async def test_fetch_route_levels_collects_known_routes_and_skips_failures():
    seen = []
    def h(req):
        seen.append(req.headers.get("user-agent"))
        return handler(req)
    async with httpx.AsyncClient(transport=httpx.MockTransport(h), headers={"User-Agent": "ttc-mcp/test"}) as c:
        levels = await fetch_route_levels(c, ["504", "300", "999", "boom"], concurrency=2)
    assert set(levels) == {"504", "300"} and all(ua == "ttc-mcp/test" for ua in seen) and len(seen) == 4
    assert levels["504"] == {"name": "Regular service", "description": "Operates all day, every day, until 1 a.m.", "frequency": "Frequent",
                             "ten_minute": True, "map_url": LEVELS["504"]["mapUrl"], "route_type": 900}
    assert levels["300"]["ten_minute"] is False and levels["300"]["frequency"] is None and levels["300"]["map_url"] is None
    assert ROUTE_DETAIL == "https://www.ttc.ca/ttcapi/routedetail/get"


@pytest.mark.anyio
async def test_the_route_id_travels_as_a_query_parameter():
    seen = []
    def h(req):
        seen.append((str(req.url), dict(req.url.params)))
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
        await fetch_route_levels(c, ["504", "a b&c"])
    assert sorted(seen) == [("https://www.ttc.ca/ttcapi/routedetail/get?id=504", {"id": "504"}),
                            ("https://www.ttc.ca/ttcapi/routedetail/get?id=a+b%26c", {"id": "a b&c"})]


@pytest.mark.anyio
async def test_a_malformed_level_or_any_exception_skips_only_that_route():
    def h(req):
        rid = req.url.params["id"]
        if rid == "odd":
            return httpx.Response(200, json={"route": {**LEVELS["504"], "serviceLevel": "Regular service"}})
        if rid == "raise":
            raise RuntimeError("socket closed by a proxy")
        return handler(req)
    logged = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
        levels = await fetch_route_levels(c, ["504", "odd", "raise", "300"], log=logged.append)
    assert set(levels) == {"504", "300"} and levels["504"]["name"] == "Regular service"
    assert [m for m in logged if "raise" in m] == ["[ttcapi] raise: RuntimeError: socket closed by a proxy"]


@pytest.mark.anyio
async def test_an_unanswered_request_returns_the_levels_fetched_before_the_deadline():
    async def h(req):
        if req.url.params["id"] == "300":
            await asyncio.sleep(30)
        return handler(req)
    logged = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
        levels = await asyncio.wait_for(fetch_route_levels(c, ["504", "300"], deadline=0.2, log=logged.append), 5)
    assert set(levels) == {"504"} and any("1 of 2 routes" in m for m in logged)


def test_build_writes_service_levels_into_routes(tmp_path):
    out = tmp_path / "levels.sqlite"
    levels = {"504": {"name": "Regular service", "description": "d", "frequency": "Frequent", "ten_minute": True, "map_url": "u", "route_type": 900}}
    build_sqlite(FIX / "gtfs-mini.zip", out, None, source_url="fixture", service_levels=levels)
    conn = sqlite3.connect(out)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT service_level_name, service_level_desc, frequency, ten_minute, map_url FROM routes WHERE route_id='504'").fetchone()
    assert tuple(r) == ("Regular service", "d", "Frequent", 1, "u")
    assert conn.execute("SELECT service_level_name FROM routes WHERE route_id='300'").fetchone()[0] is None
    rows = {x["route"]: x for x in list_routes(conn)}
    assert rows["504"]["service_level"] == {"name": "Regular service", "description": "d", "frequency": "Frequent", "ten_minute_network": True, "map_url": "u"}
    assert rows["300"]["service_level"] is None
    conn.close()
