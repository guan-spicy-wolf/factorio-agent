#!/usr/bin/env bash
# Download Factorio headless (pinned version), extract it, and dump data.raw.
#
# Usage: bin/headless.sh [install|dump|path]
#   install  download + extract to $FACTORIO_DIR (idempotent)
#   dump     run --dump-data with exactly the $FGEN_MODS mod set enabled (the stock
#            install enables everything; prototypes such as recipe categories differ
#            between sets); prints the path of data-raw-dump.json
#   path     print the factorio binary path
#
# Env: FACTORIO_VERSION (default 2.1.23), FACTORIO_DIR (default ~/factorio),
#      FGEN_MODS (comma list; default base,space-age,quality,elevated-rails,recycler)
# The headless build is a free download; no account is needed.
# Network: needs factorio.com and dl.factorio.com reachable.
set -euo pipefail

FACTORIO_VERSION="${FACTORIO_VERSION:-2.1.23}"
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

FGEN_MODS="${FGEN_MODS:-base,space-age,quality,elevated-rails,recycler}"
STOCK_MODS="base elevated-rails quality space-age recycler"
MODS_DIR="$FACTORIO_DIR/fgen-mods"

write_mod_list() {
    # mod-list.json enabling exactly $FGEN_MODS among the stock mods (the stock install
    # enables everything; we want the set to be explicit and reproducible).
    mkdir -p "$MODS_DIR"
    local entries="" n enabled
    for n in $STOCK_MODS; do
        enabled=false
        case ",$FGEN_MODS," in *",$n,"*) enabled=true ;; esac
        entries="$entries${entries:+, }{\"name\": \"$n\", \"enabled\": $enabled}"
    done
    printf '{"mods": [%s]}\n' "$entries" > "$MODS_DIR/mod-list.json"
}

cmd_dump() {
    local out="$FACTORIO_DIR/script-output/data-raw-dump.json"
    local stamp="$out.mods"
    if [[ ! -s "$out" || "$(cat "$stamp" 2>/dev/null)" != "$FACTORIO_VERSION:$FGEN_MODS" ]]; then
        write_mod_list
        "$BIN" --mod-directory "$MODS_DIR" --dump-data >/dev/null 2>&1
        echo "$FACTORIO_VERSION:$FGEN_MODS" > "$stamp"
    fi
    echo "$out"
}

case "${1:-install}" in
    install) cmd_install ;;
    dump) cmd_dump ;;
    path) echo "$BIN" ;;
    *) echo "usage: $0 [install|dump|path]" >&2; exit 2 ;;
esac
