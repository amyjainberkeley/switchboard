"""The sheet's "Claude Code" switches beside the status line: house rules, the code map, and claude-mem kept to
your own chats. Each is the pack's own plan, then apply (on) or undo (off), only when the switch is clicked
(packs/houserules.py, packs/codemap.py, packs/memory.py).

The code map and claude-mem switches install what they need (packs/install.py): turning one on when its tool is not
on this Mac answers with the plan (what it adds, from where, how big), and only the same plan sent back as consent
starts the install, five steps with progress, in the background. Off takes out exactly what the install added. A
tool the person installed themselves is used as it is: the switch then only adds Switchboard's settings for it.
"""
import os
import time

from . import core
from .core import SwitchError
from .packs import codemap, common, houserules, memory
from .packs import install as packs_install

KW = {}                 # tests: claude_dir, home, environ, path (folders and PATH that are made up), battery, free
CAP_EVERY = 3600        # seconds between checks of claude-mem's weekly cap while the switch is on
CAP = {'at': 0, 'running': False}
LOCK = packs_install.LOCK        # a click, an install, the hourly cap check and the health check never edit at once
HEALTH = packs_install.HEALTH    # claude-mem's worker, looked at once a minute while its switch is on
HEALTH_EVERY = packs_install.HEALTH_EVERY


def _sync():
    """The packs' installer sees the same made-up folders as these switches (tests); nothing in normal use."""
    for k in ('claude_dir', 'home', 'environ', 'battery', 'free'):
        if k in KW:
            packs_install.KW[k] = KW[k]


def _cd():
    return KW.get('claude_dir')


def _steps(steps):
    return [{'step': s.get('step'), 'run': s.get('run'), 'or': s.get('or'), 'then': s.get('then')} for s in steps]


def house_view():
    try:
        s = houserules.status(_cd())
    except Exception as e:  # noqa: BLE001 - one unreadable file never breaks the sheet
        core.log('house rules: %r' % e)
        s = {'state': None}
    return {'on': s.get('state') == 'on', 'state': s.get('state'), 'lines': s.get('lines'),
            'tokens': s.get('tokens_estimate'), 'file': s.get('file'), 'text': houserules.text()}


def codemap_view():
    _sync()
    try:
        s = codemap.status(_cd(), home=KW.get('home'), path=KW.get('path'))
        iv = packs_install.view('codegraph', s['state'] == 'on')
    except Exception as e:  # noqa: BLE001
        core.log('code map: %r' % e)
        return {'on': False, 'state': None, 'found': [], 'add': [], 'setup': None}
    f = s['found']
    found = []
    if f['graphify']['present']:
        found.append('graphify' + ('' if f['graphify']['command'] else ' (skill)'))
    if f['codegraph']['present']:
        found.append('codegraph' + (' (installed by Switchboard)' if iv['by_switchboard'] else
                                    ' (its own section is in your instructions)' if f['codegraph']['own_block'] else ''))
    running = bool(iv['job'] and iv['job']['state'] == 'running')
    return {'on': s['state'] == 'on' or iv['by_switchboard'] or running, 'state': s['state'], 'found': found,
            'nothing_to_add': bool(f['codegraph']['own_block']) and not f['graphify']['present'],
            'add': [], 'setup': iv}


def _cm_installed(inst):
    """Is claude-mem itself installed? Its plugin is registered, or its settings file is there and Switchboard did
    not make it (turning the switch on before installing writes that file first)."""
    if inst['registered']:
        return True
    try:
        st, _ = common.load_state(None)
        made = bool((st.get('claude_mem') or {}).get('made_file'))
    except common.Refused:
        made = False
    return bool(inst['settings_file']) and not made


