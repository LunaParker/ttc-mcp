import pytest

from ttc_mcp.adapters.ntas import NextTrains, parse_next_trains, url_for

BLOOR_NB = b'[{"line":"1","direction":"1","nextTrains":"0, 4, 5","directionText":"Northbound to Finch","stopCode":"13863"}]'
DRIFTWOOD = b'[{"line":"6","direction":"0","nextTrains":"","directionText":"Eastbound to Finch West","stopCode":"16317"}]'


def test_parses_next_train_minutes():
    p = parse_next_trains(BLOOR_NB, {})
    assert p.value == [NextTrains("13863", "1", "1", "Northbound to Finch", [0, 4, 5])]
    assert (p.entities, p.feed_timestamp) == (1, None)


def test_empty_next_trains_string_is_no_prediction_not_no_trains():
    p = parse_next_trains(DRIFTWOOD, {})
    assert p.value[0].next_min == [] and p.value[0].line == "6" and p.entities == 1


def test_non_rail_stop_is_an_empty_list():
    assert parse_next_trains(b"[]", {}).value == [] and parse_next_trains(b"", {}).value == []


def test_non_list_body_raises():
    with pytest.raises(ValueError):
        parse_next_trains(b'{"error": 1}', {})


def test_url_for():
    assert url_for("13863") == "https://ntas.ttc.ca/api/ntas/get-next-train-time/13863"
