"""The hook line: one short piece of context a Claude Code or Codex chat is given about the person's routes.

Codex has no hook that can set a model. So in Codex the routes reach the work through its own helper files (each may
carry a model and an effort: codex_files.py) and through this line, which says which helper is for which kind of
work. A Claude Code that cannot load Switchboard's plugin gets the same line, naming the model to start a helper with.

What a chat is told:
  SessionStart, and a chat's first prompt   the routes as at most four short lines, and one line on how to follow
                                            them (at most five lines)
  UserPromptSubmit, after that              one line, only when a fact the chat was told has changed: an app lost its
                                            room or has it back, the routes changed, routing went off
  anything else                             nothing
Nothing at all when the plan is missing, old, off or in its trial mode, when it has no route for this app, or when
nothing changed. Never over CAP characters.

It never reads the prompt. main() reads the hook's JSON from standard input and keeps only hook_event_name, session_id
and source (every other key is dropped while the JSON is parsed). What a chat has been told is kept as hashes in
<data folder>/router/seen/<first 16 hex of the session id's sha256>.json, removed after KEEP seconds. The data folder
is $SWITCHBOARD_HOME, else ~/.switchboard (the router contract's rule for adapters). It reads the plan file and its
own seen files, and writes only its own seen files.

The hooks file runs command(agent): this file's folder goes on the path, so only the standard library and
adapter_words are loaded, and the exit code is always 0. It ends itself after LIMIT seconds. For a person at a
terminal, with the copy's top folder as the current folder: python3 -S -m switchboard.router.hookline claude|codex.
"""
import hashlib
import json
import math
import os
import sys
import time

try:
    from . import adapter_words as W
except ImportError:             # run by a hooks file: this file's folder is on the path (command())
    import adapter_words as W

MARK = '# switchboard router'
AGENTS = ('claude', 'codex')
EVENTS = ('SessionStart', 'UserPromptSubmit')
FIELDS = ('hook_event_name', 'session_id', 'source')     # the only keys of the hook's input that are kept
CAP = 600                       # characters of what the line says, ever
MAX_KINDS = 4                   # route lines at a start; the how line makes five
PLAN_MAX = 16 << 10             # a plan file bigger than this is not read
MAX_INPUT = 32 << 20            # bytes of the hook's input read at most
LIMIT = 3                       # seconds; then the program ends itself, printing nothing
KEEP = 7 * 86400                # a seen file is removed a week after its last change
SEEN_V = 1
KINDS = ('build', 'fix', 'review', 'plan', 'find', 'write', 'other')
CODEX_HELPER = 'switchboard-{kind}'     # the name of the helper codex_files.py writes for a kind


# ---------------------------------------------------------------- the rule (router contract, section 2)
#
# The core's switchboard/router/resolve.py is the reference for this rule and replaces _resolve when the two are
# joined. Until then this is the same rule, kept here so the line loads nothing outside this folder (the core's
# module also imports providers/kinds.py, for a helper's kind, which the line does not need). Both are held to
# every resolve case of tests/router_fixtures.json.

ROOMS = ('room', 'out', 'unknown')
WHY_NOT = ('not_added', 'not_installed', 'signed_out', 'too_old', 'never')
WHERE = ('workflow', 'helper', 'chat')
MAX_GOOD_FOR_S = 3600       # a plan that says it is good for longer cannot be used (no_plan)
AHEAD_S = 60                # a plan made more than this after now is not current (old_plan)


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _problem(plan, now):
    """Why the plan cannot be used now (no_plan, old_plan, off), or None."""
    if not isinstance(plan, dict):
        return 'no_plan'
    v, hand = plan.get('v'), plan.get('hand')
    if not _num(v) or v != 1:
        return 'no_plan'
    if not (_num(plan.get('made_at')) and _num(plan.get('good_for_s')) and isinstance(plan.get('on'), bool)
            and isinstance(plan.get('agents'), dict) and isinstance(plan.get('routes'), dict)
            and isinstance(hand, dict) and isinstance(hand.get('on'), bool)):
        return 'no_plan'
    if plan['good_for_s'] > MAX_GOOD_FOR_S:
        return 'no_plan'
    # A plan from the future (a clock set back, a hand-made file) is not current, however long it says it is good.
    if not _num(now) or now > plan['made_at'] + plan['good_for_s'] or plan['made_at'] > now + AHEAD_S:
        return 'old_plan'
    if not plan['on']:
        return 'off'
    return None