def claudemem_view():
    _sync()
    try:
        s = memory.cm_status(_cd(), environ=KW.get('environ'), home=KW.get('home'))
        iv = packs_install.view('claude-mem', bool(s['switch']))
    except Exception as e:  # noqa: BLE001
        core.log('claude-mem: %r' % e)
        return {'on': False, 'installed': None, 'install': [], 'setup': None}
    inst = s['installed']
    running = bool(iv['job'] and iv['job']['state'] == 'running')
    return {'on': bool(s['switch']) or iv['by_switchboard'] or running, 'installed': _cm_installed(inst),
            'turned_off_in_claude': inst['enabled'] is False, 'cap': s['cap_percent'] or memory.CAP_DEFAULT,
            'paused': bool(s['paused']), 'helpers_dropped': s['helpers_dropped'], 'watching': s['watching'],
            'install': [], 'remove': [], 'setup': iv}


def view(statusline):
    """Everything the "Claude Code" heading of the sheet draws. `statusline` is the server's own reading."""
    return {'statusline': statusline, 'house': house_view(), 'codemap': codemap_view(), 'claudemem': claudemem_view()}


def _apply(apply_fn, plan_):
    if plan_.get('blocked'):
        raise SwitchError(plan_['blocked'])
    try:
        return apply_fn(plan_)
    except common.Refused as e:
        raise SwitchError(str(e))


