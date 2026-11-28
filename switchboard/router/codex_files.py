"""What Switchboard writes into Codex's own folder so the person's routes reach work in Codex.

Codex has no hook that can set a model ("Hook output is diagnostic only" for its model-request hooks). Two of its own
documented levers carry the routes instead:

- Helper files. "standalone TOML files under `~/.codex/agents/`", each with `name`, `description` and
  `developer_instructions`, and "Per agent: `model` and `model_reasoning_effort` in the custom agent file."
  plan_files() gives one agents/switchboard-<kind>.toml for each kind whose route's first usable choice in Codex
  names a model or an effort; apply() writes them, undo() takes them back, status() says which are there.
- Its hooks file, <Codex folder>/hooks.json. hooks_apply() merges two entries into it (SessionStart and
  UserPromptSubmit, each running the hook line for Codex, hookline.command('codex'), timeout 5), keeping everything
  already there byte for byte; hooks_undo() takes out only entries that carry hookline.MARK. Codex runs a new or
  changed hook only after the person looks at it and trusts it (/hooks in its terminal app); until then it skips it.
  config.toml is never opened.

The way models.py edits Claude Code's folder: each file is written whole or not at all (a temp file in the same
folder, flushed, then renamed over); the hash of every helper file Switchboard wrote, and what it made of the hooks
file, is kept in Switchboard's own record (<data folder>/router/codex-files.json); a file Switchboard did not write,
or one changed since it wrote it, is never written over and never removed, and is named in one line instead.

A plan in its trial mode (`dry`) writes nothing at all: apply says what it would do.

Nothing here installs anything by itself: these are functions the person's one switch calls.
"""
import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time

from . import adapter_words as W
from . import hookline

STATE_V = 1
STATE_NAME = 'codex-files.json'
LOCK_NAME = '.codex-files.lock'
AGENTS_DIR = 'agents'
HOOKS_NAME = 'hooks.json'
EVENTS = ('SessionStart', 'UserPromptSubmit')
TIMEOUT = 5                         # seconds Codex gives each of the two hooks
FILE_MODE = 0o644                   # Codex reads these; they hold no secret
HOOKS_MODE = 0o600                  # a hooks file Switchboard makes
JSON_MAX = 1 << 20
KIND_RE = re.compile(r'^[a-z][a-z0-9-]{0,31}\Z')
# The words Codex's own model option and effort key take (the same rules as providers/catalog.py).
MODEL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}\Z')
EFFORT_RE = re.compile(r'^[a-z][a-z0-9]{0,15}\Z')


# ---------------------------------------------------------------- places

def default_codex_home():
    """$CODEX_HOME when set, else ~/.codex (Codex's own rule; providers/codex_facts.py)."""
    return os.environ.get('CODEX_HOME') or os.path.join(os.path.expanduser('~'), '.codex')


def default_state_path():
    from .. import core
    return os.path.join(core.data_dir(), 'router', STATE_NAME)


def _where(codex_home, state_path):
    """(the Codex folder as given, absolute; the record's path)."""
    home = os.path.abspath(os.path.expanduser(codex_home or default_codex_home()))
    return home, state_path or default_state_path()


# ---------------------------------------------------------------- small file helpers

