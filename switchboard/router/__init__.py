"""Switchboard as the router under the coding apps.

The person keeps typing in Claude Code or Codex. Switchboard writes one small file, the plan
(<data folder>/router/plan.json): the person's routes for each kind of work, the order of the apps they added, how
much room each has, the switches they turned on, and their defaults for work with no route. An adapter inside each app reads the plan and applies it with
that app's own settings: the model and effort of a helper, or of a turn. The apps never ask Switchboard anything.

resolve.py   the rule every adapter applies to a plan (pure: no file, no clock)
plan.py      builds the plan from what Switchboard already knows, and writes and reads it
settings.py  the person's switches, all off until turned on, and their defaults, unset until set
record.py    what the adapters leave behind: a file per decision, folded into one record, and the facts they tell
             Switchboard
words.py     every sentence and name a person can see

Nothing here names an app except through the provider ids of switchboard/providers/.
"""
