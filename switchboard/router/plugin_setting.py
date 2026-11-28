"""The one key that makes Claude Code load the plugin that ships inside Switchboard, written only when the person
turns routing on.

The plugin (switchboard/mods/switchboard-router) applies the router's plan inside Claude Code. Claude Code loads a
plugin folder named in `CLAUDE_CODE_PLUGIN_DIRS`, read from the environment or from the `env` block of its user
settings. What the pages say (saved copies read 2026-11-27):

  - code.claude.com/docs/en/plugins/mods/reference, "Settings and environment variables": `CLAUDE_CODE_PLUGIN_DIRS`,
    where "Environment, or `env` in `~/.claude/settings.json`": "Plugin directories to load as `--plugin-dir` does,
    for apps you can't pass a flag to. Absolute paths separated by `:`".
  - code.claude.com/docs/en/plugins/mods/overview: "Mods are on by default. In the terminal, use Claude Code
    v2.1.287 or later."

status() reads. plan() says exactly what apply() would change and writes nothing. apply() puts the plugin's folder
in the key: a value the person set is kept and the folder is added after it with ":"; a value that already names
the folder changes nothing. undo() takes exactly that folder out again and leaves the rest; it takes the key out
(and an env block Switchboard itself made) only when nothing else is left in it. Each change is spliced into the
file so every other byte stays as it was, whole or not at all, with a copy of the file from before and a record in
Switchboard's own folder (<data folder>/router/plugin-setting.json). When nothing but Switchboard's own change
happened since, undo puts the old bytes back exactly.

Nothing is written when Claude Code is older than 2.1.287 or its version cannot be found, when the settings file is
not valid JSON (or is a link, a folder, read-only, or holds a key twice), or when the plugin folder is not there.

Nothing here runs by itself: wire.turn_on() and wire.turn_off() call it from the person's one switch.
"""
import hashlib
import json
import os
import time

from .. import core
from .. import models as _m
from . import wire_words as W

KEY = W.KEY
NEED = (2, 1, 287)                      # the first Claude Code that runs plugins of function hooks (the page above)
SEP = ':'
SETTINGS_NAME = 'settings.json'
MANIFEST = os.path.join('.claude-plugin', 'plugin.json')
RECORD = os.path.join('router', 'plugin-setting.json')
BACKUPS = os.path.join('router', 'plugin-backups')
RECORD_VERSION = 1
_MISSING = object()


def plugin_dir() -> str:
    """The plugin folder that ships inside this copy of Switchboard, as an absolute path."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'mods', 'switchboard-router')


def _sha(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def _need():
    return _m._vs(NEED)


def _parts(value):
    return value.split(SEP)


# ---------------------------------------------------------------- where things are

def _places(claude_dir, data_dir):
    """(Claude Code's folder by real path, Switchboard's data folder)."""
    return _m._cd(claude_dir), (core.data_dir() if data_dir is None else data_dir)


# ---------------------------------------------------------------- Switchboard's record of what it wrote

def _record_path(data):
    return os.path.join(data, RECORD)


def _load_record(data):
    """(record, problem line or None). The record is {'version', 'dirs': {claude folder: what Switchboard did}}."""
    blank = {'version': RECORD_VERSION, 'dirs': {}}
    if data is None:
        return blank, None
    raw = _m._read(_record_path(data))
    if raw is None:
        return blank, None
    try:
        rec = json.loads(raw.decode('utf-8'))
        if not isinstance(rec, dict) or rec.get('version') != RECORD_VERSION or not isinstance(rec.get('dirs'), dict):
            raise ValueError('unexpected shape')
        for mine in rec['dirs'].values():
            if not isinstance(mine, dict) or not isinstance(mine.get('path'), str):
                raise ValueError('unexpected entry')
    except (ValueError, UnicodeDecodeError):
        return None, W.RECORD_UNREADABLE
    return rec, None


def _save_record(data, rec):
    path = _record_path(data)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    _m._atomic_write(path, (json.dumps(rec, indent=1, sort_keys=True) + '\n').encode('utf-8'), 0o600)


def _backup(data, cd, raw):
    """A copy of the settings file's bytes in Switchboard's folder, readable only by the person (it can hold keys)."""
    d = os.path.join(data, BACKUPS)
    os.makedirs(d, mode=0o700, exist_ok=True)
    name = 'settings.%s.%s.%s.json' % (hashlib.sha256(cd.encode('utf-8')).hexdigest()[:8],
                                       time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()), _sha(raw)[:12])
    path = os.path.join(d, name)
    _m._atomic_write(path, raw, 0o600)
    return path


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _backup_bytes(mine):
    """The bytes from before Switchboard's write, when its copy is still there and unchanged, else None."""
    p = mine.get('first_backup')
    raw = _m._read(p) if p else None
    return raw if raw is not None and _sha(raw) == mine.get('sha_before') else None


