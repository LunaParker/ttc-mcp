import json
import os
import subprocess
import sys

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "probe", "version": "0"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def _send(proc, msg):
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def _reply(proc, want_id):
    while True:
        line = proc.stdout.readline()
        assert line, "server closed stdout before replying"
        msg = json.loads(line)
        if msg.get("id") == want_id:
            return msg


def test_stdio_transport_speaks_only_json_rpc_on_stdout(tmp_path):
    env = {**os.environ, "TTC_MCP_SKIP_REFRESH": "1", "TTC_MCP_CACHE": str(tmp_path)}
    proc = subprocess.Popen([sys.executable, "-m", "ttc_mcp.server", "--transport", "stdio"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        _send(proc, INIT)
        init = _reply(proc, 1)
        assert init["result"]["serverInfo"]["name"] == "ttc-mcp"
        _send(proc, INITIALIZED)
        _send(proc, LIST)
        names = {t["name"] for t in _reply(proc, 2)["result"]["tools"]}
        assert {"find_stops", "nearest_stops", "stop", "station", "routes", "route_stops", "service_hours",
                "scheduled_departures", "arrivals", "next_trains", "vehicles", "feed_status", "alerts", "service_status", "detours", "service_changes", "route_shape"} <= names
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
    assert "Traceback" not in proc.stderr.read()
