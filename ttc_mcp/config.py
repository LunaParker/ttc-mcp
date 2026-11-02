from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ttc_mcp import __version__

DEFAULT_UA = f"ttc-mcp/{__version__} (+https://github.com/LunaParker/ttc-mcp)"
GATE_VARS = ("GOOGLE_OAUTH_CLIENT_SECRET", "BASE_URL", "JWT_SIGNING_KEY", "ALLOWED_EMAILS")
SECRET_VARS = ("GOOGLE_OAUTH_CLIENT_SECRET", "JWT_SIGNING_KEY", "STORAGE_ENCRYPTION_KEY")


@dataclass(frozen=True)
class Settings:
    cache_dir: Path
    user_agent: str
    skip_refresh: bool
    static_bucket: str | None = None
    pull_s: int = 6 * 3600
    tz: str = "America/Toronto"


@dataclass(frozen=True)
class Gate:
    """Spec 6.9: the in-process Google OAuth gate. OAuth state lives in Firestore when a project is named, else in FastMCP's
    encrypted file store under FASTMCP_HOME (key derived from the JWT signing key)."""
    client_id: str
    client_secret: str
    base_url: str
    allowed_emails: frozenset[str]
    jwt_signing_key: str
    firestore_project: str | None
    storage_key: str | None


def settings() -> Settings:
    pull_s = int(os.environ.get("TTC_MCP_PULL_S") or 6 * 3600)
    if pull_s < 60:
        raise RuntimeError(f"TTC_MCP_PULL_S is {pull_s}; the bucket pull interval must be at least 60 seconds")
    return Settings(
        cache_dir=Path(os.environ.get("TTC_MCP_CACHE") or Path.home() / ".cache" / "ttc-mcp").expanduser(),
        user_agent=os.environ.get("TTC_MCP_UA") or DEFAULT_UA,
        skip_refresh=os.environ.get("TTC_MCP_SKIP_REFRESH", "") in ("1", "true", "yes"),
        static_bucket=os.environ.get("TTC_MCP_STATIC_BUCKET") or None,
        pull_s=pull_s,
    )


def allowed_emails(raw: str) -> frozenset[str]:
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def _secret(env: Mapping[str, str], var: str) -> str | None:
    """A secret set inline or read from the file <var>_FILE names (stripped); both, an unreadable file or an empty one refuses."""
    inline, path = env.get(var) or None, env.get(f"{var}_FILE") or None
    if inline and path:
        raise RuntimeError(f"{var} and {var}_FILE are both set; set one of them")
    if not path:
        return inline
    try:
        value = Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError as e:
        raise RuntimeError(f"{var}_FILE ({path}) cannot be read: {e.strerror or type(e).__name__}") from None
    if not value:
        raise RuntimeError(f"{var}_FILE ({path}) is empty")
    return value


def gate_settings(env: Mapping[str, str] | None = None) -> Gate | None:
    """GOOGLE_OAUTH_CLIENT_ID turns the gate on; then every other variable is required (a secret inline or as <name>_FILE),
    so a half-configured gate never starts."""
    env = os.environ if env is None else env
    client_id = env.get("GOOGLE_OAUTH_CLIENT_ID")
    if not client_id:
        return None
    secrets = {k: _secret(env, k) for k in SECRET_VARS}
    v = {k: (secrets[k] if k in secrets else env.get(k)) or "" for k in GATE_VARS}
    missing = [k for k in GATE_VARS if not v[k]]
    if missing:
        raise RuntimeError(f"GOOGLE_OAUTH_CLIENT_ID is set but {', '.join(missing)} is not; the gate needs all of {', '.join(GATE_VARS)} "
                           "(a secret may also come from a file named by <name>_FILE)")
    emails = allowed_emails(v["ALLOWED_EMAILS"])
    if not emails:
        raise RuntimeError("ALLOWED_EMAILS names nobody; refusing to gate a server that would admit any Google account")
    project, key = env.get("GOOGLE_CLOUD_PROJECT") or None, secrets["STORAGE_ENCRYPTION_KEY"]
    if project and not key:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is set but STORAGE_ENCRYPTION_KEY is not; OAuth state in Firestore must be encrypted")
    return Gate(client_id, v["GOOGLE_OAUTH_CLIENT_SECRET"], v["BASE_URL"].rstrip("/"), emails, v["JWT_SIGNING_KEY"], project, key)
