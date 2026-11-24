#!/usr/bin/env bash
# Build the Swift GUI and assemble a double-clickable .app bundle.
#
#   ./gui/build.sh                          release build -> gui/.build/nx2mac.app
#   ./gui/build.sh release ~/Applications   also install the bundle there
#
# The bundle is self-contained: the Python CLI is frozen with PyInstaller into
# Contents/Resources/cli/, so users do not have to install `nx2mac` themselves.
# Set NX2MAC_SKIP_CLI_BUNDLE=1 while iterating on the SwiftUI side.
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

ROOT="$(cd "$HERE/.." && pwd)"
CLI_DIR="$HERE/.build/cli-dist/nx2mac"
APP="$HERE/.build/nx2mac.app"
# Freeze the CLI so the app carries its own copy (no separate install needed).
if [ "${NX2MAC_SKIP_CLI_BUNDLE:-0}" != "1" ]; then
  echo "==> bundling the CLI (PyInstaller)"
  rm -rf "$HERE/.build/cli-dist" "$HERE/.build/cli-work" "$HERE/.build/nx2mac.spec"
  uv --project "$ROOT" run --with pyinstaller pyinstaller \
    --noconfirm --clean --onedir --name nx2mac \
    --distpath "$HERE/.build/cli-dist" \
    --workpath "$HERE/.build/cli-work" \
    --specpath "$HERE/.build" \
    --paths "$ROOT/src" \
    --add-data "$ROOT/patches:nx2mac/patches" \
    --collect-data rich --collect-submodules click \
    "$ROOT/packaging/cli_entry.py" >/dev/null
fi

echo "==> assembling $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/NX2MacGUI"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
mkdir -p "$APP/Contents/Resources/cli"
if [ -x "$CLI_DIR/nx2mac" ]; then
  cp -R "$CLI_DIR/." "$APP/Contents/Resources/cli/"
  echo "    bundled CLI: $(du -sh "$APP/Contents/Resources/cli" | cut -f1)"
else
  echo "warning: no frozen CLI in $CLI_DIR (the app will fall back to an installed nx2mac)" >&2
fi

if [ -f "$HERE/Resources/AppIcon.icns" ]; then
  cp "$HERE/Resources/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
else
  echo "warning: gui/Resources/AppIcon.icns missing (run: swift gui/tools/make_icon.swift gui/Resources/AppIcon.icns)" >&2
fi
printf 'APPL????' > "$APP/Contents/PkgInfo"

echo "==> signing (ad-hoc)"
# --deep: sign the frozen Python runtime and its dylibs inside Resources/cli too.
codesign --force --deep --sign - --timestamp=none "$APP" >/dev/null

if [ -n "$DEST" ]; then
  echo "==> copying to $DEST"
  rm -rf "$DEST/nx2mac.app"
  cp -R "$APP" "$DEST/nx2mac.app"
  echo "installed: $DEST/nx2mac.app"
fi

echo "done: $APP"
echo "open it with: open '$APP'"
