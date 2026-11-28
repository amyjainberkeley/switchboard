---
name: switchboard
description: Answers questions about Switchboard, the macOS program that keeps the Claude accounts one person signs in to on one Mac, shows each account's limits and moves Claude Code to an account with room. Use it for any question about Switchboard or its `switchboard` (or `sb`) command - the accounts and the 5-hour and weekly limits it shows, the S in the menu bar and its dot, the item gone from the menu bar, auto-switch and why it moved, which account Claude Code is on, installing it and the first run (`switchboard`, `switchboard setup`), adding or removing an account, a sign-in that ended, a claude.ai link or artifact that opens as the wrong account or says Page not found, the page at 127.0.0.1, Your week, the status line and hooks it adds to Claude Code, Codex, what it keeps and sends, updating it or removing it.
---

# Switchboard

Switchboard keeps the Claude accounts one person signs in to on one Mac, and shows each account's 5-hour and weekly limits. When the account Claude Code is on reaches its switch line, auto-switch moves Claude Code to another account with room. It is a terminal command (`switchboard`, or `sb`), a page in the browser at http://127.0.0.1:7799, a menu bar item and a status line in Claude Code, and what it keeps stays on the Mac.

## How to answer

1. Look first. These commands only read, so run them whenever they help:
   - `switchboard status --json`: every account by its whole email, its limits, the account in use, the next one, and auto-switch's lines and order.
   - `switchboard current`: the whole email of the account Claude Code is on.
   - `switchboard doctor`: every part of the install, one line each, and what to do about anything wrong.
   - `switchboard auto`: whether auto-switch is on, at which lines, in which order, and each account's rule.
   - `switchboard auto why`: what auto-switch would do now, and why.
   - `switchboard log`: what happened, newest at the bottom: each switch and why, limits reached, sign-ins.
   - `switchboard about --json`: what was installed and where, each fact checked now. An older copy has no `about`: then go on without it.
   - `switchboard help all`: every command this copy has.
2. Open the reference file for the subject (the map below) and answer from it and from what the commands printed.
3. Say what you ran and what it showed, then the one thing to do. Name every account by its whole email.

If `switchboard` is not found, see `reference/troubleshooting.md`. To look at the accounts, use `switchboard status --json`, never `switchboard status` or `switchboard` alone: when Claude Code is on a login Switchboard does not hold yet, those two keep that login, and auto-switch may turn on.

## Rules

- Commands that only read may be run freely: the list is at the top of `reference/commands.md`.
- A command that changes something is run ONLY when the person asked for that change: switching accounts, adding or removing one, auto-switch on or off or its lines, ranks and rules, links on or off, the status line, notifications, hooks, the skill, the menu bar item, settings, an update, removing Switchboard. First say the exact command and what it changes, then run it.
- Never switch accounts on your own initiative, and never because a limit is near: Switchboard does that itself, at the line the person set.
- Never set or change a route, a routing switch or a default unless the person asked for it. Routing is explained in `reference/routing.md`; `switchboard routing` shows what is on and why work went where.
- Never start, script or complete a sign-in. Signing in is the person's own, in the browser: give them the command (`switchboard add`, `switchboard add EMAIL`, `switchboard setup`) or the button (Add account, Sign in), and let them run it in their own Terminal window.
- Never read, print, copy or move a login, a token or a key. Never read the Keychain. Never run `switchboard export`, `switchboard import`, `switchboard keys add` or `switchboard add --token`: those are the person's own to run.
- Never edit the files in `~/.switchboard`, or what Switchboard put in Claude Code's settings, by hand. Use the command that owns it.
- `switchboard serve`, `switchboard demo` and `switchboard menubar` run until stopped, and in your shell they do not return: give the command, for the person to run in their own Terminal window.
- Every `switchboard` command you give must be in `reference/commands.md`, with the words written there. Never invent a command, a flag or a setting. A command that is not Switchboard's and changes something, such as a line added to a shell file, is a change too: only when the person asks.
- If the answer is not in these files or in what the commands printed, say so plainly. Point to `switchboard doctor`, and to the project's issues page with what doctor printed (it shows no login or key, but it does show email addresses and folder paths).
- Say what the program does, not what it promises. It renews logins, but it has no way to keep a person signed in for ever: each sign-in ends by a date Anthropic sets. It does not pick models by itself: routing follows only the routes and defaults the person set (`reference/routing.md`).
- Whether using several accounts fits Anthropic's terms: Switchboard makes no claim either way. Give the pages in `reference/what-it-does-not-do.md` and nothing more.

## Reference files

Open only the one a question needs. Each stands on its own.

- `reference/commands.md`: every command, grouped as only reads, changes something, and the person's own to run.
- `reference/first-run.md`: installing, `switchboard about`, the first run that `switchboard` and `switchboard setup` start, step by step.
- `reference/accounts.md`: adding (through the browser), removing, the order of the list, short names, a Team seat, two organizations.
- `reference/auto-switch.md`: the lines, the orders, when a move waits, keeping an account out, seeing why it moved.
- `reference/limits.md`: what each bar is, how fresh the numbers are, a model's own week, a spend limit.
- `reference/sign-ins.md`: renewals, the date a sign-in ends, signing in again, Claude Code losing its sign-in.
- `reference/links-and-artifacts.md`: why an artifact opens only for one account, and what Switchboard does about it.
- `reference/the-page.md`: how to open the page, what each part shows, when it will not open.
- `reference/your-week.md`: Your week under the accounts: re-reading context, storing it, replies, and the highlights.
- `reference/menu-bar.md`: the S in the menu bar and its dot, its menu (no Quit), `switchboard menubar off` and `on`, and what to do when it is gone.
- `reference/claude-code.md`: the status line, notifications, session hooks, what follows a switch, running as another account.
- `reference/codex.md`: what Switchboard does with Codex, and what it does not.
- `reference/routing.md`: routing work by the person's routes, seeing why it went where, setting it, turning it off.
- `reference/privacy.md`: what it keeps, what leaves the Mac, who can see chats, what it changes on the Mac.
- `reference/troubleshooting.md`: one entry per problem, with the command that shows it and the fix.
- `reference/update-and-remove.md`: updating, removing it, and what each takes out.
- `reference/what-it-does-not-do.md`: teams and sharing, models and agents, signing in for you, Anthropic's terms.
