#!/bin/sh
# Builds Switchboard Links.app (the link app) and tells macOS it exists.
# It does not make the app the default browser; that takes your yes to macOS's own question (the first run asks once,
# and the page offers it until then).
#
#   sh router/build.sh [folder]                 folder defaults to ~/Applications
#   sh router/build.sh --no-register folder     only builds: macOS is not told (to check a build in a scratch folder)
#   sh router/build.sh --icon                   draws SwitchboardLinks.icns again from icon.swift (Switchboard's mark),
#                                               working in a temporary folder; nothing is built, installed or opened
#
# The app is made in a temporary folder and put in its place only once it has built and been signed, so a build that
# fails (command line tools older than this macOS, say) leaves nothing behind, and a link app already there stays
# as it was.
#
# Before 0.2.0 it was named Switchboard.app. The bundle identifier is the same as ever, so a link app that is the
# default browser stays the default browser.
set -eu
cd "$(dirname "$0")"
if [ "${1:-}" = "--icon" ]; then
  WORK="$(mktemp -d "${TMPDIR:-/tmp}/switchboard-icon.XXXXXX")"
  trap 'rm -rf "$WORK"' EXIT
  swiftc -module-cache-path "$WORK/cache" -o "$WORK/icon" icon.swift
  "$WORK/icon" "$WORK/icon.png"
  mkdir "$WORK/SwitchboardLinks.iconset"
  for s in 16 32 128 256 512; do
    sips -z "$s" "$s" "$WORK/icon.png" --out "$WORK/SwitchboardLinks.iconset/icon_${s}x${s}.png" >/dev/null
    sips -z "$((s * 2))" "$((s * 2))" "$WORK/icon.png" --out "$WORK/SwitchboardLinks.iconset/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns -o "$WORK/SwitchboardLinks.icns" "$WORK/SwitchboardLinks.iconset"
  mv "$WORK/SwitchboardLinks.icns" SwitchboardLinks.icns
  echo "drew $(pwd)/SwitchboardLinks.icns"
  exit 0
fi
REGISTER=1
if [ "${1:-}" = "--no-register" ]; then
  REGISTER=0
  shift
  [ -n "${1:-}" ] || { echo "--no-register needs a folder to build into" >&2; exit 2; }
fi
DEST="${1:-$HOME/Applications}"
APP="$DEST/Switchboard Links.app"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/switchboard-links.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
NEW="$WORK/Switchboard Links.app"
mkdir -p "$NEW/Contents/MacOS" "$NEW/Contents/Resources"
cp Info.plist "$NEW/Contents/Info.plist"
[ -f SwitchboardLinks.icns ] && cp SwitchboardLinks.icns "$NEW/Contents/Resources/SwitchboardLinks.icns"
swiftc -O -o "$NEW/Contents/MacOS/SwitchboardLinks" SwitchboardLinks.swift
codesign --force --sign - "$NEW" >/dev/null 2>&1
# Built: it takes the place of the one there, if any (put back if the move fails).
mkdir -p "$DEST"
rm -rf "$APP.previous"
if [ -e "$APP" ] || [ -L "$APP" ]; then mv "$APP" "$APP.previous"; fi
if ! mv "$NEW" "$APP"; then
  rm -rf "$APP"
  if [ -e "$APP.previous" ]; then mv "$APP.previous" "$APP"; fi
  echo "could not put the app in $DEST" >&2
  exit 1
fi
rm -rf "$APP.previous"
if [ "$REGISTER" = 1 ]; then
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
fi
echo "built $APP"