# ---------------------------------------------------------------- what is there now

def _look(cd, data, versions, environ, pdir):
    """Everything plan(), undo() and status() need, read once: the settings file and the key's value in its env
    block, Switchboard's record, the version, the plugin folder, and whether the environment sets the key too."""
    path = os.path.join(cd, SETTINGS_NAME)
    lk = {'cd': cd, 'path': path, 'raw': None, 'exists': False, 'doc': None, 'value': _MISSING, 'problem': None,
          'no_folder': False, 'mine': None, 'record': None, 'record_problem': None, 'version': None,
          'new_enough': None, 'other_old': [], 'notes': [], 'plugin': pdir, 'plugin_problem': None}

    if not os.path.isdir(cd):
        lk['no_folder'] = True
    elif os.path.islink(path):
        lk['problem'] = W.CANT_EDIT.format(why=W.WHY['link'])
    elif os.path.isdir(path):
        lk['problem'] = W.CANT_EDIT.format(why=W.WHY['folder'])
    else:
        raw = _m._read(path)
        lk['raw'], lk['exists'] = raw, raw is not None
        if raw is not None:
            try:
                raw.decode('utf-8')
                text_ok = True
            except UnicodeDecodeError:
                text_ok = False
            if raw.startswith(b'\xef\xbb\xbf') or not text_ok:
                lk['problem'] = W.CANT_EDIT.format(why=W.WHY['text'])
        if lk['problem'] is None:
            try:
                lk['doc'] = _m._Doc(raw, path)
            except _m.Refused:
                lk['problem'] = W.BROKEN
        if lk['doc'] is not None:
            why, m = _key_member(lk['doc'])
            if why is None and m is not None and not isinstance(m['value'], str):
                why = 'value'
            if why is not None:
                lk['problem'] = W.CANT_EDIT.format(why=W.WHY[why])
                lk['doc'] = None
            elif m is not None:
                lk['value'] = m['value']
        if lk['problem'] is None and raw is not None and not os.access(path, os.W_OK):
            lk['problem'] = W.CANT_EDIT.format(why=W.WHY['read_only'])

    rec, lk['record_problem'] = _load_record(data)
    lk['record'] = rec
    if rec is not None:
        lk['mine'] = rec['dirs'].get(cd)

    vs = _m._versions() if versions is None else list(versions)
    first = next((v for v in vs if v.get('primary')), vs[0] if vs else None)
    vt = _m._ver(first.get('version')) if first else None
    if vt is not None:
        lk['version'], lk['new_enough'] = _m._vs(vt), vt >= NEED
    lk['other_old'] = [v.get('version') for v in vs
                       if v is not first and (_m._ver(v.get('version')) or (0,)) < NEED]

    environ = os.environ if environ is None else environ
    if environ.get(KEY):
        lk['notes'].append(W.ENV_TOO)
    if SEP in pdir:
        lk['plugin_problem'] = W.BAD_PLUGIN_PATH.format(path=pdir)
    elif not os.path.isfile(os.path.join(pdir, MANIFEST)):
        lk['plugin_problem'] = W.NO_PLUGIN.format(path=pdir)
    return lk


def _key_member(doc_):
    """(why the file cannot be edited, a WHY key, or None; the key's member in the env block, or None)."""
    try:
        env_m = doc_.member('env')
    except _m.Refused:
        return 'twice', None
    if env_m is None:
        return None, None
    if not isinstance(env_m['value'], dict):
        return 'env', None
    members, _ = _m._object_at(doc_.text, env_m['v0'])
    try:
        return None, doc_.member(KEY, members)
    except _m.Refused:
        return 'twice', None


def _ours_in(lk):
    return lk['value'] is not _MISSING and lk['plugin'] in _parts(lk['value'])


def _set_by(lk):
    if not _ours_in(lk):
        return None
    mine = lk['mine']
    return 'switchboard' if mine is not None and mine.get('path') == lk['plugin'] else 'you'


