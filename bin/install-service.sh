#!/bin/bash
# Install systemd service for Factorio Agent.
#
# Usage: ./bin/install-service.sh [--user]
#   --user: Install as user service (default: system service)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FACTORIO_DIR="${FACTORIO_DIR:-$HOME/factorio}"
SERVICE_NAME="factorio-agent"

# Parse args
USER_SERVICE=false
for arg in "$@"; do
    if [[ "$arg" == "--user" ]]; then
        USER_SERVICE=true
    fi
done

# Detect user/group
USER_NAME=$(whoami)
GROUP_NAME=$(id -gn)

# Create service file from template
SERVICE_FILE="$PROJECT_DIR/config/factorio-agent.service"
INSTALL_PATH=""

sed -e "s|%USER%|$USER_NAME|g" \
    -e "s|%GROUP%|$GROUP_NAME|g" \
    -e "s|%PROJECT_DIR%|$PROJECT_DIR|g" \
    -e "s|%FACTORIO_DIR%|$FACTORIO_DIR|g" \
    "$PROJECT_DIR/config/factorio-agent.service.template" > "$SERVICE_FILE"

# Create default save if not exists
SAVE_FILE="$PROJECT_DIR/saves/agent.zip"
if [[ ! -f "$SAVE_FILE" ]]; then
    echo "Creating default save..."
    "$FACTORIO_DIR/bin/x64/factorio" \
        --create "$SAVE_FILE" \
        --map-gen-settings "$PROJECT_DIR/config/map-gen-settings.json"
fi

if [[ "$USER_SERVICE" == true ]]; then
    # User service
    SYSTEMD_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SYSTEMD_DIR"
    INSTALL_PATH="$SYSTEMD_DIR/$SERVICE_NAME.service"
    cp "$SERVICE_FILE" "$INSTALL_PATH"
    
    echo "Installed user service: $INSTALL_PATH"
    echo ""
    echo "Commands:"
    echo "  systemctl --user daemon-reload"
    echo "  systemctl --user start $SERVICE_NAME"
    echo "  systemctl --user stop $SERVICE_NAME"
    echo "  systemctl --user status $SERVICE_NAME"
    echo "  systemctl --user enable $SERVICE_NAME  # auto-start on login"
else
    # System service (requires sudo)
    INSTALL_PATH="/etc/systemd/system/$SERVICE_NAME.service"
    echo "Installing system service (requires sudo)..."
    sudo cp "$SERVICE_FILE" "$INSTALL_PATH"
    sudo systemctl daemon-reload
    
    echo "Installed system service: $INSTALL_PATH"
    echo ""
    echo "Commands:"
    echo "  sudo systemctl start $SERVICE_NAME"
    echo "  sudo systemctl stop $SERVICE_NAME"
    echo "  sudo systemctl status $SERVICE_NAME"
    echo "  sudo systemctl enable $SERVICE_NAME  # auto-start on boot"
fi

echo ""
echo "Service installed successfully!"