#!/bin/sh
# Builds Switchboard Links.app (the link app) and tells macOS it exists.
# It does not make the app the default browser; that takes your yes to macOS's own question (the first run asks once,
# and the page offers it until then).
#
#   sh router/build.sh [folder]                 folder defaults to ~/Applications
#   sh router/build.sh --no-register folder     only builds: macOS is not told (to check a build in a scratch folder)
#   sh router/build.sh [--no-register] --from APP folder
#                                               puts an app already built and signed (a release's, checked by the
#                                               installer) in place the same way, once its signature verifies again;
#                                               nothing is compiled
#   sh router/build.sh --icon                   draws SwitchboardLinks.icns again from icon.swift (Switchboard's mark),
#                                               working in a temporary folder; nothing is built, installed or opened
#   sh router/build.sh --release OUTDIR --identity FINGERPRINT [--notarize PROFILE] [--check]
#                                               the build a release carries: see "Release builds" below
#
# The app is made in a temporary folder and put in its place only once it has built and been signed, so a build that
# fails (command line tools older than this macOS, say) leaves nothing behind, and a link app already there stays
# as it was.
#
# Before 0.2.0 it was named Switchboard.app. The bundle identifier is the same as ever, so a link app that is the
# default browser stays the default browser.
#
# Release builds. --release makes the copy of the link app a release carries in router/prebuilt, so the installer
# can put it in place without compiling it (it checks it first, and builds on the Mac when any check fails):
#   - one program for Apple silicon and Intel: two `swiftc -target` builds joined by lipo, for the macOS named by
#     LSMinimumSystemVersion in Info.plist (12.0: NSWorkspace.setDefaultApplication, which the app uses to ask to be
#     the default browser, is macOS 12 or newer);
#   - the same bundle as the normal build: the same Info.plist, so the same bundle identifier, and the same icon;
#   - signed with the hardened runtime and a secure timestamp by the identity whose SHA-1 fingerprint is given.
#     Only a fingerprint is taken: two certificates can carry the same name, and codesign refuses a name it finds
#     twice as ambiguous. The certificate's name, issuer and end date are printed before it signs;
#   - with --notarize PROFILE, sent to Apple's notary service with the credentials saved under PROFILE
#     (xcrun notarytool store-credentials PROFILE), its ticket stapled to the app and checked by Gatekeeper;
#   - written as OUTDIR/SwitchboardLinks.zip and OUTDIR/links.json (the zip's SHA-256, the signing team, the
#     architectures, the minimum macOS, whether it is notarized). Nothing else is written, nothing is registered with
#     macOS, nothing is opened.
# --check runs every test a release build needs (the tools, the identity and its dates, the notary profile, the
# screen, the disk) and builds nothing. --identity - makes an ad-hoc build to try the release build with, never
# notarized; the installer never uses one.
set -eu
CALLER="$(pwd)"
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

absolute() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$CALLER" "$1" ;; esac; }

