# Troubleshooting

One heading per problem, with why it happens and what to do. If none fits, see [the last one](#still-stuck).

## `switchboard: command not found`

The command is in `~/.local/bin`, which is not on your PATH yet. The installer's last lines said so and printed the line that fixes it. For zsh, the Mac's usual shell:

```sh
echo 'export PATH="$PATH:$HOME/.local/bin"' >> ~/.zshrc
```

Then open a new terminal window. (For bash the line goes in `~/.bash_profile`; for fish, run `fish_add_path --append ~/.local/bin`.) The folder goes last, so the `claude` your terminal runs now stays the one it runs. Until then, `~/.switchboard/bin/switchboard` works.

## macOS asks for permission

Switchboard keeps logins in your Keychain, adds two background jobs, sends notifications, and reads Chrome's tab titles to check links. macOS may ask about any of these: choose Allow. It asks about Chrome only after you ask Switchboard to look at Chrome's tabs: Let Switchboard check links or Check on the page, or `switchboard check`. A command run in a terminal while the page is not running (`switchboard check`, `switchboard open`) reads the tabs itself, so then macOS asks about the terminal. The exact prompts have not been checked on a real Mac yet.

## The Keychain is locked, slow, or waiting for you

Switchboard reads logins from your Keychain, and it never takes a failed read to mean a login is gone.

- Locked: Switchboard changes nothing, and carries on once you unlock the Mac.
- Slow (the Mac is busy): it says so, keeps showing the last numbers with their age, and tries again shortly.
- A Keychain dialog is open: answer it. Switchboard never closes it, and carries on once you have answered.
- Over SSH, the Keychain may not answer at all: switch from a terminal on the Mac itself.

None of these marks an account as needing a sign-in.

If a switch or a run says an account's renewed login could not be written or read, nothing was changed. Switchboard is holding a newer login for that account and will not hand Claude Code the old one, which Anthropic no longer takes. Try again once you unlock the Mac, or in a minute. If it keeps happening, sign in to that account in Claude Code, and Switchboard keeps it again.

## No notifications

macOS shows Switchboard's notifications as Script Editor's. If Script Editor may not notify you, none appear: allow it in System Settings > Notifications > Script Editor.

`switchboard notify` says whether notifications are on, whether Claude Code's hooks are in and can run, and what keeps them from running (Claude Code's own `disableAllHooks`, for one). "Claude Code is done" comes only after a turn of a minute or more.

## No menu bar item

If you turned it off, run `switchboard menubar on`. Otherwise open Switchboard.app (type "Switchboard" in Spotlight), or run `switchboard menubar`. If it is behind the notch of a MacBook's screen, click the desktop so Finder is in front; when the S shows, hold Command and drag it toward the clock.

A menu bar manager (Ice, Bartender) can hide the item. On macOS 26 it can keep it in view only when Switchboard.app is in /Applications. `switchboard doctor` says whether the item is installed and running, and where its app is.

## A session, or the Claude desktop app, is still on the old account

A Claude Code session that was open during the switch moves within about 30 seconds. If it doesn't, quit it and start it again; in VS Code, start a new chat. New sessions start on the new account.

The Claude desktop app has a sign-in of its own, which a switch doesn't change. To move it, sign in to the other account inside the app.

## A session says an artifact or doc was not found

After a switch, a session works as the new account, and an artifact or doc the old account made opens only for that one. The session is told which account owns it and the commands: `switchboard open LINK` opens it in the owner's browser profile, and `switchboard run OWNER -- -p "…"` works on it as that account. You don't need to sign in again.

Auto-switch avoids this: a move that can wait never comes within 30 minutes of an artifact or doc made on the account in use, or while a session watches an artifact made on it.

## An artifact opens in the wrong browser profile, or asks you to sign in

The profile Switchboard proved for that account is now signed out, or signed in as someone else. Switchboard marks it and tries it last. On the page, the account says "No Chrome profile is signed in as this account" when none is left, and Set up opens claude.ai's sign-in page in a new profile for it. `switchboard check` proves every profile again.

## An artifact shows its title, not a picture

