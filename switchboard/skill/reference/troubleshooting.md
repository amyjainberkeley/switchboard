# Troubleshooting

Start with `switchboard doctor`: it checks every part and says what to do about anything wrong. `switchboard doctor --fix` runs now the repairs the page makes by itself every 10 minutes (a change: run it when the person asks).

## How do I add another account

See `accounts.md`. The person runs `switchboard add` in their own Terminal window (it signs in through the browser), or presses Add account on the page or in the menu bar item's menu.

## What was installed, and where

`switchboard about` prints the block of checked facts the install ended with: what was installed where, how it is signed, what runs, what leaves the Mac, how to remove it. `switchboard about --json` prints the same as JSON.

## `switchboard: command not found`

The command is in `~/.local/bin`, which is not on the PATH yet. For zsh, the person adds it with `echo 'export PATH="$PATH:$HOME/.local/bin"' >> ~/.zshrc` (a change to their shell file: run it only when the person asks), then opens a new terminal window. Until then, `~/.switchboard/bin/switchboard` works.

## The page won't open

See `the-page.md`. `switchboard doctor` says whether its background job runs or another program holds the port.

## The menu bar item is gone

See `menu-bar.md`. The menu has no Quit, so the item stops only by `switchboard menubar off` or by something else: `switchboard doctor` says which. Turned off: `switchboard menubar on`. Behind the notch of a full menu bar: hold Command and drag the S toward the clock. Not running: the person opens Switchboard.app, or `switchboard menubar on`. The status line in Claude Code and the page work without it.

## Auto-switch didn't switch

`switchboard auto why` says why: auto-switch off, the page not running, no other account with room, the disk almost full, or a move that is waiting (for Claude Code to be idle for 5 minutes, 30 minutes after an artifact, 45 minutes after the last switch, the battery, a busy Mac, among others). See `auto-switch.md`.

## A session is still on the old account

A session open during a switch moves within about 30 seconds. If it doesn't, quit it and start it again; in VS Code, start a new chat. The Claude desktop app has a sign-in of its own, which a switch doesn't change.

## An artifact says Page not found, or opens as the wrong account

See `links-and-artifacts.md`: `switchboard open LINK` opens it as the account that owns it.

## An account asks for a sign-in again

See `sign-ins.md`. The person presses Sign in on its row, or Sign in to all, or runs `switchboard add EMAIL` in their own Terminal window.

## The numbers look old

The accounts are read only while the page's background job runs. `switchboard doctor` has a "Reading the accounts" line. In VS Code, Claude Code shows no status line, so the account in use is read every few minutes like the others. See `limits.md`.

## No notifications

macOS shows them as Script Editor's: allow Script Editor in System Settings > Notifications. `switchboard notify` says whether the hooks are in and can run.

## macOS asks for permission

Switchboard keeps logins in the Keychain, adds three background jobs (the page, the sign-in watch and the menu bar item), sends notifications, and reads Chrome's tab titles to check which profile is which account. Each is asked by macOS; Chrome only after the person asks Switchboard to check links.

## Another program is also switching Claude Code's login

Two programs that both write Claude Code's login undo each other's switches. `switchboard doctor` names the other program's login item. Turn that program off, then Switchboard carries on.

## A command says the page serves another data folder

A command run with its own `SWITCHBOARD_HOME`, or against the demo, changes nothing and says the one thing to do in one line.

## This skill

`switchboard skill` says where it is in, one line per place. A folder of the same name that is not Switchboard's is left as it is. When Claude Code's folder or its `skills` folder is a symbolic link, the skill does not go in by itself: `switchboard skill on` puts it in. Any other agent can read it whole from `switchboard skill show`.

## Still stuck

Run `switchboard doctor` and open an issue on the project's GitHub page (`switchboard about` prints its address, and the README's Help section links it) with what doctor printed. It shows no login or key, but it does show folder paths and the accounts' email addresses, so the person should read it before sending it.
