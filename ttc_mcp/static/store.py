from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from ttc_mcp.static.manifest import Manifest
from ttc_mcp.static.schema import SCHEMA_VERSION

EXPIRING_DAYS = 3


def _d(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y%m%d").date()


def schema_version(path: Path) -> int:
    """The schema a store was built with: 1 when it predates the column, 0 when the file is not a store or the value is not a number."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        return int(c.execute("SELECT schema_version FROM feed_info").fetchone()[0])
    except sqlite3.OperationalError as e:
        return 1 if "no such column" in str(e) else 0
    except (TypeError, ValueError, sqlite3.Error):
        return 0
    finally:
        c.close()


@dataclass
class FeedDb:
    version: str
    start: dt.date
    end: dt.date
    path: Path
    conn: sqlite3.Connection

    def covers(self, d: dt.date) -> bool:
        return self.start <= d <= self.end


class StaticStore:
    def __init__(self, feeds: list[FeedDb]):
        self.feeds = sorted(feeds, key=lambda f: (f.start, f.version))

    @classmethod
    def from_manifest(cls, cache_dir: Path) -> "StaticStore":
        feeds = []
        for e in Manifest.load(cache_dir).feeds:
            p = Path(cache_dir) / e.path
            if not p.exists():
                continue
            found = schema_version(p)
            if found != SCHEMA_VERSION:
                age = "old" if found < SCHEMA_VERSION else "newer than this server"
                print(f"[store] skipping {p.name}: its schema {found} is {age} (this server reads {SCHEMA_VERSION}); the next ttc-mcp-build "
                      "(or ttc-mcp-build --from-zip for a local feed) rebuilds it", file=sys.stderr)
                continue
            conn = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            feeds.append(FeedDb(e.feed_version, _d(e.start), _d(e.end), p, conn))
        return cls(feeds)

    def for_date(self, d: dt.date) -> FeedDb | None:
        covering = [f for f in self.feeds if f.covers(d)]
        return max(covering, key=lambda f: (f.start, f.version)) if covering else None

    def newest(self) -> FeedDb | None:
        return self.feeds[-1] if self.feeds else None

    def status(self, today: dt.date) -> dict:
        active = self.for_date(today)
        newer = any(f.start > (active.start if active else today) for f in self.feeds)
        if not self.feeds:
            verdict, days = "unavailable", None
        elif active is None:
            verdict, days = "expired", None
        else:
            days = (active.end - today).days
            verdict = "expiring" if days <= EXPIRING_DAYS and not newer else "fresh"
        return {"feeds": [{"version": f.version, "start": f.start.isoformat(), "end": f.end.isoformat(),
                           "path": str(f.path), "bytes": f.path.stat().st_size} for f in self.feeds],
                "active": active.version if active else None, "verdict": verdict, "days_remaining": days, "newer_seen": newer}

    def close(self) -> None:
        for f in self.feeds:
            f.conn.close()
