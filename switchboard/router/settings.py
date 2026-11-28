"""The person's routing switches, in one file (<data folder>/router/settings.json), all off until turned on.

    {"v": 1, "on": false, "dry": false, "helpers": {"force": false},
     "chat": {"effort": false, "model_on_first_turn": false}, "hand": {"on": false, "write": false},
     "carry_on": false,
     "defaults": {"workflow": {"model": {"claude": "sonnet"}, "effort": "xhigh"}, "helper": {...}, "chat": {...}}}

The defaults are what work gets when its kind has no route, by where it was started: a workflow agent, any other
helper, or a turn of the person's own chat. Each is unset (null) until the person sets it. An effort is one word. A
model is kept for the agent it was set for ({"claude": "sonnet", "codex": "gpt-6-luna"}), since one agent's model
names mean nothing to the other. set_default() takes only a word the list of models knows for the agent it is given.

A missing key is off (or unset). Where ready.HAND_READY is true there are two exceptions: once routing is on, handing
work to another app on the person's route (hand.on) and the one "continue" after a move at a limit (carry_on) are on
unless the person turned them off. A value the person set is kept in the file; one they never set is left out of it and follows "on". A file that cannot be read (not JSON, not an object, another version, a switch that is not
true or false, a group that is not an object) reads as every switch off, never as a guess at what it meant, and is
said to History once (said again only after the file changes, or after it was readable again and broke again). A
change is written whole or not at all, under a lock, and never over a file that cannot be read: the person moves it
aside or resets it first.
"""
import json
import os
import re

from .. import core
from ..providers import contract
from ..providers import words as provider_words
from . import words
from .ready import HAND_READY

FILE = 'router/settings.json'
SAID_FILE = 'router/settings.said'      # which unreadable file was already said to History
LOCK = 'router/settings.lock'
MAX_BYTES = 64 * 1024
NAMES = ('on', 'dry', 'helpers.force', 'chat.effort', 'chat.model_on_first_turn', 'hand.on', 'hand.write',
         'carry_on')
WHERE = ('workflow', 'helper', 'chat')
WORDS = tuple('defaults.%s.%s' % (w, k) for w in WHERE for k in ('model', 'effort'))
GROUPS = ('helpers', 'chat', 'hand', 'defaults')
FOLLOW_ON = ('hand.on', 'carry_on') if HAND_READY else ()      # the switches that follow "on" unless set (ready.py)
_WORD_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}\Z')     # a model or effort as the list spells it


class SettingsError(ValueError):
    """A change that cannot be made: str() is one plain sentence for the person."""


def defaults() -> dict:
    """Every switch off and every default unset, in the plan's shape."""
    return {'on': False, 'dry': False, 'helpers': {'force': False},
            'chat': {'effort': False, 'model_on_first_turn': False}, 'hand': {'on': False, 'write': False},
            'carry_on': False, 'defaults': {w: {'model': None, 'effort': None} for w in WHERE}}


def path() -> str:
    return core.data_path(FILE)


_MISSING = object()


def get(switches: dict, name: str):
    """One switch by its dotted name ('hand.on'), from a dict in the plan's shape."""
    node = switches
    for part in name.split('.'):
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _put(switches: dict, name: str, value) -> None:
    *groups, last = name.split('.')
    node = switches
    for g in groups:
        node = node.setdefault(g, {})
    node[last] = value


def switch(switches: dict, name: str) -> bool:
    """One switch as it acts, from a dict in the plan's shape: a FOLLOW_ON switch that is not in it follows "on"."""
    got = get(switches, name)
    if got is _MISSING and name in FOLLOW_ON:
        got = get(switches, 'on')
    return got is True


def _drop(switches: dict, name: str) -> None:
    *groups, last = name.split('.')
    node = switches
    for g in groups:
        node = node.get(g) if isinstance(node, dict) else None
    if isinstance(node, dict):
        node.pop(last, None)


def _no_constants(word):
    raise ValueError(word)


def _parse(data):
    """The switches in a file's object, or None when it cannot be read."""
    if not isinstance(data, dict) or data.get('v', 1) != 1 or isinstance(data.get('v', 1), bool):
        return None
    if any(g in data and not isinstance(data[g], dict) for g in GROUPS):
        return None
    if any(w in data.get('defaults', {}) and not isinstance(data['defaults'][w], dict) for w in WHERE):
        return None
    out = defaults()
    for name in NAMES:
        got = get(data, name)
        if got is _MISSING:
            continue
        if not isinstance(got, bool):
            return None
        _put(out, name, got)
    for name in FOLLOW_ON:
        if get(data, name) is _MISSING:
            _put(out, name, out['on'])
    for name in WORDS:
        got = get(data, name)
        if got is _MISSING or got is None:
            continue
        if name.endswith('.model'):
            got = _models(got)
            if got is _MISSING:
                return None
        elif not isinstance(got, str) or not _WORD_RE.match(got):
            return None
        _put(out, name, got)
    return out


def _models(got):
    """A default's models by agent ({"claude": "sonnet"}), None when it names none, or _MISSING when it cannot be
    read (a bare word names no agent)."""
    if not isinstance(got, dict):
        return _MISSING
    out = {}
    for agent, word in got.items():
        if not isinstance(agent, str) or not contract.ID_RE.match(agent):
            return _MISSING
        if word is None:
            continue
        if not isinstance(word, str) or not _WORD_RE.match(word):
            return _MISSING
        out[agent] = word
    return out or None


