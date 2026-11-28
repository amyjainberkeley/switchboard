"""Every sentence the router's adapters show: the hook line a chat is given, and what the Codex files say.

Only plain strings and small functions, no import of Switchboard's, so the hook line can load this file from its own
folder. The word rules of tests/test_providers_words.py hold here too.
"""

# What a kind of work is called in a line, by the kind's word (the same kinds as providers/kinds.py).
KIND_NAMES = {'build': 'Building', 'fix': 'Fixes', 'review': 'Reviews', 'plan': 'Plans', 'find': 'Lookups',
              'write': 'Writing', 'other': 'Other work'}
# What the helper a Codex file defines is for, by kind, said to Codex.
KIND_ABOUT = {'build': 'building or changing code', 'fix': 'finding and mending a fault',
              'review': 'reading work and judging it', 'plan': 'deciding how before doing',
              'find': 'looking something up or explaining what is there', 'write': 'writing prose',
              'other': 'other work'}
AGENT_NAMES = {'claude': 'Claude Code', 'codex': 'Codex'}

# ---------- the hook line: what a chat is told at its start ----------

# One line per kind of work with a route. {kind}: KIND_NAMES; {helper}: the Codex helper's name; {detail}: DETAIL_*.
# A Claude Code helper is started with a model and no effort (its Agent call takes none), so its line names only the
# model; a Codex helper file carries both.
LINE_HELPER = '{kind}: the helper {helper} ({detail}).'
LINE_CLAUDE = '{kind}: a helper with model {model}.'
LINE_HAND = '{kind}: {agent}, with {detail}.'
LINE_HAND_BARE = '{kind}: {agent}.'
LINE_ALL_PASSED = '{kind}: nothing on the route can take it now, so it stays here.'
BACK_AT = ' Room is back at {when}.'
ROOM_UNKNOWN = ' Its room is not known.'
DETAIL_BOTH = 'model {model} and effort {effort}'
DETAIL_MODEL = 'model {model}'
DETAIL_EFFORT = 'effort {effort}'
# The last line, on how a route is followed.
HOW_CODEX = 'These are the user\'s Switchboard routes. To follow one, start the helper it names; other work stays ' \
            'as usual.'
HOW_CLAUDE = 'These are the user\'s Switchboard routes. To follow one, start a helper with the model it names; ' \
             'other work stays as usual.'
HOW_HAND = ' A route that names another app is for the user to start there; tell them.'

# ---------- the hook line: one line when a fact the chat was told has changed ----------

CHANGED = "Switchboard's routes changed."
LOST = '{agent} has no room now.'
BACK = '{agent} has room again.'
NOW = ' Now: {lines}'
OFF = 'Switchboard routing is off now: work stays where it is typed, on this chat\'s own model.'
NONE_NOW = "Switchboard's routes no longer send work from this chat anywhere: it stays where it is typed."

# ---------- Codex's helper files ----------

FILE_TOP = ('# Written by Switchboard from your routes. If you change this file, Switchboard leaves it alone from '
            'then on.\n')
HELPER_DESCRIPTION = 'Use for {about}. The user\'s Switchboard routes send this kind of work to this helper.'
HELPER_INSTRUCTIONS = 'You are a helper for {about}. Do the one task you were given, then answer with what you did ' \
                      'and what you found.'
NOT_OURS = '{path} is not a file Switchboard wrote, so it was left as it is.'
CHANGED_SINCE = '{path} was changed after Switchboard wrote it, so it was left as it is.'
BAD_RECORD = "Switchboard's record of the Codex files it wrote ({path}) can't be read, so nothing was changed."
WOULD_WRITE = 'Would write {path}.'
WOULD_REMOVE = 'Would remove {path}.'
DRY_PLAN = 'Routing is in its trial mode, so nothing was written in the Codex folder.'
NO_CODEX_HOME = 'There is no Codex folder at {path}, so nothing was written.'

# ---------- Codex's hooks file ----------

HOOKS_NOT_JSON = '{path} is not valid JSON, so Switchboard left it as it is.'
HOOKS_SHAPE = '{path} is not in the shape Switchboard knows, so Switchboard left it as it is.'
HOOKS_ON = "Switchboard's two Codex hooks are in {path}."
HOOKS_ALREADY = "Switchboard's two Codex hooks are already in {path}."
HOOKS_OFF = "Switchboard's Codex hooks are out of {path}."
HOOKS_NONE = "Switchboard's Codex hooks are not in {path}."
HOOKS_MOVED = '{path} changed while Switchboard was writing it, so nothing was written. Try again.'
TRUST = 'Codex asks you once to look at the new hook and trust it (type /hooks in Codex). Until then Codex skips it.'


def kind_name(kind):
    return KIND_NAMES.get(kind) or kind.capitalize()


def agent_name(agent):
    return AGENT_NAMES.get(agent) or agent


def detail(model, effort):
    """The model and effort words of a route, or '' when it names neither."""
    if model and effort:
        return DETAIL_BOTH.format(model=model, effort=effort)
    if model:
        return DETAIL_MODEL.format(model=model)
    if effort:
        return DETAIL_EFFORT.format(effort=effort)
    return ''
