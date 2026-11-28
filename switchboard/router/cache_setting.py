"""The helpers' cache: one key of Claude Code's user settings file, written only when the person turns it on.

Claude Code keeps two cache lifetimes, one for the main chat and one for everything outside it. This module handles
only the second one, through the documented settings key `subagentPromptCacheTtl`. It never writes the main chat's
key (`promptCacheTtl`) or either environment variable. What the page says, read from
https://code.claude.com/docs/en/prompt-caching (saved copy read 2026-11-27):

  - "Everything else: the requests Claude Code makes outside that conversation, such as subagents, workflows,
    in-process teammates, forks, compaction, and session titles".
  - "You can set a TTL for either bucket. Each control takes `5m` or `1h`, and Claude Code ignores any other value."
  - "Everything else: the `subagentPromptCacheTtl` setting, or the `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`
    environment variable".
  - "Both settings and both environment variables require Claude Code v2.1.242 or later."
  - The order Claude Code takes them in: `FORCE_PROMPT_CACHING_5M=1` first, then the bucket's environment variable,
    then the bucket's setting. status() says when one of the first two is set, since it beats this key.
  - The one-hour lifetime "charges a higher rate for cache writes". It helps when requests come more than five
    minutes apart and "costs more on short bursts of work that never idle past five minutes".

status() reads. plan(value) says exactly what would change and writes nothing. apply(value) writes the key (value
'1h' or '5m') or takes Switchboard's key out again (value None), splicing it into the file so every other byte stays
as it was, whole or not at all, with one copy of the file from before the first write and a record in Switchboard's
own folder. undo() is apply(None). A value the person set themselves is never changed, a file that is not valid
JSON is never touched, and a Claude Code too old for the key gets nothing written.

Nothing here runs by itself: a caller turns it on from the person's own switch.
"""
import fcntl
import hashlib
import json
import os
import time
from contextlib import contextmanager

from .. import core
from .. import models as _m
from . import cache_words as W

KEY = W.KEY
VALUES = ('5m', '1h')
NEED = (2, 1, 242)                      # the first Claude Code that reads the key (the page above)
ENV_VAR = 'CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL'
FORCE_VAR = 'FORCE_PROMPT_CACHING_5M'
SETTINGS_NAME = 'settings.json'
RECORD = os.path.join('router', 'cache-setting.json')
BACKUPS = os.path.join('router', 'cache-backups')
LOCK = os.path.join('router', 'cache-setting.lock')
RECORD_VERSION = 1
_MISSING = object()


def _read(path):
    """The file's bytes, or None when there is none."""
    return _m._read(path)


def _sha(data):
    return None if data is None else hashlib.sha256(data).hexdigest()


def _need():
    return _m._vs(NEED)


# ---------------------------------------------------------------- where things are

def _places(claude_dir, data_dir):
    """(Claude Code's folder by real path, Switchboard's data folder)."""
    return _m._cd(claude_dir), (core.data_dir() if data_dir is None else data_dir)


# ---------------------------------------------------------------- Switchboard's record of what it wrote

def _record_path(data):
    return os.path.join(data, RECORD)


def _load_record(data):
    """(record, problem line or None). The record is {'version', 'dirs': {claude folder: what Switchboard wrote}}."""
    blank = {'version': RECORD_VERSION, 'dirs': {}}
    if data is None:
        return blank, None
    raw = _read(_record_path(data))
    if raw is None:
        return blank, None
    try:
        rec = json.loads(raw.decode('utf-8'))
        if not isinstance(rec, dict) or rec.get('version') != RECORD_VERSION or not isinstance(rec.get('dirs'), dict):
            raise ValueError('unexpected shape')
        for mine in rec['dirs'].values():
            if not isinstance(mine, dict) or mine.get('wrote') not in VALUES:
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


