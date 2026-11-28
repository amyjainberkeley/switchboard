# Security

Switchboard moves Claude Code's logins, so a security problem in it matters. Please report one privately.

## Report a problem

Don't open a public issue. Report it privately on GitHub: [Report a vulnerability](https://github.com/amyjainberkeley/switchboard/security/advisories/new). Only you and the maintainers see the report. You will get an answer within 7 days.

Say what you found, how to make it happen, and the version (`switchboard version`).

## Versions

Only the newest version gets fixes. `switchboard update` installs it.

## What counts

- How logins are kept: Switchboard's items in your Keychain (`Switchboard login`, `Switchboard login (previous)`, `Switchboard recovery` and `Switchboard login (Mac-wide parts)`), the copy of Claude Code's sign-in it keeps in Claude Code's plain login file (owner-only; `auto_policy.keep_signin_file`), and its list of accounts, `~/.switchboard/accounts.json`, which must never hold a login.
- How a switch writes Claude Code's login and the account fields of `~/.claude.json`, when those fields are written again outside a switch, when a lost sign-in is put back into Claude Code's Keychain item, and how a login is renewed.
- `switchboard run`'s temporary folder and Keychain item, which must be gone when the command ends.
- What Switchboard writes into Claude Code's settings, its folders and CLAUDE.md: the status line, the hooks, Models by the job's four helper files in `~/.claude/agents/` and its note in `~/.claude/rules/`, Sonnet for helpers, house rules, the code map and claude-mem's settings.
- The page's server on 127.0.0.1, which must answer only its own page.
- Previews: a picture of your own page, made on this Mac in headless Chrome (a temporary profile, removed after) from the file Claude Code published, kept in `~/.switchboard/previews`, which only you can read, and served only to the page, for an artifact it lists.
- The readings log, `~/.switchboard/readings.jsonl`: each account's email and limit percents over the last 8 days, and for an account it still reads, its last 5-hour percent however old, which only you can read.
- The keys gateway on 127.0.0.1, and its refusal of subscription logins.
- The accounts file `switchboard export` writes: how a passphrase makes its keys, how the file is protected and checked before it is opened, how the passphrase is read (at a terminal, from a file only you can read, or from a descriptor, never from an argument or the environment), that the file is yours alone from its first byte, what `switchboard import` takes from a file and never puts over a newer login, and `switchboard export - --plain`, which prints the logins unprotected on standard output.
- Numbers taken from another Mac (`switchboard readings take`), which are shown and never decided on.
- What secure connections trust: the roots macOS says this Mac trusts, asked with `/usr/bin/security` in each process, and `~/.switchboard/mac-trust.json`, which names only which certificates to ask about and can never add one.
- The look for a newer version: what it sends, and that it is off until you turn it on.
- The installer, what it downloads and how it checks it, and the uninstaller.
- The commands for other assistants: the note files in `~/.switchboard-notes` (only you can read them), how anything that looks like a secret is taken out of a note, the programs Switchboard starts (Codex, Claude Code) and the environment they get, and the rule that nothing goes to another company's app without your yes, given at a terminal: for that folder, or, for one move, pressing that provider's row in the list, once Switchboard has said what that press sends, to whom and under whose terms (adding the provider at a terminal says it; otherwise the first press says it and asks once, before any chat is read). Continue here in the menu bar or on the page asks no yes because it sends nothing: it opens that app with the note in its message box, for you to send there; for a provider whose press was never explained, it makes no note and sends you to the list first. `switchboard remove codex` takes Codex off the list. To write where things stand in a note, Switchboard runs git in the folder, with the folder's hooks and its file monitor turned off; a folder's own git settings can still make git start a program, as they can when you or your editor run git there, so carry on only from folders you trust. To know which Claude account is in use, the Claude provider reads the `active` key of Switchboard's own `~/.switchboard/state.json`, which never holds a login.

A problem in claude-mem, codegraph or Claude Code itself belongs to that project.

