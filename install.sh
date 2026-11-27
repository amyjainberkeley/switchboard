#!/bin/sh
# Switchboard installer for macOS.
#
#   curl -fsSL <this file's address> | sh
#   sh install.sh [--no-setup] [--port N] [--no-agent] [--no-menu-app] [--no-link-app] [--own-python]
#   sh install.sh --homebrew       (how Homebrew's cask runs it: see below)
#
# What it does, in order:
#   1. copies the program (the page, the command line and its account engine) into ~/.switchboard/app
#      (your settings and accounts in ~/.switchboard are kept), with two one-line notes for `switchboard update`:
#      app/source, where it came from (this file's address, or the folder it ran from), and app/choices, the options
#      it was given that change what is installed (--no-agent, --no-menu-app, --no-link-app, --own-python)
#   2. adds the `switchboard` command, in ~/.local/bin (or /usr/local/bin when only that one is on your PATH; when
#      neither is, ~/.local/bin all the same, and the last line says how to add it), and its short name, `sb`, beside
#      it when nothing else on this Mac has that name (else one line says so). The program and its account
#      engine run on one Python: a Python 3.9 or newer on this Mac (macOS has one with Apple's command line tools), or,
#      without one (or with --own-python), Switchboard's own copy, downloaded once from python-build-standalone on
#      GitHub and checked against the SHA-256 written below before it is unpacked, so Apple's command line tools are
#      never needed. It needs nothing else: no packages, nothing downloaded from PyPI.
#   3. adds the menu bar item: it draws with AppKit through PyObjC, which needs a Python 3.12 to 3.14 that is not
#      python.org's or Homebrew's (macOS 26 draws no menu bar item for those): one found here, or Switchboard's own.
#      PyObjC's two packages are downloaded once from PyPI into ~/.switchboard/runtime/env and checked against the
#      SHA-256s in menubar-requirements.txt (pip --require-hashes, wheels only), and ~/.switchboard/bin/switchboard-menubar
#      runs `switchboard menubar` on it (skip with --no-menu-app, which also takes out one added before; without the
#      packages everything else works the same). Nothing for this goes outside ~/.switchboard, and nothing needs an
#      administrator.
#   4. builds the link app, ~/Applications/Switchboard Links.app, so links you click anywhere open as their owner (it
#      needs Apple's command line tools; skipped without them, with one line, and everything else works the same; skip
#      it with --no-link-app, which is kept for `switchboard update`). An older install's link app,
#      ~/Applications/Switchboard.app, goes once the new one is built; without the command line tools it is renamed
#      Switchboard Links.app instead. Either way it keeps its place as the default browser, if it had it.
#   5. puts the menu bar item's app, Switchboard.app, in /Applications (in ~/Applications when /Applications needs a
#      password), where Spotlight and Launchpad find it: opening it starts the menu bar item, or opens the page when
#      the item is running. An app of that name that is not this install's is left alone, and the other folder is
#      used. Not with --no-menu-app.
#   6. adds two background jobs, so the page and the menu bar item are there after every login (skip both with
#      --no-agent, which also leaves Launch Services, macOS's list of apps, alone). With the menu bar's app in place,
#      the page runs on that app's own Python (~/.switchboard/bin/switchboard-page), so when macOS asks something on
#      its behalf it names Switchboard; without it, on the program's Python.
#   7. at a terminal, runs `switchboard setup`, the first run: it adds the account Claude Code is on, and macOS asks
#      once whether Switchboard Links may be the default browser (skip with --no-setup). Without a terminal (an agent's
#      shell, a log file) it asks nothing, and the last line says what to type next; the page then offers the link app
#      until it is the default browser.
#
# It prints one line per step, the time the slow ones took, and the whole time. The installer opens nothing and
# never changes Claude Code's login; your default browser changes only when you say yes to macOS's own question.
# Safe to run again: what is already in place is kept, so a second run is quick. Without the internet the program is
# still installed and says what is missing.
# --no-open and --link-app are still accepted from older command lines: nothing is opened, and the link app is built
# either way.
# --homebrew is for Homebrew's cask (packaging/homebrew/): Homebrew adds the two commands to its own bin folder, so
# step 2 is skipped; step 7 is too, since Homebrew's output is a log, not a terminal. app/source is "homebrew:" and
# Homebrew's copy, so `switchboard update` runs `brew upgrade`. Given alone (as the cask gives it), the choices of the
# last install are kept, since Homebrew can't give them again.
# SWITCHBOARD_REPO and SWITCHBOARD_ARCHIVE stand in for the published repository and its download (forks, tests).
# All of it is one function, called on the last line: piped from curl, sh runs nothing until the whole file is there.
switchboard_install() {
set -eu
T0="$(date +%s)"

# The GitHub repository Switchboard installs from (owner/name). A copy that still holds the placeholder owner cannot
# download anything: a piped install (curl | sh) stops before it writes (the guard is further down).
REPO="${SWITCHBOARD_REPO:-amyjainberkeley/switchboard}"
# The address the README's one-line install fetches this file from, and the program it then downloads.
INSTALL_URL="https://raw.githubusercontent.com/$REPO/main/install.sh"
ARCHIVE_URL="${SWITCHBOARD_ARCHIVE:-https://github.com/$REPO/archive/refs/heads/main.tar.gz}"
LABEL="io.github.amyjainberkeley.switchboard"
PORT=""
AGENT=1
LINKAPP=1          # the link app is built unless --no-link-app says otherwise (now or at the last install)
MENUBAR=1
WANT_MENUBAR=0     # 1 once the menu bar item will be added: wanted, and this copy has its packages' list
OWN_PYTHON=0
SETUP_WANTED=1
HOMEBREW=0         # 1 with --homebrew: run by Homebrew's cask
CHOICE_GIVEN=0     # 1 when any other option was given: then the last install's choices are not read back

# Switchboard's own Python: for the program when this Mac has no Python 3.9 or newer (or with --own-python), and for
# the menu bar item when there is no Python here it can draw with.
# python-build-standalone release 20261026, CPython 3.14.10, "install_only_stripped" builds. The checksums are
# the release's own SHA256SUMS, checked again against the files themselves when this was written.
PBS_TAG="20261026"
PBS_VERSION="3.14.10"
PBS_SHA_ARM64="95eb8c87d16af37c54e9765e31d8c56c0d8ffa9968ea0f704f73a9b83f84e9f9"
PBS_SHA_X86_64="2fd8becd48cf0ff5f0a15df3c0db7cae7aa708dcaacf901bd30d6cdd6229370b"

# One plain line per step. At a terminal the routine ones are dim, so what needs reading stands out (no colour
# with NO_COLOR or TERM=dumb, or when the output is a file or a pipe). A slow step prints "Doing it… " first and its
# time at the end of the same line.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != dumb ]; then
  DIM="$(printf '\033[2m')"; BOLD="$(printf '\033[1m')"; PLAIN="$(printf '\033[0m')"
else
  DIM=""; BOLD=""; PLAIN=""
fi
LINE_OPEN=0
close_line() { if [ "$LINE_OPEN" = 1 ]; then printf '%s\n' "$PLAIN"; LINE_OPEN=0; fi; }
say() { close_line; printf '%s\n' "$*"; }                                 # worth reading: a skip, a problem, what next
note() { close_line; printf '%s%s%s\n' "$DIM" "$*" "$PLAIN"; }            # done, nothing to do about it
begin() { close_line; printf '%s%s… ' "$DIM" "$1"; LINE_OPEN=1; }         # a slow step starts
done_in() { printf '%s s%s%s\n' "$(( $(date +%s) - $1 ))" "${2:-}" "$PLAIN"; LINE_OPEN=0; }   # ...and ends well
result() { if [ "$LINE_OPEN" = 1 ]; then printf '%s%s\n' "$PLAIN" "$1"; LINE_OPEN=0; else say "$1"; fi; }   # ...or not
stop() { close_line; printf 'Switchboard: %s\n' "$*" >&2; exit 1; }
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
short() { case "$1" in "$HOME") printf '~' ;; "$HOME"/*) printf '~%s' "${1#"$HOME"}" ;; *) printf '%s' "$1" ;; esac; }
plist_value() { [ -f "$1/Contents/Info.plist" ] && /usr/libexec/PlistBuddy -c "Print :$2" "$1/Contents/Info.plist" 2>/dev/null || true; }
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

while [ $# -gt 0 ]; do
  case "$1" in
    --homebrew) HOMEBREW=1; shift; continue ;;
  esac
  CHOICE_GIVEN=1
  case "$1" in
    --port) [ $# -ge 2 ] || stop "--port needs a number between 1024 and 65535."; PORT="$2"; shift 2 ;;
    --no-agent) AGENT=0; shift ;;
    --link-app) LINKAPP=1; shift ;;
    --no-link-app) LINKAPP=0; shift ;;
    --no-menu-app) MENUBAR=0; shift ;;
    --no-open) shift ;;
    --no-setup) SETUP_WANTED=0; shift ;;
    --own-python) OWN_PYTHON=1; shift ;;
    -h|--help) say "sh install.sh [--no-setup] [--port N] [--no-agent] [--no-menu-app] [--no-link-app] [--own-python]"; exit 0 ;;
    *) stop "unknown option: $1" ;;
  esac
done

# Check everything before changing anything.
# macOS's sudo keeps HOME: as root, everything made in the person's home folder would be root's, and theirs no longer.
[ "$(id -u)" != 0 ] || { echo "Run this as yourself, without sudo: Switchboard lives in your own home folder." >&2; exit 1; }
[ "$(uname -s)" = "Darwin" ] || stop "this is a Mac app. It needs macOS."
[ -n "${HOME:-}" ] && [ -d "$HOME" ] || stop "no home folder found."
# Run by Homebrew's cask, which gives only --homebrew (an install, an upgrade or a reinstall): the last install's
# choices, from its app/choices (or, after a removal by Homebrew, the copy kept beside the settings, choices.last),
# so Homebrew does not add back what was left out. A `switchboard update` gives all of them itself, so they are read
# back only when nothing else was given.
if [ "$HOMEBREW" = 1 ]; then
  SETUP_WANTED=0
  SAVED_CHOICES="$HOME/.switchboard/app/choices"
  [ -f "$SAVED_CHOICES" ] || SAVED_CHOICES="$HOME/.switchboard/choices.last"
  if [ "$CHOICE_GIVEN" = 0 ] && [ -f "$SAVED_CHOICES" ]; then
    saved=" $(tr '\n' ' ' < "$SAVED_CHOICES") "
    case "$saved" in *" --no-agent "*) AGENT=0 ;; esac
    case "$saved" in *" --no-menu-app "*) MENUBAR=0 ;; esac
    case "$saved" in *" --no-link-app "*) LINKAPP=0 ;; esac
    case "$saved" in *" --own-python "*) OWN_PYTHON=1 ;; esac
  fi
fi
# Launch Services, macOS's list of apps (Spotlight, Launchpad, Open With), belongs to the user account, not to a home
# folder: only the account's own install tells it about Switchboard's apps. Not with --no-agent, not for a test's own
# Applications folder, and not from a home that is not the account's (a try-out in a throwaway home would list a
# second Switchboard.app beside the real one).
LS="$AGENT"
[ -z "${SWITCHBOARD_APPLICATIONS:-}" ] || LS=0
OWN_HOME="$(dscl . -read "/Users/$(id -un)" NFSHomeDirectory 2>/dev/null | sed -n -e 's/^NFSHomeDirectory: //p' -e '/^NFSHomeDirectory:$/{n;s/^ //p;}' | head -n 1)"
[ -n "$OWN_HOME" ] && [ "$(cd "$HOME" 2>/dev/null && pwd -P)" = "$(cd "$OWN_HOME" 2>/dev/null && pwd -P)" ] || LS=0
# launchd's jobs belong to the user account too, and launchd knows a job by its label alone: added from a home that
# is known not to be the account's, the jobs would stop the account's own Switchboard and start this copy in its
# place. So such an install is made as with --no-agent, and says why. (A home that can't be told is taken as the
# account's own.)
ELSEWHERE=0
if [ "$AGENT" = 1 ] && [ -n "$OWN_HOME" ] && [ "$(cd "$HOME" 2>/dev/null && pwd -P)" != "$(cd "$OWN_HOME" 2>/dev/null && pwd -P)" ]; then
  AGENT=0; ELSEWHERE=1
fi
DATA="$HOME/.switchboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
case "$DATA" in *'"'*|*'$'*|*'`'*|*'\'*) stop "the home folder's name has a character Switchboard's launchers can't hold: $HOME" ;; esac
if [ -n "$PORT" ]; then
  case "$PORT" in *[!0-9]*|-*) stop "--port needs a number between 1024 and 65535." ;; esac
  [ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || stop "--port needs a number between 1024 and 65535."
fi
MACHINE="$(uname -m)"
# A terminal under Rosetta says x86_64 on an Apple silicon Mac: Switchboard still gets the native Python.
[ "$MACHINE" != "x86_64" ] || [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" != 1 ] || MACHINE="arm64"
case "$MACHINE" in
  arm64) PBS_ARCH="aarch64"; PBS_SHA="$PBS_SHA_ARM64" ;;
  x86_64) PBS_ARCH="x86_64"; PBS_SHA="$PBS_SHA_X86_64" ;;
  *) stop "this Mac's processor ($MACHINE) is not one Switchboard knows." ;;
esac

DATA_WAS=0
[ ! -d "$DATA" ] || DATA_WAS=1
RUNTIME="$DATA/runtime"
OWN="$RUNTIME/python/bin/python3"
INSTALL_LOG="$DATA/install.log"
LOG_STARTED=0
install_log() { [ "$LOG_STARTED" = 1 ] || { mkdir -p "$DATA"; : > "$INSTALL_LOG"; LOG_STARTED=1; }; }

# Can the menu bar item draw on this Python? 3.12 to 3.14 (menubar-requirements.txt holds PyObjC's wheels for those),
# with venv and pip's bootstrap, and not a framework build (python.org's and Homebrew's are): macOS 26 draws no menu
# bar item for one, and one can't run from an app of its own. Switchboard's own Python is neither.
menu_python_ok() {
  [ -n "$1" ] && [ -x "$1" ] || return 1
  "$1" -I -B -c 'import sys, venv, ensurepip
sys.exit(0 if (3, 12) <= sys.version_info < (3, 15) and not getattr(sys, "_framework", "") else 1)' >/dev/null 2>&1
}
program_python_ok() { [ -n "$1" ] && [ -x "$1" ] && "$1" -I -B -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; }
# Apple's /usr/bin/python3 and /usr/bin/git are stand-ins until Apple's command line tools (or Xcode) are in place: run
# then, they show a dialog that offers to install them. Each is real only while the developer folder xcode-select names
# holds the tool itself (a macOS update can leave it naming a folder that no longer does).
apple_tool_real() { d="$(xcode-select -p 2>/dev/null)" && [ -n "$d" ] && [ -x "$d/usr/bin/$1" ]; }

# Switchboard's own Python, into $RUNTIME/python: one file, checked before it is unpacked. Its line says whose it is
# and where it is kept, not what for: which parts run on it (the program, the menu bar item) depends on the Pythons
# found here. What went wrong is left in OWN_WHY.
fetch_own_python() {
  PBS_FILE="cpython-$PBS_VERSION+$PBS_TAG-$PBS_ARCH-apple-darwin-install_only_stripped.tar.gz"
  PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/cpython-$PBS_VERSION%2B$PBS_TAG-$PBS_ARCH-apple-darwin-install_only_stripped.tar.gz"
  OWN_WHY=""
  install_log
  t="$(date +%s)"
  begin "Downloading Switchboard's own Python $PBS_VERSION, kept in $(short "$DATA") (27 MB, once)"
  rm -rf "$RUNTIME/download" "$RUNTIME/python.new"
  mkdir -p "$RUNTIME/download" "$RUNTIME/python.new"
  if ! curl --proto '=https' --tlsv1.2 -fsSL --retry 2 --connect-timeout 15 -o "$RUNTIME/download/$PBS_FILE" "$PBS_URL" 2>>"$INSTALL_LOG"; then
    OWN_WHY="Switchboard's own Python could not be downloaded (no internet?)."
  elif [ "$(shasum -a 256 "$RUNTIME/download/$PBS_FILE" | cut -d ' ' -f 1)" != "$PBS_SHA" ]; then
    OWN_WHY="The downloaded Python did not match its checksum, so it was not used."
  elif ! tar -xzf "$RUNTIME/download/$PBS_FILE" -C "$RUNTIME/python.new" 2>>"$INSTALL_LOG" \
       || ! menu_python_ok "$RUNTIME/python.new/python/bin/python3"; then
    OWN_WHY="The downloaded Python would not start."
  else
    rm -rf "$RUNTIME/python"
    mv "$RUNTIME/python.new/python" "$RUNTIME/python"
    done_in "$t"
  fi
  rm -rf "$RUNTIME/download" "$RUNTIME/python.new"
  [ -z "$OWN_WHY" ] || { result "$OWN_WHY"; return 1; }
}

# ---- a Python 3.9 or newer for the program: one on this Mac (macOS ships one with Apple's command line tools), else
# Switchboard's own, fetched below once the program itself is checked (always Switchboard's own with --own-python) ----
# SWITCHBOARD_PAGE_PYTHON names the one Python to try instead ("none": none), for tests.
if [ -n "${SWITCHBOARD_PAGE_PYTHON:-}" ]; then set -- "$SWITCHBOARD_PAGE_PYTHON"
else set -- /usr/bin/python3 "$(command -v python3 2>/dev/null || true)" /opt/homebrew/bin/python3 /usr/local/bin/python3; fi
PY=""
for p in "$@"; do
  [ -n "$p" ] && [ -x "$p" ] || continue
  # /usr/bin/python3 is only a stand-in until the command line tools are installed: never run then (a dialog).
  if [ "$p" = "/usr/bin/python3" ] && ! apple_tool_real python3; then continue; fi
  if "$p" -I -B -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$p"; break; fi
done

# ---- the program files: from this copy of the project when run as a file, else from GitHub ----
# Piped from curl, $0 is just "sh", so whatever folder you happen to be in is never used as the source.
SRC=""
if [ -f "$0" ]; then
  HERE="$(cd "$(dirname "$0")" && pwd)"
  if [ -f "$HERE/switchboard/__main__.py" ] && [ -f "$HERE/router/SwitchboardLinks.swift" ]; then SRC="$HERE"; fi
fi
if [ -n "$SRC" ]; then
  SOURCE="$SRC"
  # From Homebrew, Homebrew updates it: `switchboard update` runs `brew upgrade`, and makes a change it is asked for
  # with the installer in Homebrew's copy, which each upgrade replaces (and this note with it).
  [ "$HOMEBREW" = 0 ] || SOURCE="homebrew:$SRC"
else
  case "$REPO" in OWNER/*) stop "this copy of install.sh does not name its GitHub repository yet (REPO, near its top). Clone the repository and run: sh install.sh" ;; esac
  SOURCE="$INSTALL_URL"
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  t="$(date +%s)"
  begin "Downloading Switchboard"
  # https only, a redirect too; a file:// stand-in for the download (SWITCHBOARD_ARCHIVE) is read as a file.
  PROTO='=https'; case "$ARCHIVE_URL" in file://*) PROTO='=file' ;; esac
  curl --proto "$PROTO" --tlsv1.2 -fsSL --retry 2 --connect-timeout 15 "$ARCHIVE_URL" | tar -xz -C "$TMP" --strip-components 1 2>/dev/null \
    || stop "could not download $ARCHIVE_URL"
  [ -f "$TMP/switchboard/__main__.py" ] || stop "the download from $ARCHIVE_URL is not Switchboard."
  done_in "$t"
  SRC="$TMP"
fi
[ -f "$SRC/switchboard/accounts/api.py" ] || stop "this copy of Switchboard has no account engine (switchboard/accounts is missing)."
version_in() { sed -n "s/^VERSION = '\([^']*\)'.*/\1/p" "$1" 2>/dev/null | head -n 1; }
VERSION="$(version_in "$SRC/switchboard/core.py")"
WAS="$(version_in "$DATA/app/switchboard/core.py")"
if [ -z "$WAS" ]; then say "Installing Switchboard $VERSION in $(short "$DATA")"
elif [ "$WAS" = "$VERSION" ]; then say "Installing Switchboard $VERSION again in $(short "$DATA")"
else say "Updating Switchboard $WAS to $VERSION in $(short "$DATA")"; fi
[ "$MENUBAR" = 0 ] || [ ! -f "$SRC/menubar-requirements.txt" ] || WANT_MENUBAR=1

# No Python for the program on this Mac, or --own-python: Switchboard's own runs it, so Apple's command line tools
# are not needed. With --own-python and no internet, the program runs on the Python found here until it can be
# downloaded (the install then ends asking to be run again online).
PY_SOURCE="found"
OWN_LATER=0
if [ "$OWN_PYTHON" = 1 ] || [ -z "$PY" ]; then
  if program_python_ok "$OWN"; then
    PY="$OWN"
    PY_SOURCE="own"
    note "Switchboard's own Python $("$OWN" -I -B -c 'import platform; print(platform.python_version())') is in place."
  elif fetch_own_python; then
    PY="$OWN"
    PY_SOURCE="own"
  elif [ -n "$PY" ]; then
    OWN_LATER=1
  else
    [ "$DATA_WAS" = 1 ] || rm -rf "$DATA"                    # leave nothing behind from a run that did nothing
    stop "needs the internet once, to download its own Python (27 MB). Run this again when you are online."
  fi
fi

# What `switchboard update` needs to run this again the same way: where it came from, and the options that change
# what is installed (--port is kept in settings.json, so it is not one of them).
CHOICES=""
[ "$AGENT" = 1 ] || CHOICES="$CHOICES --no-agent"
[ "$MENUBAR" = 1 ] || CHOICES="$CHOICES --no-menu-app"
[ "$LINKAPP" = 1 ] || CHOICES="$CHOICES --no-link-app"
[ "$OWN_PYTHON" = 0 ] || CHOICES="$CHOICES --own-python"
[ "$HOMEBREW" = 0 ] || CHOICES="$CHOICES --homebrew"

mkdir -p "$DATA" "$DATA/bin"
chmod 700 "$DATA"
rm -rf "$DATA/app.new"
mkdir -p "$DATA/app.new"
cp -R "$SRC/switchboard" "$SRC/router" "$DATA/app.new/"
[ ! -f "$SRC/menubar-requirements.txt" ] || cp "$SRC/menubar-requirements.txt" "$DATA/app.new/"   # the menu bar's packages
[ ! -f "$SRC/LICENSE" ] || cp "$SRC/LICENSE" "$DATA/app.new/"
cp "$SRC/uninstall.sh" "$DATA/app.new/"                       # so a piped install can be removed too
printf '%s\n' "$SOURCE" > "$DATA/app.new/source"
printf '%s\n' "${CHOICES# }" > "$DATA/app.new/choices"
# The commit this copy is, for `switchboard version`: a `git archive` download has it in switchboard/build.txt already
# (.gitattributes); from a checkout, git is asked (never Apple's stand-in git without the command line tools).
if [ -e "$SRC/.git" ] && ! grep -q '^[0-9a-f]\{4,40\}$' "$DATA/app.new/switchboard/build.txt" 2>/dev/null; then
  GIT="$(command -v git 2>/dev/null || true)"
  if [ -n "$GIT" ] && { [ "$GIT" != /usr/bin/git ] || apple_tool_real git; }; then
    BUILD="$(env -u GIT_DIR -u GIT_WORK_TREE "$GIT" -C "$SRC" rev-parse --short HEAD 2>/dev/null || true)"
    case "$BUILD" in ''|*[!0-9a-f]*) ;; *) printf '%s\n' "$BUILD" > "$DATA/app.new/switchboard/build.txt" ;; esac
  fi