@contextmanager
def _held(data):
    """One change at a time across Switchboard's processes (the menu bar, a terminal): an exclusive lock beside the
    record for the whole look, write and record. It cannot stop Claude Code itself writing the settings file between
    the last look and the swap; that window is a few microseconds and the last look makes it as small as it can be."""
    path = os.path.join(data, LOCK)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)                        # closing it lets the lock go


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------- what is there now

def _managed(managed_dir):
    """{'env': the managed env block, 'key': the managed value of KEY or _MISSING}, from the file-based managed
    settings (managed-settings.d/*.json, then managed-settings.json). Files that cannot be read are passed over."""
    out = {'env': {}, 'key': _MISSING}
    if not managed_dir or not os.path.isdir(managed_dir):
        return out
    paths = []
    drop = os.path.join(managed_dir, 'managed-settings.d')
    if os.path.isdir(drop):
        paths += [os.path.join(drop, f) for f in sorted(os.listdir(drop)) if f.endswith('.json')]
    paths.append(os.path.join(managed_dir, 'managed-settings.json'))
    for p in paths:
        raw = _read(p)
        try:
            obj = json.loads(raw.decode('utf-8')) if raw is not None else None
        except (ValueError, UnicodeDecodeError):
            obj = None
        if not isinstance(obj, dict):
            continue
        if isinstance(obj.get('env'), dict):
            out['env'].update(obj['env'])
        if KEY in obj:
            out['key'] = obj[KEY]
    return out


def _look(cd, data, versions, environ, managed_dir):
    """Everything plan() and status() need, read once: the settings file, Switchboard's record, the version, and
    what beats the key."""
    path = os.path.join(cd, SETTINGS_NAME)
    lk = {'cd': cd, 'path': path, 'raw': None, 'exists': False, 'doc': None, 'value': _MISSING, 'problem': None,
          'no_folder': False, 'mine': None, 'record': None, 'record_problem': None, 'version': None,
          'new_enough': None, 'other_old': [], 'wins': []}

    if not os.path.isdir(cd):
        lk['no_folder'] = True
    elif os.path.islink(path):
        lk['problem'] = W.CANT_EDIT.format(why=W.WHY['link'])
    elif os.path.isdir(path):
        lk['problem'] = W.CANT_EDIT.format(why=W.WHY['folder'])
    else:
        raw = _read(path)
        lk['raw'], lk['exists'] = raw, raw is not None
        text_ok = True
        if raw is not None:
            try:
                raw.decode('utf-8')
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
            try:
                m = lk['doc'].member(KEY)
                lk['value'] = _MISSING if m is None else m['value']
            except _m.Refused:
                lk['problem'] = W.CANT_EDIT.format(why=W.WHY['twice'])
                lk['doc'] = None
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
    managed = _managed(_m.default_managed_dir() if managed_dir is None else managed_dir)
    obj = lk['doc'].obj if lk['doc'] is not None else {}
    settings_env = obj.get('env') if isinstance(obj.get('env'), dict) else {}
    places = (('managed', managed['env']), ('settings', settings_env), ('environ', environ))
    for where, env in places:
        if _m._is_on(env.get(FORCE_VAR)):
            lk['wins'].append(W.FORCE_WINS.format(where=W.WHERE[where]))
            break
    for where, env in places:
        if env.get(ENV_VAR):
            lk['wins'].append(W.ENV_WINS.format(name=ENV_VAR, where=W.WHERE[where]))
            break
    if managed['key'] is not _MISSING:
        lk['wins'].append(W.MANAGED_WINS.format(value=managed['key']))
    return lk


def _set_by(lk):
    if lk['value'] is _MISSING:
        return None
    mine = lk['mine']
    return 'switchboard' if mine is not None and lk['value'] == mine['wrote'] else 'you'


def _version_lines(lk):
    if lk['new_enough'] is None:
        out = [W.VERSION_UNKNOWN.format(need=_need())]
    elif lk['new_enough']:
        out = [W.VERSION_OK.format(version=lk['version'])]
    else:
        out = [W.VERSION_OLD.format(version=lk['version'], need=_need())]
    return out + [W.VERSION_OTHER_OLD.format(version=v) for v in lk['other_old']]


