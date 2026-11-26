# Security

Switchboard moves Claude Code's logins, so a security problem in it matters. Please report one privately.

## Report a problem

Don't open a public issue. Report it privately on GitHub: [Report a vulnerability](https://github.com/amyjainberkeley/switchboard/security/advisories/new). Only you and the maintainers see the report. You will get an answer within 7 days.

Say what you found, how to make it happen, and the version (`switchboard version`).

## Versions

Only the newest version gets fixes. `switchboard update` installs it.

## What counts

- How logins are kept: Switchboard's items in your Keychain (`Switchboard login`, `Switchboard login (previous)` and `Switchboard recovery`), and its list of accounts, `~/.switchboard/accounts.json`, which must never hold a login.
- How a switch writes Claude Code's login and the account fields of `~/.claude.json`, when those fields are written again outside a switch, when a lost sign-in is put back into Claude Code's Keychain item, and how a login is renewed.
- `switchboard run`'s temporary folder and Keychain item, which must be gone when the command ends.
- What Switchboard writes into Claude Code's settings and CLAUDE.md: the status line, the hooks, Sonnet for helpers, house rules, the code map and claude-mem's settings.
- The page's server on 127.0.0.1, which must answer only its own page.
- Previews: a picture of your own page, made on this Mac in headless Chrome (a temporary profile, removed after) from the file Claude Code published, kept in `~/.switchboard/previews`, which only you can read, and served only to the page, for an artifact it lists.
- The readings log, `~/.switchboard/readings.jsonl`: each account's email and limit percents over the last 8 days, which only you can read.
- The keys gateway on 127.0.0.1, and its refusal of subscription logins.
- The accounts file `switchboard export` writes: how a passphrase makes its keys, how the file is protected and checked before it is opened, how the passphrase is read (at a terminal, from a file only you can read, or from a descriptor, never from an argument or the environment), that the file is yours alone from its first byte, what `switchboard import` takes from a file and never puts over a newer login, and `switchboard export - --plain`, which prints the logins unprotected on standard output.
- Numbers taken from another Mac (`switchboard readings take`), which are shown and never decided on.
- What secure connections trust: the roots macOS says this Mac trusts, asked with `/usr/bin/security` in each process, and `~/.switchboard/mac-trust.json`, which names only which certificates to ask about and can never add one.
- The look for a newer version: what it sends, and that it is off until you turn it on.
- The installer, what it downloads and how it checks it, and the uninstaller.
- The commands for other assistants: the note files in `~/.switchboard-notes` (only you can read them), how anything that looks like a secret is taken out of a note, the programs Switchboard starts (Codex, Claude Code) and the environment they get, and the rule that nothing goes to another company's app without your yes for that folder, given at a terminal. To write where things stand in a note, Switchboard runs git in the folder, with the folder's hooks and its file monitor turned off; a folder's own git settings can still make git start a program, as they can when you or your editor run git there, so carry on only from folders you trust. To know which Claude account is in use, the Claude provider reads the `active` key of Switchboard's own `~/.switchboard/state.json`, which never holds a login.

A problem in claude-mem, codegraph or Claude Code itself belongs to that project.

## Known, not fixed

- Any program on this Mac can reach the page's server on 127.0.0.1 and do what the page does: see your accounts' addresses and limits, switch, remove an account. That includes a program run by another user of this Mac, or one inside a container that can reach the Mac's own address. Other websites cannot.
- Switchboard hands each login it writes to macOS's `security` tool on its standard input, as Claude Code does, so it doesn't show in the process list. A login too long for that (a command line over 4,032 bytes, as when Claude Code's own item holds many MCP server logins) goes as an argument, hex-encoded, as Claude Code does too: anyone on this Mac who lists processes (`ps`) while that call runs can see it. Any program running as you can already read these items through the same tool.
- The program's own download, from GitHub, is not checked against a checksum yet. Everything else the installer downloads is.
- An accounts file is as strong as its passphrase. Switchboard asks for 8 characters at least when it makes one, and checks nothing else.
