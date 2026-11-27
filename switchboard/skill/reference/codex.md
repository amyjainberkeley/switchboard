# Codex

Switchboard shows Codex (OpenAI's app) beside the Claude accounts, and can carry a chat on there or hand it one job. It does not switch between ChatGPT accounts: it uses the one Codex is signed in to, and it never reads, keeps or signs in to Codex's login.

## What it does

- `switchboard providers`: every assistant on this Mac (Claude, Codex), whether it has room, its plan and limits, and which copy of its program Switchboard would run. Codex's numbers come from its own session files on this Mac. `switchboard providers --ask`, at a terminal, asks Codex's own helper for them now, after one yes.
- `switchboard add codex`: adds Codex to the order. It says which copy will run and whether it looks signed in.
- `switchboard continue`: carries the chat in this folder on in the next assistant with room, in the person's order (`--to PROVIDER` names one). Switchboard writes a note of the chat on this Mac, from the chat's own file, with anything that looks like a secret taken out as well as it can. At a terminal it shows the note's size and asks once for this folder whether notes may go to that assistant's maker; after the yes, that app starts on the note.
- `switchboard back`: brings a note of what was done there back to the chat that was left.
- `switchboard hand PROVIDER -- JOB`: one read-only job for Codex in this folder, in a terminal, up to 15 minutes, with its answer printed. On its first run on a Mac it asks one question about OpenAI's terms.
- `switchboard stay PROVIDER` keeps a folder's chats on one assistant; `switchboard order` sets the order `continue` follows.
- The page, `switchboard`, the menu bar and `switchboard doctor` show Codex's room and limits beside the accounts once it is added.
- This skill goes in for Codex too, in `~/.agents/skills/switchboard`, when Codex is on the Mac.

Nothing moves on its own: every move starts with a command the person types, and no flag gives the yes. A command run from an agent's own shell, with no terminal, never gives the yes and starts nothing: it prints the line to run instead.

## What leaves the Mac

A note, or a job, goes to OpenAI through Codex only after the person's yes for that folder. Notes are kept in `~/.switchboard-notes/`, which only the person can read: a note for 30 days, a job's answer for 7.

## OpenAI's terms

Switchboard makes no claim either way: OpenAI's terms of use are at https://openai.com/policies/terms-of-use/.
