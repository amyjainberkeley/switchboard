"""The router wired into Switchboard: one look per pass of the loop, the person's one switch, the trial run, the
lines the terminal list shows, and the lines History takes.

Routing is off until the person turns it on. While it is off and was never on, nothing of the router is written
anywhere: no router folder in Switchboard's data, no plan, no key in Claude Code's settings, no file in Codex's
folder. Every function here looks for the router folder first and, when it is not there, reads and writes nothing
else.

pass_(now)       one look per pass of the loop, never raising, at most BOUND_S seconds of its own work. On: the plan
                 is made and written, the adapters' decision files are folded, the record trimmed, the signals read
                 and returned. Off: a plan still there is taken away, so every adapter steps aside at once, and what
                 is left is folded.
turn_on()        the switch on, for real (the trial run stays as the person left it, off unless they chose it):
                 saved, a first plan written, Claude Code set to load the plugin, Codex's hooks and helper files
                 written when Codex is on the person's list. Each step whole, each said in one line; a step that
                 cannot be done does not undo the others.
turn_off()       the switch off: saved, the plan taken away, and everything turn_on wrote taken back by its own undo.
trial(on)        the trial run (the plan's `dry`): the adapters record what they would do and change nothing.
apply_for_real() the trial off, and Codex's helper files written.
status(), lines()  what the terminal list shows: one line when off, at most four when on.
history()        the decisions folded since a time, as History sentences, at most HISTORY_MAX a call.

Functions that write into Claude Code's or Codex's folder take claude_dir and codex_home: None is the person's own
folder.
"""
import collections
import json
import os
import threading
import time

from .. import core
from . import codex_files
from . import plan as _plan
from . import plugin_setting
from . import record as _record
from . import resolve as _resolve
from . import settings as _settings
from . import words as _words
from . import wire_words as W

BOUND_S = 2.0               # the most of its own work one pass of the loop may take
HISTORY_MAX = 20            # History sentences at most per call; the rest are counted in one line
HISTORY_SLACK_S = 30.0      # a decision folded late may carry a time a little before the last one shown
TURN_ON_MAX = 6             # the most lines turn_on_steps says, one per step
TURN_ON_BRIEF_MAX = 3       # the most lines turn_on says: the list's own sentence, then what changed
SEEN_MODELS = 3             # models named in the line of what was seen; the rest are counted together
CACHE_MARK = 'router/wire-cache'   # an empty mark: turn_on set the helpers' cache, so turn_off takes it back
ROUTER = 'router'
_GATHER = {'thread': None}  # the plan being made in the background, so a slow one never piles up a second
_SHOWN = collections.deque(maxlen=500)      # decisions already given to History in this process


def _router_dir():
    return core.data_path(ROUTER)


def made() -> bool:
    """True when the router folder is in Switchboard's data, that is, routing was turned on at least once. Never
    raises; a data folder that cannot be found counts as never."""
    try:
        return os.path.isdir(_router_dir())
    except Exception:  # noqa: BLE001 - a data folder that cannot be found: never turned on
        return False


def _why(e) -> str:
    text = str(e).strip()
    return text if text and len(text) <= 200 and '\n' not in text else type(e).__name__


def _log(what, e):
    core.log('router wire: %s %s' % (what, type(e).__name__))


# ---------------------------------------------------------------- one pass of the loop

class _Late(Exception):
    """The plan was not made within the pass's time."""


def _gather_within(now, kit, left):
    """plan.gather(now, kit), given at most `left` seconds. A gather still running from an earlier pass is not
    started again. Raises _Late when there is no plan in time."""
    t = _GATHER['thread']
    if t is not None and t.is_alive():
        raise _Late()
    box = {}

    def run():
        try:
            box['plan'] = _plan.gather(now, kit)
        except BaseException as e:  # noqa: BLE001 - carried back to the pass, which logs it
            box['error'] = e

    t = threading.Thread(target=run, name='switchboard-router-plan', daemon=True)
    _GATHER['thread'] = t
    t.start()
    t.join(max(0.0, left))
    if t.is_alive():
        raise _Late()
    if 'error' in box:
        raise box['error']
    return box['plan']


