# scripts/bench_raptor.py
"""The planner's latency budget on the live store (spec 6.10, validation item 17).

Run: TTC_MCP_SKIP_REFRESH=1 uv run python scripts/bench_raptor.py [--live] [--budget-ms 250]
Needs a built store (TTC_MCP_CACHE). Twenty pairs at four times of day in four modes: an instant (window 0), 30- and 60-minute windows,
and arrive-by an hour later. Exit 1 when any mode's p95 exceeds the budget. --live adds the advisory snapshot and the trip-update
overlay the tool would use right now (phase 6c), which reaches the TTC's hosts.
"""
import argparse
import asyncio
import datetime as dt
import resource
import statistics
import sys
import time

from ttc_mcp import runtime
from ttc_mcp.adapters.ttc_alerts import AlertList
from ttc_mcp.domain import planner
from ttc_mcp.domain.disruptions import Disruptions
from ttc_mcp.static.schedule import TZ
from ttc_mcp.tools import derived_tools

sys.path.insert(0, "scripts")
from validate_connections import PAIRS  # noqa: E402

TIMES = ((8, 0), (17, 30), (23, 30), (1, 0))
MODES = {"instant": (0, False), "window30": (30, False), "window60": (60, False), "arrive_by": (30, True)}
LIMIT = 4  # the tool's default: only the options it returns are written and annotated


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6


async def snapshot_and_overlay(feed):
    """Phase 6c fills this in: the real snapshot's Disruptions and the overlay builder's inputs."""
    from ttc_mcp import advisory
    snap = await advisory.snapshot(feed)
    return snap.disruptions, snap


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    p.add_argument("--budget-ms", type=float, default=250)
    a = p.parse_args()
    feed, store = runtime.feed_for(), runtime.get_store()
    conn = feed.conn
    day = runtime.now().date()
    disr = Disruptions.build(conn, AlertList(None, None, []), None, [], runtime.now())
    if a.live:
        disr, _ = asyncio.run(snapshot_and_overlay(feed))
    ends = [(planner.resolve_endpoint(conn, o), planner.resolve_endpoint(conn, d)) for o, d in PAIRS]
    ends = [(o, d) for o, d in ends if o and d and o.stops and d.stops]
    r0 = rss_mb()
    worst = 0.0
    for mode, (window, arrive) in MODES.items():
        lat, found = [], 0
        for h, m in TIMES:
            when = dt.datetime.combine(day + dt.timedelta(days=1 if h < 4 else 0), dt.time(h, m), tzinfo=TZ)
            for o, d in ends:
                t = time.perf_counter()
                opts, _ = derived_tools._plan(feed, store, disr, o, d, when + dt.timedelta(hours=1) if arrive else when, 3, window, arrive, lambda s: True, limit=LIMIT)
                lat.append((time.perf_counter() - t) * 1000)
                found += bool(opts)
        lat.sort()
        p95 = lat[max(0, int(len(lat) * 0.95) - 1)]
        worst = max(worst, p95)
        print(f"{mode:10s} found {found}/{len(lat)}  p50 {statistics.median(lat):6.1f} ms  p95 {p95:6.1f} ms  max {lat[-1]:6.1f} ms")
    print(f"RSS {r0:.0f} -> {rss_mb():.0f} MB; budget {a.budget_ms:.0f} ms p95: {'PASS' if worst <= a.budget_ms else 'FAIL'}")
    return 0 if worst <= a.budget_ms else 1


if __name__ == "__main__":
    sys.exit(main())
