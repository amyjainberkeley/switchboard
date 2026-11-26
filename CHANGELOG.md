# Changelog

Every change people will notice, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.2.0 (2026-11-26)

The first published version.

### Added

**Accounts and switching**

- `switchboard`: every account's limits in the terminal, and a switch with one key. `switchboard switch`, `switchboard remove` and `switchboard alias` take a number, an email or a name.
- `switchboard status --json`: every account and its limits as one JSON document, for scripts and status bars. It only reads, and its `schema` number goes up only when a field is removed or renamed. `switchboard owner LINK --json` does the same for an artifact's owner.
- The account engine. A switch saves the login in use first, writes the other, reads both back, and undoes itself on a mismatch. One cut off part-way is finished or undone at the next start.
- Signing in with `/login` in Claude Code keeps the login by itself.
- Each account says by when its sign-in ends, a week ahead (Anthropic ends each sign-in by a set date that renewing does not move, and it can end in the three days before), on the page, in the menu bar, in `switchboard` and in `switchboard doctor`, with the way to sign in again and a notification 3 days and 1 day before the earliest it can end.
- Sign in to all: one press signs in again to every account that needs it, one after another, each approved by you in the tab that opens; the account Claude Code is on too, which moves to its new sign-in at once. On the page, in the menu bar, in ⌘K and as `switchboard signin`.
- `switchboard run`: one Claude Code command as another account, without switching. It has your chats, memory, settings, hooks and skills; `--bare` runs it with none of them.
- `switchboard resume CHAT`: a chat opened again as the account it started under, so its artifacts, Claude Docs and connectors answer again. `--fork` continues it in a copy.
- When Claude Code loses its sign-in by itself (many open chats saving its Keychain item at once), Switchboard puts the same account's saved login back within seconds and says so, and stops after four times in an hour; it never undoes a sign-out you chose. When it can't put it back, a notification says so after 5 minutes.
- Switchboard no longer renews or calls "needs sign-in" the account you are working on when Claude Code's renewal moves its sign-in end date a little.

**Auto-switch**

- On by itself once a second account is in. `switchboard auto off` turns it off, and it stays off.
- It moves at once only when it must, such as at a limit. Any other move waits for Claude Code to be idle, for a real gain, and for 45 minutes since the last switch.
- Three orders: Refills first, Most room, and Stay, then refills first. One or more models' own weeks, such as Fable's, can count too.
- An account can be used last (auto-switch goes there only when no other account has room) or kept out of auto-switch (it never moves Claude Code there, or away from there): on the page (Settings, Each account), or `switchboard auto last NAME`, `out NAME` and `in NAME`.
- A switch that can wait waits while a chat is using a claude.ai connector.
- On Apple's own Python 3.9 as on newer ones, the page and the hooks read the loop's heartbeat and its recent switches on the same clock the loop wrote them by, so a loop that started after the page is never taken for stuck.
- When Claude Code can't work on the account in use and no account it could move to can be read just now, it moves by what was last known of them, inside your rules, and says it moved on older numbers.
- Under 50 MB of free disk no switch begins; the page says so in one line, History once.

**Artifacts and chats**

- Artifacts open as the account that made them: `switchboard open`, `switchboard owner`, one click on the page, and Switchboard Links for links clicked anywhere, even when the page is down.
- Session hooks: a Claude Code session that can't find an artifact or doc is told which account owns it and the command that opens it. After a switch, a chat is told which account it is on and was on.
- A page made a moment ago opens as its owner too: the session hooks note each publish as it happens.
- Previews: a picture of each of your artifacts, made on this Mac in headless Chrome and kept in `~/.switchboard/previews`.
- `switchboard chats`, and Chats on the page: the chats on each account, where each started, and the pages and connectors each holds that belong to another account. Reopen at home opens a chat as the account it started under.
- A search of past chats on this Mac.

**The page, the menu bar and Claude Code**

