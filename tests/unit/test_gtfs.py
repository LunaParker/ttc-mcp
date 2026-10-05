from ttc_mcp.static import gtfs


def test_secs_handles_times_past_midnight():
    assert gtfs.secs("05:31:00") == 19860 and gtfs.secs("25:30:00") == 91800


def test_series_follows_ttc_numbering():
    assert [gtfs.series_of(x) for x in ("1", "7", "300", "400", "504", "929", "882", "abc")] == \
        ["rail", "bus", "blue-night", "community", "streetcar", "express", "bus", "other"]


def test_mode_treats_lines_5_and_6_as_lrt_despite_route_type_0():
    assert gtfs.mode_of(1, "1") == "subway" and gtfs.mode_of(0, "5") == "lrt" and gtfs.mode_of(0, "6") == "lrt"
    assert gtfs.mode_of(0, "504") == "streetcar" and gtfs.mode_of(3, "29") == "bus" and gtfs.mode_of(4, "882") == "other"
