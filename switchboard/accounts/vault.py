"""Switchboard's saved logins: Keychain items of our own plus accounts.json metadata, atomic, with backups.

Keychain (account attribute = the account's id):
- "Switchboard login": the account's current login, the JSON Claude Code stored without the Mac-wide siblings
  (facts.SHARED_SIBLINGS: a switch takes those from the live item); a copy an older build kept with them still works;
- "Switchboard login (previous)": up to two earlier generations, newest first, so a login replaced by a stale copy can
  be put back (one previous copy is not enough);
- "Switchboard recovery": logins that exist nowhere else yet: the successor of a refresh whose save failed
  ("successor:<id>") and a safety copy of whatever a switch is about to write over ("safety:<fingerprint>"). If the
  Keychain refuses one, it goes to an owner-only file under <data>/recovery/ instead, so it is never lost. A manifest
  (<data>/recovery/manifest.json, its own lock) lists every entry, without secrets;
- "Switchboard login (Mac-wide parts)" (account attribute = the name of Claude Code's own item, one for the Mac in the
  usual setup): the Mac-wide parts of Claude Code's item as Switchboard last saw them (keep_shared), used in one place
  only: a put-back into no item at all writes the saved login with them (putback._try_once). A switch never uses it.
  <data>/mac-wide-parts.json says which are kept, by a SHA-256 of their text, without secrets.

accounts.json (schema 1, no secrets): accounts by id, their order, aliases, emails, organizations, plan, fingerprints
and expiry stamps of the saved login, its identity block for ~/.claude.json, status, strike, last proof; the
accounts removed on purpose; run-as claims; refreshes in flight; switches made. Read strictly: absent is an empty
start; a file that can't be read or used is restored from the newest of its 5 backups that can be (the damaged file
kept beside it), and with none it is an error and is never written over. Written atomically, keeping 5 backups; a
file that can't be used is never rotated into them.

Locks, always in this order: switch lock (one change of accounts at a time; may be held across a network call made
for the change), consume lock of one account (anything that may spend its refresh token), account lock (accounts.json
and the vault items; local work only), then Claude Code's locks. Kernel file locks: they die with their process.
"""
import fcntl
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager

from . import ccfiles, facts, live as live_mod
from .. import repairs
from .api import ABSENT, PRESENT, UNREADABLE, Account, Read

SCHEMA = 1
SERVICE_CURRENT = 'Switchboard login'
SERVICE_PREVIOUS = 'Switchboard login (previous)'
SERVICE_RECOVERY = 'Switchboard recovery'
SERVICE_SHARED = 'Switchboard login (Mac-wide parts)'
SHARED_NOTE = 'mac-wide-parts.json'     # which records of the Mac-wide parts are kept: {item: {sha, at, size}}
SHARED_LOCK_S = 2.0                     # the wait for the account lock to bring that record up to date (else later)
STORE = 'accounts.json'
BACKUPS = 5
LAST_GOOD = STORE + '.last-good'     # the list as of its last save, written beside it with every save (restore)
NEWER_WORDS = "Switchboard's account list was written by a newer Switchboard. Update this one: switchboard update"
PREVIOUS_KEPT = 2
EXPIRING_S = 7 * 86400
PROVES_OWNER = ('profile', 'token_account')     # proofs that say whose a login is (not just "same sign-in as before")
MIGRATIONS = {}                     # schema n -> function(doc) -> doc for schema n + 1


class VaultError(Exception):
    """accounts.json can't be used: kind 'unreadable' or 'newer'."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


class LockBusy(Exception):
    pass


def blank():
    return {'schema': SCHEMA, 'updated_at': None, 'order': [], 'accounts': {}, 'removed': {}, 'claims': {},
            'refreshing': {}, 'upkeep': {}, 'switches': [], 'settings': {}}


# ---------- locks ----------

_HELD = threading.local()


def _flock(path, timeout, mono=time.monotonic, sleep=time.sleep, interval=0.1):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    deadline = mono() + max(0.0, timeout)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except (BlockingIOError, PermissionError):
            if mono() >= deadline:
                os.close(fd)
                raise LockBusy(path)
            sleep(min(interval, max(0.0, deadline - mono())))


@contextmanager
def _lock(name, path, timeout):
    held = getattr(_HELD, name, None)
    if held:
        raise RuntimeError('the %s is not re-entrant (already held by this thread)' % name)
    fd = _flock(path, timeout)
    setattr(_HELD, name, True)
    try:
        yield
    finally:
        setattr(_HELD, name, False)
        os.close(fd)


def holds(name):
    return bool(getattr(_HELD, name, False))


def switch_lock(ctx, timeout=30.0):
    return _lock('switch_lock', ctx.path('switch.lock'), timeout)


def account_lock(ctx, timeout=10.0):
    return _lock('account_lock', ctx.path('accounts.lock'), timeout)


def _safe(name):
    return re.sub(r'[^A-Za-z0-9._-]', '_', name)[:120]


class ConsumeLock:
    """The lock on spending one account's refresh token. Can be handed to a worker thread (release() from there)."""

    def __init__(self, ctx, account_id, timeout):
        self.path = ctx.path('consume', _safe(account_id) + '.lock')
        self.fd = _flock(self.path, timeout)

    def release(self):
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def consume_lock(ctx, account_id, timeout=5.0):
    return ConsumeLock(ctx, account_id, timeout)


