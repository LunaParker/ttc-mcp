from ttc_mcp.domain.geo import bbox, haversine_m, walk_minutes


def test_haversine_between_cherry_stops_is_about_forty_metres():
    assert 30 < haversine_m(43.652840, -79.358092, 43.652513, -79.357841) < 50


def test_walk_minutes_uses_detour_factor_and_80m_per_min():
    assert walk_minutes(245) == 4 and walk_minutes(0) == 0


def test_bbox_is_symmetric_and_widens_with_latitude():
    lo, hi, wlo, whi = bbox(43.65, -79.38, 500)
    assert abs((hi - lo) - 2 * 500 / 111320) < 1e-9 and (whi - wlo) > (hi - lo)
