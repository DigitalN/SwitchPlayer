#!/bin/bash
# Builds Switch Player and packages dist/Switch-Player-<version>.dmg for a GitHub release.
# This does not install the app or publish a release. Finder lays out the disk-image window.

set -euo pipefail
cd "$(dirname "$0")/.."

VOLUME_NAME="Switch Player"
# The folder build.sh builds in (it reads the same variable): the disk image is staged inside it,
# and all of it is removed at the end.
BUILD_DIR="${SWITCHPLAYER_BUILD_DIR:-$HOME/Library/Caches/SwitchPlayer-build}"
APP=$(scripts/build.sh)
TEAM=$(codesign -d --verbose=2 "$APP" 2>&1 | sed -n 's/^TeamIdentifier=//p')
if [[ -z "$TEAM" || "$TEAM" == "not set" ]]; then
    echo "Releases must be signed with an Apple Development certificate (see docs/DEVELOPMENT.md)." >&2
    exit 1
fi
VERSION=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$APP/Contents/Info.plist")
DMG="dist/Switch-Player-$VERSION.dmg"

WORK="$BUILD_DIR/dmg"
rm -rf "$WORK"
mkdir -p "$WORK/source/.background"
ditto "$APP" "$WORK/source/Switch Player.app"
ln -s /Applications "$WORK/source/Applications"
echo "Drawing the window background…" >&2
swift scripts/make-dmg-background.swift "$WORK/source/.background/background.tiff"

echo "Laying out the window…" >&2
SIZE=$(( $(du -sm "$WORK/source" | cut -f1) + 20 ))m
hdiutil create -quiet -volname "$VOLUME_NAME" -srcfolder "$WORK/source" -fs HFS+ \
    -format UDRW -size "$SIZE" -ov "$WORK/layout.dmg"
MOUNT=$(hdiutil attach -readwrite -noverify -noautoopen "$WORK/layout.dmg" | grep -o '/Volumes/.*$')
DISK=$(basename "$MOUNT")
detach() { hdiutil detach -quiet "$MOUNT" 2>/dev/null || hdiutil detach -quiet -force "$MOUNT" 2>/dev/null || true; }
trap detach EXIT

if ! osascript <<APPLESCRIPT
tell application "Finder"
    tell disk "$DISK"
        open
        set current view of container window to icon view
        set toolbar visible of container window to false
        set statusbar visible of container window to false
        set the bounds of container window to {200, 120, 860, 568}
        set viewOptions to the icon view options of container window
        set arrangement of viewOptions to not arranged
        set icon size of viewOptions to 112
        set text size of viewOptions to 13
        set label position of viewOptions to bottom
        set shows item info of viewOptions to false
        set shows icon preview of viewOptions to false
        set background picture of viewOptions to file ".background:background.tiff"
        set position of item "Switch Player.app" of container window to {170, 205}
        set position of item "Applications" of container window to {490, 205}
        close
        open
        update without registering applications
        delay 2
        close
    end tell
end tell
APPLESCRIPT
then
    echo "Finder couldn't lay out the window. Allow Terminal to control Finder, then try again." >&2
    exit 1
fi

for _ in $(seq 1 20); do
    [[ -f "$MOUNT/.DS_Store" ]] && break
    sleep 0.25
done
rm -rf "$MOUNT/.fseventsd"
sync
detach
trap - EXIT

mkdir -p dist
rm -f "$DMG"
hdiutil convert -quiet "$WORK/layout.dmg" -format UDZO -imagekey zlib-level=9 -o "$DMG"
rm -rf "$BUILD_DIR"
echo "$DMG"
