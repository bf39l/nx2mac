#!/usr/bin/env bash
# Package a built nx2mac.app into a drag-to-install disk image.
#
#   packaging/make_dmg.sh <nx2mac.app> <version> <out-dir>
#
# The image holds the app next to a symlink to /Applications (so installing is "drag it across"),
# plus a short read-me about Gatekeeper: the app is ad-hoc signed, not notarized, so the first
# launch needs the context menu -> Open.
set -euo pipefail

app="${1:?usage: make_dmg.sh <nx2mac.app> <version> <out-dir>}"
version="${2:?usage: make_dmg.sh <nx2mac.app> <version> <out-dir>}"
out="${3:?usage: make_dmg.sh <nx2mac.app> <version> <out-dir>}"

command -v hdiutil >/dev/null 2>&1 || { echo "make_dmg.sh: hdiutil is macOS-only" >&2; exit 1; }
test -d "$app" || { echo "make_dmg.sh: no app bundle at $app" >&2; exit 1; }
exe="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$app/Contents/Info.plist" 2>/dev/null || true)"
test -n "$exe" && test -x "$app/Contents/MacOS/$exe" || {
  echo "make_dmg.sh: $app has no executable (CFBundleExecutable=${exe:-unset})" >&2
  exit 1
}
test -x "$app/Contents/Resources/cli/nx2mac" || {
  echo "make_dmg.sh: $app does not carry its frozen CLI (Contents/Resources/cli/nx2mac)" >&2
  exit 1
}

stage="$(mktemp -d)"
dmg_tmp=""
cleanup() {
  rm -rf "$stage"
  test -n "$dmg_tmp" && rm -f "$dmg_tmp"
}
trap cleanup EXIT

ditto "$app" "$stage/$(basename "$app")"
ln -s /Applications "$stage/Applications"

cat >"$stage/Read me.txt" <<TXT
nx2mac $version

Drag nx2mac.app into the Applications folder to install it. The app is self-contained: it carries
its own copy of the nx2mac command line tool, so nothing else needs installing.

First launch
============
The app is ad-hoc signed, not notarized with an Apple developer certificate, so macOS will warn
that it "cannot be checked for malicious software". Either

  right-click (or Control-click) the app -> Open -> Open,      # once, then it just opens
  or: xattr -dr com.apple.quarantine "/Applications/nx2mac.app"

Your games, saves and builds are not inside the app: they live in your game library folder, which
you pick in the app's Setup tab.
TXT

mkdir -p "$out"
dmg="$out/nx2mac-$version-macos-arm64.dmg"
rm -f "$dmg"

# UDZO = compressed read-only image, the usual format for downloads; HFS+ keeps the symlink.
hdiutil create \
  -volname "nx2mac $version" \
  -srcfolder "$stage" \
  -fs HFS+ \
  -format UDZO \
  -quiet \
  "$dmg"

test -s "$dmg" || { echo "make_dmg.sh: hdiutil produced no image" >&2; exit 1; }
echo "$dmg"