def _version_lines(lk, short=False):
    if lk['new_enough'] is None:
        out = [(W.VERSION_UNKNOWN_SHORT if short else W.VERSION_UNKNOWN).format(need=_need())]
    elif lk['new_enough']:
        out = [W.VERSION_OK.format(version=lk['version'])]
    else:
        out = [W.VERSION_OLD.format(version=lk['version'], need=_need())]
    return out + [W.VERSION_OTHER_OLD.format(version=v) for v in lk['other_old']]


def status(claude_dir=None, data_dir=None, versions=None, environ=None, plugin=None):
    """Whether Claude Code is set to load Switchboard's plugin, who set it, and whether this Claude Code runs it.
    Reads only.

    claude_dir: Claude Code's folder (default: CLAUDE_CONFIG_DIR or ~/.claude). data_dir: Switchboard's folder
    (default: core.data_dir()). versions: [{'where', 'version', 'primary'}] of Claude Code installs (default: looked
    up on disk without running anything). environ: what Claude Code starts with (default: this process's). plugin:
    the plugin folder (default: plugin_dir()).

    Returns {'file', 'exists', 'value' (the key's text, None when unset), 'state' ('unset', 'ours', 'yours',
    'other' or 'unknown'), 'set_by' (None, 'switchboard' or 'you'), 'loaded' (the plugin folder is in the key),
    'plugin', 'version', 'new_enough' (None when not known), 'problem', 'lines', 'notes'}.
    """
    cd, data = _places(claude_dir, data_dir)
    lk = _look(cd, data, versions, environ, plugin or plugin_dir())
    value = None if lk['value'] is _MISSING else lk['value']
    problem = W.NO_FOLDER.format(path=cd) if lk['no_folder'] else (lk['problem'] or lk['record_problem'])
    by = _set_by(lk)
    if lk['doc'] is None and lk['value'] is _MISSING and (lk['problem'] or lk['no_folder']):
        state = 'unknown'
    elif value is None:
        state = 'unset'
    elif by == 'switchboard':
        state = 'ours'
    elif by == 'you':
        state = 'yours'
    else:
        state = 'other'
    lines = {'unset': [W.STATE_UNSET], 'ours': [W.STATE_OURS], 'yours': [W.STATE_YOURS],
             'other': [W.STATE_OTHER], 'unknown': []}[state]
    if problem:
        lines.append(problem)
    lines += _version_lines(lk, short=True)
    return {'file': lk['path'], 'exists': lk['exists'], 'value': value, 'state': state, 'set_by': by,
            'loaded': by is not None, 'plugin': lk['plugin'], 'version': lk['version'],
            'new_enough': lk['new_enough'], 'problem': problem, 'lines': lines, 'notes': list(lk['notes'])}


# ---------------------------------------------------------------- what would change

def _blank(lk):
    return {'file': lk['path'], 'plugin': lk['plugin'], 'op': None, 'old': None, 'new': None, 'blocked': None,
            'says': None, 'notes': [], 'made_file': False, 'made_env': False, 'drops_file': False, 'forget': False}


def _expect(doc_, value):
    """The settings object after the key is set to value (None: the key out of the env block)."""
    obj = json.loads(json.dumps(doc_.obj))
    env = obj.get('env')
    if value is None:
        if isinstance(env, dict):
            env.pop(KEY, None)
    else:
        if not isinstance(env, dict):
            env = obj['env'] = {}
        env[KEY] = value
    return obj


