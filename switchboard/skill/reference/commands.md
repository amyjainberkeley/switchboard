# Commands

Every command of this version, with its words. `sb` is the same command, shorter, where the installer could add it. `switchboard CMD --help` prints the lines for one command. `switchboard help` prints the few a new person needs, and `switchboard help all` prints every command. Short words: `ls` and `list` are `status`, `rm` is `remove`, `upgrade` is `update`, `whoami` is `current`. `--debug`, after any command, says on standard error what it did. NAME is an account's number, its whole email or its short name. A command exits 0 when it worked, 1 when something failed or needs a look, and 2 for words it does not know.

## Only reads: run these freely

- `switchboard status --json`: the accounts and their limits as one JSON document (no login in it). It keeps nothing, not even a login Switchboard does not hold yet.
- `switchboard about` and `switchboard about --json`: what is installed and where, each fact checked now, as the install ended with: where the code came from, how the apps are signed, what runs, whether the program's files are unchanged, what leaves the Mac, how to remove it, and the project's address.
- `switchboard current`: the whole email of the account Claude Code is on. `switchboard current [--json]` exits 1 when Claude Code is signed out or on a login Switchboard does not keep.
- `switchboard doctor`: checks every part of the install, one line each: `ok`, `--` (not set up yet) or `NO` (needs a look), with what to do.
- `switchboard auto`: whether auto-switch is on, at which lines, in which order, and each account's rule, lines of its own or rank.
- `switchboard auto why`: what auto-switch would do now, and why. It asks Anthropic for nothing and writes nothing.
- `switchboard auto signed-in`: whether Keep Claude Code signed in is on.
- `switchboard log`: what happened, newest at the bottom; `-n N` shows N lines, `--json` prints each as JSON. (`-f` follows it and does not end.)
- `switchboard chats`: the chats on each account now, where each started, how often it moved.
- `switchboard usage`: Claude Code use over the last 7 days, as History on the page shows it.
- `switchboard owner LINK`: which account owns a claude.ai artifact, and how Switchboard knows. It opens nothing.
- `switchboard pages`: every artifact this Mac knows, and its account.
- `switchboard config`: every setting, with its value, its default and what it does. `switchboard config get KEY`, `switchboard config path`, `switchboard config --changed` and `switchboard config --as-commands` read too.
- `switchboard statusline` and `switchboard statusline show`: whether Switchboard's status line is on, and the line it would show.
- `switchboard notify`: whether notifications are on, whether Claude Code's hooks for them are in and can run, and whether "done" after a long turn is on.
- `switchboard hooks`: whether the session hooks are in Claude Code's settings and up to date.
- `switchboard links`: whether links you click anywhere open as the account that owns them.
- `switchboard models`: whether Models by the job and Sonnet for helpers are on.
- `switchboard skill` and `switchboard skill show`: alone, where this skill is in, one line per place; `show` prints the whole skill as one Markdown text, for any other agent.
- `switchboard providers`: every assistant on this Mac (Claude, Codex), whether it has room, and what Switchboard can do with it.
- `switchboard route TASK`: the provider, model and effort `switchboard do` would pick for that task, and why. It starts nothing.
- `switchboard routes`: the routes, kind by kind: the providers each kind of job goes to, in turn.
- `switchboard routing`: whether routing is on, every switch, the defaults, the routes, and the last five decisions with their reasons. `switchboard routing cache` shows the person's own count behind the hour cache for helpers.
- `switchboard folder`: what this folder runs as with `switchboard run`. `switchboard folders` lists every folder set; `switchboard folders --check` lists those whose folder or account is gone.
- `switchboard leftovers`: the logins Switchboard keeps aside as recovery copies, and whether anything still needs each. It never shows a login.
- `switchboard readings give`: this Mac's limits for each account as a small JSON document, with no login.
- `switchboard awake --once`: whether Claude Code is working, and what the power is.
- `switchboard update --check`: whether a newer version is out. It changes nothing.
- `switchboard version`, `switchboard help`, `switchboard help all`.

## Changes something: run only when the person asked for that change

Say the exact command and what it changes before you run it.