def _remove_plan() -> bool:
    """Take the plan away; True when there was one."""
    try:
        os.remove(_plan.path())
        return True
    except FileNotFoundError:
        return False


def _files_differ(p, codex_home) -> bool:
    """True when Codex's helper files are not what this plan wants: a wanted file missing, one Switchboard wrote
    that now differs from the plan, or one it wrote that the plan no longer wants. A file the person made or
    changed does not count (apply() leaves it alone and says so). Reads only."""
    home = os.path.abspath(os.path.expanduser(codex_home or codex_files.default_codex_home()))
    want = dict((path, text.encode('utf-8')) for path, text in codex_files.plan_files(p, home, p.get('made_at')))
    st = codex_files.status(codex_home)
    if st.get('message'):
        return False
    ours = set(f['path'] for f in st['files'] if f['state'] == 'ours')
    for path, text in want.items():
        try:
            with open(path, 'rb') as f:
                cur = f.read()
        except FileNotFoundError:
            return True
        if path in ours and cur != text:
            return True
    return bool(ours - set(want))


def pass_(now, kit=None, codex_home=None) -> dict:
    """One look per pass of Switchboard's loop. Never raises. At most BOUND_S seconds of its own work, counted
    between steps: the plan is made in a thread given what is left of the time, and a step not started by then is
    left for the next pass ('stopped_early' says so). A step already started (a fold, a trim) runs to its end, so
    one slow step can take a pass past the bound.

    With routing on and not in a trial, Codex's helper files are kept in step with the routes (codex_home: Codex's
    folder, None for the person's own); nothing is written when they already match.

    Returns {'on', 'dry', 'plan' ('written', 'removed', 'late', 'failed' or None), 'codex_files' (how many of
    Codex's helper files were written or taken out), 'folded', 'fold_removed', 'trimmed', 'signals' (the facts the
    adapters told, record.signals()'s shape), 'signals_left', 'stopped_early', 'errors' (the names of the steps
    that failed), 'took_s'}. When the router folder was never made it touches nothing at all and returns 'on'
    False."""
    start = time.monotonic()
    out = {'on': False, 'dry': False, 'plan': None, 'codex_files': 0, 'folded': 0, 'fold_removed': 0, 'trimmed': 0,
           'signals': [], 'signals_left': 0, 'stopped_early': False, 'errors': [], 'took_s': 0.0}
    made_plan = {}

    def left():
        return BOUND_S - (time.monotonic() - start)

    def make_plan():
        try:
            got = _gather_within(now, kit, left())
        except _Late:
            out['plan'] = 'late'
            out['stopped_early'] = True
            return
        # The switch is read again under its own lock, so a turn_off made while the plan was being made either
        # comes after this write (and takes the plan away) or before it (and no plan is written).
        with core.file_lock(_settings.LOCK):
            if _settings.get(_settings.read(say=False), 'on') is not True:
                out['on'] = False
                return
            _plan.write(got)
        out['plan'] = 'written'
        made_plan['plan'] = got

    def codex():
        p = made_plan.get('plan')
        if p is None or p.get('dry') is True or not out['on'] or not _codex_record():
            return
        if not _codex_ready(p, codex_home)[0] or not _files_differ(p, codex_home):
            return
        r = codex_files.apply(p, codex_home, now=p.get('made_at'))
        out['codex_files'] = len(r.get('written') or ()) + len(r.get('removed') or ())

    def take_plan_away():
        if _remove_plan():
            out['plan'] = 'removed'

    def fold():
        out['folded'], out['fold_removed'] = _record.fold(now)

    def trim():
        try:
            big = os.path.getsize(_record.path()) > _record.MAX_BYTES
        except OSError:
            big = False
        if big:
            out['trimmed'] = _record.trim()

    def signals():
        out['signals'], out['signals_left'] = _record.signals()

    try:
        if not made():
            return out
        switches = _settings.read()
        out['on'] = _settings.get(switches, 'on') is True
        out['dry'] = _settings.get(switches, 'dry') is True
        steps = (('plan', make_plan), ('codex', codex), ('fold', fold), ('trim', trim), ('signals', signals)) \
            if out['on'] else (('plan', take_plan_away), ('fold', fold))
        for name, step in steps:
            if left() <= 0:
                out['stopped_early'] = True
                break
            try:
                step()
            except Exception as e:  # noqa: BLE001 - one step that fails leaves the others to run
                out['errors'].append(name)
                if name == 'plan':
                    out['plan'] = 'failed'
                _log(name, e)
    except Exception as e:  # noqa: BLE001 - a pass of the loop never stops on the router
        out['errors'].append('pass')
        _log('pass', e)
    finally:
        out['took_s'] = round(time.monotonic() - start, 3)
    return out


