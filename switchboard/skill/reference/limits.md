# Limits

Switchboard shows the limits Anthropic reports for each account. It counts nothing of its own for them.

## What each bar is

- 5-hour: how much of the 5-hour limit is used, a bar, and when it refills. It has a bar only when there is a 5-hour number to show. Some plans report no 5-hour limit: an account with none says "No 5-hour limit" in that place (on the page, "No 5-hour limit on this plan"), and an account that has shown a 5-hour number before is never said to have none.
- Weekly: how much of the week is used, and when it refills. When, at the pace of its use so far, the week would be full before it refills, the line says when ("Full by Thu 2 PM") in the refill's place.
- A model's own week, such as Fable's: it has no bar. The weekly line's hover names each one, and when auto-switch counts that model and it is at or over its line, its name and percent are a short red note at the end of the weekly line on the page. The list in `switchboard` shows only the 5-hour and weekly lines.
- Spend: on a plan with a spend limit, a line of its own ("Spend 41%", "$412 of $1,000"), with no switch line, so it never turns red. It is known only for the account in use, from Claude Code's own numbers.
- Each bar has a thin mark at its switch line and turns red at or past it.

## How fresh the numbers are

- The account in use, in a terminal while Claude Code works: live. Claude Code hands its own numbers to Switchboard's status line as the session changes and every 60 seconds; this needs the status line on, and a Pro, Max or Ultra plan.
- The account in use otherwise (Claude Code's VS Code panel, which runs no status line; Claude Code idle; the status line off): read from Anthropic every 5 minutes, about every 3 while the page is open, more often near a switch line.
- Every other account: every 5 to 15 minutes, more often near a switch line.
- At once: pressing Read again on the page (or R), or opening the page when the account in use was read over a minute ago or another account over 5 minutes ago. An account read in the last minute is not asked again.
- Anthropic turns readings away when they come too often, so Switchboard keeps to about 20 reads an hour for each account. When it is told to wait, it waits, up to 75 minutes, and marks the last numbers as held back: the real ones can only be higher.
- This holds with auto-switch on or off, and with one account. Each account shows when it was last checked. A number Switchboard can't vouch for, such as a reading over an hour old, shows as not known (`null` in `switchboard status --json`), never as a guess.

## Reading them

- `switchboard status --json`: `limits.five_hour` and `limits.week`, each with `used_percent` and `refills_at` (UTC), `limits.week.pace`, `limits.models`, `limits.spend`, and `checked_at` per account.
- `switchboard status --json`, the page and the menu bar show the same numbers.
- The numbers look old: `switchboard doctor` has a "Reading the accounts" line saying whether they are being read and when they last were. The accounts are read only while the page's background job runs.
