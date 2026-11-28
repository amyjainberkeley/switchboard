"""Routing in the terminal: the lines under the bare `switchboard` list, and `switchboard routing`.

list_lines(now)  for the bare list: nothing while routing is off, so the list is as it was without the router; once
                 on, a blank line and wire.lines(now), at most four. Nothing in the demo. Never raises.
main(args)       `switchboard routing [on | off | trial on|off | set NAME VALUE | cache [on|off]]`. Alone: what is
                 on, the switches, the defaults, the routes and the last five decisions, each with its reason.
"""
import time

from .. import core
from . import terminal_words as T

LAST_N = 5


def list_lines(now=None) -> list:
    """The routing lines under the bare list. Nothing while routing is off (never on, turned off, or a switches file
    that cannot be read); once on, a blank line, then wire.lines(now). Never raises."""
    if core.DEMO:
        return []
    try:
        from . import wire
        if not wire._routing_on():
            return []
        got = wire.lines(time.time() if now is None else now)
        return [''] + [x for x in got if isinstance(x, str) and x]
    except Exception as e:  # noqa: BLE001 - the list is shown without them
        core.log('routing lines: %s' % type(e).__name__)
        return []


def _apps():
    from .. import providers
    return providers.ids()


def _app_name(pid):
    from .. import providers
    return providers.name_of(pid)


def _models(models):
    """'Claude haiku, Codex gpt-6-luna' from {agent: model}."""
    return ', '.join('%s %s' % (_app_name(a), m) for a, m in sorted(models.items()))


def _default_what(d):
    parts = []
    if d.get('model'):
        parts.append(T.DEFAULT_MODEL.format(models=_models(d['model'])))
    if d.get('effort'):
        parts.append(T.DEFAULT_EFFORT.format(effort=d['effort']))
    return ', '.join(parts) or T.DEFAULT_NONE


def _route_lines():
    from ..providers import catalog, kinds, routes
    from . import words
    try:
        table = routes.load(catalog)
        out = []
        for kind in kinds.KINDS:
            choices = []
            for c in table.route_for(kind, list(_apps())):
                one = ' '.join(x for x in (_app_name(c.provider), c.model) if x)
                choices.append(T.CHOICE_AT.format(choice=one, effort=c.effort) if c.effort else one)
            if choices:
                out.append(T.ROUTE.format(kind=words.KIND_PLURAL[kind].capitalize(),
                                          choices=T.ROUTE_THEN.join(choices)))
    except Exception as e:  # noqa: BLE001 - the rest is still shown
        core.log('routing routes: %s' % type(e).__name__)
        return [T.ROUTES_UNREAD]
    return [T.ROUTES] + out if out else [T.NO_ROUTES]


def show(now=None) -> list:
    """Everything `switchboard routing` prints alone."""
    from . import record, settings, wire, words
    from . import wire_words as W
    now = time.time() if now is None else now
    out = list(wire.lines(now))
    s = settings.read(say=False) if wire.made() else settings.defaults()
    out += ['', T.SWITCHES]
    for name in settings.NAMES:
        out.append(T.SWITCH.format(label=words.SWITCHES[name], state=T.ON if settings.switch(s, name) else T.OFF))
    out += ['', T.DEFAULTS]
    for where in settings.WHERE:
        out.append(T.DEFAULT.format(where=T.WHERE[where], what=_default_what(s['defaults'][where])))
    out += [''] + _route_lines()
    from . import hand_words
    out += ['', hand_words.CODEX_SIGN_IN if settings.HAND_READY else hand_words.HAND_NOT_YET]
    last = []
    if wire.made():
        recs, _ = record.read(200)
        for rec in reversed(recs):
            text = record.line(rec)
            if text:
                last.append(T.LAST_LINE.format(line=text))
            if len(last) >= LAST_N:
                break
    out += ['', T.LAST] + (list(reversed(last)) or [T.LAST_LINE.format(line=W.LAST_NONE)])
    return out