# ---------------------------------------------------------------- the person's one switch

def _plugin_line(r) -> str:
    """plugin_setting's answer in one line. After a write it keeps every note on what is not known (an older
    Claude Code also installed, the key also set in the environment, whether open sessions read it), so the line
    never states as certain what the module could not check."""
    line = r.get('blocked') or r.get('says') or ''
    if r.get('blocked') and plugin_setting.W.STILL_THERE in r.get('notes', ()):
        line = '%s %s' % (line, plugin_setting.W.STILL_THERE)
    if r.get('written'):
        made = (plugin_setting.W.MAKES_FILE, plugin_setting.W.MAKES_ENV)
        line = ' '.join([line] + [n for n in r.get('notes', ()) if n not in made])
    return line.strip()


def _codex_ready(p, codex_home):
    """(True, None) when Codex's files may be written for this plan, else (False, the line that says why)."""
    if not isinstance(p, dict) or not isinstance(p.get('order'), list):
        return False, W.CODEX_UNKNOWN
    if 'codex' not in p['order']:
        return False, W.CODEX_NOT_ADDED
    agent = (p.get('agents') or {}).get('codex') or {}
    if agent.get('why_not') == 'not_installed':
        return False, W.CODEX_NOT_INSTALLED
    home = os.path.abspath(os.path.expanduser(codex_home or codex_files.default_codex_home()))
    if not os.path.isdir(home):
        return False, W.CODEX_NO_FOLDER.format(path=home)
    return True, None


def _count(one, many, n) -> str:
    return one.get(n) or many.format(n=n)


def _files_lines(r, wanted=True):
    """The lines for codex_files.apply()'s answer. wanted: the plan names at least one Codex helper file."""
    if r.get('dry') or r.get('message'):
        out = [r['message']]
    elif r.get('written'):
        out = [_count(W.CODEX_FILES_WROTE, W.CODEX_FILES_WROTE_N, len(r['written']))]
    elif r.get('removed'):
        out = [_count(W.CODEX_FILES_REMOVED, W.CODEX_FILES_REMOVED_N, len(r['removed']))]
    else:
        out = [W.CODEX_FILES_SAME if wanted else W.CODEX_FILES_NONE]
    return [' '.join(out + list(r.get('left') or []))]


def _apply_files(p, codex_home):
    home = codex_home or codex_files.default_codex_home()
    wanted = bool(codex_files.plan_files(p, home, p.get('made_at')))
    return _files_lines(codex_files.apply(p, codex_home, now=p.get('made_at')), wanted)


def _codex_on(p, codex_home, facts=None):
    """Codex's hooks and helper files for this plan. facts (turn_on's): what was written, for its short form; a
    Codex that is not ready leaves nothing there, so the short form never names it."""
    facts = {} if facts is None else facts
    ready, why = _codex_ready(p, codex_home)
    if not ready:
        return [why]
    lines = []
    c = facts.setdefault('codex', {'hooks': False, 'files': 0, 'trust': None})
    try:
        r = codex_files.hooks_apply(codex_home)
        line = r.get('message') or ''
        if r.get('trust'):
            line = '%s %s' % (line, r['trust'])
        c['hooks'], c['trust'] = bool(r.get('changed')), r.get('trust') if r.get('changed') else None
        lines.append(line.strip())
    except Exception as e:  # noqa: BLE001 - said, and the other steps stand
        _log('codex hooks', e)
        lines.append(W.STEP_FAILED.format(why=_why(e)))
        facts.setdefault('failed', []).append(lines[-1])
    try:
        home = codex_home or codex_files.default_codex_home()
        wanted = bool(codex_files.plan_files(p, home, p.get('made_at')))
        r = codex_files.apply(p, codex_home, now=p.get('made_at'))
        c['files'] = len(r.get('written') or ())
        lines += _files_lines(r, wanted)
    except Exception as e:  # noqa: BLE001
        _log('codex files', e)
        lines.append(W.STEP_FAILED.format(why=_why(e)))
        facts.setdefault('failed', []).append(lines[-1])
    return lines


