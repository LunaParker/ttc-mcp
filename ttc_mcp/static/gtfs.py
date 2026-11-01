from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator

RAIL = {"1", "2", "4", "5", "6"}
LRT = {"5", "6"}


def secs(hms: str) -> int:
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def series_of(short: str) -> str:
    if not short.isdigit():
        return "other"
    n = int(short)
    if n < 7:
        return "rail"
    if 300 <= n < 400:
        return "blue-night"
    if 400 <= n < 500:
        return "community"
    if 500 <= n < 600:
        return "streetcar"
    if 900 <= n < 1000:
        return "express"
    return "bus"


def mode_of(route_type: int, short: str) -> str:
    if route_type == 1:
        return "subway"
    if short in LRT:
        return "lrt"
    if route_type == 0:
        return "streetcar"
    if route_type == 3:
        return "bus"
    return "other"


def member(z: zipfile.ZipFile, name: str) -> str:
    """A GTFS file's path in the archive: at the root, or inside one top folder when exactly one holds it (spec 11, 2026-11-01)."""
    names = z.namelist()
    if name in names:
        return name
    nested = [n for n in names if n.count("/") == 1 and n.endswith("/" + name)]
    return nested[0] if len(nested) == 1 else name


def has(z: zipfile.ZipFile, name: str) -> bool:
    return member(z, name) in z.namelist()


def read_rows(z: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    with z.open(member(z, name)) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