def _cap(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 100:
        raise SwitchError('The weekly cap is a share above 0 and at most 100 (percent).')
    return float(value)


def turn(pack, on=None, cap=None, consent=None, say=None):
    """One click on a switch (or a new cap): plan, then apply. Returns what it did, in the pack's own words. For a
    tool that is not installed: {'state': 'plan', 'lines', 'id'} (nothing changed), then, sent back with that id as
    consent, {'state': 'installing', 'job'}."""
    _sync()
    if packs_install.busy():
        raise SwitchError('An install is running. Try again when it ends.')
    with LOCK:
        return _turn(pack, on, cap, consent, say)


def _refusing(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except common.Refused as e:
        raise SwitchError(str(e))


def _install_first(pack, consent, say, cap=None):
    """None when the tool is already here (the switch goes on as before); else the plan, or the install started."""
    r = _refusing(packs_install.begin, pack, consent, cap, say)
    return None if r['state'] == 'present' else r


def _turn(pack, on, cap, consent=None, say=None):
    if pack == 'house':
        p = houserules.plan(bool(on), _cd())
        _apply(houserules.apply, p)
        return {'says': p.get('says'), 'notes': p.get('notes') or []}
    if pack == 'codemap':
        if on:
            r = _install_first('codegraph', consent, say)
            if r is not None:
                return r
        else:
            _refusing(packs_install.plan_remove, 'codegraph')
        p = codemap.plan(bool(on), _cd(), home=KW.get('home'), path=KW.get('path'))
        _apply(codemap.apply, p)
        lines = [] if on else _refusing(packs_install.remove, 'codegraph')
        return {'says': ' '.join(lines or [p.get('says') or '']) or None, 'notes': p.get('notes') or []}
    if pack == 'claudemem':
        if on is None:                                   # a new cap while it is on
            if not memory.cm_status(_cd(), environ=KW.get('environ'), home=KW.get('home'))['switch']:
                raise SwitchError('Turn it on first.')
            on = True
        elif on:
            r = _install_first('claude-mem', consent, say, _cap(cap) if cap is not None else None)
            if r is not None:
                return r
        lines = []
        if not on:
            _refusing(packs_install.plan_remove, 'claude-mem')
            lines = _refusing(packs_install.remove, 'claude-mem')
        p = memory.cm_plan(bool(on), _cap(cap) if cap is not None else (None if not on else _current_cap()),
                           _cd(), environ=KW.get('environ'), home=KW.get('home'))
        _apply(memory.cm_apply, p)
        CAP['at'] = 0                                    # check the cap on the next pass
        return {'says': ' '.join(lines or [e['says'] for e in p.get('edits') or []]) or None,
                'notes': [] if lines else p.get('notes') or [], 'install': [], 'remove': []}
    raise SwitchError('Unknown switch.')


def act(pack, body, say=None):
    """The pack's other buttons: repair (a stopped install runs again; a stopped claude-mem is repaired and started),
    map or refresh a project folder for the code map."""
    _sync()
    do = body.get('do')
    name = {'claudemem': 'claude-mem', 'codemap': 'codegraph'}.get(pack)
    if name is None:
        raise SwitchError('Unknown switch.')
    if do == 'repair':
        if packs_install.busy():
            raise SwitchError('An install is running. Try again when it ends.')
        with LOCK:
            return _refusing(packs_install.repair, name, say)
    if do in ('map', 'refresh') and pack == 'codemap':
        folder = body.get('folder')
        if not isinstance(folder, str):
            raise SwitchError('Give the project folder as a full path.')
        from .packs import codegraph_install, runner
        with LOCK:
            fn = codegraph_install.map_project if do == 'map' else codegraph_install.refresh_project
            r = _refusing(fn, folder, environ=KW.get('environ'), home=KW.get('home'),
                          log_path=runner.new_log('codegraph'))
        return {'says': 'Mapped %s.' % r['path'] if do == 'map' else 'Refreshed %s.' % r['path']}
    raise SwitchError('Unknown action.')


def health_tick(now=None, say=None):
    """claude-mem's worker, once a minute from the page's loop (packs/install.py)."""
    _sync()
    try:
        return packs_install.health_tick(now, say)
    except Exception as e:  # noqa: BLE001 - the next pass tries again
        core.log('claude-mem health: %r' % e)
        return None


def _current_cap():
    try:
        st, _ = common.load_state(None)
        return ((st.get('claude_mem') or {}).get('cap_percent')) or None
    except common.Refused:
        return None


def cap_tick(now=None, say=None):
    """While claude-mem is managed, hold it to its weekly cap: once an hour, from the server's loop."""
    now = time.time() if now is None else now
    if now - CAP['at'] < CAP_EVERY or not LOCK.acquire(False):      # a click is changing things: next pass
        CAP['running'] = False
        return None
    CAP['at'] = now
    try:
        return _cap_tick(now, say)
    finally:
        LOCK.release()
        CAP['running'] = False


def _cap_tick(now, say):
    try:
        if not memory.cm_status(_cd(), environ=KW.get('environ'), home=KW.get('home'))['switch']:
            return None
        r = memory.cm_cap_check(now, _cd(), environ=KW.get('environ'), home=KW.get('home'),
                                cache_path=os.path.join(common.packs_dir(), 'claude-mem-usage-cache.json'))
    except Exception as e:  # noqa: BLE001 - the next pass tries again
        core.log('claude-mem cap: %r' % e)
        return None
    if r.get('action') in ('pause', 'resume') and say:
        say(r['says'])
    return r


def share(until=None):
    """claude-mem's measured share of this week's Claude Code use, for the page's Work; None unless it is managed
    and installed."""
    try:
        s = memory.cm_status(_cd(), environ=KW.get('environ'), home=KW.get('home'))
        if not s['switch'] or not _cm_installed(s['installed']):
            return None
        until = time.time() if until is None else until
        m = memory.cm_measure(until - memory.WEEK, until, _cd(), environ=KW.get('environ'), home=KW.get('home'),
                              cache_path=os.path.join(common.packs_dir(), 'claude-mem-usage-cache.json'))
    except Exception as e:  # noqa: BLE001
        core.log('claude-mem share: %r' % e)
        return None
    return {'share': round(m['share'], 1), 'cap': s['cap_percent'], 'paused': bool(s['paused'])}


def undo_all(left=None):
    """Take out everything these switches put in (the uninstaller's tidy). Returns lines saying what changed. A step
    that can't be done never stops the ones after it: it gets a line of its own ('... left as it is (why)'), and its
    name goes into `left` when a list is given. A step that fails on a file Switchboard's record says it never changed
    gets neither (_recorded)."""
    said = []
    with LOCK:
        _undo_all(said, [] if left is None else left)
    return said


def plain_error(e):
    """An error as one plain phrase: a refusal's own words, or the file and what the system said about it."""
    if isinstance(e, OSError) and e.strerror:
        return '%s: %s' % (e.filename, e.strerror) if e.filename else e.strerror
    return str(e) or e.__class__.__name__


def _undo_all(said, left):
    _sync()
    _undo_block('House rules', houserules.ID, lambda cd: houserules.undo(cd), said, left)
    _undo_block('Code map', codemap.ID, lambda cd: codemap.undo(cd, home=KW.get('home'), path=KW.get('path')), said,
                left)
    for name, fn in (('codegraph', lambda: packs_install.remove('codegraph')),
                     ('claude-mem', lambda: packs_install.remove('claude-mem')),
                     ('claude-mem settings', lambda: memory.cm_undo(_cd(), environ=KW.get('environ'),
                                                                    home=KW.get('home')))):
        if name == 'claude-mem settings' and 'claude-mem' in left:     # its gate and cap stay while it is installed
            said.append('%s: left as it is (claude-mem itself is still installed)' % name)
            left.append(name)
            continue
        try:
            r = fn()
            if isinstance(r, list):
                said.extend(r)
            elif r.get('changed'):
                said.append('%s: put back as it was.' % name)
        except Exception as e:  # noqa: BLE001 - an unreadable file or a refusal: named, and the next step runs
            if not _recorded(name):     # a file Switchboard never changed holds nothing of its own: nothing to say
                continue
            said.append('%s: left as it is (%s)' % (name, plain_error(e)))
            left.append(name)


def _undo_block(name, block_id, undo, said, left):
    """House rules or the code map, taken out of this Claude Code folder's CLAUDE.md, then out of every other CLAUDE.md
    Switchboard's record says holds the block (put in from a shell with another CLAUDE_CONFIG_DIR), each through the
    pack's own undo for its folder: the record's sha, the link and folder checks and a person's own edits work as they
    do for Off. Each file that can't be done is named; the others still run."""
    folders = [_cd()] + _recorded_folders(block_id, common.claude_dir(_cd()))     # this one first
    for i, folder in enumerate(folders):
        try:
            r = undo(folder)
            if r.get('changed'):
                said.append('%s: put back as it was%s.' % (
                    name, '' if i == 0 else ' (%s)' % _short(common.instructions_path(folder))))
        except Exception as e:  # noqa: BLE001 - an unreadable file or a refusal: named, and the next one runs
            if i == 0 and not _recorded(name):      # a file Switchboard never changed holds nothing of its own
                continue
            said.append('%s: left as it is (%s)' % (name, plain_error(e)))
            if name not in left:
                left.append(name)


def _recorded_folders(block_id, here):
    """The Claude Code folders, other than `here`, whose CLAUDE.md Switchboard's record (packs/state.json) says holds
    this block: the folder of each file the record names. None at all when the record can't be read: this folder's
    own undo then refuses, and says so."""
    try:
        st, _ = common.load_state(None)
    except Exception:  # noqa: BLE001 - Refused, or the file can't be opened
        return []
    out = []
    for key in sorted(st['blocks']):
        rec = st['blocks'][key]
        f = rec.get('file') if isinstance(rec, dict) else None
        if not isinstance(f, str) or not os.path.isabs(f) or os.path.basename(f) != common.INSTRUCTIONS_NAME or \
                key != common.block_key(block_id, f):
            continue
        folder = os.path.dirname(f)
        if folder != here and folder not in out:
            out.append(folder)
    return out


def _short(path):
    home = os.path.realpath(core.home())
    return '~' + path[len(home):] if path.startswith(home + os.sep) else path


def _recorded(name):
    """Does Switchboard's record say this step changed the file it works on (its block in this Claude Code folder's
    CLAUDE.md; claude-mem's settings)? A record that can't be read counts as yes, the cautious answer. The two installs'
    undo runs only from their own record, so theirs always counts."""
    try:
        st, _ = common.load_state(None)
    except Exception:  # noqa: BLE001 - Refused, or the file can't be opened
        return True
    if name in ('House rules', 'Code map'):
        path = common.instructions_path(common.claude_dir(_cd()))
        return common.block_key(houserules.ID if name == 'House rules' else codemap.ID, path) in st['blocks']
    if name == 'claude-mem settings':
        return bool(st.get('claude_mem'))
    return True