def status(claude_dir=None, data_dir=None, versions=None, environ=None, managed_dir=None):
    """What the key is now, who set it, and whether this Claude Code reads it. Reads only.

    claude_dir: Claude Code's folder (default: CLAUDE_CONFIG_DIR or ~/.claude). data_dir: Switchboard's folder
    (default: core.data_dir()). versions: [{'where', 'version', 'primary'}] of Claude Code installs (default: looked
    up on disk without running anything). environ: what Claude Code starts with (default: this process's).
    managed_dir: the file-based managed settings folder (default: the platform's).

    Returns {'file', 'exists', 'value' (None when unset), 'state' ('unset', '5m', '1h', 'other' or 'unknown'),
    'set_by' (None, 'switchboard' or 'you'), 'version', 'new_enough' (None when not known), 'problem', 'lines',
    'notes'}: 'lines' are the plain sentences in order, 'notes' what beats the key.
    """
    cd, data = _places(claude_dir, data_dir)
    lk = _look(cd, data, versions, environ, managed_dir)
    value = None if lk['value'] is _MISSING else lk['value']
    if lk['no_folder']:
        problem = W.NO_FOLDER.format(path=cd)
    else:
        problem = lk['problem'] or lk['record_problem']
    by = _set_by(lk)
    if lk['doc'] is None and lk['value'] is _MISSING and (lk['problem'] or lk['no_folder']):
        state = 'unknown'
    elif value is None:
        state = 'unset'
    else:
        state = value if value in VALUES else 'other'
    if state == 'unset':
        lines = [W.STATE_UNSET]
    elif state == 'other':
        lines = [W.STATE_OTHER.format(value=value)]
    elif state in VALUES:
        lines = [(W.STATE_OURS if by == 'switchboard' else W.STATE_YOURS).format(value=value)]
    else:
        lines = []
    if problem:
        lines.append(problem)
    lines += _version_lines(lk)
    return {'file': lk['path'], 'exists': lk['exists'], 'value': value, 'state': state, 'set_by': by,
            'version': lk['version'], 'new_enough': lk['new_enough'], 'problem': problem, 'lines': lines,
            'notes': list(lk['wins'])}


# ---------------------------------------------------------------- plan

def _check_value(value):
    if value is not None and (not isinstance(value, str) or value not in VALUES):
        raise ValueError('value is %r; it must be one of %s, or None' % (value, ', '.join(VALUES)))


def _splice(doc_, op, value):
    """The settings file's new text after one change to KEY, every other byte as it was; checked by parsing."""
    text = doc_.text
    expect = json.loads(json.dumps(doc_.obj))
    m = doc_.member(KEY)
    if op == 'add':
        new = _m._insert(text, doc_.o, doc_.end, doc_.members, KEY, lambda ml, ind: _m._jdump(value),
                         _m._unit(doc_), True)
        expect[KEY] = value
    elif op == 'change':
        new = text[:m['v0']] + _m._jdump(value) + text[m['v1']:]
        expect[KEY] = value
    else:
        new = _m._remove(text, doc_.o, doc_.end, doc_.members, doc_.members.index(m))
        expect.pop(KEY, None)
    return _m._checked(doc_, new, expect), expect


