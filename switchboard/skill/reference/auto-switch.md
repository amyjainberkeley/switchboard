# Auto-switch

Auto-switch moves Claude Code to another account before the account in use reaches a limit. It runs in the page's background process; while the page is not running, nothing is read or switched by itself.

## On and off

- `switchboard auto` says whether it is on, at which lines, in which order, and each account's rule. `switchboard auto on` or `switchboard auto off` changes it, and it stays as set: it never turns on by itself again.
- It turns on by itself once a second account is in, unless the person turned it on or off before (on the page, in the menu bar, or with `switchboard auto`), and not while another program on the Mac switches Claude Code's login.
- Off, the accounts are still read and shown; only switching stops.

## The lines

- Each limit has a switch line: 90% of the 5-hour limit and 95% of the weekly limit, unless changed on the page (the switch line above the accounts) or with `switchboard config set auto_policy.session_at N` and `switchboard config set auto_policy.weekly_at N`. A model's own week the person chose to count (Count a model, in Settings) has a line too, 95% by default.
- One account can have lines of its own: `switchboard auto line NAME --five-hour N --week N` (50 to 99.9); `switchboard auto line NAME off` puts it back on the shared ones.
- An account at or over a line is moved off at once, within about a minute in a terminal and within a few minutes in VS Code, even one the person just picked. It lands only on an account with at least 5 points of room under every line. When none has room, it stays and says when the first account refills.

## The order

- Refills first (the default): among the accounts with room, the one whose week refills soonest. It may also move before a line, to an account that refills at least an hour sooner and has 10 points of room under every line, but that move waits (below).
- Most room: the one with the most room, and it stays on the account in use until a line.
- Stay, then refills first: stays on the account in use until a line, then goes to the one that refills first.
- Change it on the page, or with `switchboard config set auto_policy.strategy WORD` (`refills-first`, `most-room` or `stay-then-refills`).

## When a move waits

A move that does not have to go now waits, among other things, until: Claude Code is not working and has been idle for 5 minutes; no artifact or doc was made on the account in the last 30 minutes; no chat used a claude.ai connector on it in the last 30 minutes; no chat watches an artifact made on it; both accounts' numbers are under 10 minutes old; the last switch, the person's own included, was 45 minutes ago or more; neither account's numbers are held back by a rate limit; Claude Code's account label and its login agree. It also waits while the Mac is very busy (load over 4 per core) and, off power, with the battery under 25%. At most two such moves go in 6 hours. `switchboard auto why` names the one it waits for. While one waits, the page says what for. Under 50 MB of free disk, no switch begins at all.

## Keeping it off one account

- `switchboard auto out NAME`: kept out. Claude Code is never moved there by itself, and never moved away from it by itself. Its limits are still read, and the person can still switch to it by hand.
- `switchboard auto last NAME`: used last, only when no other account has room.
- `switchboard auto in NAME`: used as usual again.
- `switchboard auto rank NAME N`: rank 1 is used first, then 2; an account with no rank comes after every ranked one. `switchboard auto rank NAME off` takes it away.

## Why it moved, or did not

- `switchboard log`: each switch, with its reason, and each limit reached.
- `switchboard auto why`: what it would do now and why: the account in use against each line, the one reason each other account can't take Claude Code, and what a waiting move waits for. Exit code 0 it would move, 3 held back, 4 it stays or waits, 1 it could not look.
- `switchboard doctor`: whether automatic switching runs, and when the accounts were last read.
- It did not switch: auto-switch may be off, the page may not be running, no other account may have room, the disk may be almost full, or the move is waiting.

## By hand

`switchboard switch NAME` moves Claude Code to an account now. `switchboard switch --best` moves it to the account auto-switch would land on, without the waits, and says why. Both are changes: run them only when the person asks. Never switch because a limit looks near: auto-switch does that at the line the person set.
