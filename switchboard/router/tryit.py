"""Try routing without changing anything, and put it back: the "Try it" press and its Undo on the page's takeaways.

on()     remembers whether routing was on and whether its trial run was on (the first time only: a second call keeps
         the first memory), then turns the trial run on, and routing with it when it was off. In a trial run the apps
         record what they would do and change nothing. Returns the plain lines of what was done, at most MAX_LINES.
off()    puts both back as remembered: the trial run as it was, and when routing was off before, routing off again,
         which takes back everything turning it on wrote. With nothing remembered it does nothing and says so.
state()  {'trying', 'was_on', 'was_trial'} for a face to show.

The memory is its own small file in the router folder (router/tryit.json), removed by off(): the switches file keeps
only the switches it knows, so a memory kept in it would be lost at the next change of any switch. None of these
raise.
"""
import json
import os
import time

from .. import core
from . import settings as _settings
from . import wire

FILE = 'router/tryit.json'
MAX_LINES = 6
NOTHING = 'There is no trial run to undo.'
FAILED = 'The trial run could not be changed ({why}).'


def _path() -> str:
    return core.data_path(FILE)


def _memory():
    """{'was_on', 'was_trial'} or None for no memory (or one that cannot be read)."""
    try:
        with open(_path(), 'rb') as f:
            got = json.loads(f.read(4096).decode('utf-8'))
    except Exception:  # noqa: BLE001 - no file, or one that cannot be read: nothing remembered
        return None
    if not isinstance(got, dict) or not all(isinstance(got.get(k), bool) for k in ('was_on', 'was_trial')):
        return None
    return {'was_on': got['was_on'], 'was_trial': got['was_trial']}


def _why(e) -> str:
    text = str(e).strip()
    return text if text and len(text) <= 200 and '\n' not in text else type(e).__name__


def state() -> dict:
    """{'trying': a trial started here is still on, 'was_on' and 'was_trial': as remembered, None when not}."""
    m = _memory()
    return {'trying': m is not None, 'was_on': m and m['was_on'], 'was_trial': m and m['was_trial']}


def on(kit=None, now=None, **where) -> list:
    """Record what routing would do and change nothing. `where` (claude_dir, codex_home, versions) is for tests;
    the page calls on() with no arguments. Never raises."""
    now = time.time() if now is None else now
    try:
        switches = _settings.read(say=False) if wire.made() else _settings.defaults()
        was_on = _settings.get(switches, 'on') is True
        if _memory() is None:
            os.makedirs(os.path.dirname(_path()), mode=0o700, exist_ok=True)
            core.write_json(_path(), {'was_on': was_on, 'was_trial': _settings.get(switches, 'dry') is True,
                                      't': now})
        if was_on:
            lines = wire.trial(True, kit=kit, now=now, codex_home=where.get('codex_home'))
        else:
            _settings.set_switch('dry', True)        # before routing is on, so no plan is ever applied for real
            lines = wire.turn_on_steps(kit=kit, now=now, **where)
        return [x for x in lines if x][:MAX_LINES]
    except Exception as e:  # noqa: BLE001 - said in one line
        core.log('router tryit on: %s' % type(e).__name__)
        return [FAILED.format(why=_why(e))]


def off(kit=None, now=None, **where) -> list:
    """Put routing and its trial run back as they were before on(). Never raises."""
    now = time.time() if now is None else now
    m = _memory()
    if m is None:
        return [NOTHING]
    try:
        if m['was_on']:
            lines = wire.trial(m['was_trial'], kit=kit, now=now, codex_home=where.get('codex_home'))
        else:
            lines = wire.turn_off(kit=kit, **where)
            _settings.set_switch('dry', m['was_trial'])
        try:
            os.remove(_path())
        except FileNotFoundError:
            pass
        return [x for x in lines if x][:MAX_LINES]
    except Exception as e:  # noqa: BLE001 - said in one line; the memory stays for another try
        core.log('router tryit off: %s' % type(e).__name__)
        return [FAILED.format(why=_why(e))]
