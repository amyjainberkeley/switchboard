#!/bin/sh
# Runs install.sh and uninstall.sh in throwaway home folders and checks what they leave behind.
# Never the real home: every run gets HOME (and CFFIXED_USER_HOME, the home macOS's own libraries use) set to a
# fresh folder under $TMPDIR, a PATH that holds only that home's ~/.local/bin and the system folders, and the flags
# that keep launchd and the browser out of it (--no-agent --no-open on every install, --no-agent on every uninstall).
# The link app is never really built: nearly every install passes --no-link-app as well, and the ones that show it is
# built by default run with a stand-in swiftc (one that fails, or one that writes a made-up program), so nothing is
# compiled, and with --no-agent nothing is registered with macOS.
# The menu bar item's packages are downloaded in cases 3 and 16 only (--no-menu-app elsewhere); the menu bar itself is
# never started: its launcher, and its app opened, run only in front of a stand-in program. SWITCHBOARD_APPLICATIONS
# points the installer and the uninstaller at a folder of each test's own, never /Applications (~/Applications is the
# throwaway home's). Launch Services and launchd are never touched.
#
#   sh tests/install-check.sh [port] ["cases"]   (port: a free one for the page setting, default 7841;
#                                                  cases: the numbers to run, as "11" or "3 11", default all)
#
# It downloads (Python 3.14 once with --own-python, and the menu bar's packages from PyPI), so it needs the internet
# for cases 1 to 3, 10, 12 and 16; cases 4, 11 and 17 to 29 block it and run in seconds.
# Prints one line per check and the times, and exits 1 on any failure. The first run at the end of an install is only ever a
# stand-in (case 8): the real `switchboard setup` would read this Mac's own Claude Code login.
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-7841}"
ONLY="${2:-}"
want() { [ -z "$ONLY" ] && return 0; for n in $ONLY; do [ "$n" = "$1" ] && return 0; done; return 1; }
REAL_HOME="$(/usr/bin/python3 -I -B -c 'import os, pwd; print(pwd.getpwuid(os.getuid()).pw_dir)')"
FAILED=0
ok() { printf ' ok  %s\n' "$*"; }
no() { printf ' NO  %s\n' "$*"; FAILED=1; }
check() { if eval "$1"; then ok "$2"; else no "$2"; fi; }
ESC="$(printf '\033')"
# A log as a person reads it: without a terminal's carriage returns, the ^D that `script` echoes at the start, and
# colour codes. Then its last line that is not blank.
plain_log() { tr -d '\r\010' < "$1" | sed -e 's/^\^D//' -e "s/$ESC\[[0-9;]*m//g"; }
last_line() { plain_log "$1" | grep -v '^[[:space:]]*$' | tail -n 1; }
LINKS_ID="io.github.amyjainberkeley.switchboard.links"
MENUBAR_ID="io.github.amyjainberkeley.switchboard.menubar"
# The installer's one line for its own Python (whatever needs it), and the reason it gives when it can't be fetched.
OWN_PY_LINE="Downloading Switchboard's own Python"
OWN_PY_FAIL="Switchboard's own Python could not be downloaded (no internet?)"
# a made-up app: fake_app FOLDER BUNDLE_ID EXECUTABLE [EXTRA PLIST LINES]; its program is written by the caller
fake_app() {
  mkdir -p "$1/Contents/MacOS"
  cat > "$1/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>$2</string><key>CFBundleExecutable</key><string>$3</string>${4:-}
</dict></plist>
EOF
}

fresh_home() {
  T="$(mktemp -d "${TMPDIR:-/tmp}/sb-install-XXXXXX")"
  case "$T" in "$REAL_HOME"|"$REAL_HOME"/.*) echo "refusing: $T is the real home" >&2; exit 2 ;; esac
  mkdir -p "$T/home/.local/bin" "$T/tmp" "$T/Applications"
  printf '%s' "$T"
}

# run HOME COMMAND...: the command with nothing of this shell's environment but what is named here
run() {
  h="$1"; shift
  env -i HOME="$h" CFFIXED_USER_HOME="$h" PATH="$h/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin" LANG=en_US.UTF-8 \
    TMPDIR="$(dirname "$h")/tmp/" SWITCHBOARD_APPLICATIONS="$(dirname "$h")/Applications" ${EXTRA_ENV:-} "$@"
}

# A copy of the program as the installer takes it: no tests, no docs, and no menubar-requirements.txt.
clone_tree() {
  mkdir -p "$1"
  cp -R "$HERE/switchboard" "$HERE/router" "$1/"
  cp "$HERE/install.sh" "$HERE/uninstall.sh" "$1/"
}

doctor_ok() {      # doctor_ok LOG EXIT: doctor ended well: "All good.", or with no account kept, the setup step
  [ "$2" = 0 ] && tail -1 "$1" | grep -q -e "All good." -e "switchboard setup\.$"
}
accounts_file() {  # accounts_file HOME EMAIL...: an accounts file the engine keeps (only the emails matter here)
  h="$1"; shift
  rows=""
  for e in "$@"; do rows="$rows${rows:+, }{\"id\": \"acct-$(printf '%s' "$e" | cut -d@ -f1)\", \"email\": \"$e\"}"; done
  mkdir -p "$h/.switchboard"
  printf '{"schema": 1, "accounts": [%s]}\n' "$rows" > "$h/.switchboard/accounts.json"
}

# The menu bar's packages: the tree's own menubar-requirements.txt (PyObjC, pinned with PyPI's SHA-256s).
menubar_pins() { cat "$HERE/menubar-requirements.txt"; }

now() { /usr/bin/python3 -I -B -c 'import time; print("%.2f" % time.time())'; }

if want 1 || want 2; then          # case 2 goes on in case 1's home
echo "== 1. From nothing, with Switchboard's own Python (downloaded and checked)"
T="$(fresh_home)"; H="$T/home"
t0="$(now)"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --own-python --port "$PORT" > "$T/install-1.log" 2>&1
rc=$?; t1="$(now)"
cat "$T/install-1.log" | sed 's/^/    /'
check '[ $rc = 0 ]' "install exits 0 ($(echo "$t1 - $t0" | bc) s)"
check '[ "$(wc -l < "$T/install-1.log")" -le 7 ] && ! grep -q "$ESC" "$T/install-1.log"' \
  "it says it in $(wc -l < "$T/install-1.log" | tr -d ' ') plain lines (no colour into a file)"
check 'last_line "$T/install-1.log" | grep -q "^Done in [0-9][0-9]* s\. Type sb setup to add your accounts\.$"' \
  "it times itself, and its last line is the one next step (no terminal, so no first run)"
check '[ "$(cat "$H/.switchboard/app/source")" = "$HERE" ] && [ "$(wc -l < "$H/.switchboard/app/source")" -eq 1 ]' \
  "app/source is one line: the folder it ran from"
check '[ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-menu-app --no-link-app --own-python" ]' \
  "app/choices holds the options that change what is installed (not --port or --no-open)"
check '[ -x "$H/.switchboard/runtime/python/bin/python3" ]' "Switchboard's own Python is in ~/.switchboard/runtime/python"
check '[ ! -e "$H/.switchboard/runtime/env" ] && [ -z "$(ls "$H/.switchboard/runtime" | grep "^env")" ]' \
  "--no-menu-app: no environment of packages at all (the program and its engine need none)"
check 'grep -qxF "PY=\"$H/.switchboard/runtime/python/bin/python3\"" "$H/.switchboard/bin/switchboard" && grep -q "^exec \"\$PY\" -I -B " "$H/.switchboard/bin/switchboard" && ! grep -q xcode-select "$H/.switchboard/bin/switchboard"' \
  "--own-python: the switchboard command runs on Switchboard's own Python"
run "$H" "$H/.switchboard/bin/switchboard" doctor > "$T/doctor-1.log" 2>&1; drc=$?
sed 's/^/    /' "$T/doctor-1.log"
check 'doctor_ok "$T/doctor-1.log" $drc' "switchboard doctor: all good"
check 'grep -q "Accounts: 0 kept" "$T/doctor-1.log"' \
  "doctor: the account engine answers, with no account yet"
check 'grep -q "Switchboard.s own Python" "$T/doctor-1.log"' "doctor names the program's Python as Switchboard's own"
check 'run "$H" "$H/.switchboard/runtime/python/bin/python3" -I -B -c "import sys; sys.path.insert(0, sys.argv[1]); import switchboard.accounts.api, switchboard.server, switchboard.engine" "$H/.switchboard/app"' \
  "the program and its account engine import on Switchboard's own Python"
check '! run "$H" "$H/.switchboard/runtime/python/bin/python3" -I -c "import objc" 2>/dev/null' \
  "nothing was installed into Switchboard's own Python (no PyObjC with --no-menu-app)"
t0="$(now)"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --own-python --port "$PORT" > "$T/install-2.log" 2>&1
rc=$?; t1="$(now)"
check '[ $rc = 0 ] && grep -q "^Switchboard.s own Python [0-9.]* is in place\.$" "$T/install-2.log" && ! grep -q Downloading "$T/install-2.log"' \
  "a second run keeps what is there, downloads nothing ($(echo "$t1 - $t0" | bc) s)"
check '! grep -q "menu bar" "$T/install-2.log" && [ ! -e "$H/.switchboard/bin/switchboard-menubar" ] && [ ! -e "$H/Applications" ]' \
  "--no-menu-app: no line about it, no launcher, nothing in ~/Applications"
check '[ ! -e "$H/.switchboard/app/mac" ] && [ ! -e "$H/.switchboard/menubar" ]' "no Mac app's source in the program, and no menu bar app made"
outside="$(cd "$H" && find . -mindepth 1 -type f | grep -v '^\./\.switchboard/' | sort | tr '\n' ' ')"
check '[ -z "$outside" ]' "no file outside ~/.switchboard (the command links are links)${outside:+ (also: $outside)}"
check '[ "$(ls "$H/.local/bin" | tr "\n" " ")" = "sb switchboard " ]' "~/.local/bin holds only the switchboard link and its short name, sb"

echo "== 2. Uninstall from the installed copy (keeps settings and accounts), then --all"
check 'cmp -s "$H/.switchboard/app/uninstall.sh" "$HERE/uninstall.sh"' "the uninstaller is installed with the program"
run "$H" "$H/.switchboard/bin/switchboard" statusline on > "$T/statusline.log" 2>&1
check 'grep -q statusLine "$H/.claude/settings.json"' "the status line is on in this home's Claude Code settings (to see it put back)"
run "$H" "$H/.switchboard/bin/switchboard" notify on > "$T/notify.log" 2>&1; nrc=$?
check '[ $nrc = 0 ] && [ "$(grep -o "# switchboard notify" "$H/.claude/settings.json" | wc -l | tr -d " ")" = 3 ] && [ "$(tail -n 1 "$T/notify.log")" = "Notifications: on" ]' \
  "switchboard notify on puts the three notification hooks in beside it"
run "$H" sh "$H/.switchboard/app/uninstall.sh" --no-agent > "$T/uninstall-1.log" 2>&1; urc=$?
sed 's/^/    /' "$T/uninstall-1.log"
check '[ $urc = 0 ]' "uninstall exits 0"
check '! grep -q statusLine "$H/.claude/settings.json" && grep -q "Status line: put back as it was." "$T/uninstall-1.log"' \
  "uninstall puts Claude Code's settings back (the status line is gone again)"
check 'grep -q "^Notification hooks: taken out\.$" "$T/uninstall-1.log" && [ ! -e "$H/.claude/settings.json" ]' \
  "and the notification hooks: the settings file Switchboard made is gone, as before the status line went on"
check '[ ! -e "$H/.switchboard/runtime" ] && [ ! -e "$H/.switchboard/app" ] && [ ! -e "$H/.switchboard/bin" ]' "the program (with its engine), its Python and its commands are gone"
check '[ -z "$(ls "$H/.local/bin")" ]' "the command links are gone"
check '[ -f "$H/.switchboard/settings.json" ]' "settings kept"
check 'grep -q "Removed Switchboard.s Python" "$T/uninstall-1.log"' "it says its Python went"
check '! grep -qF "$H" "$T/uninstall-1.log" && grep -q "^Removed the switchboard command from ~/.local/bin\.$" "$T/uninstall-1.log" && grep -q "^Removed Switchboard.s Python and packages (~/.switchboard/runtime)\.$" "$T/uninstall-1.log" && grep -q "^Removed the program and its commands (~/.switchboard/app, ~/.switchboard/bin)\.$" "$T/uninstall-1.log"' \
  "every path under the home folder is said with ~, as the installer says it"
KEEP_LINES="Kept your settings in ~/.switchboard (a reinstall picks them up).
The login Claude Code is using was not touched.
To remove them too, run:
sh ~/.switchboard/uninstall.sh --all"
check '[ "$(tail -n 4 "$T/uninstall-1.log")" = "$KEEP_LINES" ] && [ ! -e "$H/.switchboard/app/uninstall.sh" ] && [ -f "$H/.switchboard/uninstall.sh" ]' \
  "no account was added: its last lines name only the settings it kept, and how to remove them"
# Then --all from the installed copy, after installing again.
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --own-python --port "$PORT" > "$T/install-3.log" 2>&1; rc=$?
run "$H" sh "$H/.switchboard/app/uninstall.sh" --all --no-agent > "$T/uninstall-2.log" 2>&1; urc=$?
sed 's/^/    /' "$T/uninstall-2.log"
check '[ $rc = 0 ] && [ $urc = 0 ] && [ ! -e "$H/.switchboard" ] && [ -z "$(ls "$H/.local/bin")" ]' \
  "install again, then sh ~/.switchboard/app/uninstall.sh --all: nothing is left"
rm -rf "$T"
fi

if want 3; then
echo "== 3. From nothing, with the Python found on this Mac: the menu bar item, and the link app tried by default"
# From a copy of the program with the menu bar's packages pinned. No --no-link-app here: the link app is built by
# default, and a swiftc that fails stands first on the PATH, so it is tried and nothing is built or registered.
T="$(fresh_home)"; H="$T/home"; C="$T/clone"
clone_tree "$C"
C="$(cd "$C" && pwd)"
mkdir -p "$T/guard"
printf '#!/bin/sh\necho "a stand-in swiftc: nothing is compiled here" >&2\nexit 1\n' > "$T/guard/swiftc"
chmod +x "$T/guard/swiftc"
EXTRA_ENV="PATH=$T/guard:$H/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"     # for every run in this case
caches() { ls -A "$REAL_HOME/Library/Caches" 2>/dev/null | grep -i -e switchboard -e placeholder -e pyobjc; }
caches > "$T/caches-before.txt"
# First with a pin whose checksum is wrong (a real package, so pip downloads it and refuses it): one line, not fatal.
printf 'six==1.16.0 --hash=sha256:%s\n' "$(printf '0%.0s' $(seq 64))" > "$C/menubar-requirements.txt"
t0="$(now)"
run "$H" sh "$C/install.sh" --no-agent --no-open --port "$PORT" > "$T/install-bad.log" 2>&1
rc=$?; t1="$(now)"
sed 's/^/    /' "$T/install-bad.log"
check '[ $rc = 0 ] && grep -q "^Adding the menu bar item… a download did not match its checksum, so it was left out\. Everything else works without it\.$" "$T/install-bad.log"' \
  "a menu bar package that fails its checksum: one line, and the install goes on ($(echo "$t1 - $t0" | bc) s)"
check '[ ! -e "$H/.switchboard/bin/switchboard-menubar" ] && ! grep -q "Start the menu bar item" "$T/install-bad.log" && last_line "$T/install-bad.log" | grep -q "^Done in [0-9][0-9]* s\. Type sb setup"' \
  "no launcher for a menu bar that can't run, and the same last line"
menubar_pins > "$C/menubar-requirements.txt"
t0="$(now)"
run "$H" sh "$C/install.sh" --no-agent --no-open --port "$PORT" > "$T/install-1.log" 2>&1
rc=$?; t1="$(now)"
sed 's/^/    /' "$T/install-1.log"
check '[ $rc = 0 ]' "install exits 0 ($(echo "$t1 - $t0" | bc) s)"
# The menu bar's Python can draw the menu bar item: python.org's and Homebrew's are framework builds, which macOS 26
# draws no item for, so with only those on a Mac the menu bar gets Switchboard's own.
EPY_NOW="$(/usr/bin/python3 -I -B -c 'import json, sys; print(json.load(open(sys.argv[1]))["menu_python"])' "$H/.switchboard/runtime/python.json" 2>/dev/null)"
check '[ -n "$EPY_NOW" ] && "$EPY_NOW" -I -B -c "import sys; sys.exit(1 if getattr(sys, \"_framework\", \"\") else 0)"' \
  "the menu bar's Python is not a framework build (${EPY_NOW:-none})"
took="$(sed -n 's/^Adding the menu bar item… \([0-9][0-9]*\) s$/\1/p' "$T/install-1.log")"
check '[ -n "$took" ] && [ "$(grep -c "menu bar" "$T/install-1.log")" = 2 ]' \
  "the menu bar item's packages were added (${took:-?} s), in one line, and one more says how to start it"
check 'run "$H" "$H/.switchboard/runtime/env/bin/python" -I -c "import objc, AppKit, Foundation"' \
  "PyObjC imports in the menu bar's environment (checked against the pins: --require-hashes, wheels only)"
check 'run "$H" "$H/.switchboard/runtime/env/bin/python" -I -B -c "import sys; sys.path.insert(0, sys.argv[1]); import switchboard.accounts.api, switchboard.menubar" "$H/.switchboard/app"' \
  "the program and its engine import on it too"
check '[ "$(ls -A "$H/.switchboard/runtime/env/" | grep -c "^\.menubar-")" = 1 ]' "one stamp says the packages are in place"
check '[ -x "$H/.switchboard/bin/switchboard-menubar" ] && [ "$(sed -n 1p "$H/.switchboard/bin/switchboard-menubar")" = "#!/bin/sh" ]' \
  "the launcher is ~/.switchboard/bin/switchboard-menubar"
check 'grep -q "^Start the menu bar item with: switchboard menubar$" "$T/install-1.log" && [ ! -e "$H/Library/LaunchAgents" ]' \
  "--no-agent: no background job for it either (no LaunchAgents folder), and one line says how to start it"
check 'grep -q -e "^Building the link app… could not (details in ~/.switchboard/build.log)\. Everything else works without it\.$" -e "^Skipped the link app" "$T/install-1.log" && [ "$(grep -ci "link app" "$T/install-1.log")" = 1 ] && [ ! -e "$H/Applications" ]' \
  "the link app is built by default: here it can't be (its swiftc fails), so one line says so and nothing is left in ~/Applications"
check '[ "$(cat "$H/.switchboard/app/choices")" = "--no-agent" ]' "app/choices: --no-agent alone"
run "$H" "$H/.switchboard/bin/switchboard" doctor > "$T/doctor.log" 2>&1; drc=$?
check 'doctor_ok "$T/doctor.log" $drc && grep -q -e "the Python found on this Mac" -e "own Python" "$T/doctor.log"' "doctor: all good, and it names the program's Python"
t0="$(now)"
run "$H" sh "$C/install.sh" --no-agent --no-open --port "$PORT" > "$T/install-2.log" 2>&1
rc=$?; t1="$(now)"
check '[ $rc = 0 ] && ! grep -q "Adding the menu bar item" "$T/install-2.log" && grep -q "^Start the menu bar item with" "$T/install-2.log" && [ -x "$H/.switchboard/bin/switchboard-menubar" ]' \
  "a second run downloads nothing for the menu bar and keeps its launcher ($(echo "$t1 - $t0" | bc) s)"
# The launcher, run in front of a stand-in program (the real one would put an item in this Mac's menu bar): it starts
# `switchboard menubar` with what it was given, on the menu bar's own Python.
cat > "$H/.switchboard/app/switchboard/__main__.py" <<'EOF'
"""A stand-in (tests/install-check.sh): it says how it was started, and on which Python."""
import os
import sys
print('(stand-in) switchboard %s, on %s' % (' '.join(sys.argv[1:]), os.path.realpath(sys.prefix)))
EOF
if grep -q '(stand-in)' "$H/.switchboard/app/switchboard/__main__.py"; then
  out="$(run "$H" "$H/.switchboard/bin/switchboard-menubar" --one two 2>&1)"
  env_dir="$(/usr/bin/python3 -I -B -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$H/.switchboard/runtime/env")"
  check '[ "$out" = "(stand-in) switchboard menubar --one two, on $env_dir" ]' \
    "the launcher runs switchboard menubar on the menu bar's Python (${out:-nothing})"
else
  no "the stand-in program is not in place, so the launcher was not run"