- The page: Search (⌘K), Accounts, Chats, Artifacts, Activity and Settings, each a view with its own address. Every setting shows its command.
- Every surface names an account by its whole email. A short name (`switchboard alias`) is only something to type, in ⌘K or in a command.
- Accounts is one list: each account's email with its plan under it, then its limits one under another (5-hour, only when there is a 5-hour number to show, and weekly), each with a mark at its switch line and when it refills, and which account is in use and which is next. A model's own week, such as Fable's, has no line: the weekly line's hover says it, and when auto-switch counts it and it is at or over its line, a short red note at the end of the weekly line. The menu bar and `switchboard` show the same.
- Your week: the last 7 days of use by hour at every window width, split by your chat turns, helpers and workflows, with limits and switches on the same axis, and up to three highlights made only from those numbers. Point at an hour to read what it went to: its projects, models and kind of work, how many agents, workflows and tool calls ran in it, how its tokens divide, and the account Claude Code was on. It is kept on disk, so a restart shows it at once.
- A menu bar item: two bars for the account in use, and every account in its menu. Switchboard.app goes in Applications.
- A status line in Claude Code with live numbers for the account in use, in a terminal.
- Every account is read whether auto-switch is on or off, with one account too: the account in use every 5 minutes, about every 3 while the page is open, the others every 5 to 15, and every account at once when you press Read again, or open the page with the account in use read over a minute ago or another over 5 minutes ago. The page says "checking…" until the read is back. Near a switch line, reads go every 90 seconds, then every 3 minutes for as long as it lasts, never a stop. One pace keeps every account to about 20 reads an hour (never more than 24), on a busy Mac as on a quiet one.
- Each account shows when it was last checked, "checked just now" under a minute. A number that can't be vouched for shows as not known. A new reading shows on the page within seconds, and in the menu bar and the terminal at their next look.
- Notifications for switches and limits, and for Claude Code's "done" and "needs you".
- Switchboard stays up: the page watches itself and is started again when it hangs or stops, the menu bar item and the page start each other again, a loop that decides nothing is replaced, and with another program on the page's port auto-switch still runs. "Auto-switch last decided 14 minutes ago." on the page, in the menu bar, in `switchboard` and in `switchboard doctor` when it has decided nothing for minutes, with the one thing to do if it stays.
- One notification, with the one thing to do, when Switchboard is not doing its job for 5 minutes: auto-switch not working, the account in use unable to work with no switch possible, a Keychain dialog waiting for you, every account's numbers over an hour old while the Mac has a network.
- Switchboard repairs itself: a damaged list of accounts or settings file comes back from its last good copy, Switchboard's status line and hooks are put back once when taken out of Claude Code's settings, and the page's check runs every 10 minutes. `switchboard doctor --fix` runs those repairs now; plain `switchboard doctor` repairs nothing.
- A command that changes something refuses, in one line, a page that serves another data folder, and says so when the page waits for its port.
- History (`switchboard log`), and the week's work by project, by who did it and by model (`switchboard usage`).
- Claude in Chrome: the page shows which accounts' profiles have the extension. While auto-switch is on, a session that uses it can move Claude Code to the extension's account when that account has room.

**Switches for Claude Code**, each with an exact undo

- Sonnet for helpers (`switchboard models`), house rules, a code map and claude-mem.
- API keys and a local gateway for when every account is out.

**Install**

- Homebrew: `brew install amyjainberkeley/apps/switchboard`. `brew upgrade` and `switchboard update` update it, `brew uninstall` removes it and keeps your accounts, and `brew uninstall --zap` removes those too.
- A one-line install that needs no administrator and no Apple command line tools. The uninstaller puts back what Switchboard changed.
- `sb`, the same command as `switchboard`, shorter. It is added when no other program on the Mac has the name.
- `switchboard doctor` checks every part of the install. `switchboard version` names the build too, as in `switchboard 0.2.0 (1a2b3c4)`.
- Docs: how it works, troubleshooting, FAQ, every command, and credits.

**Other assistants**

- `switchboard continue`: carry on in another assistant. The chat in this folder goes on in Codex, OpenAI's app, beside Claude Code, or back. A note of it is made on this Mac from the chat's own file, with no model call and anything that looks like a secret taken out as well as Switchboard can; after your yes for that folder, the other app starts on it, at a terminal, in the folder where the chat's work last ran (a folder outside the chat's own needs its own yes), on its own sign-in, sandbox and approvals.
- `switchboard back`: a note of what was done in the other assistant goes to the chat you left, with your next message there when the session hooks are on, or as a line to paste.
- `switchboard providers`: every provider on this Mac, with the version Switchboard runs, its limits and whether it has room. Codex's come from its own files; `--ask` asks its helper after your one yes.
- The other assistants you added are on the page, in `switchboard`, in the menu bar and in `switchboard doctor`, beside your accounts: whether each has room, its plan and its limits. A notification says when the first in your order is out and another has room.
- `switchboard add codex`, `switchboard order`: Codex joins your order, Claude first unless you change it. Codex keeps its own sign-in, and Switchboard never reads it.
- `switchboard hand codex -- JOB`, in a terminal: one read-only job, its answer printed and kept 7 days.
- `switchboard allow` and `switchboard stay`: your yes for a folder, given once at a terminal; a folder that stays on one provider.

### Not built

- Moving between several ChatGPT accounts, a second Codex sign-in, or a Codex folder per account.
- Anything that opens, copies, renews or writes Codex's login, or uses OpenAI's sign-in.
- A move, a job or an ask started without a person at a terminal.
- A queue that hands new jobs to an assistant because it has room.
- A router, gateway or proxy that turns one company's requests into another's.
- A Claude job through `claude -p` or the Agent SDK.
- Keeping a note for anything but the move, and telemetry.
- A feature or file named after another company's product.
- Any flag that loosens an app's own sandbox or approvals.