def _work_on(cd, data, lk):
    """What apply() would do, plus the bytes to write ('_after') and what to record ('_mine')."""
    out = _blank(lk)

    def stop(line):
        out['blocked'] = out['says'] = line
        return out

    if lk['no_folder']:
        return stop(W.NO_FOLDER.format(path=cd))
    if lk['record_problem']:
        return stop(lk['record_problem'])
    if lk['problem']:
        return stop(lk['problem'])
    if lk['plugin_problem']:
        return stop(lk['plugin_problem'])
    ours, cur, mine = lk['plugin'], lk['value'], lk['mine']
    if _ours_in(lk):
        out['says'] = W.ALREADY if _set_by(lk) == 'switchboard' else W.YOURS_KEPT
        return out
    if lk['new_enough'] is None:
        return stop(W.VERSION_UNKNOWN.format(need=_need()))
    if lk['new_enough'] is False:
        return stop(W.VERSION_OLD_NO_WRITE.format(version=lk['version'], need=_need()))
    out['notes'] += [W.VERSION_OTHER_OLD.format(version=v) for v in lk['other_old']] + lk['notes']
    doc_ = lk['doc']
    moved = mine is not None and cur is not _MISSING and mine['path'] != ours and mine['path'] in _parts(cur)
    if moved:
        # Switchboard's plugin is in a new place (an update moved it): the old folder it wrote is swapped for it.
        new = SEP.join(ours if x == mine['path'] else x for x in _parts(cur))
    else:
        new = ours if cur is _MISSING or cur == '' else cur + SEP + ours
    try:
        text, made_env = _m._env_set(doc_, KEY, new)
        text = _m._checked(doc_, text, _expect(doc_, new))
    except (_m.Refused, ValueError):
        return stop(W.CANT_EDIT.format(why=W.WHY['other']))
    after = text.encode('utf-8')
    op = 'move' if moved else ('add' if cur is _MISSING else 'append')
    out.update(op=op, old=None if cur is _MISSING else cur, new=new, made_file=lk['raw'] is None, made_env=made_env,
               says={'add': W.WILL_ADD, 'append': W.WILL_APPEND, 'move': W.WILL_MOVE}[op].format(path=ours))
    if out['made_file']:
        out['notes'].append(W.MAKES_FILE)
    elif made_env:
        out['notes'].append(W.MAKES_ENV)
    out['notes'].append(W.NEW_SESSIONS)
    out['_after'] = after
    out['_old_mine'] = mine
    out['_mine'] = {'path': ours, 'key_existed': cur is not _MISSING, 'made_env': made_env,
                    'made_file': lk['raw'] is None, 'existed': lk['raw'] is not None, 'sha_before': _sha(lk['raw']),
                    'sha_after': _sha(after), 'first_backup': None, 'at': time.time()}
    if moved:
        # What was there before Switchboard's first write is still what undo goes back to; only the folder moved.
        out['_mine'].update(key_existed=mine.get('key_existed'), made_env=mine.get('made_env'),
                            made_file=mine.get('made_file'), existed=True, sha_before=None)
    return out


def _work_off(cd, data, lk):
    """What undo() would do, plus the bytes to write ('_after', None to remove the file)."""
    out = _blank(lk)

    def stop(line):
        out['blocked'] = out['says'] = line
        return out

    if lk['no_folder']:
        return stop(W.NO_FOLDER.format(path=cd))
    if lk['record_problem']:
        return stop(lk['record_problem'])
    mine = lk['mine']
    if mine is None:
        out['says'] = W.NOTHING_OURS
        return out

    def held(line):
        # Switchboard's record says it wrote the folder, and the file cannot be changed now: the record stays,
        # so the next undo tries again, and the person is told the folder may still be named.
        out['notes'].append(W.STILL_THERE)
        return stop(line)

    if lk['problem']:
        return held(lk['problem'])
    ours, cur = mine['path'], lk['value']
    if cur is _MISSING or ours not in _parts(cur):
        out.update(forget=True, says=W.GONE_ALREADY)
        return out
    doc_ = lk['doc']
    left = [p for p in _parts(cur) if p != ours]
    if mine.get('key_existed') or any(left):
        new = SEP.join(left)
        try:
            text = _m._checked(doc_, _m._env_set(doc_, KEY, new)[0], _expect(doc_, new))
        except (_m.Refused, ValueError):
            return held(W.CANT_EDIT.format(why=W.WHY['other']))
        op, says = 'take_out', W.WILL_TAKE_OUT.format(path=ours)
    else:
        new = None
        expect = _expect(doc_, None)
        if mine.get('made_env') and expect.get('env') == {}:
            expect.pop('env')
        try:
            text = _m._checked(doc_, _m._env_del(doc_, KEY, bool(mine.get('made_env'))), expect)
        except (_m.Refused, ValueError):
            return held(W.CANT_EDIT.format(why=W.WHY['other']))
        op, says = 'remove', W.WILL_REMOVE
    after = text.encode('utf-8')
    if _sha(lk['raw']) == mine.get('sha_after'):
        # Nothing but Switchboard's own change happened since it wrote: the old bytes come back exactly.
        if not mine.get('existed'):
            after = None
        else:
            old = _backup_bytes(mine)
            after = after if old is None else old
    elif new is None and mine.get('made_file') and not json.loads(text):
        after = None
    out.update(op=op, old=cur, new=new, drops_file=after is None, says=says)
    if after is None:
        out['notes'].append(W.DROPS_FILE)
    out['_after'] = after
    out['_mine'] = None
    return out


