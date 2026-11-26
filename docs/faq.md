# FAQ

## Is using several accounts allowed by Anthropic's terms?

Switchboard makes no claim either way. Read Anthropic's [consumer terms](https://www.anthropic.com/legal/consumer-terms) and Claude Code's [legal and compliance page](https://code.claude.com/docs/en/legal-and-compliance), and decide for yourself. One rule from that page is built in: the gateway refuses any request that carries a subscription login.

## Is using Codex through Switchboard allowed by OpenAI's terms?

Switchboard makes no claim either way. Read OpenAI's [terms of use](https://openai.com/policies/terms-of-use/) and decide for yourself. `switchboard continue` and `switchboard back` start Codex itself, as you would, on its own sign-in. `switchboard hand` runs Codex's exec mode on your ChatGPT plan, and asks you once on this Mac before its first job. Switchboard does not switch between ChatGPT accounts: it uses the one Codex is signed in to.

## Is it free?

Yes. It is open source, under the MIT license.

## Is it made by Anthropic?

No. Switchboard is not affiliated with Anthropic or OpenAI. Claude is a trademark of Anthropic. Codex and ChatGPT are trademarks of OpenAI. `LICENSE` names the author.

## Can I use an account without switching to it?

Yes, one command at a time: `switchboard run work -- -p "…"` runs Claude Code as that account, and every other session keeps the login it has. [How it works](how-it-works.md#running-as-another-account).

## Why didn't it switch the moment another account had more room?

Because a switch has a cost: an open session moves, and an artifact or doc it just made stays with the old account. So auto-switch moves at once only when it must, such as at a limit. Any other move waits until Claude Code is idle, and for a real gain. [Auto-switch](how-it-works.md#auto-switch) has the rules.

## Does it work with a Team plan, API keys or Amazon Bedrock?

- A Team or Enterprise seat is added like any other account, and its plan shows as Team or Enterprise.
- Your own API keys work as a fallback for when every account is out: see [keys and the gateway](how-it-works.md#keys-and-the-gateway).
- A cloud provider Claude Code is told to use (Amazon Bedrock, Google Cloud's Agent Platform, Microsoft Foundry and the others it names): a switch changes nothing for a session on one, and `switchboard doctor` says when one is set. `switchboard run NAME` leaves the provider's variable out of that one command, so it runs on the account ([Anthropic's docs](https://code.claude.com/docs/en/authentication)).

## Does the Claude desktop app switch too?

No. It has a sign-in of its own; to move it, sign in to the other account inside the app. A switch moves Claude Code: [what follows a switch](how-it-works.md#what-follows-a-switch).

## Why do notifications come from Script Editor?

Switchboard shows them through macOS's `osascript`, so macOS names them as Script Editor's. If none appear, see [troubleshooting](troubleshooting.md#no-notifications).

## Why is there a gateway?

For when every account is out: it runs Claude Code on your own API keys, in one terminal, while plain `claude` stays on your accounts. It is off until you turn it on.

## Does it read my chats?

Switchboard reads them only on this Mac, and sends none of it anywhere itself. `switchboard continue` and `switchboard hand` write a note of one chat, or send one job, to the assistant you name, only after you say yes for that folder: that assistant's app reads it. The chat search indexes what you and Claude wrote. History's Work reads Claude Code's history for numbers, models and chat titles, and Keep Mac awake only for timing. claude-mem, if you turn it on, is different: its model works on your chats through your own Claude login.

## Does it change my default browser?

Only when you say yes to macOS. The link app opens each claude.ai artifact as its owner and sends every other link to the browser you used before, so the first run asks macOS once to make it your default browser, and the page offers it until it is. `switchboard links off` gives the seat back, and so does uninstalling; macOS asks which browser gets it.

## Can I see it before I install it?

Yes, from a copy of the repository: `python3 -m switchboard demo` opens the page with made-up accounts and changes nothing. It needs Python 3.9 or newer.

## What does it need?

macOS with Claude Code, and the internet once for the install. No administrator. Apple's command line tools only for the link app; without them, everything else works. The installer handles Apple silicon and Intel Macs; it has been tried on Apple silicon only.