def _work(value, cd, data, lk):
    """The plan, plus the bytes to write ('_after', None to remove the file) and what to record ('_mine')."""
    _check_value(value)
    out = {'value': value, 'file': lk['path'], 'op': None, 'old': None, 'new': None, 'blocked': None, 'says': None,
           'notes': [], 'made_file': False, 'drops_file': False, 'forget': False}

    def stop(line):
        out['blocked'] = out['says'] = line
        return out

    if lk['no_folder']:
        return stop(W.NO_FOLDER.format(path=cd))
    if lk['record_problem']:
        return stop(lk['record_problem'])
    if lk['problem']:
        return stop(lk['problem'])
    if value is not None:
        out['notes'] += [x for x in _version_lines(lk) if x != W.VERSION_OK.format(version=lk['version'])]
        out['notes'] += lk['wins']
    cur, mine = lk['value'], lk['mine']

    if mine is not None and cur is not _MISSING and cur != mine['wrote']:
        out['forget'] = True
        out['says'] = W.CHANGED_SINCE.format(value=cur)
        return out
    if mine is not None and cur is _MISSING:
        if value is None:
            out['forget'] = True
            out['says'] = W.GONE_ALREADY
            return out
        mine = None                 # the person took it out; a new write starts a new record
    if value is None:
        if mine is None:
            out['says'] = W.NOTHING_OURS
            return out
        op = 'remove'
    else:
        if mine is None and cur is not _MISSING:
            out['says'] = W.YOURS_KEPT.format(value=cur)
            return out
        if lk['new_enough'] is False:
            return stop(W.VERSION_OLD_NO_WRITE.format(version=lk['version'], need=_need()))
        if mine is not None and cur == value:
            out['says'] = W.ALREADY.format(value=value)
            return out
        op = 'change' if mine is not None else 'add'

    try:
        text, expect = _splice(lk['doc'], op, value)
    except _m.Refused:
        return stop(W.CANT_EDIT.format(why=W.WHY['other']))
    raw_sha = _sha(lk['raw'])
    after = text.encode('utf-8')
    if op == 'remove':
        if mine.get('exact') and raw_sha == mine.get('sha_after'):
            # Nothing but Switchboard's own key changed since its first write: the old bytes come back exactly.
            after = None if not mine.get('existed') else _backup_bytes(mine)
        elif mine.get('made_file') and not expect:
            after = None
        if after is None and mine.get('existed'):
            after = text.encode('utf-8')        # the old copy is gone or changed: the splice is still exact enough
        out['drops_file'] = after is None
        out.update(op='remove', old=cur, new=None, says=W.WILL_REMOVE)
        if out['drops_file']:
            out['notes'].append(W.DROPS_FILE)
        out['_mine'] = None
    else:
        out['made_file'] = lk['raw'] is None if op == 'add' else bool(mine.get('made_file'))
        out.update(op=op, old=None if cur is _MISSING else cur, new=value,
                   says=W.WILL_ADD.format(value=value) if op == 'add' else W.WILL_CHANGE.format(old=cur, value=value))
        if op == 'add' and out['made_file']:
            out['notes'].append(W.MAKES_FILE)
        if op == 'add' and lk['raw'] is not None and data is not None:
            out['notes'].append(W.COPY_KEPT.format(path=os.path.join(data, BACKUPS)))
        if op == 'add':
            out['_mine'] = {'wrote': value, 'made_file': out['made_file'], 'existed': lk['raw'] is not None,
                            'sha_before': raw_sha, 'exact': True, 'first_backup': None}
        else:
            out['_mine'] = dict(mine, wrote=value, exact=bool(mine.get('exact') and raw_sha == mine.get('sha_after')))
        out['_mine']['sha_after'] = _sha(after)
        out['_mine']['at'] = time.time()
    out['notes'].append(W.NEW_SESSIONS)
    out['_after'] = after
    return out


def _backup_bytes(mine):
    """The bytes from before Switchboard's first write, when its copy is still there and unchanged, else None."""
    p = mine.get('first_backup')
    raw = _read(p) if p else None
    return raw if raw is not None and _sha(raw) == mine.get('sha_before') else None


def _public(p):
    return dict((k, v) for k, v in p.items() if not k.startswith('_'))