fi
caches > "$T/caches-after.txt"
check 'cmp -s "$T/caches-before.txt" "$T/caches-after.txt"' "nothing new for Switchboard in the real ~/Library/Caches"
# --no-menu-app later takes the menu bar item out: its job's file (a stand-in here, in this home: --no-agent asks
# launchd nothing), its launcher and its app in ~/.switchboard.
mkdir -p "$H/Library/LaunchAgents" "$H/.switchboard/menubar/Switchboard.app/Contents"
printf '<plist/>\n' > "$H/Library/LaunchAgents/$MENUBAR_ID.plist"
run "$H" sh "$C/install.sh" --no-agent --no-open --no-menu-app --port "$PORT" > "$T/install-3.log" 2>&1; rc=$?
sed 's/^/    /' "$T/install-3.log"
check '[ $rc = 0 ] && grep -q "^Removed the menu bar item\.$" "$T/install-3.log" && [ "$(grep -c "menu bar" "$T/install-3.log")" = 1 ]' \
  "--no-menu-app later: one line says the menu bar item was removed"
check '[ ! -e "$H/.switchboard/bin/switchboard-menubar" ] && [ ! -e "$H/Library/LaunchAgents/$MENUBAR_ID.plist" ] && [ ! -e "$H/.switchboard/menubar" ] && [ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-menu-app" ]' \
  "its launcher, its job and its app are gone, and app/choices keeps it out for switchboard update"
run "$H" sh "$C/install.sh" --no-agent --no-open --port "$PORT" > "$T/install-back.log" 2>&1; rc=$?
check '[ $rc = 0 ] && [ -x "$H/.switchboard/bin/switchboard-menubar" ] && ! grep -q "Adding the menu bar item" "$T/install-back.log" && grep -q "^Start the menu bar item with" "$T/install-back.log" && [ "$(cat "$H/.switchboard/app/choices")" = "--no-agent" ]' \
  "without --no-menu-app (what switchboard update --menu-app passes on) it is back at once: its packages stayed"
rm -f "$C/menubar-requirements.txt"
run "$H" sh "$C/install.sh" --no-agent --no-open --port "$PORT" > "$T/install-4.log" 2>&1; rc=$?
sed 's/^/    /' "$T/install-4.log"
check '[ $rc = 0 ] && grep -q "^No menu bar item: this copy of Switchboard does not have it\. Everything else works without it\.$" "$T/install-4.log" && [ "$(grep -c "menu bar" "$T/install-4.log")" = 1 ]' \
  "a copy of Switchboard without menubar-requirements.txt: one line says there is no menu bar item, and it goes on"
check '[ ! -e "$H/.switchboard/bin/switchboard-menubar" ] && last_line "$T/install-4.log" | grep -q "^Done in [0-9][0-9]* s\. Type sb setup"' \
  "and the launcher it can't run is gone"
run "$H" sh "$HERE/uninstall.sh" --no-agent > "$T/uninstall.log" 2>&1; urc=$?
check '[ $urc = 0 ] && [ ! -e "$H/.switchboard/runtime" ] && [ ! -e "$H/.switchboard/bin" ] && [ ! -e "$H/.switchboard/menubar-install.log" ]' \
  "uninstall --no-agent: the packages (with the menu bar's environment), the launcher and the log are gone"
EXTRA_ENV=""
rm -rf "$T"
fi

if want 4; then
echo "== 4. No internet: the program is installed (it needs nothing downloaded), and says what waits"
T="$(fresh_home)"; H="$T/home"
EXTRA_ENV="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
t0="$(now)"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --own-python --port "$PORT" > "$T/install.log" 2>&1
rc=$?; t1="$(now)"
sed 's/^/    /' "$T/install.log"
check '[ $rc = 1 ]' "--own-python offline: install exits 1 ($(echo "$t1 - $t0" | bc) s)"
check 'grep -q "could not be downloaded (no internet?)" "$T/install.log" && grep -q "^Switchboard runs on the Python found on this Mac until its own can be downloaded\.$" "$T/install.log"' \
  "it says why, plainly, and what it runs on meanwhile"
check '[ "$(last_line "$T/install.log")" = "Run this installer again when you are online to finish." ]' "its last line is the one next step"
check '[ -x "$H/.switchboard/bin/switchboard" ] && run "$H" "$H/.switchboard/bin/switchboard" --version | grep -q "^switchboard [0-9]"' \
  "the program (with its engine) is in place and runs"
check '[ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-menu-app --no-link-app --own-python" ]' \
  "app/choices keeps --own-python, so switchboard update fetches it once online"
run "$H" "$H/.switchboard/bin/switchboard" doctor > "$T/doctor.log" 2>&1
check 'grep -q "Program on Python [0-9.]* (.*), the Python found on this Mac" "$T/doctor.log" && ! grep -q -i "engine: not installed" "$T/doctor.log"' \
  "doctor names the Python it runs on, and nothing is missing from the program"
check 'run "$H" env PYTHONPATH="$H/.switchboard/app" /usr/bin/python3 -B -c "
from switchboard import server, core
v = server.render(core.load(), dict(server.facts(), connecting=[]))
assert v[\"setup\"][\"engine\"] is True, v[\"setup\"]
"' "the page's first step has its engine: it is part of the program"
# With the Python found on this Mac, offline: the program needs nothing downloaded; the menu bar item waits.
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --port "$PORT" > "$T/install-found.log" 2>&1; rc=$?
check '[ $rc = 0 ] && ! grep -qi "download" "$T/install-found.log"' "offline with the Python found here: exit 0, nothing to download"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --port "$PORT" > "$T/install-menu.log" 2>&1; rc=$?
sed 's/^/    /' "$T/install-menu.log"
check '[ $rc = 0 ] && grep -q -e "^No menu bar item for now" -e "could not download it (no internet?)\. Everything else works without it\.$" "$T/install-menu.log" && [ ! -e "$H/.switchboard/bin/switchboard-menubar" ]' \
  "the menu bar item offline: one line says it waits, and no launcher is left"
check '[ -z "$(ls "$H/.switchboard/runtime" | grep "^env")" ]' "no half-made environment is left"
EXTRA_ENV=""
rm -rf "$T"
fi

if want 5; then
echo "== 5. Piped (curl | sh) before the repository is named: it says so and changes nothing"
T="$(fresh_home)"; H="$T/home"
run "$H" sh -s -- --no-agent --no-open --no-link-app --no-menu-app --port "$PORT" < "$HERE/install.sh" > "$T/pipe.log" 2>&1; rc=$?
check '[ $rc = 1 ] && grep -q "does not name its GitHub repository yet" "$T/pipe.log" && [ ! -e "$H/.switchboard" ]' \
  "the placeholder stops it before anything is written"
rm -rf "$T"
fi

if want 6; then
echo "== 6. uninstall --all asks the program to remove every account, with its login in the Keychain"
T="$(fresh_home)"; H="$T/home"
# the pictures of your pages (previews/): a plain uninstall keeps them with your settings, --all removes them
mkdir -p "$H/.switchboard/bin" "$H/.switchboard/previews"
printf '#!/bin/sh\necho "$*" > "%s/tidy-args"\n' "$T" > "$H/.switchboard/bin/switchboard"
chmod +x "$H/.switchboard/bin/switchboard"
printf 'a picture' > "$H/.switchboard/previews/FakeArtifactId00000001.jpg"
# the limit readings over 8 days (readings.jsonl): kept the same way, and gone with --all
printf '{"t":1794486600,"account":"maya@northwind.example","five":62,"week":70,"models":{}}\n' > "$H/.switchboard/readings.jsonl"
run "$H" sh "$HERE/uninstall.sh" --no-agent > "$T/uninstall.log" 2>&1; urc=$?
check '[ $urc = 0 ] && [ "$(cat "$T/tidy-args")" = "tidy" ] && [ -f "$H/.switchboard/previews/FakeArtifactId00000001.jpg" ]' \
  "a plain uninstall keeps the pictures of your pages (previews/) with your settings"
check '[ $urc = 0 ] && [ -f "$H/.switchboard/readings.jsonl" ]' \
  "a plain uninstall keeps the limit readings (readings.jsonl) with your settings"
mkdir -p "$H/.switchboard/bin"
cat > "$H/.switchboard/bin/switchboard" <<EOF
#!/bin/sh
# a stand-in: records the call, and removes the accounts as tidy --all does
echo "\$*" > "$T/tidy-args"; rm -f "$H/.switchboard/accounts.json"
EOF
chmod +x "$H/.switchboard/bin/switchboard"
run "$H" sh "$HERE/uninstall.sh" --all --no-agent > "$T/uninstall.log" 2>&1; urc=$?
check '[ $urc = 0 ] && [ "$(cat "$T/tidy-args")" = "tidy --all" ] && [ ! -e "$H/.switchboard" ]' \
  "switchboard tidy --all ran, then everything went, the pictures of your pages too"
check '[ $urc = 0 ] && [ ! -e "$H/.switchboard/readings.jsonl" ]' \
  "uninstall --all removes the limit readings (readings.jsonl)"
check 'grep -q "^Removed ~/.switchboard with your settings\. Nothing of Switchboard is left\.$" "$T/uninstall.log"' \
  "with no account, its last line names only the settings"
mkdir -p "$H/.switchboard/bin"
cat > "$H/.switchboard/bin/switchboard" <<EOF
#!/bin/sh
rm -f "$H/.switchboard/accounts.json"
EOF
chmod +x "$H/.switchboard/bin/switchboard"
accounts_file "$H" maya@northwind.example
run "$H" sh "$HERE/uninstall.sh" --all --no-agent > "$T/uninstall.log" 2>&1; urc=$?
check '[ $urc = 0 ] && grep -q "^Removed ~/.switchboard with your settings and accounts\. Nothing of Switchboard is left\.$" "$T/uninstall.log"' \
  "with one, it names the accounts too"
mkdir -p "$H/.switchboard/bin"
printf '#!/bin/sh\necho "Account maya@northwind.example: still kept"\nexit 1\n' > "$H/.switchboard/bin/switchboard"; chmod +x "$H/.switchboard/bin/switchboard"
accounts_file "$H" maya@northwind.example
run "$H" sh "$HERE/uninstall.sh" --all --no-agent > "$T/uninstall.log" 2>&1; urc=$?
check '[ $urc = 1 ] && [ -f "$H/.switchboard/accounts.json" ] && [ -x "$H/.switchboard/bin/switchboard" ] && grep -q "so the program and ~/.switchboard were kept" "$T/uninstall.log"' \
  "an account that could not be removed stops it: the program and the records stay"
rm -f "$H/.switchboard/bin/switchboard"
printf '{}' > "$H/.switchboard/settings.json"
run "$H" sh "$HERE/uninstall.sh" --all --no-agent > "$T/uninstall.log" 2>&1; urc=$?
check '[ $urc = 1 ] && [ -f "$H/.switchboard/settings.json" ] && [ -f "$H/.switchboard/accounts.json" ] && grep -q "Nothing was removed" "$T/uninstall.log"' \
  "without the program, --all stops before removing anything"
rm -rf "$T"
fi

if want 7; then
echo "== 7. An older install's link app at ~/Applications/Switchboard.app, and the menu bar item's running copies"
# Made-up apps only: each program is a script that notes its runs. Nothing is registered with macOS.
T="$(fresh_home)"; H="$T/home"
APPS="$H/Applications"
OLD="$APPS/Switchboard.app"
NEW="$APPS/Switchboard Links.app"
OTHER="$APPS/Other Browser.app"
fake_app "$OTHER" com.example.browser Other
# a link app whose --status prints the default browser: $T/default holds its path (another browser unless set)
link_script() {
  cat > "$1" <<EOF
#!/bin/sh
echo "\$*" >> "$T/ran-\$(basename "\$(dirname "\$(dirname "\$(dirname "\$0")")")" .app | tr ' ' -)"
case "\$1" in
  --status) if [ -f "$T/default" ]; then cat "$T/default"; else echo "$OTHER"; fi ;;
  --restore) [ -f "$T/stuck" ] || rm -f "$T/default" ;;
esac
EOF
  chmod +x "$1"
}
fake_app "$OLD" "$LINKS_ID" Switchboard
link_script "$OLD/Contents/MacOS/Switchboard"
sum_before="$(cksum < "$OLD/Contents/Info.plist")"
t0="$(now)"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --port "$PORT" > "$T/install-1.log" 2>&1
rc=$?; t1="$(now)"
sed 's/^/    /' "$T/install-1.log"
check '[ $rc = 0 ] && ! grep -qi -e "link app" -e "Switchboard.app" -e "menu bar" "$T/install-1.log"' \
  "--no-link-app: the older link app stays, and nothing is said about it ($(echo "$t1 - $t0" | bc) s)"
check '[ "$(cksum < "$OLD/Contents/Info.plist")" = "$sum_before" ] && [ -x "$OLD/Contents/MacOS/Switchboard" ] && [ ! -e "$NEW" ] && [ ! -e "$T/ran-Switchboard" ]' \
  "the older link app is untouched and was never run; nothing at Switchboard Links.app"
