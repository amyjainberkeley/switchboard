# Contributing

Thanks for helping. A few things make it go well:

- Open an issue before a big change, so we agree on it first. A small fix can go straight to a pull request.
- Keep each change small, with a test that fails before the fix and passes after.
- Keep the words plain and short, and every sentence true to the code.

Everyone here follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## The parts

The page, the commands and the account engine run on Python 3.9 or newer, standard library only. The menu bar item runs on 3.12 to 3.14, drawn with PyObjC. The link app is Swift.

- `switchboard/__main__.py`, `switchboard/term.py`: the commands, and the accounts as the terminal shows them
- `switchboard/accounts/`: the account engine. The rest of Switchboard uses it only through `switchboard/accounts/api.py`.
- `switchboard/server.py`, `switchboard/web/`: the page and the loop that keeps it current
- `switchboard/menubar.py`, `switchboard/menubar_wrap.py`: the menu bar item, which reads the page, and its app
- `switchboard/statusline.py`, `switchboard/notify_hooks.py`, `switchboard/models.py`: what goes into Claude Code's settings
- `switchboard/memorypage.py`, `switchboard/packs/`: the chat search, house rules, the code map, claude-mem, and the installs of claude-mem and codegraph (`python3 -m switchboard.packs.install plan claude-mem` shows a plan and changes nothing)
- `switchboard/keys.py`, `switchboard/gateway.py`, `switchboard/lanes.py`: keys in the Keychain, the gateway, its switch
- `switchboard/browser.py`, `switchboard/artifacts.py`: browser profiles, and who owns each artifact
- `router/`: Switchboard Links, the link app
- `install.sh`, `uninstall.sh`, `packaging/`: install and remove, and what Homebrew runs
- `tests/`: the tests. [tests/README.md](tests/README.md) says how they are laid out.

## Run it

The tests need no accounts, no network and no Keychain: everything outside is a stand-in, and every folder a throwaway one.

```sh
SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests
python3 -m switchboard demo                  # the page with made-up accounts
sh tests/install-check.sh                    # the installer and uninstaller, in throwaway homes
sh install.sh                                # install from this clone
sh router/build.sh --no-register /tmp/apps   # Switchboard Links; macOS is not told
ruff check                                   # lint
```

If `SWITCHBOARD_HOME` is not set, the tests use a throwaway folder; if it points at your real `~/.switchboard`, they refuse to run. Run the tests on the oldest Python Switchboard supports (Apple's `/usr/bin/python3` is 3.9) and on a new one. The whole suite is heavy, so on macOS run it at background priority: put `taskpolicy -b` in front. `install-check.sh` downloads Python and packages for some of its cases, so it needs the internet.

## Code

- Standard library only in `switchboard/`, apart from PyObjC in the menu bar item. A new dependency needs an issue first.
- Everything must run on Python 3.9.
- A comment says why, not what: an invariant, a unit, or what macOS, the Keychain or Claude Code really does. When a comment records how something outside behaves, give the version it was checked against.
- No real account, address, path or id in the code or the tests. Made-up people use `.example` addresses.

## Commits

One change per commit. The subject is one line, at most 72 characters, in the imperative, with a capital first letter and no full stop. It names what changes for the person using Switchboard, not the function that changed:

```
Keep a renewed login when a switch is cut off part-way
Show an account's limits as not known when its reading is over a day old
Fix the installer stopping on a PATH with a trailing colon
```

Add a body when the reason is not obvious from the subject: what was wrong, and why this is the fix.

## Pull requests

Say what changed and why, and how you tested it. The tests pass on both Pythons, and install-check passes when the change touches the installer, the uninstaller or the first run.

You may use an AI coding agent to write a change. You answer for it as for any other: read the diff, run the tests yourself, and say in the pull request what you checked.
