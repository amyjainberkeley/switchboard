# What it does not do

## Teams and sharing

There is no team feature. Switchboard keeps the accounts one person signs in to on one Mac, and it has no sharing.

## Models and agents

Switchboard does not pick models by itself. It moves Claude Code between the person's own accounts, at the lines they set, and routes work only by the routes and defaults the person set (`routing.md`). Its two model settings are off until turned on: Models by the job (four helpers, each on the model that fits its job) and Sonnet for helpers (Claude Code's helpers that name no model run on Sonnet instead of the chat's model). The chat keeps its own model.

## Signing in for you

It never shows a login form and never signs in for anyone: signing in is Claude Code's own, on claude.ai, and the person approves each sign-in in the browser. It renews logins, but each sign-in still ends by a date Anthropic sets.

## The Claude desktop app and the browser

A switch moves Claude Code only. The Claude desktop app and claude.ai in each browser profile keep their own sign-ins.

## Windows and Linux

macOS only. It needs Claude Code. A company that locks Claude Code to its organization (`forceLoginOrgUUID`) makes Claude Code refuse accounts in other organizations.

## Anthropic's terms

Switchboard makes no claim either way about whether using several accounts fits Anthropic's terms. The README says: "Check that using several accounts fits Anthropic's terms." The pages to read:

- Anthropic's consumer terms: https://www.anthropic.com/legal/consumer-terms
- Claude Code's legal and compliance page: https://code.claude.com/docs/en/legal-and-compliance
- For work carried to Codex, OpenAI's terms of use: https://openai.com/policies/terms-of-use/

One rule from Claude Code's page is built in: the keys gateway refuses any request that carries a subscription login. Say nothing more about it.

## Spending

It has no spending limit of its own. The keys gateway, off until turned on, runs Claude Code on the person's own API keys, billed by the key's provider.