def _set_by_person() -> set:
    """The FOLLOW_ON switches the file holds a value for (read under the lock, after _load found it readable)."""
    try:
        with open(path(), 'rb') as f:
            data = json.loads(f.read(MAX_BYTES + 1).decode('utf-8'), parse_constant=_no_constants)
    except (OSError, ValueError, RecursionError):
        return set()
    return {n for n in FOLLOW_ON if get(data, n) is not _MISSING}


def _load():
    """(the switches, or None when the file is there and cannot be read; the file's mark, or None for no file)."""
    try:
        st = os.stat(path())
    except FileNotFoundError:
        return defaults(), None
    except OSError:
        return None, 'stat'
    mark = '%d:%d' % (st.st_mtime_ns, st.st_size)
    if st.st_size > MAX_BYTES:
        return None, mark
    try:
        with open(path(), 'rb') as f:
            data = json.loads(f.read(MAX_BYTES + 1).decode('utf-8'), parse_constant=_no_constants)
    except (OSError, ValueError, RecursionError):
        return None, mark
    return _parse(data), mark


def _said_path() -> str:
    return core.data_path(SAID_FILE)


def _say_once(mark: str) -> None:
    """SETTINGS_UNREADABLE to History, unless this same unreadable file was already said."""
    try:
        with core.file_lock(LOCK):
            try:
                with open(_said_path(), encoding='utf-8') as f:
                    if f.read() == mark:
                        return
            except OSError:
                pass
            core.write_text(_said_path(), mark)
        from .. import history
        history.record('problem', words.SETTINGS_UNREADABLE, by='auto')
    except Exception as e:  # noqa: BLE001 - a History line that cannot be written is logged, never raised
        core.log('router settings: %s' % type(e).__name__)


def _forget_said() -> None:
    try:
        os.remove(_said_path())
    except FileNotFoundError:
        pass
    except OSError as e:
        core.log('router settings: %s' % type(e).__name__)


def read(say: bool = True) -> dict:
    """The switches now, in the plan's shape. A file that cannot be read is every switch off; with say, it is said to
    History once. Never raises."""
    try:
        switches, mark = _load()
    except Exception:  # noqa: BLE001 - never a guess: off
        switches, mark = None, 'error'
    if switches is not None:
        if say and mark is not None and os.path.exists(_said_path()):
            _forget_said()
        return switches
    if say:
        _say_once(mark or 'error')
    return defaults()


def _router_folder() -> None:
    os.makedirs(os.path.dirname(path()), mode=0o700, exist_ok=True)


def _unknown(name: str) -> SettingsError:
    return SettingsError(words.SETTINGS_UNKNOWN.format(name=provider_words.one_line(str(name), 40),
                                                       names=', '.join(NAMES + WORDS)))


def set_switch(name: str, value: bool) -> dict:
    """Turn one switch on or off ('hand.on', True); the switches as kept. SettingsError for a name there is not, or
    when the file is there and cannot be read (nothing is changed)."""
    if name not in NAMES:
        raise _unknown(name)
    if not isinstance(value, bool):
        raise TypeError('a switch is True or False')
    return _change(name, value)


def set_default(name: str, value, agent: str) -> dict:
    """Set one default ('defaults.workflow.effort', 'xhigh') or unset it (None); the switches as kept. The word must
    be one the list of models knows for `agent` (the agent in use), and is kept as the list spells it. A model is set
    or unset for `agent` only; a model set for another agent stays. SettingsError for a name there is not, a word
    the list does not know (its own sentence), or a file that cannot be read (nothing is changed)."""
    if name not in WORDS:
        raise _unknown(name)
    if value is not None and not isinstance(value, str):
        raise TypeError('a default is a word or None')
    if value is not None:
        from ..providers import catalog
        try:
            if name.endswith('.model'):
                value = catalog.find_model(agent, value).id
            else:
                value = catalog.find_effort(agent, value)
        except catalog.CatalogError as e:
            raise SettingsError(str(e)) from None
    if name.endswith('.model'):
        return _change(name, value, agent)
    return _change(name, value)


def _change(name: str, value, agent=None) -> dict:
    """Write one switch or default into the file, under the lock; the switches as kept. With `agent`, the value is a
    default's model for that agent alone."""
    _router_folder()
    with core.file_lock(LOCK):
        switches, _ = _load()
        if switches is None:
            raise SettingsError(words.SETTINGS_REFUSED.format(path=path()))
        kept = _set_by_person() | ({name} & set(FOLLOW_ON))
        if agent is not None:
            models = dict(get(switches, name) or {})
            models.pop(agent, None)
            if value is not None:
                models[agent] = value
            value = models or None
        _put(switches, name, value)
        doc = {'v': 1}
        doc.update(json.loads(json.dumps(switches)))
        for left_out in set(FOLLOW_ON) - kept:
            _drop(doc, left_out)
            _put(switches, left_out, switches['on'])
        core.write_json(path(), doc, 0o600)
    return switches


def reset() -> None:
    """Every switch off: the file goes, readable or not."""
    _router_folder()
    with core.file_lock(LOCK):
        try:
            os.remove(path())
        except FileNotFoundError:
            pass
