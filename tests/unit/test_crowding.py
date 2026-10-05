import pytest

from ttc_mcp.domain.crowding import crowding


@pytest.mark.parametrize("occ,label,conf,gtfs", [
    ("EMPTY", "Not busy", "low", "EMPTY"),
    ("FEW_SEATS_AVAILABLE", "Busy", "high", "FEW_SEATS_AVAILABLE"),
    ("FULL", "Very busy", "high", "FULL"),
    (None, "No info", "none", None),
    ("NO_DATA_AVAILABLE", "No info", "none", None),
])
def test_crowding_vocabulary(occ, label, conf, gtfs):
    assert crowding(occ) == {"label": label, "gtfs": gtfs, "confidence": conf}
