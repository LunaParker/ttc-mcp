from __future__ import annotations

from ttc_mcp.domain.arrivals import stop_row
from ttc_mcp.domain.disruptions import Disruptions, station_by_name, station_row
from ttc_mcp.domain.network import station_detail

BOARDING = {0: "unknown", 1: "accessible", 2: "not accessible"}


def resolve(conn, query: str):
    """A station by id, code, name or alias, else a stop; an all-digit query tries the exact station and stop before any name."""
    q = query.strip()
    if q.isdigit():
        return station_row(conn, q) or stop_row(conn, q) or station_by_name(conn, q)
    return station_row(conn, q) or station_by_name(conn, q) or stop_row(conn, q)


def for_stop(conn, row) -> dict:
    return {"stop": {"number": row["stop_id"], "name": row["name"]}, "wheelchair": row["wheelchair"], "boarding": BOARDING.get(row["wheelchair"], "unknown")}


def for_station(conn, station, disr: Disruptions) -> dict:
    """Decision b summary from the platforms; units from coded nodes joined to alerts active now by code (unmatched codes kept);
    alerts not active yet are listed under upcoming and leave the units and the verdict alone."""
    detail = station_detail(conn, station["stop_id"])
    sid = station["stop_id"]
    units: dict[tuple[str, str], dict] = {}
    uncoded = {"elevator": 0, "escalator": 0, "stair": 0}
    for u in conn.execute("SELECT * FROM units WHERE station_id=? ORDER BY level_index, node_id", (sid,)):
        if u["code"] is None:
            if u["kind"] in uncoded:
                uncoded[u["kind"]] += 1
            continue
        rec = units.setdefault((u["kind"], u["code"]), {"code": u["code"], "kind": u["kind"], "levels": [], "nodes": 0, "node_match": True,
                                                        "status": "in_service", "alert_id": None, "text": None})
        rec["nodes"] += 1
        if u["level_name"] and u["level_name"] not in rec["levels"]:
            rec["levels"].append(u["level_name"])
    upcoming: list[dict] = []
    for el in disr.for_station(sid):
        if el["type"] not in ("elevator", "escalator") or not el.get("unit_code"):
            continue
        if not el["active_now"]:
            upcoming.append({"alert_id": el["alert_id"], "kind": el["type"], "code": el["unit_code"], "period": el["period"], "text": el["text"]})
            continue
        key = (el["type"], el["unit_code"])
        rec = units.setdefault(key, {"code": el["unit_code"], "kind": el["type"], "levels": [], "nodes": 0, "node_match": False,
                                     "status": "in_service", "alert_id": None, "text": None})
        rec.update(status="out_of_service", alert_id=el["alert_id"], text=el["text"])
    rows = sorted(units.values(), key=lambda r: (r["kind"], r["code"]))
    elevators = [r for r in rows if r["kind"] == "elevator"]
    if any(r["status"] == "out_of_service" for r in elevators):
        verdict = {"verdict": "no", "basis": "alerts"}
    elif not elevators and uncoded["elevator"] == 0:
        verdict = {"verdict": "unknown", "basis": "no elevator nodes"}
    elif uncoded["elevator"]:
        verdict = {"verdict": "unknown", "basis": "uncoded nodes"}
    else:
        verdict = {"verdict": "yes", "basis": "nodes"}
    return {"station": {"id": sid, "name": station["name"]}, "summary": detail["wheelchair_summary"],
            "platforms": [{"platform": p["number"], "name": p["name"], "wheelchair": p["wheelchair"]} for p in detail["platforms"]],
            "entrances": [{"id": e["number"], "name": e["name"], "lat": e["lat"], "lon": e["lon"], "wheelchair": e["wheelchair"]} for e in detail["entrances"]],
            "units": rows, "uncoded_nodes": uncoded, "step_free": verdict, "upcoming": upcoming}
