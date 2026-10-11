import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from fastmcp.exceptions import AuthorizationError

from ttc_mcp import gate, server
from ttc_mcp.config import Gate

GATE = Gate("client-id-test", "secret-test", "https://ttc.example", frozenset({"owner@example.com"}), "jwt-test", None, None)
CLOUD_VARS = ("GOOGLE_CLOUD_PROJECT", "STORAGE_ENCRYPTION_KEY", "TTC_MCP_STATIC_BUCKET")


async def _ok(context):
    return "ok"


@pytest.mark.anyio
@pytest.mark.parametrize("claims, allowed", [
    ({"email": "Owner@Example.com", "email_verified": True}, True),
    ({"email": "owner@example.com", "email_verified": "true"}, True),
    ({"email": "owner@example.com", "email_verified": False}, False),
    ({"email": "stranger@example.com", "email_verified": True}, False),
    ({}, False),
])
async def test_the_allowlist_admits_verified_listed_emails_only(monkeypatch, claims, allowed):
    monkeypatch.setattr(gate, "get_access_token", lambda: SimpleNamespace(claims=claims))
    mw = gate.EmailAllowlist(GATE.allowed_emails)
    if allowed:
        assert await mw.on_request(SimpleNamespace(), _ok) == "ok"
    else:
        with pytest.raises(AuthorizationError, match="not authorized"):
            await mw.on_request(SimpleNamespace(), _ok)


@pytest.mark.anyio
async def test_a_request_without_a_token_is_refused(monkeypatch):
    monkeypatch.setattr(gate, "get_access_token", lambda: None)
    with pytest.raises(AuthorizationError, match="your account"):
        await gate.EmailAllowlist(GATE.allowed_emails).on_request(SimpleNamespace(), _ok)


def test_build_auth_is_a_google_provider_on_the_base_url_with_memory_state_when_no_project():
    from fastmcp.server.auth.providers.google import GoogleProvider
    auth = gate.build_auth(GATE)
    assert isinstance(auth, GoogleProvider) and str(auth.base_url).rstrip("/") == "https://ttc.example"
    assert gate.client_storage(GATE) is None


def test_client_storage_refuses_a_project_without_a_key_before_touching_firestore(monkeypatch):
    monkeypatch.setitem(sys.modules, "key_value.aio.stores.firestore", None)  # any import of the store fails loudly
    with pytest.raises(RuntimeError, match="STORAGE_ENCRYPTION_KEY"):
        gate.client_storage(replace(GATE, firestore_project="project-test"))


def test_create_app_gates_only_when_asked():
    plain = server.create_app()
    gated = server.create_app(gate=GATE)
    assert plain.auth is None and gated.auth is not None
    assert any(isinstance(m, gate.EmailAllowlist) for m in gated.middleware) and not any(isinstance(m, gate.EmailAllowlist) for m in plain.middleware)


def test_a_misconfigured_gate_stops_the_http_server_before_the_ckan_check(monkeypatch, tmp_path):
    monkeypatch.setenv("TTC_MCP_CACHE", str(tmp_path))
    for k in ("TTC_MCP_SKIP_REFRESH", "GOOGLE_OAUTH_CLIENT_SECRET", "BASE_URL", "JWT_SIGNING_KEY", "ALLOWED_EMAILS", *CLOUD_VARS):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "client-id-test")
    monkeypatch.setattr(server, "ensure_static", lambda *args, **kwargs: pytest.fail("CKAN was checked before the gate"))
    with pytest.raises(RuntimeError, match="GOOGLE_OAUTH_CLIENT_ID is set but"):
        server.main(["--transport", "http"])


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(tmp_path, **gate_vars):
    """The developer's shell minus anything that would turn the gate, Firestore or the bucket on; the test names what it wants."""
    env = {k: v for k, v in os.environ.items() if k not in CLOUD_VARS and not k.startswith("GOOGLE_OAUTH")}
    return {**env, "TTC_MCP_SKIP_REFRESH": "1", "TTC_MCP_CACHE": str(tmp_path), **gate_vars}


def _boot(env, port):
    proc = subprocess.Popen([sys.executable, "-m", "ttc_mcp.server", "--transport", "http", "--host", "127.0.0.1", "--port", str(port)],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early:\n{proc.stderr.read()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proc
        except OSError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("server did not open its port")


LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_the_gated_http_server_refuses_an_unauthenticated_call_and_publishes_its_metadata(tmp_path):
    port = _free_port()
    env = _env(tmp_path, GOOGLE_OAUTH_CLIENT_ID="client-id-test", GOOGLE_OAUTH_CLIENT_SECRET="secret-test", BASE_URL=f"http://127.0.0.1:{port}",
               JWT_SIGNING_KEY="jwt-test", ALLOWED_EMAILS="owner@example.com")
    proc = _boot(env, port)
    try:
        r = httpx.post(f"http://127.0.0.1:{port}/mcp", json=LIST, headers=HEADERS)
        assert r.status_code == 401 and "www-authenticate" in {k.lower() for k in r.headers}
        meta = httpx.get(f"http://127.0.0.1:{port}/.well-known/oauth-protected-resource/mcp")
        assert meta.status_code == 200 and "authorization_servers" in meta.json()
    finally:
        proc.terminate()
        proc.wait(10)
    assert "Traceback" not in proc.stderr.read()


def test_an_ungated_http_server_answers_tools_list(tmp_path):
    port = _free_port()
    proc = _boot(_env(tmp_path), port)
    try:
        r = httpx.post(f"http://127.0.0.1:{port}/mcp", json=LIST, headers=HEADERS)
        assert r.status_code == 200 and {t["name"] for t in json.loads(r.text)["result"]["tools"]} >= {"connections", "feed_status"}
    finally:
        proc.terminate()
        proc.wait(10)
