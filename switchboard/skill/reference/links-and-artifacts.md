# Links and artifacts

## Why an artifact opens only for one account

A claude.ai artifact (or a Claude Doc) that is not shared opens only for the account that published it; any other account sees "Page not found". So after a switch, a page made on the old account does not open in the new account's browser, and a session now on the new account gets "not found" for it. A switch moves Claude Code only: the browser, each Chrome profile and the Claude desktop app keep their own sign-ins.

## What Switchboard does about it

- It knows each artifact's account from when it was published: the session hooks note each publish, and Switchboard knows which account Claude Code was on then. A page published within 90 seconds of a switch is only a guess until the first open checks it.
- `switchboard open LINK` opens it in the Chrome profile signed in as the account that owns it. One click on its tile in the page's Artifacts does the same. `switchboard owner LINK` names that account without opening anything; `switchboard pages` lists every artifact this Mac knows.
- Switchboard Links, the link app: once macOS makes it the default browser, a claude.ai artifact clicked anywhere opens as the account that owns it, and every other link goes to the browser used before. `switchboard links` says whether it is on; `switchboard links on` asks macOS (the person answers), `switchboard links off` gives the seat back. It needs Apple's command line tools: `switchboard update --link-app` adds it later.
- A Claude Code session that gets "not found" is told which account owns the page and the commands: `switchboard open LINK` to see it, and `switchboard run OWNER -- -p "…"` to work on it as that account. It is never told to sign in.
- Auto-switch holds a move that can wait for 30 minutes after an artifact or doc was made on the account in use, and while a chat watches one made on it.

## It opens in the wrong profile, or asks to sign in

The profile Switchboard proved for that account is now signed out, or signed in as someone else. Switchboard marks it and tries it last. The page says "No Chrome profile is signed in as this account" when none is left, and Set up opens claude.ai's sign-in page in a new profile for it. `switchboard check` proves every profile again; macOS may ask for permission to read Chrome's tabs. Links are tested with Chrome only.

## Making a page open for every account

Sharing the artifact on claude.ai is the person's own step there; Switchboard does not share or unshare anything.

## A tile shows a title, not a picture

The preview is made on this Mac from the file Claude Code published. There is none when that file is gone or changed after the publish, when the page had no file (a Claude Doc), or when Chrome is not in Applications.