def _codex_record():
    try:
        return os.path.exists(codex_files.default_state_path())
    except Exception:  # noqa: BLE001 - no data folder: nothing was written
        return False


def _codex_off(codex_home, did=None):
    """The lines for taking Codex's hooks and helper files back. did: a list that gets True when something was
    taken out."""
    did = [] if did is None else did
    if not _codex_record():
        return [W.CODEX_NOTHING]
    lines = []
    for what, call in (('codex hooks', lambda: codex_files.hooks_undo(codex_home)),
                       ('codex files', lambda: codex_files.undo(codex_home))):
        try:
            r = call()
            if r.get('changed') or r.get('removed'):
                did.append(True)
            if what == 'codex files' and not r.get('dry') and not r.get('message'):
                gone = len(r.get('removed') or ())
                lines.append(_count(W.CODEX_FILES_REMOVED, W.CODEX_FILES_REMOVED_N, gone) if gone
                             else W.CODEX_FILES_GONE)
                lines += list(r.get('left') or [])
            else:
                lines.append(r.get('message') or '')
        except Exception as e:  # noqa: BLE001
            _log(what, e)
            lines.append(W.STEP_FAILED.format(why=_why(e)))
    return [x for x in lines if x]


def _write_plan(now, kit):
    """(the plan written, or None; its line)."""
    try:
        p = _plan.gather(now, kit)
        _plan.write(p)
        return p, W.PLAN_WRITTEN
    except Exception as e:  # noqa: BLE001 - said; the loop writes it again at its next look
        _log('plan', e)
        return None, W.PLAN_FAILED.format(why=_why(e))


def _cache_on(cache, claude_dir, versions, facts=None):
    """The helpers' cache lifetime the person's own count calls for, set in one line; [] when there is no count
    or it calls for nothing (the key is left alone). facts (turn_on's): whether it was written."""
    if cache is None:
        return []
    try:
        value = cache()
        if value is None:
            return []
        from . import cache_setting
        r = cache_setting.apply(value, claude_dir=claude_dir, versions=versions)
        if r.get('written'):
            core.write_bytes(core.data_path(CACHE_MARK), b'', 0o600)
            if facts is not None:
                facts['cache_written'] = True
        return [(r.get('blocked') or r.get('says') or '').strip()]
    except Exception as e:  # noqa: BLE001 - said, and the other steps stand
        _log('cache', e)
        return [W.STEP_FAILED.format(why=_why(e))]


def _cache_off(claude_dir, versions):
    """Take back the helpers' cache key only when turn_on set it; the cache's own switch is left as it is."""
    try:
        if not os.path.exists(core.data_path(CACHE_MARK)):
            return []
        from . import cache_setting
        r = cache_setting.undo(claude_dir=claude_dir, versions=versions)
        if not r.get('blocked'):
            os.remove(core.data_path(CACHE_MARK))
        return [(r.get('blocked') or r.get('says') or '').strip()]
    except Exception as e:  # noqa: BLE001
        _log('cache', e)
        return [W.STEP_FAILED.format(why=_why(e))]


def turn_on(kit=None, claude_dir=None, codex_home=None, versions=None, now=None, cache=None) -> list:
    """The person turns routing on (the first run, `switchboard routing on`). Returns at most TURN_ON_BRIEF_MAX lines:
    the sentence the bare list shows for routing now, then each change it made with its undo. An app that is not on
    this Mac, or not on the person's list, is never named. Safe to run twice; never raises. cache: as
    turn_on_steps."""
    lines, facts = _turn_on(kit, claude_dir, codex_home, versions, now, cache)
    if facts.get('switch_failed'):
        return lines
    try:
        return _brief(facts, facts['now'], claude_dir, versions)
    except Exception as e:  # noqa: BLE001 - turn_on never raises: the first run prints what it returns
        _log('brief', e)
        return [W.TURNED_ON] + list(facts.get('failed') or [])[:TURN_ON_BRIEF_MAX - 1]


