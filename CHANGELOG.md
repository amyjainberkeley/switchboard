# Changelog

Every change people will notice, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 1.0.0 (2026-11-27)

Codex joins your Claude accounts in one list, Claude Code's sign-in is watched and kept, and routing can set the model each kind of work starts on.

### Added

**Other assistants in the one list**

- Pressing another assistant's number in `switchboard` (or typing `switchboard 3` for row 3) carries this folder's newest chat on there: a note of it is made and that app starts on it in this terminal. Adding the assistant says once what pressing its row sends, to whom and under whose terms; when that was never said at a terminal, as for one added on an earlier version, the first press at a terminal in a folder with no yes of its own says it and asks once. `--why` says what was done along the way.
- Continue here, in the menu bar and on the page, opens that app with the note in its message box, and you send it there; without the app, it gives the line to run in a terminal.
- `switchboard remove codex` takes Codex off your order and the list, and `switchboard add codex` adds it again. Bare `switchboard add` asks before it adds a signed-in Codex, at a terminal only.

**Keeping Claude Code signed in**

- Keep Claude Code signed in (`switchboard auto signed-in`, on until you turn it off): when the sign-in in use is lost and can't be put back, or ten minutes before it stops working, Claude Code moves to another signed-in account, also with auto-switch off.
- The sign-in watch, a third background job, notices within a second when Claude Code's sign-in changes and has Switchboard look at once; the page and the watch each start the other again when it stops. `switchboard about` names it among what runs.
- Claude Code's sign-in is also kept in its plain login file (`.credentials.json` in Claude Code's folder, which only you can read), as an exact copy of its Keychain item, so a chat or a status check whose Keychain read gives nothing on a busy Mac finds the sign-in there. The copy follows each renewal, switch and put-back, is never made from a blank or refused login, and goes when you sign out of Claude Code, at uninstall, or when `auto_policy.keep_signin_file` is false; then only a copy for about 35 seconds after a put-back is kept, as before. While the copy holds the login, a lost sign-in is put back only after 10 seconds, so a sign-out you choose is not taken for a lost sign-in. A file Claude Code wrote itself is never written over.
- When the Keychain gives no answer during an uninstall, the program and its data folder stay, and one line gives the command that runs the uninstall again.

**Routing**

- `switchboard routing on` sets the model and the effort a helper or a workflow agent starts on in Claude Code, as your route for that kind of work says, through a plugin Claude Code loads (Claude Code 2.1.287 or newer). With Codex in your list, it also puts two hooks in Codex's folder and a helper file for each kind of work your routes send to a Codex model; Codex asks you once to trust the hooks. The Codex part follows Codex's published formats and has not yet been seen working in a running Codex. With no routes and no defaults nothing changes. `switchboard routing off` takes back everything turning it on wrote, and `switchboard routing trial on` records what would happen and changes nothing.
- `switchboard routing` shows whether routing is on, every switch, your defaults, your routes and the last five decisions, each with its reason. Once routing is on, a line under the list in `switchboard` says so.
- `switchboard routing cache` counts how often helpers stored their whole context again after a wait, from the records Claude Code keeps on this Mac; `switchboard routing cache on` sets Claude Code's hour cache for helpers, and `off` puts back what was there.
- The first run turns routing on in a step of its own and says what it changed.

**The front door**

- `switchboard do TASK`, in a terminal: type a task once. Switchboard works out its kind from its first sentence by a fixed table of words (build, fix, review, plan, find, write), picks the provider and model your routes name for that kind, says why and which providers it passed, asks the same yes for the folder as `continue`, and starts that provider's own app on it in this folder. Your words go into one private file; the app's first message names only the file.
- `switchboard route TASK`: the same pick and why, starting nothing.
- `switchboard routes`: your routes kind by kind, with `set`, `reset` and `preset`. A fresh install has none, and the one preset, `by-the-job`, is off until you turn it on.

**Models, the week and artifacts**

- Models by the job (`switchboard models on`, off until you turn it on): four helpers in Claude Code, each on the model that fits its job (lookup on Haiku; reader, reviewer and verifier on Sonnet), and one short note that says which is for what. It changes no setting, and `switchboard models off` takes out exactly the files it added.
- Your week on the page shows up to three takeaways, each on a number the week already shows, and says what it means for you; where a documented change exists, one press makes it and Undo takes it back.
- What stopped your work, on the page under your accounts: how many chats, helpers and workflow agents stopped early in the last 7 days, each cause in the last 14 days with the step for it, and the workflow runs that lost agents. It is read from files on this Mac and never keeps what a message said.
- `switchboard run --roomiest -- ARGS` runs as the account with the most room that auto-switch is not about to leave, and names that account first.
- When a chat's Artifact or Claude Docs call says "not found" and Switchboard knows the item is another account's (it never guesses), Claude Code moves to that account while auto-switch is on: at most once in 10 minutes for each owner, and only when every other rule for a move allows it. The chat is told whether Claude Code is moving.

### Changed