check 'run "$H" env PYTHONPATH="$H/.switchboard/app" /usr/bin/python3 -B -c "
from switchboard import linkapp
assert linkapp.app_path().endswith(\"/Applications/Switchboard.app\") and linkapp.bin_path().endswith(\"/MacOS/Switchboard\"), linkapp.bin_path()
s = linkapp.status(fresh=True)
assert s == {\"installed\": True, \"on\": False, \"browser\": \"Other Browser\"}, s
"' "the page still finds it until it moves (linkapp.app_path), and asks it"
rm -f "$T/ran-Switchboard"

rm -rf "$OLD"; fake_app "$OLD" com.example.someone-else Switchboard
printf '#!/bin/sh\ntouch "%s/ran-foreign"\n' "$T" > "$OLD/Contents/MacOS/Switchboard"; chmod +x "$OLD/Contents/MacOS/Switchboard"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --port "$PORT" > "$T/install-2.log" 2>&1; rc=$?
check '[ $rc = 0 ] && ! grep -qi "Switchboard.app" "$T/install-2.log" && [ -d "$OLD" ]' "another app at ~/Applications/Switchboard.app is left alone"
run "$H" sh "$HERE/uninstall.sh" --no-agent > "$T/uninstall-1.log" 2>&1; urc=$?
check '[ $urc = 0 ] && [ -d "$OLD" ] && [ ! -e "$T/ran-foreign" ] && ! grep -q "Removed the link app\|menu bar" "$T/uninstall-1.log"' \
  "uninstall leaves it there and never runs it"
rm -rf "$OLD"

# The give-back step, with each name.
fake_app "$NEW" "$LINKS_ID" SwitchboardLinks
link_script "$NEW/Contents/MacOS/SwitchboardLinks"
printf '%s\n' "$NEW" > "$T/default"
run "$H" sh "$HERE/uninstall.sh" --no-agent > "$T/uninstall-2.log" 2>&1; urc=$?
sed 's/^/    /' "$T/uninstall-2.log"
check '[ $urc = 0 ] && grep -q "Switchboard Links is your default browser" "$T/uninstall-2.log" && grep -q -- "--restore" "$T/ran-Switchboard-Links" && [ ! -e "$NEW" ] && grep -q "Removed the link app (~/Applications/Switchboard Links.app)" "$T/uninstall-2.log"' \
  "Switchboard Links as the default browser: uninstall asks for it back, then removes it"
fake_app "$OLD" "$LINKS_ID" Switchboard
link_script "$OLD/Contents/MacOS/Switchboard"
printf '%s\n' "$OLD" > "$T/default"; : > "$T/stuck"
run "$H" sh "$HERE/uninstall.sh" --no-agent > "$T/uninstall-3.log" 2>&1; urc=$?
check '[ $urc = 1 ] && [ -d "$OLD" ] && grep -q "still the default browser, so nothing was removed" "$T/uninstall-3.log" && grep -q -- "--restore" "$T/ran-Switchboard"' \
  "the older link app as the default browser, not given back: nothing removed"
rm -f "$T/stuck" "$T/default"
run "$H" sh "$HERE/uninstall.sh" --no-agent > "$T/uninstall-4.log" 2>&1; urc=$?
check '[ $urc = 0 ] && [ ! -e "$OLD" ] && grep -q "Removed the link app (~/Applications/Switchboard.app)" "$T/uninstall-4.log"' \
  "the older link app goes by its bundle identifier once it is not the default"
# Another copy of the link app holds the seat (a try-out in another home folder: macOS keeps one default browser for
# the account, whatever the home). This install's uninstaller has no seat to give back, so macOS is never asked.
ELSEWHERE="$T/elsewhere/Applications/Switchboard Links.app"
fake_app "$ELSEWHERE" "$LINKS_ID" SwitchboardLinks
link_script "$ELSEWHERE/Contents/MacOS/SwitchboardLinks"
fake_app "$NEW" "$LINKS_ID" SwitchboardLinks
link_script "$NEW/Contents/MacOS/SwitchboardLinks"
printf '%s\n' "$ELSEWHERE" > "$T/default"; : > "$T/stuck"; rm -f "$T/ran-Switchboard-Links"
run "$H" sh "$HERE/uninstall.sh" --no-agent > "$T/uninstall-5.log" 2>&1; urc=$?
sed 's/^/    /' "$T/uninstall-5.log"
check '[ $urc = 0 ] && ! grep -q "default browser" "$T/uninstall-5.log" && ! grep -q -- "--restore" "$T/ran-Switchboard-Links" 2>/dev/null && [ ! -e "$NEW" ] && [ -x "$ELSEWHERE/Contents/MacOS/SwitchboardLinks" ]' \
  "another copy of the link app is the default browser: macOS is not asked, this install's copy goes, the other stays"
rm -rf "$T/elsewhere" "$T/stuck" "$T/default"

# The menu bar item's running copies: only this install's are stopped. They are its app in ~/.switchboard/menubar
# and its copy in /Applications (here a folder of this test's own, through SWITCHBOARD_APPLICATIONS), by bundle
# identifier, and the launcher's command, which names this install's program folder and `menubar`.
mkdir -p "$T/nap"
printf '#include <unistd.h>\nint main(void) { sleep(120); return 0; }\n' > "$T/nap/nap.c"
if (cd "$T/nap" && cc -o nap nap.c) >/dev/null 2>&1; then
  NAP="$T/nap/nap"
  MB="$H/.switchboard/menubar/Switchboard.app"
  COPY="$T/Applications/Switchboard.app"
  fake_app "$MB" "$MENUBAR_ID" Switchboard; cp "$NAP" "$MB/Contents/MacOS/Switchboard"
  fake_app "$COPY" "$MENUBAR_ID" Switchboard; cp "$NAP" "$COPY/Contents/MacOS/Switchboard"
  mkdir -p "$T/decoy/Switchboard.app/Contents/MacOS"; cp "$NAP" "$T/decoy/Switchboard.app/Contents/MacOS/Switchboard"
  # started as an app is (its program's full path, or the argv given), and not as a child of this script
  start() {
    out="$1"; shift
    ( sh -c 'echo $$ > "$1"; shift; exec /usr/bin/python3 -I -c "import os, sys; os.execv(sys.argv[1], sys.argv[1:])" "$@"' sh "$out" "$@" </dev/null >/dev/null 2>&1 & )
    i=0; while [ $i -lt 40 ] && [ ! -s "$out" ]; do i=$((i + 1)); sleep 0.05; done; cat "$out"
  }
  gone() { i=0; while [ $i -lt 40 ] && kill -0 "$1" 2>/dev/null; do i=$((i + 1)); sleep 0.05; done; ! kill -0 "$1" 2>/dev/null; }
  app_pid="$(start "$T/app.pid" "$MB/Contents/MacOS/Switchboard")"
  copy_pid="$(start "$T/copy.pid" "$COPY/Contents/MacOS/Switchboard")"
  cmd_pid="$(start "$T/cmd.pid" "$NAP" -I -B -c run "$H/.switchboard/app" menubar --x)"
  decoy_pid="$(start "$T/decoy.pid" "$T/decoy/Switchboard.app/Contents/MacOS/Switchboard")"
  other_pid="$(start "$T/other.pid" "$NAP" -I -B -c run "$T/elsewhere/.switchboard/app" menubar)"
  run "$H" sh "$HERE/uninstall.sh" --no-agent > "$T/uninstall-5.log" 2>&1; urc=$?
  sed 's/^/    /' "$T/uninstall-5.log"
  check '[ $urc = 0 ] && gone "$app_pid" && gone "$copy_pid" && gone "$cmd_pid" && grep -q "^Quit the menu bar item\.$" "$T/uninstall-5.log"' \
    "uninstall stops the menu bar item: its app, its copy in /Applications, and the launcher's command"
  check 'kill -0 "$decoy_pid" 2>/dev/null && kill -0 "$other_pid" 2>/dev/null' \
    "another app named Switchboard, and another install's menu bar, keep running"
  check '[ ! -e "$H/.switchboard/menubar" ] && [ ! -e "$COPY" ] && grep -q "Removed the menu bar item.s app ($COPY)" "$T/uninstall-5.log"' \
    "its app in ~/.switchboard and its copy in /Applications are gone"
  kill "$app_pid" "$copy_pid" "$cmd_pid" "$decoy_pid" "$other_pid" 2>/dev/null; gone "$decoy_pid"; gone "$other_pid"
  fake_app "$COPY" com.example.someone-else Switchboard
  run "$H" sh "$HERE/uninstall.sh" --no-agent > "$T/uninstall-6.log" 2>&1; urc=$?
  check '[ $urc = 0 ] && [ -d "$COPY" ] && ! grep -q "menu bar" "$T/uninstall-6.log"' \
    "another app at /Applications/Switchboard.app is left alone"
else
  ok "(no C compiler: the running-copies check was skipped)"
fi
check '[ -z "$(pgrep -f "$T/")" ]' "nothing started here is still running"
rm -rf "$T"
fi

if want 8; then
echo "== 8. The end of an install: the first run at a terminal, one next step without one, and where it came from"
# A copy of the program whose every command is a stand-in that says how it was started, so the real first run
# (which reads this Mac's Claude Code login) never runs here. `script` gives the installer a terminal of its own.
T="$(fresh_home)"; H="$T/home"; C="$T/clone"
clone_tree "$C"
cat > "$C/switchboard/__main__.py" <<'EOF'
"""A stand-in for every command (tests/install-check.sh): it says how it was started, and does nothing else."""
import os
import sys
print('(stand-in) switchboard %s, input from %s' % (' '.join(sys.argv[1:]), 'the terminal' if os.isatty(0) else 'elsewhere'))
EOF
C="$(cd "$C" && pwd)"                       # as the installer writes a folder (TMPDIR's own // gone)
FLAGS="--no-agent --no-open --no-link-app --no-menu-app --port $PORT"
t0="$(now)"
EXTRA_ENV="TERM=xterm-256color"             # what a terminal sets (script sets TERM=dumb when there is none)
run "$H" script -q /dev/null sh "$C/install.sh" $FLAGS < /dev/null > "$T/tty.log" 2>&1; rc=$?
EXTRA_ENV=""
t1="$(now)"
plain_log "$T/tty.log" | sed 's/^/    /'
check '[ $rc = 0 ] && [ "$(last_line "$T/tty.log")" = "(stand-in) switchboard setup, input from the terminal" ]' \
  "at a terminal it ends with switchboard setup, reading from the terminal ($(echo "$t1 - $t0" | bc) s)"
check 'plain_log "$T/tty.log" | grep -B 1 "^(stand-in)" | head -n 1 | grep -q "^Done in [0-9][0-9]* s\.$"' \
  "the line before it is the time the install took"
check 'grep -q "$ESC\[2m" "$T/tty.log"' "at a terminal the routine lines are dim"
check '[ "$(cat "$H/.switchboard/app/source")" = "$C" ] && [ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-menu-app --no-link-app" ]' \
  "app/source is the folder it ran from; app/choices has --no-agent, --no-menu-app and --no-link-app"

EXTRA_ENV="NO_COLOR=1"
run "$H" script -q /dev/null sh "$C/install.sh" $FLAGS --no-setup < /dev/null > "$T/tty-nosetup.log" 2>&1; rc=$?
EXTRA_ENV=""
plain_log "$T/tty-nosetup.log" | sed 's/^/    /'
check '[ $rc = 0 ] && ! grep -q "stand-in" "$T/tty-nosetup.log" && last_line "$T/tty-nosetup.log" | grep -q "^Done in [0-9][0-9]* s\. Type sb setup to add your accounts\.$"' \
  "--no-setup at a terminal: no first run, and the last line says to type sb setup"
check '! grep -q "$ESC" "$T/tty-nosetup.log"' "NO_COLOR: no colour, even at a terminal"

# Without a terminal, and without --no-menu-app: this copy has no menubar-requirements.txt, so one line says there is
# no menu bar item, and the rest is as before.
run "$H" sh "$C/install.sh" --no-agent --no-open --no-link-app --port "$PORT" > "$T/plain.log" 2>&1; rc=$?
sed 's/^/    /' "$T/plain.log"
check '[ $rc = 0 ] && ! grep -q "stand-in" "$T/plain.log" && last_line "$T/plain.log" | grep -q "^Done in [0-9][0-9]* s\. Type sb setup to add your accounts\.$"' \
  "without a terminal: no first run, the same last line"
check 'grep -q "^No menu bar item: this copy of Switchboard does not have it\. Everything else works without it\.$" "$T/plain.log" && [ "$(grep -c "menu bar" "$T/plain.log")" = 1 ] && [ ! -e "$H/.switchboard/bin/switchboard-menubar" ]' \
  "no menubar-requirements.txt: one line says so, and no launcher"

# curl | sh, at a terminal: the program comes from an archive (a local file standing in for GitHub's), and the
# first run reads the terminal, not the pipe the rest of the installer is still arriving through.
(cd "$T" && tar -czf main.tar.gz clone)
EXTRA_ENV="SWITCHBOARD_REPO=example/switchboard SWITCHBOARD_ARCHIVE=file://$T/main.tar.gz"
run "$H" script -q /dev/null sh -c 'cat "$1" | sh -s -- $2' sh "$C/install.sh" "$FLAGS" < /dev/null > "$T/piped.log" 2>&1; rc=$?
EXTRA_ENV=""
plain_log "$T/piped.log" | sed 's/^/    /'
check '[ $rc = 0 ] && plain_log "$T/piped.log" | grep -q "^Downloading Switchboard… [0-9][0-9]* s$" && [ "$(last_line "$T/piped.log")" = "(stand-in) switchboard setup, input from the terminal" ]' \
  "piped at a terminal: it downloads the program, and the first run reads the terminal, not the pipe"
check '[ "$(cat "$H/.switchboard/app/source")" = "https://raw.githubusercontent.com/example/switchboard/main/install.sh" ]' \
  "app/source is the address the install line fetches"
check '[ -z "$(ls "$T/tmp" | grep "^tmp\.")" ]' "the download's temporary folder is gone"

rm -f "$H/.local/bin/switchboard"
EXTRA_ENV="PATH=/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" sh "$C/install.sh" $FLAGS > "$T/nopath.log" 2>&1; rc=$?
EXTRA_ENV=""
sed 's/^/    /' "$T/nopath.log"
WANT="  echo 'export PATH=\"\$PATH:\$HOME/.local/bin\"' >> ~/.zshrc"   # after the PATH: it never changes which claude runs
check '[ $rc = 0 ] && [ -L "$H/.local/bin/switchboard" ] && [ "$(last_line "$T/nopath.log")" = "$WANT" ] && plain_log "$T/nopath.log" | grep -q "^To type it, put ~/.local/bin on your PATH with this line, then open a new terminal:$"' \
  "~/.local/bin not on the PATH: linked there all the same, and the last line is the command that adds it"

accounts_file "$H" maya@northwind.example
run "$H" sh "$C/install.sh" $FLAGS > "$T/again.log" 2>&1; rc=$?
check '[ $rc = 0 ] && last_line "$T/again.log" | grep -q "^Done in [0-9][0-9]* s\. Type sb to see your accounts\.$" && grep -q "^Installing Switchboard [0-9.]* again in ~/.switchboard$" "$T/again.log"' \
  "with accounts in the engine, the next step is sb itself (and it says it is installing again)"
check 'run "$H" sh "$C/install.sh" --help | grep -q -- "--no-setup" && run "$H" sh "$C/install.sh" --help | grep -q -- "--no-link-app"' \
  "--help names --no-setup and --no-link-app"

# Ctrl-C at the first run's question, typed into the terminal: the first run ends there, and the installer still
# prints what it owes after it. (SIGINT is reset first: a job started by a script may inherit it ignored.)
cat > "$C/switchboard/__main__.py" <<'EOF'
"""A stand-in for `switchboard setup` that asks, and ends quietly on Ctrl-C (tests/install-check.sh)."""
import os
import sys
if sys.argv[1:2] == ['setup']:
    tty = open('/dev/tty')
    sys.stdout.write('Add another? [y/N] ')
    sys.stdout.flush()
    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'asking'), 'w').close()
    try:
        tty.readline()
        print('(stand-in) answered')
    except KeyboardInterrupt:
        print('\n(stand-in) Ctrl-C at the question')
EOF
sh -c 'i=0; while [ $i -lt 240 ] && [ ! -e "$1" ]; do i=$((i + 1)); sleep 0.25; done; sleep 0.5; printf "\003"; sleep 2' \
    sh "$H/.switchboard/app/asking" \
  | /usr/bin/python3 -I -c 'import os, signal, sys; signal.signal(signal.SIGINT, signal.SIG_DFL); os.execvp(sys.argv[1], sys.argv[1:])' \
    env -i HOME="$H" CFFIXED_USER_HOME="$H" PATH="/usr/bin:/bin:/usr/sbin:/sbin" LANG=en_US.UTF-8 TMPDIR="$T/tmp/" \
    SWITCHBOARD_APPLICATIONS="$T/Applications" script -q /dev/null sh "$C/install.sh" $FLAGS > "$T/ctrl-c.log" 2>&1; rc=$?
plain_log "$T/ctrl-c.log" | tail -n 4 | sed 's/^/    /'
check '[ $rc = 0 ] && plain_log "$T/ctrl-c.log" | grep -q "^(stand-in) Ctrl-C at the question$" && [ "$(last_line "$T/ctrl-c.log")" = "$WANT" ]' \
  "Ctrl-C at the first run's question: it ends there, and the installer still prints its last lines"
rm -rf "$T"
fi

if want 9; then
echo "== 9. The two background jobs, as the installer writes them (read from install.sh; launchd is never asked)"
# Each plist, written out with a home whose name needs escaping, then read back by plutil.
T="$(fresh_home)"
eval "$(grep '^xml()' "$HERE/install.sh")"
job_plist() {      # job_plist VARIABLE: the plist install.sh writes to "$VARIABLE", for the home /Users/a&b<c>
  body="$(awk -v want="  cat > \"\$$1\" <<EOF" '$0 == want {on = 1; next} on && $0 == "EOF" {exit} on {print}' "$HERE/install.sh")"
  [ -n "$body" ] || return 1
  ( HOME='/Users/a&b<c>'; DATA="$HOME/.switchboard"; LABEL=io.github.amyjainberkeley.switchboard; MENUBAR_LABEL="$LABEL.menubar"
    MENUBAR_CMD="$DATA/bin/switchboard-menubar"; PAGE_CMD="$DATA/bin/switchboard-page"; PY=/usr/bin/python3
    JOB_PATH="$HOME/.local/bin:/usr/bin:/bin"
    eval "cat <<EOF
$body
EOF" )
}
key() { plutil -extract "$2" raw -o - "$1" 2>/dev/null; }
job_plist MENUBAR_PLIST > "$T/menubar.plist" && job_plist PLIST > "$T/page.plist"
check 'plutil -lint -s "$T/menubar.plist" && plutil -lint -s "$T/page.plist"' "both are well-formed plists"
check '[ "$(key "$T/menubar.plist" Label)" = "$MENUBAR_ID" ] && [ "$(key "$T/menubar.plist" ProgramArguments.0)" = "/Users/a&b<c>/.switchboard/bin/switchboard-menubar" ] && [ "$(plutil -extract ProgramArguments raw -o - "$T/menubar.plist")" = 1 ]' \
  "the menu bar item's job ($MENUBAR_ID) runs its launcher alone"
check '[ "$(key "$T/menubar.plist" RunAtLoad)" = true ] && [ "$(key "$T/menubar.plist" KeepAlive.SuccessfulExit)" = false ] && [ "$(key "$T/menubar.plist" ProcessType)" = Interactive ]' \
  "it starts at login, comes back after a crash but not after Quit, and runs as an app"
check '[ "$(key "$T/menubar.plist" StandardErrorPath)" = "/Users/a&b<c>/.switchboard/menubar.log" ] && [ "$(key "$T/page.plist" Label)" = io.github.amyjainberkeley.switchboard ]' \
  "its log is ~/.switchboard/menubar.log"
check '[ "$(key "$T/page.plist" ProgramArguments.0)" = "/Users/a&b<c>/.switchboard/bin/switchboard-page" ] && [ "$(plutil -extract ProgramArguments raw -o - "$T/page.plist")" = 1 ] && [ "$(key "$T/page.plist" KeepAlive)" = true ] && ! plutil -extract EnvironmentVariables.PYTHONPATH raw -o - "$T/page.plist" >/dev/null 2>&1' \
  "the page's job runs the page's launcher alone, kept alive, with no PYTHONPATH"
# The page's launcher, as install.sh writes it: on the menu bar app's own Python when it is there, else the program's.
launcher() {       # launcher APP_PY PY: the launcher install.sh writes, for this test's own data folder
  body="$(awk '$0 == "cat > \"$PAGE_CMD.new\" <<EOF" {on = 1; next} on && $0 == "EOF" {exit} on {print}' "$HERE/install.sh")"
  [ -n "$body" ] || return 1
  ( DATA="$T/data"; APP_PY="$1"; PY="$2"; PAGE_CHECK=""        # (the check is only for Apple's Python: case 21)
    RUNPY='import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module("switchboard", run_name="__main__", alter_sys=True)'
    eval "cat <<EOF
$body
EOF" )
}
mkdir -p "$T/data/app" "$T/app/Contents/MacOS" "$T/py"
printf '#!/bin/sh\necho "app-python $*"\n' > "$T/app/Contents/MacOS/python"
printf '#!/bin/sh\necho "program-python $*"\n' > "$T/py/python3"
chmod +x "$T/app/Contents/MacOS/python" "$T/py/python3"
launcher "$T/app/Contents/MacOS/python" "$T/py/python3" > "$T/page-1"; launcher "" "$T/py/python3" > "$T/page-2"
out1="$(sh "$T/page-1" | sed 's/ -c .* \// \//')"; out2="$(sh "$T/page-2" | sed 's/ -c .* \// \//')"
rm -rf "$T/app"; out3="$(sh "$T/page-1" | sed 's/ -c .* \// \//')"
check '[ "$out1" = "app-python -I -B $T/data/app serve" ] && [ "$out2" = "program-python -I -B $T/data/app serve" ] && [ "$out3" = "program-python -I -B $T/data/app serve" ]' \
  "the page's launcher runs switchboard serve on the menu bar app's own Python, else (or once the app is gone) on the program's (${out1:-nothing} / ${out3:-nothing})"
rm -rf "$T"
fi

if want 10; then
echo "== 10. An older link app is built again by default; a Mac with no Python gets Switchboard's own for the page"
# An older install's link app (~/Applications/Switchboard.app, known by its bundle identifier): the installer tries
# to build the new one, as it does by default. A swiftc that fails stands first on the PATH, so nothing is built or
# registered. Beside it, a Switchboard Links.app an earlier build left half-made (its identifier, no program).
T="$(fresh_home)"; H="$T/home"
mkdir -p "$T/guard"
printf '#!/bin/sh\nexit 1\n' > "$T/guard/swiftc"
chmod +x "$T/guard/swiftc"
fake_app "$H/Applications/Switchboard.app" "$LINKS_ID" Switchboard
fake_app "$H/Applications/Switchboard Links.app" "$LINKS_ID" SwitchboardLinks
EXTRA_ENV="PATH=$T/guard:$H/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-menu-app --port "$PORT" > "$T/older.log" 2>&1; rc=$?
sed 's/^/    /' "$T/older.log"
check '[ $rc = 0 ] && grep -q -e "^Building the link app… could not" -e "^Skipped the link app" "$T/older.log" && [ -d "$H/Applications/Switchboard.app" ]' \
  "an older link app: the new one is tried (here it can't be built, so the older one stays)"
check '[ "$(ls -A "$H/Applications")" = "Switchboard.app" ] && [ -z "$(ls -A "$T/tmp" | grep -i links)" ]' \
  "the half-made one is gone, and the build that failed left nothing (not in ~/Applications, not in the temporary folder)"
check '[ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-menu-app" ]' \
  "and app/choices names nothing for it: the link app is the default, so switchboard update tries again"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-menu-app --no-link-app --port "$PORT" > "$T/older-no.log" 2>&1; rc=$?
check '[ $rc = 0 ] && ! grep -qi "link app" "$T/older-no.log" && [ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-menu-app --no-link-app" ] && [ -d "$H/Applications/Switchboard.app" ]' \
  "--no-link-app: no line about it, the older app is left as it is, and app/choices keeps it out"
EXTRA_ENV=""
rm -rf "$T"
# No Python for the page (SWITCHBOARD_PAGE_PYTHON=none stands in for a Mac without Apple's command line tools):
# Switchboard's own runs the program (and with it the engine), downloaded once.
T="$(fresh_home)"; H="$T/home"
EXTRA_ENV="SWITCHBOARD_PAGE_PYTHON=none"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --own-python --port "$PORT" > "$T/nopy.log" 2>&1; rc=$?
sed 's/^/    /' "$T/nopy.log"
check '[ $rc = 0 ] && [ "$(grep -c "^Downloading " "$T/nopy.log")" = 1 ] && grep -q "^$OWN_PY_LINE [0-9.]*, kept in ~/.switchboard (27 MB, once)… [0-9][0-9]* s$" "$T/nopy.log"' \
  "no Python for the page: Switchboard's own is downloaded once (for the program and its engine), its line says whose it is and where it is kept"
check 'grep -qxF "PY=\"$H/.switchboard/runtime/python/bin/python3\"" "$H/.switchboard/bin/switchboard" && run "$H" "$H/.switchboard/bin/switchboard" --version | grep -q "^switchboard [0-9]"' \
  "the switchboard command runs on it (without the internet too: case 11)"
EXTRA_ENV=""
rm -rf "$T"
fi

if want 11; then
echo "== 11. Offline: --no-menu-app takes the menu bar item out, notifications in and out, the uninstaller's last lines, a half-made link app, no Python"
# Every run here has the internet blocked (a proxy that answers nothing), so nothing is downloaded (--own-python waits
# for it, on the Python found here): each run takes seconds. The menu bar item's files are stand-ins in this home.
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
T="$(fresh_home)"; H="$T/home"
EXTRA_ENV="$OFFLINE"
mkdir -p "$H/.switchboard/bin" "$H/.switchboard/menubar/Switchboard.app/Contents" "$H/Library/LaunchAgents"
printf '#!/bin/sh\nexit 0\n' > "$H/.switchboard/bin/switchboard-menubar"; chmod +x "$H/.switchboard/bin/switchboard-menubar"
printf '<plist/>\n' > "$H/Library/LaunchAgents/$MENUBAR_ID.plist"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --own-python --port "$PORT" > "$T/nomenu.log" 2>&1; rc=$?
sed 's/^/    /' "$T/nomenu.log"
check '[ $rc = 1 ] && grep -q "^Removed the menu bar item\.$" "$T/nomenu.log" && [ "$(grep -c "menu bar" "$T/nomenu.log")" = 1 ]' \
  "--no-menu-app where the menu bar item is: one line says it was removed (offline, its own Python is left for later)"
check '[ ! -e "$H/.switchboard/bin/switchboard-menubar" ] && [ ! -e "$H/Library/LaunchAgents/$MENUBAR_ID.plist" ] && [ ! -e "$H/.switchboard/menubar" ] && [ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-menu-app --no-link-app --own-python" ]' \
  "its launcher, its job's file (with --no-agent launchd is not asked) and its app are gone; app/choices keeps it out"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --own-python --port "$PORT" > "$T/nomenu-2.log" 2>&1
check '! grep -q "menu bar" "$T/nomenu-2.log"' "with nothing of it left, the next run says nothing about it"

# Notifications on, in a Claude Code settings file of this home's own, to see the uninstaller give it back as it was.
mkdir -p "$H/.claude"
printf '{\n  "model": "claude-opus-5-5",\n  "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}\n}\n' > "$H/.claude/settings.json"
cp "$H/.claude/settings.json" "$T/claude-settings-before.json"
run "$H" "$H/.switchboard/bin/switchboard" notify on > "$T/notify.log" 2>&1; nrc=$?
check '[ $nrc = 0 ] && [ "$(grep -o "# switchboard notify" "$H/.claude/settings.json" | wc -l | tr -d " ")" = 3 ] && grep -q "\"command\": \"say done\"" "$H/.claude/settings.json" && [ "$(tail -n 1 "$T/notify.log")" = "Notifications: on" ]' \
  "switchboard notify on (offline too): the three notification hooks go in beside the hook already there"
run "$H" "$H/.switchboard/bin/switchboard" notify > "$T/notify-show.log" 2>&1
check '[ "$(head -n 2 "$T/notify-show.log")" = "Notifications: on
Done and needs you in Claude Code: on" ]' "switchboard notify says they are on, and run"

# The uninstaller from the installed copy: its last lines stay true after it has removed itself.
run "$H" sh "$H/.switchboard/app/uninstall.sh" --no-agent > "$T/uninstall.log" 2>&1; urc=$?
sed 's/^/    /' "$T/uninstall.log"
KEEP_LINES="Kept your settings in ~/.switchboard (a reinstall picks them up).
The login Claude Code is using was not touched.
To remove them too, run:
sh ~/.switchboard/uninstall.sh --all"
check '[ $urc = 0 ] && [ "$(tail -n 4 "$T/uninstall.log")" = "$KEEP_LINES" ] && [ -f "$H/.switchboard/settings.json" ] && [ ! -e "$H/.switchboard/app/uninstall.sh" ] && [ -f "$H/.switchboard/uninstall.sh" ]' \
  "a plain uninstall with no account added: its last lines name only the settings it kept, and how to remove them"
check '! grep -qF "$H" "$T/uninstall.log" && grep -q "^Removed the switchboard command from ~/.local/bin\.$" "$T/uninstall.log" && grep -q "^Removed the program and its commands (~/.switchboard/app, ~/.switchboard/bin)\.$" "$T/uninstall.log"' \
  "every path under the home folder is said with ~, as the installer says it"
check 'grep -q "^Notification hooks: taken out\.$" "$T/uninstall.log" && cmp -s "$T/claude-settings-before.json" "$H/.claude/settings.json"' \
  "it takes the notification hooks out: Claude Code's settings file is byte for byte as before"
T2="$(fresh_home)"; H2="$T2/home"
mkdir -p "$H2/.switchboard/app" "$H2/.switchboard/bin"
cp "$HERE/uninstall.sh" "$H2/.switchboard/app/"
run "$H2" sh "$H2/.switchboard/app/uninstall.sh" --no-agent > "$T2/uninstall.log" 2>&1; urc=$?
check '[ $urc = 0 ] && [ ! -e "$H2/.switchboard" ] && [ "$(tail -n 2 "$T2/uninstall.log")" = "Nothing of Switchboard is left.
The login Claude Code is using was not touched." ]' \
  "with no settings or accounts to keep, it says nothing is left, and nothing is"
rm -rf "$T2"
# What it keeps is named only when it is there: an account in the engine's accounts file (its login in the Keychain), a
# key in the gateway's list (the key in the Keychain), or both. Made-up records only: a plain uninstall never asks the
# engine or the Keychain, and with no switchboard command here it runs no tidy either.
for kept in accounts keys both; do
  T2="$(fresh_home)"; H2="$T2/home"
  mkdir -p "$H2/.switchboard/app" "$H2/.switchboard/bin"
  cp "$HERE/uninstall.sh" "$H2/.switchboard/app/"
  printf '{"port": %s}\n' "$PORT" > "$H2/.switchboard/settings.json"
  accounts_file "$H2"
  case "$kept" in accounts|both) accounts_file "$H2" maya@northwind.example ;; esac
  case "$kept" in keys|both)
    printf '{"lanes": [{"name": "work", "kind": "anthropic", "last4": "abcd"}]}\n' > "$H2/.switchboard/gateway-lanes.json" ;; esac
  case "$kept" in
    accounts) words="settings and accounts"; theirs="their logins" ;;
    keys) words="settings and keys"; theirs="the keys" ;;
    both) words="settings, accounts and keys"; theirs="their logins and the keys" ;;
  esac
  run "$H2" sh "$H2/.switchboard/app/uninstall.sh" --no-agent > "$T2/uninstall.log" 2>&1; urc=$?
  check '[ $urc = 0 ] && [ "$(tail -n 4 "$T2/uninstall.log")" = "Kept your $words in ~/.switchboard (a reinstall picks them up).
