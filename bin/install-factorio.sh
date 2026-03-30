#!/usr/bin/env bash
# Install Factorio headless server and link the agent mod.
#
# Usage: ./bin/install-factorio.sh [version]
#   version: Factorio version to install (default: "stable")
#
# Prerequisites:
#   - FACTORIO_USERNAME and FACTORIO_TOKEN env vars, OR
#   - A pre-downloaded tarball at ./factorio-headless.tar.xz

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER_DIR="${PROJECT_DIR}/factorio-server"
VERSION="${1:-stable}"

echo "=== Factorio Agent: Server Installation ==="

# --- Download or locate tarball ---
TARBALL="${PROJECT_DIR}/factorio-headless.tar.xz"
if [[ ! -f "$TARBALL" ]]; then
    if [[ -z "${FACTORIO_USERNAME:-}" || -z "${FACTORIO_TOKEN:-}" ]]; then
        echo "ERROR: No tarball found at ${TARBALL}"
        echo "Either:"
        echo "  1. Set FACTORIO_USERNAME and FACTORIO_TOKEN env vars for auto-download"
        echo "  2. Download headless server manually and place at ${TARBALL}"
        echo ""
        echo "Download from: https://factorio.com/download (headless, Linux 64-bit)"
        exit 1
    fi

    echo "Downloading Factorio headless server (${VERSION})..."
    curl -L -o "$TARBALL" \
        "https://factorio.com/get-download/${VERSION}/headless/linux64?username=${FACTORIO_USERNAME}&token=${FACTORIO_TOKEN}"
fi

# --- Extract ---
echo "Extracting to ${SERVER_DIR}..."
rm -rf "$SERVER_DIR"
mkdir -p "$SERVER_DIR"
tar xf "$TARBALL" -C "$SERVER_DIR" --strip-components=1

# --- Link mod ---
MOD_DIR="${SERVER_DIR}/mods"
mkdir -p "$MOD_DIR"
ln -sfn "${PROJECT_DIR}/mod" "${MOD_DIR}/factorio-agent"
echo "Linked mod: ${MOD_DIR}/factorio-agent -> ${PROJECT_DIR}/mod"

# --- Create initial save ---
SAVE_DIR="${PROJECT_DIR}/saves"
mkdir -p "$SAVE_DIR"
SAVE_FILE="${SAVE_DIR}/agent-dev.zip"

if [[ ! -f "$SAVE_FILE" ]]; then
    echo "Creating initial save with map-gen-settings..."
    "${SERVER_DIR}/bin/x64/factorio" \
        --create "$SAVE_FILE" \
        --map-gen-settings "${PROJECT_DIR}/config/map-gen-settings.json"
    echo "Save created: ${SAVE_FILE}"
else
    echo "Save already exists: ${SAVE_FILE}"
fi

echo "=== Installation complete ==="
echo "Start the server with: ./bin/start-server.sh"