fi
find "$DATA/app.new" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
chmod +x "$DATA/app.new/switchboard/shim/open"
# SHA256SUMS beside the program: each file as it is put in place, for `switchboard about` and doctor (about.py).
"$PY" -I -B -c 'import sys; sys.path.insert(0, sys.argv[1]); from switchboard import about; about.write_manifest(sys.argv[1])' \
  "$DATA/app.new" 2>/dev/null || true
if [ -d "$DATA/app" ]; then rm -rf "$DATA/app.old"; mv "$DATA/app" "$DATA/app.old"; fi
mv "$DATA/app.new" "$DATA/app"
rm -rf "$DATA/app.old"

# ---- the `switchboard` command ----
SB="$DATA/bin/switchboard"
RUNPY='import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module("switchboard", run_name="__main__", alter_sys=True)'
# stand_in_check [PAUSE]: what a launcher on Apple's /usr/bin/python3 runs before it, at each start (nothing for any
# other Python). A macOS update can take the command line tools away, and the stand-in left in their place shows a
# dialog when it runs: for the page's job at every login, and again at every restart. So when the developer folder no
# longer holds python3, Switchboard's own Python or the menu bar's runs the program instead; with neither, one line
# says what to do and the launcher stops (after PAUSE: the page's job is started again 20 s after it stops).
stand_in_check() {
  [ "$PY" = /usr/bin/python3 ] || return 0
  cat <<EOF
if ! { d="\$(xcode-select -p 2>/dev/null)" && [ -n "\$d" ] && [ -x "\$d/usr/bin/python3" ]; }; then
  PY=""
  for p in "$OWN" "$RUNTIME/env/bin/python"; do
    if [ -x "\$p" ]; then PY="\$p"; break; fi
  done
  if [ -z "\$PY" ]; then
    echo "Switchboard: Apple's /usr/bin/python3 needs its command line tools again. Install them with xcode-select --install, or install Switchboard again so it uses its own Python." >&2
    ${1:-:}
    exit 1
  fi
fi
EOF
}
# -I: nothing from the folder you run it in, PYTHONPATH or your own site-packages can stand in for the program;
# -B: it leaves no bytecode cache behind (Apple's Python would put one in ~/Library/Caches).
cat > "$SB" <<EOF
#!/bin/sh
PY="$PY"
$(stand_in_check)
exec "\$PY" -I -B -c '$RUNPY' "$DATA/app" "\$@"
EOF
chmod +x "$SB"
[ -z "$PORT" ] || "$PY" -I -B -c 'import sys; sys.path.insert(0, sys.argv[1])
from switchboard.core import set_setting; set_setting("port", int(sys.argv[2]))' "$DATA/app" "$PORT"

