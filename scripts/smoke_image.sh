#!/usr/bin/env bash
# Build the image, build the fixture store inside it, serve it, list the tools and call one. Exit 0 only when all of that works.
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE=${IMAGE:-ttc-mcp:smoke}
NAME=ttc-mcp-smoke-$$
VOL=ttc-mcp-smoke-$$
PORT=${PORT:-18080}
cleanup() {
  local rc=$?
  if [[ $rc -ne 0 ]] && docker container inspect "$NAME" >/dev/null 2>&1; then
    echo "--- $NAME log (last 40 lines)"; docker logs "$NAME" 2>&1 | tail -40 || true
  fi
  docker rm -f "$NAME" >/dev/null 2>&1 || true; docker volume rm "$VOL" >/dev/null 2>&1 || true
}
trap cleanup EXIT
mcp() { curl -sf -X POST "http://127.0.0.1:$PORT/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$1"; }

docker build -t "$IMAGE" .
# The store's per-thread connections and FastMCP's threadpool rely on a serialized sqlite3 (threadsafety 3).
TS=$(docker run --rm "$IMAGE" /app/.venv/bin/python -c 'import sqlite3; print("sqlite3.threadsafety", sqlite3.threadsafety, sqlite3.sqlite_version)')
echo "image smoke: $TS"
[[ "$TS" == "sqlite3.threadsafety 3 "* ]] || { echo "sqlite3 in the image is not serialized"; exit 1; }
docker volume create "$VOL" >/dev/null
docker run --rm -v "$VOL:/data" -v "$PWD/tests/fixtures:/fx:ro" "$IMAGE" \
  /app/.venv/bin/ttc-mcp-build --from-zip /fx/gtfs-mini.zip --surface-zip /fx/surface-mini.zip --no-checks --no-ttcapi
docker run -d --name "$NAME" -v "$VOL:/data" -p "$PORT:8080" "$IMAGE" >/dev/null
for _ in $(seq 1 60); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/mcp"; then break; fi
  [[ "$(docker inspect -f '{{.State.Running}}' "$NAME")" == true ]] || { echo "server container exited before it answered"; exit 1; }
  sleep 0.5
done
mcp '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 -c '
import json, sys
tools = {t["name"] for t in json.load(sys.stdin)["result"]["tools"]}
want = {"find_stops", "nearest_stops", "stop", "station", "routes", "route_stops", "service_hours", "scheduled_departures", "arrivals",
        "next_trains", "vehicles", "feed_status", "alerts", "service_status", "detours", "service_changes", "route_shape", "accessibility", "connections"}
assert tools == want, f"missing tools: {want - tools}, unexpected tools: {tools - want}"
print(f"image smoke: {len(tools)} tools")'
# tools/list only proves the server started; a static tool proves it serves the store under /data.
mcp '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"routes","arguments":{}}}' | python3 -c '
import json, sys
res = json.load(sys.stdin)["result"]
out = res.get("structuredContent") or {}
err = out.get("error") or (res.get("content") if res.get("isError") else None)
assert not err, f"routes answered an error: {err}"
names = {r["route"] for r in out.get("routes", [])}
assert "504" in names, f"fixture route 504 missing from routes: {sorted(names)}"
print(f"image smoke: routes served {len(names)} routes from /data, 504 among them")'
docker logs "$NAME" 2>&1 | grep "Traceback" >/dev/null && { echo "traceback in server log"; docker logs "$NAME"; exit 1; }
echo "image smoke: PASS"