def turn_on_steps(kit=None, claude_dir=None, codex_home=None, versions=None, now=None, cache=None) -> list:
    """The same work as turn_on, said one line per step, at most TURN_ON_MAX (for a face that shows each step).
    cache: a function of no arguments for the helpers' cache lifetime the person's own count calls for ('1h' or
    '5m'), or None to leave the key alone (the default; a function that returns None does the same)."""
    return _turn_on(kit, claude_dir, codex_home, versions, now, cache)[0]


def _turn_on(kit, claude_dir, codex_home, versions, now, cache):
    """(the step lines, the facts each step left), for turn_on and turn_on_steps."""
    now = time.time() if now is None else now
    facts = {'now': now}
    try:
        _settings.set_switch('on', True)
    except Exception as e:  # noqa: BLE001 - nothing else is done without the switch
        facts['switch_failed'] = True
        return [W.SWITCH_FAILED.format(why=_why(e))], facts
    dry = _settings.get(_settings.read(), 'dry') is True
    lines = [W.ON_TRIAL if dry else W.TURNED_ON]
    p, line = _write_plan(now, kit)
    lines.append(line)
    if line != W.PLAN_WRITTEN:
        facts['failed'] = [line]
    try:
        r = plugin_setting.apply(claude_dir=claude_dir, versions=versions)
        facts['plugin'] = r
        lines.append(_plugin_line(r))
    except Exception as e:  # noqa: BLE001 - said, and the other steps stand
        _log('plugin', e)
        lines.append(W.STEP_FAILED.format(why=_why(e)))
        facts.setdefault('failed', []).append(lines[-1])
    for step, args in ((_codex_on, (p, codex_home, facts)), (_cache_on, (cache, claude_dir, versions, facts))):
        try:                        # turn_on never raises: the first run prints what it returns
            lines += step(*args)
        except Exception as e:  # noqa: BLE001 - said, and the other steps stand
            _log(step.__name__, e)
            lines.append(W.STEP_FAILED.format(why=_why(e)))
            facts.setdefault('failed', []).append(lines[-1])
    lines = [x for x in lines if x]
    if len(lines) > TURN_ON_MAX:     # kept to six by joining the last ones, so no line is lost
        lines = lines[:TURN_ON_MAX - 1] + [' '.join(lines[TURN_ON_MAX - 1:])]
    return lines, facts


def _brief(facts, now, claude_dir, versions) -> list:
    """turn_on's lines from the facts its steps left: the list's own sentence, then each change with its undo."""
    try:
        first = lines(now, claude_dir=claude_dir, versions=versions)[0]
    except Exception as e:  # noqa: BLE001 - the switch is on: say that much
        _log('brief', e)
        first = W.TURNED_ON
    changes = []
    r = facts.get('plugin')
    if r is not None:
        if r.get('written'):
            made = (plugin_setting.W.MAKES_FILE, plugin_setting.W.MAKES_ENV)
            changes.append(' '.join([W.BRIEF_PLUGIN] + [n for n in r.get('notes', ()) if n not in made]))
        elif r.get('blocked'):
            changes.append(_plugin_line(r))              # why Claude Code's part is not set
    c = facts.get('codex') or {}
    if c.get('hooks') or c.get('files'):
        files = _count(W.BRIEF_CODEX_FILES, W.BRIEF_CODEX_FILES_N, c['files']) if c.get('files') else ''
        changes.append(W.BRIEF_CODEX.format(files=files) + (' ' + c['trust'] if c.get('trust') else ''))
    if facts.get('cache_written'):
        changes.append(W.BRIEF_CACHE)
    changes += facts.get('failed') or []
    out = [first] + [x for x in changes if x]
    if len(out) > TURN_ON_BRIEF_MAX:     # kept to three by joining the last ones, so no change is left unsaid
        out = out[:TURN_ON_BRIEF_MAX - 1] + [' '.join(out[TURN_ON_BRIEF_MAX - 1:])]
    return out


