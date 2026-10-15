# scripts/bench_raptor.py
"""The planner's latency budget on the live store (spec 6.10, validation item 17).

Run: TTC_MCP_SKIP_REFRESH=1 uv run python scripts/bench_raptor.py [--live] [--budget-ms 250]
Needs a built store (TTC_MCP_CACHE). Twenty pairs at four times of day in four modes: an instant (window 0), 30- and 60-minute windows,
and arrive-by an hour later, on tomorrow's timetable (01:00 the day after), so no target is in the past whatever the clock says.
Exit 1 when any mode's p95 exceeds the budget, or when the grid did not plan: an arrive-by target not after now, a grid day no feed
covers or with no service, or an empty answer without one of the planner's notes. An empty answer the search ran to the end for (no
service at a pair's stops, no journey) is a timetable fact: it is listed and counted. --live adds the advisory snapshot and the
trip-update overlay the tool would use right now (phase 6c), which reaches the TTC's hosts: the grid then runs on today's date at four
instants from now (the overlay only matters near now), each mode twice, with the overlay memo warm and cleared before every call (a fresh
fetch's cost), and the numbers are a measurement, not a verdict: the budget is defined on the committed grid (spec 6.10, section 11).
"""
import argparse
import asyncio
import datetime as dt
import resource
import statistics
import sys
import time
from collections import Counter

from ttc_mcp import overlay, runtime
from ttc_mcp.adapters.ttc_alerts import AlertList
from ttc_mcp.domain import planner
from ttc_mcp.domain.disruptions import Disruptions
from ttc_mcp.static import schedule
from ttc_mcp.static.schedule import TZ
from ttc_mcp.tools import derived_tools

sys.path.insert(0, "scripts")
from validate_connections import PAIRS  # noqa: E402

TIMES = ((8, 0), (17, 30), (23, 30), (1, 0))
MODES = {"instant": (0, False), "window30": (30, False), "window60": (60, False), "arrive_by": (30, True)}
LIMIT = 4  # the tool's default: only the options it returns are written and annotated
KINDS = ("arrive_by", "no service", "no journey", derived_tools.CLOSED)  # how _plan's empty-answer notes begin


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


async def snapshot_and_overlay(feed):
    """The real snapshot's Disruptions and the overlay builder's inputs: the detour feed and both trip-update feeds."""
    from ttc_mcp import advisory
    snap = await advisory.snapshot(feed)
    trips, subway, _ = await derived_tools._trip_feeds(runtime.get_realtime(), True)
    return snap.disruptions, snap.detour_feed, trips, subway


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    p.add_argument("--budget-ms", type=float, default=250)
    a = p.parse_args()
    feed, store = runtime.feed_for(), runtime.get_store()
    conn = feed.conn
    day = runtime.now().date() + dt.timedelta(days=0 if a.live else 1)
    disr, detour_feed, trips, subway = Disruptions.build(conn, AlertList(None, None, []), None, [], runtime.now()), None, None, None
    if a.live:
        disr, detour_feed, trips, subway = asyncio.run(snapshot_and_overlay(feed))
    instants = [runtime.now() + dt.timedelta(minutes=15 * k) for k in range(len(TIMES))] if a.live else \
        [dt.datetime.combine(day + dt.timedelta(days=1 if h < 4 else 0), dt.time(h, m), tzinfo=TZ) for h, m in TIMES]
    ends = [(planner.resolve_endpoint(conn, o), planner.resolve_endpoint(conn, d), f"{o} -> {d}") for o, d in PAIRS]
    ends = [(o, d, label) for o, d, label in ends if o and d and o.stops and d.stops]
    problems = [f"no feed covers {d}, or it runs no service that day" for d in (day, day + dt.timedelta(days=1))
                if not (f := store.for_date(d)) or not schedule.service_ids_on(f.conn, d)]
    r0 = rss_mb()
    worst, empty = 0.0, {}
    for (mode, (window, arrive)), (variant, cold) in ((m, v) for m in MODES.items() for v in ((("warm", False), ("cold", True)) if a.live else (("", False),))):
        lat, found, why = [], 0, Counter()
        for when in instants:
            for o, d, label in ends:
                if cold:
                    overlay.reset()
                t = time.perf_counter()
                opts, note = derived_tools._plan(feed, store, disr, o, d, when + dt.timedelta(hours=1) if arrive else when, 3, window, arrive,
                                                 None, limit=LIMIT, detour_feed=detour_feed, trips=trips, subway=subway)
                lat.append((time.perf_counter() - t) * 1000)
                found += bool(opts)
                if not opts:
                    kind = next((k for k in KINDS if (note or "").startswith(k)), "other")
                    why[kind] += 1
                    if kind not in ("arrive_by", "other"):
                        empty.setdefault(label, Counter())[kind] += 1
        lat.sort()
        p95 = lat[max(0, int(len(lat) * 0.95) - 1)]
        worst = max(worst, p95)
        print(f"{mode + ' ' + variant:15s} found {found}/{len(lat)}  p50 {statistics.median(lat):6.1f} ms  p95 {p95:6.1f} ms  max {lat[-1]:6.1f} ms")
        if why["arrive_by"]:
            problems.append(f"{mode}: {why['arrive_by']} arrive-by targets were not after now, so they never planned")
        if why["other"]:
            problems.append(f"{mode}: {why['other']} answers came back empty without a planner note")
    verdict = "measured, no verdict" if a.live else "PASS" if worst <= a.budget_ms else "FAIL"
    print(f"RSS {r0:.0f} -> {rss_mb():.0f} MB; budget {a.budget_ms:.0f} ms p95: {verdict}; grid {day}{' from now, live' if a.live else ' and the night after'}")
    for label, kinds in empty.items():
        print(f"empty after a full search (counted, not a failure): {label}: {dict(kinds)}")
    for line in problems:
        print(f"INVALID: {line}")
    return 0 if verdict != "FAIL" and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
