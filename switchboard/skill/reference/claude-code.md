# Claude Code

A switch moves Claude Code: it writes the other account's login into Claude Code's Keychain item (`Claude Code-credentials`) and the account's name into `~/.claude.json`. Chats, settings and projects stay as they are.

## What follows a switch

- A new Claude Code session, in a terminal or VS Code, starts on the new account.
- A session already open moves within about 30 seconds. If one stays on the old account, restart it; in VS Code, start a new chat.
- An artifact or doc a session made stays with the account that made it (see `links-and-artifacts.md`).
- A claude.ai connector (Google Drive, Linear, Slack…) belongs to the account that added it on claude.ai; calls made just after a switch can fail.
- The conversation stays. The model's private reasoning from earlier in the chat is used only in the account that produced it, so after a switch it may be dropped, with no error.
- The Claude desktop app and claude.ai in the browser keep their own sign-ins.

## What Switchboard puts in Claude Code's settings

In `~/.claude/settings.json` (in `CLAUDE_CONFIG_DIR` when that is set). It keeps a copy of the file from before its last change (`settings.json.switchboard-backup`), and off takes out exactly what went in.

- The status line: a line at the bottom of each window, such as `● ana@work.example on Opus · 51% left · refills Sun 2 PM · next: ana@home.example`. It also hands Switchboard Claude Code's own numbers for the account in use. On by itself at the first run, never over a status line of the person's own. `switchboard statusline on`, `on --replace`, `switchboard statusline off`.
- Notifications: three hooks, for "Claude Code needs you" and, when it is on, "Claude Code is done" (after a turn of a minute or more; off on a new install, `switchboard notify done on`). macOS shows them as Script Editor's. `switchboard notify on`, `switchboard notify off`.
- Session hooks: they tell an open chat what a switch means for it, which account owns an artifact or doc it can't open, and what is known when a connector stops answering after a switch. They never tell a chat to sign in. `switchboard hooks on`, `switchboard hooks off`.
- Sonnet for helpers, off until turned on: helpers that name no model run on Sonnet instead of the chat's model. `switchboard models sonnet on`, `switchboard models sonnet off`.
- Models by the job, off until turned on: four helper files in Claude Code's agents folder, each on the model that fits its job, and a short note in its rules folder; no setting. `switchboard models on`, `switchboard models off`.
- This skill: a folder in Claude Code's skills folder (`skills/switchboard`), with a small marker file that says it is Switchboard's and from which version. `switchboard skill on`, `switchboard skill off`.

When `settings.json` is a symbolic link (kept with dotfiles), nothing goes in by itself; the `on` commands still put each in. The same holds for this skill when Claude Code's folder or its `skills` folder is a link. A piece the person turned on or off never changes by itself again.

## Running as another account

- `switchboard run NAME -- -p "…"` runs one Claude Code command as that account, in this terminal only. Every other session keeps the login it has, and auto-switch never moves Claude Code onto an account a run is using. The run has the person's chats, memory, instructions, settings, hooks and skills; `--bare` starts it with none of that, and `--no-history` keeps its chat apart.
- `switchboard folder NAME`: this folder, and every folder inside it, runs as that account with `switchboard run`.
- `switchboard resume CHAT` opens a chat again as the account it started under; `switchboard chats` lists every chat with its id.

## The status line is missing

`switchboard statusline` says whether it is on. Claude Code's VS Code panel runs no status line. A status line of the person's own stays; `switchboard statusline on --replace` puts Switchboard's in its place, and `switchboard statusline off` puts theirs back exactly.
