import pytest

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
    with pytest.raises(ValueError, match="not after"):
        shapes.route_shape(conn, "504", "0", pattern=2, between=("4573", "4569"))


def test_between_uses_the_first_visit_of_a_repeated_stop(conn):
    r = shapes.route_shape(conn, "400", "0", between=("15607", "11641"))
    assert r["pattern_id"] == 41 and [(s["seq"], s["number"]) for s in r["stops"]] == [(34, "15607"), (35, "11641")]
    r = shapes.route_shape(conn, "400", "0", between=("175", "15607"))
    assert [(s["seq"], s["number"]) for s in r["stops"]] == [(33, "175"), (34, "15607")]
    r = shapes.route_shape(conn, "400", "0", between=("11641", "15607"))
    assert [(s["seq"], s["number"]) for s in r["stops"]] == [(35, "11641"), (36, "15607")]


def test_between_with_no_shape_vertex_inside_still_brackets_the_span(conn):
    for rank in (2, 4):
        r = shapes.route_shape(conn, "400", "0", pattern=rank, between=("5350", "5403"))
        pts = decode_polyline(r["polyline"])
        assert len(pts) >= 2 and r["points"] == len(pts) and "note" not in r


def test_between_errors_say_why(conn):
    with pytest.raises(ValueError, match="stop 99999 is not on"):
        shapes.route_shape(conn, "504", "0", pattern=2, between=("4569", "99999"))
    with pytest.raises(ValueError, match="stop 99999 is not on"):
        shapes.route_shape(conn, "504", "0", pattern=2, between=("99999", "4569"))
    with pytest.raises(ValueError, match="not after"):
        shapes.route_shape(conn, "504", "0", pattern=2, between=("4569", "4569"))
    # The build zeroes a blank first-stop distance, so no fixture stop lacks one; exercise the branch directly.
    with pytest.raises(ValueError, match="no shape distance for stop 14111"):
        shapes._span([{"number": "14111", "dist_m": None}, {"number": "13789", "dist_m": 1520.0}], "14111", "13789", "the pattern")


def test_unknown_route_or_pattern_is_none(conn):
    assert shapes.route_shape(conn, "999", "0") is None and shapes.route_shape(conn, "504", "0", pattern=99) is None


def test_rail_stops_between_work_from_the_first_station(conn):
    r = shapes.route_shape(conn, "1", "0")
    first, third = r["stops"][0]["number"], r["stops"][2]["number"]
    assert r["stops"][0]["dist_m"] == 0.0 and abs(r["stops"][-1]["dist_m"] - r["length_m"]) < 150
    span = shapes.route_shape(conn, "1", "0", between=(first, third))
    assert span is not None and [s["number"] for s in span["stops"]] == [first, r["stops"][1]["number"], third]
    assert 1000 < span["between"]["length_m"] < 6000 and 2 < len(decode_polyline(span["polyline"])) < 633
