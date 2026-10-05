import sqlite3
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory) -> Path:
    from ttc_mcp.static.build import build_sqlite

    out = tmp_path_factory.mktemp("static") / "static-mini.sqlite"
    build_sqlite(FIX / "gtfs-mini.zip", out, FIX / "surface-mini.zip", source_url="fixture", source_etag='"fix"')
    return out


@pytest.fixture(scope="session")
def conn(fixture_db):
    c = sqlite3.connect(f"file:{fixture_db}?mode=ro", uri=True, check_same_thread=False)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"
