# Update and remove

| To | With Homebrew | Without |
|---|---|---|
| Update | `brew upgrade switchboard` | `switchboard update` |
| Remove it, keep the accounts, settings and history | `brew uninstall switchboard` | `sh ~/.switchboard/app/uninstall.sh` |
| Remove it all, with the accounts' logins | `brew uninstall --zap switchboard` | `sh ~/.switchboard/app/uninstall.sh --all` |

`switchboard about` names the remove line for the way this copy was installed. Each is a change, and removing is the person's decision: say the exact line and what it removes, and run it only when they ask.

## Update

- `switchboard update` installs the newest version the way it was installed, with the same choices; the accounts and settings stay. With Homebrew it runs `brew upgrade switchboard`. It never opens the page or runs the first run again.
- `switchboard update --check` only looks whether a newer version is out, and changes nothing (exit 10 when one is).
- `switchboard version` says which version this is. Once the page runs the new version, it brings this skill up to date within minutes.

## Remove

Removing takes out the program, Switchboard.app, the link app, the background jobs and the command, and puts back what Switchboard changed in Claude Code: the status line, the hooks, Models by the job, Sonnet for helpers, house rules, the code map and claude-mem's settings. It takes out this skill too, Switchboard's own copy only (the folder with its marker). It never touches the login Claude Code is using. If Switchboard Links is the default browser, macOS asks which browser gets it back.

- A plain remove keeps `~/.switchboard` (settings, accounts, History), the accounts' logins in the Keychain and `~/.switchboard-notes`, so a reinstall picks them up. Its last line is the command that removes the rest: `sh ~/.switchboard/uninstall.sh --all`.
- `--all` also removes the accounts (their logins leave the Keychain), the gateway's keys, everything in `~/.switchboard` and `~/.switchboard-notes`.

To take out one piece and keep the rest: `switchboard statusline off`, `switchboard notify off`, `switchboard hooks off`, `switchboard links off`, `switchboard skill off`, `switchboard auto off`, `switchboard menubar off`.

When the uninstaller says something could not be put back, it names it and keeps the program, so it can run again once that is fixed (a settings file that can't be read, an edited block in CLAUDE.md, a locked Keychain).