def unusable(plan, now):
    """None when the plan may be used now, else the reason code: no_plan, old_plan or off."""
    try:
        return _problem(plan, now)
    except Exception:
        return 'no_plan'


def _dict(x):
    return x if isinstance(x, dict) else {}


def _route(plan, kind):
    """This kind's choices as (app, model, effort); [] for no route; None for a route the rule cannot read."""
    if not isinstance(kind, str):
        return []
    got = plan['routes'].get(kind)
    if got is None:
        return []
    if not isinstance(got, list):
        return None
    out = []
    for c in got:
        if not isinstance(c, dict) or not isinstance(c.get('agent'), str):
            return None
        model, effort = c.get('model'), c.get('effort')
        if (model is not None and not isinstance(model, str)) or (effort is not None and not isinstance(effort, str)):
            return None
        out.append((c['agent'], model, effort))
    return out


def _look(agents, agent, here, hand_on):
    """(pass code or None, room when not passed, back_at when passed as out)."""
    a = agents.get(agent)
    if not isinstance(a, dict):
        return 'not_added', None, None
    if a.get('can') is not True:
        why = a.get('why_not')
        return (why if isinstance(why, str) and why in WHY_NOT else 'not_added'), None, None
    room = a.get('room') if a.get('room') in ROOMS else 'unknown'
    if room == 'out':
        back = a.get('back_at')
        return 'out', None, back if _num(back) else None
    if agent != here and not hand_on:
        return 'hand_off', None, None
    return None, room, None


def _default(plan, where, here):
    """(model, effort) of the plan's default for `where`, or None when there is none it can read or it names
    neither. A default's model names its agent ({"claude": "haiku"}): only the entry for `here` is read, so a model
    set for one agent is never given to another. A model that is not such an object (a bare word) cannot be read."""
    if where not in WHERE:
        return None
    got = plan.get('defaults')
    got = got.get(where) if isinstance(got, dict) else None
    if not isinstance(got, dict):
        return None
    model, effort = got.get('model'), got.get('effort')
    if isinstance(model, dict):
        model = model.get(here) if isinstance(here, str) else None
    elif model is not None:
        return None
    if not (model is None or isinstance(model, str)) or not (effort is None or isinstance(effort, str)):
        return None
    if model is None and effort is None:
        return None
    return model, effort


def _answer(do, kind, passed, why, **more):
    out = {'do': do}
    out.update(more)
    out.update(kind=kind, passed=[list(p) for p in passed], why=why)
    return out


def _resolve_or_raise(plan, kind, here, now, where):
    problem = _problem(plan, now)
    if problem is not None:
        return _answer('nothing', kind, (), None, reason=problem)
    words = plan.get('why')
    why = words.get(kind) if isinstance(words, dict) and isinstance(kind, str) else None
    why = why if isinstance(why, str) else None
    route = _route(plan, kind)
    if route is None:
        return _answer('nothing', kind, (), None, reason='no_plan')
    if not route:
        got = _default(plan, where, here)
        if got is not None:
            return _answer('model', kind, (), None, model=got[0], effort=got[1], reason='default')
        return _answer('nothing', kind, (), why, reason='no_route')
    looked = [_look(plan['agents'], agent, here, plan['hand']['on']) for agent, _, _ in route]
    passed, backs = [], []
    for i, ((agent, model, effort), (code, room, back)) in enumerate(zip(route, looked)):
        if code is None and room == 'unknown' and any(c is None and r == 'room' for c, r, _ in looked[i + 1:]):
            code = 'unknown'
        if code is not None:
            passed.append((agent, code))
            if back is not None:
                backs.append(back)
            continue
        if agent != here:
            return _answer('hand', kind, passed, why, to=agent, model=model, effort=effort, reason='route')
        if model is None and effort is None:
            return _answer('nothing', kind, passed, why, reason='stays')
        return _answer('model', kind, passed, why, model=model, effort=effort, reason='route')
    return _answer('nothing', kind, passed, why, reason='all_passed', back_at=min(backs) if backs else None)


