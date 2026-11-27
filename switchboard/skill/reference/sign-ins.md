# Sign-ins

## Renewals, and the date a sign-in ends

Switchboard renews each account's login by itself, so an account keeps working without the person. Anthropic ends each sign-in by a set date, though, and renewing does not move that date: only signing in again does. The date is the latest it ends: a sign-in can end any time in the three days before it.

Switchboard shows that date a week ahead: under the account on the page ("Sign-in ends by Wed 1:15 AM"), in the menu bar, in `switchboard` and in `switchboard doctor`. A notification comes 3 days and 1 day before the earliest it can end. Until then the account works as usual, auto-switch included. Claude Code shows the same for the account it is on with `claude auth status --text`.

Promise no more than this: Switchboard renews the logins, and each sign-in still ends by its date.

## Signing in again

Signing in is the person's own: never start, script or complete it for them. Tell them one of these:

- The page: Sign in to all, in the line above the accounts (it shows while a sign-in has ended or ends within the week). It signs in again to each account that needs it, one after another, each in its own browser profile, and the person approves each in the tab that opens. Sign in on one account's row does the same for that account.
- The terminal: `switchboard signin` does what Sign in to all does. `switchboard add EMAIL` signs in again as that one account, in the browser. The page must be running.
- The menu bar item's menu: Sign in again on that account, or Sign in to all.
- A `/login` in Claude Code as that account works too: Switchboard keeps the new login.

Accounts signed in together end together, so the next time is again one press. The account Claude Code is on signs in the same way, and Claude Code moves to its new sign-in at once.

## A sign-in that ended

Once the date has passed ("Sign-in ended by Fri 8:38 PM"), or when Anthropic refused the saved login, the account needs a fresh sign-in: Sign in on its row, `switchboard add EMAIL`, or a `/login` in Claude Code as that account. Auto-switch passes over an account that needs a sign-in. An account is never marked as needing a sign-in because the Keychain was slow or locked.

## Claude Code lost its sign-in

When many open chats save Claude Code's Keychain item at once, the item can be left without the login. Switchboard puts the same account's saved login back within seconds and says so ("Claude Code lost its sign-in. Switchboard put … back."). When it can't, a notification says so after 5 minutes: on the page, press Use on an account, or Sign in on the one Claude Code was on. When Switchboard can't write Claude Code's login at all, the person runs `/login` in Claude Code.

## The Keychain

Logins stay in the login Keychain. When it is locked, Switchboard changes nothing, and carries on after they unlock the Mac. When a Keychain dialog is open, the person answers it; Switchboard never closes it. Never read the Keychain yourself.
