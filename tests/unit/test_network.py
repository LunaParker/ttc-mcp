from ttc_mcp.domain import network as net

CHERRY_FRONT = (43.6527, -79.3581)


def test_find_stops_by_name_returns_both_directional_stops_with_headsigns(conn):
    hits = net.find_stops(conn, "cherry st at front")
    assert {"15460", "15461"} <= {h["number"] for h in hits}
    eb = next(h for h in hits if h["number"] == "15461")
    assert any(s["route"] == "504" and s["direction_id"] == "0" and "Distillery" in s["headsign"] for s in eb["served"])


def test_find_stops_by_number_and_by_alias(conn):
    assert [h["number"] for h in net.find_stops(conn, "15461")] == ["15461"]
    tmu = net.find_stops(conn, "Dundas")
    assert any(h["kind"] == "station" and h["name"] == "TMU" for h in tmu)
    assert any(h["kind"] == "platform" and h["station"] for h in tmu)


def test_find_stops_route_and_direction_filters(conn):
    hits = net.find_stops(conn, "cherry", route="504", direction="1")
    assert hits and {"15460", "15461"} >= {h["number"] for h in hits}
    assert all(any(s["route"] == "504" and s["direction_id"] == "1" for s in h["served"]) for h in hits)


def test_nearest_stops_orders_by_distance_and_reports_walk_minutes(conn):
    near = net.nearest_stops(conn, *CHERRY_FRONT, radius_m=300, limit=5)
    assert near[0]["number"] == "15461" and near[0]["distance_m"] < near[1]["distance_m"]
    assert all(n["walk_min"] >= 0 and n["kind"] in ("stop", "platform") for n in near)


def test_nearest_stations_only(conn):
    st = net.nearest_stops(conn, 43.6465, -79.3800, radius_m=600, limit=3, kind="station")
    assert st and st[0]["kind"] == "station" and "1" in st[0]["lines"]


def test_stop_detail_includes_station_membership_for_platforms(conn):
    d = net.stop_detail(conn, "13863")
    assert d["kind"] == "platform" and d["station"] == "14572" and d["station_name"] == "Bloor-Yonge"
    assert net.stop_detail(conn, "0") is None


def test_station_detail_resolves_aliases_and_lists_platforms_and_entrances(conn):
    s = net.station_detail(conn, "Bloor")
    assert s["name"] == "Bloor-Yonge" and {p["number"] for p in s["platforms"]} >= {"13863", "13864"}
    assert s["entrances"] and all("lat" in e for e in s["entrances"]) and "1" in s["lines"]
    assert net.station_detail(conn, "nowhere") is None


def test_list_routes_series_overnight_and_mode(conn):
    rs = {r["route"]: r for r in net.list_routes(conn)}
    assert rs["300"]["overnight_share"] > 0.85 and rs["504"]["overnight_share"] < 0.5
    assert [r["route"] for r in net.list_routes(conn, overnight=True)] == ["300"]
    assert {r["route"] for r in net.list_routes(conn, mode="lrt")} == {"5"}
    assert "Mon,Tue,Wed,Thu,Fri" in rs["504"]["service"]


def test_route_stops_returns_ranked_patterns_with_typical_minutes(conn):
    r = net.route_stops(conn, "504", "0")
    p = r["patterns"][0]
    assert p["rank"] == 1 and p["stops"][0]["typical_min"] == 0 and p["stops"][-1]["typical_min"] > 10
    assert net.route_stops(conn, "999", "0") is None


def test_station_platforms_are_the_rail_platforms_and_bus_bays_are_surface_stops(conn):
    """York Mills' nine bus bays are children of the station flagged 0: stops at the station, never platforms, so the summary
    reads the two rail platforms only."""
    s = net.station_detail(conn, "York Mills")
    assert {p["number"] for p in s["platforms"]} == {"13791", "13792"} and s["wheelchair_summary"] == "accessible"
    bays = s["surface_stops"]
    assert {b["number"] for b in bays} >= {"14806", "14814"} and all(b["kind"] == "stop" and b["station"] == "43276" for b in bays)
    assert net.stop_detail(conn, "14806")["kind"] == "stop" and net.stop_detail(conn, "13791")["kind"] == "platform"
    assert net.station_detail(conn, "St George")["wheelchair_summary"] == "accessible"
