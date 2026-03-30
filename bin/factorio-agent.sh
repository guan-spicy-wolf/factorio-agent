#!/bin/bash
# Factorio Agent server management script.
#
# Usage: ./bin/factorio-agent.sh <command> [options]
#
# Commands:
#   start       Start the server
#   stop        Stop the server
#   restart     Restart the server
#   status      Check server status
#   console     Attach to server console (RCON)
#   fresh       Create fresh save and restart
#   install     Install systemd service
#   logs        Tail server logs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FACTORIO_DIR="${FACTORIO_DIR:-$HOME/factorio}"
SAVE_NAME="${SAVE_NAME:-agent}"
RCON_PORT="${RCON_PORT:-27015}"
RCON_PASSWORD="${RCON_PASSWORD:-changeme}"
PID_FILE="$PROJECT_DIR/factorio.pid"
LOG_FILE="$PROJECT_DIR/factorio.log"

usage() {
    echo "Factorio Agent Server"
    echo ""
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Commands:"
    echo "  start       Start the server"
    echo "  stop        Stop the server"
    echo "  restart     Restart the server"
    echo "  status      Check server status"
    echo "  fresh       Create fresh save and start"
    echo "  install     Install systemd service"
    echo "  logs        Tail server logs"
    echo ""
    echo "Environment:"
    echo "  FACTORIO_DIR=$FACTORIO_DIR"
    echo "  SAVE_NAME=$SAVE_NAME"
    echo "  RCON_PORT=$RCON_PORT"
}

is_running() {
    if [[ -f "$PID_FILE" ]]; then
        local pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

cmd_start() {
    if is_running; then
        echo "Server already running (PID $(cat $PID_FILE))"
        return 0
    fi

    local save_file="$PROJECT_DIR/saves/${SAVE_NAME}.zip"
    if [[ ! -f "$save_file" ]]; then
        echo "Creating save: $save_file"
        "$FACTORIO_DIR/bin/x64/factorio" \
            --create "$save_file" \
            --map-gen-settings "$PROJECT_DIR/config/map-gen-settings.json"
    fi

    # Link mod
    local mod_link="$FACTORIO_DIR/mods/factorio-agent"
    ln -sfn "$PROJECT_DIR/mod" "$mod_link"

    echo "Starting server..."
    nohup "$FACTORIO_DIR/bin/x64/factorio" \
        --start-server "$save_file" \
        --rcon-port "$RCON_PORT" \
        --rcon-password "$RCON_PASSWORD" \
        --server-settings "$PROJECT_DIR/config/server-settings.json" \
        > "$LOG_FILE" 2>&1 &

    local pid=$!
    echo $pid > "$PID_FILE"
    
    sleep 2
    
    if kill -0 $pid 2>/dev/null; then
        echo "Server started (PID $pid, RCON port $RCON_PORT)"
    else
        echo "Server failed to start. Check $LOG_FILE"
        rm -f "$PID_FILE"
        return 1
    fi
}

cmd_stop() {
    if ! is_running; then
        echo "Server not running"
        rm -f "$PID_FILE"
        return 0
    fi

    local pid=$(cat "$PID_FILE")
    echo "Stopping server (PID $pid)..."
    kill "$pid" 2>/dev/null || true
    
    for i in $(seq 1 10); do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "Server stopped"
            rm -f "$PID_FILE"
            return 0
        fi
        sleep 1
    done

    echo "Force killing..."
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "Server stopped"
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

cmd_status() {
    if is_running; then
        echo "Server running (PID $(cat $PID_FILE))"
        echo "RCON: 127.0.0.1:$RCON_PORT"
        echo "Save: saves/${SAVE_NAME}.zip"
    else
        echo "Server not running"
    fi
}

cmd_fresh() {
    cmd_stop
    local save_file="$PROJECT_DIR/saves/${SAVE_NAME}.zip"
    rm -f "$save_file"
    echo "Creating fresh save..."
    "$FACTORIO_DIR/bin/x64/factorio" \
        --create "$save_file" \
        --map-gen-settings "$PROJECT_DIR/config/map-gen-settings.json"
    cmd_start
}

cmd_install() {
    exec "$PROJECT_DIR/bin/install-service.sh" "$@"
}

cmd_logs() {
    tail -f "$LOG_FILE"
}

# Main
case "${1:-}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    status)  cmd_status ;;
    fresh)   cmd_fresh ;;
    install) shift; cmd_install "$@" ;;
    logs)    cmd_logs ;;
    *)       usage ;;
esac