#!/usr/bin/env bash
# Build the image, build the fixture store inside it, serve it, and ask for the tool list. Exit 0 only when all of that works.
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE=${IMAGE:-ttc-mcp:smoke}
VOL=ttc-mcp-smoke-$$
PORT=${PORT:-18080}
cleanup() { docker rm -f ttc-mcp-smoke >/dev/null 2>&1 || true; docker volume rm "$VOL" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker build -t "$IMAGE" .
# The store's per-thread connections and FastMCP's threadpool rely on a serialized sqlite3 (threadsafety 3).
TS=$(docker run --rm "$IMAGE" /app/.venv/bin/python -c 'import sqlite3; print("sqlite3.threadsafety", sqlite3.threadsafety, sqlite3.sqlite_version)')
echo "image smoke: $TS"
[[ "$TS" == "sqlite3.threadsafety 3 "* ]] || { echo "sqlite3 in the image is not serialized"; exit 1; }
docker volume create "$VOL" >/dev/null
docker run --rm -v "$VOL:/data" -v "$PWD/tests/fixtures:/fx:ro" "$IMAGE" \
  /app/.venv/bin/ttc-mcp-build --from-zip /fx/gtfs-mini.zip --surface-zip /fx/surface-mini.zip --no-checks --no-ttcapi
docker run -d --name ttc-mcp-smoke -v "$VOL:/data" -p "$PORT:8080" "$IMAGE" >/dev/null
for _ in $(seq 1 60); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/mcp"; then break; fi
  sleep 0.5
done
BODY='{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
curl -sf -X POST "http://127.0.0.1:$PORT/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$BODY" \
  | python3 -c '
import json, sys
tools = {t["name"] for t in json.load(sys.stdin)["result"]["tools"]}
want = {"find_stops", "nearest_stops", "stop", "station", "routes", "route_stops", "service_hours", "scheduled_departures", "arrivals",
        "next_trains", "vehicles", "feed_status", "alerts", "service_status", "detours", "service_changes", "route_shape", "accessibility", "connections"}
missing = want - tools
assert not missing, f"missing tools: {missing}"
print(f"image smoke: {len(tools)} tools")'
docker logs ttc-mcp-smoke 2>&1 | grep "Traceback" >/dev/null && { echo "traceback in server log"; docker logs ttc-mcp-smoke; exit 1; }
echo "image smoke: PASS"
