# Privacy

## Does it send my chats anywhere? Can anyone else see them?

- Switchboard has no service or database of its own on the internet. What it keeps is in `~/.switchboard` on this Mac, which only the person can read. No telemetry.
- It reads Claude Code's chat files on this Mac, to show each chat's account and title and for the chat search, and sends none of it anywhere itself. The chat search keeps an index of the person's prompts and Claude's text replies, with chat titles, folders and dates, in `~/.switchboard`: readable only by the person, never sent, built the first time they open Past chats on the page; tool output and helpers' chats are left out. History keeps no prompt, chat text, login or key.
- Nothing of a chat leaves the Mac through Switchboard unless the person carries a chat on, or hands a job, to another assistant (`switchboard continue`, `switchboard hand`): then a note goes to that assistant's maker, only after their yes for that folder.
- claude-mem, only when turned on in the page's Settings, is different: its model works on the chats through the person's own Claude login.
- The page opens only for the person: Switchboard hands their browser a link that works once, and the page keeps a key in that browser's storage for its own address. Another program on this Mac, including one run by another user of this Mac, can only read the page's first screen (the accounts, their limits and the artifacts listed, never a login, a key or a chat's words) and ask it to open a claude.ai artifact link or set up a new browser profile for an account (which changes that account's profile link). Other websites cannot reach it. SECURITY.md says exactly what.

## What leaves the Mac

- To Anthropic: each account's limits, its login's renewal, and a lookup of which account a login belongs to, at the addresses Claude Code uses, under Switchboard's own name.
- To OpenAI, through Codex: a note or a job the person asked for, and Codex's own request for its limits with `switchboard providers --ask`.
- To a key provider the person added: the requests sent through the keys gateway.
- A look for a newer version: only with `switchboard update --check`, or once a day when turned on (`switchboard config set update_check daily`). It asks only the place an update would come from, for its version number.
- For a preview of a published page: that page's own scripts, styles and fonts, from the code and font sites claude.ai allows.
- Nothing else, besides the installer's downloads.

## What it keeps

- Logins and API keys: in the login Keychain, never in Switchboard's files (except a recovery file only the person can read, when the Keychain refused one, until the login is safely kept). Its files keep only a fingerprint of each login, and each key's name and last four characters.
- Claude Code's own sign-in, also in its plain login file (`~/.claude/.credentials.json`, which only the person can read): an exact copy of its Keychain item, so a chat whose read of the Keychain gives nothing on a busy Mac still finds it. It follows each renewal, switch and put-back, is never made from a blank or refused login, and goes at a sign-out and at uninstall. It is a plain file, so a backup of the home folder (Time Machine) copies it as it is. `auto_policy.keep_signin_file` set to false in `~/.switchboard/settings.json` keeps it only for about 35 seconds after a put-back.
- In `~/.switchboard`: settings, each account's email, organization, name and place, History, each account's numbers for 8 days, the chat search's index (the person's prompts and Claude's text replies), artifact links and small pictures of the pages.
- In `~/.switchboard-notes`: notes of chats carried on to another assistant (30 days) and job answers (7 days).

## What it changes on the Mac

- `~/.switchboard`, the `switchboard` command in `~/.local/bin` (with Homebrew, in Homebrew's bin folder) and `sb` beside it, Switchboard.app in Applications, Switchboard Links in `~/Applications`, and three background jobs that start at login: the page, the sign-in watch (it notices within a second when Claude Code's sign-in changes) and the menu bar item.
- The default browser, only when the person says yes to macOS: Switchboard Links, which sends every link but a claude.ai artifact to the browser used before.
- At each switch: Claude Code's own login in the Keychain, its plain login file (above) and the account named in `~/.claude.json`.
- Claude Code's settings: the status line, three notification hooks and the session hooks.
- This skill: `skills/switchboard` in Claude Code's folder, and `~/.agents/skills/switchboard` when Codex is on the Mac.
- While Claude Code works, it keeps the Mac awake (`"awake": false` in `~/.switchboard/settings.json` turns that off).
- Only when turned on in the page's Settings: house rules and the code map (a short block in `~/.claude/CLAUDE.md`), Models by the job, Sonnet for helpers, claude-mem.

`switchboard about` prints, each checked now, what this install put where, what runs, and what leaves the Mac. `switchboard doctor` lists each part and whether it is in. `update-and-remove.md` says how each comes out.