# ---------- accounts.json ----------

def store_path(ctx):
    return ctx.path(STORE)


def load(ctx, restore_it=True):
    """The metadata document. Absent: an empty start. Can't be read or used: restored from the newest of its copies
    that can be (restore); with none, VaultError (never "no accounts"). restore_it=False: a damaged list is reported
    (VaultError) and left for the next ordinary read to restore, for a caller that must never judge by an older copy
    in the look that found the damage (recovery.py)."""
    path = store_path(ctx)
    r, obj = ccfiles.read_json(path)
    if r.kind == ABSENT:
        return blank()
    why = r.why if r.kind != PRESENT else unusable(obj)
    if why == 'newer':
        raise VaultError('newer', NEWER_WORDS)      # never set aside nor replaced: the newer Switchboard's to use
    if why:
        got, outcome = restore(ctx, why) if restore_it else (None, 'off')
        if got is None:
            raise _load_error(path, why, outcome)
        obj = got
    schema = obj.get('schema', 0)
    if not isinstance(schema, int) or schema > SCHEMA:
        raise VaultError('newer', NEWER_WORDS)
    while schema < SCHEMA:
        step = MIGRATIONS.get(schema)
        if step is None:
            break
        obj = step(obj)
        schema = obj.get('schema', schema + 1)
    doc = blank()
    for k, v in obj.items():
        doc[k] = v
    for k in ('accounts', 'removed', 'claims', 'refreshing', 'upkeep', 'settings'):
        if not isinstance(doc.get(k), dict):
            doc[k] = {}
    doc['accounts'] = dict((k, v) for k, v in doc['accounts'].items() if isinstance(v, dict))
    for k in ('order', 'switches'):
        if not isinstance(doc.get(k), list):
            doc[k] = []
    doc['order'] = [a for a in doc['order'] if a in doc['accounts']] + \
        [a for a in doc['accounts'] if a not in doc['order']]
    return doc


