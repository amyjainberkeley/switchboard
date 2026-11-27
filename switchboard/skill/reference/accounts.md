# Accounts

An account is one Claude login, kept in the Keychain of this Mac. Every surface names an account by its whole email: the page, the menu bar, the terminal, the status line and the notifications.

## Adding one

- `switchboard add`, at a terminal: each account signs in through the browser, on Anthropic's own page in a new browser profile, and the person approves it there. The login Claude Code is on is not touched. It asks whether there is another, and says when the browser came back as an account that is already here. The page must be running. `switchboard add --as SHORT --at N` gives the first account it keeps a short name and a place in the list.
- `switchboard add EMAIL`: the same for one account, by its email (a new one, or one whose sign-in ended).
- Add account on the page, or in the menu bar item's menu (it opens the page's Add form): the same from the browser.
- The first run (`switchboard setup`) offers the same in its second step.
- A login the person makes with `/login` in Claude Code is kept by itself too, while the page runs; a notification says so. Claude Code moves to that account, as `/login` always does.
- A Team or Enterprise seat is added like any other account; its plan shows as Team or Enterprise.
- A one-year token (what `claude setup-token` prints) or a Claude Console API key can be kept as an account with `switchboard add --token`. It starts kept out of auto-switch.

Signing in is always the person's own: never start or complete it for them. Once a second account is in, auto-switch turns on by itself unless the person turned it on or off before.

## Removing one

- `switchboard remove NAME` forgets the account: its saved login leaves the Keychain. Claude Code stays on the login it has until a switch.
- An account removed stays out: a `/login` to it in Claude Code does not bring it back. `switchboard add` does.
- Signing out of Claude Code with `/logout` does not take an account out of Switchboard.

## Order and names

- The list has an order: `switchboard move NAME N` puts an account at place N (1 is first), `switchboard swap NAME NAME` swaps two. A number is a place in the list and changes when the list changes, so a script names an account by its email or short name.
- `switchboard alias NAME WORD` gives a short name to type, such as `work`, in place of a number or an email. It is only something to type.
- When two accounts share an email (one person in two organizations), each shows its organization after the email, and a command given that email says it fits two accounts.
- Auto-switch has its own order of preference: see `auto-switch.md` (ranks, used last, kept out).

## Which account am I on

- `switchboard current` prints the whole email of the account Claude Code is on.
- `switchboard status --json` has it as `in_use`, with `next`, the account auto-switch would move to.
- The page's header, the menu bar item and the status line in Claude Code show it too.
- Why it moved: `switchboard log` (each switch with its reason) and `switchboard auto why` (what it would do now).

## Using an account without switching

`switchboard run NAME -- -p "…"` runs one Claude Code command as that account; every other session keeps the login it has. `switchboard folder NAME` makes `switchboard run` in this folder use that account. See `claude-code.md`.

## Moving to another Mac

`switchboard export FILE` writes the accounts' logins to one file protected with a passphrase, and `switchboard import FILE` takes them in on the other Mac. Both are the person's own to run. A login carried this way is one sign-in in two places: once one Mac renews it, the copy on the other stops working at its next renewal and asks for a fresh sign-in. On a Mac kept in use as well, sign in there again.