The login Claude Code is using was not touched.
To remove them too, with $theirs in your Keychain, run:
sh ~/.switchboard/uninstall.sh --all" ] && [ -f "$H2/.switchboard/accounts.json" ]' \
    "kept $words: its last lines name them, and the way to remove them with what is in the Keychain"
  rm -rf "$T2"
done

# The link app is built by default, and an older install's link app is kept until the new one is built; a Switchboard
# Links.app an earlier build left half-made (its identifier, no program) would hide it from the page. A swiftc that
# fails stands first on the PATH.
mkdir -p "$T/guard"; printf '#!/bin/sh\nexit 1\n' > "$T/guard/swiftc"; chmod +x "$T/guard/swiftc"
fake_app "$H/Applications/Switchboard.app" "$LINKS_ID" Switchboard
printf '#!/bin/sh\necho "$HOME/Applications/Other.app"\n' > "$H/Applications/Switchboard.app/Contents/MacOS/Switchboard"
chmod +x "$H/Applications/Switchboard.app/Contents/MacOS/Switchboard"
fake_app "$H/Applications/Switchboard Links.app" "$LINKS_ID" SwitchboardLinks
EXTRA_ENV="$OFFLINE PATH=$T/guard:$H/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-menu-app --own-python --port "$PORT" > "$T/links.log" 2>&1
sed 's/^/    /' "$T/links.log"
check 'grep -q -e "^Building the link app… could not" -e "^Skipped the link app" "$T/links.log" && [ "$(ls -A "$H/Applications")" = "Switchboard.app" ] && [ -z "$(ls -A "$T/tmp" | grep -i links)" ]' \
  "the half-made link app is taken out, and the build that failed left nothing (~/Applications, the temporary folder)"
check 'run "$H" env PYTHONPATH="$H/.switchboard/app" /usr/bin/python3 -B -c "
from switchboard import linkapp
assert linkapp.installed() and linkapp.app_path().endswith(\"/Applications/Switchboard.app\"), linkapp.app_path()
"' "so the page finds the older link app that works"

# No Python for the page and no internet: Switchboard's own can't be downloaded.
rm -rf "$T"; T="$(fresh_home)"; H="$T/home"
EXTRA_ENV="SWITCHBOARD_PAGE_PYTHON=none $OFFLINE"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --port "$PORT" > "$T/nopy-offline.log" 2>&1; rc=$?
sed 's/^/    /' "$T/nopy-offline.log"
check '[ $rc = 1 ] && grep -q "^Switchboard: needs the internet once, to download its own Python (27 MB)\. Run this again when you are online\.$" "$T/nopy-offline.log" && ! grep -q xcode-select "$T/nopy-offline.log" && [ ! -e "$H/.switchboard" ]' \
  "no Python and no internet: it stops, says it needs the internet once for its own Python, and leaves nothing behind"
check '[ "$(grep -c "^Downloading " "$T/nopy-offline.log")" = 1 ] && grep -q "^$OWN_PY_LINE [0-9.]*, kept in ~/.switchboard (27 MB, once)… $OWN_PY_FAIL\.$" "$T/nopy-offline.log"' \
  "its one download line says whose Python it is and where it is kept, then why it stopped"
EXTRA_ENV=""
rm -rf "$T"
fi

if want 12; then
echo "== 12. Switchboard's own Python can't be downloaded, the menu bar item is wanted, and a framework Python is here"
# GitHub is blocked for curl alone (a ~/.curlrc in the throwaway home sends github.com to a port where nothing
# answers). python.org's and Homebrew's Pythons are framework builds: the program can run on one, the menu bar item
# can't, so with no Python it can draw with and no download, the menu bar item waits.
FW=""
for p in /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
         /usr/local/bin/python3.14 /usr/local/bin/python3.13 /usr/local/bin/python3.12 \
         /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.12/bin/python3; do
  if [ -x "$p" ] && "$p" -I -B -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) and getattr(sys, "_framework", "") else 1)' 2>/dev/null; then
    FW="$p"; break
  fi
done
if [ -z "$FW" ]; then
  ok "(no framework Python 3.12 or newer on this Mac, so there is nothing to fall back to: skipped)"
else
  T="$(fresh_home)"; H="$T/home"; C="$T/clone"
  clone_tree "$C"
  C="$(cd "$C" && pwd)"
  menubar_pins > "$C/menubar-requirements.txt"
  printf 'connect-to = github.com:443:127.0.0.1:9\n' > "$H/.curlrc"
  t0="$(now)"
  run "$H" sh "$C/install.sh" --no-agent --no-open --no-link-app --port "$PORT" > "$T/fallback.log" 2>&1; rc=$?
  t1="$(now)"
  sed 's/^/    /' "$T/fallback.log"
  check '[ $rc = 0 ] && grep -q "^No menu bar item for now: it needs a Python to draw with\. To add it, run switchboard update when you are online\.$" "$T/fallback.log" && grep -q "^$OWN_PY_LINE [0-9.]*, kept in ~/.switchboard (27 MB, once)… $OWN_PY_FAIL\.$" "$T/fallback.log" && [ "$(grep -c "menu bar" "$T/fallback.log")" = 1 ]' \
    "the download fails: its line says whose Python it is and where it is kept, and one line says the menu bar item waits ($(echo "$t1 - $t0" | bc) s)"
  EPY_NOW="$(/usr/bin/python3 -I -B -c 'import json, sys; r = json.load(open(sys.argv[1])); print(r["python"] if r["python_source"] == "found" else "")' "$H/.switchboard/runtime/python.json" 2>/dev/null)"
  check '[ -n "$EPY_NOW" ] && run "$H" "$H/.switchboard/bin/switchboard" --version | grep -q "^switchboard [0-9]"' \
    "the program (with its engine) runs on the Python found here (${EPY_NOW:-none})"
  check '[ ! -e "$H/.switchboard/bin/switchboard-menubar" ] && [ ! -e "$H/.switchboard/runtime/python" ] && [ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-link-app" ] && last_line "$T/fallback.log" | grep -q "^Done in [0-9][0-9]* s\. Type sb setup"' \
    "no menu bar launcher, no half-downloaded Python, and app/choices still wants the menu bar, so switchboard update adds it"
  rm -rf "$T"
fi
fi

if want 13; then
echo "== 13. claude-mem and codegraph installed by Switchboard (stand-ins served on this Mac), then the uninstaller takes them out"
# Offline as in case 11: nothing comes from anywhere but 127.0.0.1. The program's own install, then each pack's switch
# through Switchboard's own code (python3 -m switchboard.packs.install on ... --yes) with pins that point at made-up
# releases (tests/fixtures/packs/fake_releases.py, stopped by its PID at the end), then the plain uninstaller. Claude
# Code's files are the person's own and must come back byte for byte; claude-mem's notes stay.
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
T="$(fresh_home)"; H="$T/home"
EXTRA_ENV="$OFFLINE"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --own-python --port "$PORT" > "$T/install.log" 2>&1
mkdir -p "$H/.claude" "$H/.claude-mem" "$T/rel" "$T/conf"
printf '{\n    "hooks": {"Stop": []},\n    "permissions": {"allow": ["Bash(ls:*)"]},\n    "theme": "dark"\n}\n' > "$H/.claude/settings.json"
printf '{\n  "numStartups": 3,\n  "mcpServers": {}\n}\n' > "$H/.claude.json"
WPORT="$(/usr/bin/python3 -I -B -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
printf '{"CLAUDE_MEM_WORKER_PORT": "%s"}\n' "$WPORT" > "$H/.claude-mem/settings.json"
# Every file, link and folder of the home but Switchboard's own and claude-mem's notes (and the two command links the
# uninstaller removes anyway), with a hash for each file.
list_home() { (cd "$1" && find . \( -path ./.switchboard -o -path ./.claude-mem -o -path ./.local/bin/switchboard -o -path ./.local/bin/sb \
  \) -prune -o -print | sort | while read -r f; do
    if [ -L "$f" ]; then echo "$f -> $(readlink "$f")"; elif [ -f "$f" ]; then echo "$f $(shasum -a 256 < "$f" | cut -c 1-16)"; else echo "$f/"; fi
  done); }
list_home "$H" > "$T/before.txt"
/usr/bin/python3 -I -B "$HERE/tests/fixtures/packs/fake_releases.py" "$T/rel" "$T/conf" > /dev/null 2>&1 &
SRV=$!
i=0; while [ $i -lt 100 ] && [ ! -s "$T/rel/port" ]; do i=$((i + 1)); sleep 0.1; done
PACKS_CLI='import sys; sys.path.insert(0, sys.argv[1]); from switchboard.packs import install; sys.exit(install.main(sys.argv[2:]))'
for pack in claude-mem codegraph; do
  run "$H" /usr/bin/python3 -I -B -c "$PACKS_CLI" "$H/.switchboard/app" on "$pack" --yes --pins "$T/rel/pins.json" > "$T/on-$pack.log" 2>&1
  sed 's/^/    /' "$T/on-$pack.log"
done
check 'grep -q "is installed and running on port $WPORT" "$T/on-claude-mem.log" && curl -fs --noproxy "*" "http://127.0.0.1:$WPORT/api/health" | grep -q "ok"' \
  "claude-mem through Switchboard's own code: Node, Bun and uv from their pins (made up), its installer, its worker answers"
check 'grep -q "is installed and connected to Claude Code" "$T/on-codegraph.log" && grep -q "\"codegraph\"" "$H/.claude.json" && grep -q "mcp__codegraph__" "$H/.claude/settings.json"' \
  "codegraph: the checked release, its MCP entry and its one allow rule"
run "$H" sh "$H/.switchboard/app/uninstall.sh" --no-agent > "$T/uninstall.log" 2>&1; urc=$?
sed 's/^/    /' "$T/uninstall.log"
check '[ $urc = 0 ] && grep -q "^codegraph: taken out; project maps stay\.$" "$T/uninstall.log" && grep -q "^claude-mem: taken out; your notes stay in ~/.claude-mem\.$" "$T/uninstall.log"' \
  "the uninstaller says it took both out"
list_home "$H" > "$T/after.txt"
diff "$T/before.txt" "$T/after.txt" > "$T/diff.txt"
check '[ ! -s "$T/diff.txt" ]' "every file of the home but ~/.switchboard and claude-mem's notes is as it was, byte for byte$( [ -s "$T/diff.txt" ] && printf ' (differs: %s)' "$(head -6 "$T/diff.txt" | tr '\n' ' ')")"
check '! curl -fs --noproxy "*" "http://127.0.0.1:$WPORT/api/health" >/dev/null 2>&1 && [ -f "$H/.claude-mem/settings.json" ]' \
  "claude-mem's worker is stopped; its notes stay"
kill "$SRV" 2>/dev/null; wait "$SRV" 2>/dev/null
rm -rf "$T"
fi

if want 14; then
echo "== 14. A tidy that can't put everything back keeps the program; a page started by hand is stopped; no backup copy is left"
# Offline as in case 11. House rules go in through the installed program's own code, then CLAUDE.md can't be read: the
# uninstaller must stop before the program and its records go, and finish once that is fixed. The page started by hand
# is a stand-in with the command line `switchboard serve` has (only its -c code differs), stopped by the uninstaller.
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
T="$(fresh_home)"; H="$T/home"
EXTRA_ENV="$OFFLINE"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --own-python --port "$PORT" > "$T/install.log" 2>&1
mkdir -p "$H/.claude"
printf '# mine\n' > "$H/.claude/CLAUDE.md"
printf '{\n  "model": "opus"\n}\n' > "$H/.claude/settings.json"
cp "$H/.claude/CLAUDE.md" "$T/claude-md-before"; cp "$H/.claude/settings.json" "$T/settings-before"
run "$H" "$H/.switchboard/bin/switchboard" notify on > "$T/notify.log" 2>&1
run "$H" /usr/bin/python3 -I -B -c 'import sys; sys.path.insert(0, sys.argv[1]); from switchboard import claudecode; claudecode.turn("house", True)' "$H/.switchboard/app" > "$T/house.log" 2>&1
check '[ -f "$H/.claude/settings.json.switchboard-backup" ] && grep -q "switchboard" "$H/.claude/CLAUDE.md"' \
  "notifications on (a backup copy beside Claude Code's settings) and house rules in, through the installed program"
# Started from a subshell, so this shell never reports it when it is stopped; env runs the stand-in in its own place,
# so the PID written down is the stand-in's own.
( env -i HOME="$H" CFFIXED_USER_HOME="$H" PATH=/usr/bin:/bin /usr/bin/python3 -I -B -c 'import time; time.sleep(300)' \
  "$H/.switchboard/app" serve > /dev/null 2>&1 & echo $! > "$T/page.pid" )
PAGE="$(cat "$T/page.pid")"
i=0; while [ $i -lt 40 ] && ! ps -p "$PAGE" -o command= | grep -q " $H/.switchboard/app serve$"; do i=$((i + 1)); sleep 0.1; done
chmod 000 "$H/.claude/CLAUDE.md"
run "$H" sh "$H/.switchboard/app/uninstall.sh" --no-agent > "$T/uninstall-1.log" 2>&1; urc=$?
chmod 644 "$H/.claude/CLAUDE.md"
sed 's/^/    /' "$T/uninstall-1.log"
check '[ $urc = 1 ] && grep -q "^House rules: left as it is (.*CLAUDE.md: Permission denied)$" "$T/uninstall-1.log" && [ "$(tail -n 1 "$T/uninstall-1.log")" = "Not everything could be put back (named above), so the program and ~/.switchboard were kept. Fix that, then run this again: sh ~/.switchboard/app/uninstall.sh" ] && [ -x "$H/.switchboard/bin/switchboard" ] && [ -f "$H/.switchboard/app/uninstall.sh" ]' \
  "CLAUDE.md can't be read: the uninstaller names it, says how to run it again, exits 1 and keeps the program and its records"
check 'grep -q "^Notification hooks: taken out\.$" "$T/uninstall-1.log" && grep -q "^Stopped the page you started by hand\.$" "$T/uninstall-1.log" && ! kill -0 "$PAGE" 2>/dev/null' \
  "the other steps still ran, and the page started by hand (switchboard serve) was stopped"
! kill -0 "$PAGE" 2>/dev/null || kill "$PAGE"              # only when the uninstaller did not stop it
run "$H" sh "$H/.switchboard/app/uninstall.sh" --no-agent > "$T/uninstall-2.log" 2>&1; urc=$?
sed 's/^/    /' "$T/uninstall-2.log"
check '[ $urc = 0 ] && cmp -s "$T/claude-md-before" "$H/.claude/CLAUDE.md" && cmp -s "$T/settings-before" "$H/.claude/settings.json" && [ ! -e "$H/.switchboard/app" ] && [ ! -e "$H/.switchboard/bin" ]' \
  "run again once it is fixed: CLAUDE.md and settings.json byte for byte as before, and the program is gone"
check '[ -z "$(ls -A "$H/.claude" | grep -e switchboard-backup -e switchboard-tmp)" ]' \
  "no .switchboard-backup copy of Claude Code's settings is left behind"
EXTRA_ENV=""
rm -rf "$T"
fi

if want 15; then
echo "== 15. Nothing turned on: Claude Code's own files, which Switchboard never changed, don't stop the uninstaller"
# Offline as in case 11. A fresh install with nothing turned on, then Claude Code's folder as a person can have it:
# settings.json that is not JSON, and CLAUDE.md a link into a dotfiles folder. Nothing of Switchboard's is in either,
# so there is nothing to put back there: the uninstaller exits 0 and the program goes; both files stay as they were.
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
T="$(fresh_home)"; H="$T/home"
EXTRA_ENV="$OFFLINE"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --own-python --port "$PORT" > "$T/install.log" 2>&1
mkdir -p "$H/.claude" "$T/dotfiles"
printf 'not json{' > "$H/.claude/settings.json"
printf '# mine, kept in my dotfiles\n' > "$T/dotfiles/CLAUDE.md"
ln -s "$T/dotfiles/CLAUDE.md" "$H/.claude/CLAUDE.md"
run "$H" sh "$H/.switchboard/app/uninstall.sh" --no-agent > "$T/uninstall.log" 2>&1; urc=$?
sed 's/^/    /' "$T/uninstall.log"
check '[ $urc = 0 ] && ! grep -q ": left as " "$T/uninstall.log" && [ ! -e "$H/.switchboard/app" ] && [ ! -e "$H/.switchboard/bin" ]' \
  "settings.json not JSON and CLAUDE.md a link: the uninstaller exits 0, names nothing as left, and the program is gone"
check '[ "$(cat "$H/.claude/settings.json")" = "not json{" ] && [ "$(readlink "$H/.claude/CLAUDE.md")" = "$T/dotfiles/CLAUDE.md" ] && [ "$(cat "$T/dotfiles/CLAUDE.md")" = "# mine, kept in my dotfiles" ]' \
  "both files are as they were, and CLAUDE.md is still a link"
EXTRA_ENV=""
rm -rf "$T"
fi

