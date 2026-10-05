from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ttc_mcp.cache import Parsed

ID = re.compile(r"^detour_(\d+)_([^:]+):(\d)_(\d)_(\d+)_(.*)$")


@dataclass(frozen=True)
class DetourEntity:
    id: str
    detour_id: str
    route_label: str
    route: str
    direction_id: str
    pattern_ref: str
    destination: str
    trip_ids: tuple[str, ...]
    shape_id: str | None
    service_dates: tuple[str, ...]
    spans: tuple[tuple[int, int, int], ...]


@dataclass(frozen=True)
class DetourFeed:
    entities: list[DetourEntity]
    shapes: dict[str, str]


def parse_entity_id(eid: str) -> dict | None:
    """detour_{n}_{detourId}:{dir}_{dir}_{pattern}_{route}_{destination}; the route label may be '306 Bus'."""
    m = ID.match(eid)
    if not m:
        return None
    label, _, dest = m.group(6).partition("_")
    return {"detour_id": m.group(2), "direction_id": m.group(3), "pattern_ref": m.group(5), "route_label": label,
            "route": label.split(" ")[0], "destination": dest}


def parse_detours(body: bytes, headers: dict[str, str]) -> Parsed:
    doc = json.loads(body)
    ents = doc.get("entity") if isinstance(doc, dict) else None
    if ents is None:
        raise ValueError("detour feed: no entity list")
    entities, shapes = [], {}
    for e in ents:
        if "shape" in e:
            shapes[e["shape"].get("shapeId") or e["id"]] = e["shape"].get("encodedPolyline") or ""
            continue
        tm = e.get("tripModifications")
        if not tm:
            continue
        meta = parse_entity_id(e["id"]) or {"detour_id": e["id"], "direction_id": "", "pattern_ref": "", "route_label": "", "route": "", "destination": ""}
        selected = tm.get("selectedTrips") or [{}]
        trip_ids = tuple(t for s in selected for t in s.get("tripIds", []))
        spans = tuple((int(m["startStopSelector"]["stopSequence"]), int(m["endStopSelector"]["stopSequence"]),
                       int(m.get("propagatedModificationDelay", 0)))
                      for m in tm.get("modifications", []) if "startStopSelector" in m and "endStopSelector" in m)
        entities.append(DetourEntity(e["id"], meta["detour_id"], meta["route_label"], meta["route"], meta["direction_id"],
                                     meta["pattern_ref"], meta["destination"], trip_ids, selected[0].get("shapeId"),
                                     tuple(tm.get("serviceDates", [])), spans))
    return Parsed(DetourFeed(entities, shapes), None, len(entities))
