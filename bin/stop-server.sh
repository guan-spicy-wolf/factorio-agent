#!/usr/bin/env bash
# Stop Factorio headless server gracefully.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_FILE="${PROJECT_DIR}/factorio.pid"

if [[ ! -f "$PID_FILE" ]]; then
    echo "No PID file found. Server may not be running."
    exit 0
fi

SERVER_PID=$(cat "$PID_FILE")

if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "Process ${SERVER_PID} not running. Cleaning up PID file."
    rm -f "$PID_FILE"
    exit 0
fi

echo "Stopping Factorio server (PID ${SERVER_PID})..."
kill "$SERVER_PID"

# Wait for graceful shutdown
for i in $(seq 1 15); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "Server stopped."
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

echo "Server did not stop gracefully. Sending SIGKILL..."
kill -9 "$SERVER_PID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "Server killed."
