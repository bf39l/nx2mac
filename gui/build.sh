#!/usr/bin/env bash
# Build the Swift GUI and assemble a double-clickable .app bundle.
#
#   ./gui/build.sh                    release build -> gui/.build/nx2mac.app
#   ./gui/build.sh release ~/Applications   also install the bundle there
#
# No Xcode project is involved: SwiftPM compiles the executable, this script
# wraps it in Contents/{MacOS,Resources,Info.plist} and ad-hoc signs it.
set -euo pipefail

CONFIG="${1:-release}"
DEST="${2:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> swift build -c $CONFIG"
swift build --package-path "$HERE" -c "$CONFIG"

BIN="$(swift build --package-path "$HERE" -c "$CONFIG" --show-bin-path)/NX2MacGUI"
[ -x "$BIN" ] || { echo "error: no executable at $BIN" >&2; exit 1; }

APP="$HERE/.build/nx2mac.app"
echo "==> assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/NX2MacGUI"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
if [ -f "$HERE/Resources/AppIcon.icns" ]; then
  cp "$HERE/Resources/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
else
  echo "warning: gui/Resources/AppIcon.icns missing (run: swift gui/tools/make_icon.swift gui/Resources/AppIcon.icns)" >&2
fi
printf 'APPL????' > "$APP/Contents/PkgInfo"

echo "==> signing (ad-hoc)"
codesign --force --sign - --timestamp=none "$APP" >/dev/null

if [ -n "$DEST" ]; then
  echo "==> copying to $DEST"
  rm -rf "$DEST/nx2mac.app"
  cp -R "$APP" "$DEST/nx2mac.app"
  echo "installed: $DEST/nx2mac.app"
fi

echo "done: $APP"
echo "open it with: open '$APP'"
