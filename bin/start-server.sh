#!/usr/bin/env bash
# Start Factorio headless server with RCON enabled.
#
# Usage: ./bin/start-server.sh [rcon_password] [rcon_port]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER_DIR="${PROJECT_DIR}/factorio-server"
SAVE_FILE="${PROJECT_DIR}/saves/agent-dev.zip"
RCON_PASSWORD="${1:-changeme}"
RCON_PORT="${2:-27015}"
PID_FILE="${PROJECT_DIR}/factorio.pid"
LOG_FILE="${PROJECT_DIR}/factorio.log"

if [[ ! -d "$SERVER_DIR" ]]; then
    echo "ERROR: Factorio server not found at ${SERVER_DIR}"
    echo "Run ./bin/install-factorio.sh first."
    exit 1
fi

if [[ ! -f "$SAVE_FILE" ]]; then
    echo "ERROR: Save file not found at ${SAVE_FILE}"
    echo "Run ./bin/install-factorio.sh first."
    exit 1
fi

# Check if already running
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Server already running (PID ${OLD_PID}). Stop it first with ./bin/stop-server.sh"
        exit 1
    else
        rm -f "$PID_FILE"
    fi
fi

echo "Starting Factorio server..."
echo "  RCON port: ${RCON_PORT}"
echo "  Save: ${SAVE_FILE}"
echo "  Log: ${LOG_FILE}"

"${SERVER_DIR}/bin/x64/factorio" \
    --start-server "$SAVE_FILE" \
    --server-settings "${PROJECT_DIR}/config/server-settings.json" \
    --rcon-port "$RCON_PORT" \
    --rcon-password "$RCON_PASSWORD" \
    > "$LOG_FILE" 2>&1 &

SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"
echo "Server started (PID ${SERVER_PID})"

# Wait for RCON to become available
echo -n "Waiting for RCON..."
for i in $(seq 1 30); do
    if (echo > /dev/tcp/127.0.0.1/"$RCON_PORT") 2>/dev/null; then
        echo " ready!"
        exit 0
    fi
    echo -n "."
    sleep 1
done

echo " timeout!"
echo "Server may still be starting. Check ${LOG_FILE} for details."
exit 1
