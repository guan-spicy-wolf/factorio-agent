#!/usr/bin/env bash
# Commit a Factorio save (zip) to the orphan `saves` branch and push it, so a save made
# in a throwaway cloud container survives and a desktop client can download it from
# GitHub. The main branches never carry save files (`saves/` is gitignored).
#
# Usage: bin/publish-save.sh <save.zip> [message]
#   The file lands at <branch>/<basename>; a README on the branch lists the mod set
#   and version a client needs to open them.
# Env: SAVES_BRANCH (default saves), SAVES_REMOTE (default origin)
set -euo pipefail

SAVE="${1:?usage: bin/publish-save.sh <save.zip> [message]}"
MSG="${2:-add $(basename "$SAVE")}"
BRANCH="${SAVES_BRANCH:-saves}"
REMOTE="${SAVES_REMOTE:-origin}"
[[ -s "$SAVE" ]] || { echo "no such save: $SAVE" >&2; exit 1; }

ROOT="$(git rev-parse --show-toplevel)"
WT="$(mktemp -d)"
trap 'cd "$ROOT"; git worktree remove --force "$WT" 2>/dev/null || true' EXIT

if git -C "$ROOT" fetch -q "$REMOTE" "$BRANCH" 2>/dev/null; then
    git -C "$ROOT" worktree add -q "$WT" "$REMOTE/$BRANCH"
    git -C "$WT" checkout -q -B "$BRANCH"
else
    git -C "$ROOT" worktree add -q --detach "$WT"
    git -C "$WT" checkout -q --orphan "$BRANCH"
    git -C "$WT" rm -rfq . >/dev/null 2>&1 || true
    cat > "$WT/README.md" <<EOF
# saves

Factorio saves produced by \`fgen\` in cloud sessions (headless $(cat "$ROOT/.factorio-version" 2>/dev/null || echo 2.1.23)).
Mods enabled: base, space-age, quality, elevated-rails, recycler — the client must have the same set
(Space Age DLC) to load them. Download a zip and drop it into the client's \`saves/\` folder.
EOF
fi

cp "$SAVE" "$WT/$(basename "$SAVE")"
git -C "$WT" add -A
git -C "$WT" commit -q -m "$MSG"
git -C "$WT" push -u "$REMOTE" "$BRANCH"
echo "pushed $(basename "$SAVE") to $REMOTE/$BRANCH ($(du -h "$SAVE" | cut -f1))"
