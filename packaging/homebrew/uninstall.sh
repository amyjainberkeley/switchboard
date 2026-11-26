#!/bin/sh
# Homebrew's uninstall step for Switchboard: the cask's `uninstall script` runs this file.
#
# Homebrew runs a cask's uninstall step for `brew uninstall`, and also right before every upgrade and reinstall, just
# before it runs the installer again. Only a removal should remove anything: Switchboard's uninstall.sh takes out the
# Python it runs on, what Switchboard added to Claude Code's settings and claude-mem, so running it before an upgrade
# would set all of that up again for nothing, and the status line would be gone in between. So this looks for the
# brew command that is running (the nearest brew process above this one; `brew bundle cleanup` runs its own
# `brew uninstall`), and:
#
#   brew upgrade, reinstall or install    leaves Switchboard as it is: the installer runs next and keeps it
#   with --zap (uninstall or reinstall)    runs uninstall.sh --all: your accounts and settings go too
#   anything else                          runs uninstall.sh: the program goes, your settings and accounts stay
#
# "Anything else" includes `brew uninstall` and no brew command found at all, so a removal is never missed. Before a
# plain removal the install's choices are kept beside the settings (~/.switchboard/choices.last), so a Homebrew
# install after it, even one this misread, keeps them (install.sh --homebrew reads them back).
# On a removal, the short name the installer added in Homebrew's bin folder goes too, when it is still its link.
# It runs the installed copy of uninstall.sh (~/.switchboard/app), and only when that is missing the one beside this
# file. An install made with --no-agent is removed with --no-agent.
# SWITCHBOARD_BREW_ARGS stands in for the brew command's arguments (tests).
set -eu
SHORT="sb"          # the command's short name, which install.sh --homebrew links beside Homebrew's switchboard

brew_args() {
  if [ -n "${SWITCHBOARD_BREW_ARGS+set}" ]; then
    printf '%s\n' "$SWITCHBOARD_BREW_ARGS"
    return 0
  fi
  pid="$PPID"
  n=0
  while [ "$n" -lt 10 ] && [ -n "$pid" ] && [ "$pid" -gt 1 ]; do
    args="$(ps -o args= -p "$pid" 2>/dev/null || true)"
    case "$args" in
      */Homebrew/brew.rb\ *) printf '%s\n' "${args#*/Homebrew/brew.rb }"; return 0 ;;
    esac
    pid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
    n=$((n + 1))
  done
  return 1
}

command=""
zap=0
if args="$(brew_args)"; then
  set -f
  # shellcheck disable=SC2086
  set -- $args
  set +f
  for a in "$@"; do
    case "$a" in
      --zap) zap=1 ;;
      -*) ;;
      *) [ -n "$command" ] || command="$a" ;;
    esac
  done
fi
if [ "$zap" = 0 ]; then
  case "$command" in
    install|upgrade|reinstall) echo "Switchboard stays as it is for brew $command: the installer that runs next keeps it."; exit 0 ;;
  esac
fi

data="$HOME/.switchboard"
script="$data/app/uninstall.sh"
[ -f "$script" ] || script="$(cd "$(dirname "$0")/../.." && pwd)/uninstall.sh"
set --
[ "$zap" = 0 ] || set -- --all
if grep -q -e '--no-agent' "$data/app/choices" 2>/dev/null; then set -- "$@" --no-agent; fi
if [ "$zap" = 0 ] && [ -f "$data/app/choices" ]; then cp "$data/app/choices" "$data/choices.last" 2>/dev/null || true; fi
rc=0
/bin/sh "$script" "$@" || rc=$?
# The short name the installer added in Homebrew's bin folder (a link to Homebrew's switchboard link beside it), once
# the program is gone: only that link, never another program's file of the same name. Never a reason to fail.
here="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
case "$here" in */Caskroom/*) bin="${here%%/Caskroom/*}/bin" ;; *) bin="${HOMEBREW_PREFIX:+$HOMEBREW_PREFIX/bin}" ;; esac
if [ "$rc" = 0 ] && [ -n "$bin" ] && [ -L "$bin/$SHORT" ] && [ "$(readlink "$bin/$SHORT")" = switchboard ]; then
  rm -f "$bin/$SHORT" 2>/dev/null && echo "Removed the $SHORT command from $bin." || true
fi
exit "$rc"
