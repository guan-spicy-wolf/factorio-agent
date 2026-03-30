#!/usr/bin/env bash
# Start a fresh Factorio server for agent development.
# Creates new map, links mod, starts server with RCON.
#
# Usage: ./bin/start-agent-server.sh [--fresh]
#   --fresh: Force create new save (overwrite existing)
#
# Environment variables:
#   FACTORIO_DIR: Factorio installation directory (default: ~/factorio)
#   RCON_PASSWORD: RCON password (default: changeme)
#   RCON_PORT: RCON port (default: 27015)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FACTORIO_DIR="${FACTORIO_DIR:-$HOME/factorio}"
RCON_PASSWORD="${RCON_PASSWORD:-changeme}"
RCON_PORT="${RCON_PORT:-27015}"
SAVE_NAME="agent-dev"
PID_FILE="${PROJECT_DIR}/factorio.pid"
LOG_FILE="${PROJECT_DIR}/factorio.log"

# Parse args
FRESH_MODE=false
for arg in "$@"; do
    if [[ "$arg" == "--fresh" ]]; then
        FRESH_MODE=true
    fi
done

echo "=== Factorio Agent Server ==="
echo "Factorio: ${FACTORIO_DIR}"
echo "Project:  ${PROJECT_DIR}"
echo ""

# Check Factorio exists
if [[ ! -d "$FACTORIO_DIR" ]]; then
    echo "ERROR: Factorio not found at ${FACTORIO_DIR}"
    echo "Set FACTORIO_DIR environment variable to point to Factorio installation"
    exit 1
fi

FACTORIO_BIN="${FACTORIO_DIR}/bin/x64/factorio"
if [[ ! -x "$FACTORIO_BIN" ]]; then
    echo "ERROR: Factorio binary not found at ${FACTORIO_BIN}"
    exit 1
fi

# Stop existing server if running
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Stopping existing server (PID ${OLD_PID})..."
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
        rm -f "$PID_FILE"
    else
        rm -f "$PID_FILE"
    fi
fi

# Link mod to Factorio mods directory
MOD_SRC="${PROJECT_DIR}/mod"
MOD_LINK="${FACTORIO_DIR}/mods/factorio-agent"

echo "Linking mod..."
if [[ -L "$MOD_LINK" ]]; then
    # Update existing symlink
    ln -sfn "$MOD_SRC" "$MOD_LINK"
    echo "  Updated symlink: ${MOD_LINK} -> ${MOD_SRC}"
elif [[ -d "$MOD_LINK" ]]; then
    # Remove directory and create symlink
    rm -rf "$MOD_LINK"
    ln -s "$MOD_SRC" "$MOD_LINK"
    echo "  Created symlink: ${MOD_LINK} -> ${MOD_SRC}"
else
    ln -s "$MOD_SRC" "$MOD_LINK"
    echo "  Created symlink: ${MOD_LINK} -> ${MOD_SRC}"
fi

# Update mod-list.json to enable factorio-agent
MOD_LIST="${FACTORIO_DIR}/mods/mod-list.json"
if [[ -f "$MOD_LIST" ]]; then
    # Check if factorio-agent already in list
    if ! grep -q '"factorio-agent"' "$MOD_LIST" 2>/dev/null; then
        echo "Adding factorio-agent to mod-list.json..."
        # Simple append to mods array
        python3 -c "
import json
with open('$MOD_LIST', 'r') as f:
    data = json.load(f)
# Add factorio-agent if not present
for m in data.get('mods', []):
    if m.get('name') == 'factorio-agent':
        exit(0)
data.setdefault('mods', []).append({'name': 'factorio-agent', 'enabled': True})
with open('$MOD_LIST', 'w') as f:
    json.dump(data, f, indent=2)
"
        echo "  Added factorio-agent to mod list"
    else
        echo "  factorio-agent already in mod list"
    fi
fi

# Create save directory
SAVE_DIR="${PROJECT_DIR}/saves"
mkdir -p "$SAVE_DIR"

# Create or recreate save
SAVE_FILE="${SAVE_DIR}/${SAVE_NAME}.zip"
MAP_GEN_SETTINGS="${PROJECT_DIR}/config/map-gen-settings.json"

if [[ "$FRESH_MODE" == "true" ]] || [[ ! -f "$SAVE_FILE" ]]; then
    echo "Creating fresh save..."
    "$FACTORIO_BIN" \
        --create "$SAVE_FILE" \
        --map-gen-settings "$MAP_GEN_SETTINGS" \
        --map-settings "$MAP_GEN_SETTINGS" \
        > /dev/null 2>&1
    echo "  Created: ${SAVE_FILE}"
else
    echo "Using existing save: ${SAVE_FILE}"
fi

# Start server
echo ""
echo "Starting server..."
echo "  RCON port: ${RCON_PORT}"
echo "  RCON password: ${RCON_PASSWORD}"
echo "  Log: ${LOG_FILE}"

"$FACTORIO_BIN" \
    --start-server "$SAVE_FILE" \
    --server-settings "${PROJECT_DIR}/config/server-settings.json" \
    --rcon-port "$RCON_PORT" \
    --rcon-password "$RCON_PASSWORD" \
    --server-banlist "${PROJECT_DIR}/config/server-banlist.json" \
    --server-whitelist "${PROJECT_DIR}/config/server-whitelist.json" \
    --server-adminlist "${PROJECT_DIR}/config/server-adminlist.json" \
    > "$LOG_FILE" 2>&1 &

SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"
echo "  Started (PID ${SERVER_PID})"

# Wait for RCON
echo ""
echo -n "Waiting for RCON..."
for i in $(seq 1 30); do
    if (echo > /dev/tcp/127.0.0.1/"$RCON_PORT") 2>/dev/null; then
        echo " ready!"
        echo ""
        echo "=== Server ready ==="
        echo "Connect with: RCON ${RCON_PORT}, password '${RCON_PASSWORD}'"
        echo "Stop with: ./bin/stop-agent-server.sh"
        exit 0
    fi
    echo -n "."
    sleep 1
done

echo " timeout!"
echo "Check ${LOG_FILE} for details."
exit 1