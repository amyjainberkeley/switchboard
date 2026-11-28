#!/bin/sh
# Removes Switchboard.
#
#   sh uninstall.sh [--all] [--no-agent]
#
#   (no options)  remove the program (with its account engine), Switchboard's Python and packages
#                 (~/.switchboard/runtime), the menu bar item, the background jobs, the link app (Switchboard
#                 Links) if it was built, and the commands. Your settings and accounts stay in ~/.switchboard, and the
#                 accounts' saved logins stay in your Keychain, so a reinstall picks them up. To remove those later,
#                 run the copy of this file it leaves there, with --all: sh ~/.switchboard/uninstall.sh --all
#                 The notes of chats you carried on in another assistant stay too, in ~/.switchboard-notes.
#                 Whatever Switchboard changed in Claude Code's settings and CLAUDE.md is put back, and claude-mem and
#                 codegraph, when Switchboard installed them, are taken out (claude-mem's notes in ~/.claude-mem stay).
#                 An older install's jobs and apps, under the names Switchboard had before, go the same way.
#   --all         also remove the accounts (their saved logins leave your Keychain, with any earlier logins and
#                 recovery copies Switchboard kept), the gateway's keys (from your Keychain) and everything else in
#                 ~/.switchboard, the pictures of your pages (previews/) included, and the notes folder beside it
#                 (~/.switchboard-notes)
#   --no-agent    leave the system's own lists alone: launchd, and the record of apps macOS keeps (Launch
#                 Services). For an install made with --no-agent, and for tests.
#
# It never touches the login Claude Code is using, or your browser profiles.
set -eu

[ -n "${HOME:-}" ] && [ -d "$HOME" ] || { echo "No home folder found." >&2; exit 1; }
# macOS's sudo keeps HOME: as root, this would work on the person's own folder with root's rights.
[ "$(id -u)" != 0 ] || { echo "Run this as yourself, without sudo: Switchboard lives in your own home folder." >&2; exit 1; }
DATA="$HOME/.switchboard"          # always this folder and, with --all, the notes folder beside it:
NOTES="$HOME/.switchboard-notes"   # nothing else is ever removed. (The notes of chats carried on in another assistant
                                   # and handed jobs' answers: switchboard/providers/contract.py NOTES_DIR_NAME.)