- Another assistant you added is a numbered row in the same list as your Claude accounts, in `switchboard`, on the page and in the menu bar, with its plan and its limits, in place of a group of its own; an assistant with no row gets one line that says why. `switchboard doctor` says whether each has room, its plan and its limits.
- When every Claude account you can use is at your switch lines and another assistant has room, one notification says so and which number to press, and so does the S in the menu bar; when another assistant is first in your order, the notification says when that one is out and which has room.
- `switchboard models on` and `off` now turn Models by the job on and off; Sonnet for helpers is `switchboard models sonnet on` and `off`.
- Only an account's own 5-hour and weekly limits decide whether it can work. A model's own week decides only whether the account can take that model, and Next and the notification follow the same rule.
- `switchboard` shows under each account only its 5-hour line (or "no 5-hour limit") and its weekly line: no line or note for a model's own week, and no "checked 3 min ago" after its status. Another assistant's row says "had room when last read 2 hours ago" when its numbers are over an hour old.
- A notification when a long turn ends is its own setting now, off on a new install: `switchboard notify done on`.
- The page opens only for you. Open it once in each browser with `switchboard page` or Open Switchboard in the menu bar; its address is `http://127.0.0.1:7799`. `switchboard page --new-key` makes a new key.
- Chat search leaves out text that looks like a key, token or password, and `python3 -m switchboard.packs.memory erase` erases its index. The note each chat gets at its start names no email address.
- Your week weighs each cache read at its own model's price.
- A switch's line in History names the account it left.
- After a Homebrew update, one line says when a newer version is out that Homebrew does not have yet.
- The skill also answers about another assistant's row, Keep Claude Code signed in and its login copy, routing (a page of its own), `switchboard run --roomiest` and `switchboard notify done`.

### Fixed

- `switchboard current` and `switchboard status --json` name no account when Claude Code is on a login that no account here is.
- Removing an account says plainly when its kept token's record could not be removed.
- `switchboard doctor` says an install with no background jobs has none, instead of naming a step that adds nothing.
- A job of yours whose files sit in a folder with "claude" and "switch" in its name is no longer taken for another program that switches Claude Code's login, which kept auto-switch from turning on and ended `switchboard` with a line asking you to turn that job off. A job is judged by its label and the names of its program and files, never by its folders.

### Not built

- Handing work to the other app by its route: inside each app, routing sets models and never starts the other app. You start it yourself, with `switchboard do` or `switchboard hand` at a terminal.
- A move, a job or an ask that starts without you at a terminal, apart from Continue here in the menu bar and on the page, which only opens the other app with the note in its message box: you send it there.

## 0.3.0 (2026-11-27)

The first run moves into the terminal, and every install says what it checked.

### Added

**The first run**

- `switchboard`, typed alone after an install, runs the whole first run in the terminal, in numbered steps: your account, your other accounts, links, and where to find everything. `switchboard setup` runs it again.
- Each other account signs in through your browser, started from the terminal: no `/login`, and nothing is signed out. It says when the browser came back as an account that is already here, and what to do. Codex is offered once when it is on the Mac.
- One real switch during the first run, when two or more accounts are kept and one has room: Enter moves Claude Code, s skips.
- Before macOS asks about the default web browser, the first run says what turning it on changes (only claude.ai links; every other link opens in the browser you use now) and waits for Enter. After, one line says what changed. `switchboard links on` says the same before it asks.
- The first run ends by naming the menu bar item, the status line, notifications and the page, and every change it made in Claude Code's settings with the command that undoes it.
- `switchboard add` at a terminal signs each account in through your browser; `switchboard add EMAIL` signs in as one account.

**The install**

- Every install and update ends with a block of facts, each checked at that moment: where the copy came from and whether a checksum was checked, what macOS says of each app's signature, who it runs as, where it put things, what is running, whether the program's files are as installed, where sign-ins are kept, what leaves the Mac, what the install downloaded and from where, and how to remove it. `switchboard about` prints it again; `--json` too.
- The installer writes `~/.switchboard/app/SHA256SUMS`; `switchboard doctor` says when a program file changed since.
- A release can carry the link app already built for Apple silicon and Intel, signed with a Developer ID and notarized (`sh router/build.sh --release`). The installer checks it (checksum, source, signature, team, stapled ticket, macOS, processor) and puts it in place without Apple's command line tools; on any failed check it says which and builds the app on the Mac as before. This release does not carry one yet.

**The menu bar**

- The menu bar item is Switchboard's S. A dot at its top right is orange at a switch line and red when something needs you.
- `switchboard menubar off` turns the item off across logins and updates; `switchboard menubar on` brings it back.
- The first time, the item asks macOS for the place beside the clock (`"menubar_position": false` leaves it to macOS). When it ends up behind the notch of a MacBook's screen, one notification per login says how to reach it.
- Add account in the menu opens the page's Add form.

**Help**

- A skill lets a coding agent answer questions about Switchboard. It goes in at the first run, into Claude Code's skills folder and, when Codex is on the Mac, `~/.agents/skills`, never over a skill of your own with that name. `switchboard skill` says where it is; `on` and `off` put it in and take it out; `show` prints it for any other agent. The uninstaller takes it out.

### Changed

- The menu is cleaner: each account is its whole email, one word and its bars. When it was checked, reset times and a model's week stay on the page.
- No Quit in the menu. Anything that stops the item, other than `switchboard menubar off`, starts it again.
- `switchboard help` fits on one screen; `switchboard help all` is every command. A command it does not know prints one line and the short help.
- No message sends you to `/login` to add an account.
- The install's last line names the one thing to type next: `switchboard`.
- The page's address is printed as `127.0.0.1`, where it listens.

### Fixed

- `uninstall.sh --all` (and Homebrew's `--zap`) now also takes one-year tokens and API keys out of the Keychain when the program itself cannot run.

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
