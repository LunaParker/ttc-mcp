from pathlib import Path

import pytest

from ttc_mcp.adapters.detour_feed import parse_detours, parse_entity_id

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "advisories"


def test_parses_entities_shapes_and_ids():
    p = parse_detours((FIX / "detour.json").read_bytes(), {})
    f = p.value
    assert p.entities == 2 and p.feed_timestamp is None
    assert set(f.shapes) == {"detour_0_9001:0_0_6_504_Distillery_shape", "detour_1_9002:1_1_77_77_Nowhere_shape"}
    e = f.entities[0]
    assert (e.detour_id, e.route, e.route_label, e.direction_id, e.pattern_ref, e.destination) == ("9001", "504", "504", "0", "6", "Distillery")
    assert e.trip_ids == ("100677020", "100895020", "101100020") and e.service_dates == ("20261005", "20261006", "20261007")
    assert e.spans == ((3, 5, 0),) and e.shape_id == "detour_0_9001:0_0_6_504_Distillery_shape" and f.shapes[e.shape_id].startswith("wfwiGturcN")
    assert f.entities[1].trip_ids == ("999999999",) and f.entities[1].spans == ((2, 2, 0),)


def test_entity_id_parsing_handles_replacement_bus_labels_and_garbage():
    assert parse_entity_id("detour_3_695:0_0_1209_112_Kipling Station via Renforth Station") == {
        "detour_id": "695", "direction_id": "0", "pattern_ref": "1209", "route_label": "112", "route": "112",
        "destination": "Kipling Station via Renforth Station"}
    bus = parse_entity_id("detour_41_786:1_1_880_306 Bus_Replacement Bus towards Main Street Station")
    assert (bus["route_label"], bus["route"], bus["destination"]) == ("306 Bus", "306", "Replacement Bus towards Main Street Station")
    assert parse_entity_id("something else") is None


def test_missing_entity_list_raises():
    with pytest.raises(ValueError):
        parse_detours(b'{"header": {}}', {})
