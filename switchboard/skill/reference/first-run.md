# Installing, and the first run

## Install

For macOS, with Claude Code. No administrator is needed.

- With Homebrew: the README's install lines. Homebrew checks the release file's SHA-256 before it puts anything on the Mac.
- Without Homebrew: the README's one-line install. This line, and `switchboard update` on an install without Homebrew, take the newest code on `main`, with no checksum.
- To look first with made-up accounts, changing nothing: `switchboard demo` (the page on port 7798), which the person runs in their own Terminal window.

The installer puts the program in `~/.switchboard`, the command in `~/.local/bin` (with Homebrew, in Homebrew's bin folder) and its short name `sb` beside it unless another program already has that name, Switchboard.app in Applications, the link app Switchboard Links in `~/Applications` when Apple's command line tools are there, and three background jobs that start at login: one for the page, one for the sign-in watch, one for the menu bar item.

Every install ends with a block of facts, each checked as it is printed: where the code came from and whether a checksum was checked, how the apps are signed, that no administrator password was used, what was installed where, what runs, whether the program's files are unchanged, where sign-ins are kept, what leaves the Mac, how to remove it. `switchboard about` prints the same block again at any time, and `switchboard about --json` prints it as JSON.

## The one thing to type

At a terminal, the installer starts the first run right after that block. Otherwise its last line names the one command to type next: `switchboard` (or `sb`). `switchboard` alone, while no account is kept, runs the whole first run. `switchboard setup` runs it again at any time. `switchboard setup --yes` asks nothing.

## What the first run does

It goes in numbered steps ("1 of 4", "2 of 4", ...). A step with nothing to do on this Mac is left out and not counted. Each line says only what was checked just before it was printed.

1. Your account: keeps the account Claude Code is signed in to, without asking. When Claude Code is not installed or not signed in, it says what to do first, and then to run `switchboard` again.
2. Your other accounts: offers to add each other Claude account. Enter opens the browser, where the person signs in on Anthropic's own page in a new browser profile and approves it there; s skips. Nothing is signed out, and the account Claude Code is on is not touched. When the browser signs in as an account already kept, it says so and offers to try again in a new browser profile. When Codex is on the Mac, it offers to add Codex to the order; Switchboard never reads or keeps Codex's sign-in. When the page is not running and does not start, it says to run `switchboard doctor`, and to add accounts later with `switchboard add`.
3. See it move: when there is another account, it offers one real switch: Claude Code moves to that account now, open chats follow within about 30 seconds, and it prints the `switchboard switch` command that moves it back. s skips, and nothing moves.
4. Links that open in the right account: when the link app is there, it explains what macOS is about to ask, then macOS asks once whether Switchboard Links may be the default web browser. Only the person's yes changes it. `switchboard links off` undoes it; `switchboard links on` asks again later.
5. Where to find it: the status line and notifications in Claude Code, the page (`switchboard page`, with its address), whether auto-switch is on and at which lines, and what was changed in Claude Code's settings, each with its undo (`switchboard statusline off`, `switchboard notify off`, `switchboard hooks off`).

It ends with how many accounts are kept, and that `switchboard` shows them.

Along the way it turns on Switchboard's status line in Claude Code (never over a status line of the person's own: it leaves that one and says so), notifications, and the session hooks, beside any hooks of their own. Once an account is kept, it puts this skill in Claude Code's skills folder, and in `~/.agents/skills` when Codex is on the Mac, never over a skill of the same name that is not Switchboard's. With a second account, auto-switch turns on by itself.

Without a terminal (an agent's shell, a pipe) it asks nothing: it keeps the account Claude Code is on, turns on notifications and the status line, and says to add the other accounts at a terminal with `switchboard setup`. Signing in is the person's own: give them `switchboard setup` or `switchboard add` to run in their own Terminal window.

## After the first run

- Another account: `switchboard add` (it signs in through the browser, one account at a time, and asks whether there is another), `switchboard add EMAIL` for one account by its email, or Add account on the page or in the menu bar item's menu. A login the person makes with `/login` in Claude Code is kept by itself too.
- Once a second account is in, auto-switch turns on by itself. `switchboard auto off` turns it off, and it stays off.
- To see the accounts: `switchboard` in a terminal, the page (`switchboard page`), or the menu bar item.

When something Switchboard sets up by itself is turned on or off by the person, it never changes it by itself again: that holds for auto-switch, notifications, the status line, the session hooks, links and this skill.