def _onoff(word):
    return {'on': True, 'true': True, 'yes': True, 'off': False, 'false': False, 'no': False}.get(word.lower())


def _set(name, value) -> list:
    from ..providers import catalog
    from . import settings, wire, words
    if name in ('on', 'dry'):                       # the switch and the trial run, with all they write and undo
        on = _onoff(value)
        if on is None:
            return [T.NOT_A_SWITCH.format(name=name)]
        if name == 'dry':
            return wire.trial(on)
        return wire.turn_on(cache=_cache_pick) if on else wire.turn_off()
    if name in settings.NAMES:
        on = _onoff(value)
        if on is None:
            return [T.NOT_A_SWITCH.format(name=name)]
        settings.set_switch(name, on)
        lines = [T.SET_SWITCH.format(label=words.SWITCHES[name], state=T.ON if on else T.OFF)]
    elif name in settings.WORDS:
        where = name.split('.')[1]
        is_model = name.endswith('.model')
        if value.lower() in ('none', 'unset', 'off'):
            for agent in (_apps() if is_model else _apps()[:1]):
                settings.set_default(name, None, agent)
        else:
            find = catalog.find_model if is_model else catalog.find_effort
            agent = next((a for a in _apps() if _knows(find, a, value)), None)
            if agent is None:
                return [T.NOT_KNOWN.format(word=value, what=T.WORD_MODEL if is_model else T.WORD_EFFORT)]
            settings.set_default(name, value, agent)
        s = settings.read(say=False)
        lines = [T.SET_DEFAULT.format(where=T.WHERE[where], what=_default_what(s['defaults'][where]))]
    else:
        raise settings.SettingsError(words.SETTINGS_UNKNOWN.format(name=name,
                                                                   names=', '.join(settings.NAMES + settings.WORDS)))
    if wire.made() and settings.read(say=False)['on']:
        wire._write_plan(time.time(), None)         # the apps follow the change now, not at the loop's next look
    return lines


def _knows(find, agent, word) -> bool:
    try:
        find(agent, word)
        return True
    except Exception:  # noqa: BLE001 - not a word this app's list has
        return False


def _cache_pick():
    """The helpers' cache lifetime the person's own count calls for ('1h'), or None (cache_rule.py)."""
    from . import cache_count, cache_rule
    return cache_rule.pick(cache_count.count(cache_count.projects_folders()))


def _cache(rest) -> list:
    from . import cache_count, cache_setting
    lines = [T.COUNTING]
    try:
        lines += cache_count.say(cache_count.count(cache_count.projects_folders()))
    except Exception as e:  # noqa: BLE001
        lines.append(T.COUNT_FAILED.format(why=type(e).__name__))
    if rest == ['on'] or rest == ['off']:
        try:
            got = cache_setting.apply('1h') if rest == ['on'] else cache_setting.undo()
            lines += [x for x in [got.get('blocked') or got.get('says')] if x]
        except Exception as e:  # noqa: BLE001
            lines.append(T.CACHE_FAILED.format(why=type(e).__name__))
    else:
        lines += list(cache_setting.status().get('lines') or [])
    return lines


def main(args) -> int:
    """`switchboard routing ...` (args: the words after `routing`). Prints its lines; the exit code."""
    from . import settings, wire
    try:
        if not args:
            lines = show()
        elif args == ['on']:
            lines = wire.turn_on(cache=_cache_pick)
        elif args == ['off']:
            lines = wire.turn_off()
        elif len(args) == 2 and args[0] == 'trial' and _onoff(args[1]) is not None:
            lines = wire.trial(_onoff(args[1]))
        elif len(args) == 3 and args[0] == 'set':
            lines = _set(args[1], args[2])
        elif args[:1] == ['cache'] and args[1:] in ([], ['on'], ['off']):
            lines = _cache(args[1:])
        else:
            print(T.USAGE)
            return 2
    except settings.SettingsError as e:
        print(str(e))
        return 1
    for line in lines:
        print(line)
    return 0
