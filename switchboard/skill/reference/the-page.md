# The page

## Opening it

- `switchboard page` opens it in the browser.
- Or open http://127.0.0.1:7799 in a browser that has opened it from Switchboard before (the default port; `switchboard update --port N` moves it, and `switchboard doctor` names the address in use). Anywhere else the page shows one line: open it with Open Switchboard in the menu bar, or `switchboard page`. It opens only for the person.
- Or Open Switchboard in the menu bar item's menu, or open Switchboard.app (Spotlight finds it): when the menu bar item is already running, or was turned off with `switchboard menubar off`, that opens the page.

It listens on this Mac only and refuses requests from other websites. A background job keeps it running and starts it at login.

## What it shows

A sidebar holds Search, Accounts, Chats, Artifacts, Activity and Settings. Each view has its own address (`#accounts`, `#week`, `#chats`, `#artifacts`, `#activity`, `#settings`), so Back works and a link opens a view.

- Accounts: the account Claude Code is on and when it was last checked (press it, or R, to read every account now). A row per account: its whole email, its plan, its limits one under another, each with a bar and when it refills, and In use, Use or Next. A row that needs the person shows one button, Sign in or Set up. Above the accounts is the switch line: Auto-switch at 90% 5-hour, 95% weekly, which the person can change there (50 to 99.9).
- Your week, under the accounts: the last 7 days of Claude Code use by hour. See `your-week.md`.
- Chats: the chats of the last 7 days, each under the account it started under, and where it is now when it moved. Reopen at home opens a chat in Terminal with `switchboard resume`.
- Artifacts: a folder per account, each artifact a tile; one click opens it as the account that owns it.
- Activity: History (what happened) and Past chats (a search of chats on this Mac).
- Settings: grouped by what each is for. Each shows its command where one exists; the last line is the version and the page's address.

Keys: ⌘K finds a view, an account, an artifact or a setting; R reads the limits again; `?` lists the keys.

## When it will not open

`switchboard page` says the page is not running, or the browser can't reach it. `switchboard doctor` says which: its background job is not running, or another program holds the port. When another program holds it, auto-switch and the readings still run, and the page opens by itself once the port is free; another port: `switchboard update --port N`. Otherwise the menu bar item starts the page again within a few minutes, or the person runs `switchboard serve` in their own Terminal window (it runs until stopped). When the Mac is busy, the page keeps the last numbers and says how old they are.