def _resolve(plan, kind, here, now, where=None):
    """What the plan says for a piece of work of this kind started in agent `here` as `where` (see the contract).
    Never raises: anything the rule cannot read is {"do": "nothing", "reason": "no_plan"}."""
    try:
        return _resolve_or_raise(plan, kind, here, now, where)
    except Exception:
        return _answer('nothing', kind, (), None, reason='no_plan')


# ---------------------------------------------------------------- the words

def _ok_word(x, size=64):
    """A kind, model or effort word that is safe to put in a line: short, printable, one line."""
    return isinstance(x, str) and 0 < len(x) <= size and x.isprintable()


def _when(t, now):
    """A clock time for a person: 3:05 PM today, else Wed 3:05 PM."""
    lt, ln = time.localtime(t), time.localtime(now)
    clock = '%d:%02d %s' % ((lt.tm_hour % 12) or 12, lt.tm_min, 'AM' if lt.tm_hour < 12 else 'PM')
    if (lt.tm_year, lt.tm_yday) == (ln.tm_year, ln.tm_yday):
        return clock
    return time.strftime('%a ', lt) + clock


def _kinds(plan):
    """The plan's kinds with a route, in the plan's order of kinds (then KINDS, then the rest by name)."""
    routes = _dict(plan.get('routes'))
    order = _dict(plan.get('kinds')).get('order')
    order = [k for k in order if isinstance(k, str)] if isinstance(order, list) else []
    seen, out = set(), []
    for k in order + list(KINDS) + sorted(k for k in routes if isinstance(k, str)):
        if k in routes and k not in seen and _ok_word(k, 32):
            seen.add(k)
            out.append(k)
    return out


def kind_lines(agent, plan, now):
    """[(kind, the line, what the rule said: 'model', 'hand' or 'nothing')] for every kind with something to say to a
    chat in `agent`."""
    out = []
    agents = _dict(plan.get('agents'))
    for kind in _kinds(plan):
        # where is None: a default is for work whose kind has no route, and the line names only routes.
        r = _resolve(plan, kind, agent, now, None)
        name = W.kind_name(kind)
        if r['do'] == 'nothing':
            if r['reason'] != 'all_passed' or all(code == 'hand_off' for _, code in r['passed']):
                continue                    # stays where it was typed, or a route only for another app
            text = W.LINE_ALL_PASSED.format(kind=name)
            if r.get('back_at') is not None:
                text += W.BACK_AT.format(when=_when(r['back_at'], now))
            out.append((kind, text, 'nothing'))
            continue
        model = r['model'] if _ok_word(r['model']) else None
        effort = r['effort'] if _ok_word(r['effort'], 16) else None
        detail = W.detail(model, effort)
        if r['do'] == 'model':
            if not detail:
                continue
            if agent == 'codex':
                text = W.LINE_HELPER.format(kind=name, helper=CODEX_HELPER.format(kind=kind), detail=detail)
            elif model:
                # Claude Code's Agent call takes a model and no effort: the line names only what can be followed.
                text = W.LINE_CLAUDE.format(kind=name, model=model)
            else:
                continue
            who = agent
        else:
            who = r['to']
            name_of = W.agent_name(who) if _ok_word(who, 32) else W.agent_name('another app')
            text = (W.LINE_HAND if detail else W.LINE_HAND_BARE).format(kind=name, agent=name_of, detail=detail)
        if _dict(agents.get(who)).get('room') != 'room':
            text += W.ROOM_UNKNOWN
        out.append((kind, text, r['do']))
    return out


def _how(agent, lines):
    how = W.HOW_CODEX if agent == 'codex' else W.HOW_CLAUDE
    return how + (W.HOW_HAND if any(do == 'hand' for _, _, do in lines) else '')


def _cut(text, cap=CAP):
    return text if len(text) <= cap else text[:cap - 1].rstrip() + '…'


def start_text(agent, plan, now):
    """The routes as short lines and the how line, or '' when there is nothing to say."""
    if unusable(plan, now) or plan.get('dry') is True:
        return ''
    lines = kind_lines(agent, plan, now)
    if not lines:
        return ''
    shown = lines[:MAX_KINDS]
    while shown:
        text = '\n'.join([t for _, t, _ in shown] + [_how(agent, shown)])
        if len(text) <= CAP:
            return text
        shown = shown[:-1]
    return _cut(_how(agent, lines))


