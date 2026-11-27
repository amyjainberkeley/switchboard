# Your week

Under the accounts on the page (`#week`): the last 7 days of Claude Code use by hour, stacked by who did it: workflows, helpers and the person's own chat turns. On the same time axis: each limit reached (a red dot), each switch (a tick), the average hour with use (the line marked avg) and the busiest hour, named. Pointing at an hour shows who did it, its projects, its models, how its tokens divide, and the account Claude Code was on. Under the chart: up to three highlights, then the week by project, by what agents did and by model. `switchboard usage` prints the same work in the terminal.

It is made only from Claude Code's own chat files on this Mac, Switchboard's History and its readings of the limits. No model is called and nothing leaves the Mac. It is not Anthropic's own accounting.

## Use, and the shares

Use is tokens weighted by today's prices, against an input token: a cache read counts 0.05, a cache write 1.25 (2 for one kept an hour), a reply token 5, an input token 1. Every share on the page is a share of that weighted use.

- Re-reading context: cache reads. Each request Claude Code makes carries the chat so far; the part stored earlier is read back from the cache, and that is this share. It grows with every turn of a long chat, and with what the chat has read in: files, tool output, a long CLAUDE.md.
- Storing it: cache writes. The new part of each request (the new turn, the files and tool output it brought in) is stored so that later requests can read it back.
- Claude's replies: the tokens Claude wrote.
- The fourth share, input that was not cached, is the rest; the highlight does not name it.

The highlight "48% of the use was re-reading context. Storing it was 37%, Claude's replies 15%" shows when re-reading context is 30% or more of the week. The three numbers are those shares of the week's weighted use.

## Lowering the first two

What follows from how Claude Code works:

- A new chat for a new task (`/clear`, or a new session): the old chat is no longer carried, so the next requests re-read and store only what the new task needs.
- Compacting a long chat (`/compact` in Claude Code): the chat so far is replaced by a summary, so each later request carries less.
- Less read into a chat: a chat that reads many large files, or long tool output, stores and then re-reads all of it.

Switchboard does not change these shares itself, and it has no setting that lowers them.