# ---- what an older install left that this one no longer has ----
# A switchboard-engine command, its links and its runtime file are taken out: the account engine runs inside the
# program. A ~/.switchboard/engine folder is left as it is.
rm -f "$DATA/bin/switchboard-engine" "$RUNTIME/engine.json"
for d in "$HOME/.local/bin" /usr/local/bin; do
  if [ -L "$d/switchboard-engine" ] && [ "$(readlink "$d/switchboard-engine")" = "$DATA/bin/switchboard-engine" ]; then
    rm -f "$d/switchboard-engine"
  fi
done

# ---- the menu bar item: a Python it can draw with, PyObjC in ~/.switchboard/runtime/env, and its launcher ----
# `switchboard menubar` draws with AppKit through PyObjC: its packages are pinned with SHA-256s in
# menubar-requirements.txt and installed with pip --require-hashes, wheels only. The Python: the one an earlier run
# chose while it still works (a new Python on the Mac does not move it by itself), else one found here, else
# Switchboard's own (always Switchboard's own with --own-python). A stamp named after the requirements file says the
# packages are in place, so a second run downloads nothing. When the menu bar can't run here, no launcher (and below,
# no background job) is left that would fail at every login.
MENUBAR_CMD="$DATA/bin/switchboard-menubar"
MENUBAR_REQ="$DATA/app/menubar-requirements.txt"
MENUBAR_LOG="$DATA/menubar-install.log"
MENUBAR_OK=0
MPY=""
MPY_SOURCE=""
mkdir -p "$RUNTIME"
menubar_works() { "$1" -I -c 'import objc, AppKit' >/dev/null 2>&1; }
record() {      # record KEY: a value runtime/python.json holds, or nothing
  "$PY" -I -B -c 'import json, sys
try:
    v = json.load(open(sys.argv[1])).get(sys.argv[2])
    print(v if isinstance(v, str) else "")
except Exception:
    pass' "$RUNTIME/python.json" "$1" 2>/dev/null || true
}
# The first Python on this Mac the menu bar can draw with; nothing when there is none. Switchboard's own and Apple's
# /usr/bin/python3 (3.9) are not looked at here.
found_python() {
  for p in "$(command -v python3.14 2>/dev/null || true)" "$(command -v python3.13 2>/dev/null || true)" \
           "$(command -v python3.12 2>/dev/null || true)" "$(command -v python3 2>/dev/null || true)" \
           /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
           /usr/local/bin/python3.14 /usr/local/bin/python3.13 /usr/local/bin/python3.12 \
           /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.12/bin/python3; do
    case "$p" in "$RUNTIME"/*|/usr/bin/python3) continue ;; esac
    if menu_python_ok "$p"; then printf '%s' "$p"; return 0; fi
  done
  return 1
}
if [ "$MENUBAR" = 1 ] && [ "$WANT_MENUBAR" = 0 ]; then
  say "No menu bar item: this copy of Switchboard does not have it. Everything else works without it."
fi
if [ "$WANT_MENUBAR" = 1 ]; then
  if [ "$OWN_PYTHON" = 1 ]; then
    if menu_python_ok "$OWN" || fetch_own_python; then MPY="$OWN"; MPY_SOURCE="own"; fi
  else
    KEPT_PY="$(record menu_python)"
    if [ -n "$KEPT_PY" ] && menu_python_ok "$KEPT_PY"; then MPY="$KEPT_PY"; MPY_SOURCE="$(record menu_python_source)"
    elif p="$(found_python)"; then MPY="$p"; MPY_SOURCE="found"
    elif menu_python_ok "$OWN" || fetch_own_python; then MPY="$OWN"; MPY_SOURCE="own"; fi
    [ -n "$MPY_SOURCE" ] || MPY_SOURCE="found"
  fi
  [ -n "$MPY" ] || say "No menu bar item for now: it needs a Python to draw with. To add it, run switchboard update when you are online."
fi
FRESH=0
if [ -n "$MPY" ]; then
  # One environment per Python and pins: a second run with the same ones keeps what is there.
  MPY_VERSION="$("$MPY" -I -B -c 'import platform; print(platform.python_version())')"
  KEY="$( { printf '%s\n%s\n' "$MPY" "$MPY_VERSION"; cat "$MENUBAR_REQ"; } | shasum -a 256 | cut -c 1-16)"
  ENV="$RUNTIME/env-$KEY"
  STAMP="$ENV/.menubar-$(shasum -a 256 "$MENUBAR_REQ" | cut -c 1-16)"
  if [ -f "$STAMP" ] && menubar_works "$ENV/bin/python"; then
    MENUBAR_OK=1
  else
    FRESH=1
    t="$(date +%s)"
    begin "Adding the menu bar item"
    : > "$MENUBAR_LOG"
    rm -rf "$ENV"
    WHY=""
    if ! "$MPY" -I -m venv "$ENV" >>"$MENUBAR_LOG" 2>&1; then
      WHY="Python $MPY_VERSION ($MPY) could not make its environment"
    elif ! "$ENV/bin/python" -I -m pip install --isolated --disable-pip-version-check --no-input --no-cache-dir \
         --require-hashes --only-binary :all: --no-deps --index-url https://pypi.org/simple -q -r "$MENUBAR_REQ" \
         >>"$MENUBAR_LOG" 2>&1 || ! menubar_works "$ENV/bin/python"; then
      if grep -q 'HASHES' "$MENUBAR_LOG" 2>/dev/null; then WHY="a download did not match its checksum, so it was left out"
      else WHY="could not download it (no internet?)"; fi
    fi
    if [ -z "$WHY" ]; then
      : > "$STAMP"
      MENUBAR_OK=1
      done_in "$t"
    else
      rm -rf "$ENV"
      if [ -e "$RUNTIME/env" ] && menubar_works "$RUNTIME/env/bin/python"; then
        ENV="$RUNTIME/$(readlink "$RUNTIME/env")"
        MENUBAR_OK=1                                      # an earlier run's packages still work: they stay
        result "$WHY. The menu bar item already here stays."
      else
        result "$WHY. Everything else works without it."
      fi
    fi
  fi
fi
if [ "$MENUBAR_OK" = 1 ]; then
  # The env link is swapped in one step, so a running menu bar never sees half of one. Older environments go.
  "$PY" -B -c 'import os, sys; t = sys.argv[2] + ".new"
if os.path.lexists(t): os.remove(t)
os.symlink(sys.argv[1], t); os.replace(t, sys.argv[2])' "$(basename "$ENV")" "$RUNTIME/env"
  for old in "$RUNTIME"/env-*; do
    [ "$old" = "$ENV" ] || [ ! -d "$old" ] || rm -rf "$old"
  done
  [ "$FRESH" = 1 ] || note "The menu bar item is in place, on Python $MPY_VERSION."
  # Like the switchboard command: -I so nothing from where it starts stands in for the program, -B so it leaves no
  # bytecode behind.
  cat > "$MENUBAR_CMD.new" <<EOF
#!/bin/sh
exec "$RUNTIME/env/bin/python" -I -B -c 'import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module("switchboard", run_name="__main__", alter_sys=True)' "$DATA/app" menubar "\$@"
EOF
  chmod +x "$MENUBAR_CMD.new"
  mv -f "$MENUBAR_CMD.new" "$MENUBAR_CMD"
elif [ "$MENUBAR" = 1 ]; then
  rm -f "$MENUBAR_CMD"                                  # a launcher it can't run would fail at every login
fi                                                      # (--no-menu-app: the item goes below; its packages stay)

# What Switchboard runs on, for `switchboard update` and doctor (written again once the page's Python is known).
write_record() {
  "$PY" -B -c 'import json, os, sys, time
keys = ("python", "python_version", "python_source", "menu_python", "menu_python_source", "page_python")
rec = dict(zip(keys, sys.argv[2:]), installed_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
tmp = sys.argv[1] + ".tmp"
open(tmp, "w").write(json.dumps(rec, indent=1))
os.replace(tmp, sys.argv[1])' "$RUNTIME/python.json" "$PY" "$("$PY" -I -B -c 'import platform; print(platform.python_version())')" \
    "$PY_SOURCE" "$( [ "$MENUBAR_OK" = 1 ] && printf '%s' "$MPY" )" "$( [ "$MENUBAR_OK" = 1 ] && printf '%s' "$MPY_SOURCE" )" "$1"
}
write_record "$PY"

# ---- the commands on your PATH ----
# Never replace a different program that happens to share the name.
link_into() {
  for name in switchboard; do
    [ -e "$DATA/bin/$name" ] || continue
    if [ -e "$1/$name" ] || [ -L "$1/$name" ]; then
      [ -L "$1/$name" ] && [ "$(readlink "$1/$name")" = "$DATA/bin/$name" ] || continue
    fi
    ln -sf "$DATA/bin/$name" "$1/$name"
  done
}
LINKED=""
ON_PATH=1
CMD="switchboard"
# From Homebrew, nothing is added here: the cask links both commands into Homebrew's own bin folder
# (packaging/homebrew/launcher). The PATH seen here is Homebrew's, not the terminal's, so it could not tell anyway.
if [ "$HOMEBREW" = 0 ]; then
  for d in "$HOME/.local/bin" /usr/local/bin; do
    case ":$PATH:" in *":$d:"*) ;; *) continue ;; esac
    [ "$d" != "$HOME/.local/bin" ] || mkdir -p "$d"
    [ -d "$d" ] && [ -w "$d" ] || continue
    link_into "$d"
    LINKED="$d"
    break
  done
  if [ -z "$LINKED" ]; then
    # Neither folder is on the PATH: ~/.local/bin all the same (Claude Code's own installer uses it too), and the
    # last line says how to add it.
    mkdir -p "$HOME/.local/bin"
    link_into "$HOME/.local/bin"
    LINKED="$HOME/.local/bin"
    ON_PATH=0
  fi
  if [ -L "$LINKED/switchboard" ] && [ "$(readlink "$LINKED/switchboard")" = "$SB" ]; then
    note "Added the switchboard command to $(short "$LINKED")."
  else
    say "$(short "$LINKED")/switchboard is another program, so it was left alone. Switchboard's command is $(short "$SB")."
    CMD="$(short "$SB")"
    ON_PATH=1
  fi
fi

# ---- the short name: sb, the same command, beside switchboard ----
# Added only when nothing else on this Mac answers to the name: no program of that name in any folder on the PATH
# (Homebrew's own PATH too, HOMEBREW_PATH), no file of another's where the link would go, and no alias or function of
# that name in a shell's startup files. Otherwise nothing is added or changed, and one line says the command is
# switchboard. Every step here is allowed to fail: the short name never fails an install, an update or Homebrew's.
# With the one-line install the link goes beside switchboard's, to the same program. From Homebrew it goes in
# Homebrew's bin folder, as a link to Homebrew's own switchboard link beside it (so it follows every upgrade, and runs
# the launcher for whoever types it). It is not a `binary` line in the cask: when that name is taken, Homebrew stops
# the cask's whole install. Homebrew's folder is the prefix its copy of Switchboard sits under (<prefix>/Caskroom/...),
# else HOMEBREW_PREFIX.
SHORT="sb"
NEXT="$CMD"                                    # the command the last line says to type
short_ours() { [ -L "$1" ] && { [ "$(readlink "$1")" = "$SB" ] || [ "$(readlink "$1")" = switchboard ]; }; }
# Where another program already has the name (a path, said as ~/...), or nothing.
short_taken() {
  { printf '%s\n' "$PATH" "${HOMEBREW_PATH:-}" | tr ':' '\n'; printf '%s\n' "$@"; } | while IFS= read -r d; do
    case "$d" in /*) ;; *) continue ;; esac
    if { [ -e "$d/$SHORT" ] || [ -L "$d/$SHORT" ]; } && ! short_ours "$d/$SHORT"; then short "$d/$SHORT"; exit 0; fi
  done | head -n 1
  for f in .zshrc .zshenv .zprofile .zlogin .bashrc .bash_profile .bash_login .profile .aliases .bash_aliases \
           .config/fish/config.fish; do
    if grep -Eqs "^[[:space:]]*(alias[[:space:]]+(-[a-z]+[[:space:]]+)*$SHORT[=[:space:]]|function[[:space:]]+$SHORT([[:space:](]|\$)|$SHORT[[:space:]]*\(\))" "$HOME/$f"; then
      printf '~/%s\n' "$f"; return 0
    fi
  done
  [ ! -e "$HOME/.config/fish/functions/$SHORT.fish" ] || printf '~/.config/fish/functions/%s.fish\n' "$SHORT"
}
add_short() {   # add_short FOLDER TARGET
  [ -n "$1" ] && [ -d "$1" ] && [ -w "$1" ] || return 0
  taken="$(short_taken "$1" 2>/dev/null | head -n 1)" || taken=""
  if [ -n "$taken" ]; then
    say "$SHORT already means something else on this Mac ($taken), so the command is switchboard."
    return 0
  fi
  short_ours "$1/$SHORT" || ln -sfn "$2" "$1/$SHORT" 2>/dev/null || return 0
  ! short_ours "$1/$SHORT" || [ "$CMD" != switchboard ] || NEXT="$SHORT"
}
if [ "$HOMEBREW" = 0 ]; then
  add_short "$LINKED" "$SB" || true
else
  case "$SRC" in */Caskroom/*) BREW_BIN="${SRC%%/Caskroom/*}/bin" ;; *) BREW_BIN="${HOMEBREW_PREFIX:+$HOMEBREW_PREFIX/bin}" ;; esac
  add_short "$BREW_BIN" switchboard || true
fi

# ---- the link app, Switchboard Links (always, unless --no-link-app) ----
# Opening links as their owner is what Switchboard is for, so the link app is built by default; macOS makes it the
# default browser only when you say yes (the first run asks once, and the page offers it until then). Before 0.2.0 the
# link app was ~/Applications/Switchboard.app. That older copy is known by its bundle identifier, which never changed:
# macOS keeps the default browser by it, so once the new copy is built and macOS knows it, the older one goes and
# links keep opening as they did. With --no-link-app nothing here is touched.
LINKS_ID="io.github.amyjainberkeley.switchboard.links"
OLDER_LINKS="$HOME/Applications/Switchboard.app"
# A link app that an earlier build left half-made (its bundle identifier, no program) would hide a working one: the
# page and doctor look at Switchboard Links.app first. It goes before the link app is built again.
LINKS_APP="$HOME/Applications/Switchboard Links.app"
half_made() { exe="$(plist_value "$1" CFBundleExecutable)"; [ ! -f "$1/Contents/MacOS/${exe:-SwitchboardLinks}" ]; }
if [ "$LINKAPP" = 1 ] && [ -d "$LINKS_APP" ] && [ "$(plist_value "$LINKS_APP" CFBundleIdentifier)" = "$LINKS_ID" ] \
   && half_made "$LINKS_APP"; then
  rm -rf "$LINKS_APP"
fi
# Launch Services only for the account's own install (LS above): otherwise the app is put in place, and macOS is not
# told.
if [ "$LS" = 1 ]; then REGISTER=""; else REGISTER="--no-register"; fi
# Once the new link app is in place, the older copy goes; macOS is told (only the account's own install).
older_links_gone() {
  [ -d "$OLDER_LINKS" ] && [ "$(plist_value "$OLDER_LINKS" CFBundleIdentifier)" = "$LINKS_ID" ] || return 1
  [ "$LS" = 0 ] || "$LSREGISTER" -u "$OLDER_LINKS" >/dev/null 2>&1 || true
  rm -rf "$OLDER_LINKS"
}
# A release carries the link app already built, signed with its publisher's Developer ID and notarized by Apple, in
# router/prebuilt (router/build.sh --release makes it): then this Mac needs no compiler for it. It is used only when
# every check below passes, and is then put in place by build.sh --from exactly as a freshly built one is. On any
# failed check one line says which, and the app is built on this Mac as before: a prebuilt app that fails a check is
# never installed and never stops the install. LINKS_TEAM is the only Developer ID team whose prebuilt app is used;
# while it is empty (a tree no release has set it in), a prebuilt app is never used.
# SWITCHBOARD_BUILD_LINKS=1 builds the app on this Mac even when the release carries one (tests/install-check.sh).
LINKS_TEAM=""
PREBUILT="$DATA/app/router/prebuilt"
LINKS_PLACED=0
links_from_release() {
  LINKS_WHY=""; LINKS_SIGNER=""
  [ -n "$LINKS_TEAM" ] \
    || { LINKS_WHY="this installer does not name the Developer ID team it trusts"; return 1; }
  # links.json, read by the program's own Python: one value per line, each checked to be a plain string.
  said="$("$PY" -I -B -c 'import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
v = [d["zip_sha256"], d["source_sha256"], d["team"], d["minimum_macos"], " ".join(d["architectures"]),
     "yes" if d["notarized"] is True else "no"]
if not all(isinstance(x, str) and x and "\n" not in x for x in v):
    sys.exit(1)
print("\n".join(v))' "$PREBUILT/links.json" 2>/dev/null)" \
    || { LINKS_WHY="its links.json is missing or can't be read"; return 1; }
  want_sum="$(printf '%s\n' "$said" | sed -n 1p)"; want_source="$(printf '%s\n' "$said" | sed -n 2p)"
  want_team="$(printf '%s\n' "$said" | sed -n 3p)"; want_macos="$(printf '%s\n' "$said" | sed -n 4p)"
  archs="$(printf '%s\n' "$said" | sed -n 5p)"; notarized="$(printf '%s\n' "$said" | sed -n 6p)"
  [ "$(shasum -a 256 "$PREBUILT/SwitchboardLinks.zip" | cut -d ' ' -f 1)" = "$want_sum" ] \
    || { LINKS_WHY="its SHA-256 does not match links.json"; return 1; }
  [ "$(shasum -a 256 "$DATA/app/router/SwitchboardLinks.swift" | cut -d ' ' -f 1)" = "$want_source" ] \
    || { LINKS_WHY="it was built from another version of router/SwitchboardLinks.swift"; return 1; }
  [ "$notarized" = yes ] || { LINKS_WHY="links.json says it is not notarized"; return 1; }
  case " $archs " in *" $MACHINE "*) ;; *) LINKS_WHY="it has no build for this Mac's processor ($MACHINE)"; return 1 ;; esac
  have_macos="$(sw_vers -productVersion 2>/dev/null || true)"
  awk -v have="$have_macos" -v need="$want_macos" 'BEGIN { split(have, h, "."); split(need, n, ".")
    for (i = 1; i <= 3; i++) { if (h[i] + 0 > n[i] + 0) exit 0; if (h[i] + 0 < n[i] + 0) exit 1 } exit 0 }' \
    || { LINKS_WHY="it needs macOS $want_macos or newer, and this Mac has ${have_macos:-an unknown version}"; return 1; }
  # Unpacked without the quarantine flag: Homebrew marks what it downloads, ditto passes a zip's mark on to what it
  # unpacks, and macOS would then ask before the first link opens. A copy built here carries none either; this one is
  # used only once its Developer ID signature, team and stapled notarization ticket are checked below.
  LINKS_TMP="$(mktemp -d "${TMPDIR:-/tmp}/switchboard-prebuilt.XXXXXX")"
  ditto -x -k --noqtn "$PREBUILT/SwitchboardLinks.zip" "$LINKS_TMP" 2>/dev/null && [ -d "$LINKS_TMP/Switchboard Links.app" ] \
    || { LINKS_WHY="its zip could not be unpacked"; return 1; }
  cmp -s "$LINKS_TMP/Switchboard Links.app/Contents/Info.plist" "$DATA/app/router/Info.plist" \
    || { LINKS_WHY="its Info.plist is not the one in router/Info.plist"; return 1; }
  codesign --verify --strict "$LINKS_TMP/Switchboard Links.app" >/dev/null 2>&1 \
    || { LINKS_WHY="its signature did not verify (codesign --verify --strict)"; return 1; }
  shown="$(codesign -dv --verbose=2 "$LINKS_TMP/Switchboard Links.app" 2>&1 || true)"
  team="$(printf '%s\n' "$shown" | sed -n 's/^TeamIdentifier=//p' | head -n 1)"
  [ "$team" = "$want_team" ] || { LINKS_WHY="it is signed by team ${team:-none}, and links.json names $want_team"; return 1; }
  [ "$team" = "$LINKS_TEAM" ] || { LINKS_WHY="it is signed by team $team, and this install.sh trusts only $LINKS_TEAM"; return 1; }
  printf '%s\n' "$shown" | grep -qx 'Notarization Ticket=stapled' \
    || { LINKS_WHY="its signature carries no notarization ticket"; return 1; }
  LINKS_SIGNER="$(printf '%s\n' "$shown" | sed -n 's/^Authority=//p' | head -n 1)"
  sh "$DATA/app/router/build.sh" --from "$LINKS_TMP/Switchboard Links.app" $REGISTER "$HOME/Applications" \
    >/dev/null 2>"$DATA/build.log" \
    || { LINKS_WHY="it could not be put in ~/Applications (details in $(short "$DATA/build.log"))"; return 1; }
}
if [ "$LINKAPP" = 1 ] && [ -f "$PREBUILT/SwitchboardLinks.zip" ] && [ "${SWITCHBOARD_BUILD_LINKS:-}" != 1 ]; then
  LINKS_TMP=""
  if links_from_release; then
    LINKS_PLACED=1
    if older_links_gone; then OLDER_SAID=" (it was ~/Applications/Switchboard.app before)"; else OLDER_SAID=""; fi
    note "Installed the link app from the release: signed by $LINKS_SIGNER, checksum and signature checked, with a stapled notarization ticket.$OLDER_SAID"
  else
    say "Not using the release's link app: $LINKS_WHY."
  fi
  [ -z "$LINKS_TMP" ] || rm -rf "$LINKS_TMP"
fi
if [ "$LINKAPP" = 0 ] || [ "$LINKS_PLACED" = 1 ]; then
  :                                                   # left out (--no-link-app, now or from before), or in place
# Apple's /usr/bin/swiftc is a stand-in too (see apple_tool_real): real only while the developer folder holds swiftc,
# the command line tools' own or Xcode's toolchain's; else running it shows the dialog that offers to install them.
elif SWIFTC="$(command -v swiftc 2>/dev/null)" && xcode-select -p >/dev/null 2>&1 && { [ "$SWIFTC" != /usr/bin/swiftc ] \
     || apple_tool_real swiftc || [ -x "$(xcode-select -p)/Toolchains/XcodeDefault.xctoolchain/usr/bin/swiftc" ]; }; then
  t="$(date +%s)"
  begin "Building the link app"
  if sh "$DATA/app/router/build.sh" $REGISTER "$HOME/Applications" >/dev/null 2>"$DATA/build.log"; then
    if older_links_gone; then
      done_in "$t" " (it was ~/Applications/Switchboard.app before)"
    else
      done_in "$t"
    fi
  else
    result "could not (details in $(short "$DATA/build.log")). Everything else works without it."
  fi
elif [ "$(plist_value "$OLDER_LINKS" CFBundleIdentifier)" = "$LINKS_ID" ] && [ ! -e "$LINKS_APP" ]; then
  # Without the command line tools it can't be built again: the older one goes on working under the new name, so
  # "Switchboard" in Spotlight is the menu bar item's app. Its bundle identifier stays, and with it its place as the
  # default browser; macOS is told its new name.
  if mv "$OLDER_LINKS" "$LINKS_APP" 2>/dev/null; then
    [ "$LS" = 0 ] || { "$LSREGISTER" -u "$OLDER_LINKS" >/dev/null 2>&1; "$LSREGISTER" -f "$LINKS_APP" >/dev/null 2>&1; } || true
    note "Renamed the link app to Switchboard Links.app (it was ~/Applications/Switchboard.app before)."
  else
    say "Could not rename the link app, ~/Applications/Switchboard.app, to Switchboard Links.app. It still works."
  fi
else
  say "Skipped the link app: it needs Apple's command line tools (xcode-select --install). Everything else works without it."
fi

# ---- the menu bar item's app, in Applications ----
# Made by the menu bar's Python (menubar_wrap.place): /Applications when it can be written to without a password, else
# ~/Applications. A Switchboard.app there that is not this install's is left alone, and the other folder is used.
# The menu bar's job runs this copy, and opening it starts the menu bar item, or opens the page when the item runs.
# macOS is told it is there, so Spotlight and Launchpad show it at once (only the account's own install: LS above).
APP_PATH=""
if [ "$MENUBAR_OK" = 1 ]; then
  APP_SAID="$(SWITCHBOARD_HOME="$DATA" "$RUNTIME/env/bin/python" -I -B -c 'import sys; sys.path.insert(0, sys.argv[1])
from switchboard import menubar_wrap; sys.exit(menubar_wrap.installer())' "$DATA/app" 2>>"$MENUBAR_LOG" || true)"
  [ -n "$APP_SAID" ] || APP_SAID="say Couldn't put the menu bar's app in Applications (details in $(short "$MENUBAR_LOG"))."
  APP_PATH="$(printf '%s\n' "$APP_SAID" | sed -n 's/^app //p' | head -n 1)"
  printf '%s\n' "$APP_SAID" | while IFS= read -r l; do
    case "$l" in
      "app "*) [ "$LS" = 0 ] || "$LSREGISTER" -f "${l#app }" >/dev/null 2>&1 || true ;;
      "note "*) note "${l#note }" ;;
      "say "*) say "${l#say }" ;;
    esac
  done
fi

# ---- the page's launcher: on Switchboard.app's own Python when this install has it, so macOS names Switchboard ----
# The app's Python is a copy of the menu bar's, inside the app: when the page runs on it, what macOS asks on the page's
# behalf (the Keychain, controlling the browser) names Switchboard. It is used only when it runs the program here; the
# launcher falls back to the program's own Python when the app is gone later (taken out of Applications, say), so the
# page always starts.
PAGE_CMD="$DATA/bin/switchboard-page"
APP_PY=""
if [ -n "$APP_PATH" ] && [ -x "$APP_PATH/Contents/MacOS/python" ] && "$APP_PATH/Contents/MacOS/python" -I -B -c \
     'import sys; sys.path.insert(0, sys.argv[1]); import switchboard.core, switchboard.accounts.api' "$DATA/app" >/dev/null 2>&1; then
  APP_PY="$APP_PATH/Contents/MacOS/python"
fi
PAGE_CHECK="$(stand_in_check 'sleep 300')"
cat > "$PAGE_CMD.new" <<EOF
#!/bin/sh
# Switchboard's page, for its background job (made by install.sh): on Switchboard.app's own Python when it is there,
# so macOS names Switchboard when it asks something on the page's behalf; else on the program's own Python.
APP_PY="$APP_PY"
if [ -n "\$APP_PY" ] && [ -x "\$APP_PY" ]; then
  exec "\$APP_PY" -I -B -c '$RUNPY' "$DATA/app" serve
fi
PY="$PY"
$PAGE_CHECK
exec "\$PY" -I -B -c '$RUNPY' "$DATA/app" serve
EOF
chmod +x "$PAGE_CMD.new"
mv -f "$PAGE_CMD.new" "$PAGE_CMD"
write_record "${APP_PY:-$PY}"

# ---- the background jobs: the page, then the menu bar item ----
URL="http://localhost:$("$PY" -I -B -c 'import sys; sys.path.insert(0, sys.argv[1])
from switchboard.core import port; print(port())' "$DATA/app")"
ME="gui/$(id -u)"
JOB_PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
if [ "$AGENT" = 1 ]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  [ -e "$PLIST" ] || say "If macOS asks, choose Allow."        # its background items, the first time only
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$(xml "$PAGE_CMD")</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(xml "$JOB_PATH")</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>20</integer>
  <key>ExitTimeOut</key><integer>60</integer>
  <key>StandardOutPath</key><string>$(xml "$DATA/agent.log")</string>
  <key>StandardErrorPath</key><string>$(xml "$DATA/agent.log")</string>
</dict>
</plist>
EOF
  # Stop the old copy and wait until macOS has really let go of it, or starting the new one fails.
  launchctl bootout "$ME/$LABEL" >/dev/null 2>&1 || true
  i=0
  while [ $i -lt 20 ] && launchctl print "$ME/$LABEL" >/dev/null 2>&1; do i=$((i + 1)); sleep 0.25; done
  i=0
  until launchctl bootstrap "$ME" "$PLIST" 2>/dev/null; do
    i=$((i + 1))
    [ $i -lt 5 ] || stop "macOS would not start the background job. The program is installed; start the page with: switchboard serve"
    sleep 1
  done
  # The first run below keeps the account Claude Code is on through the page, so it waits for the page (10 s at most).
  # Straight to it, never through a proxy: curl sends even localhost to one named in http_proxy.
  i=0
  while [ $i -lt 20 ] && ! curl -fs --noproxy '*' "$URL/api/state" >/dev/null 2>&1; do i=$((i + 1)); sleep 0.5; done
  if [ $i -lt 20 ]; then note "The page is at $URL, and starts at every login."
  else say "Added the background job, but the page has not answered yet: switchboard doctor says why."; fi
else
  [ "$ELSEWHERE" = 0 ] || say "This is not your account's own home folder, so no background job was added: it would take the place of your account's own."
  say "Without the background job, start the page with: $CMD serve"
fi
# The menu bar item's job runs its launcher at every login, under the same rule (none with --no-agent). Its menu has
# no Quit: launchd starts it again whenever it exits (KeepAlive), and only `switchboard menubar off` turns it off, by
# taking this job out. When the menu bar can't run here, its job goes too.
MENUBAR_LABEL="$LABEL.menubar"
MENUBAR_PLIST="$HOME/Library/LaunchAgents/$MENUBAR_LABEL.plist"
APPS="${SWITCHBOARD_APPLICATIONS:-/Applications}"     # (tests point this at a folder of their own)
# This install's menu bar app: its bundle identifier, and made for this data folder (one made before the app named
# its folder counts as the usual ~/.switchboard's, which this always is).
our_menu_app() {
  [ -d "$1" ] && [ "$(plist_value "$1" CFBundleIdentifier)" = "$MENUBAR_LABEL" ] || return 1
  made_for="$(plist_value "$1" SwitchboardData)"
  [ -z "$made_for" ] || [ "$made_for" = "$(cd "$DATA" && pwd -P)" ]
}
if [ "$AGENT" = 1 ] && [ "$MENUBAR_OK" = 1 ]; then
  cat > "$MENUBAR_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$MENUBAR_LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$(xml "$MENUBAR_CMD")</string></array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(xml "$JOB_PATH")</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>ThrottleInterval</key><integer>20</integer>
  <key>StandardOutPath</key><string>$(xml "$DATA/menubar.log")</string>
  <key>StandardErrorPath</key><string>$(xml "$DATA/menubar.log")</string>
</dict>
</plist>
EOF
  launchctl bootout "$ME/$MENUBAR_LABEL" >/dev/null 2>&1 || true
  i=0
  while [ $i -lt 20 ] && launchctl print "$ME/$MENUBAR_LABEL" >/dev/null 2>&1; do i=$((i + 1)); sleep 0.25; done
  i=0
  until launchctl bootstrap "$ME" "$MENUBAR_PLIST" 2>/dev/null || [ $i -ge 4 ]; do i=$((i + 1)); sleep 1; done
  if launchctl print "$ME/$MENUBAR_LABEL" >/dev/null 2>&1; then note "The menu bar item is running, and starts at every login."
  else say "Added the menu bar item's background job, but macOS would not start it. Start it with: $CMD menubar"; fi
elif [ "$AGENT" = 1 ] && [ "$MENUBAR" = 1 ]; then
  launchctl bootout "$ME/$MENUBAR_LABEL" >/dev/null 2>&1 || true
  rm -f "$MENUBAR_PLIST"
elif [ "$MENUBAR_OK" = 1 ]; then
  say "Start the menu bar item with: $CMD menubar"
elif [ "$MENUBAR" = 0 ] && { [ -f "$MENUBAR_PLIST" ] || [ -e "$MENUBAR_CMD" ] || our_menu_app "$APPS/Switchboard.app" \
                              || our_menu_app "$HOME/Applications/Switchboard.app"; }; then
  # --no-menu-app on a Mac that has the menu bar item: it goes, its job first so it stops before its files do. With
  # --no-agent launchd is not asked, but the job's file goes too: a job left pointing at a launcher that is gone
  # would fail at every login. Its app goes from Applications too, when it is this install's (opened, it would start
  # the item again).
  [ "$AGENT" = 0 ] || launchctl bootout "$ME/$MENUBAR_LABEL" >/dev/null 2>&1 || true
  rm -f "$MENUBAR_PLIST" "$MENUBAR_CMD"
  for app in "$APPS/Switchboard.app" "$HOME/Applications/Switchboard.app"; do
    our_menu_app "$app" || continue
    [ "$LS" = 0 ] || "$LSREGISTER" -u "$app" >/dev/null 2>&1 || true
    rm -rf "$app"
  done
  rm -rf "$DATA/menubar"
  say "Removed the menu bar item."
fi

# ---- the end: the facts of this install, then the first run at a terminal, or one line saying what to type ----
took=$(( $(date +%s) - T0 ))
[ "$took" -ge 1 ] || took=1
case "$(basename "${SHELL:-/bin/zsh}")" in
  bash) PATH_HOW="echo 'export PATH=\"\$PATH:\$HOME/.local/bin\"' >> ~/.bash_profile" ;;
  fish) PATH_HOW="fish_add_path --append ~/.local/bin" ;;
  *) PATH_HOW="echo 'export PATH=\"\$PATH:\$HOME/.local/bin\"' >> ~/.zshrc" ;;
esac
# ~/.local/bin goes after everything else on the PATH: Claude Code's own installer can leave an older `claude` there,
# and putting the folder first would change which claude the person's terminal runs.
# Not on the PATH: the advice, then the command to copy, alone on the last line.
path_lines() { say "To type it, put ~/.local/bin on your PATH with this line, then open a new terminal:"; say "  $PATH_HOW"; }
if [ "$OWN_LATER" = 1 ]; then
  say "Switchboard runs on the Python found on this Mac until its own can be downloaded."
  # From Homebrew the program works all the same, so the cask's step succeeds (a failed installer script fails
  # Homebrew's whole install of the cask); brew reinstall runs this again with the same choices.
  if [ "$HOMEBREW" = 1 ]; then say "Run brew reinstall switchboard when you are online to finish."; exit 0; fi
  say "Run this installer again when you are online to finish."
  exit 1
fi
# The block of facts comes from `switchboard about` (switchboard/about.py), which checks each one now, so its words
# live in one place; it ends with "Done in N s." and what to type next. Over an install that was already here (an
# update, `brew upgrade`, a second run) it is the short form: the version, the program's files and what runs. It
# reads nothing from standard input. When it can't run, the last line is said here instead: the block never fails an
# install.
FORM=""
[ -z "$WAS" ] || FORM="--update"
close_line
# At a terminal: the output is one, and /dev/tty opens (curl | sh keeps it; an agent's shell has none). The first
# run reads its answer from the terminal, never from the pipe this file may be arriving through.
if [ "$SETUP_WANTED" = 1 ] && [ -t 1 ] && ( : </dev/tty ) 2>/dev/null; then
  "$SB" about --done "$took" --setup-now $FORM </dev/null || say "Done in $took s."
  rc=0
  "$SB" setup </dev/tty || rc=$?
  [ "$ON_PATH" = 1 ] || path_lines
  exit "$rc"
fi
if ! "$SB" about --done "$took" --as "$NEXT" $FORM </dev/null; then
  # Without the block: "Next: switchboard" while the engine keeps no account yet (`switchboard` alone runs the first
  # run then), else "Type switchboard to see your accounts.". (Its accounts' file, read leniently: a list of accounts
  # or one keyed by id, each with an email.)
  ACCOUNTS="$("$PY" -I -B -c 'import json, sys
try:
    a = json.load(open(sys.argv[1])).get("accounts")
    rows = a.values() if isinstance(a, dict) else a if isinstance(a, list) else []
    print(len([r for r in rows if isinstance(r, dict) and r.get("email")]))
except Exception:
    print(0)' "$DATA/accounts.json" 2>/dev/null || echo 0)"
  if [ "$ACCOUNTS" = 0 ]; then say "Done in $took s. Next: ${BOLD}$NEXT${PLAIN}"
  else say "Done in $took s. Type ${BOLD}$NEXT${PLAIN} to see your accounts."; fi
fi
[ "$ON_PATH" = 1 ] || path_lines
}
switchboard_install "$@"
