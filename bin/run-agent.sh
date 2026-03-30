#!/bin/bash
# Run the Factorio agent with a task.
#
# Usage: ./bin/run-agent.sh "task description"
#
# Environment variables (can be set in .env):
#   ANTHROPIC_API_KEY  - Required
#   RCON_HOST          - Default: 127.0.0.1
#   RCON_PORT          - Default: 27015
#   RCON_PASSWORD      - Default: changeme

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"

# Load .env if exists
if [[ -f ".env" ]]; then
    source .env
fi

# Check API key
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "Error: ANTHROPIC_API_KEY not set"
    echo ""
    echo "Create .env file with:"
    echo "  ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi

# Check server is running
if ! ss -tlnp 2>/dev/null | grep -q ":${RCON_PORT:-27015}"; then
    echo "Factorio server not running. Starting..."
    ./bin/factorio-agent.sh start
    sleep 2
fi

# Run agent
TASK="${1:-在地图上放置一个储物箱}"

echo "Running agent with task: $TASK"
echo ""

uv run python -m agent.run "$TASK"