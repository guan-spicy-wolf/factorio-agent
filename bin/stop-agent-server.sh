#!/usr/bin/env bash
# Stop the Factorio agent server.
#
# Usage: ./bin/stop-agent-server.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_FILE="${PROJECT_DIR}/factorio.pid"

if [[ ! -f "$PID_FILE" ]]; then
    echo "No PID file found at ${PID_FILE}"
    echo "Server may not be running or was started manually."
    exit 0
fi

PID=$(cat "$PID_FILE")

if ! kill -0 "$PID" 2>/dev/null; then
    echo "Process ${PID} is not running."
    rm -f "$PID_FILE"
    exit 0
fi

echo "Stopping Factorio server (PID ${PID})..."
kill "$PID"

# Wait for process to terminate
for i in $(seq 1 10); do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "Server stopped."
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

# Force kill if still running
echo "Force killing..."
kill -9 "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "Server stopped (force)."