# ---------------------------------------------------------------- what a chat was told

def _h(*parts):
    return hashlib.sha256('\x00'.join(parts).encode('utf-8')).hexdigest()[:16]


def told(agent, plan, now):
    """What a chat in `agent` has been told when it was given this plan's line: hashes only."""
    rec = {'v': SEEN_V, 'agent': _h('agent', agent)}
    if unusable(plan, now) or plan.get('dry') is True:
        rec.update(on=_h('on', 'no'), lines=_h('lines'), apps={})
        return rec
    lines = kind_lines(agent, plan, now)
    rec.update(on=_h('on', 'yes'), lines=_h('lines', *[t for _, t, _ in lines]))
    rec['apps'] = dict((_h('agent', a), _h('room', a, str(_dict(info).get('room'))))
                       for a, info in _dict(plan.get('agents')).items() if isinstance(a, str))
    return rec


def _change_text(agent, plan, seen, now):
    reason = unusable(plan, now)
    was_on = seen.get('on') == _h('on', 'yes')
    if reason == 'off' or (reason is None and plan.get('dry') is True):
        return W.OFF if was_on and seen.get('lines') != _h('lines') else ''
    if reason:
        return ''                       # missing or old: Switchboard may not be running; say nothing
    lines = kind_lines(agent, plan, now)
    if seen.get('on') == _h('on', 'yes') and seen.get('lines') == _h('lines', *[t for _, t, _ in lines]):
        return ''
    if not lines:
        return W.NONE_NOW if was_on and seen.get('lines') != _h('lines') else ''
    lead = W.CHANGED
    rooms = seen.get('apps') if isinstance(seen.get('apps'), dict) else {}
    lost, back = [], []
    for a, info in sorted(_dict(plan.get('agents')).items()):
        key, room = _h('agent', a), _dict(info).get('room')
        if key not in rooms or rooms[key] == _h('room', a, str(room)):
            continue
        if room == 'out':
            lost.append(W.LOST.format(agent=W.agent_name(a)))
        elif room == 'room' and rooms[key] == _h('room', a, 'out'):
            back.append(W.BACK.format(agent=W.agent_name(a)))
    if lost or back:
        lead = ' '.join(lost + back)
    body = ' '.join(t for _, t, _ in lines)
    return _cut(lead + W.NOW.format(lines=body))


def line(agent, event, plan, seen, now):
    """What a chat in `agent` is told at this hook event: '' for nothing. seen: what told() returned when the chat
    was last told something, or None when it has been told nothing yet. Pure: reads nothing, writes nothing."""
    if agent not in AGENTS or event not in EVENTS:
        return ''
    if event == 'SessionStart' or not isinstance(seen, dict) or seen.get('v') != SEEN_V:
        return start_text(agent, plan, now)
    return _change_text(agent, plan, seen, now)


# ---------------------------------------------------------------- files

def data_dir(env=None):
    env = os.environ if env is None else env
    return env.get('SWITCHBOARD_HOME') or os.path.join(os.path.expanduser('~'), '.switchboard')


def plan_path(env=None):
    return os.path.join(data_dir(env), 'router', 'plan.json')


def seen_dir(env=None):
    return os.path.join(data_dir(env), 'router', 'seen')


def read_plan(path):
    """The plan file's object, or None when it is missing, too big, not UTF-8 JSON, or not an object."""
    try:
        with open(path, 'rb') as f:
            raw = f.read(PLAN_MAX + 1)
    except OSError:
        return None
    if len(raw) > PLAN_MAX:
        return None
    try:
        plan = json.loads(raw.decode('utf-8'))
    except (ValueError, RecursionError):
        return None
    return plan if isinstance(plan, dict) else None


def _seen_path(folder, session_id):
    return os.path.join(folder, hashlib.sha256(session_id.encode('utf-8')).hexdigest()[:16] + '.json')