## Who can talk to the page

The page's server listens on this Mac only (127.0.0.1) and answers only you:

- Your own browser, opened by Switchboard: Open Switchboard in the menu bar, or `switchboard page`. Switchboard writes a small file in `~/.switchboard/page-open/` that only you can read, holding a link that works once, within a minute, and hands that file to the browser. The page's script trades that link for the page key and keeps the key in the browser's own storage for `http://127.0.0.1:<port>` (never the name localhost, which may lead elsewhere) and nowhere else. It is never a cookie: the browser never sends it by itself, and a server on another port can't read it. The page sends it in a header with each request. Only a used link is ever in the browser's History.
- Switchboard's own programs (the `switchboard` command, the menu bar item), which send in that header a proof made from the key for that one request: good once, for that route, within a minute, never the key itself.
- Not another website: requests another website starts are refused.
- Not another program or another user of this Mac, except for the things listed below. Every other change and reading needs the key. Without it the page shows only a line that says how to open it.

What a program on this Mac can still do without the key:

- Read `/api/state`, which the menu bar item and status checks read. It holds: the version and build, whether it is the demo, and a mark of the data folder; each account's address, name, alias, plan and organization, limits and when they were read, when its sign-in ends, its browser profile's name and how many chats it has open; which account is in use and which is next, auto-switch's settings and plan, the forecast and the timeline of switches; the recent events and History lines; the artifacts the page lists, with their addresses and titles; the names of your browser profiles; the names, never the secrets, of kept tokens and keys; the state of the browser, the link app, the engine, sign-ins under way, setup, notifications, the status line, the keys gateway, the disk, the Mac's load and repairs; and the other providers on the board and their rows. It never holds a login, a key, the install's secret, this Mac's account name or the words of a chat, and another provider's row there names no chat.
- Ask the page to open a claude.ai artifact link (`/api/go`): it opens in the browser profile of the account that owns it. The link app sends links this way, and so does the page a link or the "Open as its owner" bookmark lands on, in browser profiles that don't hold the key. Such a caller is told only whether the page was checked; never which account owns it, nor the page's title.
- Ask the page to set up a new browser profile for an account (`/api/setup-profile`, the Set up button on that same page): Switchboard makes the profile, changes the account's profile link to it and opens claude.ai's sign-in there. Such a caller is told only that it opened.

Apart from that profile link, none of these can switch an account, remove one, sign in, change a setting or read anything else.

The key is made from the install's own secret, the `key` in `~/.switchboard/settings.json`, which only you can read. To make a new one, run `switchboard page --new-key`: every browser then has to open the page again from Switchboard, and the "Open as its owner" bookmark has to be added again from the page.

## Known, not fixed

- Any program on this Mac, including one run by another user of this Mac or one inside a container that can reach the Mac's own address, can read the page's state (your accounts' addresses and limits, and the rest listed under "Who can talk to the page"), ask it to open a claude.ai artifact link, and ask it to set up a new browser profile for an account. Everything else needs the page key. Other websites cannot do any of these.
- A program that takes the page's own port while Switchboard is not running is, to your browser, the page: an open page tab sends it the key with its next reading, and a page opened then can read the key the browser keeps. A Switchboard command or the menu bar item would hand it only a proof, which it could use once, on that route, within a minute. `switchboard page --new-key` makes the old key worthless.
- Switchboard hands each login it writes to macOS's `security` tool on its standard input, as Claude Code does, so it doesn't show in the process list. A login too long for that (a command line over 4,032 bytes, as when Claude Code's own item holds many MCP server logins) goes as an argument, hex-encoded, as Claude Code does too: anyone on this Mac who lists processes (`ps`) while that call runs can see it. Any program running as you can already read these items through the same tool.
- The program's own download, from GitHub, is not checked against a checksum yet. Everything else the installer downloads is.
- An accounts file is as strong as its passphrase. Switchboard asks for 8 characters at least when it makes one, and checks nothing else.