- `switchboard` and `switchboard status`: the accounts and their limits; `switchboard` alone, at a terminal, then waits for a number to switch, and while no account is kept it runs the whole first run instead (see `switchboard setup` below). When Claude Code is on a login Switchboard does not hold yet, either one keeps that login first, and auto-switch may turn on. To look, use `switchboard status --json`.
- `switchboard 2`: move Claude Code to account 2. When 2 is Codex's row in the list, it carries this folder's newest chat on in Codex instead. `switchboard EMAIL` moves it to the account with that email.
- `switchboard switch NAME`: move Claude Code to that account. Alone, to the next one in the list. `switchboard use NAME` is the same.
- `switchboard switch NAME --json`: the same switch, then one JSON document saying what happened.
- `switchboard switch --best`: move Claude Code now to the account auto-switch would land on, and say why.
- `switchboard auto on` and `switchboard auto off`: turn auto-switch on or off. It stays as set.
- `switchboard auto last NAME`: auto-switch goes there only when no other account has room. `switchboard auto last|out|in NAME`: `out` keeps it out of auto-switch, `in` uses it as usual again.
- `switchboard auto line NAME`: that account's own lines, with `--five-hour N`, `--week N`, or both (50 to 99.9). Alone, it says them; `switchboard auto line NAME off` puts it back on the shared ones.
- `switchboard auto rank NAME N`: auto-switch lands on rank 1 first, then 2. `off` takes the rank away.
- `switchboard auto signed-in on` and `switchboard auto signed-in off`: Keep Claude Code signed in on or off. While it is on, Switchboard moves Claude Code to another signed-in account when its sign-in is lost and can't be put back, or is about to end, also with auto-switch off.
- `switchboard auto check`: one pass of auto-switch now, for a Mac where the page is not running. With `--dry-run` it changes nothing (that is `switchboard auto why`).
- `switchboard remove NAME`: forget an account; its saved login leaves the Keychain. Claude Code stays on the login it has. It asks first; `--yes` answers.
- `switchboard alias NAME WORD`: give an account a short name to type. `switchboard alias NAME` alone takes its short name away.
- `switchboard move NAME N`: put an account at place N in the list. `switchboard swap NAME NAME`: two accounts change places.
- `switchboard config set KEY VALUE` and `switchboard config unset KEY`: change a setting, or put its default back.
- `switchboard statusline on` and `switchboard statusline off`: put Switchboard's status line in Claude Code's settings, or take it out. `on --replace` puts it in place of one of your own.
- `switchboard notify on` and `switchboard notify off`: notifications on or off, with their three hooks in Claude Code's settings.
- `switchboard notify done on` and `switchboard notify done off`: a notification when a turn of a minute or more ends, on or off. A new install has it off; "needs you" stays as it is.
- `switchboard hooks on` and `switchboard hooks off`: the session hooks in Claude Code's settings, or out.
- `switchboard models on` and `switchboard models off`: Models by the job on or off. `switchboard models sonnet on` and `switchboard models sonnet off`: Sonnet for helpers on or off.
- `switchboard routing on` and `switchboard routing off`: routing on, or off with everything turning it on wrote taken back. `switchboard routing trial on|off`: a trial run that records and changes nothing. `switchboard routing set NAME VALUE`: one switch or default. `switchboard routing cache on|off`: the hour cache for helpers.
- `switchboard links on` and `switchboard links off`: ask macOS to make Switchboard Links the default browser, or give the seat back.
- `switchboard skill on` and `switchboard skill off`: put this skill in, or take Switchboard's copy out. Off stays off. (The help writes the command as `switchboard skill [on|off|show]`.)
- `switchboard open LINK`: open a claude.ai artifact in the browser profile signed in as the account that owns it.
- `switchboard check`: prove which browser profile is signed in as which account. It reads Chrome's tabs, and macOS may ask for permission.
- `switchboard page`: open the page in the browser. `switchboard page --new-key` makes a new key for it: every browser opens it again from Switchboard.
- `switchboard menubar off`: the menu bar item stops and stays off, after a login or an update too. `switchboard menubar on`: it starts now and at every login. Both return.
- `switchboard menubar --applications`: put Switchboard.app back in Applications.
- `switchboard menubar`: run the menu bar item. The person runs it in their own Terminal window: in an agent's shell the item runs tied to that shell, and the command does not return. To start it, `switchboard menubar on` is enough.
- `switchboard run NAME -- ARGS`: run one Claude Code command as that account, without switching: `switchboard run NAME -- -p "…"`. Every other session keeps its login.
- `switchboard run NAME --no-history -- ARGS`: the same, with the chat kept apart; it goes when the run ends.
- `switchboard run NAME --strict -- ARGS`: the same, never on your own Claude Code folder. `--bare` starts it with none of that folder; `--history` keeps the chat for this run when the `run_history` setting is set to apart.
- `switchboard run -- ARGS`: as the account set for this folder with `switchboard folder NAME`.
- `switchboard run --roomiest -- ARGS`: the same, as the account with the most room that auto-switch is not about to leave; its first line, on standard error, names the account it picked.
- `switchboard folder NAME`: this folder runs as that account with `switchboard run`. `switchboard folder off` takes it out. `switchboard folders --prune` takes out every folder set whose folder or account is gone.
- `switchboard resume CHAT`: open a chat again as the account it started under, in this terminal; `--as NAME` as another account, `--fork` in a copy.
- `switchboard leftovers clear ID`: remove leftover logins that nothing needs. It asks first.
- `switchboard readings take [-|FILE] [--for 10m]`: show numbers from another Mac here for a while.
- `switchboard update`: install the newest version, the way it was installed. `switchboard update --port N` moves the page to port N. `switchboard update --link-app` adds the link app, `switchboard update --menu-app` adds the menu bar item and `switchboard update --no-menu-app` takes it out.
- `switchboard doctor --fix`: run now the repairs the page makes by itself every 10 minutes.
- `switchboard demo`: the page with made-up accounts, on port 7798. It changes nothing of yours, and runs until stopped: the person runs it in their own Terminal window.
- `switchboard serve`: run the page, until stopped. Its background job does this; by hand, the person runs it in their own Terminal window.
- `switchboard add PROVIDER`: add another assistant to the order, as in `switchboard add codex`.
- `switchboard remove PROVIDER`: take another assistant off the order and the list, as in `switchboard remove codex`. The app itself is not changed.
- `switchboard continue`: carry the chat in this folder on in another assistant. It asks the person first, at a terminal.
- `switchboard back`: bring a note of what was done there back to the chat you left.
- `switchboard hand PROVIDER -- JOB`: one read-only job for another assistant, in a terminal.
- `switchboard do TASK`: in a terminal, start the task in the provider the routes name for its kind, with the same yes per folder as `continue`. `switchboard route TASK` says the pick first.
- `switchboard allow PROVIDER`, `switchboard stay PROVIDER`, `switchboard order`: this folder's yes, a folder that stays on one assistant, and the order.
- `switchboard claude [args]`: Claude Code on your own API keys, in one terminal, when the gateway is on.
- `switchboard keys list` and `switchboard keys remove NAME`: the API keys by name and last four characters, or one taken out of the Keychain.

