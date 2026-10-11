"""Spec 6.9: the static store lives in a bucket. The Job pushes a finished build; the service pulls at cold start and on a timer."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Protocol

from ttc_mcp.static.manifest import NAME, Manifest


def _log(log, msg):
    (log or (lambda m: print(m, file=sys.stderr)))(msg)


class Blobs(Protocol):
    def read(self, name: str) -> bytes | None: ...
    def write(self, name: str, data: bytes) -> None: ...
    def size(self, name: str) -> int | None: ...
    def download(self, name: str, path: Path) -> bool: ...
    def upload(self, name: str, path: Path) -> None: ...


class MemoryBlobs:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def read(self, name: str) -> bytes | None:
        return self.objects.get(name)

    def write(self, name: str, data: bytes) -> None:
        self.objects[name] = data

    def size(self, name: str) -> int | None:
        return len(self.objects[name]) if name in self.objects else None

    def download(self, name: str, path: Path) -> bool:
        if name not in self.objects:
            return False
        path.write_bytes(self.objects[name])
        return True

    def upload(self, name: str, path: Path) -> None:
        self.objects[name] = path.read_bytes()


class GcsBlobs:
    """google-cloud-storage behind the protocol; the client is injectable so the wiring is testable without the cloud."""

    def __init__(self, bucket: str, client=None) -> None:
        if client is None:
            from google.cloud import storage
            client = storage.Client()
        self.bucket = client.bucket(bucket)

    def _blob(self, name: str):
        from google.api_core.exceptions import NotFound
        blob = self.bucket.blob(name)
        try:
            blob.reload()
        except NotFound:
            return None
        return blob

    def read(self, name: str) -> bytes | None:
        blob = self._blob(name)
        return blob.download_as_bytes() if blob else None

    def write(self, name: str, data: bytes) -> None:
        self.bucket.blob(name).upload_from_string(data)

    def size(self, name: str) -> int | None:
        blob = self._blob(name)
        return blob.size if blob else None

    def download(self, name: str, path: Path) -> bool:
        blob = self._blob(name)
        if blob is None:
            return False
        blob.download_to_filename(str(path))
        return True

    def upload(self, name: str, path: Path) -> None:
        self.bucket.blob(name).upload_from_filename(str(path))


def pull(cache_dir: Path, blobs: Blobs, log=None) -> Manifest | None:
    """Bring the bucket's manifest and the feeds it lists into cache_dir: same-size files are kept, a download lands in a .part file first,
    the manifest is written after the feeds, then static-*.sqlite files it no longer lists go. A feed missing from the bucket is logged."""
    cache_dir = Path(cache_dir)
    raw = blobs.read(NAME)
    if raw is None:
        _log(log, "[bucket] no manifest in the bucket; static tools will say the store is not built yet")
        return None
    manifest = Manifest.from_doc(json.loads(raw))
    cache_dir.mkdir(parents=True, exist_ok=True)
    for f in manifest.feeds:
        dest = cache_dir / f.path
        if dest.exists() and dest.stat().st_size == blobs.size(f.path):
            continue
        part = dest.with_suffix(".part")
        try:
            if not blobs.download(f.path, part):
                _log(log, f"[bucket] {f.path} is listed but missing from the bucket")
                continue
            part.replace(dest)
            _log(log, f"[bucket] pulled {f.path} ({dest.stat().st_size} bytes)")
        finally:
            part.unlink(missing_ok=True)
    manifest.save(cache_dir)
    listed = {f.path for f in manifest.feeds}
    for stale in cache_dir.glob("static-*.sqlite"):
        if stale.name not in listed:
            stale.unlink()
            _log(log, f"[bucket] removed {stale.name}, no longer in the manifest")
    return manifest


def push(cache_dir: Path, blobs: Blobs, log=None) -> list[str]:
    """Upload the feeds the local manifest lists (skipping same-size files), then the manifest itself, last."""
    cache_dir = Path(cache_dir)
    manifest, sent = Manifest.load(cache_dir), []
    for f in manifest.feeds:
        p = cache_dir / f.path
        if blobs.size(f.path) != p.stat().st_size:
            blobs.upload(f.path, p)
            sent.append(f.path)
            _log(log, f"[bucket] pushed {f.path} ({p.stat().st_size} bytes)")
    blobs.write(NAME, (cache_dir / NAME).read_bytes())
    return sent + [NAME]