if want 16; then
echo "== 16. The menu bar item's app in Applications: where it goes, what opening it runs, update, uninstall"
# From a copy of the program with the menu bar's packages pinned (downloaded, as in case 3). /Applications is a folder
# of this test's own (SWITCHBOARD_APPLICATIONS), ~/Applications the throwaway home's. --no-agent everywhere, so launchd
# and Launch Services are never asked. Opening the app is stood in for by starting its Python the way macOS starts an
# opened app (no arguments, nothing on stdin, Info.plist's LSEnvironment), in front of a stand-in program.
T="$(fresh_home)"; T="$(cd "$T" && pwd)"; H="$T/home"; C="$T/clone"; A="$T/Applications"   # (TMPDIR's own // gone)
APP="$A/Switchboard.app"; MINE="$H/Applications/Switchboard.app"
clone_tree "$C"
C="$(cd "$C" && pwd)"
menubar_pins > "$C/menubar-requirements.txt"
FLAGS="--no-agent --no-open --no-link-app --port $PORT"
bundle_id() { plutil -extract CFBundleIdentifier raw -o - "$1/Contents/Info.plist" 2>/dev/null; }
t0="$(now)"
run "$H" sh "$C/install.sh" $FLAGS > "$T/install-1.log" 2>&1; rc=$?
t1="$(now)"
sed 's/^/    /' "$T/install-1.log"
check '[ $rc = 0 ] && grep -qxF "Added Switchboard to $A." "$T/install-1.log" && [ "$(grep -c "Switchboard to" "$T/install-1.log")" = 1 ]' \
  "a default install puts the app in /Applications, and one line says so ($(echo "$t1 - $t0" | bc) s)"
check '[ "$(bundle_id "$APP")" = "$MENUBAR_ID" ] && [ -x "$APP/Contents/MacOS/python" ] && [ ! -e "$H/Applications" ] && [ ! -e "$H/.switchboard/menubar/Switchboard.app" ]' \
  "it is the menu bar's app (its identifier, its Python); nothing in ~/Applications, nor hidden in ~/.switchboard/menubar"
check 'grep -qxF "APP_PY=\"$APP/Contents/MacOS/python\"" "$H/.switchboard/bin/switchboard-page" && [ "$(/usr/bin/python3 -I -B -c "import json, sys; print(json.load(open(sys.argv[1]))[\"page_python\"])" "$H/.switchboard/runtime/python.json")" = "$APP/Contents/MacOS/python" ]' \
  "the page's job runs on the app's own Python, so macOS names Switchboard when it asks (runtime/python.json says so)"
check 'run "$H" env PYTHONPATH="$H/.switchboard/app" /usr/bin/python3 -B -c "
from switchboard import menubar_wrap as w
assert w.home() == \"$A\" and w.present() == \"$APP\", (w.home(), w.present())
"' "the menu bar's job runs that copy: each start of the menu bar makes the app where this install's is"
run "$H" "$H/.switchboard/bin/switchboard" doctor > "$T/doctor-1.log" 2>&1
check 'grep -qxF " ok  Menu bar app ($APP)" "$T/doctor-1.log"' "switchboard doctor names where the app is"
# Opened: in front of a stand-in program (the real one would start the menu bar here).
cp "$H/.switchboard/app/switchboard/__main__.py" "$T/main.py.real"
cat > "$H/.switchboard/app/switchboard/__main__.py" <<'EOF'
"""A stand-in (tests/install-check.sh): it says how it was started."""
import os
import sys
print('(stand-in) switchboard %s, on %s, data %s' % (' '.join(sys.argv[1:]), os.path.realpath(sys.executable),
                                                     os.environ.get('SWITCHBOARD_HOME')))
EOF
LSENV="$(plutil -extract LSEnvironment json -o - "$APP/Contents/Info.plist" | /usr/bin/python3 -I -B -c 'import json, sys
print(" ".join("%s=%s" % kv for kv in sorted(json.load(sys.stdin).items())))')"
out="$(env -i HOME="$H" CFFIXED_USER_HOME="$H" PATH=/usr/bin:/bin:/usr/sbin:/sbin TMPDIR="$T/tmp/" $LSENV "$APP/Contents/MacOS/python" < /dev/null 2>&1)"
want_out="(stand-in) switchboard menubar --opened, on $(cd -P "$APP/Contents/MacOS" && pwd)/python, data $(cd -P "$H/.switchboard" && pwd)"
check '[ "$out" = "$want_out" ]' \
  "opened (its Python started as macOS starts it, with Info.plist's LSEnvironment: $LSENV), it runs switchboard menubar --opened from the app itself, on this install's data folder (${out:-nothing})"
check '[ ! -e "$APP/Contents/Resources/__pycache__" ]' "and writes nothing into the app"
out="$(env -i HOME="$H" CFFIXED_USER_HOME="$H" PATH=/usr/bin:/bin:/usr/sbin:/sbin TMPDIR="$T/tmp/" "$APP/Contents/MacOS/python" < /dev/null 2>&1)"
check '[ -z "$out" ]' "started without them (not opened by macOS), it runs nothing"
cp "$T/main.py.real" "$H/.switchboard/app/switchboard/__main__.py"
# Another app named Switchboard in /Applications: left alone, and the app goes to ~/Applications.
rm -rf "$APP"; fake_app "$APP" com.example.someone-else Switchboard
printf '#!/bin/sh\ntouch "%s/ran-foreign"\n' "$T" > "$APP/Contents/MacOS/Switchboard"; chmod +x "$APP/Contents/MacOS/Switchboard"
sum="$(cksum < "$APP/Contents/Info.plist")"
run "$H" sh "$C/install.sh" $FLAGS > "$T/install-2.log" 2>&1; rc=$?
sed 's/^/    /' "$T/install-2.log"
check '[ $rc = 0 ] && grep -qxF "$APP is another app, so it was left alone." "$T/install-2.log" && grep -qxF "Added Switchboard to ~/Applications." "$T/install-2.log"' \
  "another app at /Applications/Switchboard.app: one line says it was left alone, one that the app is in ~/Applications"
check '[ "$(cksum < "$APP/Contents/Info.plist")" = "$sum" ] && [ ! -e "$T/ran-foreign" ] && [ "$(bundle_id "$MINE")" = "$MENUBAR_ID" ]' \
  "that app is untouched and never run; ~/Applications/Switchboard.app is the menu bar's"
# /Applications that needs a password: ~/Applications.
rm -rf "$APP" "$MINE"; chmod 555 "$A"
run "$H" sh "$C/install.sh" $FLAGS > "$T/install-3.log" 2>&1; rc=$?
chmod 755 "$A"
check '[ $rc = 0 ] && grep -qxF "Added Switchboard to ~/Applications." "$T/install-3.log" && [ "$(bundle_id "$MINE")" = "$MENUBAR_ID" ] && [ -z "$(ls -A "$A")" ]' \
  "/Applications that can't be written to without a password: the app goes to ~/Applications"
run "$H" "$H/.switchboard/bin/switchboard" doctor > "$T/doctor-2.log" 2>&1
check 'grep -qxF " ok  Menu bar app (~/Applications/Switchboard.app)" "$T/doctor-2.log"' "doctor names it there"
# switchboard update replaces an older app of ours in /Applications (from an older version: no data folder named,
# version 0.1.0, an older program), and the copy left in ~/Applications goes.
fake_app "$APP" "$MENUBAR_ID" python '<key>CFBundleShortVersionString</key><string>0.1.0</string>'
printf '#!/bin/sh\necho an older Python\n' > "$APP/Contents/MacOS/python"; chmod +x "$APP/Contents/MacOS/python"
run "$H" "$H/.switchboard/bin/switchboard" update > "$T/update.log" 2>&1; rc=$?
sed 's/^/    /' "$T/update.log"
EPY_REAL="$(/usr/bin/python3 -I -B -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$H/.switchboard/runtime/env/bin/python")"
check '[ $rc = 0 ] && grep -qxF "Updating from $C" "$T/update.log" && grep -qxF "Added Switchboard to $A." "$T/update.log"' \
  "switchboard update runs the installer again, and the app is put back in /Applications"
check '[ "$(plutil -extract CFBundleShortVersionString raw -o - "$APP/Contents/Info.plist")" != 0.1.0 ] && plutil -extract LSEnvironment xml1 -o - "$APP/Contents/Info.plist" >/dev/null && cmp -s "$APP/Contents/MacOS/python" "$EPY_REAL"' \
  "the older app of ours is replaced: its Info.plist is this version's and its Python is the menu bar's"
check '[ ! -e "$MINE" ]' "and the copy left in ~/Applications is gone (no second Switchboard for Spotlight)"
# --no-menu-app takes it out of Applications too (opened, it would start the menu bar again); without it, it is back.
run "$H" sh "$C/install.sh" $FLAGS --no-menu-app > "$T/install-4.log" 2>&1; rc=$?
check '[ $rc = 0 ] && grep -qxF "Removed the menu bar item." "$T/install-4.log" && [ ! -e "$APP" ] && [ ! -e "$MINE" ]' \
  "--no-menu-app: the menu bar item goes, and its app with it, in one line"
check 'grep -qxF "APP_PY=\"\"" "$H/.switchboard/bin/switchboard-page"' "and the page's job is back on the program's own Python"
run "$H" sh "$C/install.sh" $FLAGS > "$T/install-5.log" 2>&1; rc=$?
check '[ $rc = 0 ] && [ "$(bundle_id "$APP")" = "$MENUBAR_ID" ]' "without it again, the app is back in /Applications"
# The uninstaller: this install's app goes; another install's (Switchboard's, made for another data folder) stays.
fake_app "$MINE" "$MENUBAR_ID" python '<key>SwitchboardData</key><string>/Users/someone/.switchboard</string>'
sum="$(cksum < "$MINE/Contents/Info.plist")"
run "$H" sh "$H/.switchboard/app/uninstall.sh" --no-agent > "$T/uninstall.log" 2>&1; urc=$?
sed 's/^/    /' "$T/uninstall.log"
check '[ $urc = 0 ] && [ ! -e "$APP" ] && grep -qxF "Removed the menu bar item'"'"'s app ($APP)." "$T/uninstall.log"' \
  "uninstall removes this install's app from /Applications, and says so"
check '[ "$(cksum < "$MINE/Contents/Info.plist")" = "$sum" ] && [ "$(grep -c "menu bar item.s app" "$T/uninstall.log")" = 1 ]' \
  "and leaves another install's ~/Applications/Switchboard.app as it was"
check '[ -z "$(pgrep -f "$T/")" ]' "nothing started here is still running"
rm -rf "$T"
fi

if want 17; then
echo "== 17. The link app from before 0.2.0 at ~/Applications/Switchboard.app, without and with Apple's command line tools"
# Offline as in case 11. Made-up apps; nothing is registered with macOS (--no-agent). Without the tools (a stand-in
# xcode-select that fails stands first on the PATH, so the page gets Apple's Python by its own path) the older link
# app is renamed Switchboard Links.app, so "Switchboard" is the menu bar's app. With them (stand-in swiftc and codesign
# that work) it is built again under that name, and the older one goes.
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
T="$(fresh_home)"; H="$T/home"
OLD="$H/Applications/Switchboard.app"; LINKS="$H/Applications/Switchboard Links.app"
PAGE_PY="$(/usr/bin/python3 -I -B -c 'import sys; print(sys.executable)')"
mkdir -p "$T/noclt" "$T/clt"
printf '#!/bin/sh\nexit 2\n' > "$T/noclt/xcode-select"
printf '#!/bin/sh\nprintf "#!/bin/sh\\nexit 0\\n" > "$3" && chmod +x "$3"\n' > "$T/clt/swiftc"
printf '#!/bin/sh\nexit 0\n' > "$T/clt/codesign"
chmod +x "$T/noclt/xcode-select" "$T/clt/swiftc" "$T/clt/codesign"
older_link_app() {
  fake_app "$OLD" "$LINKS_ID" Switchboard '<key>CFBundleName</key><string>Switchboard</string>'
  printf '#!/bin/sh\necho "$HOME/Applications/Other.app"\n' > "$OLD/Contents/MacOS/Switchboard"
  chmod +x "$OLD/Contents/MacOS/Switchboard"
}
older_link_app
sum="$(cksum < "$OLD/Contents/Info.plist")"
EXTRA_ENV="$OFFLINE SWITCHBOARD_PAGE_PYTHON=$PAGE_PY PATH=$T/noclt:$H/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-menu-app --own-python --port "$PORT" > "$T/noclt.log" 2>&1
sed 's/^/    /' "$T/noclt.log"
check 'grep -qxF "Renamed the link app to Switchboard Links.app (it was ~/Applications/Switchboard.app before)." "$T/noclt.log" && ! grep -q "Skipped the link app" "$T/noclt.log"' \
  "without the command line tools: one line says the older link app was renamed"
check '[ ! -e "$OLD" ] && [ "$(cksum < "$LINKS/Contents/Info.plist")" = "$sum" ] && [ -x "$LINKS/Contents/MacOS/Switchboard" ]' \
  "it is the same app under the new name: its Info.plist byte for byte, so its bundle identifier, and its place as the default browser, stay"
check 'run "$H" env PYTHONPATH="$H/.switchboard/app" /usr/bin/python3 -B -c "
from switchboard import linkapp
assert linkapp.installed() and linkapp.app_path().endswith(\"/Applications/Switchboard Links.app\"), linkapp.app_path()
"' "the page finds it under the new name and runs it as the link app"
check '[ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-menu-app --own-python" ]' \
  "app/choices names nothing for the link app (the default), so switchboard update builds it once the tools are there"
rm -rf "$LINKS"; older_link_app
EXTRA_ENV="$OFFLINE PATH=$T/clt:$H/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-menu-app --own-python --port "$PORT" > "$T/clt.log" 2>&1
sed 's/^/    /' "$T/clt.log"
check 'grep -q "^Building the link app… [0-9][0-9]* s (it was ~/Applications/Switchboard.app before)$" "$T/clt.log" && [ ! -e "$OLD" ] && [ -x "$LINKS/Contents/MacOS/SwitchboardLinks" ]' \
  "with them: it is built again as Switchboard Links.app, and the older one goes"
check '[ "$(plutil -extract CFBundleIdentifier raw -o - "$LINKS/Contents/Info.plist")" = "$LINKS_ID" ] && [ -z "$(ls -A "$T/tmp" | grep -i links)" ]' \
  "with the same bundle identifier, and nothing left in the temporary folder"
EXTRA_ENV=""
rm -rf "$T"
fi

if want 18; then
echo "== 18. The build in the version: installed from a checkout, from a git archive, and from a copy with neither"
# Offline as in case 11: the program goes in on the Python found here (--own-python waits for the internet), which is
# all `switchboard version` and the doctor's first line read. The archive is HEAD's, so it holds what is committed, not this folder's edits.
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
EXTRA_ENV="$OFFLINE"
V="$(sed -n "s/^VERSION = '\([^']*\)'.*/\1/p" "$HERE/switchboard/core.py" | head -n 1)"
# install_from FOLDER LABEL WANT: install from FOLDER in a fresh home; check the version line and the doctor's first line
install_from() {
  T="$(fresh_home)"; H="$T/home"; WANT="$3"          # (check's eval sees its own $1-$3, so the line is named)
  run "$H" sh "$1/install.sh" --no-agent --no-open --no-link-app --no-menu-app --own-python --port "$PORT" > "$T/install.log" 2>&1
  got="$(run "$H" "$H/.switchboard/bin/switchboard" version 2>&1)"
  doc="$(run "$H" "$H/.switchboard/bin/switchboard" doctor 2>&1 | head -n 1)"
  check '[ "$got" = "switchboard $WANT" ] && [ "$doc" = "Switchboard $WANT, in ~/.switchboard" ]' \
    "$2: switchboard version says \"switchboard $WANT\", and the doctor's first line agrees"
  [ "$got" = "switchboard $WANT" ] || printf '    got: %s / %s\n' "$got" "$doc"
  rm -rf "$T"
}
if [ -e "$HERE/.git" ] && SHORT="$(git -C "$HERE" rev-parse --short HEAD 2>/dev/null)" && [ -n "$SHORT" ]; then
  install_from "$HERE" "installed from a checkout" "$V ($SHORT)"
  A="$(fresh_home)"
  mkdir -p "$A/archive"
  git -C "$HERE" archive --format=tar HEAD | tar -x -C "$A/archive"
  check '[ "$(cat "$A/archive/switchboard/build.txt")" = "$SHORT" ]' "a git archive of HEAD has its commit in switchboard/build.txt ($SHORT)"
  install_from "$A/archive" "installed from that archive" "$V ($SHORT)"
  rm -rf "$A"
else
  echo " --  this copy is not a git checkout: the checkout and archive checks are left out"
fi
A="$(fresh_home)"
clone_tree "$A/copy"
printf '%s\n' '$Format:%h$' > "$A/copy/switchboard/build.txt"    # as a checkout holds it (a download holds its commit)
install_from "$A/copy" "installed from a copy with neither" "$V"
rm -rf "$A"
EXTRA_ENV=""
fi

if want 19; then
echo "== 19. Homebrew's cask: --homebrew, the launcher Homebrew links, and the uninstall step brew runs"
# Offline as in case 11, so it takes seconds. A stand-in launchctl stands first on the PATH and only writes down what
# it was asked: the run as the cask gives no --no-agent of its own, so this proves the saved one is read back without
# ever reaching launchd. The uninstall step's choices are seen through a stand-in uninstall.sh in a home of its own;
# the real uninstall.sh runs once, last, with --no-agent (from app/choices).
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
T="$(fresh_home)"; H="$T/home"
mkdir -p "$T/guard" "$T/brewbin"
printf '#!/bin/sh\necho "$*" >> "%s/launchctl.calls"\nexit 1\n' "$T" > "$T/guard/launchctl"
chmod +x "$T/guard/launchctl"
EXTRA_ENV="$OFFLINE PATH=$T/guard:$H/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" sh "$HERE/install.sh" --homebrew --no-agent --no-open --no-menu-app --no-link-app --own-python --port "$PORT" > "$T/brew.log" 2>&1
sed 's/^/    /' "$T/brew.log"
check '[ -x "$H/.switchboard/bin/switchboard" ] && [ ! -e "$H/.local/bin/switchboard" ] && ! grep -q "switchboard command\|PATH" "$T/brew.log"' \
  "--homebrew: the program is in ~/.switchboard, no command is added to ~/.local/bin, and no line asks about the PATH"
check '[ "$(cat "$H/.switchboard/app/source")" = "homebrew:$HERE" ]' \
  "app/source says Homebrew, and Homebrew's copy"
check '[ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-menu-app --no-link-app --own-python --homebrew" ]' \
  "app/choices keeps --homebrew"
check 'run "$H" env PYTHONPATH="$H/.switchboard/app" /usr/bin/python3 -B -c "
from switchboard.__main__ import install_choices
f = install_choices()
assert \"--homebrew\" in f and \"--no-menu-app\" in f and \"--no-agent\" in f and \"--no-link-app\" in f, f
"' "switchboard update gives --homebrew again, so an update adds no commands either"
run "$H" sh "$HERE/install.sh" --homebrew > "$T/brew-2.log" 2>&1
check '[ ! -e "$T/launchctl.calls" ] && [ ! -e "$H/.switchboard/bin/switchboard-menubar" ] && [ ! -e "$H/Applications" ] && [ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-menu-app --no-link-app --own-python --homebrew" ]' \
  "run as the cask runs it (--homebrew alone): the last install's --no-agent, --no-menu-app and --no-link-app are kept, and launchd is never asked"
ln -s "$HERE/packaging/homebrew/launcher" "$T/brewbin/switchboard"
ln -s "$HERE/packaging/homebrew/launcher" "$T/brewbin/launcher"
V="$(sed -n "s/^VERSION = '\([^']*\)'.*/\1/p" "$HERE/switchboard/core.py" | head -n 1)"
check 'run "$H" "$T/brewbin/switchboard" version 2>&1 | grep -qF "$V"' \
  "the launcher, linked as switchboard, runs this home's own command (switchboard version says $V)"
check 'E="$T/empty"; mkdir -p "$E"; run "$E" "$T/brewbin/switchboard" version > "$T/launch-none.log" 2>&1; [ $? = 1 ] && grep -q "brew reinstall switchboard" "$T/launch-none.log"' \
  "for a user it was never set up for, it says how to set it up, and fails"
check 'run "$H" "$T/brewbin/launcher" > /dev/null 2>&1; [ $? = 2 ]' "under any other name it refuses"
# switchboard update: Homebrew updates it. A stand-in brew writes down what it was asked and changes nothing.
printf '#!/bin/sh\necho "$*" >> "%s/brew.calls"\n' "$T" > "$T/guard/brew"; chmod +x "$T/guard/brew"
run "$H" "$H/.switchboard/bin/switchboard" update > "$T/update.log" 2>&1; rc=$?
check '[ $rc = 0 ] && [ "$(cat "$T/brew.calls")" = "upgrade --cask switchboard" ] && grep -qx "Updating with Homebrew: brew upgrade switchboard" "$T/update.log" && [ ! -e "$T/launchctl.calls" ]' \
  "switchboard update runs brew upgrade, and nothing else"