def turn_off(kit=None, claude_dir=None, codex_home=None, versions=None) -> list:
    """The person turns routing off. Everything turn_on wrote is taken back by its own undo. When routing was never
    on, nothing is written at all. Returns the lines to show. Safe to run twice."""
    if not made():
        return [W.ALREADY_OFF]
    lines, did = [], []
    try:
        was_on = _settings.get(_settings.read(), 'on') is True
        if was_on:
            _settings.set_switch('on', False)
        lines.append(W.TURNED_OFF if was_on else W.ALREADY_OFF)     # ALREADY_OFF is changed below if a step did
    except _settings.SettingsError as e:    # a switches file that cannot be read already counts as off
        lines.append(W.OFF_SWITCH_FAILED.format(why=_why(e)))
    except Exception as e:  # noqa: BLE001 - not saved (a full disk, a lock): routing is still on, so nothing goes
        _log('switch', e)
        return [W.OFF_NOT_SAVED.format(why=_why(e))]
    try:
        gone = _remove_plan()
        did += [True] if gone else []
        lines.append(W.PLAN_REMOVED if gone else W.PLAN_NONE)
    except Exception as e:  # noqa: BLE001
        _log('plan', e)
        lines.append(W.PLAN_REMOVE_FAILED.format(why=_why(e)))
    try:
        r = plugin_setting.undo(claude_dir=claude_dir, versions=versions)
        did += [True] if r.get('written') else []
        lines.append(_plugin_line(r))
    except Exception as e:  # noqa: BLE001
        _log('plugin', e)
        lines.append(W.STEP_FAILED.format(why=_why(e)))
    lines += _codex_off(codex_home, did)
    gone = _cache_off(claude_dir, versions)
    did += [True] if gone else []
    lines += gone
    if did and lines[0] == W.ALREADY_OFF:
        lines[0] = W.TURNED_OFF      # the switch was off, but something of an earlier on was still there
    return [x for x in lines if x]


def _routing_on() -> bool:
    """True when the router folder is there and the switch in it is on. Reads only; nothing when never on."""
    return made() and _settings.get(_settings.read(say=False), 'on') is True


def _files_out(codex_home):
    """Take Codex's helper files out for a trial run (its hooks stay: they do nothing under a trial plan)."""
    r = codex_files.undo(codex_home)
    if r.get('dry') or r.get('message'):
        return [r.get('message') or '']
    gone = len(r.get('removed') or ())
    return ([_count(W.CODEX_FILES_REMOVED, W.CODEX_FILES_REMOVED_N, gone)] if gone else []) + \
        list(r.get('left') or [])


def trial(on: bool, kit=None, now=None, codex_home=None) -> list:
    """Turn the trial run on or off while routing is on; a new plan is written at once so the adapters follow it.
    On: Codex's helper files Switchboard wrote are taken out, so no app changes anything. Off: they are written
    again from the routes when Codex is set up. While routing is off it writes nothing and says so in one line.
    Returns the lines to show."""
    if not isinstance(on, bool):
        raise TypeError('a switch is True or False')
    if not _routing_on():
        return [W.TRIAL_ROUTING_OFF]
    now = time.time() if now is None else now
    try:
        _settings.set_switch('dry', on)
    except Exception as e:  # noqa: BLE001
        return [W.SWITCH_FAILED.format(why=_why(e))]
    lines = [W.TRIAL_ON if on else W.TRIAL_OFF]
    p, line = _write_plan(now, kit)
    lines.append(line)
    if p is not None and _codex_record():
        try:
            if on:
                lines += _files_out(codex_home)
            elif _codex_ready(p, codex_home)[0]:
                lines += _apply_files(p, codex_home)
        except Exception as e:  # noqa: BLE001
            _log('codex files', e)
            lines.append(W.STEP_FAILED.format(why=_why(e)))
    return [x for x in lines if x]


def apply_for_real(kit=None, codex_home=None, now=None) -> list:
    """Turn the trial run off: the adapters now apply the person's routes, and Codex's helper files are written
    when Codex is set up. While routing is off it writes nothing and says so in one line. Returns the lines to
    show."""
    return trial(False, kit=kit, now=now, codex_home=codex_home)