def _read_seen(path):
    try:
        with open(path, 'rb') as f:
            rec = json.loads(f.read(4096).decode('utf-8'))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def _write_seen(path, rec):
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    tmp = '%s.%d.tmp' % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(rec, f, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def prune(folder, now):
    """Seen files (and a stray temp file) not changed for KEEP seconds are removed."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if not (name.endswith('.json') or name.endswith('.tmp')):
            continue
        p = os.path.join(folder, name)
        try:
            if now - os.stat(p).st_mtime > KEEP:
                os.unlink(p)
        except OSError:
            pass


# ---------------------------------------------------------------- the program a hooks file runs

def _fields(raw):
    """The hook input's FIELDS that are short strings. Every other key (the prompt among them) is dropped while the
    JSON is parsed, so it is never held; None when the input is not a JSON object."""
    def keep(pairs):
        return dict((k, v) for k, v in pairs if k in FIELDS and isinstance(v, str) and len(v) <= 256)
    try:
        got = json.loads(raw.decode('utf-8'), object_pairs_hook=keep)
    except (ValueError, RecursionError):
        return None
    return got if isinstance(got, dict) else None


def emit(event, text, out):
    out.write(json.dumps({'hookSpecificOutput': {'hookEventName': event, 'additionalContext': text}},
                         ensure_ascii=False) + '\n')
    out.flush()


def main(argv=None, stdin=None, out=None, env=None, now=None):
    """Read the hook's input, print the line for argv[0]'s agent (or nothing), keep what the chat was told. Always
    returns 0, whatever goes wrong."""
    try:
        argv = sys.argv[1:] if argv is None else argv
        agent = argv[0] if argv else None
        if agent not in AGENTS:
            return 0
        stdin = stdin if stdin is not None else sys.stdin.buffer
        out = out if out is not None else sys.stdout
        now = time.time() if now is None else now
        event = _fields(stdin.read(MAX_INPUT))
        if event is None or event.get('hook_event_name') not in EVENTS:
            return 0
        name, session = event['hook_event_name'], event.get('session_id')
        plan = read_plan(plan_path(env))
        folder = seen_dir(env)
        if name == 'SessionStart':
            prune(folder, now)
        elif not session:
            return 0                    # no way to know what this chat was told: say nothing at a prompt
        spath = _seen_path(folder, session) if session else None
        seen = _read_seen(spath) if spath and name == 'UserPromptSubmit' else None
        text = line(agent, name, plan, seen, now)
        # Printed first, then kept as told: a line that could not be printed (a closed pipe, say) is not counted as
        # told, so the chat gets it at its next prompt.
        if text:
            emit(name, _cut(text), out)
        if spath and (text or (seen is not None and unusable(plan, now) == 'off')):
            try:
                _write_seen(spath, told(agent, plan, now))
            except OSError:
                pass
    except Exception:
        pass
    return 0


def _end_after(seconds):
    """End this process, with exit code 0 and nothing printed, after `seconds`."""
    try:
        import signal

        def stop(*_):
            os._exit(0)
        signal.signal(signal.SIGALRM, stop)
        signal.alarm(seconds)
    except Exception:
        pass


def run(agent):
    """The hooks file's entry point."""
    _end_after(LIMIT)
    main([agent])
    sys.exit(0)


def command(agent, python=None, folder=None):
    """The shell command a hooks file runs for `agent`: this Python by its real path, -I and -S, this file's folder at
    the end of the path (a moved copy does nothing), `|| true` so the exit code stays 0 when the Python is gone, and
    MARK at the end so Switchboard knows its own entry."""
    import shlex
    if agent not in AGENTS:
        raise ValueError(agent)
    python = python or sys.executable or '/usr/bin/python3'
    folder = folder or os.path.dirname(os.path.abspath(__file__))
    # The folder is written as itself, not as escapes: json.dumps would write a character outside the Basic
    # Multilingual Plane (an emoji in a folder name) as two escapes that Python reads as two broken halves.
    code = ('import sys; D = %s; sys.path[:] = [p for p in sys.path if p] + [D]; import os; '
            'os.path.isfile(os.path.join(D, "hookline.py")) and __import__("hookline").run(%s)'
            % (json.dumps(folder, ensure_ascii=False), json.dumps(agent)))
    return '%s -I -S -c %s || true %s' % (shlex.quote(python), shlex.quote(code), MARK)


if __name__ == '__main__':
    run(sys.argv[1] if len(sys.argv) > 1 else '')
