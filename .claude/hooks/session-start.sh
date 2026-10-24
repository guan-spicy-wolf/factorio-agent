#!/bin/bash
# SessionStart hook: make `uv run pytest` / ruff work and have a Factorio headless
# binary + data dump ready. Only runs in Claude Code cloud sessions.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"
uv sync --quiet

# Factorio headless: free download, needs factorio.com + dl.factorio.com allowed.
# Failure here must not block the session; the serverless parts of fgen still work.
if bin/headless.sh install && bin/headless.sh dump >/dev/null; then
  echo "export FACTORIO_DIR=\"${FACTORIO_DIR:-$HOME/factorio}\"" >> "$CLAUDE_ENV_FILE"
  echo "export FACTORIO_DATA_DUMP=\"${FACTORIO_DIR:-$HOME/factorio}/script-output/data-raw-dump.json\"" >> "$CLAUDE_ENV_FILE"
else
  echo "warning: factorio headless not available (network policy?); continuing without it" >&2
fi
