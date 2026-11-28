"""What of routing is in this tree. One place, so the switches, the sentences and the docs say the same thing.

HAND_READY: handing work to another app, and the one "continue" after a move at a limit, are done inside Claude Code
by Switchboard's plugin. While that part of the plugin is not in the tree, both are plain switches that start
nothing, off until set, and every place a person reads about a hand-over says it is not in this version
(tests/test_router_hand_ready.py holds each of those places to this one value).

The helper that runs a hand-over in Claude Code (switchboard-hand) is kept in router/agents/ while this is False: a
file in the plugin's own agents/ folder is offered to Claude in every session that loads the plugin, so it goes there
only with the plugin code that starts it and guards it. Turning this on moves it back to
mods/switchboard-router/agents/.
"""
HAND_READY = False
