from array import array

from ttc_mcp.static import timetable
from ttc_mcp.static.schema import SCHEMA, SCHEMA_VERSION


def _t(tid, *times):
    return tid, array("i", times)


def test_schema_3_declares_the_two_tables():
    assert SCHEMA_VERSION == 3 and "CREATE TABLE raptor_groups" in SCHEMA and "CREATE TABLE station_paths" in SCHEMA


def test_split_keeps_a_non_overtaking_group_whole_and_in_departure_order():
    buckets = timetable.split_overtaking([_t(2, 100, 200, 300), _t(1, 50, 150, 250), _t(3, 150, 250, 350)])
    assert [[tid for tid, _ in b] for b in buckets] == [[1, 2, 3]]


def test_split_moves_an_overtaking_trip_to_its_own_bucket_and_keeps_later_trips_with_the_first():
    # Trip 2 leaves after trip 1 but arrives before it at the last stop; trip 3 overtakes nobody in bucket 1.
    buckets = timetable.split_overtaking([_t(1, 0, 100, 400), _t(2, 10, 110, 300), _t(3, 20, 200, 500)])
    assert [[tid for tid, _ in b] for b in buckets] == [[1, 3], [2]]


def test_split_puts_an_equal_time_in_the_same_bucket():
    assert len(timetable.split_overtaking([_t(1, 0, 100), _t(2, 0, 100)])) == 1


def test_pack_and_unpack_round_trip_columns_by_position():
    times, ids = timetable.pack([_t(7, 0, 60, 120), _t(9, 30, 90, 150)])
    cols, trip_ids = timetable.unpack(3, times, ids)
    assert [list(c) for c in cols] == [[0, 30], [60, 90], [120, 150]] and list(trip_ids) == [7, 9]
    assert len(times) == 3 * 2 * 4 and len(ids) == 2 * 8


def test_accumulator_groups_by_pattern_and_service_sorts_splits_and_counts():
    acc = timetable.GroupAccumulator()
    for pid, sid, tid, times in [(5, "1", 11, [100, 200]), (5, "1", 10, [0, 300]), (5, "2", 12, [0, 100]), (6, "1", 13, [0, 50, 60])]:
        acc.add(pid, sid, tid, times)
    rows = acc.rows()
    assert [(r[0], r[1], r[2], r[3], r[4]) for r in rows] == [(5, 0, "1", 1, 2), (5, 1, "1", 1, 2), (5, 0, "2", 1, 2), (6, 0, "1", 1, 3)]
    assert acc.splits == 1 and timetable.unpack(2, rows[0][5], rows[0][6])[1][0] == 10
