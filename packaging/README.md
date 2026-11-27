# Packaging

Switchboard installs two ways, both in the main README: Homebrew (`brew install amyjainberkeley/apps/switchboard`), the first
line, and the one-line installer without Homebrew. This folder holds what Homebrew runs.

The menu bar item is part of the Python program. The link app, a small Swift program, can come two ways: a release
carries it already built, signed with the publisher's Developer ID and notarized by Apple (`router/prebuilt`, made as
in "Signing a release" below), and the installer puts that copy in place once it has checked it. When the release
carries none, or any check fails, the installer builds it on the Mac that uses it, as before, which needs Apple's
command line tools. Nothing needs a Developer ID on the Mac that installs.

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

Make the link app again first, as in "Signing a release": a new version changes `router/Info.plist`, and the installer
takes a prebuilt link app only when it was made from the tree's own `Info.plist` and `SwitchboardLinks.swift`. Tag it
`v<version>` on GitHub. Then, in a clone of the tap (the `homebrew-tap` repository, not this one), run its
`./update-switchboard.sh <version>`, which sets the cask's version and SHA-256 from the tag's download and runs
`brew style`.

## Signing a release

`router/build.sh --release` makes the link app a release carries. It runs on the publisher's Mac, with Xcode or
Apple's command line tools, the Developer ID Application certificate in the login keychain, and a notary profile saved
once with `xcrun notarytool store-credentials <profile>`:

    sh router/build.sh --release router/prebuilt --identity <SHA-1 fingerprint> --notarize <profile> --check
    sh router/build.sh --release router/prebuilt --identity <SHA-1 fingerprint> --notarize <profile>

The first line checks everything the second needs (the tools, the certificate for that fingerprint with its issuer and
end date, the screen, the notary profile, the disk) and builds nothing. The second:

- builds one program for Apple silicon and Intel (two `swiftc -target` builds for the macOS in `LSMinimumSystemVersion`,
  joined by `lipo`), in the same bundle as the installer's build: the same `Info.plist`, so the same bundle
  identifier, which keeps the link app's place as the default browser;
- signs it with the hardened runtime and a secure timestamp, and verifies the signature;
- sends it to Apple's notary service and waits, staples the ticket to the app, and checks it with Gatekeeper
  (`spctl --assess`);
- writes `router/prebuilt/SwitchboardLinks.zip` and `router/prebuilt/links.json` (the zip's SHA-256, the source's
  SHA-256, the team, the architectures, the minimum macOS, whether it is notarized). Nothing else is written,
  registered with macOS or opened.

The identity is given by its SHA-1 fingerprint (`security find-identity -v -p codesigning` lists them), never by its
name: two certificates can carry the same name, and codesign then refuses the name as ambiguous. Notarizing takes a
few minutes, and the screen must stay unlocked until it ends: notarytool reads its saved sign-in from the keychain.

Commit the two files, and set `LINKS_TEAM` in `install.sh` to the team the build prints (once: a team stays the same for
later releases; `tests/test_link_app_release.py` fails while the zip is in the tree and `LINKS_TEAM` is empty). The
release is cut from the tree, so they ride along with it. The installer uses the prebuilt app only when the zip matches
its SHA-256, it was made from the tree's `router/SwitchboardLinks.swift` and `router/Info.plist`, its signature verifies
(`codesign --verify --strict`), it is signed by the team in `links.json` and in `LINKS_TEAM`, it carries a stapled
notarization ticket, and it was built for the Mac's processor and macOS. On any other outcome it says which check failed
and builds the app on the Mac as before.

`--identity -` makes the same build signed ad hoc and never notarized, to try the release build without a
certificate. The installer never uses it.

## PyPI

Switchboard is not on PyPI. The program, the account engine with it, runs on any Python 3.9 or newer with the
standard library only, so it fits in one environment; the menu bar item needs Python 3.12 to 3.14 and PyObjC (the
Pythons its pinned wheels are made for). What a package can't do yet is what the installer does after copying the
program: the background jobs and Switchboard.app in Applications. `switchboard setup` would have to add those.