# ---- the release build ----
if [ "${1:-}" = "--release" ]; then
  OUT=""; IDENTITY=""; PROFILE=""; CHECK=0
  [ $# -ge 2 ] && [ -n "$2" ] && case "$2" in --*) false ;; esac \
    || { echo "--release needs a folder to write SwitchboardLinks.zip and links.json into." >&2; exit 2; }
  OUT="$(absolute "$2")"; shift 2
  while [ $# -gt 0 ]; do
    case "$1" in
      --identity) [ $# -ge 2 ] \
                    || { echo "--identity needs a certificate's SHA-1 fingerprint, or - for an ad-hoc build." >&2; exit 2; }
                  IDENTITY="$2"; shift 2 ;;
      --notarize) [ $# -ge 2 ] && [ -n "$2" ] || {
                    echo "--notarize needs the name of a notary profile saved with: xcrun notarytool store-credentials NAME" >&2
                    exit 2; }
                  PROFILE="$2"; shift 2 ;;
      --check) CHECK=1; shift ;;
      *) echo "unknown option for --release: $1" >&2; exit 2 ;;
    esac
  done
  if [ -z "$IDENTITY" ]; then
    echo "--release needs --identity: the SHA-1 fingerprint of your Developer ID Application certificate" \
         "(security find-identity -v -p codesigning lists them), or - for an ad-hoc build to try it with." >&2
    exit 2
  fi
  if [ "$IDENTITY" != "-" ]; then
    case "$IDENTITY" in
      *[!0-9A-Fa-f]*) OK=0 ;;
      *) OK=1; [ "${#IDENTITY}" = 40 ] || OK=0 ;;
    esac
    if [ "$OK" != 1 ]; then
      echo "--identity takes the certificate's SHA-1 fingerprint (40 hexadecimal digits), not its name: two" \
           "certificates can carry the same name, and codesign then refuses it as ambiguous. List them with:" \
           "security find-identity -v -p codesigning" >&2
      exit 2
    fi
    IDENTITY="$(printf '%s' "$IDENTITY" | tr 'a-f' 'A-F')"
  elif [ -n "$PROFILE" ]; then
    echo "An ad-hoc build can't be notarized: give --identity your Developer ID Application certificate's" \
         "fingerprint." >&2
    exit 2
  fi

  WORK="$(mktemp -d "${TMPDIR:-/tmp}/switchboard-release.XXXXXX")"
  trap 'rm -rf "$WORK"' EXIT
  PROBLEMS=0; FIRST=0
  row() { printf '  %-14s %s\n' "$1" "$2"; }
  # A check that fails: with --check it is listed and the others still run; otherwise the build stops here.
  problem() {
    if [ "$CHECK" = 1 ]; then
      row "$2" "$3"; PROBLEMS=$((PROBLEMS + 1)); [ "$FIRST" != 0 ] || FIRST="$1"
    else
      echo "$3" >&2; exit "$1"
    fi
  }
  failed() { echo "$1" >&2; exit 1; }       # a step of the build itself went wrong
  started() { date +%s; }
  took() { echo "$(( $(date +%s) - $1 )) s"; }
  PLIST="$(pwd)/Info.plist"
  plist() { /usr/libexec/PlistBuddy -c "Print :$1" "$PLIST" 2>/dev/null || true; }

  if [ "$CHECK" = 1 ]; then echo "Checking what a release build of the link app needs. Nothing is built."
  else echo "Release build of the link app, into $OUT"; fi

  TOOLS="swiftc lipo codesign ditto shasum"
  [ "$IDENTITY" = "-" ] || TOOLS="$TOOLS security openssl"
  [ -z "$PROFILE" ] || TOOLS="$TOOLS xcrun spctl ioreg"
  MISSING=0
  for tool in $TOOLS; do
    command -v "$tool" >/dev/null 2>&1 || {
      MISSING=1
      problem 69 tools "$tool is missing: install Xcode or Apple's command line tools (xcode-select --install), then\
 run this again."
    }
  done
  if [ -n "$PROFILE" ] && command -v xcrun >/dev/null 2>&1; then
    for tool in notarytool stapler; do
      xcrun --find "$tool" >/dev/null 2>&1 || {
        MISSING=1
        problem 69 tools "$tool is missing (xcrun --find $tool): install Xcode or Apple's command line tools, then\
 run this again."
      }
    done
  fi
  if [ "$MISSING" = 0 ]; then
    SWIFT_VERSION="$(swiftc --version 2>/dev/null | grep -o 'Apple Swift version [0-9.]*' | head -n 1 || true)"
    row tools "$TOOLS$([ -z "$PROFILE" ] || echo " notarytool stapler"): found ($SWIFT_VERSION)"
  fi

  MIN="$(plist LSMinimumSystemVersion)"
  VERSION="$(plist CFBundleShortVersionString)"
  BUNDLE_VERSION="$(plist CFBundleVersion)"
  BUNDLE_ID="$(plist CFBundleIdentifier)"
  if [ -n "$MIN" ] && [ -n "$VERSION" ] && [ -n "$BUNDLE_ID" ]; then
    row "minimum macOS" "$MIN (LSMinimumSystemVersion in router/Info.plist)"
  else
    problem 69 Info.plist \
      "router/Info.plist has no LSMinimumSystemVersion, CFBundleShortVersionString or CFBundleIdentifier."
  fi

  # The identity: present in a keychain for this fingerprint, a Developer ID Application certificate, not ended.
  SIGNER="ad hoc"; TEAM=""
  if [ "$IDENTITY" = "-" ]; then
    row identity \
      "ad hoc (no certificate: a build to try the release build with; the installer never uses an ad-hoc build)"
  elif command -v security >/dev/null 2>&1 && command -v openssl >/dev/null 2>&1; then
    NAME="$(security find-identity -v -p codesigning 2>/dev/null | tr 'a-f' 'A-F' | grep -F " $IDENTITY " | head -n 1 \
            | sed -n 's/^[^"]*"\(.*\)"[^"]*$/\1/p')"
    if [ -z "$NAME" ]; then
      problem 69 identity "No signing identity with the fingerprint $IDENTITY is in your keychains (or it is not\
 valid for code signing). Check it with: security find-identity -v -p codesigning"
    else
      # find-identity printed the name in capitals with the rest (the fingerprint is matched without case): read it
      # again as written, then the certificate with that name whose own SHA-1 is the fingerprint.
      NAME="$(security find-identity -v -p codesigning 2>/dev/null | grep -i -F " $IDENTITY " | head -n 1 \
              | sed -n 's/^[^"]*"\(.*\)"[^"]*$/\1/p')"
      mkdir "$WORK/certificates"
      security find-certificate -a -p -c "$NAME" 2>/dev/null \
        | awk -v dir="$WORK/certificates" '/-----BEGIN CERTIFICATE-----/ { n++ } n { print > (dir "/" n ".pem") }'
      CERT=""
      for pem in "$WORK"/certificates/*.pem; do
        [ -f "$pem" ] || continue
        sha1="$(openssl x509 -noout -fingerprint -sha1 < "$pem" 2>/dev/null | sed 's/^.*=//' | tr -d ':' | tr 'a-f' 'A-F')"
        [ "$sha1" != "$IDENTITY" ] || { CERT="$pem"; break; }
      done
      if [ -z "$CERT" ]; then
        problem 69 identity "The certificate with the fingerprint $IDENTITY could not be read from your keychains\
 (security find-certificate -c \"$NAME\")."
      else
        row identity "$NAME"
        row fingerprint "$IDENTITY"
        ISSUER="$(openssl x509 -noout -issuer < "$CERT" 2>/dev/null | sed 's/^issuer= *//')"
        ENDS="$(openssl x509 -noout -enddate < "$CERT" 2>/dev/null | sed 's/^notAfter=//')"
        # Apple's Developer ID certificates come from one of two intermediates: the older one, and G2, from which
        # Apple has issued them since 2019 (its subject has OU=G2).
        if printf '%s' "$ISSUER" | grep -Eq 'OU ?= ?G2([,/]|$)'; then row issuer "$ISSUER (Apple's G2 intermediate)"
        else row issuer "$ISSUER (not Apple's G2 intermediate: the older one. A new Developer ID certificate comes\
 from G2.)"; fi
        case "$NAME" in
          "Developer ID Application: "*) SIGNER="$NAME" ;;
          *) problem 69 identity "\"$NAME\" is not a Developer ID Application certificate: an app given out to\
 others, and notarizing, need one. Make one at developer.apple.com, under Certificates." ;;
        esac
        if ! openssl x509 -noout -checkend 0 < "$CERT" >/dev/null 2>&1; then
          problem 69 ends "The certificate has ended ($ENDS): make a new Developer ID Application certificate, then\
 run this again with its fingerprint."
        elif ! openssl x509 -noout -checkend 7776000 < "$CERT" >/dev/null 2>&1; then
          row ends "$ENDS: within 90 days. Renew the certificate before the next release."
        else
          row ends "$ENDS"
        fi
        TEAM="$(printf '%s' "$NAME" | sed -n 's/.*(\([A-Z0-9]\{10\}\))$/\1/p')"
        [ -z "$TEAM" ] || row team "$TEAM"
      fi
    fi
  fi

  # Notarizing: notarytool reads the saved sign-in from the login keychain, which it can read only while the screen
  # is unlocked, and the profile must be one Apple's notary service accepts.
  if [ -n "$PROFILE" ] && command -v ioreg >/dev/null 2>&1; then
    if ioreg -n Root -d1 2>/dev/null | grep -q '"CGSSessionScreenIsLocked"=Yes'; then
      problem 76 screen "The screen is locked: notarytool can read its saved sign-in only while the screen is\
 unlocked. Unlock it and run this again."
    else
      row screen "unlocked"
      if command -v xcrun >/dev/null 2>&1 && xcrun --find notarytool >/dev/null 2>&1; then
        if xcrun notarytool history --keychain-profile "$PROFILE" >/dev/null 2>&1; then
          row "notary profile" "$PROFILE (accepted by notarytool history)"
        else
          problem 69 "notary profile" "The notary profile \"$PROFILE\" is not saved, or Apple's notary service\
 refused it. Save it with: xcrun notarytool store-credentials $PROFILE"
        fi
      fi
    fi
  fi

  # Room for two builds, their module caches and the zips, in the temporary folder.
  FREE_KB="$(df -k "$WORK" 2>/dev/null | awk 'NR == 2 { print $4 }')"
  case "$FREE_KB" in ''|*[!0-9]*) FREE_KB=0 ;; esac
  if [ "$FREE_KB" -ge 1048576 ]; then row disk "$((FREE_KB / 1048576)) GB free for the build"
  else problem 69 disk "Only $((FREE_KB / 1024)) MB is free on the disk that holds ${TMPDIR:-/tmp}: the build\
 needs 1 GB."; fi

  if [ "$CHECK" = 1 ]; then
    if [ "$PROBLEMS" = 0 ]; then
      echo "Everything a release build needs is here. Nothing was built."
      exit 0
    fi
    if [ "$PROBLEMS" = 1 ]; then echo "Not ready: 1 problem above. Nothing was built."
    else echo "Not ready: $PROBLEMS problems above. Nothing was built."; fi
    exit "$FIRST"
  fi

  NEW="$WORK/Switchboard Links.app"
  mkdir -p "$NEW/Contents/MacOS" "$NEW/Contents/Resources"
  cp Info.plist "$NEW/Contents/Info.plist"
  [ -f SwitchboardLinks.icns ] && cp SwitchboardLinks.icns "$NEW/Contents/Resources/SwitchboardLinks.icns"
  t="$(started)"
  for arch in arm64 x86_64; do
    swiftc -O -module-cache-path "$WORK/cache" -target "$arch-apple-macos$MIN" -o "$WORK/SwitchboardLinks-$arch" \
      SwitchboardLinks.swift || failed "swiftc could not build the link app for $arch (its messages are above)."
  done
  lipo -create -output "$NEW/Contents/MacOS/SwitchboardLinks" \
    "$WORK/SwitchboardLinks-arm64" "$WORK/SwitchboardLinks-x86_64" || failed "lipo could not join the two builds."
  ARCHS="$(lipo -archs "$NEW/Contents/MacOS/SwitchboardLinks" | tr ' ' '\n' | sort | tr '\n' ' ' | sed 's/ $//')"
  echo "Built for $(echo "$ARCHS" | sed "s/ / and /g"), macOS $MIN or newer ($(took "$t"))"
  if [ "$IDENTITY" = "-" ]; then
    # No timestamp for an ad-hoc signature: it has no certificate for a timestamp to vouch for.
    codesign --force --options runtime --sign - "$NEW" >/dev/null 2>&1 \
      || failed "codesign could not sign the app ad hoc."
    echo "Signed ad hoc, hardened runtime"
  else
    codesign --force --options runtime --timestamp --sign "$IDENTITY" "$NEW" \
      || failed "codesign could not sign the app with $IDENTITY (its message is above). Check that the keychain\
 holding it is unlocked."
    echo "Signed by $SIGNER, hardened runtime, secure timestamp"
  fi
  codesign --verify --strict --verbose=2 "$NEW" 2>&1 | sed 's/^/  /' >&2 || true
  codesign --verify --strict "$NEW" >/dev/null 2>&1 \
    || failed "The signature did not verify (codesign --verify --strict --verbose=2)."
  SIGNED_TEAM="$(codesign -dv --verbose=2 "$NEW" 2>&1 | sed -n 's/^TeamIdentifier=//p' | head -n 1)"
  [ "$SIGNED_TEAM" != "not set" ] || SIGNED_TEAM=""
  if [ "$IDENTITY" != "-" ]; then
    [ -n "$SIGNED_TEAM" ] || failed "The signature names no team (codesign -dv shows no TeamIdentifier)."
    [ -z "$TEAM" ] || [ "$TEAM" = "$SIGNED_TEAM" ] \
      || failed "The signature names team $SIGNED_TEAM, but the certificate's name says $TEAM."
    TEAM="$SIGNED_TEAM"
  fi
  echo "Verified: codesign --verify --strict"
  ditto -c -k --keepParent "$NEW" "$WORK/SwitchboardLinks.zip"
  NOTARIZED=false
  if [ -n "$PROFILE" ]; then
    t="$(started)"
    echo "Sending it to Apple's notary service (this usually takes a few minutes)"
    SAID="$(xcrun notarytool submit "$WORK/SwitchboardLinks.zip" --keychain-profile "$PROFILE" --wait 2>&1 || true)"
    printf '%s\n' "$SAID" | sed 's/^/  /'
    ID="$(printf '%s\n' "$SAID" | sed -n 's/^ *id: *//p' | tail -n 1)"
    printf '%s\n' "$SAID" | grep -q '^ *status: Accepted$' \
      || failed "Apple's notary service did not accept the build. Its log:\
 xcrun notarytool log ${ID:-<id>} --keychain-profile $PROFILE"
    xcrun stapler staple "$NEW" >/dev/null \
      || failed "The notary ticket could not be stapled to the app (xcrun stapler staple)."
    GATE="$(spctl --assess --type execute -vv "$NEW" 2>&1 || true)"
    printf '%s\n' "$GATE" | sed 's/^/  /'
    printf '%s\n' "$GATE" | grep -q 'source=Notarized Developer ID' \
      || failed "Gatekeeper did not accept the stapled app as notarized (spctl --assess --type execute -vv)."
    rm -f "$WORK/SwitchboardLinks.zip"
    ditto -c -k --keepParent "$NEW" "$WORK/SwitchboardLinks.zip"
    NOTARIZED=true
    echo "Notarized and stapled ($(took "$t"))"
  fi
  SUM="$(shasum -a 256 "$WORK/SwitchboardLinks.zip" | cut -d ' ' -f 1)"
  SOURCE_SUM="$(shasum -a 256 SwitchboardLinks.swift | cut -d ' ' -f 1)"
  json() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"; }
  {
    echo "{"
    echo "  \"version\": $(json "$VERSION"),"
    echo "  \"build\": $(json "$BUNDLE_VERSION"),"
    echo "  \"bundle_id\": $(json "$BUNDLE_ID"),"
    echo "  \"zip_sha256\": $(json "$SUM"),"
    echo "  \"source_sha256\": $(json "$SOURCE_SUM"),"
    echo "  \"team\": $(json "$TEAM"),"
    echo "  \"signer\": $(json "$SIGNER"),"
    echo "  \"architectures\": [$(for a in $ARCHS; do printf '%s' "${sep:-}"; json "$a"; sep=", "; done)],"
    echo "  \"minimum_macos\": $(json "$MIN"),"
    echo "  \"notarized\": $NOTARIZED"
    echo "}"
  } > "$WORK/links.json"
  mkdir -p "$OUT"
  mv "$WORK/SwitchboardLinks.zip" "$OUT/SwitchboardLinks.zip"
  mv "$WORK/links.json" "$OUT/links.json"
  echo "Wrote $OUT/SwitchboardLinks.zip (SHA-256 $SUM)"
  echo "Wrote $OUT/links.json"
  if [ -n "$TEAM" ]; then
    echo "The installer uses it only when install.sh trusts this team: LINKS_TEAM=\"$TEAM\""
  fi
  exit 0
