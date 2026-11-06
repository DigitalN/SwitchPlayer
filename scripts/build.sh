#!/bin/bash
# Builds and signs Switch Player.app, then prints its path. It does not install or open it.
#   scripts/build.sh           Apple Development certificate, falling back to ad hoc
#   scripts/build.sh --adhoc   local signature; this copy cannot update itself

set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-/Library/Frameworks/Python.framework/Versions/3.13/bin/python3}"

# iCloud-synced source folders add Finder metadata that codesign rejects.
WORK="${SWITCHPLAYER_BUILD_DIR:-$HOME/Library/Caches/SwitchPlayer-build}"
APP="$WORK/dist/Switch Player.app"
LOG="$WORK/build.log"

if [[ "${1:-}" == "--adhoc" ]]; then
    IDENTITY="-"
    echo "Signing locally (ad hoc); this copy won't update itself." >&2
else
    IDENTITY=$(security find-identity -v -p codesigning 2>/dev/null | awk '/"Apple Development/ {print $2; exit}')
    if [[ -z "$IDENTITY" ]]; then
        echo "No Apple Development certificate found; signing ad hoc. This copy won't update itself." >&2
        IDENTITY="-"
    fi
fi

rm -rf "$WORK"
mkdir -p "$WORK"
echo "Building Switch Player…" >&2
if ! "$PYTHON" -m PyInstaller "Application Files/SwitchPlayer.spec" --noconfirm --clean \
    --distpath "$WORK/dist" --workpath "$WORK/build" >"$LOG" 2>&1; then
    tail -20 "$LOG" >&2
    echo "Build failed; full log: $LOG" >&2
    exit 1
fi

xattr -cr "$APP"
if ! codesign --force --deep -s "$IDENTITY" "$APP" >>"$LOG" 2>&1 \
    || ! codesign --verify --deep --strict "$APP" >>"$LOG" 2>&1; then
    tail -10 "$LOG" >&2
    echo "Signing failed; full log: $LOG" >&2
    exit 1
fi
TEAM=$(codesign -d --verbose=2 "$APP" 2>&1 | sed -n 's/^TeamIdentifier=//p')
echo "Signed (team: $TEAM)." >&2
echo "$APP"
