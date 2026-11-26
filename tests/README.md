# Switchboard's tests

Every test runs on made-up accounts in throwaway folders, and none of them touches a real login.

## Three kinds of tests

- **Unit tests** (most `test_*.py` files) use stand-ins for the outside world: a fake `security` tool and Keychain,
  made-up servers on 127.0.0.1 in place of Anthropic's endpoints, and Claude Code's files, hook inputs and a stand-in
  `claude` written into a throwaway home.
- **Page tests** drive the real page (`switchboard/web/index.html`) in headless Chrome, with a throwaway profile and
  no window, on the demo's made-up accounts. They are skipped when Google Chrome is not installed.
- **`install-check.sh`** runs the real installer and uninstaller in throwaway homes: `sh tests/install-check.sh`.

## Names

A test file is named `test_<area>_<topic>.py`. The areas:

- `accounts_`: the account engine in `switchboard/accounts/` (Keychain, vault, switch, identity, tokens, run-as)
- `app_`: how the app's parts work together (`engine.py`, `keep.py`, setup and the first run)
- `autoswitch_`: automatic switching: the loop, its policy, usage polling, sessions
- `artifacts_`, `previews_`, `links_`: published pages and their owners, their pictures, opening a link as its owner
- `chats_`, `resume_`, `run_`, `hooks_`: chat files, reopening a chat, running as an account, session hooks
- `cli_`, `term_`, `doctor_`: the `switchboard` command, what it prints, its health check
- `server_`, `page_`: the page's server, and the page itself in headless Chrome
- `menubar_`, `notify_`, `statusline_`: the menu bar, notifications, Claude Code's status line
- `readings_`, `usage_`, `insights_`: limit readings and their age, token counts from the history, the week's summary
- `packs_`, `models_`, `gateway_`, `keys_`: the packs, the Models setting, the API key gateway and its keys
- `settings_`, `install_`, `uninstall_`: edits to Claude Code's settings, the installer's parts, the uninstaller
- `core_`, `docs_`: `core.py`, the README and docs

Shared stand-ins are in `fakes_<area>.py`. `hand_count.py` counts the usage history a second, naive way, and
`cut_off_switch.py` is a switch that a test kills part way. Made-up data is in `fixtures/`;
`fixtures/packs/memory/make_history.py` writes its made-up chats out as a Claude Code history for the memory tests.

## Isolation

Every test file imports `_isolate.py` first. It points `SWITCHBOARD_HOME`, `HOME` and `CLAUDE_CONFIG_DIR` at a
throwaway folder (and stops if `switchboard` was imported before that), and refuses any write outside the temp folders
and any command no test may run, such as `security`, `launchctl` or `claude`. `test_zz_real_folders.py` sorts last, so
it runs last: it fails if anything in the run wrote outside the throwaway folders or changed the real
`~/.claude/settings.json` or `~/.switchboard`.

## Running

    SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests
    SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests -p 'test_plan.py'