fi

# ---- the normal build, or --from: the app is made (or copied) aside, then put in its place ----
REGISTER=1
FROM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-register) REGISTER=0; shift
                   [ -n "${1:-}" ] || { echo "--no-register needs a folder to build into" >&2; exit 2; } ;;
    --from) [ -n "${2:-}" ] || { echo "--from needs the app to put in place" >&2; exit 2; }
            FROM="$(absolute "$2")"; shift 2 ;;
    *) break ;;
  esac
done
DEST="${1:-$HOME/Applications}"
APP="$DEST/Switchboard Links.app"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/switchboard-links.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
NEW="$WORK/Switchboard Links.app"
if [ -n "$FROM" ]; then
  # ditto keeps the signature, the stapled ticket and every attribute as they are; the copy must verify again.
  ditto "$FROM" "$NEW"
  codesign --verify --strict "$NEW" >/dev/null 2>&1 \
    || { echo "the app's signature did not verify: $FROM" >&2; exit 1; }
else
  mkdir -p "$NEW/Contents/MacOS" "$NEW/Contents/Resources"
  cp Info.plist "$NEW/Contents/Info.plist"
  [ -f SwitchboardLinks.icns ] && cp SwitchboardLinks.icns "$NEW/Contents/Resources/SwitchboardLinks.icns"
  swiftc -O -o "$NEW/Contents/MacOS/SwitchboardLinks" SwitchboardLinks.swift
  codesign --force --sign - "$NEW" >/dev/null 2>&1
fi
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
if [ -n "$FROM" ]; then echo "put $APP in place"; else echo "built $APP"; fi
