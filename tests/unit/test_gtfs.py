import zipfile

from ttc_mcp.static import gtfs


def test_secs_handles_times_past_midnight():
    assert gtfs.secs("05:31:00") == 19860 and gtfs.secs("25:30:00") == 91800


def test_series_follows_ttc_numbering():
    assert [gtfs.series_of(x) for x in ("1", "7", "300", "400", "504", "929", "882", "abc")] == \
        ["rail", "bus", "blue-night", "community", "streetcar", "express", "bus", "other"]


def test_mode_treats_lines_5_and_6_as_lrt_despite_route_type_0():
    assert gtfs.mode_of(1, "1") == "subway" and gtfs.mode_of(0, "5") == "lrt" and gtfs.mode_of(0, "6") == "lrt"
    assert gtfs.mode_of(0, "504") == "streetcar" and gtfs.mode_of(3, "29") == "bus" and gtfs.mode_of(4, "882") == "other"


def _zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for name, text in members.items():
            z.writestr(name, text)
    return path


def test_a_feed_packed_inside_one_folder_reads_like_a_flat_one(tmp_path):
    """The surface zip CKAN served from 2026-10-29 (S1000544) put every file under 'GTFSExport - Copy/'."""
    with zipfile.ZipFile(_zip(tmp_path / "f.zip", {"GTFSExport - Copy/": "", "GTFSExport - Copy/stops.txt": "stop_id,stop_code\n4282,15461\n"})) as z:
        assert gtfs.has(z, "stops.txt") and list(gtfs.read_rows(z, "stops.txt")) == [{"stop_id": "4282", "stop_code": "15461"}]


def test_a_root_member_wins_and_an_ambiguous_or_deeper_one_is_not_guessed(tmp_path):
    with zipfile.ZipFile(_zip(tmp_path / "a.zip", {"stops.txt": "stop_id\n1\n", "old/stops.txt": "stop_id\n2\n"})) as z:
        assert [r["stop_id"] for r in gtfs.read_rows(z, "stops.txt")] == ["1"]
    with zipfile.ZipFile(_zip(tmp_path / "b.zip", {"a/stops.txt": "stop_id\n1\n", "b/stops.txt": "stop_id\n2\n"})) as two, \
            zipfile.ZipFile(_zip(tmp_path / "c.zip", {"a/b/stops.txt": "stop_id\n1\n"})) as deep:
        assert not gtfs.has(two, "stops.txt") and not gtfs.has(deep, "stops.txt")
