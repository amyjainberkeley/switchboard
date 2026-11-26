# Credits

## What Switchboard runs on

The installer downloads these once, each checked against a pinned SHA-256.

- Python, from [python-build-standalone](https://github.com/astral-sh/python-build-standalone) (MPL-2.0), when the Mac has no Python Switchboard can use.
- [PyObjC](https://github.com/ronaldoussoren/pyobjc) (MIT), its core and Cocoa packages: the menu bar item draws with it.

The rest of Switchboard's Python, the account engine included, uses only the standard library.

The page's typeface is [Geist](https://github.com/vercel/geist-font) (SIL Open Font License 1.1). It ships inside the page; its licence is in `switchboard/web/Geist-OFL.txt`.

The page's icons are [Lucide](https://github.com/lucide-icons/lucide)'s (ISC; some come from Feather, MIT), drawn inline; their licence is in `switchboard/web/Lucide-LICENSE.txt`. Its colours follow the zinc set of [shadcn/ui](https://github.com/shadcn-ui/ui) (MIT).

## What the switches download

Only after you agree to the plan each one shows.

- [claude-mem](https://github.com/thedotmack/claude-mem) 15.18.3 (Apache-2.0): its switch runs claude-mem's own installer, sets its settings, and takes out exactly what it added. Its code is not copied. After the checked package, claude-mem's installer fetches its own npm packages, and its search fetches Python packages through uv: Switchboard pins neither.
- [codegraph](https://github.com/colbymchenry/codegraph) v1.6.2 (MIT): the code map switch downloads its pinned release, checks it, and connects it to Claude Code only. [graphify](https://github.com/Graphify-Labs/graphify) (Apache-2.0) is used when it is already on the Mac.
- When the Mac has none, the claude-mem switch downloads [Node.js](https://nodejs.org) (MIT), [Bun](https://github.com/oven-sh/bun) (MIT) and [uv](https://github.com/astral-sh/uv) (MIT or Apache-2.0), each checked against a pinned checksum.

## Words

- The house rules are Switchboard's own words for what these rule sets share: [andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills) (no licence stated), [ponytail](https://github.com/DietrichGebert/ponytail) (MIT), [agent-skills](https://github.com/addyosmani/agent-skills) (MIT), [gstack](https://github.com/garrytan/gstack) (MIT) and [ECC](https://github.com/affaan-m/ECC) (MIT). No text is copied.
- `CODE_OF_CONDUCT.md` is the [Contributor Covenant](https://www.contributor-covenant.org) 2.1, word for word but its contact line.
