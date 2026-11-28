# Routing

Routing sends each piece of work started in Claude Code or Codex to the model and effort the person's own routes name for that kind of work, with nothing new to type. A small plugin inside Claude Code, and hooks and helper files for Codex, read one small file Switchboard writes (the plan) as work starts. With no plan, an old plan or one they cannot read, the apps work as if Switchboard were not there.

## What it does

- A route is set per kind of work (build, fix, review, plan, find, write): the choices to try in turn, each an app with a model and an effort. A choice whose app has no room, is signed out, is not installed or was never added in Switchboard is passed, and the next one is tried. When every choice is passed, the work stays where it was typed.
- With no route and no default set, routing changes nothing, and nothing is recorded.
- A default is what work gets when its kind has no route, by where it was started: a workflow agent, any other helper, or the person's own chat. A default's effort is a ceiling: work already at that effort or lower keeps its own.
- A helper whose caller named its own model keeps it, unless the person turns `helpers.force` on.
- Handing work to the other app is not in this version: a choice that names the other app is passed, and Switchboard starts nothing there.
- Sending a stopped chat one "continue" after Switchboard moved the account (`carry_on`) is not in this version.
- Routing is off until it is turned on (`switchboard routing on`). Someone who updates from a version without routing stays off until they turn it on.

## Seeing why work went where

- `switchboard routing`: whether routing is on, every switch, the defaults, the routes, and the last five decisions, each with its reason.
- `switchboard log`: History has one line per decision, and a line when an app says the account in use is at the person's line or a turn stopped at a usage limit.
- `switchboard`: while routing is on, the list has at most four lines about it under the rows. While it is off, it has none.

## Setting it

Run these only when the person asked for that change.

- `switchboard routes set KIND CHOICE`: one kind's route (a choice is written as in `switchboard routes`).
- `switchboard routing set NAME VALUE`: one switch (`on` or `off`) or one default. The switches are `hand.on`, `hand.write`, `carry_on`, `helpers.force`, `chat.effort` and `chat.model_on_first_turn`. The defaults are `defaults.workflow.model`, `defaults.workflow.effort`, `defaults.helper.model`, `defaults.helper.effort`, `defaults.chat.model` and `defaults.chat.effort`: a model or an effort word the app's list knows, or `none`. For example `switchboard routing set helpers.force on`.
- `switchboard routing trial on`: a trial run. The apps record what they would do and change nothing. `switchboard routing trial off` ends it.
- `switchboard routing cache`: the person's own count of how often helpers stored their whole context again after a wait, from the records Claude Code keeps on this Mac (numbers only). `switchboard routing cache on` sets an hour cache for helpers in Claude Code's settings; `switchboard routing cache off` puts back what was there.

## Turning it off

`switchboard routing off` takes the plan away at once, so every app steps aside, and takes back what turning it on wrote: the one key that makes Claude Code load the plugin, and Codex's hooks and helper files. `switchboard routing on` turns it on again.

## What is recorded, and what leaves the Mac

- Each decision is kept in `~/.switchboard` as the app, where the work started, its kind, the model and effort, and the reason. Never a word of a prompt or a task, a path, an email or an id.
- Switchboard sends nothing itself and is never in the path of a request.
- Nothing is handed to the other app in this version.
