from __future__ import annotations

LABELS = {"EMPTY": ("Not busy", "low"), "FEW_SEATS_AVAILABLE": ("Busy", "high"), "FULL": ("Very busy", "high")}


def crowding(occupancy: str | None) -> dict:
    """ttc.ca's four labels over the three GTFS values the feed uses; EMPTY is low confidence (spec 6.3)."""
    label, confidence = LABELS.get(occupancy or "", ("No info", "none"))
    return {"label": label, "gtfs": occupancy if occupancy in LABELS else None, "confidence": confidence}