# ---------------------------------------------------------------- what the terminal list shows

def _age(seconds) -> str:
    s = max(0, int(seconds))
    for size, one, many in ((86400, W.DAYS, W.DAYS_N), (3600, W.HOURS, W.HOURS_N), (60, W.MINUTES, W.MINUTES_N)):
        if s >= size:
            n = s // size
            return one.get(n) or many.format(n=n)
    return W.SECONDS.get(s) or W.SECONDS_N.format(n=s)


def _last(now):
    """(the newest decision record that has a History sentence, its sentence), or (None, None)."""
    recs, _ = _record.read(50)
    for rec in reversed(recs):
        try:
            text = _record.line(rec)
        except Exception as e:  # noqa: BLE001 - one odd record is passed over; the list still shows
            _log('last', e)
            continue
        if text:
            return rec, text
    return None, None


def _ran_on(rec):
    """The model a piece of work ran on, as the list of models names it, or None when the record does not say."""
    if rec.get('did') in ('model', 'hand'):
        pid, word = (rec.get('to') if rec.get('did') == 'hand' else rec.get('agent')), rec.get('model')
    else:
        pid, word = rec.get('agent'), rec.get('from_model')
    name = _record._model(pid, word)
    return None if name in (None, _words.A_MODEL) else name


def _seen():
    """What the record holds now, counted: {'helper', 'workflow' (how many of each were recorded), 'models'
    ([(name, how many)], most first)}, or None when it holds no helper or workflow agent."""
    recs, _ = _record.read()
    count = {'helper': 0, 'workflow': 0}
    models = collections.Counter()
    for rec in recs:
        if rec.get('where') not in count:
            continue
        count[rec['where']] += 1
        try:
            name = _ran_on(rec)
        except Exception:  # noqa: BLE001 - a model that cannot be named is not counted by name
            name = None
        if name:
            models[name] += 1
    if not count['helper'] and not count['workflow']:
        return None
    count['models'] = sorted(models.items(), key=lambda kv: (-kv[1], kv[0]))
    return count


def _seen_line(seen) -> str:
    what = W.SEEN_AND.join([_count(one, many, seen[k]) for k, one, many in
                            (('helper', W.HELPERS, W.HELPERS_N), ('workflow', W.WORKFLOWS, W.WORKFLOWS_N)) if seen[k]])
    shown = [W.SEEN_MODEL.format(model=m, n=n) for m, n in seen['models'][:SEEN_MODELS]]
    rest = seen['models'][SEEN_MODELS:]
    if rest:
        k, n = len(rest), sum(x for _, x in rest)
        shown.append(W.SEEN_OTHER_MODELS[1].format(n=n) if k == 1 else W.SEEN_OTHER_MODELS_N.format(k=k, n=n))
    if not shown:
        return W.SEEN_NO_MODEL.format(what=what)
    return W.SEEN.format(what=what, models=', '.join(shown))


def status(now, claude_dir=None, versions=None) -> dict:
    """What the terminal list shows, as data. Reads only, and nothing at all when routing was never on.

    Returns {'on', 'dry', 'plan_age_s' (None for no plan), 'plan_usable', 'last' (the newest decision's History
    sentence, or None), 'last_t', 'seen' (_seen()'s counts of what the record holds, or None), 'plugin'
    (plugin_setting.status()'s 'state' and 'loaded', or None when off), 'cache' (the helpers' cache setting's
    first line, or None when off)}."""
    out = {'on': False, 'dry': False, 'plan_age_s': None, 'plan_usable': False, 'last': None, 'last_t': None,
           'seen': None, 'plugin': None, 'cache': None, 'nothing_set': False}
    if not made():
        return out
    s = _settings.read(say=False)
    out['on'] = _settings.get(s, 'on') is True
    out['dry'] = _settings.get(s, 'dry') is True
    if not out['on']:
        return out
    p = _plan.read()
    if isinstance(p, dict) and isinstance(p.get('made_at'), (int, float)) and not isinstance(p['made_at'], bool):
        out['plan_age_s'] = max(0.0, now - p['made_at'])
        out['plan_usable'] = _resolve.usable(p, now)
        out['nothing_set'] = _nothing_set(p)
    try:
        rec, text = _last(now)
    except Exception as e:  # noqa: BLE001 - the list still shows the rest
        _log('last', e)
        rec, text = None, None
    if rec is not None:
        out['last'], out['last_t'] = text, rec['t']
    try:
        out['seen'] = _seen()
    except Exception as e:  # noqa: BLE001 - the list still shows the rest
        _log('seen', e)
    try:
        st = plugin_setting.status(claude_dir=claude_dir, versions=versions)
        out['plugin'] = {'state': st['state'], 'loaded': st['loaded']}
    except Exception as e:  # noqa: BLE001 - the list still shows the rest
        _log('plugin status', e)
    try:
        from . import cache_setting
        cs = cache_setting.status(claude_dir=claude_dir, versions=versions)
        out['cache'] = cs['lines'][0] if cs.get('lines') else None
    except Exception as e:  # noqa: BLE001
        _log('cache status', e)
    return out