def plan(value, claude_dir=None, data_dir=None, versions=None, environ=None, managed_dir=None):
    """Exactly what apply(value) would change, writing nothing. value: '1h', '5m', or None for "as it was before
    Switchboard". The other arguments are status()'s.

    Returns {'value', 'file', 'op' ('add', 'change', 'remove' or None), 'old', 'new', 'blocked' (the line when it
    will not), 'says' (one line), 'notes', 'made_file', 'drops_file', 'forget'}.
    """
    _check_value(value)
    cd, data = _places(claude_dir, data_dir)
    return _public(_work(value, cd, data, _look(cd, data, versions, environ, managed_dir)))


# ---------------------------------------------------------------- apply and undo

def apply(value, claude_dir=None, data_dir=None, versions=None, environ=None, managed_dir=None):
    """Make the plan for value, whole or not at all. Returns the plan with 'written' (bool) and 'backup' (the copy
    of the file from before Switchboard's first write, in Switchboard's folder, made only by that write, else None);
    'says' is then the line for what was done.

    It writes nothing when the plan has nothing to change or will not change it, and when the file changes while it
    works. A failure after the file was written puts the old bytes back before it is raised.
    """
    _check_value(value)
    cd, data = _places(claude_dir, data_dir)
    first = _work(value, cd, data, _look(cd, data, versions, environ, managed_dir))
    if first['blocked'] or (first['op'] is None and not first['forget']):
        out = _public(first)                # nothing to write: no lock, and nothing made in Switchboard's folder
        out.update(written=False, backup=None)
        return out
    with _held(data):                       # looked at again under the lock, so a change made meanwhile counts
        return _apply(value, cd, data, versions, environ, managed_dir)


def _apply(value, cd, data, versions, environ, managed_dir):
    lk = _look(cd, data, versions, environ, managed_dir)
    p = _work(value, cd, data, lk)
    out = _public(p)
    out.update(written=False, backup=None)
    if p['blocked']:
        return out
    rec = lk['record']
    if p['op'] is None:
        if p['forget']:
            rec['dirs'].pop(cd, None)
            _save_record(data, rec)
        return out

    path, raw, after = lk['path'], lk['raw'], p['_after']
    mine = lk['mine']
    # One copy only: the file from before Switchboard's first write, which an exact undo puts back. It can hold keys,
    # so no further copy is made for a later change.
    backup = _backup(data, cd, raw) if raw is not None and p['op'] == 'add' else None
    if _sha(_read(path)) != _sha(raw):                  # last look before the swap
        if backup:
            _unlink(backup)
        out['says'] = W.CHANGED_WHILE
        return out
    mode = _m._mode(path, 0o600)
    try:
        if after is None:
            os.unlink(path)
        else:
            _m._atomic_write(path, after, mode)
        if p['_mine'] is None:
            rec['dirs'].pop(cd, None)
        else:
            new = p['_mine']
            if p['op'] == 'add':
                new['first_backup'] = backup
            new['backups'] = list((mine or {}).get('backups') or []) + ([backup] if backup else [])
            rec['dirs'][cd] = new
        _save_record(data, rec)
    except BaseException:
        try:
            if raw is None:
                _unlink(path)
            else:
                _m._atomic_write(path, raw, mode)
        except OSError:
            pass
        if backup:
            _unlink(backup)
        raise
    if p['op'] == 'remove':
        for b in (mine or {}).get('backups') or []:     # the file is as it was, so Switchboard's copies can go
            if b != backup:
                _unlink(b)
        if backup:
            _unlink(backup)
            backup = None
    out.update(written=True, backup=backup)
    out['says'] = {'add': W.DID_ADD.format(value=value), 'change': W.DID_CHANGE.format(old=p['old'], value=value),
                   'remove': W.DID_REMOVE}[p['op']]
    return out


def undo(claude_dir=None, data_dir=None, versions=None, environ=None, managed_dir=None):
    """Put back what was there before Switchboard wrote the key (the key gone, and the file gone when Switchboard
    made it), only while the file still holds Switchboard's value. The same as apply(None)."""
    return apply(None, claude_dir, data_dir, versions, environ, managed_dir)