run "$H" "$H/.switchboard/bin/switchboard" update --port 7844 > "$T/update-port.log" 2>&1; rc=$?
check '[ $rc = 0 ] && [ "$(sed -n 2p "$T/brew.calls")" = "upgrade --cask switchboard" ] && grep -q "\"port\": 7844" "$H/.switchboard/settings.json" && [ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-menu-app --no-link-app --own-python --homebrew" ] && [ ! -e "$H/.local/bin/switchboard" ] && [ ! -e "$T/launchctl.calls" ]' \
  "switchboard update --port 7844: brew upgrade, then the installer in Homebrew's copy moves the page, and the install stays Homebrew's (--own-python stays until it is fetched, as in case 4)"
check '[ "$(last_line "$T/update-port.log")" = "Run brew reinstall switchboard when you are online to finish." ]' \
  "offline, its last line is Homebrew's way to finish, and it exits 0 (a failed installer script would fail brew install itself)"
rm -f "$T/guard/brew"     # (no check without brew: this Mac's own brew would be found where Homebrew puts it)

# The uninstall step: which brew command decides what it does.
S="$T/stand"; mkdir -p "$S/home/.switchboard/app"
printf '#!/bin/sh\necho "ran:$*" >> "%s/uninstall.calls"\n' "$S" > "$S/home/.switchboard/app/uninstall.sh"
printf '%s\n' "--homebrew" > "$S/home/.switchboard/app/choices"
step() { run "$S/home" env SWITCHBOARD_BREW_ARGS="$1" sh "$HERE/packaging/homebrew/uninstall.sh"; }
rm -f "$S/uninstall.calls"
for c in "upgrade --cask --greedy switchboard" "reinstall switchboard" "install switchboard"; do step "$c" > "$T/step.log" 2>&1; done
check '[ ! -e "$S/uninstall.calls" ] && grep -q "^Switchboard stays as it is for brew install" "$T/step.log"' \
  "before an upgrade, a reinstall or an install, it removes nothing"
step "uninstall --cask switchboard" > /dev/null 2>&1
step "rm switchboard" > /dev/null 2>&1
step "uninstall --zap switchboard" > /dev/null 2>&1
step "reinstall --zap switchboard" > /dev/null 2>&1
step "something-new switchboard" > /dev/null 2>&1
check '[ "$(cat "$S/uninstall.calls")" = "ran:
ran:
ran:--all
ran:--all
ran:" ]' "brew uninstall (or rm) runs uninstall.sh; with --zap (uninstall or reinstall) it runs it with --all; any other brew command removes"
check '[ "$(cat "$S/home/.switchboard/choices.last")" = "--homebrew" ]' "before a plain removal, the choices are kept beside the settings"
printf '%s\n' "--no-agent --homebrew" > "$S/home/.switchboard/app/choices"; rm -f "$S/uninstall.calls"
step "uninstall switchboard" > /dev/null 2>&1
run "$S/home" sh "$HERE/packaging/homebrew/uninstall.sh" > /dev/null 2>&1        # no brew above it
check '[ "$(cat "$S/uninstall.calls")" = "ran:--no-agent
ran:--no-agent" ]' "an install made with --no-agent is removed with --no-agent; with no brew command above it, it removes"
run "$H" env SWITCHBOARD_BREW_ARGS="uninstall switchboard" sh "$HERE/packaging/homebrew/uninstall.sh" > "$T/unbrew.log" 2>&1; urc=$?
sed 's/^/    /' "$T/unbrew.log"
check '[ $urc = 0 ] && [ ! -e "$H/.switchboard/app" ] && [ ! -e "$H/.switchboard/bin" ] && [ -d "$H/.switchboard" ] && [ ! -e "$T/launchctl.calls" ]' \
  "brew uninstall, for real: the installed uninstall.sh removes the program, keeps the settings, and launchd is never asked"
run "$H" sh "$HERE/install.sh" --homebrew > "$T/brew-3.log" 2>&1
check '[ ! -e "$T/launchctl.calls" ] && [ ! -e "$H/.switchboard/bin/switchboard-menubar" ] && [ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-menu-app --no-link-app --own-python --homebrew" ]' \
  "brew install after it: the kept choices are read back (--own-python and --no-link-app with them), so launchd is still never asked"
EXTRA_ENV=""
rm -rf "$T"
fi

if want 20; then
echo "== 20. uninstall --all takes the account engine's logins out of the Keychain, through the program; a plain uninstall keeps them"
# Offline as in case 11. The installed program is the real one; only the Keychain is a stand-in. The installed copy's
# keychain.py names a stand-in for /usr/bin/security (its items in a JSON file of this case's own), and nothing runs
# until that has taken. A second stand-in named security stands first on the PATH and only notes that it ran: nothing
# here may reach the real tool. The records are made up: two kept accounts, one removed earlier, a switch's safety
# copy in the Keychain and one in a file, a successor, earlier logins; beside them Claude Code's own item and another
# program's, which must stay.
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
T="$(fresh_home)"; H="$T/home"; KC="$T/kc"
mkdir -p "$T/guard" "$KC"
printf '#!/bin/sh\necho "$*" >> "%s/path-security.calls"\nexit 1\n' "$T" > "$T/guard/security"
chmod +x "$T/guard/security"
{ printf "#!/usr/bin/python3 -IB\nSTATE = '%s'\n" "$KC"; cat <<'EOF'
"""A stand-in for /usr/bin/security (tests/install-check.sh case 20). Its items are in STATE/store.json; each call is
noted in STATE/calls.log as its kind, service and account, never a value. -i reads one command per line."""
import json
import os
import shlex
import sys

STORE = os.path.join(STATE, 'store.json')


def load():
    try:
        with open(STORE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(items):
    with open(STORE + '.tmp', 'w') as f:
        json.dump(items, f, indent=1, sort_keys=True)
    os.replace(STORE + '.tmp', STORE)


def one(args):
    verb = args[0] if args else ''
    takes = ('-a', '-s', '-d', '-k', '-l', '-c', '-C', '-D', '-j', '-G', '-T')
    if verb == 'add-generic-password':
        takes += ('-X', '-w')
    opts, flags, i = {}, set(), 1
    while i < len(args):
        if args[i] in takes and i + 1 < len(args):
            opts[args[i]] = args[i + 1]
            i += 2
        else:
            flags.add(args[i])
            i += 1
    kind = {'find-generic-password': 'read' if '-w' in flags or '-g' in flags else 'look',
            'add-generic-password': 'add', 'delete-generic-password': 'delete', 'list-keychains': 'list'}.get(verb, verb)
    svc, acct = opts.get('-s', ''), opts.get('-a', '')
    with open(os.path.join(STATE, 'calls.log'), 'a') as f:
        f.write('%s|%s|%s\n' % (kind, svc, acct))
    items, key = load(), svc + '\t' + acct
    where = '%s/Library/Keychains/login.keychain-db' % os.environ.get('HOME', '/nowhere')
    if kind == 'list':
        print('    "%s"' % where)
        return 0
    if kind in ('read', 'look', 'delete'):
        if key not in items:
            sys.stderr.write('security: SecKeychainSearchCopyNext: The specified item could not be found in the '
                             'keychain.\n')
            return 44
        if kind == 'read':
            print(items[key])
        elif kind == 'look':
            print('keychain: "%s"\nattributes:\n    "mdat"<timedate>=0x3230  "20261117000000Z"' % where)
        else:
            del items[key]
            save(items)
        return 0
    if kind == 'add':
        if '-U' not in flags and key in items:
            return 45
        items[key] = bytes.fromhex(opts['-X']).decode('utf-8') if '-X' in opts else opts.get('-w', '')
        save(items)
        return 0
    return 1


if sys.argv[1:2] == ['-i']:
    code = 0
    for line in sys.stdin:
        if line.strip():
            code = one(shlex.split(line)) or code
    sys.exit(code)
sys.exit(one(sys.argv[1:]))
EOF
} > "$T/fake-security"
chmod +x "$T/fake-security"
LIVE_ACCOUNT="$(id -un)"
seed_keychain() {          # the stand-in's items: six of the engine's, Claude Code's own, and another program's
  /usr/bin/python3 -I -B -c 'import json, sys
items = {"Switchboard login\tacct-maya": "{\"claudeAiOauth\": {\"refreshToken\": \"made-up-1\"}}",
         "Switchboard login (previous)\tacct-maya": "{\"generations\": []}",
         "Switchboard login\tacct-sam": "{\"claudeAiOauth\": {\"refreshToken\": \"made-up-2\"}}",
         "Switchboard login (previous)\tacct-old": "{\"generations\": []}",
         "Switchboard recovery\tsuccessor:acct-old": "{\"claudeAiOauth\": {\"refreshToken\": \"made-up-3\"}}",
         "Switchboard recovery\tsafety:0123456789abcdef": "{\"claudeAiOauth\": {\"refreshToken\": \"made-up-4\"}}",
         "Claude Code-credentials\t" + sys.argv[2]: "{\"stand-in\": true}",
         "Some other app\tsomeone": "theirs"}
json.dump(items, open(sys.argv[1], "w"), indent=1, sort_keys=True)' "$KC/store.json" "$LIVE_ACCOUNT"
}
seed_records() {           # the engine's own records of them, as it writes them (accounts.json schema 1, recovery/)
  mkdir -p "$H/.switchboard/recovery"
  cat > "$H/.switchboard/accounts.json" <<'EOF'
{"schema": 1, "order": ["acct-maya", "acct-sam"],
 "accounts": {"acct-maya": {"id": "acct-maya", "uuid": "acct-maya", "email": "maya@northwind.example", "added_at": 1},
              "acct-sam": {"id": "acct-sam", "uuid": "acct-sam", "email": "sam@harbor.example", "added_at": 2}},
 "removed": {"acct-old": {"at": 1, "email": "old@harbor.example"}},
 "claims": {}, "refreshing": {}, "upkeep": {}, "switches": [], "settings": {}}
EOF
  cat > "$H/.switchboard/recovery/manifest.json" <<'EOF'
{"entries": [{"key": "successor:acct-old", "account_id": "acct-old", "reason": "removed while refreshing", "where": "keychain", "at": 1},
             {"key": "safety:0123456789abcdef", "account_id": null, "reason": "before switching", "where": "keychain", "at": 1},
             {"key": "safety:fedcba9876543210", "account_id": null, "reason": "before switching", "where": "file", "at": 1}]}
EOF
  printf '{"key": "safety:fedcba9876543210", "account_id": null, "at": 1, "text": "{}"}\n' \
    > "$H/.switchboard/recovery/safety_fedcba9876543210.json"
  chmod 600 "$H/.switchboard/recovery/safety_fedcba9876543210.json"
}
# stand_in: the installed copy's engine runs the stand-in, not /usr/bin/security; 0 only when that has taken
stand_in() {
  f="$H/.switchboard/app/switchboard/accounts/keychain.py"
  [ -f "$f" ] && sed -i '' "s|^TOOL = '/usr/bin/security'\$|TOOL = '$T/fake-security'|" "$f" \
    && grep -qxF "TOOL = '$T/fake-security'" "$f" && ! grep -q "^TOOL = '/usr/bin/security'" "$f"
}
items() { /usr/bin/python3 -I -B -c 'import json, sys; print("\n".join(sorted(json.load(open(sys.argv[1])))))' "$KC/store.json" | tr '\t' '|'; }
ours() { items | grep -c -e '^Switchboard login|' -e '^Switchboard login (previous)|' -e '^Switchboard recovery|'; }
EXTRA_ENV="$OFFLINE PATH=$T/guard:$H/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --port "$PORT" > "$T/install-1.log" 2>&1; rc=$?
if [ $rc = 0 ] && stand_in; then
  ok "installed (offline, on the Python found here), and the program's Keychain is the stand-in"
  seed_records; seed_keychain
  run "$H" sh "$H/.switchboard/app/uninstall.sh" --no-agent > "$T/uninstall-1.log" 2>&1; urc=$?
  sed 's/^/    /' "$T/uninstall-1.log"
  KEEP_LINES="Kept your settings and accounts in ~/.switchboard (a reinstall picks them up).
The login Claude Code is using was not touched.
To remove them too, with their logins in your Keychain, run:
sh ~/.switchboard/uninstall.sh --all"
  check '[ $urc = 0 ] && [ "$(tail -n 4 "$T/uninstall-1.log")" = "$KEEP_LINES" ] && [ -f "$H/.switchboard/accounts.json" ]' \
    "a plain uninstall keeps the accounts, and its last lines say how to remove them with their logins"
  check '[ "$(ours)" = 6 ] && [ "$(items | wc -l | tr -d " ")" = 8 ] && ! grep -q -e "^delete" -e "^add" "$KC/calls.log" 2>/dev/null' \
    "and the Keychain keeps every login: nothing was deleted or written ($(ours) of the engine's 6)"
  run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --port "$PORT" > "$T/install-2.log" 2>&1; rc=$?
  if [ $rc = 0 ] && stand_in; then
    : > "$KC/calls.log"
    run "$H" sh "$H/.switchboard/app/uninstall.sh" --all --no-agent > "$T/uninstall-2.log" 2>&1; urc=$?
    sed 's/^/    /' "$T/uninstall-2.log"
    check '[ $urc = 0 ] && grep -qx "Account maya@northwind.example: removed, with its saved login." "$T/uninstall-2.log" && grep -qx "Account sam@harbor.example: removed, with its saved login." "$T/uninstall-2.log" && grep -qx "Earlier logins and recovery copies: 4 removed." "$T/uninstall-2.log"' \
      "install again, then --all: switchboard tidy --all removes each account, then the earlier logins and recovery copies"
    check '[ "$(ours)" = 0 ] && [ "$(items | tr "\n" " ")" = "Claude Code-credentials|$LIVE_ACCOUNT Some other app|someone " ]' \
      "not one item of Switchboard login, Switchboard login (previous) or Switchboard recovery is left; Claude Code's and another program's stay ($(ours) left)"
    check '! grep -q -e "^read|Switchboard" -e "^add" "$KC/calls.log" && ! grep -v -e "^delete|Switchboard" -e "^look|" -e "^list|" -e "^read|Claude Code-credentials|" "$KC/calls.log" | grep -q . && ! grep -q "^delete|Claude Code" "$KC/calls.log"' \
      "it only looked and deleted: no login of Switchboard's was read, nothing was written, Claude Code's own item was only read"
    check 'grep -qx "Removed ~/.switchboard with your settings and accounts. Nothing of Switchboard is left." "$T/uninstall-2.log" && [ ! -e "$H/.switchboard" ]' \
      "~/.switchboard is gone too, so with the check above its last line is true: nothing of Switchboard is left"
  else
    no "the second install, or the stand-in Keychain in it, did not take: --all was not run ($(tail -n 1 "$T/install-2.log"))"
  fi
  check '[ ! -e "$T/path-security.calls" ]' "nothing ran security from the PATH"
else
  sed 's/^/    /' "$T/install-1.log"
  no "the install, or the stand-in Keychain in it, did not take: nothing was uninstalled (the real Keychain is never used here)"
fi
EXTRA_ENV=""
rm -rf "$T"
fi

if want 21; then
echo "== 21. Apple's /usr/bin/python3 as a stand-in (no command line tools): never picked, and never run by a launcher"
# Offline as in case 11. A stand-in xcode-select stands first on the PATH and names a developer folder that holds no
# python3, as a macOS update can leave it: Apple's /usr/bin/python3 is then only a stand-in, which shows a dialog when it
# runs. The real /usr/bin/python3 is never run while that stand-in is on the PATH: what each launcher would run is a
# stand-in program that says it ran.
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
T="$(fresh_home)"; H="$T/home"
mkdir -p "$T/stale/developer/usr/bin" "$T/guard"
printf '#!/bin/sh\necho "%s/stale/developer"\n' "$T" > "$T/stale/xcode-select"
printf '#!/bin/sh\necho "slept $*" >> "%s/sleep.calls"\n' "$T" > "$T/stale/sleep"
chmod +x "$T/stale/xcode-select" "$T/stale/sleep"
STALE_PATH="PATH=$T/stale:$H/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
EXTRA_ENV="$OFFLINE $STALE_PATH"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --port "$PORT" > "$T/install-1.log" 2>&1; rc=$?
sed 's/^/    /' "$T/install-1.log"
PY_NOW="$(/usr/bin/python3 -I -B -c 'import json, sys; print(json.load(open(sys.argv[1]))["python"])' "$H/.switchboard/runtime/python.json" 2>/dev/null)"
check '{ [ $rc = 0 ] && [ -n "$PY_NOW" ] && [ "$PY_NOW" != /usr/bin/python3 ]; } || { [ $rc = 1 ] && grep -q "^Switchboard: needs the internet once" "$T/install-1.log" && [ ! -e "$H/.switchboard" ]; }' \
  "the installer passes over the stand-in: another Python runs the program (${PY_NOW:-none: offline, it stops for its own Python})"
rm -rf "$H/.switchboard" "$H/.local/bin/switchboard"
# Launchers made on the real /usr/bin/python3 (this Mac's, while its tools are there), then run with the stand-in.
if [ -x /usr/bin/python3 ] && d="$(xcode-select -p 2>/dev/null)" && [ -x "$d/usr/bin/python3" ]; then
  EXTRA_ENV="$OFFLINE SWITCHBOARD_PAGE_PYTHON=/usr/bin/python3"
  run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --port "$PORT" > "$T/install-2.log" 2>&1; rc=$?
  SBIN="$H/.switchboard/bin"
  check '[ $rc = 0 ] && grep -qxF "PY=\"/usr/bin/python3\"" "$SBIN/switchboard" && grep -qxF "PY=\"/usr/bin/python3\"" "$SBIN/switchboard-page" && [ "$(grep -c "xcode-select -p" "$SBIN/switchboard")" = 1 ] && [ "$(grep -c "xcode-select -p" "$SBIN/switchboard-page")" = 1 ]' \
    "made on Apple's /usr/bin/python3, the command's launcher and the page's both check it at each start"
  # The program the launchers start is a stand-in from here on, so a launcher that ran anything would only print a
  # line (the page itself would start a server and never stop).
  cat > "$H/.switchboard/app/switchboard/__main__.py" <<'EOF'
"""A stand-in (tests/install-check.sh case 21): it says how it was started, and on which Python."""
import os
import sys
print('(stand-in) switchboard %s, on %s' % (' '.join(sys.argv[1:]), os.path.realpath(sys.executable)))
EOF
  EXTRA_ENV="$OFFLINE $STALE_PATH"
  out="$(run "$H" "$SBIN/switchboard" version 2>&1)"; urc=$?
  check '[ $urc = 1 ] && [ "$out" = "Switchboard: Apple'"'"'s /usr/bin/python3 needs its command line tools again. Install them with xcode-select --install, or install Switchboard again so it uses its own Python." ]' \
    "with only the stand-in left, the command runs nothing and says what to do in one line (${out:-nothing})"
  out="$(run "$H" "$SBIN/switchboard-page" 2>&1)"; prc=$?
  check '[ $prc = 1 ] && [ "$out" = "Switchboard: Apple'"'"'s /usr/bin/python3 needs its command line tools again. Install them with xcode-select --install, or install Switchboard again so it uses its own Python." ] && [ "$(cat "$T/sleep.calls" 2>/dev/null)" = "slept 300" ]' \
    "the page's launcher says the same in its log and waits 5 minutes before it stops, so its job's restarts don't fill the log"
  mkdir -p "$H/.switchboard/runtime/python/bin"
  printf '#!/bin/sh\necho "own-python $1 $2 $5 $6"\n' > "$H/.switchboard/runtime/python/bin/python3"
  chmod +x "$H/.switchboard/runtime/python/bin/python3"
  out1="$(run "$H" "$SBIN/switchboard" version 2>&1)"; out2="$(run "$H" "$SBIN/switchboard-page" 2>&1)"
  check '[ "$out1" = "own-python -I -B $H/.switchboard/app version" ] && [ "$out2" = "own-python -I -B $H/.switchboard/app serve" ]' \
    "with Switchboard's own Python there, both run the program on it instead (${out1:-nothing} / ${out2:-nothing})"
  EXTRA_ENV="$OFFLINE"
  out="$(run "$H" "$SBIN/switchboard" version 2>&1)"
  check 'case "$out" in "(stand-in) switchboard version, on /"*) true ;; *) false ;; esac' \
    "with the tools really there, the command runs on Apple's /usr/bin/python3 as before (${out:-nothing})"
else
  ok "(this Mac's /usr/bin/python3 is itself a stand-in, so no launcher can be made on it: skipped)"
fi
EXTRA_ENV=""
rm -rf "$T"
fi

if want 22; then
echo "== 22. The short name, sb: added beside switchboard when nothing else answers to it, and never in the way"
# Offline as in case 11, on the Python found here, so each run takes seconds. Every other `sb` here is a made-up one
# in the throwaway home or the test's own folders.
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
FLAGS="--no-agent --no-open --no-link-app --no-menu-app --port $PORT"
TAKEN_LINE="sb already means something else on this Mac"
EXTRA_ENV="$OFFLINE"
T="$(fresh_home)"; H="$T/home"
run "$H" sh "$HERE/install.sh" $FLAGS > "$T/install.log" 2>&1; rc=$?
sed 's/^/    /' "$T/install.log"
check '[ $rc = 0 ] && [ -L "$H/.local/bin/sb" ] && [ "$(readlink "$H/.local/bin/sb")" = "$H/.switchboard/bin/switchboard" ]' \
  "a plain install links sb beside switchboard, to the same program"
v1="$(run "$H" "$H/.local/bin/switchboard" version 2>&1)"; v2="$(run "$H" sb version 2>&1)"
run "$H" sb no-such-command > /dev/null 2>&1; s=$?; run "$H" switchboard no-such-command > /dev/null 2>&1; f=$?
check '[ -n "$v1" ] && [ "$v1" = "$v2" ] && [ $s = 2 ] && [ $f = 2 ]' "sb runs: sb version says what switchboard version says ($v2), and the exit codes are the same"
check 'last_line "$T/install.log" | grep -q "^Done in [0-9][0-9]* s\. Type sb setup to add your accounts\.$" && ! grep -q "$TAKEN_LINE" "$T/install.log"' \
  "the last line says to type sb setup"
accounts_file "$H" maya@northwind.example
run "$H" sh "$HERE/install.sh" $FLAGS > "$T/again.log" 2>&1; rc=$?
check '[ $rc = 0 ] && last_line "$T/again.log" | grep -q "^Done in [0-9][0-9]* s\. Type sb to see your accounts\.$" && [ "$(readlink "$H/.local/bin/sb")" = "$H/.switchboard/bin/switchboard" ]' \
  "with accounts, it says to type sb; a second run keeps the link"
run "$H" "$H/.switchboard/bin/switchboard" doctor > "$T/doctor.log" 2>&1
check 'grep -q "^ ok  The short command sb (~/.local/bin)$" "$T/doctor.log"' "doctor knows sb is in place, and ours"
# An install from before sb: switchboard update adds it.
rm -f "$H/.local/bin/sb"
run "$H" "$H/.switchboard/bin/switchboard" update > "$T/update.log" 2>&1; rc=$?
check '[ $rc = 0 ] && [ "$(readlink "$H/.local/bin/sb")" = "$H/.switchboard/bin/switchboard" ]' \
  "switchboard update over an install without sb adds it"
run "$H" sh "$H/.switchboard/app/uninstall.sh" --no-agent > "$T/uninstall.log" 2>&1; urc=$?
check '[ $urc = 0 ] && [ ! -e "$H/.local/bin/sb" ] && [ ! -L "$H/.local/bin/sb" ] && grep -q "^Removed the sb command from ~/.local/bin\.$" "$T/uninstall.log"' \
  "uninstall takes our sb out, and says so"
rm -rf "$T"

# Another sb on the PATH, after ~/.local/bin: nothing is added, one line says why, and the words stay switchboard.
T="$(fresh_home)"; H="$T/home"
mkdir -p "$T/other"; printf '#!/bin/sh\necho other\n' > "$T/other/sb"; chmod +x "$T/other/sb"
EXTRA_ENV="$OFFLINE PATH=$H/.local/bin:$T/other:/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" sh "$HERE/install.sh" $FLAGS > "$T/install.log" 2>&1; rc=$?
sed 's/^/    /' "$T/install.log"
check '[ $rc = 0 ] && [ ! -e "$H/.local/bin/sb" ] && [ ! -L "$H/.local/bin/sb" ] && [ -L "$H/.local/bin/switchboard" ]' \
  "another sb on the PATH: no sb is added, switchboard is, and the install exits 0"
check '[ "$(grep -c "$TAKEN_LINE" "$T/install.log")" = 1 ] && grep -qx "sb already means something else on this Mac ($T/other/sb), so the command is switchboard\." "$T/install.log"' \
  "one plain line says sb is another program, so the command is switchboard"
check 'last_line "$T/install.log" | grep -q "^Done in [0-9][0-9]* s\. Type switchboard setup to add your accounts\.$" && [ "$(run "$H" sb)" = other ]' \
  "the last line says switchboard setup, and sb is still the other program"
run "$H" "$H/.switchboard/bin/switchboard" doctor > "$T/doctor.log" 2>&1; drc=$?
check 'grep -q "^ ok  sb already means something else on this Mac (/.*/other/sb), so the command is switchboard$" "$T/doctor.log" && doctor_ok "$T/doctor.log" $drc' \
  "doctor says sb is another program's, and that is no problem (all good)"
run "$H" "$H/.switchboard/bin/switchboard" update > "$T/update.log" 2>&1; rc=$?
check '[ $rc = 0 ] && [ ! -e "$H/.local/bin/sb" ] && [ ! -L "$H/.local/bin/sb" ] && [ "$(grep -c "$TAKEN_LINE" "$T/update.log")" = 1 ]' \
  "switchboard update there adds nothing either, says the same one line, and exits 0"
rm -rf "$T"

# A file that is not ours where the link would go (a person's own script, a link to something else), or an alias or a
# function named sb in a shell's startup file: each stays exactly as it was.
for what in "a file" "a link" "an alias" "a function" "a fish function"; do
  T="$(fresh_home)"; H="$T/home"
  case "$what" in
    "a file") printf '#!/bin/sh\necho mine\n' > "$H/.local/bin/sb"; chmod +x "$H/.local/bin/sb"; where="~/.local/bin/sb" ;;
    "a link") ln -s "$T/elsewhere/sb" "$H/.local/bin/sb"; where="~/.local/bin/sb" ;;   # a link to nothing, even
    "an alias") printf '%s\n' "alias ls='ls -G'" "alias sb='echo mine'" > "$H/.zshrc"; where="~/.zshrc" ;;
    "a function") printf '%s\n' "sb() {" "  echo mine" "}" > "$H/.bash_profile"; where="~/.bash_profile" ;;
    "a fish function") mkdir -p "$H/.config/fish/functions"; printf 'function sb\nend\n' > "$H/.config/fish/functions/sb.fish"; where="~/.config/fish/functions/sb.fish" ;;
  esac
  before="$(ls -l "$H/.local/bin/sb" 2>/dev/null; cat "$H/.local/bin/sb" 2>/dev/null)"
  EXTRA_ENV="$OFFLINE"
  run "$H" sh "$HERE/install.sh" $FLAGS > "$T/install.log" 2>&1; rc=$?
  check '[ $rc = 0 ] && [ "$(ls -l "$H/.local/bin/sb" 2>/dev/null; cat "$H/.local/bin/sb" 2>/dev/null)" = "$before" ] && grep -qx "sb already means something else on this Mac ($where), so the command is switchboard\." "$T/install.log" && last_line "$T/install.log" | grep -q "Type switchboard setup"' \
    "$what named sb ($where): left as it was, one line, exit 0, and the last line says switchboard"
  if [ "$what" = "a file" ]; then
    run "$H" sh "$H/.switchboard/app/uninstall.sh" --no-agent > "$T/uninstall.log" 2>&1; urc=$?
    check '[ $urc = 0 ] && [ "$(cat "$H/.local/bin/sb")" = "$(printf "#!/bin/sh\necho mine")" ] && ! grep -q "sb command" "$T/uninstall.log" && [ ! -e "$H/.local/bin/switchboard" ]' \
      "uninstall leaves another sb where it is, and takes switchboard out"
  fi
  rm -rf "$T"
