import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from ttc_mcp.adapters.bustime_web import Prediction, eta_minutes, parse_stop_predictions, url_for

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "bustime"


def test_parses_predictions_and_collapses_the_double_space_in_direction_text():
    p = parse_stop_predictions((FIX / "stop-15461.xml").read_bytes(), {})
    sp = p.value
    assert (sp.stop, sp.name, sp.message, p.entities, p.feed_timestamp) == ("15461", "Cherry St at Front St East", None, 4, None)
    assert sp.predictions[0] == Prediction("504", "East - 504A King towards Distillery", "2 MIN", 2, "4617", False)
    assert [x.vehicle_id for x in sp.predictions] == ["4617", "4517", "4432", "4637"]


def test_stop_not_found_yields_message_and_no_predictions():
    p = parse_stop_predictions((FIX / "stop-notfound.xml").read_bytes(), {})
    assert (p.value.predictions, p.value.message, p.entities) == ([], "Stop not found", 0)


def test_unexpected_root_becomes_a_message():
    p = parse_stop_predictions(b"<error>Service unavailable</error>", {})
    assert p.value.predictions == [] and p.value.message == "Service unavailable"


@pytest.mark.parametrize("text,expected", [("9 MIN", 9), ("DUE", 0), ("1 MIN", 1), ("DLY", None), ("", None), ("12  MIN", 12)])
def test_eta_minutes(text, expected):
    assert eta_minutes(text) == expected


def test_scheduled_flag_and_missing_vehicle():
    body = b"<stop><id>1</id><nm>x</nm><pre><pt>DUE</pt><fd>North</fd><v></v><scheduled>true</scheduled><rn>7</rn></pre></stop>"
    row = parse_stop_predictions(body, {}).value.predictions[0]
    assert row == Prediction("7", "North", "DUE", 0, None, True)


def test_url_for_uses_the_public_stop_number():
    assert url_for("15461") == "https://bustime.ttc.ca/bustime/map/getStopPredictions.jsp?stop=15461&route=all"


def test_malformed_xml_raises():
    with pytest.raises(ET.ParseError):
        parse_stop_predictions(b"<stop><pre>", {})
