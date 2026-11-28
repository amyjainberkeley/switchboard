# Commands

Every command and flag. `switchboard help` prints the few commands a new person needs, `switchboard help all` prints every command, and `switchboard CMD --help` prints the lines for one command.

`sb` is the same command as `switchboard`, with the same words and exit codes. The installer adds it beside `switchboard` unless another program on your Mac already has the name (a program on your PATH, a file where the link would go, or an alias or function in your shell's startup files); then it adds nothing, says so in one line, and the command is `switchboard`. `switchboard update` adds it the same way to an install from before, and the uninstaller takes out only its own.

A command exits 0 when it worked, 1 when something failed or needs a look, and 2 when it was given words it doesn't know (an unknown command also prints the help). A few have more codes, so a script can tell their outcomes apart: they are in one table under [For scripts](#for-scripts). The commands for other assistants exit 130 when stopped with Ctrl-C.

`switchboard run` exits with Claude Code's own code, or 6 when `--strict` (or `--no-history`) kept it from starting on your own Claude Code folder.

## Accounts

| Command | What it does |
|---|---|
| `switchboard` | Your accounts and their limits, in one numbered list: your Claude accounts, then a row for each account of another provider you added (Codex), with its maker, its plan and the same bars. At a terminal, press a number to move there: a Claude account's number moves Claude Code; another provider's number carries this folder's newest chat on there and starts it here, with one line and no question. Enter, q or Escape stays. With more than 9 rows, type the number and press Enter. A login Claude Code is on that Switchboard doesn't hold yet is kept first. Under the list, a provider you added that has no row says why in one line (`not signed in`, `not on this Mac`), and a provider on this Mac that is not added is offered in one line, with the command that adds it. The list is the same whether the page runs or not; without the page, when reading the other providers' files takes more than eight seconds, it shows your Claude accounts and one line says the others were not read in time. `switchboard --why` also says what a key press on another provider's row did along the way. |
| `switchboard 2` | Move Claude Code to account 2. `switchboard you@example.com` does the same by email. An account whose login stopped working asks for a fresh sign-in instead. Codex's number carries this folder's newest chat on in Codex, and so do `switchboard codex` and the email its row shows; a Claude account with that name or email comes first. |
| `switchboard switch NAME` | Move Claude Code to an account, by its number, email or name. Without NAME, to the next one in the list, passing over one that needs a fresh sign-in, one kept out of auto-switch (naming it still goes there), and one at or past a line, judged by its own lines where it has them, while another is not. Without NAME, when the list marks Codex's row next, to Codex with this folder's newest chat. Numbers brought from another Mac are not used for this: such an account counts as one with no numbers. |
| `switchboard use NAME` | The same as `switchboard switch NAME`. |
| `switchboard current` | The whole email of the account Claude Code is on, in one line, for a prompt or a script. It exits 1 when Claude Code is signed out, or on a login Switchboard doesn't keep, and says which on standard error (the email is still printed when it is known). It reads Claude Code's own config and Switchboard's list of accounts and, while a one-year token is kept, Claude Code's login item, as `status --json` does. It reads nothing else, so it is quick. `--json` prints the same as one JSON document. `whoami` is the same command. |
| `switchboard switch NAME --json` | The same switch, then one JSON document: whether Claude Code moved, from which account, to which, why and when. `switchboard use NAME --json`, `switchboard switch --json` and `switchboard switch --best --json` do the same. When it refuses, the document says why and the exit code is the plain command's own ([for scripts](#for-scripts)). With `--json` it moves Claude Code only: another provider's number is refused (exit 1) and nothing starts, and `switchboard switch --json` alone goes to the next Claude account. |
| `switchboard config` | Every setting, grouped as the page's Settings view groups them, with its value, its default and what it does ([settings from the terminal](#settings-from-the-terminal)). |
| `switchboard status` | Your accounts and their limits, without the question. `--json` prints them as one JSON document for scripts, and keeps no login ([its shape](#the-accounts-as-json)). |
| `switchboard chats` | What is going on in each account: the chats on it now (working, or idle and for how long), where each started, since when it is on its account and how often it moved, and what it holds that belongs to another account (pages it made there, connectors that answered it there). It shows the chats of the last day; `--all` shows the last 7 days, and `--days N` (up to 366) reads and shows N days. `--account NAME` shows one account. `--json` prints it as JSON. It reads the chats itself, so it works when the page is not running. The page's Chats shows the same, each chat under the account it started under. |
| `switchboard add` | Add accounts, one sign-in at a time. At a terminal while the page runs, each one signs in through your browser, as the page's Add account does: Claude Code's own sign-in runs in a throwaway folder and opens Anthropic's page in a new browser profile (in your default browser on a Mac without Chrome, Brave or Edge), so the login in use is not touched, and the engine keeps the new login. It waits on one line (Ctrl-C cancels that sign-in), says when your browser came back as an account that is already here and what to do, then asks whether there is another. Then, at a terminal, it adds Codex when it is signed in and not added yet, says what pressing its row will do, and asks once whether to show its account now (the first run reaches Codex the same way). When the page is not running, it says so and adds through `/login` in Claude Code instead. A login you make with `/login` is kept by itself too, while the page runs. |
| `switchboard add EMAIL` | Sign in as that account in your browser, through the running page: a new account, or one whose sign-in has ended. It opens in the account's own browser profile when it has one. It says when your browser came back as another account. Exit 0 when that account's login is kept. |
| `switchboard signin` | Sign in again to every account that needs it, one after another, as Sign in to all on the page does: each opens in its own browser profile, and you approve each in the tab that opens. It takes every account whose sign-in has ended or ends within the week, and any that ends well before the others, so the next time is again one press. It says where it is until it ends; Ctrl-C stops it. It runs through the page, so the page must be running. |
| `switchboard remove NAME` | Forget an account: its saved login leaves your Keychain. The login Claude Code is using stays as it is. |
| `switchboard alias NAME WORD` | Give an account a short name, such as `work`, to use in place of its number or email. A short name is letters, digits, dots, dashes and underscores. It can't be only digits, start with a dash, be an account's id or another account's short name, or be `off`, the word `switchboard folder off` uses. |
| `switchboard add --as SHORT --at N` | The account this run keeps gets the short name SHORT and place N in the list, in one step; either flag can be left out. When a run keeps several, the first one kept gets them, and it says so. Without a terminal it is the account Claude Code is on, kept already or not. A name that is taken or looks like a number or an email, and a place that does not exist, are refused before any sign-in starts. |
| `switchboard move NAME N` | Put an account at place N in the list, 1 being first; the others keep their order. It prints the list as it is then. A number is a place in the list and changes when the list changes, so a script names an account by its email or short name. |
| `switchboard swap NAME NAME` | Two accounts change places in the list. It prints the list as it is then; the same command again puts them back. |
| `switchboard leftovers` | The logins Switchboard keeps aside, its recovery copies: none of them is an account's login in use. Each has a short id, the account it was kept for, why and when it was kept, where it is (the Keychain or a file), and whether anything still needs it: "safe to clear", or "needed" and why, such as which account it is the only login of until that account signs in again. `--json` prints the list as JSON. It never shows a login. |
| `switchboard leftovers clear ID` | Removes leftover logins by their ids, or every one that is safe to clear with `--all`. It asks first; `--yes` answers. One still needed is never removed: it says which account needs it and what would free it. It exits 1 when one it was asked for stays. |
| `switchboard leftovers clear ID --only-copy` | Removes a leftover login that is the only copy of a login that was in use: no saved account holds it and Claude Code is not on it. `switchboard leftovers` marks it needed, as the only copy of a login that was in use; `--all` never removes one, and naming it without `--only-copy` keeps it and says how to keep its account instead. It asks first, saying the login is then gone for good; `--yes` answers that question. |
| `switchboard run NAME -- ARGS` | Run `claude ARGS` as that account, without switching: `switchboard run work -- -p "hello"`. Every other session keeps the login it has. It has your chats, memory, instructions, settings, hooks and skills, and what it writes there stays when it ends ([what it shares](how-it-works.md#running-as-another-account)). `--bare` before the `--` starts it with none of that. |
| `switchboard run NAME --no-history -- ARGS` | The same, with all of that but your chat history: your instructions, settings, hooks, skills and MCP servers come along, your chats, prompt history, checkpoints, tasks and plans do not, and the chat stays in the run's own folder and goes when it ends. Its first line says the chat is not kept. The `run_history` setting set to `apart` (`switchboard config set run_history apart`) makes this the usual way for every run, and `--history` asks for the shared way for one run. On the account Claude Code is on, `--no-history` starts nothing, since the run would use your own folder and its chat would be kept: it says why and exits 6. ([what an apart run has](how-it-works.md#a-run-that-keeps-its-chat-apart)) |
| `switchboard run NAME --strict -- ARGS` | The same, but never on your own Claude Code folder: when NAME is the account Claude Code is on, it starts nothing, says why in one line, and exits 6. For a script that must know its run was apart. On another account it runs as usual, and the exit code is Claude Code's. |
| `switchboard run --roomiest -- ARGS` | The same, as the account with the most room that auto-switch is not about to leave, in place of NAME: its first line, on standard error, names the account it picked and its room; `switchboard run` may still refuse after it, as it does for a named account. It keeps auto-switch's rules for where to land (never an account kept out, one used last only when no other has room, a lower rank first, each account's own lines, at least 5 points under every line on numbers read in the last 15 minutes), always by room, and the account Claude Code is on counts too, except with `--strict` or `--no-history`, which never start on it. An account another run holds is passed over, so runs started one after another spread across your accounts. When no account has room it says why, starts nothing and exits 1. It takes the other flags of `switchboard run`. |
| `switchboard run -- ARGS` | Without NAME, in a folder set with `switchboard folder`: runs as that account, and its first line says which, and which folder set it. The nearest folder above wins. In a folder with none, it prints its usage and exits 2. `switchboard run` alone starts Claude Code with nothing after it. |
| `switchboard folder NAME` | This folder, and every folder inside it, runs as that account with `switchboard run`. NAME is its number, email or short name; no short name can be `off`, so `switchboard folder off` always means off. A folder is the same folder however it is spelt (through a link, with a trailing slash). Kept for this Mac only. Auto-switch never reads it. |
| `switchboard folder` | What this folder runs as, and which folder set it; or that none is set (exit 0). `switchboard folder off` takes out this folder's own entry, and says so when a folder above still applies. An account you remove takes its folders with it. |
| `switchboard folders` | Every folder set to run as an account, with the account. `--json` prints them as JSON: `schema`, then `folders`, each with `folder`, `account`, `organization` (`null` unless two accounts share the email), `set_at`, `folder_found` and `account_found` (`null` when the accounts can't be read). `--check` lists only the folders that are gone or whose account is gone, and exits 1 when there is one. `--prune` takes those out and says which; with `--json` it prints `schema` and `removed`, and `refused` with exit 1 when it could take nothing out. An account that can't be read is never taken out. `switchboard resume` is not changed by any of this: it opens a chat as the account it started under (or `--as NAME`), whatever folder you are in and whatever `run_history` says. |
| `switchboard resume CHAT` | Open a chat again in this terminal, as the account it started under, in the folder it ran in. CHAT is its id or the start of one; `switchboard chats` lists every chat with its id. `--as NAME` opens it as another account. A chat a running window holds, or written in the last minute, or with a turn still open, is open in another window: it is not opened twice, and `--fork` continues it in a copy instead. Words after `--` go to Claude Code. The exit code is Claude Code's. |
| `switchboard setup` | The first run, as the installer ends it, in numbered steps (a step with nothing to do is left out): keeps the account Claude Code is on without asking; adds your other accounts through your browser, as `switchboard add` does, then adds Codex as `switchboard add` does; when the link app is installed and not yet the default browser, says what Switchboard Links does before macOS asks, asks macOS only after you press Enter, and says what macOS's answer changed; then names what is on (the menu bar item, the status line, notifications, auto-switch) and the page. It turns on notifications and the status line (never over a status line of your own). Without a terminal it asks nothing and says in at most three lines what it did. `--yes` asks nothing and says every step. `switchboard` alone runs it too while no account is kept. |

## Auto-switch and models

| Command | What it does |
|---|---|
| `switchboard auto` | Whether auto-switch is on, at which lines, in which order, and a line for each account with a rule, lines of its own or a rank. `switchboard auto on` or `off` changes it, and it stays as you set it: it never turns on by itself again. Off, the accounts are still read; only switching stops. |
| `switchboard auto check` | One pass of auto-switch now, in this terminal, then it exits: the decision the page's loop would make, with the same lines, order, account rules and pace of reading. It runs with auto-switch off too, and never turns it on. While the page runs, its loop runs too (whenever an account is kept), so the check moves nothing and says the loop's last decision: the command is for a Mac where the page is not running. A move it makes is in History like any other, marked `auto check`. `--dry-run` decides and says why, and changes nothing. `--five-hour N` and `--week N` (50 to 99.9; `--week=N` works too) and `--order WORD` (`refills-first`, `most-room` or `stay-then-refills`) change the shared lines and the order for this check only; an account with a line of its own keeps it. `--json` prints it as one JSON document. [Its exit codes](#a-check-for-a-scheduled-job). |
| `switchboard auto why` | What auto-switch would do now, and why: the same as `switchboard auto check --dry-run`. The decision in one line, then the account in use against each of its lines, each other account and the one reason it can't take Claude Code, what a move that can wait is waiting for, and, when nothing has room, when the first account refills. It asks Anthropic for nothing and writes nothing, so it answers whether the page runs or not. It takes the same flags as `check`. Its exit code is the answer, as a dry run gives it: 0 it would move, 3 it would be held back, 4 it stays or a move that can wait is waiting, and 1 when it could not look. |
| `switchboard auto last NAME` | Auto-switch goes to that account only when no other account has room. `switchboard auto out NAME` keeps it out of auto-switch: Claude Code is never moved there, or away from there, by itself. `switchboard auto in NAME` uses it as usual again. NAME is its number, email or short name. [The rules](how-it-works.md#auto-switch). |
| `switchboard auto line NAME` | That account switches at lines of its own: `--five-hour N`, `--week N`, or both, from 50 to 99.9. Auto-switch moves off it at its own line, lands on it only with room under its own line, and its bars on the page, in the terminal and in the menu bar turn red there. Alone, it says the account's lines; `switchboard auto line NAME off` puts it back on the shared ones. |
| `switchboard auto rank NAME N` | Auto-switch lands on rank 1 first, then 2, and so on, and on an account with no rank after every ranked one; inside one rank the order decides. N is a whole number from 1 to 99, and two accounts may share one. `switchboard auto rank NAME off` takes the rank away. An account used last still comes after every other, and one kept out is still never used. |
| `switchboard switch --best` | Move Claude Code now to the account auto-switch would land on (the same rules, ranks, lines and fresh numbers, without waiting for Claude Code to be idle), and say which and why. It works with auto-switch off. When no account has room it says when the first one refills, moves nothing and exits 1. |
| `switchboard auto signed-in` | Whether Keep Claude Code signed in is on. `switchboard auto signed-in off` turns it off, and `on` turns it on again. While it is on, Switchboard moves Claude Code to another signed-in account when its sign-in is lost and can't be put back, or ten minutes before the sign-in in use stops working, also with auto-switch off. It is on until you turn it off. |
| `switchboard models` | Whether Models by the job and Sonnet for helpers are on. `switchboard models on` or `off` turns Models by the job on or off, and `switchboard models sonnet on` or `off` turns Sonnet for helpers, as the page's switches do. Each is off until you turn it on. |

## A check for a scheduled job

`switchboard auto check` runs one pass of auto-switch and exits, so a scheduled job can run it on a Mac where the page is not running, with auto-switch on or off. It decides as the page's loop does, and holds the loop's lock while it runs, so the two never switch at once. While the page runs, its loop does the checking: the loop runs whenever an account is kept, auto-switch on or off, so there a check moves nothing and exits 5. The command is for a Mac where the page is not running. It reads your saved logins from your login Keychain, as the page does. Its exit codes:

| Code | What it means |
|---|---|
| `0` | It moved Claude Code to another account. With `--dry-run`, it would. |
| `1` | It failed: the move was tried and did not happen, another program switches Claude Code's login too, or nothing was checked (Claude Code's login could not be read, the Mac is offline, or it just woke). The document's `problem` says which. |
| `2` | The command line was wrong: an unknown word, or a line outside 50 to 99.9. |
| `3` | It wanted to move and could not: no account has room, or the move was held back (a full disk, a low battery, numbers that changed when read again). |
| `4` | It looked, and there is nothing to do: Claude Code stays where it is, or a move that can wait is waiting. |
| `5` | The page's loop runs, so the check moved nothing. It says the loop's last decision and when it was made. |

With `--json` (`auto check` and `auto why`), the document has `outcome` (`switched`, `would_switch`, `stays`, `waits`, `blocked`, `failed`, `loop_running` or `refused`) and `exit_code`, `from` and `to` with `from_number` and `to_number`, `in_use` and `candidates`, `settings` and `problem`, among others. `in_use` and each candidate have `email`, `number` and `organization` (`null` unless two accounts share the email). Each candidate has `can_take_it` and `why_not`: `null` for the one it picks and any other that could take Claude Code, else one word: `needs_sign_in`, `in_use_by_run`, `not_usable`, `kept_out`, `no_numbers`, `numbers_too_old`, `at_line`, `in_use_kept_out` (the account in use is kept out, so nothing moves from it), `used_last`, `ranked_after` (ranked after the account in use: a move that can wait never goes to a later rank) or `no_gain`. `settings` is what the check decided on, with `own_lines` and `ranks`: `--five-hour` and `--week` change the shared lines for this check, and an account with a line of its own keeps it.

A line for `crontab -e` that checks every 10 minutes:

```sh
*/10 * * * * $HOME/.local/bin/switchboard auto check > /dev/null 2>&1
```

Switchboard reads each account about 20 times an hour at most, so a check run more often than every few minutes uses the numbers it already has and says how old they are. Every 5 to 10 minutes is enough.

Each move a check makes is in History as it happens: `switchboard log -f --json` prints it as one line of JSON, with `"by": "auto"` and a reason that starts `auto check:`. A check that stays adds no line of its own; History notes once, as it always does, when every account is out.

## The page and the menu bar

| Command | What it does |
|---|---|
| `switchboard page` | Open the page, http://127.0.0.1:7799, in your browser with a link that works once. |
| `switchboard page --new-key` | Make a new key for the page. Every browser opens the page again from Switchboard, and the Open as its owner bookmark is added again from the page. |
| `switchboard menubar` | Start the menu bar item. At a terminal, with its background job set up, it starts the job instead, so the item outlives the terminal. `off` stops it and keeps it off across logins and updates; `on` brings it back. `--applications` puts its app back in Applications, as the installer does. |
| `switchboard demo` | The page with made-up accounts, on port 7798. It changes nothing. |
| `switchboard serve` | Run the page. Its background job does this for you. |

## History

| Command | What it does |
|---|---|
| `switchboard log` | What happened, newest at the bottom: switches and why, keeps, auto-switch, limits, sign-ins, repairs and problems. It shows the last 20 lines; `-n N` shows N. `-f` follows it as it grows. `--json` prints each line as JSON. |
| `switchboard usage` | Work over the last 7 days, as History on the page shows it. `--days N` (up to 366) changes the span. `--json` prints it as JSON. |

## Claude Code's settings

| Command | What it does |
|---|---|
| `switchboard statusline` | Same as `show`: whether Switchboard's status line is on, the settings file, and the line it would show now. |
| `switchboard statusline on` | Puts Switchboard's status line in Claude Code's settings and prints the change. With a status line of your own, it leaves yours; `on --replace` puts Switchboard's in its place. |
| `switchboard statusline off` | Takes it out, and puts back exactly what was there. |
| `switchboard notify` | Whether notifications are on, whether Claude Code's "done" and "needs you" hooks are in and can run, and whether "done" is on. |
| `switchboard notify on` | Turns notifications on, with three hooks in Claude Code's settings. |
| `switchboard notify off` | Turns them off, and takes exactly those hooks out. |
| `switchboard notify done on` | Adds a notification when a turn of a minute or more ends. A new install has it off. |
| `switchboard notify done off` | No notification when a turn ends. "Needs you" stays as it is. |
| `switchboard hooks` | Whether the session hooks are in Claude Code's settings, can run, and are up to date. |
| `switchboard hooks on` | Puts the session hooks in, beside hooks of your own, or brings ones put in by an older version up to date. They tell an open Claude Code chat what a switch means for it, which account owns an artifact or doc it can't open, and what is known when a claude.ai connector stops answering after a switch. |
| `switchboard hooks off` | Takes exactly those hooks out. They don't go back in by themselves. |
| `switchboard skill [on\|off\|show]` | Switchboard's skill, which lets a coding agent answer questions about Switchboard: alone, where it is in, one line per place. It goes in by itself at the first run, into Claude Code's skills folder (`skills/switchboard` in its folder) and, when Codex is on the Mac, into `~/.agents/skills/switchboard`, with a small marker file that says it is Switchboard's and from which version; never over a folder of that name without the marker, and never by itself through a symbolic link. `on` puts it in, `off` takes out Switchboard's own copies, and it stays off. `show` prints the whole skill as one Markdown text, to point any other agent at; it writes nothing. |

## Artifacts

| Command | What it does |
|---|---|
| `switchboard open LINK` | Open a claude.ai artifact in the browser profile signed in as its owner. |
| `switchboard owner LINK` | Which account owns a claude.ai artifact, and how Switchboard knows. It opens nothing. `--json` prints it as JSON. |
| `switchboard pages` | Every artifact this Mac knows, and its account. |
| `switchboard links` | Whether links you click anywhere open as their owner, that is, whether Switchboard Links is your default browser. It is on unless you turn it off: the first run asks macOS once, and the page offers it until it is the default. |
| `switchboard links on` | Asks macOS to make Switchboard Links your default browser. At a terminal it first says what that does and asks you to press Enter, then says what macOS's answer changed. |
| `switchboard links off` | Asks macOS to give the seat back to your usual browser. Nothing offers it again until `switchboard links on`. |
| `switchboard check` | Prove which browser profile is signed in as which account. It reads Chrome's tabs, which needs macOS's permission. With the page running, the page checks and this waits for its answer, five minutes at most; the page's Let Switchboard check links asks macOS for that permission. Without the page, it checks from the terminal, and macOS asks about the terminal. |

## Other assistants

`switchboard continue` lets you carry on in another assistant: the chat in this folder goes on in Codex (OpenAI's app) beside Claude Code, or back. Each is a provider, and the provider is a word after the command. Once added, each of its accounts is a numbered row of the list `switchboard` prints, and pressing that number makes the same move with one key. Nothing moves on its own. [How it works](how-it-works.md#other-assistants) says what a note holds and what leaves the Mac. These commands exit 130 when stopped with Ctrl-C.

| Command | What it does |
|---|---|
| `switchboard providers` | Every provider on this Mac (Claude, Codex): whether it has room (`has room`, `had room when last read ...`, `is out until ...`, `numbers not known`, `not signed in`), its accounts or its plan and limits, which copy of its program Switchboard runs and where, the copy on your PATH when that is another one, every copy it never uses and why, and what Switchboard can do with it. Then this folder's choices and your order. Codex's numbers come from its own files on this Mac. `--ask`, at a terminal, asks Codex's own helper for them now; the first time it asks you once whether it may start that helper. Not at a terminal it starts nothing and shows the saved numbers. `--json` prints it all as JSON. |
| `switchboard add PROVIDER` | Adds another provider to your order: `switchboard add codex`. It says which copy will run and whether it looks signed in. Codex keeps its own sign-in: Switchboard never reads it, keeps it or signs in for you. `switchboard add claude` is `switchboard add`. |
| `switchboard continue` | Carries the chat in this folder on in the next provider with room, in your order; `--to PROVIDER` names one. It makes a note of the chat on this Mac from the chat's own file, with anything that looks like a secret taken out as well as Switchboard can. Then, at a terminal, it shows the note's size and asks once for this folder whether notes may go to that provider's maker (`y`, `n`, `s` shows the note first, `never`). After the yes, that provider's app starts in this terminal, on the note, in the folder where the chat's work last ran: this folder, or the folder its assistant moved into, and the command says so first. A folder outside this one is used only with your yes for it: at a terminal Switchboard asks before it reads git there or starts anything, and otherwise uses this folder and says how to allow the other. It never starts in `/`, your home folder or a folder that holds it. Not at a terminal it prints the line to run, with `--in app` too. At a terminal, `--in app` opens the desktop app with the note in its message box, for you to send. A chat is listed under the folder it was started in; with none here, it names the folders above this one that have a chat of the last day. With several chats here it lists them; `--chat NAME` (a name, or the start of an id) or `--latest` picks one. `--show` prints the note before it starts anything. `--again` makes a new note of a chat already carried on. `switchboard continue` carries a chat on in another provider, and `switchboard resume` opens a Claude chat again as the account it started under. |
| `switchboard back` | Comes back: a note of what was done in the other provider goes to the chat you left. With the session hooks on and that chat open in Claude Code, it arrives with your next message there; otherwise it prints a line to paste, or, at a terminal, offers to open the chat again with the note. `--chat NAME` picks one of several chats carried on. `--show` prints the note's path and the line to paste, and starts nothing. |
| `switchboard hand PROVIDER -- JOB` | In a terminal: one read-only job for another provider in this folder, and its answer printed: `switchboard hand codex -- "review the change to the footer"`. It asks the same yes for this folder (one yes covers this folder's chats and its jobs, and the question says so), and on its first run on this Mac one question about OpenAI's terms. One job at a time, up to 15 minutes; Ctrl-C stops it. The answer is kept for 7 days. `--chat NAME` gives the job a note of that chat: the note is made first, and the question is the one for a chat, with its size. Not in a terminal (an assistant's own shell, a script) it prints the line to run and starts nothing. Claude takes no job in this version. |
| `switchboard allow PROVIDER` | In a terminal: asks the yes for this folder without moving anything, and lifts a `never` you typed. A stay stays. Not in a terminal it refuses. |
| `switchboard stay PROVIDER` | This folder's chats stay on that provider: `continue` and `hand` never send them elsewhere. `switchboard stay off` lifts it; a `never` you typed at a question stays until `switchboard allow`. With no word it prints its two forms. |
| `switchboard order` | The order `continue` picks the next provider in, Claude first unless you change it: `switchboard order codex claude`. Your Claude accounts keep their own order. Alone, it prints the order. |
| `switchboard remove PROVIDER` | Takes another provider off your order and the list: `switchboard remove codex`. Codex itself is not changed, and what you said per folder stays. `switchboard add codex` adds it again and says again what pressing its row sends. `switchboard remove NAME` with an account's name forgets that account. |
| `switchboard do TASK` | In a terminal: type a task once, `switchboard do "review the changes on this branch"`. Switchboard works out its kind from its first sentence by a fixed table of words (build, fix, review, plan, find, write, or other) and says which word decided it. It picks the provider your routes name for that kind (`switchboard routes`): the first choice that can run and has room, then the rest of your order. It says why, and names each provider it passed and why. A provider this folder never goes to is passed, and a stay is honoured. It asks the same yes for this folder as `continue` (once per folder and provider), then starts that provider's own app on the task in this folder, with the model and effort the route names when that version of the app takes them. Your words go into one private file beside Switchboard's folder, never onto the app's command line: its first message names the file only. `--as KIND` names the kind, `--on PROVIDER` the provider, and `--model` and `--effort` replace the route's. `--show` shows the pick and starts no app. Not in a terminal it prints the line to run on standard output, with the task on one line and cut at 4,000 characters, and starts nothing. A task may be up to 24 KB; put more detail in a file in the folder and name that file in the task. It refuses to start an app in `/`, your home folder or a folder that holds it. |
| `switchboard route TASK` | The pick `do` would make, and why: the kind and the word that decided it, the provider with its model and effort, the reason, and each provider passed with why. It starts no app. `--as` and `--on` work as for `do`; `--json` prints it as JSON. It exits 1 when nobody can take the task now. |
| `switchboard routes` | Your routes, kind by kind: the providers each kind of job goes to, in turn. A kind with no route follows your order. `switchboard routes set review codex:gpt-6.2-astra:high claude:sonnet` sets one (a choice is a provider, then a model and an effort if you like; an unknown one is refused with the known ones named). `switchboard routes reset [KIND]` takes one route, or all, away. `switchboard routes preset by-the-job` turns on the one preset: lookups on each app's small model at medium effort (Claude's Haiku, Codex's GPT-6 Luna), writing on its middle model at medium (Sonnet, GPT-6.2 Sol). It fills only kinds you have no route for, says which, and its rows are tried in your order, so it never decides between the apps. `switchboard routes preset off` takes out what it filled. A fresh install has no routes, and the preset is off. `--json` prints the table as JSON. |
| `switchboard routing` | Routing inside the apps, with nothing new to type. Alone, it shows whether routing is on, every switch, your defaults, your routes, and the last five decisions, each with its reason. `switchboard routing on` turns it on: Switchboard writes one small file, the plan, from your routes (`switchboard routes`) and the room each app has, adds one key to Claude Code's settings so that Claude Code loads Switchboard's plugin (Claude Code 2.1.287 or newer), and, when Codex is added, puts two hooks and a helper file for each kind of work your routes send to a Codex model in Codex's folder; Codex asks you once to trust the hooks. In Claude Code the plugin sets the model and the effort a helper or a workflow agent starts on, as your route for that kind of work says. A choice with no room is passed, a helper that names its own model keeps it, and with no routes and no defaults nothing changes. `switchboard routing off` takes back everything turning it on wrote. `switchboard routing trial on` records what would happen and changes nothing, and `switchboard routing trial off` ends that. `switchboard routing set NAME VALUE` sets one switch or one default: `switchboard routing set defaults.workflow.effort xhigh` makes a workflow agent whose kind of work has no route run at xhigh effort at most. `switchboard routing cache` shows your own count of how often helpers stored their whole context again after a wait, from the records Claude Code keeps on this Mac, and `switchboard routing cache on` or `off` sets Claude Code's hour cache for helpers or puts back what was there. Handing work to the other app is not in this version. |

## Moving to another Mac

Switchboard keeps your accounts' logins in your Keychain. To carry them to a new Mac without signing in to each account again, put them in one file, protected with a passphrase, and take the file in on the other Mac.

| Command | What it does |
|---|---|
| `switchboard export FILE` | Writes your accounts to FILE: each one's saved login, its whole email, its short name, its place in your list and its rule in auto-switch. Not your API keys (unless `--with-keys`), folders, History, readings or settings. It asks for a passphrase twice and protects the file with it; the file is yours alone (mode 600) from its first byte. `-` writes it to standard output, still protected. `--account NAME` (again for each) writes only those accounts. It refuses a folder that iCloud Drive, Dropbox, Google Drive, OneDrive or another such service copies off the Mac, unless `--anyway`, and a file that is there already. It changes nothing on this Mac. `--passphrase-file PATH` and `--passphrase-fd N` give the passphrase for a script, as the paragraph under this table says. |
| `switchboard export --check FILE` | Asks the passphrase and says whether the file is whole and what it holds. It keeps nothing. |
| `switchboard export - --plain` | The same accounts unprotected, as JSON, to standard output only, for a tool of your own. It never writes a file. Treat what it prints as the logins themselves. |
| `switchboard import FILE` | Asks the passphrase, then shows each account in the file (its email, when its login was saved, when its sign-in ends) and what it would do with it: add it, or skip it because it is here already, because the login here is newer, or because Claude Code is on it here. Then it asks before keeping anything; `--yes` answers yes. `-` reads the file from standard input. Each account it keeps is read once to see that it works; one Anthropic refuses is kept and marked as needing a sign-in. |
| `switchboard import FILE --replace` | Also replaces an older login here with the newer one in the file, but only when Anthropic says the file's login is that account's; when Anthropic can't be asked about it (its access has run out, or there is no connection), it is skipped and the login here stays. Nothing ever puts an older login over a newer one, with any word. |
| `switchboard readings give` | This Mac's limits for each account as a small JSON document: whole emails, the numbers, and when each was read. No login and nothing secret. |
| `switchboard readings take [-\|FILE] [--for 10m]` | Shows those numbers here, for accounts this Mac keeps too, when they are newer than its own: for 10 minutes unless `--for` says (an hour at most). The page and `switchboard status` mark them: "from another Mac, 3 min ago". Their age is their real age. Auto-switch decides on this Mac's own numbers only. |

At a terminal, the passphrase is asked without showing it, twice for a new file. A new file takes one of at least 8 characters. For a script, the passphrase comes from a file only you can read (`--passphrase-file PATH`; one others can read is refused) or from a descriptor (`--passphrase-fd N`); never from an argument or the environment. To show one Mac's numbers on another: `switchboard readings give | ssh other-mac switchboard readings take -`.

`switchboard import` and `switchboard export --check` exit 0 when done, 1 when something failed or you said no, 2 for words they don't know, and for a file they can't use, nothing kept: 3 the passphrase does not open it, 4 it was changed or damaged after it was made (or it is not an accounts file), 5 it is cut short, 6 it was made by a newer Switchboard.

How the file is protected: a key is made from your passphrase with PBKDF2-HMAC-SHA256 (600,000 rounds, a random salt), the accounts are encrypted with AES-256 by the `openssl` macOS ships, and the whole file is signed with HMAC-SHA256, which is checked before anything is decrypted. A passphrase you can remember is only as strong as it is long: use several words.

After the other Mac has taken the file, delete it; Switchboard never does. A login carried this way is one sign-in kept in two places, and each renewal of a login replaces the one before it: once one Mac renews an account's login, the copy on the other Mac stops working at its next renewal and asks for a fresh sign-in. Move accounts with it; on a Mac you keep using as well, sign in there again.

## Keys

| Command | What it does |
|---|---|
| `switchboard keys list` | Your API keys: their names and last four characters only. |
| `switchboard keys add NAME` | Asks for the key without showing it, and puts it in your Keychain. A key piped in on one line works too. |
| `switchboard keys remove NAME` | Takes it out of your Keychain, after asking; `--yes` doesn't ask. The key stays valid with its provider. |
| `switchboard claude [args]` | Claude Code on your keys, in this terminal only. The gateway must be on (on the page: Settings, Keys). |
| `switchboard add --token` | Keeps a one-year token (what `claude setup-token` prints) or an API key from the Claude Console as an account. It asks for it without showing it; `-` reads one line from standard input instead. It is never taken from an argument or the environment, and adding it sends nothing to Anthropic. `--for NAME` puts a token with one of your accounts (that account stays one row); `--label WORD` gives it a name of its own; `--as SHORT` a short name to type. Without them, it asks. Anything with neither shape is refused. A key is billed to its Console organization, so it always takes a name of its own. Running it again with the same token changes nothing; a new token for the same account takes the old one's place. |
| `switchboard switch NAME` (a token) | Puts Claude Code's login on the one-year token, the same way as any switch, and back again: switching to a sign-in puts that sign-in back whole. On a token, claude.ai connectors and Remote Control do not work; model requests do. Onto an API key it changes nothing, says how to use it in one terminal (`switchboard claude`) and exits 1. |
| `switchboard auto last\|out\|in NAME` (a token or a key) | Each starts kept out of auto-switch. A token may be used last; it is never used as usual, since its limits are not known. An API key always stays kept out: it is billed per token. |
| `switchboard remove NAME` (a token or a key) | Forgets it and takes it out of your Keychain. Removing an account also forgets a token kept for it. |

## Install and checks

| Command | What it does |
|---|---|
| `switchboard update` | Install the newest version the way it was installed, with the same choices. `--link-app` adds the link app (it needs Apple's command line tools). `--menu-app` adds the menu bar item and `--no-menu-app` takes it and its app out. `--port N` moves the page to port N, from 1024 to 65535. A changed choice is kept for later updates. |
| `switchboard doctor` | Checks every part of the install, one line each: `ok` is fine, `--` is not set up (yet), `NO` needs a look, and a line that isn't fine says what to do. It repairs nothing (`switchboard doctor --fix` does) and changes no account and nothing in Claude Code's files; like other commands, it may note where your terminal found `claude`, for the page, and refresh its small record of what macOS trusts. It exits 1 when something needs a look. |
| `switchboard doctor --fix` | Runs now the repairs the page makes by itself every 10 minutes, one line each, `fixed`, `ok` or `NO` with the one thing to do: a damaged list of accounts, settings file or page file brought back from its last good copy, a switch cut off part-way settled, Switchboard's status line and hooks put back in Claude Code's settings. It exits 1 when something still needs a look. |
| `switchboard awake --once` | Whether Claude Code is working, and what the power is. It holds nothing. `--json` prints it as JSON. |
| `switchboard version` | Which version this is, with the commit it was made from when it knows it: `switchboard 0.2.0 (1a2b3c4)`. `--version` and `-v` do the same. |
| `switchboard help` | The few commands a new person needs, on one screen. `-h` and `--help` do the same. `switchboard help all` is every command; `switchboard help CMD` is one command's lines. |
| `switchboard about` | What this install is and what it put on the Mac, each fact checked when you ask: the version and where it came from (with Homebrew, the release file's SHA-256 was checked; the one-line install takes `main` with no checksum), what macOS says of each app's signature (`codesign`, `spctl`), the places it uses, what runs now, whether the program's files are as installed (against `~/.switchboard/app/SHA256SUMS`, which `shasum -a 256 -c` reads too), where sign-ins are kept, what leaves the Mac, and the command that removes it. The installer ends with it. It changes nothing and exits 0. `--json` prints it as JSON. |
| `switchboard update --check` | Looks once whether a newer version is out, in the place an update would install from: Homebrew's list on this Mac, the folder you installed from, or the repository the installer came from. It changes nothing. It exits 0 when this is the newest, 10 when a newer one is out, and 1 when it could not look. `--json` prints it as JSON. |

## The installer

```sh
sh install.sh [options]                  # from a copy of the repository
curl -fsSL … | sh -s -- [options]        # the one-line install, with options
```

Homebrew's cask runs it with `--homebrew` alone: Homebrew adds the commands, the first run is left for you to type, and a later run by Homebrew keeps the choices you made before. `switchboard update` then runs `brew upgrade switchboard`.

| Option | What it does |
|---|---|
| `--no-setup` | Skips the first run at the end. |
| `--port N` | The page on port N, from 1024 to 65535, instead of 7799. |
| `--no-agent` | No background jobs, and macOS's list of apps is left alone. Start the page with `switchboard serve`. |
| `--no-menu-app` | No menu bar item and no Switchboard.app. On an install that has them, it takes them out. |
| `--no-link-app` | Leaves out the link app, Switchboard Links, and keeps it out for `switchboard update`. Without this option the installer builds it when Apple's command line tools are there. |
| `--own-python` | Uses Switchboard's own Python even when the Mac has one it could use. |

## The uninstaller

```sh
sh ~/.switchboard/app/uninstall.sh [--all] [--no-agent]
```

| Option | What it does |
|---|---|
| (none) | Removes the program and puts back what Switchboard changed. Your settings, accounts and History stay in `~/.switchboard`, and the accounts' logins in your Keychain. Your notes of chats stay in `~/.switchboard-notes`. A copy of the uninstaller stays there too: `sh ~/.switchboard/uninstall.sh --all` removes the rest. |
| `--all` | Also removes your accounts (their logins leave your Keychain), the gateway's keys, everything else in `~/.switchboard`, and the notes folder beside it, `~/.switchboard-notes`. |
| `--no-agent` | Leaves launchd and macOS's list of apps alone: for an install made with `--no-agent`, and for tests. |

## Settings from the terminal

| Command | What it does |
|---|---|
| `switchboard config` | Every setting, grouped as on the page, with its value, its default and one line that says what it does. A key in the settings file that nothing reads (a typing mistake, or a key from an older version) is said under the list, with the nearest real key; it is never deleted. `--json` prints it as one JSON document. |
| `switchboard config get KEY` | The value alone, as `config` says it: `on`, `90`, `refills-first`. `--json` prints `schema`, `key`, `value`, `default` and `refused`. |
| `switchboard config set KEY VALUE` | Sets it, through the running page when it answers, else here, with the page's own checks: `switchboard config set auto_policy.weekly_at 90`. It says in one line what changed, and run twice it says it is so already. |
| `switchboard config unset KEY` | Puts its default back. |
| `switchboard config path` | The files settings are kept in: Switchboard's, then Claude Code's. |
| `switchboard config --changed` | Only the settings that differ from their defaults. |
| `switchboard config --as-commands` | Those settings as the commands that set them again, one per line, for a dotfile: run them on a new Mac to get the same setup. With `--json` it prints `schema` and `commands`, those lines as a list. |

The keys are the ones the page shows when you hover a setting's title, and the ones in `~/.switchboard/settings.json`: `auto`, `auto_policy.session_at`, `auto_policy.weekly_at`, `auto_policy.model_at`, `auto_policy.model`, `auto_policy.strategy`, `auto_policy.sign_lines`, `auto_policy.pick_hold` (`default`, `until_out` or `off`), `awake`, `notify_done`, `gateway_on`, `run_history`, `update_check` and `network_trust`. `config set notify_done on` does what `switchboard notify done on` does; the page has no row for it. `auto_policy.model` takes one model or several with commas (`Fable,Opus`). `switchboard config set run_history apart`, `update_check daily` and `network_trust python` set the last three in the settings file, and `config unset` puts each default back (`shared`, `off`, `mac`). `auto_policy.rules`, `auto_policy.own_lines` and `auto_policy.ranks` are listed too, one value per account; they are set with `switchboard auto last|out|in`, `switchboard auto line` and `switchboard auto rank`, and `--as-commands` writes those commands. The settings kept in Claude Code's own files keep their own commands, which undo exactly what they did: `statusline`, `notify`, `hooks`, `models` and `models_sonnet` are listed with the command that sets each, as are `links` (`switchboard links on`) and `port` (`switchboard update --port N`), and `config` never sets them. Nor does it set what only the page sets (house rules, the code map, claude-mem, the profile for other links) or what you set by hand in the file (`browser`, `gateway_port`, `menubar_position`). An unknown key exits 2 and names the nearest one.

`--debug`, after any command or before it, and `SWITCHBOARD_DEBUG=1` say on standard error what the command did: each file it read or wrote, each program it started (with secrets masked), each call to the running page or the network (method, address, status and how long; never a query, a header or a body), each call to the Keychain, and whether this command or the running page did the work. Its last line is how long the command took, and how much of that went to this command, the page, the Keychain, the network and other programs. No token, key or email is in it. Words after `--`, and after `switchboard claude`, are Claude Code's own. Without it, nothing about a command changes. `SWITCHBOARD_DEBUG=1` counts only for a command you run at a terminal: the page (`serve`), the demo, the menu bar, the status line, notifications, hooks, `switchboard claude`, `run`, `resume`, `hand` and `awake` ignore it, so a variable left in a shell profile never traces them. `--debug` typed on a command always counts.

`ls` and `list` are `status`, `rm` is `remove`, `upgrade` is `update`, and `whoami` is `current`.

## For scripts

These keep the rules below: `switchboard status --json`, `current`, `switch` and `use` with `--json`, `config`, `owner LINK --json`, `leftovers --json`, `folders --json`, `auto check` and `auto why` with `--json`, `update --check --json` and `readings give`. (`log --json` prints one JSON line for each entry; `usage`, `chats`, `awake` and `providers` print JSON of their own, without `schema`.)

- With `--json`, standard output holds one JSON document and nothing else, also when the command refuses: the document then says why. Anything else the command says goes to standard error.
- Words a command doesn't take are refused too, with exit code 2. With `--json` the document says why: its `refused` is the command's usage line (`{"schema": 1, "refused": "..."}` for a command whose document has no field for it); `auto check`, `auto why` and `update --check` say it in their own document's `problem`. `status --json` is the one that passes over words it doesn't take.
- A document starts with `"schema": 1`. Removing or renaming a field raises it; adding one does not.
- Times are UTC, ISO 8601 to the second, ending in `Z`. Percents are numbers. What is not known is `null`, never 0. An account is its whole email.
- A command that changes something says what it changed in one line. Run again, it changes nothing more and says so, with three exceptions: `swap` puts the two accounts back, `leftovers clear ID` finds no leftover with that id and exits 1, and `export FILE` refuses a file that is there already.

`switchboard switch NAME --json`, after a switch through the running page:

```json
{
 "schema": 1,
 "moved": true,
 "from": "maya@northwind.example",
 "to": "maya@fernhill.example",
 "in_use": "maya@fernhill.example",
 "reason": "Picked on the page.",
 "at": "2026-11-25T06:40:12Z",
 "refused": null,
 "from_number": 1,
 "to_number": 3
}
```

`moved` is false when Claude Code was on that account already (then `to` and `refused` are `null`), or when the switch was refused: then `refused` is the sentence the plain command says, and `in_use` is where Claude Code still is. `reason` is the reason History records for the switch. `from_number` and `to_number` are the numbers you type for those two accounts (`null` when Switchboard keeps none): two accounts with one email have the same `from` or `to`, and their own numbers. `switchboard current --json` prints `schema`, `email`, `signed_in`, `in_switchboard`, `number` and `short_name`. It also prints `kind` (`sign_in`, or `one_year` while Claude Code is on a one-year token Switchboard keeps) and `label` (the name of a token kept as an account of its own, which `current` prints in place of an email; `email` is then `null`).

Exit codes. A script reads a code with the command it came from: the same number can mean different things in two commands.

| Command | 0 | 1 | 2 | Other codes |
|---|---|---|---|---|
| `switchboard status --json` | the document | the accounts can't be read (a sentence on standard error) | | |
| `switchboard switch [NAME] --json`, `switchboard use NAME --json` | moved, or already there | refused: no such account, a sign-in is needed, nowhere to go | words it doesn't take | |
| `switchboard switch --best` | moved | no account has room (it says when the first one refills), or the move was refused | words it doesn't take | |
| `switchboard current [--json]` | on an account Switchboard keeps | signed out, or on a login Switchboard doesn't keep | words it doesn't take | |
| `switchboard config ...` | done | a value the checks refuse, a setting set elsewhere, or one that needs the page | an unknown key, or words it doesn't take | |
| `switchboard owner LINK --json` | the owner is known | not known yet | words it doesn't take | |
| `switchboard auto check [--dry-run]` | moved (with `--dry-run`: it would) | the move failed, another program switches Claude Code's login, or nothing was checked (Claude Code's login can't be read, the Mac is offline, or it just woke) | words it doesn't take, or a line outside 50 to 99.9 | 3 held back, or no account has room; 4 nothing to do; 5 the page's loop runs, so the check moved nothing ([details](#a-check-for-a-scheduled-job)) |
| `switchboard auto why` | it would move | nothing was checked, as for `auto check` | words it doesn't take | 3 it would be held back; 4 Claude Code stays, or a move that can wait is waiting |
| `switchboard auto line NAME ...`, `switchboard auto rank NAME ...` | done | a value or an account refused | words it doesn't take | |
| `switchboard move NAME N`, `switchboard swap NAME NAME` | done, also when it was so already | no such place or account, or the same account twice | words it doesn't take | |
| `switchboard add --as SHORT --at N` | `add` ended, and the account it kept has the name and the place (when it kept none, it says so) | a short name or a place refused, before any sign-in | words it doesn't take | |
| `switchboard leftovers [--json]` | the list | | words it doesn't take | |
| `switchboard leftovers clear ID...\|--all` | cleared | an id no leftover has, one still needed, a no, or one that could not be removed | words it doesn't take | |
| `switchboard run [NAME] -- ARGS` | Claude Code's own exit code | Switchboard refused before Claude Code started | words it doesn't take, or no NAME and no folder set | 6 `--strict` or `--no-history` on the account Claude Code is on, so nothing started. Claude Code's own code can be any number, 6 too. |
| `switchboard folder ...`, `switchboard folders ...` | done | `folders --check` found an entry that no longer fits, or no such account or folder | words it doesn't take | |
| `switchboard export FILE` | written | refused: a file there already, a synced folder, the passphrase, no such account | words it doesn't take | |
| `switchboard export --check FILE`, `switchboard import FILE` | done (`import`: also when there was nothing to keep) | failed, or you said no | words it doesn't take | 3 the passphrase does not open it; 4 it was changed or damaged after it was made, or it is not an accounts file; 5 it is cut short; 6 it was made by a newer Switchboard |
| `switchboard readings give`, `switchboard readings take` | done | the file can't be read, or isn't what `readings give` prints | words it doesn't take | |
| `switchboard update --check [--json]` | this is the newest, or newer | it could not look | words it doesn't take | 10 a newer version is out |

## The accounts as JSON

`switchboard status --json` is for scripts, status bars and coding assistants. It only reads: it keeps no login, writes nothing, and prints nothing but the document. The same document comes whether the page is running or not. On the demo's made-up accounts, with one account shown:

```json
{
 "schema": 1, "version": "0.2.0", "read_at": "2026-11-25T06:32:22Z",
 "in_use": "maya@northwind.example", "next": "maya@ivy.example",
 "auto_switch": {"on": true, "order": "refills-first", "five_hour_line": 90, "week_line": 95,
                 "model_line": 95, "models": ["Fable"]},
 "accounts": [
  {"number": 2, "email": "maya.reyes@mail.example", "short_name": "personal", "plan": "Max 20x",
   "organization": "Personal", "in_use": false, "next": false, "needs_sign_in": true, "in_switchboard": true,
   "sign_in_ends_by": "2026-11-24T06:32:22Z",
   "limits": {"five_hour": null, "week": {"used_percent": 91, "refills_at": "2026-11-29T07:00:00Z", "pace": null},
              "models": [], "spend": null},
   "checked_at": "2026-11-25T06:31:42Z"}
 ]
}
```

Accounts come in the order `switchboard status` prints them, each named by its whole email. `number` is the one you type (`switchboard 2`); it is `null` for a login Claude Code is on that Switchboard doesn't hold yet, which says `"in_switchboard": false`. Times are UTC. A number Switchboard can't vouch for, such as a reading over an hour old, is `null`, never 0, and a limit the plan doesn't have (some have no 5-hour limit) is `null` itself. `next` is marked only while auto-switch runs, and it runs in the page. No login, token, fingerprint or id is ever in it.

An account with a switch line of its own (`switchboard auto line`) also has `five_hour_line` or `week_line`, or both, each only when it has that line of its own. `order` is `refills-first`, `most-room` or `stay-then-refills` (stay on the account in use until a line, then go to what refills first): a new word may join them, never a new shape.

`this_folder` is the account `switchboard run` without NAME starts as in the folder the command ran in, with the folder that set it: `{"account": "maya@fernhill.example", "folder": "/Users/maya/work/client"}`, or `null` when none applies. At a terminal, `switchboard status` says the same in one line under the list.

`from_another_mac` is true while an account's numbers are ones `switchboard readings take` brought from another Mac; `checked_at` is then when that Mac read them.

`limits.week.pace` is the week at its own pace: `even_percent` (where an even week would be now), `ahead` (`true` when, at the pace of its use so far, the week is full before it refills; `null` while it is too early to say) and `full_at` (when; `null` unless `ahead`). It is `null` when the reading can't tell. `limits.spend` is a spend limit, where the reading carries one: `spent`, `limit`, `currency`, `used_percent` and `refills_at`; `null` when there is none. Two accounts with one email have the same `email`, and their own `number` and `organization`.

Each account also has `kind`: `"sign_in"`, or for a one-year token or an API key kept as an account (`switchboard add --token`) `"one_year"` or `"api_key"`. A token or a key with a name of its own is an account of its own, with `email` and `number` `null`, its name in `label`, and `added_at`, the day Switchboard kept it (a token carries no end date). A token's limits are not known, so its percents are `null`; an API key has no plan limits, so its `five_hour` and `week` are `null`. A sign-in with a one-year token kept for it gives the day in `one_year_added_at`. While Claude Code is on a token, the top `in_use` is `null` and that token's own `in_use` is `true`.

`switchboard switch NAME --json` (and `use NAME --json`) onto a token, or away from one, names it the same way: its label, or the name of the account it is kept for. Its `to_number` or `from_number` is `null`, and `in_use` is `null` while Claude Code is on it.

`schema` goes up when a field is removed or renamed; a new field leaves it as it is. `switchboard owner LINK --json` prints `schema`, `link`, `owner` (an email, or `null` while not known), `how` and `open_with` the same way.

## Files

The commands and flags on this page, what Switchboard writes in Claude Code's settings ([how it works](how-it-works.md#what-it-puts-in-claude-codes-settings)) and the files below are how other tools can rely on Switchboard. While its version starts with 0, any of them may change.

| In `~/.switchboard` | What it holds |
|---|---|
| `app/` | the program |
| `app/SHA256SUMS` | the SHA-256 of each program file as the installer put it in place, in the form `shasum -a 256` writes; `switchboard about` and `switchboard doctor` compare the files with it |
| `bin/` | the command (the one in `~/.local/bin` links here) |
| `runtime/` | Switchboard's own Python when it has one, the menu bar item's packages, and `python.json`, which says what each part runs on |
| `accounts.json` | your accounts: emails, organizations, names, order, and a fingerprint of each login (the logins are in your Keychain) |
| `usage.json` | each account's latest numbers, and 7 days of them |
| `run/` | while `switchboard run` or `switchboard resume` runs as another account: its private Claude Code folder, with that account's identity and links to your Claude Code folder. It goes when Claude Code ends. One is kept only when a link in it was replaced by a real file or folder; History says where |
| `folders.json` | the folders set to run as an account (`switchboard folder`): each folder's real path and the account's id and email |
| `terminal/` | the small files that open Terminal as an account (`account-<id>.command`) or on a chat (`chat-<id>.command`) |
| `folders.json.lock` | one change to `folders.json` at a time |
| `readings-taken.json` | numbers you took from another Mac (`switchboard readings take`): for each account, its numbers, when that Mac read them, when they were taken and until when they are shown |
| `newer.json` | what the last daily look for a newer version found: when, the newest version, or what kept it from looking (only once the look is turned on) |
| `mac-trust.json` | which root certificates to ask macOS about: the SHA-256 digests of those it called trusted at its last full look, and the times and sizes of the files that answer depends on. Never a certificate: a root is trusted only because macOS says so to the process asking |
| `readings.jsonl` | each account's limit percents over the last 8 days, a line when one changed, for the week the page draws; for an account it still reads, its last line with a 5-hour number stays however old, so a plan with a 5-hour limit is never said to have none |
| `week.json` | the last week the page read, so a restart shows it at once when it was read within a day |
| `owners.json` | which account owns each artifact, and its browser profile, for the link app when the page is down |
| `hooks/item-need.json` | when a chat last needed another account's artifact, for each owner, and whether Claude Code was moved there (`hooks/item-need.lock`: one at a time) |
| `hooks/` | the session hooks' small records: for each chat, what it was told, whether it runs on a login of its own, how far its file was read, the accounts it was on (organization and time) and when each claude.ai connector answered or failed in it; and `published.jsonl`, each artifact a session published (its link, the file it came from, the time, the session), kept a week or two |
| `hooks/notes/` | after `switchboard back`, a pointer for a Claude chat's next message: fixed words, times and the path of the note, never a word of a chat; read once and removed, or dropped after a week |
| `providers/` | the other assistants' settings (your order, the providers you added, your yes or never per folder and provider), a ledger of each move (ids, paths, times, sizes and counts, never a chat's text), each provider's latest numbers, and the versions of the programs found |
| `previews/` | a small picture of each artifact's page, made on this Mac from the file Claude Code published |
| `chats.json` | what the last look at your chats found: each chat's file and how far it was read, the account it started under (organization and account id) and each account it was on, with times, and its folder; per account, the claude.ai connectors that answered under it. The session hooks and `switchboard resume` read it |
| `chats-view.json` | the rest of what `switchboard chats` shows: titles, each chat's connectors, turns over the last day, how far the look has got (`chats.lock`: one look at a time) |
| `switch-journal.json` | a switch under way, so one cut off can be finished or undone |
| `logins/` | a sign-in under way from the page: one folder for each, with its link and what the sign-in printed. No login is in it: that waits in a temporary Keychain item until the sign-in is kept or cleared |
| `settings.json` | your settings |
| `state.json` | what the page shows: browser profile names and artifact links |
| `history.jsonl` | History, as `switchboard log` shows it |
| `live/` | the numbers Claude Code handed the status line |
| `packs/` | the chat search's index, and Switchboard's records of house rules, the code map and claude-mem |
| `models.json` | Switchboard's record of Models by the job and Sonnet for helpers |
| `switchboard.log` | the page's log (`agent.log` is its background job's, `menubar.log` the menu bar item's) |
| `install.log`, `menubar-install.log` | what the installer did: Switchboard's own Python, and the menu bar item's packages |
| `auto.log` | the log of the loop that reads the accounts and, while auto-switch is on, switches: one line per pass, accounts named by their number (`auto.log.1` to `.3` are older) |
| `auto-heartbeat.json` | where that loop is: its step, its last pass, the last read the page asked for that it answered, and since when it found no network |
| `auto-state.json` | that loop's record: its recent switches and which account was in use when, and the notification of a move that keeps Claude Code signed in, for the page to give the same one |
| `auto.lease`, `auto.pid` | that loop's lock (one at a time) and its process id |
| `read-request.json` | the page's last request for a read now (opening the page, Read again, a changed setting): its time, and whether for every account |
| `recovery/` | `manifest.json`, the list of logins Switchboard keeps for recovery until it is done with them (account ids, fingerprints and times, no login); and only when the Keychain refused one, that login, in a file only you can read, until the login it protects is safely kept |
| `signin-watch.json` | the sign-in watch's heartbeat, every 10 seconds |
| `signin-watch.lease` | the sign-in watch's lock (one at a time) |
| `signin-watch.log` | the sign-in watch's log (`signin-watch.out` is its background job's) |
| `signin-record.jsonl` | what the sign-in watch saw happen to Claude Code's sign-in, and what was done; no email and no token in it |
| `put-back.json` | when Claude Code's login was first seen missing and when each put-back landed, so every process shares one wait and one limit; no login in it |
| `signin-hold.json` | which plain login file of Claude Code's is Switchboard's copy (its path, its stamp and when it was made), so it can take that copy away again; no login in it. `auto_policy.keep_signin_file` false in `settings.json` stops the standing copy; `auto_policy.hold_signin_file` false stops the copy after a put-back too |
| `opens.jsonl` | each artifact link Switchboard opened: when, the first 8 characters of its id, its owner, the browser profile tried and how it went (`opens.jsonl.1` is the one before) |
| `keychain-argv.json` | how many logins were too long for the Keychain's standard input and went in its command line (`switchboard doctor` says so) |

Beside `~/.switchboard`, never in it, `~/.switchboard-notes/` holds the notes of chats you carried on in another assistant (kept 30 days) and the answers of jobs you handed on (kept 7 days): one folder per piece of work, and only you can read them. It holds nothing else, so it is the one folder to allow when the next app asks to read a note. A plain uninstall keeps it and says so; `--all` removes it.
