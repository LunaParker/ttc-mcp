from ttc_mcp.domain import shapes
from ttc_mcp.domain.geo import decode_polyline


def test_canonical_shape_with_stops_and_distances(conn):
    r = shapes.route_shape(conn, "504", "0")
    assert (r["route"], r["direction_id"], r["pattern"], r["pattern_id"], r["shape_id"], r["trips_using"], r["format"]) == ("504", "0", 1, 10, "shp-504-04", 240, "polyline")
    assert r["headsign"] == "East - 504B King towards Broadview Station" and 9400 < r["length_m"] < 9600 and r["points"] == 539
    assert len(decode_polyline(r["polyline"])) == 539 and len(r["stops"]) == 36
    assert r["stops"][0]["dist_m"] == 0 and r["stops"][-1]["dist_m"] > 9000 and r["stops"][1]["number"] and r["stops"][1]["name"]


def test_geojson_and_simplification(conn):
    g = shapes.route_shape(conn, "1", "0", fmt="geojson", simplify_m=20)
    assert g["format"] == "geojson" and g["geometry"]["type"] == "LineString" and g["shape_id"] == "1124986"
    assert 2 < len(g["geometry"]["coordinates"]) < 874 and g["points"] == len(g["geometry"]["coordinates"])
    lon, lat = g["geometry"]["coordinates"][0]
    assert -80 < lon < -79 and 43 < lat < 44 and "polyline" not in g


def test_stops_between_slices_by_shape_dist(conn):
    r = shapes.route_shape(conn, "504", "0", pattern=2, between=("4569", "4573"))
    assert r["pattern_id"] == 6 and [s["number"] for s in r["stops"]] == ["4569", "15039", "14994", "14993", "4573"]
    assert abs(r["between"]["length_m"] - (1790.29 - 623.66)) < 2 and r["between"] == {"from": "4569", "to": "4573", "length_m": r["between"]["length_m"]}
    pts = decode_polyline(r["polyline"])
    assert 2 < len(pts) < 539 and r["length_m"] > 10000
    assert shapes.route_shape(conn, "504", "0", pattern=2, between=("4573", "4569")) is None


def test_unknown_route_or_pattern_is_none(conn):
    assert shapes.route_shape(conn, "999", "0") is None and shapes.route_shape(conn, "504", "0", pattern=99) is None