def _read(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except FileNotFoundError:
        return None


def _sha(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def _atomic_write(path, data, mode):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.%s.' % os.path.basename(path), suffix='.switchboard-tmp')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _Bad(Exception):
    """Switchboard's record can't be read."""


def _load(state_path):
    raw = _read(state_path) if state_path else None
    if raw is None:
        return {'v': STATE_V, 'homes': {}}
    try:
        st = json.loads(raw.decode('utf-8'))
        if not isinstance(st, dict) or st.get('v') != STATE_V or not isinstance(st.get('homes'), dict):
            raise ValueError('unexpected shape')
    except ValueError:
        raise _Bad()
    return st


def _save(state_path, st):
    folder = os.path.dirname(os.path.abspath(state_path))
    os.makedirs(folder, mode=0o700, exist_ok=True)
    _atomic_write(state_path, (json.dumps(st, indent=1, sort_keys=True) + '\n').encode('utf-8'), 0o600)


class _Locked(object):
    """One change to the record at a time (a lock file beside it)."""

    def __init__(self, state_path):
        self.path = os.path.join(os.path.dirname(os.path.abspath(state_path)), LOCK_NAME)
        self.fd = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def _home_state(st, home):
    hs = st['homes'].setdefault(os.path.realpath(home), {})
    if not isinstance(hs.get('agents'), dict):
        hs['agents'] = {}
    return hs


def _tidy(st, home):
    key = os.path.realpath(home)
    hs = st['homes'].get(key)
    if isinstance(hs, dict) and not hs.get('agents') and not hs.get('made_dir') and not hs.get('hooks'):
        del st['homes'][key]


# ---------------------------------------------------------------- the helper files

def toml_string(s):
    """s as a TOML basic string: quotes, backslashes and control characters escaped, other text kept as it is (a
    lone surrogate, which is not text, becomes U+FFFD)."""
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == '\\':
            out.append('\\\\')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\r':
            out.append('\\r')
        elif ch == '\t':
            out.append('\\t')
        elif ch == '\b':
            out.append('\\b')
        elif ch == '\f':
            out.append('\\f')
        elif o < 0x20 or o == 0x7f:
            out.append('\\u%04X' % o)
        elif 0xd800 <= o <= 0xdfff:
            out.append('�')
        else:
            out.append(ch)
    out.append('"')
    return ''.join(out)


def helper_name(kind):
    return hookline.CODEX_HELPER.format(kind=kind)


def helper_text(kind, model, effort):
    """The text of agents/switchboard-<kind>.toml (model and effort lines only when given)."""
    about = W.KIND_ABOUT.get(kind) or kind
    lines = [W.FILE_TOP.rstrip('\n'),
             'name = %s' % toml_string(helper_name(kind)),
             'description = %s' % toml_string(W.HELPER_DESCRIPTION.format(about=about)),
             'developer_instructions = %s' % toml_string(W.HELPER_INSTRUCTIONS.format(about=about))]
    if model:
        lines.append('model = %s' % toml_string(model))
    if effort:
        lines.append('model_reasoning_effort = %s' % toml_string(effort))
    return '\n'.join(lines) + '\n'


def plan_files(plan, codex_home, now=None):
    """[(path, text)]: one helper file for each kind whose route's first usable choice for Codex (the router rule,
    started in Codex) names a model or an effort. None for a plan that is missing, old or off."""
    now = time.time() if now is None else now
    if hookline.unusable(plan, now):
        return []
    out = []
    folder = os.path.join(os.path.abspath(os.path.expanduser(codex_home)), AGENTS_DIR)
    for kind in hookline._kinds(plan):
        if not KIND_RE.match(kind):
            continue
        r = hookline._resolve(plan, kind, 'codex', now)
        if r['do'] != 'model':
            continue
        model = r['model'] if isinstance(r['model'], str) and MODEL_RE.match(r['model']) else None
        effort = r['effort'] if isinstance(r['effort'], str) and EFFORT_RE.match(r['effort']) else None
        if model or effort:
            out.append((os.path.join(folder, helper_name(kind) + '.toml'), helper_text(kind, model, effort)))
    return out


def _result(**kw):
    base = {'dry': False, 'written': [], 'removed': [], 'left': [], 'would': [], 'message': None}
    base.update(kw)
    return base


def _rmdir_if_made(hs, folder):
    if hs.get('made_dir'):
        try:
            os.rmdir(folder)
            hs['made_dir'] = False
        except FileNotFoundError:
            hs['made_dir'] = False
        except OSError:
            pass                    # something else lives there: it stays


def apply(plan, codex_home=None, state_path=None, now=None):
    """Make the Codex folder's Switchboard helper files match the plan. A plan turned off takes every file out; a
    missing, unreadable or old plan changes nothing. Returns {'dry', 'written', 'removed', 'left' (one sentence per
    file left alone), 'would' (when dry), 'message'}."""
    home, sp = _where(codex_home, state_path)
    folder = os.path.join(home, AGENTS_DIR)
    # Only a plan the person turned off takes the files out. A missing, unreadable or old plan (the Mac slept past
    # its time, say) is not a change of routes, so the files an open Codex chat was told to use stay as they are.
    if hookline.unusable(plan, time.time() if now is None else now) in ('no_plan', 'old_plan'):
        return _result()
    want = dict((os.path.basename(p), t.encode('utf-8')) for p, t in plan_files(plan, home, now))
    trial = isinstance(plan, dict) and plan.get('dry') is True
    if trial:
        st = {'v': STATE_V, 'homes': {}}
        try:
            st = _load(sp)
        except _Bad:
            pass
        own = _home_state(copy.deepcopy(st), home)['agents']
        would = [W.WOULD_WRITE.format(path=os.path.join(folder, n)) for n in sorted(want)
                 if _read(os.path.join(folder, n)) != want[n]]
        would += [W.WOULD_REMOVE.format(path=os.path.join(folder, n)) for n in sorted(own) if n not in want]
        return _result(dry=True, would=would, message=W.DRY_PLAN)
    if want and not os.path.isdir(home):
        return _result(message=W.NO_CODEX_HOME.format(path=home))
    with _Locked(sp):
        try:
            st = _load(sp)
        except _Bad:
            return _result(message=W.BAD_RECORD.format(path=sp))
        hs = _home_state(st, home)
        own = hs['agents']
        res = _result()
        try:
            for name in sorted(want):
                path = os.path.join(folder, name)
                cur = _read(path)
                if cur is not None and own.get(name) != _sha(cur):
                    res['left'].append((W.CHANGED_SINCE if name in own else W.NOT_OURS).format(path=path))
                    own.pop(name, None)
                    continue
                if cur == want[name]:
                    continue
                if not os.path.isdir(folder):
                    os.makedirs(folder, mode=0o755)
                    hs['made_dir'] = True
                _atomic_write(path, want[name], FILE_MODE)
                own[name] = _sha(want[name])
                res['written'].append(path)
            for name in sorted(n for n in list(own) if n not in want):
                path = os.path.join(folder, name)
                cur = _read(path)
                if cur is not None and _sha(cur) != own[name]:
                    res['left'].append(W.CHANGED_SINCE.format(path=path))
                elif cur is not None:
                    os.unlink(path)
                    res['removed'].append(path)
                own.pop(name)
            if not own:
                _rmdir_if_made(hs, folder)
        finally:
            _tidy(st, home)
            _save(sp, st)
    return res


def undo(codex_home=None, state_path=None):
    """Take out every helper file Switchboard wrote that is still as it wrote it, and the agents folder when
    Switchboard made it and it is empty. Returns apply()'s shape."""
    home, sp = _where(codex_home, state_path)
    folder = os.path.join(home, AGENTS_DIR)
    with _Locked(sp):
        try:
            st = _load(sp)
        except _Bad:
            return _result(message=W.BAD_RECORD.format(path=sp))
        hs = _home_state(st, home)
        own = hs['agents']
        res = _result()
        try:
            for name in sorted(own):
                path = os.path.join(folder, name)
                cur = _read(path)
                if cur is not None and _sha(cur) != own[name]:
                    res['left'].append(W.CHANGED_SINCE.format(path=path))
                elif cur is not None:
                    os.unlink(path)
                    res['removed'].append(path)
            hs['agents'] = {}
            _rmdir_if_made(hs, folder)
        finally:
            _tidy(st, home)
            _save(sp, st)
    return res


def status(codex_home=None, state_path=None):
    """{'home', 'files': [{'path', 'state': 'ours' | 'changed' | 'missing'}], 'message'}. Reads only."""
    home, sp = _where(codex_home, state_path)
    folder = os.path.join(home, AGENTS_DIR)
    try:
        own = _home_state(_load(sp), home)['agents']
    except _Bad:
        return {'home': home, 'files': [], 'message': W.BAD_RECORD.format(path=sp)}
    files = []
    for name in sorted(own):
        cur = _read(os.path.join(folder, name))
        files.append({'path': os.path.join(folder, name),
                      'state': 'missing' if cur is None else ('ours' if _sha(cur) == own[name] else 'changed')})
    return {'home': home, 'files': files, 'message': None}


# ---------------------------------------------------------------- the hooks file
#
# Edited in place with notify_hooks' splicing helpers (the ones Switchboard's Claude Code hooks use): what is added
# copies the spacing around it, so the person's text stays as it was, and taking it out gives that text back. After
# every edit the result is parsed and compared with the object it should be, so a wrong splice is never written.

def _nh():
    from .. import notify_hooks
    return notify_hooks


def is_ours(handler):
    return isinstance(handler, dict) and isinstance(handler.get('command'), str) \
        and handler['command'].rstrip().endswith(hookline.MARK)


def wanted(command_line):
    """{event: the group Switchboard adds for it}."""
    return dict((e, {'hooks': [{'type': 'command', 'command': command_line, 'timeout': TIMEOUT}]}) for e in EVENTS)


class _NotJson(Exception):
    pass


class _Shape(Exception):
    pass


def _parse(path):
    """(text, obj, raw) of a hooks file; ('', {}, None) for no file. Raises _NotJson or _Shape."""
    raw = _read(path)
    if raw is None:
        return '', {}, None
    if len(raw) > JSON_MAX or raw.startswith(b'\xef\xbb\xbf'):
        raise _NotJson()
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise _NotJson()
    if not text.strip():
        return text, {}, raw
    nh = _nh()
    dec = nh._decoder()
    try:
        obj = dec.decode(text)
    except (ValueError, RecursionError):
        raise _NotJson()
    if not isinstance(obj, dict):
        raise _Shape()
    try:
        top, _ = nh._object_at(text, nh._skip(text, 0), dec)
    except (ValueError, RecursionError):
        raise _NotJson()
    hm = [m for m in top if m['key'] == 'hooks']
    if len(hm) > 1:
        raise _Shape()
    if hm:
        if not isinstance(obj['hooks'], dict):
            raise _Shape()
        inner, _ = nh._object_at(text, hm[0]['v0'], dec)
        for e in set(m['key'] for m in inner):
            if len([m for m in inner if m['key'] == e]) > 1:
                raise _Shape()
        for e, groups in obj['hooks'].items():
            if not isinstance(groups, list):
                raise _Shape()
    return text, obj, raw


def _found(obj):
    """{event: [the commands of Switchboard's handlers in it]}, over every event."""
    out = {}
    hooks = obj.get('hooks') if isinstance(obj, dict) else None
    for e, groups in (hooks.items() if isinstance(hooks, dict) else ()):
        for g in groups if isinstance(groups, list) else []:
            for h in g.get('hooks') if isinstance(g, dict) and isinstance(g.get('hooks'), list) else []:
                if is_ours(h):
                    out.setdefault(e, []).append(h['command'])
    return out


def _with_ours(obj, want, cmd):
    o = copy.deepcopy(obj)
    hooks = o.setdefault('hooks', {})
    for e in EVENTS:
        groups = hooks.get(e)
        if groups is None:
            hooks[e] = [want[e]]
            continue
        mine = [h for g in groups if isinstance(g, dict) and isinstance(g.get('hooks'), list)
                for h in g['hooks'] if is_ours(h)]
        for h in mine:
            h['command'] = cmd
        if not mine:
            groups.append(want[e])
    return o


def _without_ours(obj, made):
    o = copy.deepcopy(obj)
    hooks = o.get('hooks')
    if not isinstance(hooks, dict):
        return o
    for e, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get('hooks'), list):
                mine = [h for h in g['hooks'] if is_ours(h)]
                if mine and len(mine) == len(g['hooks']):
                    continue
                if mine:
                    g = dict(g, hooks=[h for h in g['hooks'] if not is_ours(h)])
            kept.append(g)
        hooks[e] = kept
    for e in EVENTS:
        if e in made.get('events', []) and hooks.get(e) == []:
            del hooks[e]
    if made.get('hooks') and hooks == {}:
        del o['hooks']
    return o


def _put_in(text, want, cmd, made):
    """Text with Switchboard's two groups added (or its commands brought up to date) in place."""
    nh = _nh()
    dec, nl = nh._decoder(), nh._nl(text)
    w = nh._Where(text, dec)
    unit = nh._unit(text, w.o, w.top)
    if w.hooks is None:
        if not w.top:
            made['inner'] = text[w.o + 1:w.end - 1]
        made['hooks'], made['events'] = True, list(EVENTS)
        return nh._add_member(text, w.o, w.end, w.top, 'hooks', dict((e, [want[e]]) for e in EVENTS), unit, nl, True)
    for e in EVENTS:
        w = nh._Where(text, dec)
        m = w.event(e)
        if m is None:
            if not w.inner and 'hooks_inner' not in made:
                made['hooks_inner'] = text[w.hooks['v0'] + 1:w.hend - 1]
            text = nh._add_member(text, w.hooks['v0'], w.hend, w.inner, e, [want[e]], unit, nl, w.spread(w.hooks))
            made['events'].append(e)
            continue
        groups, end = nh._array_at(text, m['v0'], dec)
        ours, spans = False, []
        for g0, _ in groups:
            if text[g0] != '{':
                continue
            hm = next((x for x in nh._object_at(text, g0, dec)[0] if x['key'] == 'hooks'), None)
            if hm is None or text[hm['v0']] != '[':
                continue
            for h0, _ in nh._array_at(text, hm['v0'], dec)[0]:
                value = dec.raw_decode(text, h0)[0]
                if is_ours(value):
                    ours = True
                    if value['command'] != cmd:
                        c = next(x for x in reversed(nh._object_at(text, h0, dec)[0]) if x['key'] == 'command')
                        spans.append((c['v0'], c['v1'], json.dumps(cmd, ensure_ascii=False)))
        if not ours:
            text = nh._add_element(text, m['v0'], end, groups, want[e], unit, nl, w.spread(m))
            continue
        for a, b, new in sorted(spans, reverse=True):
            text = text[:a] + new + text[b:]
    return text


def _take_out(text, made):
    """Text with every handler of Switchboard's taken out (a group left with none of the person's goes with it), then
    the events and "hooks" Switchboard made, once they are empty."""
    nh = _nh()
    dec = nh._decoder()
    while True:
        w = nh._Where(text, dec)
        cut = None
        for m in w.inner:
            if text[m['v0']] != '[':
                continue
            groups, end = nh._array_at(text, m['v0'], dec)
            for gi in range(len(groups) - 1, -1, -1):
                g0 = groups[gi][0]
                if text[g0] != '{':
                    continue
                hm = next((x for x in nh._object_at(text, g0, dec)[0] if x['key'] == 'hooks'), None)
                if hm is None or text[hm['v0']] != '[':
                    continue
                handlers, hend = nh._array_at(text, hm['v0'], dec)
                mine = [i for i, (h0, _) in enumerate(handlers) if is_ours(dec.raw_decode(text, h0)[0])]
                if mine and len(mine) == len(handlers):
                    cut = nh._drop_element(text, m['v0'], end, groups, gi)
                elif mine:
                    cut = nh._drop_element(text, hm['v0'], hend, handlers, mine[-1])
                if cut is not None:
                    break
            if cut is not None:
                break
        if cut is None:
            break
        text = cut
    for e in reversed(EVENTS):
        w = nh._Where(text, dec)
        m = w.event(e)
        if m is not None and e in made.get('events', []) and text[m['v0']] == '[' \
                and not nh._array_at(text, m['v0'], dec)[0]:
            text = nh._drop_member(text, w.hooks['v0'], w.hend, w.inner, w.inner.index(m), made.get('hooks_inner'))
    w = nh._Where(text, dec)
    if w.hooks is not None and made.get('hooks') and not w.inner:
        text = nh._drop_member(text, w.o, w.end, w.top, w.top.index(w.hooks), made.get('inner'))
    return text


def _checked(text, expect):
    """text when it parses to expect, else None."""
    try:
        return text if _nh()._decoder().decode(text) == expect else None
    except (ValueError, RecursionError):
        return None


def _hooks_result(**kw):
    base = {'dry': False, 'changed': False, 'path': None, 'message': None, 'trust': None, 'would': []}
    base.update(kw)
    return base


def hooks_plan(codex_home=None, state_path=None, command_line=None):
    """The exact change that puts Switchboard's two hooks into <Codex folder>/hooks.json. Changes nothing.
    {'path', 'text' (the new text, or None for no change), 'raw' (the file's bytes now), 'made', 'message'}."""
    home, sp = _where(codex_home, state_path)
    path = os.path.join(home, HOOKS_NAME)
    cmd = command_line or hookline.command('codex')
    plan = {'path': path, 'text': None, 'raw': None, 'made': None, 'message': None}
    try:
        text, obj, raw = _parse(os.path.realpath(path))
    except _NotJson:
        return dict(plan, message=W.HOOKS_NOT_JSON.format(path=path))
    except _Shape:
        return dict(plan, message=W.HOOKS_SHAPE.format(path=path))
    plan['raw'] = raw
    found = _found(obj)
    if all(found.get(e) for e in EVENTS) and all(c == cmd for e in EVENTS for c in found[e]):
        return dict(plan, message=W.HOOKS_ALREADY.format(path=path))
    try:
        st = _load(sp)
    except _Bad:
        return dict(plan, message=W.BAD_RECORD.format(path=sp))
    made = copy.deepcopy(_home_state(st, home).get('hooks') or {})
    made.setdefault('hooks', False)
    made.setdefault('events', [])
    expect = _with_ours(obj, wanted(cmd), cmd)
    if not text.strip():
        made.setdefault('file_was', text if raw is not None else None)    # None: there was no file at all
        made.update(hooks=True, events=list(EVENTS))
        new = '{\n  "hooks": %s\n}\n' % _nh()._render(expect['hooks'], '  ', '  ', '\n')
    else:
        try:
            new = _checked(_put_in(text, wanted(cmd), cmd, made), expect)
        except (ValueError, RecursionError, KeyError, StopIteration):
            new = None
        if new is None:
            return dict(plan, message=W.HOOKS_SHAPE.format(path=path))
    return dict(plan, text=new, made=made, message=W.HOOKS_ON.format(path=path))


def hooks_apply(codex_home=None, state_path=None, command_line=None):
    """Put Switchboard's two hooks into the hooks file, whole or not at all. Returns {'dry', 'changed', 'path',
    'message', 'trust' (the sentence on Codex's trust step, when a hook went in or changed), 'would'}."""
    home, sp = _where(codex_home, state_path)
    path = os.path.join(home, HOOKS_NAME)
    with _Locked(sp):
        p = hooks_plan(home, sp, command_line)
        if p['text'] is None:
            return _hooks_result(path=path, message=p['message'])
        target = os.path.realpath(path)
        # The last look before the swap. Switchboard's own runs take turns by the lock; Codex itself takes no lock,
        # so a write it makes in the few microseconds between this look and the swap would be lost.
        if _read(target) != p['raw']:
            return _hooks_result(path=path, message=W.HOOKS_MOVED.format(path=path))
        if not os.path.isdir(os.path.dirname(target)):
            return _hooks_result(path=path, message=W.NO_CODEX_HOME.format(path=home))
        mode = HOOKS_MODE
        try:
            mode = os.stat(target).st_mode & 0o7777
        except FileNotFoundError:
            pass
        st = _load(sp)
        _home_state(st, home)['hooks'] = p['made']
        _atomic_write(target, p['text'].encode('utf-8'), mode)
        _save(sp, st)
    return _hooks_result(changed=True, path=path, message=p['message'], trust=W.TRUST)


def hooks_undo(codex_home=None, state_path=None):
    """Take out every hooks-file entry that carries Switchboard's mark, and only those; a file Switchboard made (or
    found blank) goes back to how it was once nothing else is in it. Returns hooks_apply()'s shape."""
    home, sp = _where(codex_home, state_path)
    path = os.path.join(home, HOOKS_NAME)
    target = os.path.realpath(path)
    try:
        text, obj, raw = _parse(target)
    except _NotJson:
        return _hooks_result(path=path, message=W.HOOKS_NOT_JSON.format(path=path))
    except _Shape:
        return _hooks_result(path=path, message=W.HOOKS_SHAPE.format(path=path))
    with _Locked(sp):
        try:
            st = _load(sp)
        except _Bad:
            return _hooks_result(path=path, message=W.BAD_RECORD.format(path=sp))
        hs = _home_state(st, home)
        made = hs.get('hooks') or {}
        if not _found(obj):
            hs.pop('hooks', None)
            _tidy(st, home)
            _save(sp, st)
            return _hooks_result(path=path, message=W.HOOKS_NONE.format(path=path))
        expect = _without_ours(obj, made)
        try:
            new = _checked(_take_out(text, made), expect)
        except (ValueError, RecursionError, KeyError, StopIteration):
            new = None
        if new is None:
            return _hooks_result(path=path, message=W.HOOKS_SHAPE.format(path=path))
        if _read(target) != raw:
            return _hooks_result(path=path, message=W.HOOKS_MOVED.format(path=path))
        if 'file_was' in made and expect == {}:
            if made['file_was'] is None:
                os.unlink(target)
            else:
                _atomic_write(target, made['file_was'].encode('utf-8'), os.stat(target).st_mode & 0o7777)
        else:
            _atomic_write(target, new.encode('utf-8'), os.stat(target).st_mode & 0o7777)
        hs.pop('hooks', None)
        _tidy(st, home)
        _save(sp, st)
    return _hooks_result(changed=True, path=path, message=W.HOOKS_OFF.format(path=path))