def save(ctx, doc):
    """Write the metadata atomically, keeping the last 5 copies, and the same bytes as LAST_GOOD (a copy that can't be
    written is logged; the list is saved all the same). Over a list a newer Switchboard wrote, nothing is written
    (VaultError 'newer'). The caller holds the account lock."""
    if not holds('account_lock'):
        raise RuntimeError('accounts.json is written only under the account lock')
    path = store_path(ctx)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    doc['schema'] = SCHEMA
    doc['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ctx.wall()))
    data = (json.dumps(doc, indent=1, sort_keys=False, allow_nan=False) + '\n').encode('utf-8')
    if os.path.exists(path) and _rotatable(ctx, path):
        for n in range(BACKUPS - 1, 0, -1):
            older = '%s.%d' % (path, n)
            if os.path.exists(older):
                os.replace(older, '%s.%d' % (path, n + 1))
        try:
            with open(path, 'rb') as f:
                ccfiles.atomic_write(path + '.1', f.read(), mode=0o600)
        except OSError:
            pass
    ccfiles.atomic_write(path, data, mode=0o600)
    try:
        ccfiles.atomic_write(ctx.path(LAST_GOOD), data, mode=0o600)
    except OSError as e:
        ctx.log('Could not write the copy of the list of accounts (%s); the list itself was saved'
                % e.__class__.__name__)


# ---------- accounts.json damaged: set aside, restored from its newest copy that can be used ----------
#
# The list can't be read (not JSON, half written) or is not the list's shape: under the account lock, the copy of its
# last save (LAST_GOOD) is written back as the list (whole, then read back), else the newest of accounts.json.1 to .5
# that can be used, the damaged file kept beside it as accounts.json.damaged-<time>, and it is said once: in the log,
# in History, and for a day on the page and in doctor (repairs.json). A numbered copy is older than the list's last
# save, and the sentence says what that means. A list that reads fine is never restored; with no copy that can be used
# nothing moves and it stays an error. A list a newer Switchboard wrote is never restored over, set aside or written
# over (another build of Switchboard may still be running for a moment): load and save refuse it, 'newer'. The logins
# (the Keychain) are not touched. A look that must change nothing (doctor) says no_restore().

RESTORE_LOCK_S = 2.0        # a look that finds the list damaged waits this long for the account lock to restore it;
                            # when it is busy, nothing moves now and the next look restores it
TRANSIENT = ('io',)         # a read error that may pass by itself: the file is left alone, never set aside
_RESTORE = {'off': 0}


@contextmanager
def no_restore():
    """Within the block, a damaged list is reported (VaultError) and never restored: doctor only looks."""
    _RESTORE['off'] += 1
    try:
        yield
    finally:
        _RESTORE['off'] -= 1


def unusable(obj):
    """Why a parsed accounts.json can't be used as the list: 'newer' (a newer Switchboard wrote it), 'shape' (its schema
    not a number, its accounts not an object or missing, an account that is not a record with its id, the order not a
    list), or None."""
    schema = obj.get('schema', 0) if isinstance(obj, dict) else None
    if isinstance(schema, bool) or not isinstance(schema, int):
        return 'shape'
    if schema > SCHEMA:
        return 'newer'
    accts = obj.get('accounts')
    if not isinstance(accts, dict) or not isinstance(obj.get('order', []), list):
        return 'shape'
    for a in accts.values():
        if not isinstance(a, dict) or not isinstance(a.get('uuid'), str) or not a['uuid']:
            return 'shape'
    return None


def _load_error(path, why, outcome):
    """load's VaultError when nothing was restored. outcome: restore's word for why not."""
    if why == 'newer' and outcome != 'no_copy':
        return VaultError('newer', NEWER_WORDS)
    verb = 'read' if why in TRANSIENT + ('denied', 'not_a_file') else 'understood'
    if outcome == 'no_copy':
        return VaultError('newer' if why == 'newer' else 'unreadable',
                          "Switchboard's account list (%s) can't be %s, and none of its copies beside it (%s.1 to .%d) "
                          "could be used; nothing was changed." % (path, verb, STORE, BACKUPS))
    return VaultError('unreadable', "Switchboard's account list (%s) can't be %s; nothing was changed. Its last good "
                                    "copies are beside it (%s.1 to .%d)." % (path, verb, STORE, BACKUPS))


def _made_at(obj, path):
    """When a copy was written: its own updated_at, else the file's time."""
    try:
        import calendar
        return float(calendar.timegm(time.strptime(obj.get('updated_at') or '', '%Y-%m-%dT%H:%M:%SZ')))
    except (TypeError, ValueError):
        try:
            return os.stat(path).st_mtime
        except OSError:
            return None


def _set_aside(ctx, path, kind):
    """Keep the file at path beside it as <path>.<kind>-<time> (a copy when its bytes can be read, else the file itself
    moved). Returns the new name, or None when it could not be kept (then nothing else may change)."""
    stamp = int(ctx.wall())
    aside = '%s.%s-%d' % (path, kind, stamp)
    n = 1
    while os.path.exists(aside):
        n += 1
        aside = '%s.%s-%d-%d' % (path, kind, stamp, n)
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except OSError:
        raw = None
    try:
        if raw is None:
            os.replace(path, aside)
        else:
            ccfiles.atomic_write(aside, raw, mode=0o600, follow=False)
    except OSError as e:
        ctx.log('Could not keep the damaged list of accounts aside (%s): it was left as it is' % e.__class__.__name__)
        return None
    return aside


def _rotatable(ctx, path):
    """save(): the list about to become the newest copy can be used. A file that can't (damaged after it was read) is
    set aside instead, so a bad file never pushes the good copies out."""
    r, obj = ccfiles.read_json(path)
    if r.kind == PRESENT and unusable(obj) is None:
        return True
    if r.kind == PRESENT and unusable(obj) == 'newer':
        raise VaultError('newer', NEWER_WORDS)      # a newer Switchboard's list is never set aside nor written over
    if r.kind != ABSENT:
        aside = _set_aside(ctx, path, 'newer' if r.kind == PRESENT and unusable(obj) == 'newer' else 'damaged')
        if aside is None:                           # it could not be kept: nothing is written over it
            raise OSError("the list of accounts can't be used and could not be kept aside, so it was not written over")
        ctx.log('The list of accounts was damaged when it was about to be written; kept as %s, the copies beside it '
                'left as they were' % os.path.basename(aside))
    return False


def restore(ctx, why):
    """(the restored document as parsed, 'restored') or (None, why not: 'off', 'transient', 'busy', 'no_copy',
    'not_kept', 'not_written'). why: what load found. See above."""
    if _RESTORE['off']:
        return None, 'off'
    if why in TRANSIENT:
        return None, 'transient'
    try:
        if holds('account_lock'):
            return _restore_locked(ctx)
        with account_lock(ctx, timeout=RESTORE_LOCK_S):
            return _restore_locked(ctx)
    except LockBusy:
        return None, 'busy'


def _restore_locked(ctx):
    path = store_path(ctx)
    r, obj = ccfiles.read_json(path)
    if r.kind == PRESENT and unusable(obj) is None:
        return obj, 'fine'                          # it reads fine now (another look restored it): never over it
    if r.kind == ABSENT:
        return None, 'gone'
    if r.kind == UNREADABLE and r.why in TRANSIENT:
        return None, 'transient'
    why = unusable(obj) if r.kind == PRESENT else r.why
    if why == 'newer':
        return None, 'newer'                        # never set aside nor replaced (load refuses it)
    first, fobj = ccfiles.read_json('%s.1' % path)
    newest = _made_at(fobj, '%s.1' % path) if first.kind == PRESENT and unusable(fobj) is None else None
    for copy in [ctx.path(LAST_GOOD)] + ['%s.%d' % (path, n) for n in range(1, BACKUPS + 1)]:
        cr, cobj = ccfiles.read_json(copy)
        if cr.kind != PRESENT or unusable(cobj) is not None:
            continue
        as_it_was = copy == ctx.path(LAST_GOOD)
        if as_it_was and newest is not None and (_made_at(cobj, copy) or 0) < newest:
            continue                                # an older copy than .1 (its own write failed once): passed over
        aside = _set_aside(ctx, path, 'damaged')
        if aside is None:
            return None, 'not_kept'
        raw = cr.value.encode('utf-8')
        try:
            ccfiles.atomic_write(path, raw, mode=0o600)
            with open(path, 'rb') as f:
                back = f.read()
        except OSError as e:
            ctx.log('Could not write the list of accounts back from %s (%s)' % (os.path.basename(copy),
                                                                              e.__class__.__name__))
            if not os.path.exists(path):
                try:
                    os.replace(aside, path)         # moved aside and nothing in its place: the damaged one goes back
                except OSError:
                    pass
            return None, 'not_written'
        if back != raw:
            return None, 'not_written'
        made = _made_at(cobj, copy)
        when = repairs.when_words(made) if made else 'earlier'
        # the copy of the last save is the list as it was; a numbered one is older than the list's last save
        text = ("Switchboard's list of accounts was damaged. It was restored from a copy made %s." % when
                if as_it_was else
                "Switchboard's list of accounts was damaged. It was restored from an older copy, made %s: accounts "
                "added, removed or changed after that are not in it. Add an account that is missing again." % when)
        ctx.log('Restored the list of accounts from %s (%s); the one that could not be used is kept as %s'
                % (os.path.basename(copy), why, os.path.basename(aside)))
        ctx.say(text, 'warn', h='repair', by='auto')
        repairs.note(ctx.data_dir, 'accounts', 'restored', text, now=ctx.wall(), aside=aside, copy=copy,
                     copy_at=made, why=why)
        return cobj, 'restored'
    ctx.log("The list of accounts can't be used (%s) and none of its copies can be: it was left as it is" % why)
    return None, 'no_copy'


@contextmanager
def changing(ctx, timeout=10.0):
    """Load, let the caller change, save: under the account lock."""
    with account_lock(ctx, timeout=timeout):
        doc = load(ctx)
        yield doc
        save(ctx, doc)


# ---------- the saved logins in the Keychain ----------

def read_current(ctx, account_id, fresh=True, interactive=False):
    return ctx.keychain.read(SERVICE_CURRENT, account_id, fresh=fresh, interactive=interactive)


def read_previous(ctx, account_id):
    """(Read, [generation dicts newest first]); each {'text', 'fp', 'saved_at'}."""
    r = ctx.keychain.read(SERVICE_PREVIOUS, account_id, fresh=True, interactive=True)
    if r.kind != PRESENT:
        return r, []
    try:
        env = json.loads(r.value)
        gens = [g for g in env.get('generations', []) if isinstance(g, dict) and isinstance(g.get('text'), str)]
    except (ValueError, AttributeError):
        return Read(UNREADABLE, why='unparseable'), []
    return r, gens


def _push_previous(ctx, account_id, old_text):
    """Keep old_text as the newest earlier generation (best effort; an unreadable list is not written over)."""
    r, gens = read_previous(ctx, account_id)
    if r.kind == UNREADABLE:
        ctx.log('Kept no earlier copy of %s: its earlier copies could not be read (%s)' % (account_id, r.why))
        return False
    fp = live_mod.parse(old_text).fingerprint
    keep = [{'text': old_text, 'fp': fp, 'saved_at': ctx.wall()}]
    keep += [g for g in gens if g.get('text') != old_text][:PREVIOUS_KEPT - 1]
    done = ctx.keychain.write(SERVICE_PREVIOUS, account_id, json.dumps({'generations': keep}, ensure_ascii=True),
                              interactive=True)
    if not done.ok:
        ctx.log('Kept no earlier copy of %s (%s)' % (account_id, done.why))
    return done.ok


def bound_text(text):
    """text as a saved login keeps it: without the Mac-wide parts of Claude Code's item (facts.SHARED_SIBLINGS), which
    a switch always takes from the live item, never from a saved copy. text itself, byte for byte, when it has none."""
    blob = live_mod.parse(text).blob
    if not isinstance(blob, dict) or not any(k in blob for k in facts.SHARED_SIBLINGS):
        return text
    return live_mod.dump(live_mod.account_bound(blob))


def same_saved(a, b):
    """Would logins a and b be kept as the same saved login? Compared without the Mac-wide parts, so a copy saved by an
    older build (with them) is not saved again only for that."""
    if a == b:
        return True
    ba, bb = live_mod.parse(a).blob, live_mod.parse(b).blob
    return isinstance(ba, dict) and isinstance(bb, dict) and \
        live_mod.dump(live_mod.account_bound(ba)) == live_mod.dump(live_mod.account_bound(bb))


def save_login(ctx, account_id, text, old=None):
    """Make text account_id's current saved login, the replaced one kept as an earlier generation, and read it back.
    Only its account-bound parts are kept (bound_text). old: the Read of the current item the caller already has (else
    it is read now). Caller holds the account lock. Returns keychain.Done."""
    from .keychain import Done
    if old is None:
        old = read_current(ctx, account_id, fresh=True, interactive=True)
    text = bound_text(text)
    if old.kind == PRESENT and old.value and not same_saved(old.value, text):
        _push_previous(ctx, account_id, old.value)
    done = ctx.keychain.write(SERVICE_CURRENT, account_id, text, interactive=True)
    if not done.ok:
        return done
    back = read_current(ctx, account_id, fresh=True, interactive=True)
    if back.kind != PRESENT or back.value != text:
        return Done(False, 'verify')
    ctx.log('Saved the login of %s (%s)' % (account_id, live_mod.parse(text).fingerprint or 'no refresh token'))
    return done


def delete_logins(ctx, account_id):
    """Remove an account's current and earlier saved logins and its pending successor. [(service, Done)]."""
    out = []
    for service, acct in ((SERVICE_CURRENT, account_id), (SERVICE_PREVIOUS, account_id),
                          (SERVICE_RECOVERY, 'successor:' + account_id)):
        out.append((service, ctx.keychain.delete(service, acct)))
    return out


# ---------- the Mac-wide parts of Claude Code's item, kept once for the Mac ----------
#
# A saved login keeps only its account's parts (bound_text): a switch takes the Mac-wide parts (connector and plugin
# logins, the design login) from the live item. When Claude Code's item is gone entirely there is no live item to take
# them from, so Switchboard keeps one record of them, brought up to date whenever it holds Claude Code's item read whole
# with an account login (roster.add_current, the keep pass, a look of the put-back), and taken out when such a read
# holds no Mac-wide part (every connector removed), and used in one place only: a put-back into no item at all writes
# the saved login with them. Never Claude Code's overflow pointer (facts.OVERFLOW_KEY: it points at parts that may be
# gone or newer). A record that can't be read changes nothing: the saved login is written as before.

_SHARED_LOCK = threading.Lock()
_SHARED_SEEN = {}                   # (data folder, item) -> the SHA-256 (16 hex) of the text kept, as this process knows
_SHARED_SAID = set()                # (data folder, item, why): a failure to keep or take out the record, said once


def shared_parts(blob):
    """The Mac-wide parts of a login object as the record keeps them: facts.SHARED_SIBLINGS but the overflow pointer.
    {} when there are none (or blob is not an object)."""
    if not isinstance(blob, dict):
        return {}
    return dict((k, blob[k]) for k in facts.SHARED_SIBLINGS if k in blob and k != facts.OVERFLOW_KEY)


def _sha16(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def _shared_noted(ctx):
    """{item: {'sha', 'at', 'size'}} from SHARED_NOTE ({} when it is not there or can't be used)."""
    r, obj = ccfiles.read_json(ctx.path(SHARED_NOTE))
    if r.kind != PRESENT or not isinstance(obj, dict):
        return {}
    return dict((k, v) for k, v in obj.items() if isinstance(k, str) and isinstance(v, dict))


def shared_services(ctx):
    """The names of Claude Code's items a record of the Mac-wide parts is kept for (the uninstaller takes each out), with
    this environment's own item always among them."""
    names = set(_shared_noted(ctx))
    names.add(facts.keychain_service(ctx.env))
    return sorted(names)


def keep_shared(ctx, lr):
    """Bring the record of the Mac-wide parts up to date from a live read lr (live.LiveRead): only a read of Claude
    Code's Keychain item itself (never its plain login file, a cached copy or a fallback) that holds a full account
    login. With at least one Mac-wide part, the record is written only when the parts differ from the ones kept
    (SHARED_NOTE, by their SHA-256), under the account lock, and read back as a saved login is; a long value goes the
    way the vault's do (keychain.write). With none (every connector removed) and a record noted for that item, the
    record is taken out of the Keychain and its note goes (forget_shared for that item), under the same lock, so a
    later put-back into no item at all never writes a removed login back; nothing is asked of the Keychain while none
    is noted. A write or a removal that does not land changes nothing and is said once in the log (a later look tries
    again). Never raises. True when the record was written or taken out."""
    service, sha, out = '?', None, False
    try:
        read, login = getattr(lr, 'read', None), getattr(lr, 'login', None)
        if read is None or login is None or read.kind != PRESENT or read.degraded or \
                read.provenance != 'keychain' or not login.full_pair:
            return False
        parts = shared_parts(login.blob)
        service = getattr(lr, 'service', None) or facts.keychain_service(ctx.env)
        key = (ctx.data_dir, service)
        if not parts:
            if service not in _shared_noted(ctx):
                with _SHARED_LOCK:
                    _SHARED_SEEN.pop(key, None)
                return False
            out = True
            if holds('account_lock'):
                why = _drop_shared(ctx, service)
            else:
                with account_lock(ctx, timeout=SHARED_LOCK_S):
                    why = _drop_shared(ctx, service)
        else:
            text = live_mod.dump(parts)
            sha = _sha16(text)
            with _SHARED_LOCK:
                if _SHARED_SEEN.get(key) == sha:
                    return False
            if (_shared_noted(ctx).get(service) or {}).get('sha') == sha:
                with _SHARED_LOCK:
                    _SHARED_SEEN[key] = sha
                return False
            if holds('account_lock'):
                why = _write_shared(ctx, service, text, sha)
            else:
                with account_lock(ctx, timeout=SHARED_LOCK_S):
                    why = _write_shared(ctx, service, text, sha)
    except LockBusy:
        return False                                # busy: the next look brings it up to date
    except Exception as e:  # noqa: BLE001 - the record is a help for a rare case; nothing else may fail for it
        why = e.__class__.__name__
    if why is None:
        if not out:
            with _SHARED_LOCK:
                _SHARED_SEEN[(ctx.data_dir, service)] = sha
        return True
    said = (ctx.data_dir, service, ('out:' if out else '') + why)
    with _SHARED_LOCK:
        new = said not in _SHARED_SAID
        _SHARED_SAID.add(said)
    if new and out:
        ctx.log("Could not take out the record of the Mac-wide parts of Claude Code's login (%s), which holds none "
                "now; a put-back into no item at all could write a removed connector login back" % why)
    elif new:
        ctx.log("Could not keep the Mac-wide parts of Claude Code's login (%s); a put-back into no item at all would "
                "write the saved login without them" % why)
    return False


def _drop_shared(ctx, service):
    """Under the account lock: the record of Claude Code's item `service` taken out of the Keychain, then its note
    (forget_shared for that item). None when it is gone, else why not (then nothing changed)."""
    if service not in _shared_noted(ctx):
        return None                                 # another process took it out a moment ago
    done = ctx.keychain.delete(SERVICE_SHARED, service)
    if not done.ok:
        return done.why or 'unknown'
    forget_shared(ctx, service)
    return None


def _write_shared(ctx, service, text, sha):
    """Under the account lock: the record written and read back, then noted. None when it landed, else why not."""
    noted = _shared_noted(ctx)
    if (noted.get(service) or {}).get('sha') == sha:
        return None                                 # another process kept the same parts a moment ago
    done = ctx.keychain.write(SERVICE_SHARED, service, text, interactive=True)
    if not done.ok:
        return done.why or 'unknown'
    back = ctx.keychain.read(SERVICE_SHARED, service, fresh=True, interactive=True)
    if back.kind != PRESENT or back.value != text:
        return 'verify'
    noted[service] = {'sha': sha, 'at': round(ctx.wall(), 3), 'size': len(text.encode('utf-8'))}
    ccfiles.atomic_write(ctx.path(SHARED_NOTE), (json.dumps(noted, indent=1, sort_keys=True) + '\n').encode('utf-8'),
                         mode=0o600)
    return None


def read_shared(ctx, service):
    """The Mac-wide parts kept for Claude Code's item `service` (shared_parts of the record), or None: none is noted as
    kept, or the record is not there, can't be read now, or is not an object holding any."""
    if service not in _shared_noted(ctx):
        return None
    r = ctx.keychain.read(SERVICE_SHARED, service, fresh=True, interactive=True)
    if r.kind != PRESENT or not r.value:
        return None
    return shared_parts(live_mod.parse(r.value).blob) or None


def forget_shared(ctx, service=None):
    """After the uninstaller took the records out of the Keychain: the note goes, and this process forgets them. With
    service (keep_shared, once that record is out of the Keychain): only that item's line of the note, and the note
    itself when it was the last."""
    if service is not None:
        noted = _shared_noted(ctx)
        if noted.pop(service, None) is not None:
            if noted:
                ccfiles.atomic_write(ctx.path(SHARED_NOTE),
                                     (json.dumps(noted, indent=1, sort_keys=True) + '\n').encode('utf-8'), mode=0o600)
            else:
                try:
                    os.unlink(ctx.path(SHARED_NOTE))
                except OSError:
                    pass
        with _SHARED_LOCK:
            _SHARED_SEEN.pop((ctx.data_dir, service), None)
        return
    try:
        os.unlink(ctx.path(SHARED_NOTE))
    except OSError:
        pass
    with _SHARED_LOCK:
        for key in [k for k in _SHARED_SEEN if k[0] == ctx.data_dir]:
            del _SHARED_SEEN[key]


# ---------- recovery: logins that exist nowhere else yet ----------

def _recovery_dir(ctx):
    return ctx.path('recovery')


@contextmanager
def _manifest(ctx):
    """The recovery manifest, read-changed-written under its own lock. A damaged one is set aside, never lost."""
    folder = _recovery_dir(ctx)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    path = os.path.join(folder, 'manifest.json')
    fd = _flock(os.path.join(folder, 'manifest.lock'), 10.0)
    try:
        r, obj = ccfiles.read_json(path)
        writable = True
        if r.kind == ABSENT:
            entries = []
        elif r.kind == PRESENT and isinstance(obj.get('entries'), list):
            entries = obj['entries']
        elif r.kind == UNREADABLE and r.why == 'unparseable':
            os.replace(path, '%s.damaged-%d' % (path, int(time.time())))
            entries = []
        else:
            entries, writable = [], False             # can't be read now: never written over
        box = {'entries': entries}
        yield box
        if writable:
            ccfiles.atomic_write(path, (json.dumps({'entries': box['entries']}, indent=1) + '\n').encode('utf-8'))
    finally:
        os.close(fd)


def stash(ctx, key, text, account_id=None, reason='', supersedes=None, names=None):
    """Keep text somewhere it survives: the "Switchboard recovery" Keychain item named key, or an owner-only file when
    the Keychain refuses. names: the account uuid Anthropic named as the login's owner, when that is not account_id's
    (tokens.py's identity conflict). Each copy is marked, beside it, whether its text holds a token or a key and no
    refresh token (live.unrenewable): `leftovers` then tells it from a sign-in without reading it, and a copy with no
    mark is one an older Switchboard made. Returns (ok, where)."""
    login = live_mod.parse(text)
    mark = {'token': bool(live_mod.unrenewable(login))}
    where = None
    done = ctx.keychain.write(SERVICE_RECOVERY, key, text, interactive=True)
    if done.ok:
        back = ctx.keychain.read(SERVICE_RECOVERY, key, fresh=True, interactive=True)
        if back.kind == PRESENT and back.value == text:
            where = 'keychain'
        else:                                       # "done", yet not there: this may be the login's only copy
            from .keychain import Done
            done = Done(False, 'verify')
    if where is None:
        try:
            folder = _recovery_dir(ctx)
            os.makedirs(folder, mode=0o700, exist_ok=True)
            body = dict({'key': key, 'account_id': account_id, 'reason': reason, 'supersedes': supersedes,
                         'names': names, 'fp': login.fingerprint, 'at': ctx.wall(), 'text': text}, **mark)
            ccfiles.atomic_write(os.path.join(folder, _safe(key) + '.json'),
                                 json.dumps(body, ensure_ascii=True).encode('utf-8'), mode=0o600)
            where = 'file'
        except OSError as e:
            ctx.log('Could not keep a recovery copy %s anywhere: Keychain %s, file %s' % (key, done.why, e))
            return False, None
    entry = dict({'key': key, 'account_id': account_id, 'reason': reason, 'supersedes': supersedes, 'names': names,
                  'fp': login.fingerprint, 'expires_ms': login.expires_ms, 'rx_ms': login.refresh_expires_ms,
                  'at': ctx.wall(), 'where': where}, **mark)
    try:
        with _manifest(ctx) as m:
            m['entries'] = [e for e in m['entries'] if e.get('key') != key] + [entry]
    except (OSError, LockBusy) as e:
        ctx.log('Recovery copy %s kept in the %s but not listed: %s' % (key, where, e))
    ctx.log('Kept a recovery copy %s (%s) in the %s: %s' % (key, login.fingerprint or login.text_fp, where, reason))
    return True, where


def stashed(ctx):
    """Every recovery entry (no secrets): the manifest, plus self-describing files it may have missed."""
    entries = []
    try:
        with _manifest(ctx) as m:
            entries = list(m['entries'])
    except (OSError, LockBusy):
        pass
    keys = set(e.get('key') for e in entries)
    folder = _recovery_dir(ctx)
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    for name in names:
        if not name.endswith('.json') or name == 'manifest.json':
            continue
        r, obj = ccfiles.read_json(os.path.join(folder, name))
        if r.kind == PRESENT and obj.get('key') and obj['key'] not in keys:
            entry = dict((k, obj.get(k)) for k in ('key', 'account_id', 'reason', 'supersedes', 'names', 'fp', 'at'))
            entry['where'] = 'file'
            if isinstance(obj.get('token'), bool):
                entry['token'] = obj['token']
            entries.append(entry)
    return entries


def read_stash(ctx, key, where=None):
    """The text kept under key, as a Read (Keychain first, then its file). With no place given, a Keychain that can't
    be read just now is said as that when no file holds the text either: the item may be there."""
    asked = None
    if where in (None, 'keychain'):
        asked = r = ctx.keychain.read(SERVICE_RECOVERY, key, fresh=True, interactive=True)
        if r.kind == PRESENT or where == 'keychain':
            return r
    r, obj = ccfiles.read_json(os.path.join(_recovery_dir(ctx), _safe(key) + '.json'))
    if r.kind == PRESENT:
        text = obj.get('text')
        return Read(PRESENT, value=text, provenance='file') if isinstance(text, str) else Read(UNREADABLE,
                                                                                                why='unparseable')
    return asked if r.kind == ABSENT and asked is not None and asked.kind == UNREADABLE else r


def drop_stash(ctx, key):
    """Forget a recovery entry once its login is safely saved."""
    ctx.keychain.delete(SERVICE_RECOVERY, key)
    try:
        os.unlink(os.path.join(_recovery_dir(ctx), _safe(key) + '.json'))
    except OSError:
        pass
    try:
        with _manifest(ctx) as m:
            m['entries'] = [e for e in m['entries'] if e.get('key') != key]
    except (OSError, LockBusy):
        pass


# ---------- accounts as people see them ----------

def account_key(doc, uuid, org):
    """The id a login of (account uuid, organization) is kept under: the uuid, or uuid/org when the same person is
    already kept for another organization (one email in two organizations is two accounts)."""
    if not uuid:
        return None
    org = org or ''
    for aid, a in doc['accounts'].items():
        if a.get('uuid') == uuid and (not org or not a.get('org_id') or a.get('org_id') == org):
            return aid
    return '%s/%s' % (uuid, org) if uuid in doc['accounts'] else uuid


def claimed(ctx, doc, account_id):
    """The live run-as claim on an account, or None (a claim whose processes are gone is not a claim)."""
    c = doc.get('claims', {}).get(account_id)
    if not isinstance(c, dict):
        return None
    return c if claim_alive(ctx, c) else None


def claim_alive(ctx, c):
    for pid_key, start_key in (('pid', 'start'), ('child', 'child_start')):
        pid = c.get(pid_key)
        if not pid or not ctx.pid_alive(pid):
            continue
        want = c.get(start_key)
        if not want:
            return True                                 # can't tell: a live process is never called gone
        got = ctx.proc_start(pid)
        if got is None or got == want:
            return True
    return False


def status_of(ctx, doc, aid, now=None):
    a = doc['accounts'][aid]
    now = ctx.wall() if now is None else now
    if claimed(ctx, doc, aid):
        return 'claimed'
    if a.get('conflict'):
        return 'conflict'                               # its saved login is another account's (Anthropic said so)
    strike = a.get('strike') or {}
    if strike.get('fp') and strike.get('fp') == a.get('fp'):
        return 'needs_login'
    rx = a.get('rx_ms')
    if rx and rx / 1000.0 <= now:
        return 'needs_login'
    if a.get('unreadable_at'):
        return 'unreadable'
    if rx and rx / 1000.0 - now <= EXPIRING_S:
        return 'expiring'
    return 'ok'


def _said(block, key):
    """One word of the settings block kept with an account, or None when it is not a word."""
    v = block.get(key) if isinstance(block, dict) else None
    return (v.strip() or None) if isinstance(v, str) else None


def to_account(ctx, doc, aid, now=None):
    a = doc['accounts'][aid]
    order = doc['order'].index(aid) + 1 if aid in doc['order'] else len(doc['order']) + 1
    rx = a.get('rx_ms')
    block = a.get('identity')       # Claude Code's settings for the account, as they were when it was last kept
    return Account(id=aid, email=a.get('email') or '', org_id=a.get('org_id') or None,
                   org_name=a.get('org_name') or None, plan=a.get('plan') or None, alias=a.get('alias') or None,
                   order=order, status=status_of(ctx, doc, aid, now), refresh_expires_at=rx / 1000.0 if rx else None,
                   added_at=float(a.get('added_at') or 0.0), fingerprint=a.get('fp') or None,
                   tier=_said(block, 'organizationRateLimitTier'), org_type=_said(block, 'organizationType'))


def list_accounts(ctx):
    doc = load(ctx)
    now = ctx.wall()
    return [to_account(ctx, doc, aid, now) for aid in doc['order'] if aid in doc['accounts']]


def note_login(a, login, proof=None, now=None):
    """Copy what metadata keeps about a saved login onto an account record."""
    old = a.get('fp')
    if old and old != login.fingerprint:
        a['previous_fps'] = ([old] + [f for f in (a.get('previous_fps') or []) if f != old])[:PREVIOUS_KEPT]
    a['fp'] = login.fingerprint
    a['access_fp'] = login.access_fp
    a['expires_ms'] = login.expires_ms
    a['rx_ms'] = login.refresh_expires_ms
    if login.plan:
        a['plan'] = login.plan
    a.pop('unreadable_at', None)
    if (a.get('strike') or {}).get('fp') != login.fingerprint:
        a.pop('strike', None)
    if proof:
        a['proof'] = {'kind': proof, 'at': now}
    if proof in PROVES_OWNER:
        a.pop('conflict', None)                         # Anthropic said this login is this account's own
    a['saved_at'] = now


def new_account(doc, aid, block, login, proof, now):
    """A fresh record for an account seen for the first time."""
    uuid, email, org = live_mod.label(block)
    a = {'id': aid, 'uuid': uuid or aid.split('/')[0], 'email': email, 'org_id': org,
         'org_name': (block or {}).get('organizationName') if isinstance((block or {}).get('organizationName'),
                                                                         str) else None,
         'identity': dict(block or {}), 'added_at': now}
    note_login(a, login, proof, now)
    doc['accounts'][aid] = a
    if aid not in doc['order']:
        doc['order'].append(aid)
    doc['removed'].pop(aid, None)
    return a
