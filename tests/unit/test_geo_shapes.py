from ttc_mcp.domain.geo import cumulative_m, decode_polyline, encode_polyline, simplify, slice_by_dist

GOOGLE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
POINTS = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def test_encode_round_trips_the_google_example():
    assert encode_polyline(POINTS) == GOOGLE and decode_polyline(encode_polyline(POINTS)) == POINTS
    assert encode_polyline([]) == ""


def test_cumulative_metres_start_at_zero_and_grow():
    d = cumulative_m([(43.65, -79.38), (43.65, -79.37), (43.66, -79.37)])
    assert d[0] == 0 and 800 < d[1] < 810 and 1900 < d[2] < 1930


def test_simplify_drops_collinear_points_but_keeps_corners():
    line = [(43.650, -79.400), (43.651, -79.400), (43.652, -79.400), (43.652, -79.390)]
    assert simplify(line, 5) == [(43.650, -79.400), (43.652, -79.400), (43.652, -79.390)]
    assert simplify(line, 100000) == [(43.650, -79.400), (43.652, -79.390)]
    assert simplify(line[:2], 5) == line[:2] and simplify([], 5) == []


def test_slice_by_dist_brackets_the_span_with_its_nearest_vertices():
    pts = [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)]
    dists = [0, 100, 200, 300, 400]
    assert slice_by_dist(pts, dists, 150, 250) == [(0, 1), (0, 2), (0, 3)]
    assert slice_by_dist(pts, dists, 0, 100) == [(0, 0), (0, 1)]
    assert slice_by_dist(pts, dists, 350, 400) == [(0, 3), (0, 4)]
    assert slice_by_dist(pts, dists, 120, 130) == [(0, 1), (0, 2)]
    assert slice_by_dist(pts, dists, -10, 450) == pts and slice_by_dist([], [], 0, 1) == []
