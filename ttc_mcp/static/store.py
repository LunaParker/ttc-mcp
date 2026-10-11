from __future__ import annotations

import datetime as dt
import sqlite3
import sys
import threading
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ttc_mcp.static.manifest import Manifest
from ttc_mcp.static.schema import SCHEMA_VERSION

EXPIRING_DAYS = 3
TripLookup = Callable[[str], "tuple[sqlite3.Connection, sqlite3.Row] | None"]


def _d(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y%m%d").date()


def trip_row(conn: sqlite3.Connection, trip_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM trips WHERE trip_id=?", (trip_id,)).fetchone()


def single_feed(conn: sqlite3.Connection) -> TripLookup:
    """Trip resolution against one connection, for callers without a store."""
    def look(trip_id: str):
        row = trip_row(conn, trip_id)
        return (conn, row) if row else None
    return look


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


def open_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


class _Held:
    """A thread's connection, kept in its thread-local; when the thread exits it dies and its finalizer closes the connection."""
    __slots__ = ("conn", "__weakref__")

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn


def _forget(conn: sqlite3.Connection, opened: list[sqlite3.Connection], lock: threading.Lock) -> None:
    with lock:
        if conn not in opened:
            return  # close() already took it
        opened.remove(conn)
    conn.close()


@dataclass
class FeedDb:
    version: str
    start: dt.date
    end: dt.date
    path: Path
    _local: threading.local = field(default_factory=threading.local, repr=False, compare=False)
    _opened: list[sqlite3.Connection] = field(default_factory=list, repr=False, compare=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @property
    def conn(self) -> sqlite3.Connection:
        """One read-only connection per thread: SQLite in multi-thread mode forbids sharing one across threads at once."""
        held = getattr(self._local, "held", None)
        if held is None:
            held = self._local.held = _Held(open_ro(self.path))
            with self._lock:
                self._opened.append(held.conn)
            weakref.finalize(held, _forget, held.conn, self._opened, self._lock)
        return held.conn

    def covers(self, d: dt.date) -> bool:
        return self.start <= d <= self.end

    def close(self) -> None:
        with self._lock:
            opened = self._opened[:]
            self._opened.clear()
        for c in opened:
            c.close()
        self._local = threading.local()


class StaticStore:
    def __init__(self, feeds: list[FeedDb], *, checked_at: str | None = None, ckan_last_modified: str | None = None):
        self.feeds = sorted(feeds, key=lambda f: (f.start, f.version))
        self.checked_at, self.ckan_last_modified = checked_at, ckan_last_modified

    @classmethod
    def from_manifest(cls, cache_dir: Path) -> StaticStore:
        feeds, manifest = [], Manifest.load(cache_dir)
        for e in manifest.feeds:
            p = Path(cache_dir) / e.path
            if not p.exists():
                continue
            found = schema_version(p)
            if found != SCHEMA_VERSION:
                age = "old" if found < SCHEMA_VERSION else "newer than this server"
                print(f"[store] skipping {p.name}: its schema {found} is {age} (this server reads {SCHEMA_VERSION}); the next ttc-mcp-build "
                      "(or ttc-mcp-build --from-zip for a local feed) rebuilds it", file=sys.stderr)
                continue
            feeds.append(FeedDb(e.feed_version, _d(e.start), _d(e.end), p))
        return cls(feeds, checked_at=manifest.checked_at, ckan_last_modified=manifest.ckan_last_modified)

    def for_date(self, d: dt.date) -> FeedDb | None:
        covering = [f for f in self.feeds if f.covers(d)]
        return max(covering, key=lambda f: (f.start, f.version)) if covering else None

    def newest(self) -> FeedDb | None:
        return self.feeds[-1] if self.feeds else None

    def ordered(self, today: dt.date) -> list[FeedDb]:
        """Every loaded feed, today's first: in a board-period overlap the live feeds carry the incoming feed's trip ids (spec 2.1)."""
        active = self.for_date(today)
        return [f for f in self.feeds if f is active] + [f for f in self.feeds if f is not active]

    def lookup_trip(self, trip_id: str, today: dt.date | None = None) -> tuple[FeedDb, sqlite3.Row] | None:
        for f in self.ordered(today or dt.date.today()):
            row = trip_row(f.conn, trip_id)
            if row is not None:
                return f, row
        return None

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
                "active": active.version if active else None, "verdict": verdict, "days_remaining": days, "newer_package_seen": newer,
                "last_check": self.checked_at, "ckan_last_refreshed": self.ckan_last_modified}

    def close(self) -> None:
        for f in self.feeds:
            f.close()
