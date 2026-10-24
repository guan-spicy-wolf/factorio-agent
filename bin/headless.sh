#!/usr/bin/env bash
# Download Factorio headless (pinned version), extract it, and dump data.raw.
#
# Usage: bin/headless.sh [install|dump|path]
#   install  download + extract to $FACTORIO_DIR (idempotent)
#   dump     run --dump-data with only the base mod enabled (the stock install enables
#            space-age, quality and elevated-rails, which change base prototypes, e.g.
#            recipe categories); prints the path of data-raw-dump.json
#   path     print the factorio binary path
#
# Env: FACTORIO_VERSION (default 2.0.76), FACTORIO_DIR (default ~/factorio)
# The headless build is a free download; no account is needed.
# Network: needs factorio.com and dl.factorio.com reachable.
set -euo pipefail

FACTORIO_VERSION="${FACTORIO_VERSION:-2.0.76}"
FACTORIO_DIR="${FACTORIO_DIR:-$HOME/factorio}"
BIN="$FACTORIO_DIR/bin/x64/factorio"
DL_DIR="${FACTORIO_DL_DIR:-$HOME/factorio-dl}"
TARBALL="$DL_DIR/factorio-headless_linux_${FACTORIO_VERSION}.tar.xz"
URL="https://factorio.com/get-download/${FACTORIO_VERSION}/headless/linux64"

installed_version() {
    [[ -x "$BIN" ]] && "$BIN" --version 2>/dev/null | head -1 | awk '{print $2}' || true
}

cmd_install() {
    if [[ "$(installed_version)" == "$FACTORIO_VERSION" ]]; then
        echo "factorio $FACTORIO_VERSION already installed at $FACTORIO_DIR"
        return 0
    fi
    mkdir -p "$DL_DIR" "$FACTORIO_DIR"
    if [[ ! -s "$TARBALL" ]]; then
        echo "downloading factorio headless $FACTORIO_VERSION ..."
        curl -fsSL --retry 3 -o "$TARBALL.part" "$URL"
        mv "$TARBALL.part" "$TARBALL"
    fi
    rm -rf "$FACTORIO_DIR"
    mkdir -p "$FACTORIO_DIR"
    tar xJf "$TARBALL" -C "$FACTORIO_DIR" --strip-components=1
    echo "installed: $("$BIN" --version | head -1)"
}

MODS_BASE="$FACTORIO_DIR/fgen-mods-base"

cmd_dump() {
    local out="$FACTORIO_DIR/script-output/data-raw-dump.json"
    local stamp="$out.mods"
    if [[ ! -s "$out" || "$(cat "$stamp" 2>/dev/null)" != "base" ]]; then
        mkdir -p "$MODS_BASE"
        cat > "$MODS_BASE/mod-list.json" <<'JSON'
{"mods": [{"name": "base", "enabled": true}, {"name": "elevated-rails", "enabled": false},
          {"name": "quality", "enabled": false}, {"name": "space-age", "enabled": false}]}
JSON
        "$BIN" --mod-directory "$MODS_BASE" --dump-data >/dev/null 2>&1
        echo base > "$stamp"
    fi
    echo "$out"
}

case "${1:-install}" in
    install) cmd_install ;;
    dump) cmd_dump ;;
    path) echo "$BIN" ;;
    *) echo "usage: $0 [install|dump|path]" >&2; exit 2 ;;
esac
