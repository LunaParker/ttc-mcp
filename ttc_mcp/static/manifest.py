from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path

NAME = "manifest.json"


@dataclass
class FeedEntry:
    feed_version: str
    start: str
    end: str
    path: str
    etag: str | None
    source_url: str
    built_at: str
    surface_etag: str | None = None


@dataclass
class Manifest:
    """The loaded feeds plus the last CKAN check: when it ran (Toronto time) and the package's last_modified."""
    feeds: list[FeedEntry]
    checked_at: str | None = None
    ckan_last_modified: str | None = None

    @classmethod
    def load(cls, cache_dir: Path) -> "Manifest":
        p = Path(cache_dir) / NAME
        if not p.exists():
            return cls([])
        doc = json.loads(p.read_text())
        return cls([FeedEntry(**f) for f in doc.get("feeds", [])], doc.get("checked_at"), doc.get("ckan_last_modified"))

    def save(self, cache_dir: Path) -> None:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        tmp = Path(cache_dir) / (NAME + ".tmp")
        tmp.write_text(json.dumps({"feeds": [asdict(f) for f in self.feeds], "checked_at": self.checked_at,
                                   "ckan_last_modified": self.ckan_last_modified}, indent=1))
        tmp.replace(Path(cache_dir) / NAME)

    def upsert(self, entry: FeedEntry) -> None:
        self.feeds = [f for f in self.feeds if f.feed_version != entry.feed_version] + [entry]
        self.feeds.sort(key=lambda f: (f.start, f.feed_version))

    def prune(self, today: dt.date) -> None:
        cutoff = (today - dt.timedelta(days=1)).strftime("%Y%m%d")
        keep = [f for f in self.feeds if f.end >= cutoff]
        self.feeds = keep or self.feeds[-1:]

    def get(self, version: str) -> FeedEntry | None:
        return next((f for f in self.feeds if f.feed_version == version), None)