LABEL="io.github.amyjainberkeley.switchboard"
LINKS_ID="io.github.amyjainberkeley.switchboard.links"
LINKS_APP="$HOME/Applications/Switchboard Links.app"     # the link app
OLDER_LINKS="$HOME/Applications/Switchboard.app"         # the link app's path before 0.2.0 (known by LINKS_ID)
MENUBAR_ID="$LABEL.menubar"                              # the menu bar item: its job's label and its app's identifier
MENUBAR_PLIST="$HOME/Library/LaunchAgents/$MENUBAR_ID.plist"
WATCH_ID="$LABEL.watch"                                  # the sign-in watch's job
WATCH_PLIST="$HOME/Library/LaunchAgents/$WATCH_ID.plist"
MENUBAR_APP="$DATA/menubar/Switchboard.app"              # the app it runs in, when it is not in Applications
APPLICATIONS="${SWITCHBOARD_APPLICATIONS:-/Applications}"   # (tests point this at a folder of their own)
MENUBAR_COPY="$APPLICATIONS/Switchboard.app"             # where the installer puts it,
MENUBAR_MINE="$HOME/Applications/Switchboard.app"        # or here, when /Applications needs a password
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
PROGRAM="$DATA/bin/switchboard"
SHORT="sb"                         # the command's short name, a link to PROGRAM beside switchboard's (install.sh)
SECURITY=/usr/bin/security         # macOS's Keychain tool (the install check runs a copy of this file naming a stand-in)
# The names an older install used (before Nov 26 2026), swept once below: its jobs, its menu bar app (only one made for
# this data folder), and its link app (which gives the default-browser seat back first, as this install's does).
OLD_LABEL="io.github.claude-switchboard"
OLD_MENUBAR_ID="$OLD_LABEL.menubar"
OLD_LINKS_ID="$OLD_LABEL.links"
OLD_LINKS="$HOME/Applications/Switchboard Links (old).app"  # where install.sh moves it while it is the default browser
# A path under the home folder as ~/..., as the installer says it.
short() { case "$1" in "$HOME") printf '~' ;; "$HOME"/*) printf '~%s' "${1#"$HOME"}" ;; *) printf '%s' "$1" ;; esac; }
ALL=0
AGENT=1
for a in "$@"; do
  case "$a" in
    --all) ALL=1 ;;
    --no-agent) AGENT=0 ;;
    -h|--help) echo "sh uninstall.sh [--all] [--no-agent]"; exit 0 ;;
    *) echo "Unknown option: $a" >&2; exit 1 ;;
  esac
done
# launchd's jobs and macOS's list of apps belong to the user account, not to a home folder, and launchd knows a job
# by its label alone. From a home that is known not to be the account's (a try-out, a test), this runs as with
# --no-agent, so the account's own Switchboard is never stopped. (A home that can't be told is taken as its own.)
OWN_HOME="$(dscl . -read "/Users/$(id -un)" NFSHomeDirectory 2>/dev/null | sed -n -e 's/^NFSHomeDirectory: //p' -e '/^NFSHomeDirectory:$/{n;s/^ //p;}' | head -n 1)"
if [ -n "$OWN_HOME" ] && [ "$(cd "$HOME" 2>/dev/null && pwd -P)" != "$(cd "$OWN_HOME" 2>/dev/null && pwd -P)" ]; then AGENT=0; fi

# What ~/.switchboard holds besides settings, read before anything changes, so the last lines name only what is there:
# accounts (the engine's accounts file; their logins are in the Keychain) and keys (the gateway's list; the keys
# themselves are in the Keychain). An accounts file that can't be read counts as accounts.
ACCOUNTS_FILE="$DATA/accounts.json"
HAS="settings"
THEIRS=""
if [ -e "$ACCOUNTS_FILE" ] && { [ ! -r "$ACCOUNTS_FILE" ] || grep -q '"email"' "$ACCOUNTS_FILE"; }; then
  HAS="settings and accounts"; THEIRS="their logins"
fi
if grep -q '"name"' "$DATA/gateway-lanes.json" 2>/dev/null; then
  if [ -n "$THEIRS" ]; then HAS="settings, accounts and keys"; THEIRS="their logins and the keys"
  else HAS="settings and keys"; THEIRS="the keys"; fi
fi
# The notes folder, read before anything changes too: the person's own folder, or a link someone put in its place.
HAS_NOTES=0
if [ -d "$NOTES" ] || [ -L "$NOTES" ]; then HAS_NOTES=1; fi

# --all needs the program to take its accounts' logins out of the Keychain: check that before changing anything. After
# a plain uninstall (it left a copy of this file beside the settings) the program is gone, and they go below by their
# services' names.
if [ "$ALL" = 1 ] && [ -n "$THEIRS" ] && [ "$THEIRS" != "the keys" ] && [ ! -x "$PROGRAM" ] && [ ! -f "$DATA/uninstall.sh" ]; then
  echo "Switchboard is not installed, so the logins it keeps in your Keychain can't be removed." >&2
  echo "Run the installer, then this again. Nothing was removed." >&2
  exit 1
fi

plist_value() { [ -f "$1/Contents/Info.plist" ] && /usr/libexec/PlistBuddy -c "Print :$2" "$1/Contents/Info.plist" 2>/dev/null || true; }
# The link app, by its bundle identifier (any copy, under either name), or an older install's, by the identifier it had
# before (in its place, or moved aside by install.sh while it was the default browser). Nothing else is ever run as
# the link app.
is_link_app() {
  [ -n "$1" ] && [ -d "$1" ] || return 1
  id="$(plist_value "$1" CFBundleIdentifier)"
  [ "$id" = "$LINKS_ID" ] || [ "$id" = "$OLD_LINKS_ID" ]
}
link_exe() { printf '%s/Contents/MacOS/%s' "$1" "$(plist_value "$1" CFBundleExecutable)"; }

# Give the default-browser seat back first. macOS asks, and only you can answer.
HELPER=""
for app in "$LINKS_APP" "$OLDER_LINKS" "$OLD_LINKS"; do
  if [ -z "$HELPER" ] && is_link_app "$app" && [ -x "$(link_exe "$app")" ]; then HELPER="$app"; fi
done
# The seat is this install's to give back only when its own link app holds it. macOS keeps one default browser for
# the whole account, whatever the home folder, so another copy of the link app (a try-out installed in another home)
# can hold it: that copy keeps its seat, and macOS is not asked.
same_place() { [ -d "$1" ] && [ -d "$2" ] && [ "$(cd "$1" && pwd -P)" = "$(cd "$2" && pwd -P)" ]; }
default_is_ours() {
  now="$("$(link_exe "$HELPER")" --status 2>/dev/null || true)"
  is_link_app "$now" && { same_place "$now" "$LINKS_APP" || same_place "$now" "$OLDER_LINKS" || same_place "$now" "$OLD_LINKS"; }
}
if [ -n "$HELPER" ] && default_is_ours; then
  echo "Switchboard Links is your default browser. macOS will ask who gets it back: choose your usual browser."
  "$(link_exe "$HELPER")" --restore >/dev/null 2>&1 || true
  if default_is_ours; then
    echo "Switchboard Links is still the default browser, so nothing was removed." >&2
    echo "Pick another one in System Settings > Desktop & Dock > Default web browser, then run this again." >&2
    exit 1
  fi
fi

# An older install, under the names Switchboard had before (io.github.claude-switchboard...): its jobs go before this
# install's, once, each only when launchd has it or its file is there: its sign-in watch first (it starts the page
# again), then its menu bar item, then its page (a switch under way finishes first: up to 60 s), each waited for. On a
# Mac that never had them launchd is only asked whether it has them, and nothing is said. Its menu bar app and its link
# app go below, with this install's.
if [ "$AGENT" = 1 ]; then
  for old in "$OLD_LABEL.watch" "$OLD_MENUBAR_ID" "$OLD_LABEL"; do
    went=0
    if launchctl print "gui/$(id -u)/$old" >/dev/null 2>&1; then
      launchctl bootout "gui/$(id -u)/$old" >/dev/null 2>&1 || true
      case "$old" in *.watch) n=60 ;; *.menubar) n=20 ;; *) n=240 ;; esac
      i=0; while [ $i -lt $n ] && launchctl print "gui/$(id -u)/$old" >/dev/null 2>&1; do i=$((i + 1)); sleep 0.25; done
      went=1
    fi
    if [ -f "$HOME/Library/LaunchAgents/$old.plist" ]; then
      rm -f "$HOME/Library/LaunchAgents/$old.plist"
      went=1
    fi
    [ "$went" = 0 ] || echo "Removed an older install's background job ($old)."
  done
fi

# The menu bar item goes first: its job, then any copy of it still running (one started by hand, or with
# --no-agent). Only this install's copies are stopped: a program in one of its apps (by bundle identifier), or the
# launcher's own command, which names this install's program folder and `menubar`. Asked first, told after 5 s.
# This install's: the menu bar's bundle identifier, and made for this data folder (an app made before the app named
# its folder counts as the usual ~/.switchboard's, which this always is). Another account's or another try's is never
# stopped or removed.
is_menubar_app() {
  [ -d "$1" ] || return 1
  made_for="$(plist_value "$1" SwitchboardData)"
  case "$(plist_value "$1" CFBundleIdentifier)" in
    "$MENUBAR_ID") [ -z "$made_for" ] || [ "$made_for" = "$(cd "$DATA" 2>/dev/null && pwd -P)" ] ;;
    # an older install's (the identifier it had before): only one that names this data folder
    "$OLD_MENUBAR_ID") here="$(cd "$DATA" 2>/dev/null && pwd -P)"
      [ -n "$made_for" ] && [ -n "$here" ] && [ "$(cd "$made_for" 2>/dev/null && pwd -P)" = "$here" ] ;;
    *) return 1 ;;
  esac
}
menubar_pids() {
  e1=""; e2=""; e3=""
  if is_menubar_app "$MENUBAR_APP"; then e1="$(link_exe "$MENUBAR_APP")"; fi
  if is_menubar_app "$MENUBAR_COPY"; then e2="$(link_exe "$MENUBAR_COPY")"; fi
  if is_menubar_app "$MENUBAR_MINE"; then e3="$(link_exe "$MENUBAR_MINE")"; fi
  ps -axo pid=,command= 2>/dev/null | while read -r pid cmd; do
    case "$cmd" in
      *" $DATA/app menubar"|*" $DATA/app menubar "*) echo "$pid" ;;
      *) for e in "$e1" "$e2" "$e3"; do
           [ -n "$e" ] || continue
           case "$cmd" in "$e"|"$e "*) echo "$pid"; break ;; esac
         done ;;
    esac
  done
}
if [ "$AGENT" = 1 ]; then
  launchctl bootout "gui/$(id -u)/$MENUBAR_ID" >/dev/null 2>&1 || true
  if [ -f "$MENUBAR_PLIST" ]; then
    rm -f "$MENUBAR_PLIST"
    echo "Removed the menu bar item's background job ($MENUBAR_ID)."
  fi
fi
pids="$(menubar_pids || true)"
if [ -n "$pids" ]; then
  kill -TERM $pids 2>/dev/null || true
  i=0; while [ $i -lt 20 ] && [ -n "$(menubar_pids || true)" ]; do i=$((i + 1)); sleep 0.25; done
  pids="$(menubar_pids || true)"
  [ -z "$pids" ] || kill -KILL $pids 2>/dev/null || true
  echo "Quit the menu bar item."
fi

# The sign-in watch, before the page: it starts a page that has stopped, so it goes first, or it could start the page
# again while the page is being removed. Its job, then a copy still running (one started by hand, or with --no-agent):
# only this install's, which runs the watch from this install's program folder. Asked first, told after 12 s (it gives
# a look under way up to 10 s to finish).
watch_pids() {
  ps -axo pid=,command= 2>/dev/null | while read -r pid cmd; do
    case "$cmd" in *switchboard.accounts.signin_watch*" $DATA/app"|*switchboard.accounts.signin_watch*" $DATA/app "*) echo "$pid" ;; esac
  done
}
if [ "$AGENT" = 1 ]; then
  launchctl bootout "gui/$(id -u)/$WATCH_ID" >/dev/null 2>&1 || true
  if [ -f "$WATCH_PLIST" ]; then
    rm -f "$WATCH_PLIST"
    echo "Removed the sign-in watch's background job ($WATCH_ID)."
  fi
fi
pids="$(watch_pids || true)"
if [ -n "$pids" ]; then
  kill -TERM $pids 2>/dev/null || true
  i=0; while [ $i -lt 48 ] && [ -n "$(watch_pids || true)" ]; do i=$((i + 1)); sleep 0.25; done
  pids="$(watch_pids || true)"
  [ -z "$pids" ] || kill -KILL $pids 2>/dev/null || true
  echo "Stopped the sign-in watch."
fi

# The page, and with it automatic switching (the engine's loop runs under it).
if [ "$AGENT" = 1 ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  if [ -f "$HOME/Library/LaunchAgents/$LABEL.plist" ]; then
    rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
    echo "Removed the background job ($LABEL)."
  fi
fi
# A page started by hand (`switchboard serve`, how a --no-agent install runs it), from this install's program: asked
# to stop (it lets automatic switching finish a switch first), told after 60 s, as launchd's ExitTimeOut would.
page_pids() {
  ps -axo pid=,command= 2>/dev/null | while read -r pid cmd; do
    case "$cmd" in *" $DATA/app serve"|*" $DATA/app serve "*) echo "$pid" ;; esac
  done
}
pids="$(page_pids || true)"
if [ -n "$pids" ]; then
  kill -TERM $pids 2>/dev/null || true
  i=0; while [ $i -lt 240 ] && [ -n "$(page_pids || true)" ]; do i=$((i + 1)); sleep 0.25; done
  pids="$(page_pids || true)"
  [ -z "$pids" ] || kill -KILL $pids 2>/dev/null || true
  echo "Stopped the page you started by hand."
fi
# A page started by hand may still run its automatic-switching loop (python -m switchboard.accounts.autoloop, its pid
# recorded by the page): stop that loop (it ends after the step it is in, finishes a switch first, and saves a renewal
# already sent), only when that pid is still such a loop; after 45 s it is killed. Nothing below runs while it is still
# there.
is_loop() {
  ps -p "$1" -o command= 2>/dev/null | grep -q 'switchboard\.accounts\.autoloop'
}
stop_loop() {
  [ -f "$DATA/auto.pid" ] || return 0
  pid="$(cat "$DATA/auto.pid" 2>/dev/null || true)"
  case "$pid" in ''|*[!0-9]*) return 0 ;; esac
  is_loop "$pid" || return 0
  kill -TERM "$pid" 2>/dev/null || true
  i=0; while [ $i -lt 45 ] && is_loop "$pid"; do i=$((i + 1)); sleep 1; done
  if is_loop "$pid"; then
    kill -KILL "$pid" 2>/dev/null || true
    i=0; while [ $i -lt 10 ] && is_loop "$pid"; do i=$((i + 1)); sleep 1; done
  fi
  if is_loop "$pid"; then
    echo "Automatic switching (process $pid) did not stop, so the uninstall stopped here: the program, its commands, your settings and accounts are still there. Run this again in a minute." >&2
    return 1
  fi
  echo "Stopped automatic switching."
}
stop_loop || exit 1

# Claude Code's plain login file, when the sign-in watch put it back or left it in place after the login in it went
# back into the Keychain (signin-hold.json knows it by its exact stamp), goes before the program and that note do
# (the watch is stopped above, so it can't put it back again); a file Claude Code wrote itself is never touched, and
# one that is the only sign-in Claude Code has stays (the program says so). One call of the program's own Python.
# When the Keychain gives that call no answer (exit 3), the file and the note stay and so do the program and
# ~/.switchboard, so the line it prints can be followed: run this again. When the note is there and that call can't be
# made (no program, or no Python that runs without a dialog), the file is left as it is, and a line says so.
HOLD_PY="$(sed -n 's/^PY="\(.*\)"$/\1/p' "$PROGRAM" 2>/dev/null | head -n 1 || true)"
if [ -f "$DATA/signin-hold.json" ] && [ -f "$DATA/app/switchboard/accounts/signin_watch.py" ] && [ -n "$HOLD_PY" ] \
   && [ -x "$HOLD_PY" ] && { [ "$HOLD_PY" != /usr/bin/python3 ] \
   || { d="$(xcode-select -p 2>/dev/null)" && [ -n "$d" ] && [ -x "$d/usr/bin/python3" ]; }; }; then
  hold_rc=0
  "$HOLD_PY" -I -B -c 'import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module("switchboard.accounts.signin_watch", run_name="__main__", alter_sys=True)' "$DATA/app" --tidy || hold_rc=$?
  if [ "$hold_rc" = 3 ]; then
    if [ "${ALL:-0}" = 1 ]; then again=" --all"; else again=""; fi
    echo "The program and its data folder were kept, so this can run again once the Keychain answers: sh \"$DATA/app/uninstall.sh\"$again. Switchboard's page, menu bar item and automatic switching stay stopped until then." >&2
    exit 1
  elif [ "$hold_rc" != 0 ]; then
    echo "A plain login file the sign-in watch kept for Claude Code may still be in Claude Code's folder (.credentials.json): the program could not finish looking at it, so it was left as it is. Claude Code removes it itself the next time it saves its login."
  fi
elif [ -f "$DATA/signin-hold.json" ]; then
  echo "A plain login file the sign-in watch kept for Claude Code may still be in Claude Code's folder (.credentials.json): the program was not there to look at it, so it was left as it is. Claude Code removes it itself the next time it saves its login."
fi

# Before the program goes: put back what Switchboard changed in Claude Code (the status line, its notification hooks,
# the Models setting, house rules, the code map, claude-mem's settings), take out its own copies of Switchboard's
# skill, take out claude-mem and codegraph when Switchboard installed them, and clear a sign-in that was cut short.
# With --all, every account (its saved login leaves your Keychain), then every other login of Switchboard's still
# there (earlier ones, recovery copies: only the program's records find them), and the keys for the gateway go too.
# When anything could not be put back or removed (tidy names it), the program and ~/.switchboard, with the records
# the undo needs, stay, so this can run again.
# A program that can't start names nothing (its Python is gone: 126 or 127; or its folder is): then one line says
# what was not put back, and the rest goes on, so a broken install can still be removed.
TIDY_RAN=0
if [ -x "$DATA/bin/switchboard" ]; then
  if [ "$ALL" = 1 ]; then TIDY_ALL="--all"; else TIDY_ALL=""; fi
  rc=0
  if grep -qF "\"$DATA/app\"" "$DATA/bin/switchboard" && [ ! -f "$DATA/app/switchboard/__main__.py" ]; then rc=127
  else "$DATA/bin/switchboard" tidy $TIDY_ALL || rc=$?; fi
  case "$rc" in
    0) TIDY_RAN=1 ;;
    126|127) echo "Switchboard could not start, so what it changed in Claude Code's settings was not put back: see \"The uninstaller says something could not be put back\" in docs/troubleshooting.md." >&2 ;;
    *) echo "Not everything could be put back (named above), so the program and ~/.switchboard were kept. Fix that, then run this again: sh ~/.switchboard/app/uninstall.sh${TIDY_ALL:+ --all}" >&2
       exit 1 ;;
  esac
fi
# --all when no program ran tidy (none is left, or it could not start): what is still in the Keychain under
# Switchboard's own seven services (saved logins, earlier logins, recovery copies, the Mac-wide parts of Claude Code's
# login, the gateway's keys, one-year tokens, API keys) goes, in plain shell, one item at a time until none is found
# (exit 44). Never any other service. (When tidy ran, the program did this itself.)
if [ "$ALL" = 1 ] && [ "$TIDY_RAN" = 0 ]; then
  for service in "Switchboard login" "Switchboard login (previous)" "Switchboard recovery" \
                 "Switchboard login (Mac-wide parts)" "switchboard-gateway" "Switchboard token" "Switchboard API key"; do
    n=0; rc=0
    while [ $n -lt 500 ]; do
      "$SECURITY" delete-generic-password -s "$service" >/dev/null 2>&1 || { rc=$?; break; }
      n=$((n + 1))
    done
    if [ "$rc" != 44 ]; then
      echo "Not every login of Switchboard's could be taken out of your Keychain (is it locked?), so ~/.switchboard was kept. Run this again once it is unlocked." >&2
      exit 1
    fi
  done
fi
# Switchboard's skill for coding agents, when no program ran tidy (tidy takes it out itself): only a copy that holds
# Switchboard's marker, and in it only SKILL.md, the files in reference/ its marker names and the marker (switchboard/
# skillfile.py: NAME, MARK and AGENTS_SKILLS; the marker lists one file a line). A folder that is a link, and anything
# else in the folder, is left.
if [ "$TIDY_RAN" = 0 ]; then
  for d in "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/switchboard" "$HOME/.agents/skills/switchboard"; do
    mark="$d/.switchboard-skill.json"
    if [ ! -L "$d" ] && [ -f "$mark" ] && [ ! -L "$mark" ] && grep -q '"by": "switchboard"' "$mark"; then
      if [ ! -L "$d/reference" ]; then
        for f in $(sed -n 's|^ *"\(reference/[A-Za-z0-9_-][A-Za-z0-9_.-]*\)",\{0,1\}$|\1|p' "$mark"); do
          rm -f "$d/$f"
        done
      fi
      rm -f "$d/SKILL.md" "$mark"
      rmdir "$d/reference" 2>/dev/null || true
      rmdir "$d" 2>/dev/null || true
      echo "Removed Switchboard's skill ($(short "$d"))."
    fi
  done
fi
for d in "$HOME/.local/bin" /usr/local/bin; do
  for name in switchboard switchboard-engine; do              # (switchboard-engine: an older install's command)
    if [ -L "$d/$name" ] && [ "$(readlink "$d/$name")" = "$DATA/bin/$name" ]; then
      rm -f "$d/$name"
      echo "Removed the $name command from $(short "$d")."
    fi
  done
  # The short name: only the installer's own link to the program. Another program's is never touched.
  if [ -L "$d/$SHORT" ] && [ "$(readlink "$d/$SHORT")" = "$PROGRAM" ]; then
    rm -f "$d/$SHORT" || true
    echo "Removed the $SHORT command from $(short "$d")."
  fi
done
# The menu bar item's app: the one in ~/.switchboard/menubar goes with the program below; a copy in /Applications or
# ~/Applications only when it is this install's (above). macOS is told it went (not with --no-agent). Then the empty
# cache folder macOS may make for it.
for app in "$MENUBAR_COPY" "$MENUBAR_MINE"; do
  is_menubar_app "$app" || continue
  [ "$AGENT" = 0 ] || "$LSREGISTER" -u "$app" >/dev/null 2>&1 || true
  if rm -rf "$app" 2>/dev/null && [ ! -e "$app" ]; then
    echo "Removed the menu bar item's app ($(short "$app"))."
  else
    echo "Could not remove $(short "$app"): move it to the Trash yourself." >&2
  fi
done
rmdir "$HOME/Library/Caches/$MENUBAR_ID" "$HOME/Library/Caches/$OLD_MENUBAR_ID" 2>/dev/null || true
for app in "$LINKS_APP" "$OLDER_LINKS" "$OLD_LINKS"; do
  is_link_app "$app" || continue
  [ "$AGENT" = 0 ] || "$LSREGISTER" -u "$app" >/dev/null 2>&1 || true
  rm -rf "$app"
  echo "Removed the link app (~/Applications/$(basename "$app"))."
done
if [ -d "$DATA/runtime" ]; then
  rm -rf "$DATA/runtime"
  echo "Removed Switchboard's Python and packages ($(short "$DATA/runtime"))."
fi
# A plain uninstall takes this file away with the program: a copy stays beside the settings, so the command that
# removes the rest (the last line) is still there.
if [ "$ALL" = 0 ] && [ -d "$DATA" ] && [ -f "$0" ]; then
  { cp "$0" "$DATA/uninstall.sh.new" && mv -f "$DATA/uninstall.sh.new" "$DATA/uninstall.sh"; } 2>/dev/null || rm -f "$DATA/uninstall.sh.new"
fi
rm -rf "$DATA/app" "$DATA/app.new" "$DATA/app.old" "$DATA/bin" "$DATA/logins" "$DATA/menubar" "$DATA/terminal" \
       "$DATA/auto.pid" "$DATA/auto.log" "$DATA/auto.log.1" "$DATA/install.log" "$DATA/menubar-install.log" \
       "$DATA/engine-auto.pid" "$DATA/engine-auto.err" "$DATA/engine-install.log" \
       "$DATA/build.log" "$DATA/menu-app-build.log" \
       "$DATA/signin-watch.json" "$DATA/signin-watch.lease" "$DATA/signin-watch.out" \
       "$DATA/signin-watch.log" "$DATA/signin-watch.log.1" "$DATA/signin-watch.log.2" "$DATA/signin-watch.log.3" \
       "$DATA/signin-record.jsonl" "$DATA/signin-record.jsonl.1" "$DATA/signin-record.jsonl.2" \
       "$DATA/signin-record.jsonl.3"
echo "Removed the program and its commands ($(short "$DATA/app"), $(short "$DATA/bin"))."

REST=""
if [ "$ALL" = 1 ]; then
  rm -rf "$DATA"
  # The notes folder goes too. A link in its place is unlinked, never followed. Notes that will not go are named, and
  # then the last line does not say that nothing is left.
  LEFT=0
  if [ "$HAS_NOTES" = 1 ]; then
    if [ -L "$NOTES" ]; then rm -f "$NOTES" 2>/dev/null || true; else rm -rf "$NOTES" 2>/dev/null || true; fi
    if [ -e "$NOTES" ] || [ -L "$NOTES" ]; then
      echo "Could not remove $(short "$NOTES") (your notes of chats): move it to the Trash yourself." >&2
      LEFT=1
    else
      echo "Removed your notes of chats ($(short "$NOTES"))."
    fi
  fi
  if [ "$LEFT" = 0 ]; then echo "Removed $(short "$DATA") with your $HAS. Nothing of Switchboard is left."
  else echo "Removed $(short "$DATA") with your $HAS."; fi
elif [ ! -d "$DATA" ] || { [ -z "$(ls -A "$DATA" | grep -vx uninstall.sh)" ] && rm -f "$DATA/uninstall.sh" && rmdir "$DATA" 2>/dev/null; }; then
  [ "$HAS_NOTES" = 1 ] || echo "Nothing of Switchboard is left."     # there were no settings or accounts to keep
elif [ -n "$THEIRS" ]; then
  echo "Kept your $HAS in $(short "$DATA") (a reinstall picks them up)."
  REST="To remove them too, with $THEIRS in your Keychain, run:"
else
  echo "Kept your settings in $(short "$DATA") (a reinstall picks them up)."
  REST="To remove them too, run:"
fi
# A plain uninstall keeps the notes with the settings, and says where they are.
if [ "$ALL" = 0 ] && [ "$HAS_NOTES" = 1 ]; then echo "Kept your notes of chats in $(short "$NOTES")."; fi
echo "The login Claude Code is using was not touched."
# The command that removes the rest, alone on the last line: the copy of this file kept beside the settings. Without
# that copy (this file was not run from a file), the ways there were before it.
if [ "$ALL" = 0 ] && [ -f "$DATA/uninstall.sh" ]; then
  echo "$REST"
  echo "sh ~/.switchboard/uninstall.sh --all"
elif [ "$ALL" = 0 ] && [ -d "$DATA" ] && [ -n "$THEIRS" ]; then
  echo "To remove them too, with $THEIRS in your Keychain, install Switchboard again, then run: sh ~/.switchboard/app/uninstall.sh --all"
elif [ "$ALL" = 0 ] && [ -d "$DATA" ] && [ "$HAS_NOTES" = 1 ]; then
  echo "To remove them too: rm -rf ~/.switchboard ~/.switchboard-notes"
elif [ "$ALL" = 0 ] && [ -d "$DATA" ]; then
  echo "To remove them too: rm -rf ~/.switchboard"
elif [ "$ALL" = 0 ] && [ "$HAS_NOTES" = 1 ]; then
  echo "To remove them too: rm -rf ~/.switchboard-notes"
fi
