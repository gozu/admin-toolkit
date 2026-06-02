#!/usr/bin/env bash
set -euo pipefail

# Stream TAM server backend.log while deploying.
# Usage: bash scripts/deploy-with-log.sh <TAM_HOST> [PORT]

TAM_HOST="${1:?Usage: $0 <TAM_HOST> [PORT]}"
PORT="${2:-9876}"
URL="http://${TAM_HOST}:${PORT}/"
LOG_FILE="tam-restart-$(date +%Y%m%d-%H%M%S).log"

echo "=== Connecting to ${URL} ==="
curl --no-buffer -s "$URL" | tee "$LOG_FILE" &
CURL_PID=$!

# Give curl a moment to connect
sleep 1
if ! kill -0 "$CURL_PID" 2>/dev/null; then
    echo "[ERROR] Could not connect to ${URL}. Is stream-log.py running on the TAM server?"
    exit 1
fi

echo ""
echo "=== Running make deploy ==="
make deploy COMMIT_MSG="Deploy update" || true

echo ""
echo "=== Waiting 5s for trailing log lines ==="
sleep 5

kill "$CURL_PID" 2>/dev/null || true
wait "$CURL_PID" 2>/dev/null || true

echo ""
echo "=== Done. Log saved to ${LOG_FILE} ==="
