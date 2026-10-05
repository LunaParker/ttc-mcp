from pathlib import Path

from ttc_mcp import __version__
from ttc_mcp.config import settings


def test_defaults_point_at_user_cache_and_name_the_project(monkeypatch):
    monkeypatch.delenv("TTC_MCP_CACHE", raising=False)
    monkeypatch.delenv("TTC_MCP_UA", raising=False)
    monkeypatch.delenv("TTC_MCP_SKIP_REFRESH", raising=False)
    s = settings()
    assert s.cache_dir == Path.home() / ".cache" / "ttc-mcp"
    assert s.user_agent == f"ttc-mcp/{__version__} (+https://github.com/LunaParker/ttc-mcp)"
    assert s.skip_refresh is False
    assert s.tz == "America/Toronto"


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("TTC_MCP_CACHE", str(tmp_path))
    monkeypatch.setenv("TTC_MCP_UA", "custom/1")
    monkeypatch.setenv("TTC_MCP_SKIP_REFRESH", "1")
    s = settings()
    assert s.cache_dir == tmp_path and s.user_agent == "custom/1" and s.skip_refresh is True