Its preview is made from the file Claude Code published, so it has none when that file is gone (a session's temporary folder is emptied within days), when the file changed after that publish (a session wrote the same file again for another page, or edited it; the next publish of the page gets its picture), when it had no file (an Artifact made from a type, a Claude Doc), or when Chrome is not in Applications. A Mac that is barely moving waits before making one. When Chrome can't draw a page within 30 seconds, it is tried again an hour later; after three tries that failed on a quiet Mac, a day later. Previews are made only while the page is running.

## Claude in Chrome says it isn't connected

Claude in Chrome works only when the extension's account is the one Claude Code is on. Add the extension to that account's Chrome profile (the page's Add to this profile link opens the Chrome Web Store there), and have Chrome open in that profile.

## The numbers look old

Switchboard reads the account in use every 5 minutes (about every 3 while the page is open) and the others every 5 to 15, more often near a switch line, whether auto-switch is on or off. Pressing Read again reads every account at once, except one read in the last minute. So does opening the page, when the account in use was read over a minute ago or another account over 5 minutes ago. It reads no more often than that to stay under what Anthropic allows. In a terminal with Switchboard's status line on, the account in use is live while Claude Code works. Each account shows when it was last checked.

The accounts are read only while the page's background job runs. `switchboard doctor` says whether they are being read and when they last were (its "Reading the accounts" line). If the status line is off, `switchboard statusline on` turns it on (`on --replace` if you have one of your own); Claude Code's VS Code panel shows no status line, so there the account in use is read like the others. If Anthropic turned an account's readings away for asking too often, Switchboard waits as long as it is told, up to 75 minutes, and marks the last numbers as held back.

## The page says Switchboard is busy

The Mac is too busy for the page to get a quick answer. It keeps the last numbers on screen, says how old they are, and catches up when it can. Nothing is lost.

## The page won't open

`switchboard page` says the page is not running, or the browser can't reach http://localhost:7799. Either its background job is not running, or another program holds the port. `switchboard doctor` says which. When another program holds it, auto-switch and the readings still run, the menu bar item says which port, and the page opens by itself once the port is free; to use another port: `switchboard update --port 7801`. Otherwise the menu bar item starts the page again within a few minutes, or start it by hand with `switchboard serve`.

While the page waits for its port, a command that changes something says so in one line, makes the change itself, and ends with the same `switchboard update --port` line. `switchboard signin` changes nothing then: Sign in to all runs on the page.

## A command says the page serves another data folder

A command that changes something checks first that the page on its port keeps the same accounts it does. When the page serves another data folder (a command run with its own `SWITCHBOARD_HOME`), is the demo, or is from an older Switchboard that does not say, it changes nothing and says so in one line, which ends with the one thing to do: for another folder, run the command with the page's data folder or give it its own port (`SWITCHBOARD_PORT`); for the demo, stop it and run the command again; for an older page, restart it.

## The page says Claude Code is not installed, and it is

The page's background job looks on its own short PATH, then where a terminal last found `claude`, then in the usual places. Run `switchboard doctor` once in a terminal where `claude` works: Switchboard remembers that path for the page, as long as the file and its folder are yours and nobody else can write to them.

## Auto-switch didn't switch

It may be off, the page may not be running (auto-switch runs under it), no other account may have room under its lines, or the disk may have under 50 MB free (the page says so). Or the move could wait, and is waiting: for Claude Code to be idle for 5 minutes, for 30 minutes after an artifact or doc was made, for 6 hours after a session that watches an artifact goes quiet, or for 45 minutes after the last switch. The page says what it is waiting for.

- `switchboard auto` says whether it is on, at which lines, and in which order.
- `switchboard log` shows each switch and why, and each limit reached.
- `switchboard doctor` says whether automatic switching is on, and whether the accounts are being read and when they last were.

## Another program is also switching Claude Code's login

Two programs that both write Claude Code's login undo each other's switches and leave each other's copies stale. Switchboard names the other program's login item. Turn that program off, then Switchboard carries on.

## An account asks for a sign-in again

Switchboard renews your logins by itself. Anthropic ends each sign-in by a set date, though, and renewing does not move it: only signing in again does. That date is the latest it ends: a sign-in can end any time in the three days before it. A week before the date, the account's row on the page says when ("Sign-in ends by Wed 1:15 AM", why on hover), and so do the menu bar, `switchboard` and `switchboard doctor`; a notification comes 3 days and 1 day before the earliest it can end. Claude Code shows the same for the account it is on: `claude auth status --text`. Sign in again before the earliest date and nothing stops. Press Sign in to all, in the line above the accounts (it shows while a sign-in has ended or ends within the week): it signs in again to each account that needs it, one after another, each in its own browser profile, and you approve each in the tab that opens. Its hover names each account it opens, in that order. Sign in on a row does the same for one account. The account Claude Code is on signs in the same way, and Claude Code moves to its new sign-in at once.

Once the date has passed ("Sign-in ended by Fri 8:38 PM"), or when Anthropic refused its saved login, the account needs a fresh sign-in. Press Sign in on its row, or sign in to it with `/login` in Claude Code, and Switchboard keeps the new login. In a terminal, `switchboard` and its number tell you the same.

## Claude Code lost its sign-in

When many open chats save Claude Code's Keychain item at once, the item can be left without the login. Switchboard puts the same account's saved login back within seconds and says so. When it can't (the account a switch just left, no usable saved login for it, or four times already in an hour), a notification says so after 5 minutes: open Switchboard and press Use on an account, or Sign in on the one Claude Code was on. When Switchboard can't write Claude Code's login at all, run `/login` in Claude Code instead. A chat that showed the sign-in screen meanwhile may need that screen cancelled.

## Switchboard says it restored a file

Switchboard keeps the last good copies of its list of accounts and its settings. When one is damaged (half written, for one), it is brought back from its newest good copy, the damaged one is kept beside it, and History, the page and `switchboard doctor` say so for a day. Look over your accounts and settings then. `switchboard doctor --fix` runs the same repairs now.

A list of accounts written by a newer Switchboard is not damage, and it is never replaced. An older Switchboard that meets it changes nothing and says so. Update that one: `switchboard update`.

## A switch was cut off part-way

A crash, a kill or a power cut stopped a switch halfway. The next time Switchboard starts, it finishes or undoes that switch before anything else, from what Claude Code's login really holds, and History says which.

## The install stopped halfway

Most often the internet dropped: the installer downloads Python and packages once. Run it again. What is in place is kept, so a second run is quick. `switchboard doctor` names anything still missing, and the details are in `~/.switchboard/install.log` (Switchboard's own Python) and `~/.switchboard/menubar-install.log` (the menu bar item's packages).

## The uninstaller says something could not be put back

When something Switchboard changed in Claude Code can't be put back, the uninstaller names it, keeps the program so it can run again, and exits with an error. Fix that, then run it again.

Three ways it happens: Claude Code's settings file can't be read, you edited inside a block Switchboard added to CLAUDE.md, or a sign-in was under way and the Keychain would not give its login up. For the last, the Keychain was locked or macOS was waiting for your answer: see to that first. Run it again the same way: `sh ~/.switchboard/app/uninstall.sh`, with `--all` if you used it the first time.

When the uninstaller says Switchboard could not start (its Python or its folder is gone), it removes everything else and leaves Claude Code's files as they are. To put them back by hand:

- In `~/.claude/settings.json`, take out `statusLine` when its command ends with `# switchboard statusline`, and every hook whose command ends with `# switchboard notify` or `# switchboard sessions`. If you turned on Sonnet for helpers, take `CLAUDE_CODE_SUBAGENT_MODEL` out of `env`.
- In `~/.claude/CLAUDE.md`, take out each block between a `<!-- switchboard …: start` line and its `end` line.
- `settings.json.switchboard-backup`, beside the settings file, is the file as it was before Switchboard's last change.

claude-mem and codegraph stay installed when a switch installed them.

## Still stuck

Run `switchboard doctor` and [open an issue](https://github.com/amyjainberkeley/switchboard/issues) with what it printed. It shows no login or key: a key shows only as its name and last four characters. It does show folder paths on your Mac and your accounts' email addresses, so read it before you send it.

## Secure connections fail on a company network

Some networks inspect secure connections: a proxy shows its own certificate, signed by the company's root certificate. Switchboard trusts Python's certificates, the root certificates this Mac trusts for secure connections (those you or an administrator marked as trusted in Keychain Access), and the files named in `SSL_CERT_FILE` and `NODE_EXTRA_CA_CERTS`, the variable Claude Code reads for the same thing. When a connection is refused, the message names the issuer that was not trusted.

Add the company's root certificate to the Keychain and set it to Always Trust. The page runs in the background, where variables set in your shell are not seen, so the Keychain is the way that reaches it too. Each Switchboard process asks macOS before its first secure connection, and again when a keychain changes, and at least once a day; what macOS answers is kept only in memory, so no file can add a trusted certificate. With the setting `network_trust` set to `python` (`switchboard config set network_trust python`), the Keychain is not read. Nothing turns off the check of a certificate or of the name it is for.