def _public(p):
    return dict((k, v) for k, v in p.items() if not k.startswith('_'))


def plan(claude_dir=None, data_dir=None, versions=None, environ=None, plugin=None):
    """Exactly what apply() would change, writing nothing. The arguments are status()'s.

    Returns {'file', 'plugin', 'op' ('add', 'append', 'move' or None), 'old', 'new', 'blocked' (the line when it
    will not), 'says' (one line), 'notes', 'made_file', 'made_env', 'drops_file', 'forget'}.
    """
    cd, data = _places(claude_dir, data_dir)
    return _public(_work_on(cd, data, _look(cd, data, versions, environ, plugin or plugin_dir())))


# ---------------------------------------------------------------- apply and undo

def _run(p, lk, cd, data, did):
    """Write what p says, whole or not at all. Returns p's public part with 'written' and 'backup'."""
    out = _public(p)
    out.update(written=False, backup=None)
    if p['blocked']:
        return out
    rec = lk['record']
    if p['op'] is None:
        if p['forget']:
            for b in (lk['mine'] or {}).get('backups') or []:
                _unlink(b)
            rec['dirs'].pop(cd, None)
            _save_record(data, rec)
        return out
    path, raw, after = lk['path'], lk['raw'], p['_after']
    backup = _backup(data, cd, raw) if raw is not None else None
    if _sha(_m._read(path)) != _sha(raw):                  # last look before the swap
        if backup:
            _unlink(backup)
        out['says'] = W.CHANGED_WHILE
        return out
    mode = _m._mode(path, 0o600)
    old_rec = json.loads(json.dumps(rec))
    try:
        if p['_mine'] is not None:
            # A write: the record goes first, so a stop between the two leaves a record of a write that may not
            # have happened (undo then forgets it) and never our key with no record (undo would leave it in).
            new = p['_mine']
            new['first_backup'] = backup
            new['backups'] = [backup] if backup else []
            rec['dirs'][cd] = new
            _save_record(data, rec)
        if after is None:
            os.unlink(path)
        else:
            _m._atomic_write(path, after, mode)
        if p['_mine'] is None:
            # A take-back: the record goes after, so a stop between the two leaves a record that undo forgets.
            rec['dirs'].pop(cd, None)
            _save_record(data, rec)
    except BaseException:
        try:
            if raw is None:
                _unlink(path)
            else:
                _m._atomic_write(path, raw, mode)
        except OSError:
            pass
        try:
            _save_record(data, old_rec)
        except OSError:
            pass
        if backup:
            _unlink(backup)
        raise
    old = lk['mine'] if p['_mine'] is None else p.get('_old_mine')
    for b in (old or {}).get('backups') or []:             # a copy from an earlier write that is done with
        if b != backup:
            _unlink(b)
    if p['_mine'] is None and backup:
        _unlink(backup)
        backup = None
    out.update(written=True, backup=backup, says=did[p['op']])
    return out


def apply(claude_dir=None, data_dir=None, versions=None, environ=None, plugin=None):
    """Make Claude Code load Switchboard's plugin, whole or not at all. Returns plan()'s shape with 'written'
    (bool) and 'backup' (the copy of the file from before, in Switchboard's folder, or None); 'says' is then the line
    for what was done. Writes nothing when the plan has nothing to change or will not change it, and when the file
    changes while it works. A failure after the file was written puts the old bytes back before it is raised."""
    cd, data = _places(claude_dir, data_dir)
    lk = _look(cd, data, versions, environ, plugin or plugin_dir())
    return _run(_work_on(cd, data, lk), lk, cd, data, {'add': W.DID_ADD, 'append': W.DID_APPEND,
                                                       'move': W.DID_MOVE})


def undo(claude_dir=None, data_dir=None, versions=None, environ=None, plugin=None):
    """Take Switchboard's plugin folder out of the key again, leaving every folder the person set, on any version of
    Claude Code. The key goes (and an env block Switchboard made, and a file it made) only when nothing else is left
    in it. Returns apply()'s shape, with 'op' 'remove', 'take_out' or None."""
    cd, data = _places(claude_dir, data_dir)
    lk = _look(cd, data, versions, environ, plugin or plugin_dir())
    return _run(_work_off(cd, data, lk), lk, cd, data, {'remove': W.DID_REMOVE, 'take_out': W.DID_TAKE_OUT})
