"""Boots the real ttc-mcp process over streamable HTTP once per session, against the local store and the live TTC endpoints."""
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _boot():
    port = _free_port()
    env = {**os.environ, "TTC_MCP_SKIP_REFRESH": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "ttc_mcp.server", "--transport", "http", "--host", "127.0.0.1", "--port", str(port)],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early:\n{proc.stderr.read()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("server did not open its port within 30 s")
    try:
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def server_url():
    """One server for the whole session; its caches warm up as the modules run."""
    with _boot() as url:
        yield url


@pytest.fixture
def fresh_server_url():
    """A server booted for this test alone, for invariants about process start."""
    with _boot() as url:
        yield url


@pytest.fixture
def anyio_backend():
    return "asyncio"
