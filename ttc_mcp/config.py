from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ttc_mcp import __version__

DEFAULT_UA = f"ttc-mcp/{__version__} (+https://github.com/LunaParker/ttc-mcp)"


@dataclass(frozen=True)
class Settings:
    cache_dir: Path
    user_agent: str
    skip_refresh: bool
    tz: str = "America/Toronto"


def settings() -> Settings:
    return Settings(
        cache_dir=Path(os.environ.get("TTC_MCP_CACHE") or Path.home() / ".cache" / "ttc-mcp").expanduser(),
        user_agent=os.environ.get("TTC_MCP_UA") or DEFAULT_UA,
        skip_refresh=os.environ.get("TTC_MCP_SKIP_REFRESH", "") in ("1", "true", "yes"),
    )
