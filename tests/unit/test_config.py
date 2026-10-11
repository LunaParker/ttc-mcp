from pathlib import Path

import pytest

from ttc_mcp import __version__, config
from ttc_mcp.config import settings

FULL = {"GOOGLE_OAUTH_CLIENT_ID": "client-id-test", "GOOGLE_OAUTH_CLIENT_SECRET": "secret-test", "BASE_URL": "https://ttc.example/",
        "JWT_SIGNING_KEY": "jwt-test", "ALLOWED_EMAILS": " Owner@Example.com, other@example.com "}


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


def test_no_client_id_means_no_gate():
    assert config.gate_settings({}) is None


def test_the_gate_reads_every_variable_and_normalises_emails_and_the_base_url():
    g = config.gate_settings(FULL)
    assert (g.client_id, g.client_secret, g.base_url, g.jwt_signing_key) == ("client-id-test", "secret-test", "https://ttc.example", "jwt-test")
    assert g.allowed_emails == frozenset({"owner@example.com", "other@example.com"}) and g.firestore_project is None and g.storage_key is None


@pytest.mark.parametrize("missing", ["GOOGLE_OAUTH_CLIENT_SECRET", "BASE_URL", "JWT_SIGNING_KEY", "ALLOWED_EMAILS"])
def test_a_gate_without_one_of_its_variables_refuses_to_start_and_names_it(missing):
    with pytest.raises(RuntimeError, match=missing):
        config.gate_settings({k: v for k, v in FULL.items() if k != missing})


def test_an_allowlist_of_only_separators_is_empty_and_refused():
    assert config.allowed_emails(" , ,") == frozenset()
    with pytest.raises(RuntimeError, match="ALLOWED_EMAILS"):
        config.gate_settings({**FULL, "ALLOWED_EMAILS": " , "})


def test_firestore_state_needs_the_encryption_key():
    with pytest.raises(RuntimeError, match="STORAGE_ENCRYPTION_KEY"):
        config.gate_settings({**FULL, "GOOGLE_CLOUD_PROJECT": "proj"})
    g = config.gate_settings({**FULL, "GOOGLE_CLOUD_PROJECT": "proj", "STORAGE_ENCRYPTION_KEY": "fernet-test"})
    assert (g.firestore_project, g.storage_key) == ("proj", "fernet-test")


def test_settings_read_the_bucket_and_the_pull_interval(monkeypatch):
    monkeypatch.delenv("TTC_MCP_STATIC_BUCKET", raising=False)
    monkeypatch.delenv("TTC_MCP_PULL_S", raising=False)
    assert (config.settings().static_bucket, config.settings().pull_s) == (None, 21600)
    monkeypatch.setenv("TTC_MCP_STATIC_BUCKET", "ttc-mcp-static-test")
    monkeypatch.setenv("TTC_MCP_PULL_S", "60")
    assert (config.settings().static_bucket, config.settings().pull_s) == ("ttc-mcp-static-test", 60)
