import datetime as dt

import pytest

from ttc_mcp.static.schedule import TZ, day_label, departures_at, service_hours, service_ids_on, service_secs, wall_clock


def test_service_ids_follow_calendar_and_exceptions(conn):
    assert "1" in service_ids_on(conn, dt.date(2026, 10, 6))          # a Tuesday
    assert "1" not in service_ids_on(conn, dt.date(2026, 9, 7))       # Labour Day: service 1 removed
    assert "4" in service_ids_on(conn, dt.date(2026, 9, 7))           # ... and holiday service added
    assert "4401" in service_ids_on(conn, dt.date(2026, 10, 7))       # Wednesday community bus
    assert "4401" not in service_ids_on(conn, dt.date(2026, 10, 6))


def test_wall_clock_uses_noon_minus_twelve_hours_in_absolute_time():
    assert wall_clock(dt.date(2026, 10, 5), 19860).isoformat() == "2026-10-05T05:31:00-04:00"
    assert wall_clock(dt.date(2026, 10, 5), 91800).isoformat() == "2026-10-06T01:30:00-04:00"
    # Fall-back day: GTFS 01:00 is one elapsed hour after noon-12h, i.e. the second 01:00 (EST).
    assert wall_clock(dt.date(2026, 11, 1), 3600).isoformat() == "2026-11-01T01:00:00-05:00"


def test_service_secs_count_elapsed_seconds_from_the_days_origin_across_a_clock_change():
    assert service_secs(dt.date(2026, 10, 6), dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ)) == 28800
    assert service_secs(dt.date(2026, 11, 1), dt.datetime.fromisoformat("2026-11-01T08:00-05:00")) == 28800
    assert service_secs(dt.date(2026, 11, 1), dt.datetime(2026, 11, 1, 1, 10, fold=1, tzinfo=TZ)) == 4200  # the second 01:10
    assert service_secs(dt.date(2027, 3, 14), dt.datetime(2027, 3, 14, 8, 0, tzinfo=TZ)) == 28800


def test_departures_include_previous_service_day_after_midnight(conn):
    at = dt.datetime(2026, 10, 6, 1, 15, tzinfo=TZ)       # Tuesday 01:15: Monday's 25:xx trips still apply
    rows = departures_at(conn, "15461", at, limit=5)
    assert rows and all(r["when"] >= at.isoformat() for r in rows)
    assert any(r["service_day"] == "2026-10-05" and r["dep_secs"] >= 86400 for r in rows)


def test_departures_filter_by_route_and_are_sorted(conn):
    at = dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ)
    rows = departures_at(conn, "15461", at, limit=4, route="504")
    assert len(rows) == 4 and [r["when"] for r in rows] == sorted(r["when"] for r in rows)
    assert all(r["route"] == "504" and r["in_min"] >= 0 and r["gtfs_time"] for r in rows)


def test_departures_on_a_clock_change_day_keep_the_wall_clock_of_an_ordinary_sunday(dst_conn):
    def clock(rows):
        return [(r["when"][11:19], r["in_min"]) for r in rows]
    ordinary = departures_at(dst_conn, "15461", dt.datetime(2026, 10, 25, 10, 0, tzinfo=TZ), limit=4)
    assert ordinary and clock(departures_at(dst_conn, "15461", dt.datetime(2026, 11, 1, 10, 0, tzinfo=TZ), limit=4)) == clock(ordinary)
    assert clock(departures_at(dst_conn, "15461", dt.datetime(2027, 3, 14, 10, 0, tzinfo=TZ), limit=4)) == clock(ordinary)


def test_departures_through_the_repeated_hour_run_in_elapsed_order_from_the_question(dst_conn):
    """Saturday's late King cars run into fall-back day: from 00:50 EDT the list crosses into EST in elapsed order, and from the second 01:10
    the first is Saturday's 26:14, not its 25:14 an hour earlier."""
    rows = departures_at(dst_conn, "15461", dt.datetime(2026, 11, 1, 0, 50, tzinfo=TZ), limit=10)
    assert [r["in_min"] for r in rows] == [4, 14, 24, 34, 44, 54, 64, 74, 84, 94] and rows[7]["when"] == "2026-11-01T01:04:23-05:00"
    second = departures_at(dst_conn, "15461", dt.datetime(2026, 11, 1, 1, 10, fold=1, tzinfo=TZ), limit=3)
    assert [(r["when"][11:], r["in_min"]) for r in second] == [("01:14:23-05:00", 4), ("01:24:23-05:00", 14), ("01:39:23-05:00", 29)]


@pytest.mark.parametrize("at", [dt.datetime(2026, 10, 6, 8, 0, tzinfo=TZ), dt.datetime(2026, 10, 6, 0, 40, 59, 500000, tzinfo=TZ)])
def test_departures_stop_at_the_horizon_when_one_is_given(conn, at):
    full = departures_at(conn, "15461", at, limit=500)
    near = departures_at(conn, "15461", at, limit=500, horizon_min=30)
    assert near and near == [r for r in full if r["in_min"] <= 30] and len(near) < len(full)


def test_service_hours_for_a_blue_night_route(conn):
    h = service_hours(conn, "300", dt.date(2026, 10, 6))
    assert h["runs"] and h["first"] < "06:00" and h["last"] > "24:00" and h["trips"] > 0
    assert service_hours(conn, "400", dt.date(2026, 10, 6))["runs"] is False
    assert service_hours(conn, "400", dt.date(2026, 10, 7))["runs"] is True


def test_day_label():
    assert day_label(0b0011111) == "Mon,Tue,Wed,Thu,Fri" and day_label(0b1000000) == "Sun" and day_label(0) == ""