def _nothing_set(p) -> bool:
    """The plan holds no route and no default, so no piece of work can be changed by it."""
    if p.get('routes'):
        return False
    for d in (p.get('defaults') or {}).values():
        if isinstance(d, dict) and (d.get('model') or d.get('effort')):
            return False
    return True


def lines(now, claude_dir=None, versions=None) -> list:
    """The terminal list's lines for routing: ONE when off, at most four when on: the switch, the plan's age,
    what the record holds (counted), the last decision, then the helpers' cache. Reads only."""
    st = status(now, claude_dir=claude_dir, versions=versions)
    if not st['on']:
        return [W.OFF]
    if st['nothing_set'] and not st['dry'] and not st['last'] and not st['seen']:
        return [W.ON_NOTHING_SET]           # nothing could change and nothing was seen: one line says all of it
    loaded = st['plugin'] is None or st['plugin'].get('loaded')
    if st['dry']:
        out = [W.ON_TRIAL if loaded else W.ON_NO_PLUGIN_TRIAL]
    else:
        out = [W.ON if loaded else W.ON_NO_PLUGIN]
    if st['plan_age_s'] is None:
        out.append(W.PLAN_MISSING)
    elif st['plan_usable']:
        out.append(W.PLAN_AGE.format(age=_age(st['plan_age_s'])))
    else:
        out.append(W.PLAN_OLD.format(age=_age(st['plan_age_s'])))
    if st['seen']:
        out.append(_seen_line(st['seen']))
    if st['last']:
        out.append(W.LAST.format(line=st['last']))
    elif not st['seen']:
        out.append(W.LAST_NONE)
    if st['cache']:
        out.append(st['cache'])
    return out[:4]


# ---------------------------------------------------------------- History

def _key(rec) -> str:
    return json.dumps(rec, sort_keys=True, separators=(',', ':'))


def history(now, since) -> list:
    """The decisions folded into the record since `since` (epoch seconds; None for every one not given yet in this
    process), as [(t, sentence)], oldest first, for the loop to put into History. At most HISTORY_MAX sentences: the
    newest are kept, and the rest are counted in one line put first. Pass the largest t returned as the next
    `since`. A decision folded a little late is still given once. Reads only; nothing when routing was never on."""
    if not made():
        return []
    try:
        recs, _ = _record.read()
    except Exception as e:  # noqa: BLE001 - History waits for the next pass
        _log('history', e)
        return []
    floor = None if since is None else since - HISTORY_SLACK_S
    pairs = []
    for rec in recs:
        if floor is not None and rec['t'] <= floor:
            continue
        k = _key(rec)
        if k in _SHOWN:
            continue
        try:
            pairs += _record.lines([rec])
        except Exception as e:  # noqa: BLE001 - not marked as given, so the next call tries it again
            _log('history', e)
            continue
        _SHOWN.append(k)        # marked only once its sentence is made
    if len(pairs) > HISTORY_MAX:
        dropped = pairs[:-HISTORY_MAX]
        pairs = [(dropped[-1][0], _count(W.MORE, W.MORE_N, len(dropped)))] + pairs[-HISTORY_MAX:]
    return pairs
