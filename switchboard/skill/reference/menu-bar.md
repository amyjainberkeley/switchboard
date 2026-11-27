# The menu bar

## What it is

Switchboard's item next to the clock is its S. A dot on the S says when something needs a look: orange when the account Claude Code is on is at or over its switch line; red when a sign-in has ended, no account has room, or the page is not running. The S is dimmed while the item starts and while its numbers are not current. Pointing at it (and VoiceOver) gives the account in use by its whole email, with its weekly use. It reads the page every 15 seconds, every 2 seconds while its menu is open, and asks Anthropic nothing.

Its menu has one row per account: the whole email, one word (In use, Next, Use, or Sign in again when its sign-in has ended), and under it the account's bars, 5-hour and weekly, with their percent. When a sign-in ends within the week, the row says by when, with a Sign in again line under it. Then Sign in to all when an account needs it, Codex's room once Codex is added, Auto-switch with its lines, Add account (the page's Add form) and Open Switchboard. The menu has no Quit. While the page is not running, it says "Switchboard isn't running" and offers Start.

Its app is Switchboard.app, in /Applications (or ~/Applications when /Applications needs a password). A background job starts the item at login.

## Turning it off and on

- `switchboard menubar off`: the item stops and stays off, also after a login or an update, until `switchboard menubar on`.
- `switchboard menubar on`: the item starts now and at every login.
- Anything else that stops it is undone: its background job starts it again within about 20 seconds, and the page starts the job when that did not.
- `switchboard doctor` says whether the item is on, off by the person's choice, or installed and not running, and where its log is.

## The item disappeared

- Turned off with `switchboard menubar off`: `switchboard menubar on` brings it back. Opening Switchboard.app then opens the page only.
- Hidden: on a MacBook with a notch, a full menu bar puts the items that do not fit behind the notch. When that happens to the S, one notification says so, once per login: hold Command and drag the S toward the clock. A menu bar manager can hide it too. On macOS 26 a manager can keep it in view only when Switchboard.app is in /Applications; `switchboard menubar --applications` puts the app back in Applications.
- Not running: the person opens Switchboard.app (type "Switchboard" in Spotlight), or runs `switchboard menubar on`. Plain `switchboard menubar` the person runs only in their own Terminal window: in an agent's shell the item would run tied to that shell, and the command would not return.
- The status line in Claude Code, the page and `switchboard` in the terminal keep working without the item: the item only reads the page.

## The install has no item

An install made with `--no-menu-app` has none. `switchboard update --menu-app` adds it; `switchboard update --no-menu-app` takes it out.
