# Packaging

Switchboard installs two ways, both in the main README: Homebrew (`brew install amyjainberkeley/apps/switchboard`), the first
line, and the one-line installer without Homebrew. This folder holds what Homebrew runs.

Neither way needs a Mac app built ahead of time, a Developer ID or notarizing. The menu bar item is part of the
Python program, and the installer builds the link app on the Mac that uses it.

## Homebrew

A cask, not a formula: a formula installs into Homebrew's own folders and may not write to your home folder or add
background jobs, and Switchboard does both. A cask's `installer script` runs as you, so it runs `install.sh`
itself, the same program the one-line install runs. Claude Code is a cask too.

The cask, `Casks/switchboard.rb`, lives in the tap, the same owner's `homebrew-tap` repository on GitHub, so the command is
`brew install amyjainberkeley/apps/switchboard`. It downloads the release's tag from GitHub, checked against its SHA-256, and:

- runs `install.sh --homebrew`: everything the one-line install does but the command, which Homebrew links into its
  own bin folder (`homebrew/launcher`, linked as `switchboard` by the cask's one `binary` line, runs the one in
  `~/.switchboard/bin`), and the first run, left for `switchboard setup`. app/source is `homebrew:` and Homebrew's
  copy, so `switchboard update` runs `brew upgrade`. Run by Homebrew alone, the installer keeps the last install's
  choices (app/choices), which Homebrew can't give.
- adds the short name, `sb`, from the installer, not from a `binary` line: when a `binary` line's name is already in
  Homebrew's bin folder, Homebrew stops the whole install ("It seems there is already a Binary at ..."), and `sb` can
  be another program's. So the cask has one `binary` line, `switchboard`, and `install.sh --homebrew` links `sb` to
  it, in the same folder (the prefix its copy sits under, `<prefix>/Caskroom/...`, else `HOMEBREW_PREFIX`), only when
  nothing else on the Mac has the name; a link it can't make is skipped, never a failed install. The launcher answers
  to both names. If a formula installed later brings its own `sb`, Homebrew says it can't link it over ours;
  `brew uninstall switchboard` takes ours out.
- runs `homebrew/uninstall.sh` for its uninstall step. Homebrew runs that step before every upgrade and reinstall
  too, so it looks for the brew command above it: `brew upgrade`, `reinstall` and `install` leave Switchboard as it
  is, `--zap` runs `uninstall.sh --all` (the accounts and their logins in the Keychain go too), and anything else
  (`brew uninstall`, or no brew command found) runs `uninstall.sh`, keeping the choices beside the settings for a
  later Homebrew install.

`tests/install-check.sh` case 19 checks the installer's and the uninstall step's side of it.

## The one-line installer

The README's second way, for a Mac without Homebrew. It fetches `install.sh` from `raw.githubusercontent.com`
(`INSTALL_URL` in `install.sh`), which downloads the program from the repository's main branch and runs the first
run at the end. `INSTALL_URL` is also what `switchboard update` fetches again, through `~/.switchboard/app/source`.
A copy whose `REPO` line still holds its placeholder stops before it writes anything and says so.

## Releasing a version

Tag it `v<version>` on GitHub. Then, in a clone of the tap (the `homebrew-tap` repository, not this one), run its
`./update-switchboard.sh <version>`, which sets the cask's version and SHA-256 from the tag's download and runs
`brew style`.

## PyPI

Switchboard is not on PyPI. The program, the account engine with it, runs on any Python 3.9 or newer with the
standard library only, so it fits in one environment; the menu bar item needs Python 3.12 to 3.14 and PyObjC (the
Pythons its pinned wheels are made for). What a package can't do yet is what the installer does after copying the
program: the background jobs and Switchboard.app in Applications. `switchboard setup` would have to add those.