done

# Homebrew: no `binary` line for sb (a taken name would stop the whole brew install). The installer, run by the cask,
# links sb beside Homebrew's own switchboard link, in the prefix its copy sits under (<prefix>/Caskroom/...), when the
# name is free; the uninstall step takes it out on a removal only.
T="$(fresh_home)"; H="$T/home"; P="$T/brew"
C="$P/Caskroom/switchboard/0.2.0/switchboard-0.2.0"
mkdir -p "$P/bin" "$C" "$T/guard"
clone_tree "$C"; cp -R "$HERE/packaging" "$C/"
printf '#!/bin/sh\necho "$*" >> "%s/launchctl.calls"\nexit 1\n' "$T" > "$T/guard/launchctl"; chmod +x "$T/guard/launchctl"
ln -s "$C/packaging/homebrew/launcher" "$P/bin/switchboard"         # what the cask's binary line makes
EXTRA_ENV="$OFFLINE PATH=$P/bin:$T/guard:/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" sh "$C/install.sh" --homebrew --no-agent --no-open --no-menu-app --no-link-app > "$T/brew.log" 2>&1; rc=$?
sed 's/^/    /' "$T/brew.log"
check '[ $rc = 0 ] && [ -L "$P/bin/sb" ] && [ "$(readlink "$P/bin/sb")" = switchboard ] && [ ! -e "$H/.local/bin/sb" ]' \
  "--homebrew: sb is a link to switchboard in Homebrew's bin folder, beside it, and nothing goes in ~/.local/bin"
v1="$(run "$H" switchboard version 2>&1)"; v2="$(run "$H" sb version 2>&1)"
check '[ -n "$v2" ] && [ "$v1" = "$v2" ] && last_line "$T/brew.log" | grep -q "Type sb setup to add your accounts\.$"' \
  "sb runs through Homebrew's launcher ($v2), and the last line says sb"
S="$T/stand"; mkdir -p "$S"
run "$H" env SWITCHBOARD_BREW_ARGS="upgrade switchboard" sh "$C/packaging/homebrew/uninstall.sh" > /dev/null 2>&1; urc=$?
check '[ $urc = 0 ] && [ -L "$P/bin/sb" ]' "before an upgrade the uninstall step leaves sb"
run "$H" env SWITCHBOARD_BREW_ARGS="uninstall switchboard" sh "$C/packaging/homebrew/uninstall.sh" > "$T/unbrew.log" 2>&1; urc=$?
sed 's/^/    /' "$T/unbrew.log"
check '[ $urc = 0 ] && [ ! -e "$P/bin/sb" ] && [ ! -L "$P/bin/sb" ] && [ ! -e "$H/.switchboard/app" ]' \
  "brew uninstall: the uninstall step takes sb out with the program"
# A foreign sb in Homebrew's bin folder (another formula's), then a bin folder it may not write to: exit 0 each time.
printf '#!/bin/sh\necho theirs\n' > "$P/bin/sb"; chmod +x "$P/bin/sb"
run "$H" sh "$C/install.sh" --homebrew > "$T/brew-2.log" 2>&1; rc=$?
check '[ $rc = 0 ] && [ "$(cat "$P/bin/sb")" = "$(printf "#!/bin/sh\necho theirs")" ] && grep -qx "sb already means something else on this Mac ($P/bin/sb), so the command is switchboard\." "$T/brew-2.log" && last_line "$T/brew-2.log" | grep -q "Type switchboard setup"' \
  "another sb in Homebrew's bin folder: left alone, one line, exit 0"
run "$H" env SWITCHBOARD_BREW_ARGS="uninstall switchboard" sh "$C/packaging/homebrew/uninstall.sh" > /dev/null 2>&1; urc=$?
check '[ $urc = 0 ] && [ "$(cat "$P/bin/sb")" = "$(printf "#!/bin/sh\necho theirs")" ]' "brew uninstall leaves that sb where it is"
rm -f "$P/bin/sb"; chmod 555 "$P/bin"
run "$H" sh "$C/install.sh" --homebrew > "$T/brew-3.log" 2>&1; rc=$?
chmod 755 "$P/bin"
check '[ $rc = 0 ] && [ ! -e "$P/bin/sb" ] && last_line "$T/brew-3.log" | grep -q "Type switchboard setup"' \
  "a Homebrew bin folder it can't write to: no sb, exit 0, and the words say switchboard"
# Homebrew's prefix from HOMEBREW_PREFIX, for a copy that does not sit under a Caskroom (and brew upgrade's rerun).
rm -rf "$H/.switchboard"
EXTRA_ENV="$OFFLINE PATH=$P/bin:$T/guard:/usr/bin:/bin:/usr/sbin:/sbin HOMEBREW_PREFIX=$P"
run "$H" sh "$HERE/install.sh" --homebrew --no-agent --no-open --no-menu-app --no-link-app > "$T/brew-4.log" 2>&1; rc=$?
check '[ $rc = 0 ] && [ "$(readlink "$P/bin/sb")" = switchboard ]' "from HOMEBREW_PREFIX too, when the copy is not under a Caskroom"
EXTRA_ENV=""
rm -rf "$T"
fi

if want 23; then
echo "== 23. A download cut off part-way (curl | sh): the part that arrived runs nothing"
# Offline as in case 11. The installer arrives on sh's input as from curl, and the program comes from a local archive
# (as in case 8), so the whole file installs; cut at a line inside its body (half-way, and inside the background job's
# file), the part that arrived must change nothing in the home folder. Each run takes seconds.
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
T="$(fresh_home)"; H="$T/home"
PAGE_PY="$(/usr/bin/python3 -I -B -c 'import sys; print(sys.executable)')"
clone_tree "$T/clone"
(cd "$T" && tar -czf main.tar.gz clone)
LINES="$(wc -l < "$HERE/install.sh" | tr -d ' ')"
for cut in $((LINES / 2)) "$(grep -n '<key>Label</key><string>\$LABEL</string>' "$HERE/install.sh" | head -n 1 | cut -d: -f1)"; do
  head -n "$cut" "$HERE/install.sh" > "$T/part.sh"
  EXTRA_ENV="$OFFLINE SWITCHBOARD_PAGE_PYTHON=$PAGE_PY SWITCHBOARD_REPO=example/switchboard SWITCHBOARD_ARCHIVE=file://$T/main.tar.gz"
  run "$H" sh -s -- --no-agent --no-open --no-link-app --no-menu-app --port "$PORT" < "$T/part.sh" > "$T/part.log" 2>&1; rc=$?
  EXTRA_ENV=""
  sed 's/^/    /' "$T/part.log"
  check '[ $rc != 0 ] && [ "$(ls -A "$H")" = ".local" ] && [ -z "$(ls -A "$H/.local/bin")" ] && [ -z "$(ls -A "$T/tmp")" ]' \
    "cut after line $cut of $LINES: it exits $rc and nothing is made (no ~/.switchboard, no command, no download)"
done
EXTRA_ENV="$OFFLINE SWITCHBOARD_PAGE_PYTHON=$PAGE_PY SWITCHBOARD_REPO=example/switchboard SWITCHBOARD_ARCHIVE=file://$T/main.tar.gz"
run "$H" sh -s -- --no-agent --no-open --no-link-app --no-menu-app --port "$PORT" < "$HERE/install.sh" > "$T/whole.log" 2>&1; rc=$?
EXTRA_ENV=""
check '[ $rc = 0 ] && [ -x "$H/.switchboard/bin/switchboard" ] && [ -L "$H/.local/bin/switchboard" ] && last_line "$T/whole.log" | grep -q "^Done in [0-9][0-9]* s\. Type sb setup to add your accounts\.$"' \
  "the whole file, the same way: it installs, and its last line is the next step"
rm -rf "$T"
fi

if want 24; then
echo "== 24. Run as root (sudo keeps HOME): the installer and the uninstaller say so in one line and change nothing"
# Offline as in case 11. Nothing runs as root: a stand-in id stands first on the PATH and answers 0 for `id -u`.
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
T="$(fresh_home)"; H="$T/home"
mkdir -p "$T/guard"
printf '#!/bin/sh\ncase "$1" in -u) echo 0 ;; -un) echo root ;; *) exec /usr/bin/id "$@" ;; esac\n' > "$T/guard/id"
chmod +x "$T/guard/id"
ROOT_LINE="Run this as yourself, without sudo: Switchboard lives in your own home folder."
EXTRA_ENV="$OFFLINE PATH=$T/guard:$H/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-link-app --no-menu-app --port "$PORT" > "$T/install.log" 2>&1; rc=$?
sed 's/^/    /' "$T/install.log"
check '[ $rc = 1 ] && [ "$(cat "$T/install.log")" = "$ROOT_LINE" ] && [ "$(ls -A "$H")" = ".local" ] && [ -z "$(ls -A "$H/.local/bin")" ]' \
  "install as root: exit 1, that one sentence, and nothing is made in the home folder"
# An install to remove: settings, an account, and a stand-in program that notes whether it was run.
mkdir -p "$H/.switchboard/bin"
printf '{"port": %s}\n' "$PORT" > "$H/.switchboard/settings.json"
accounts_file "$H" maya@northwind.example
printf '#!/bin/sh\necho "$*" >> "%s/program.calls"\n' "$T" > "$H/.switchboard/bin/switchboard"
chmod +x "$H/.switchboard/bin/switchboard"
ln -sfn "$H/.switchboard/bin/switchboard" "$H/.local/bin/switchboard"
before="$(cd "$H" && find . | sort)"
for all in "" --all; do
  run "$H" sh "$HERE/uninstall.sh" $all --no-agent > "$T/uninstall.log" 2>&1; urc=$?
  sed 's/^/    /' "$T/uninstall.log"
  check '[ $urc = 1 ] && [ "$(cat "$T/uninstall.log")" = "$ROOT_LINE" ] && [ "$(cd "$H" && find . | sort)" = "$before" ] && [ ! -e "$T/program.calls" ]' \
    "uninstall${all:+ $all} as root: exit 1, that one sentence; nothing is removed and the program is not run"
done
EXTRA_ENV=""
rm -rf "$T"
fi

if want 25; then
echo "== 25. A program that can't start (its Python or its folder gone): the uninstaller says what was not put back, and finishes"
# Offline, and nothing is installed: a made-up ~/.switchboard whose launcher exits 127, as one does when the Python it
# names is gone; then a launcher in the installer's form whose program folder is gone (run, it would only note that it
# ran). Plain uninstalls only (with --all the Keychain is asked: case 26, with a stand-in).
T="$(fresh_home)"; H="$T/home"
NOSTART_LINE="Switchboard could not start, so what it changed in Claude Code's settings was not put back: see \"The uninstaller says something could not be put back\" in docs/troubleshooting.md."
broken_program() {         # broken_program 127 | folder
  mkdir -p "$H/.switchboard/bin"
  printf '{"port": %s}\n' "$PORT" > "$H/.switchboard/settings.json"
  if [ "$1" = 127 ]; then
    printf '#!/bin/sh\nexit 127\n' > "$H/.switchboard/bin/switchboard"
  else
    printf '#!/bin/sh\necho "$*" >> "%s/ran"\nexit 1\nexec "/nowhere/python3" -I -B -c run "%s/.switchboard/app" "$@"\n' "$T" "$H" \
      > "$H/.switchboard/bin/switchboard"
  fi
  chmod +x "$H/.switchboard/bin/switchboard"
}
broken_program 127
run "$H" sh "$HERE/uninstall.sh" --no-agent > "$T/uninstall.log" 2>&1; urc=$?
sed 's/^/    /' "$T/uninstall.log"
check '[ $urc = 0 ] && grep -qxF "$NOSTART_LINE" "$T/uninstall.log" && ! grep -q "named above" "$T/uninstall.log" && [ ! -e "$H/.switchboard/bin" ] && [ -f "$H/.switchboard/settings.json" ]' \
  "its launcher exits 127: one line says Claude Code's settings were not put back and where the docs say how; the program goes, the settings stay, exit 0"
rm -rf "$H/.switchboard"; broken_program folder
run "$H" sh "$HERE/uninstall.sh" --no-agent > "$T/uninstall.log" 2>&1; urc=$?
check '[ $urc = 0 ] && grep -qxF "$NOSTART_LINE" "$T/uninstall.log" && [ ! -e "$T/ran" ] && [ ! -e "$H/.switchboard/bin" ]' \
  "its program folder is gone: the launcher is not run, the same line, and the uninstall finishes"
rm -rf "$H/.switchboard"; broken_program 127
# The cask's step gives no --no-agent of its own and this home has no saved choices, so a stand-in launchctl stands
# first on the PATH, as in case 19: launchd is never reached, and what the stand-in was asked is checked.
mkdir -p "$T/guard"
printf '#!/bin/sh\necho "$*" >> "%s/launchctl.calls"\nexit 1\n' "$T" > "$T/guard/launchctl"; chmod +x "$T/guard/launchctl"
EXTRA_ENV="PATH=$T/guard:$H/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" env SWITCHBOARD_BREW_ARGS="uninstall switchboard" sh "$HERE/packaging/homebrew/uninstall.sh" > "$T/brew.log" 2>&1; urc=$?
EXTRA_ENV=""
sed 's/^/    /' "$T/brew.log"
check '[ $urc = 0 ] && grep -qxF "$NOSTART_LINE" "$T/brew.log" && [ ! -e "$H/.switchboard/bin" ] && [ ! -e "$T/launchctl.calls" ]' \
  "brew uninstall the same way: Homebrew's uninstall step exits 0, so the cask can be removed, and launchd is not asked"