## The person's own to run

Each of these signs in, or handles a login, a token or a key. Give the person the command; never run it for them.

- `switchboard setup`: the first run, in numbered steps: it keeps the account Claude Code is on, offers to add the others through the browser, offers one real switch, and asks about links (see `first-run.md`). `switchboard setup --yes` asks nothing. Without a terminal it asks nothing and keeps the login Claude Code is on.
- `switchboard add`: add accounts, one at a time: each signs in through the browser, on Anthropic's own page, while the page runs. A login made with `/login` in Claude Code is kept by itself too. At a terminal it then offers Codex when it is signed in and not added yet: it says what pressing its row sends, and asks once.
- `switchboard add EMAIL`: sign in as that account in the browser: a new one, or one whose sign-in ended.
- `switchboard signin`: sign in again to every account that needs it, each in its own browser profile.
- `switchboard add --as SHORT --at N`: an `add` whose account gets that short name and place. Without a terminal it keeps the login Claude Code is on.
- `switchboard add --token`: keep a one-year token or an API key as an account.
- `switchboard keys add NAME`: put an API key in the Keychain.
- `switchboard export FILE`, `switchboard export --check FILE` and `switchboard export - --plain`: the accounts' logins in one file protected with a passphrase, or unprotected to standard output.
- `switchboard import FILE` and `switchboard import FILE --replace`: take those accounts in on another Mac.
- `switchboard leftovers clear ID --only-copy`: removes the only copy of a login that was in use, for good.
