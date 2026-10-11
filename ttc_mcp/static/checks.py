"""Self-checks logged after every static build (spec 8 items 3 and 9): names and codes the answers must be able to resolve."""
from __future__ import annotations

import sys

from ttc_mcp.adapters.ttc_alerts import RAIL_LINES, RAIL_TYPES, AlertList
from ttc_mcp.domain.disruptions import STATION_KINDS, UNIT_KINDS, station_by_name, station_row
from ttc_mcp.static.aliases import SUFFIX, normalize


def _log(log, msg):
    (log or (lambda m: print(m, file=sys.stderr)))(msg)


def unmatched_suffixes(conn) -> list[dict]:
    """Surface stops whose ' - X Station' suffix attached to no station (none within 300 m, not a GO station), so X resolves to nothing."""
    aliases = {r[0] for r in conn.execute("SELECT alias FROM station_aliases")}
    out = []
    for number, name in conn.execute("SELECT stop_id, name FROM stops WHERE location_type=0 AND (parent_station IS NULL OR parent_station='') "
                                     "AND name LIKE '% - % Station%' ORDER BY stop_id"):
        m = SUFFIX.search(name)
        if m and not m.group(1).endswith(" GO") and normalize(m.group(1)) not in aliases:
            out.append({"number": number, "name": name, "suffix": m.group(1)})
    return out


def _rail(a) -> bool:
    return a.route_type in RAIL_TYPES or any(r in RAIL_LINES for r in a.routes)


def unresolved_alert_stations(conn, alerts: AlertList) -> list[dict]:
    """Station ids on station alerts and station names on rail alerts that resolve to nothing, the way the answers resolve them."""
    out = []
    for a in alerts.alerts:
        if a.kind in STATION_KINDS:
            out += [{"alert_id": a.id, "kind": a.kind, "value": raw} for raw in a.stop_ids if station_row(conn, raw) is None]
        if _rail(a):
            names = [n for n in (a.stop_start, a.stop_end, *a.station_names) if n]
            out += [{"alert_id": a.id, "kind": a.kind, "value": n} for n in dict.fromkeys(names) if station_by_name(conn, n) is None]
    return out


def unit_code_coverage(conn, alerts: AlertList) -> dict:
    """How many unit codes named by elevator and escalator alerts match a coded pathway node of the same kind."""
    coded = {(r[0], r[1]) for r in conn.execute("SELECT DISTINCT kind, code FROM units WHERE code IS NOT NULL")}
    codes, matched, unmatched = 0, 0, []
    for a in alerts.alerts:
        if a.kind not in UNIT_KINDS or not a.unit_code:
            continue
        codes += 1
        if (a.kind, a.unit_code) in coded:
            matched += 1
            continue
        row = next((r for raw in a.stop_ids if (r := station_row(conn, raw)) is not None), None)
        station = row["name"] if row else (a.header.split(":")[0].strip() if ":" in a.header else None)
        unmatched.append({"alert_id": a.id, "kind": a.kind, "code": a.unit_code, "station": station})
    return {"codes": codes, "matched": matched, "unmatched": sorted(unmatched, key=lambda u: u["alert_id"])}


def describe(rep: dict) -> list[str]:
    s = rep["suffixes"]
    lines = [f"[check] surface-stop suffixes with no station: {len(s)}" + (f" ({', '.join(x['name'] for x in s[:5])})" if s else "")]
    if rep["alert_stations"] is None:
        return lines + ["[check] alert stations and unit codes: alerts unavailable"]
    u = rep["alert_stations"]
    lines.append(f"[check] alert stations unresolved: {len(u)}" + (f" ({', '.join(f'{x['value']} [{x['alert_id']}]' for x in u[:5])})" if u else ""))
    c = rep["unit_codes"]
    lines.append(f"[check] unit codes in alerts matching a coded node: {c['matched']} of {c['codes']}"
                 + (f"; unmatched {', '.join(f'{x['code']} ({x['station']})' for x in c['unmatched'])}" if c["unmatched"] else ""))
    return lines


def run_checks(conn, alerts: AlertList | None, *, log=None) -> dict:
    """The three checks on a freshly built store; the live two need the alert list and are skipped without it."""
    rep = {"suffixes": unmatched_suffixes(conn), "alert_stations": None, "unit_codes": None}
    if alerts is not None:
        rep["alert_stations"] = unresolved_alert_stations(conn, alerts)
        rep["unit_codes"] = unit_code_coverage(conn, alerts)
    for line in describe(rep):
        _log(log, line)
    return rep