rm -rf "$T"
fi

if want 26; then
echo "== 26. What a plain uninstall keeps, the copy of the uninstaller it leaves, and --all from that copy with no program"
# Offline, and nothing is installed. The uninstaller run is a copy of this tree's with its Keychain tool, SECURITY,
# naming a stand-in (its items in a JSON file of this case's own; each call is written down), and nothing runs until
# that has taken: the real security tool is never run here. The program is a stand-in too.
T="$(fresh_home)"; H="$T/home"; KC="$T/kc"
mkdir -p "$KC" "$T/u"
{ printf "#!/usr/bin/python3 -IB\nSTATE = '%s'\n" "$KC"; cat <<'PYEOF'
"""A stand-in for /usr/bin/security (tests/install-check.sh case 26): delete-generic-password -s SERVICE takes out one
item of that service, or exits 44 when there is none (36 for every call while STATE/locked is there)."""
import json
import os
import sys

args = sys.argv[1:]
with open(os.path.join(STATE, 'calls.log'), 'a') as f:
    f.write(' '.join(args) + '\n')
if os.path.exists(os.path.join(STATE, 'locked')):
    sys.exit(36)
with open(os.path.join(STATE, 'store.json')) as f:
    items = json.load(f)
if len(args) != 3 or args[:2] != ['delete-generic-password', '-s']:
    sys.exit(1)
for i, (service, account) in enumerate(items):
    if service == args[2]:
        del items[i]
        with open(os.path.join(STATE, 'store.json'), 'w') as f:
            json.dump(items, f)
        sys.exit(0)
sys.exit(44)
PYEOF
} > "$T/fake-security"
chmod +x "$T/fake-security"
sed "s|^SECURITY=/usr/bin/security |SECURITY=$T/fake-security |" "$HERE/uninstall.sh" > "$T/u/uninstall.sh"
seed_keychain() {
  printf '%s\n' '[["Switchboard login", "acct-maya"], ["Switchboard login", "acct-lost"],' \
    '["Switchboard login (previous)", "acct-maya"], ["Switchboard recovery", "safety:0123456789abcdef"],' \
    '["Switchboard recovery", "successor:acct-lost"], ["switchboard-gateway", "work"],' \
    '["Claude Code-credentials", "me"], ["Some other app", "someone"]]' > "$KC/store.json"
  rm -f "$KC/calls.log" "$KC/locked"
}
left() { /usr/bin/python3 -I -B -c 'import json, sys; print(" + ".join(s for s, a in json.load(open(sys.argv[1]))))' "$KC/store.json"; }
only_ours() {              # every Keychain call was a delete of one of Switchboard's four services, by service alone
  ! grep -v -x -e "delete-generic-password -s Switchboard login" -e "delete-generic-password -s Switchboard login (previous)" \
    -e "delete-generic-password -s Switchboard recovery" -e "delete-generic-password -s switchboard-gateway" "$KC/calls.log"
}
installed() {              # installed [launcher text]: settings, an account, a gateway key, the program, its folder
  mkdir -p "$H/.switchboard/bin" "$H/.switchboard/app/switchboard"
  printf '{"port": %s}\n' "$PORT" > "$H/.switchboard/settings.json"
  accounts_file "$H" maya@northwind.example
  printf '{"lanes": [{"name": "work", "kind": "anthropic", "last4": "abcd"}]}\n' > "$H/.switchboard/gateway-lanes.json"
  if [ -n "${1:-}" ]; then printf '%s\n' "$1" > "$H/.switchboard/bin/switchboard"
  else printf '#!/bin/sh\necho "$*" >> "%s/program.calls"\n' "$T" > "$H/.switchboard/bin/switchboard"; fi
  chmod +x "$H/.switchboard/bin/switchboard"
}
if grep -q "^SECURITY=$T/fake-security " "$T/u/uninstall.sh" && ! grep -q "^SECURITY=/usr/bin/security" "$T/u/uninstall.sh"; then
  ok "the uninstaller copy run here names the stand-in Keychain tool"
  installed; seed_keychain
  run "$H" sh "$T/u/uninstall.sh" --no-agent > "$T/plain.log" 2>&1; urc=$?
  sed 's/^/    /' "$T/plain.log"
  check '[ $urc = 0 ] && [ "$(tail -n 4 "$T/plain.log")" = "Kept your settings, accounts and keys in ~/.switchboard (a reinstall picks them up).
The login Claude Code is using was not touched.
To remove them too, with their logins and the keys in your Keychain, run:
sh ~/.switchboard/uninstall.sh --all" ]' "a plain uninstall: its last line is the command that removes the rest"
  check 'cmp -s "$T/u/uninstall.sh" "$H/.switchboard/uninstall.sh" && [ ! -e "$H/.switchboard/app" ] && [ ! -e "$H/.switchboard/bin" ] && [ ! -e "$KC/calls.log" ]' \
    "it leaves a copy of itself at ~/.switchboard/uninstall.sh (the program folder, the other copy, is gone), and asks the Keychain nothing"
  run "$H" sh "$H/.switchboard/uninstall.sh" --all --no-agent > "$T/all.log" 2>&1; urc=$?
  sed 's/^/    /' "$T/all.log"
  check '[ $urc = 0 ] && [ ! -e "$H/.switchboard" ] && [ "$(tail -n 2 "$T/all.log")" = "Removed ~/.switchboard with your settings, accounts and keys. Nothing of Switchboard is left.
The login Claude Code is using was not touched." ]' "that command, with no program folder: it works, and ~/.switchboard is gone"
  check '[ "$(left)" = "Claude Code-credentials + Some other app" ] && only_ours && [ "$(grep -c . "$KC/calls.log")" = 10 ]' \
    "every item of Switchboard's four services left the Keychain, one call each, then one not-found per service; Claude Code's and another program's stay"
  # Nothing to keep: the copy is not left either.
  mkdir -p "$H/.switchboard/app" "$H/.switchboard/bin"
  run "$H" sh "$T/u/uninstall.sh" --no-agent > "$T/empty.log" 2>&1; urc=$?
  check '[ $urc = 0 ] && [ ! -e "$H/.switchboard" ] && [ "$(tail -n 2 "$T/empty.log")" = "Nothing of Switchboard is left.
The login Claude Code is using was not touched." ]' "with nothing to keep, no copy is left: nothing of Switchboard is"
  # Settings only.
  mkdir -p "$H/.switchboard/bin"; printf '{"port": %s}\n' "$PORT" > "$H/.switchboard/settings.json"
  run "$H" sh "$T/u/uninstall.sh" --no-agent > "$T/settings.log" 2>&1; urc=$?
  check '[ $urc = 0 ] && [ "$(tail -n 4 "$T/settings.log")" = "Kept your settings in ~/.switchboard (a reinstall picks them up).
The login Claude Code is using was not touched.
To remove them too, run:
sh ~/.switchboard/uninstall.sh --all" ] && [ -f "$H/.switchboard/uninstall.sh" ]' "settings only: the same last line"
  rm -rf "$H/.switchboard"
  # --all with a program that can't start: the Keychain is emptied of Switchboard's items in plain shell.
  installed '#!/bin/sh
exit 127'
  seed_keychain
  run "$H" sh "$T/u/uninstall.sh" --all --no-agent > "$T/all-127.log" 2>&1; urc=$?
  sed 's/^/    /' "$T/all-127.log"
  check '[ $urc = 0 ] && [ ! -e "$H/.switchboard" ] && [ "$(left)" = "Claude Code-credentials + Some other app" ] && only_ours && grep -q "^Switchboard could not start" "$T/all-127.log"' \
    "--all whose program can't start: the same line about Claude Code's settings, every item of Switchboard's leaves the Keychain, ~/.switchboard goes"
  # --all with a program that runs tidy: the program takes its logins out itself; the shell asks the Keychain nothing.
  installed; seed_keychain; rm -f "$T/program.calls"
  run "$H" sh "$T/u/uninstall.sh" --all --no-agent > "$T/all-tidy.log" 2>&1; urc=$?
  check '[ $urc = 0 ] && [ "$(cat "$T/program.calls")" = "tidy --all" ] && [ ! -e "$KC/calls.log" ] && [ ! -e "$H/.switchboard" ]' \
    "--all with a program that runs: tidy --all does it, and the shell asks the Keychain nothing"
  # A Keychain that refuses (locked): nothing more is removed, and it says so.
  installed '#!/bin/sh
exit 127'
  seed_keychain; : > "$KC/locked"
  run "$H" sh "$T/u/uninstall.sh" --all --no-agent > "$T/locked.log" 2>&1; urc=$?
  check '[ $urc = 1 ] && [ "$(tail -n 1 "$T/locked.log")" = "Not every login of Switchboard'"'"'s could be taken out of your Keychain (is it locked?), so ~/.switchboard was kept. Run this again once it is unlocked." ] && [ -f "$H/.switchboard/accounts.json" ] && [ -x "$H/.switchboard/bin/switchboard" ]' \
    "a Keychain that refuses: exit 1, one line, and ~/.switchboard and the program stay for another run"
  # Without the program and without the copy a plain uninstall leaves, --all still stops before removing anything.
  rm -rf "$H/.switchboard"; installed; rm -rf "$H/.switchboard/bin" "$H/.switchboard/app"; seed_keychain
  run "$H" sh "$T/u/uninstall.sh" --all --no-agent > "$T/noprog.log" 2>&1; urc=$?
  check '[ $urc = 1 ] && grep -q "Nothing was removed" "$T/noprog.log" && [ -f "$H/.switchboard/accounts.json" ] && [ ! -e "$KC/calls.log" ]' \
    "no program and no copy beside the settings: --all stops before anything, as before"
else
  no "the uninstaller copy does not name the stand-in Keychain tool: nothing was run (the real one is never used here)"
fi
rm -rf "$T"
fi

if want 27; then
echo "== 27. A developer folder a macOS update emptied: the link app is skipped, and Apple's stand-in swiftc never runs"
# Offline as in case 11. A stand-in xcode-select names a developer folder with no swiftc in it (as in case 21), so
# Apple's /usr/bin/swiftc is only a stand-in, which would show the "install the command line developer tools" window:
# the installer must not run the link app's build. DEVELOPER_DIR names the same empty folder, so even a build would
# find no compiler.
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
T="$(fresh_home)"; H="$T/home"
PAGE_PY="$(/usr/bin/python3 -I -B -c 'import sys; print(sys.executable)')"
mkdir -p "$T/stale/developer/usr/bin"
printf '#!/bin/sh\necho "%s/stale/developer"\n' "$T" > "$T/stale/xcode-select"; chmod +x "$T/stale/xcode-select"
EXTRA_ENV="$OFFLINE SWITCHBOARD_PAGE_PYTHON=$PAGE_PY DEVELOPER_DIR=$T/stale/developer PATH=$T/stale:$H/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" sh "$HERE/install.sh" --no-agent --no-open --no-menu-app --port "$PORT" > "$T/install.log" 2>&1; rc=$?
EXTRA_ENV=""
sed 's/^/    /' "$T/install.log"
check '[ $rc = 0 ] && grep -qxF "Skipped the link app: it needs Apple'"'"'s command line tools (xcode-select --install). Everything else works without it." "$T/install.log" && ! grep -q "Building the link app" "$T/install.log" && [ ! -e "$H/.switchboard/build.log" ] && [ -z "$(ls -A "$H/Applications" 2>/dev/null)" ]' \
  "swiftc only Apple's stand-in: the link app is skipped in one line, and its build never runs"
rm -rf "$T"
fi

if want 28; then
echo "== 28. The installer's downloads: https only"
# Offline: the program's download is served over plain http on 127.0.0.1 (a port the system picks), as a redirect off
# https could lead; the installer must refuse it. The served copy is this tree's own.
T="$(fresh_home)"; H="$T/home"
PAGE_PY="$(/usr/bin/python3 -I -B -c 'import sys; print(sys.executable)')"
mkdir -p "$T/srv"; clone_tree "$T/clone"; (cd "$T" && tar -czf srv/main.tar.gz clone)
/usr/bin/python3 -I -B -c 'import functools, http.server, sys
s = http.server.HTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=sys.argv[1]))
open(sys.argv[2] + ".tmp", "w").write(str(s.server_address[1])); __import__("os").replace(sys.argv[2] + ".tmp", sys.argv[2])
s.serve_forever()' "$T/srv" "$T/port" > /dev/null 2>&1 &
SRV=$!
i=0; while [ $i -lt 50 ] && [ ! -s "$T/port" ]; do i=$((i + 1)); sleep 0.1; done
URL="http://127.0.0.1:$(cat "$T/port" 2>/dev/null)/main.tar.gz"
check 'curl -fs --noproxy "*" -o /dev/null "$URL"' "the stand-in serves the program over http ($URL)"
EXTRA_ENV="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 SWITCHBOARD_PAGE_PYTHON=$PAGE_PY SWITCHBOARD_REPO=example/switchboard SWITCHBOARD_ARCHIVE=$URL"
run "$H" sh -s -- --no-agent --no-open --no-link-app --no-menu-app --port "$PORT" < "$HERE/install.sh" > "$T/http.log" 2>&1; rc=$?
EXTRA_ENV=""
kill "$SRV" 2>/dev/null; wait "$SRV" 2>/dev/null
sed 's/^/    /' "$T/http.log"
# (curl refuses it; the pipe into tar then ends with tar's status, so the line is either of the two download stops)
check '[ $rc = 1 ] && grep -qxF -e "Switchboard: could not download $URL" -e "Switchboard: the download from $URL is not Switchboard." "$T/http.log" && [ "$(ls -A "$H")" = ".local" ]' \
  "the program over plain http: refused, it stops, and nothing is installed"
dl="$(grep -n "curl " "$HERE/install.sh" | grep -v "^[0-9]*: *#" | grep -e "-fsSL")"
check '[ "$(printf "%s\n" "$dl" | grep -c .)" = 2 ] && [ -z "$(printf "%s\n" "$dl" | grep -v -e "--proto ")" ] && [ -z "$(printf "%s\n" "$dl" | grep -v -e "--tlsv1.2")" ] && [ "$(printf "%s\n" "$dl" | grep -c -e "--retry 2 --connect-timeout 15")" = 2 ]' \
  "both downloads in install.sh (the program, the Python) ask for https, TLS 1.2 or newer, two retries and a 15 s connect limit"
rm -rf "$T"
fi

if want 29; then
echo "== 29. A home folder that is not the account's own: the installer and the uninstaller leave launchd alone"
# Offline. launchd's jobs belong to the user account and are known by their labels alone, so an install or an
# uninstall run from another home folder (a try-out, this check) must never stop or replace the account's own jobs.
# No run here is given --no-agent. A stand-in launchctl stands first on the PATH and only writes down what it was
# asked, so launchd is never reached either way.
OFFLINE="https_proxy=http://127.0.0.1:9 HTTPS_PROXY=http://127.0.0.1:9 http_proxy=http://127.0.0.1:9"
T="$(fresh_home)"; H="$T/home"
PAGE_PY="$(/usr/bin/python3 -I -B -c 'import sys; print(sys.executable)')"
mkdir -p "$T/guard"
printf '#!/bin/sh\necho "$*" >> "%s/launchctl.calls"\nexit 1\n' "$T" > "$T/guard/launchctl"; chmod +x "$T/guard/launchctl"
ELSEWHERE_LINE="This is not your account's own home folder, so no background job was added: it would take the place of your account's own."
EXTRA_ENV="$OFFLINE SWITCHBOARD_PAGE_PYTHON=$PAGE_PY PATH=$T/guard:$H/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" sh "$HERE/install.sh" --no-setup --no-open --no-link-app --no-menu-app --port "$PORT" > "$T/install.log" 2>&1; rc=$?
sed 's/^/    /' "$T/install.log"
check '[ $rc = 0 ] && [ ! -e "$T/launchctl.calls" ] && [ ! -e "$H/Library/LaunchAgents" ] && [ -x "$H/.switchboard/bin/switchboard" ]' \
  "the installer, without --no-agent: the program is installed, no job's file is written, and launchd is not asked"
check 'grep -qxF "$ELSEWHERE_LINE" "$T/install.log" && grep -q "^Without the background job, start the page with: " "$T/install.log" && [ "$(cat "$H/.switchboard/app/choices")" = "--no-agent --no-menu-app --no-link-app" ]' \
  "it says why in one line and how to start the page, and app/choices keeps --no-agent for the next update"
# The worst case for the uninstaller: this home holds job files named as the account's are, and choices from an
# install made with the jobs.
mkdir -p "$H/Library/LaunchAgents"
: > "$H/Library/LaunchAgents/io.github.amyjainberkeley.switchboard.plist"; : > "$H/Library/LaunchAgents/$MENUBAR_ID.plist"
printf '%s\n' "--no-menu-app --no-link-app" > "$H/.switchboard/app/choices"
cp -R "$H/.switchboard" "$T/installed"
run "$H" sh "$H/.switchboard/app/uninstall.sh" > "$T/uninstall.log" 2>&1; urc=$?
sed 's/^/    /' "$T/uninstall.log"
check '[ $urc = 0 ] && [ ! -e "$T/launchctl.calls" ] && [ ! -e "$H/.switchboard/bin" ] && [ ! -e "$H/.switchboard/app" ]' \
  "the uninstaller, without --no-agent: the program goes, and launchd is not asked"
rm -rf "$H/.switchboard"; cp -R "$T/installed" "$H/.switchboard"
run "$H" env SWITCHBOARD_BREW_ARGS="uninstall switchboard" sh "$HERE/packaging/homebrew/uninstall.sh" > "$T/brew.log" 2>&1; urc=$?
sed 's/^/    /' "$T/brew.log"
check '[ $urc = 0 ] && [ ! -e "$T/launchctl.calls" ] && [ ! -e "$H/.switchboard/bin" ]' \
  "Homebrew's uninstall step, with no --no-agent in the saved choices: the program goes, and launchd is not asked"
[ ! -e "$T/launchctl.calls" ] || sed 's/^/    launchctl was asked: /' "$T/launchctl.calls"
rm -rf "$T"
# The other side: in the account's own home the jobs are still added and removed. A stand-in dscl answers this home
# as the account's; the stand-in launchctl says a job is not loaded and takes whatever else it is told. The page is
# not there to answer, so the installer waits its 10 s for it.
T="$(fresh_home)"; H="$T/home"
mkdir -p "$T/guard"
printf '#!/bin/sh\necho "$*" >> "%s/launchctl.calls"\n[ "$1" != print ]\n' "$T" > "$T/guard/launchctl"
printf '#!/bin/sh\necho "NFSHomeDirectory: %s"\n' "$H" > "$T/guard/dscl"
chmod +x "$T/guard/launchctl" "$T/guard/dscl"
JOB="$H/Library/LaunchAgents/io.github.amyjainberkeley.switchboard.plist"
EXTRA_ENV="$OFFLINE SWITCHBOARD_PAGE_PYTHON=$PAGE_PY PATH=$T/guard:$H/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
run "$H" sh "$HERE/install.sh" --no-setup --no-open --no-link-app --no-menu-app --port "$PORT" > "$T/install.log" 2>&1; rc=$?
sed 's/^/    /' "$T/install.log"
check '[ $rc = 0 ] && grep -q "<string>io.github.amyjainberkeley.switchboard</string>" "$JOB" && grep -qxF "bootstrap gui/$(id -u) $JOB" "$T/launchctl.calls" && ! grep -qF "$ELSEWHERE_LINE" "$T/install.log" && [ "$(cat "$H/.switchboard/app/choices")" = "--no-menu-app --no-link-app" ]' \
  "in the account's own home (a stand-in answers this one as it): the job's file is written and launchd is asked to start it"
run "$H" sh "$H/.switchboard/app/uninstall.sh" > "$T/uninstall.log" 2>&1; urc=$?
sed 's/^/    /' "$T/uninstall.log"
check '[ $urc = 0 ] && grep -qxF "bootout gui/$(id -u)/io.github.amyjainberkeley.switchboard" "$T/launchctl.calls" && grep -qxF "bootout gui/$(id -u)/$MENUBAR_ID" "$T/launchctl.calls" && [ ! -e "$JOB" ]' \
  "and the uninstaller there asks launchd to stop both jobs, and the job's file goes"
EXTRA_ENV=""
rm -rf "$T"
fi

[ "$FAILED" = 0 ] && echo "All installer checks passed." || echo "Some installer checks failed."
exit "$FAILED"
