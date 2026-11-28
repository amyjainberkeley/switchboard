"""Claude Code's files and locks: its global config (merge, never clobber), atomic writes, its lock directories.

- Every file Switchboard writes goes through atomic_write: a temp file beside the target, owner-only from its first
  byte, every byte written, fsync, rename over the target. A reader sees the old file or the new one, never half. A
  symlinked target is written through (the link stays a link), a link loop is refused, a dangling link writes where
  it points. Disk full comes back as DiskFull with the old file untouched.
- The global config (~/.claude.json, or whichever file Claude Code reads: facts.global_config) is read strictly:
  absent is an empty start; a file that can't be read or parsed is never written over (it holds the person's whole
  Claude Code state). Only oauthAccount is ours to replace; every other key is written back exactly as read, in
  Claude Code's own format (2-space JSON, no NaN).
- Claude Code's locks are folders made with mkdir (npm proper-lockfile). We wait for a held one (never steal one
  younger than its stale window), take a stale one over through a sibling ".takeover" folder by renaming it aside and
  checking that what moved is what was judged stale, touch ours every 3 s from a thread, and give up after 9 s with
  nothing changed. Locks are taken in Claude Code's order: .oauth_refresh.lock, then the legacy <folder>.lock. A
  process on its way out lets go of every one it still holds (release_all, run at exit and by the stop paths).
"""
import atexit
import errno
import json
import os
import random
import secrets
import shutil
import stat
import threading
import time
from contextlib import contextmanager

from . import facts
from .api import ABSENT, PRESENT, UNREADABLE, Read

_write = os.write                   # tests replace this to fill the disk at the Nth write
_FSYNC_OK = tuple(e for e in (getattr(errno, n, None) for n in ('EINVAL', 'ENOTSUP', 'EOPNOTSUPP', 'ENOSYS')) if e)
_FULL = tuple(e for e in (getattr(errno, n, None) for n in ('ENOSPC', 'EDQUOT')) if e)


class CCFileError(Exception):
    """A file of Claude Code's that we must not write: kind 'unparseable', 'unreadable', 'loop' or 'changed'."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


class DiskFull(OSError):
    """A write ran out of disk; the target is as it was."""


class LockBusy(Exception):
    """A lock stayed held past its budget. Nothing was changed."""


# ---------- atomic writes ----------

def resolve_target(path):
    """Where a write to path lands: through a chain of symlinks (relative links are relative to their own folder),
    ending at the first name that is not a link (it may not exist yet). A loop raises CCFileError('loop')."""
    p = os.path.abspath(path)
    seen = set()
    for _ in range(40):
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            return p
        if not stat.S_ISLNK(st.st_mode):
            return p
        ident = (st.st_dev, st.st_ino)
        if ident in seen:
            break
        seen.add(ident)
        link = os.readlink(p)
        p = link if os.path.isabs(link) else os.path.join(os.path.dirname(p), link)
    raise CCFileError('loop', 'The path %s is a loop of links; nothing was written there.' % path)


def atomic_write(path, data, mode=0o600, follow=True, sync=True):
    """Publish data (bytes) at path all at once. Returns the path actually written. sync=False leaves out the wait
    for the disk (for a note that only has to outlive this process, not a power cut)."""
    target = resolve_target(path) if follow else os.path.abspath(path)
    folder, name = os.path.split(target)
    tmp = os.path.join(folder, '.%s.%s.tmp' % (name[:40], secrets.token_hex(6)))
    fd = -1
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), mode)
        view = memoryview(data)
        while view:
            n = _write(fd, view)
            view = view[n:]
        os.fchmod(fd, mode)
        if sync:
            try:
                os.fsync(fd)
            except OSError as e:
                if e.errno not in _FSYNC_OK:
                    raise
        os.close(fd)
        fd = -1
        os.replace(tmp, target)
    except BaseException as e:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        if isinstance(e, OSError) and e.errno in _FULL:
            raise DiskFull(e.errno, 'The disk is full; %s was left as it was.' % path) from e
        raise
    return target


def read_bytes(path):
    """(Read, bytes or None): absent only when the file is not there."""
    try:
        with open(path, 'rb') as f:
            return Read(PRESENT, provenance='file'), f.read()
    except (FileNotFoundError, NotADirectoryError):
        return Read(ABSENT), None
    except PermissionError:
        return Read(UNREADABLE, why='denied'), None
    except IsADirectoryError:
        return Read(UNREADABLE, why='not_a_file'), None
    except OSError:
        return Read(UNREADABLE, why='io'), None


def _no_constants(name):
    raise ValueError('not JSON: %s' % name)


def loads_strict(text):
    """JSON as Claude Code's parser takes it: no NaN or Infinity."""
    return json.loads(text, parse_constant=_no_constants)


def dumps_for_claude(obj):
    """JSON the way Claude Code writes its config: 2-space indent, UTF-8 text as is, no NaN."""
    return json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False)


def read_json(path):
    """(Read, dict or None). present: value is the file's text. A file that is not a JSON object is unreadable
    ('unparseable'), never empty."""
    r, raw = read_bytes(path)
    if r.kind != PRESENT:
        return r, None
    try:
        text = raw.decode('utf-8')
        obj = loads_strict(text)
    except (UnicodeDecodeError, ValueError):
        return Read(UNREADABLE, why='unparseable'), None
    if not isinstance(obj, dict):
        return Read(UNREADABLE, why='unparseable'), None
    return Read(PRESENT, value=text, provenance='file'), obj


def free_bytes(path):
    """Free space on the disk holding path (the nearest folder that exists)."""
    p = os.path.abspath(path)
    while not os.path.exists(p) and os.path.dirname(p) != p:
        p = os.path.dirname(p)
    return shutil.disk_usage(p).free


# ---------- Claude Code's global config ----------

def read_config(env, home):
    """(path, Read, dict or None) for the global config Claude Code reads in this environment."""
    path = facts.global_config(env, home)
    r, obj = read_json(path)
    return path, r, obj


def identity_of(obj):
    """The oauthAccount block of a parsed config (a dict), or None."""
    block = obj.get('oauthAccount') if isinstance(obj, dict) else None
    return block if isinstance(block, dict) else None


def write_identity(env, home, block, expect_uuid=None):
    """Replace oauthAccount in the global config with block (a dict), keeping every other key as it was. The caller
    holds Claude Code's config lock. Raises CCFileError when the file can't be read or parsed (nothing written) or
    the read-back does not show the block; DiskFull when the disk is full."""
    path, r, obj = read_config(env, home)
    if r.kind == UNREADABLE:
        raise CCFileError('unparseable' if r.why == 'unparseable' else 'unreadable',
                          "Claude Code's settings file (%s) can't be %s, so Switchboard did not touch it. Open Claude "
                          "Code once so it repairs the file, then try again."
                          % (path, 'understood' if r.why == 'unparseable' else 'read'))
    obj = {} if obj is None else obj
    if block is None:
        obj.pop('oauthAccount', None)
    else:
        obj['oauthAccount'] = block
    atomic_write(path, dumps_for_claude(obj).encode('utf-8'), mode=0o600)
    _, r2, back = read_config(env, home)
    got = identity_of(back) or {}
    want = (block or {}).get('accountUuid') if expect_uuid is None else expect_uuid
    if r2.kind != PRESENT or (block is not None and got.get('accountUuid') != want) or \
            (block is None and back and 'oauthAccount' in back):
        raise CCFileError('changed', "Claude Code's settings file did not keep the account Switchboard wrote.")
    return path


def bump_credentials_file(env, home, text, log=None, announce=None):
    """When Claude Code's plaintext login file exists, write the same login into it (the rename moves its mtime, which
    is how a running Claude Code notices a new login at once). Never creates or deletes it. Best effort: returns the
    stamp of the file it put there, (device, inode, modification time in ns, size), or None when it wrote nothing. The
    stamp is taken from the temporary file before the rename (a rename keeps all four): another process may replace
    the file the moment after, and a stat then would describe that process's file. announce (stamp ->), when given, is
    called with that stamp before the rename, so whoever watches the file knows this write before it can see it."""
    path = facts.credentials_file(env, home)
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    tmp = None
    try:
        target = resolve_target(path)
        folder, name = os.path.split(target)
        tmp = os.path.join(folder, '.%s.%s.new' % (name[:40], secrets.token_hex(6)))
        atomic_write(tmp, text.encode('utf-8'), mode=0o600, follow=False)
        st = os.stat(tmp)
        stamp = (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)
        if announce is not None:
            announce(stamp)
        os.replace(tmp, target)
        return stamp
    except OSError as e:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if log:
            log('Could not refresh Claude Code\'s login file (%s): %s' % (path, e.__class__.__name__))
        return None


# ---------- Claude Code's lock folders ----------

def _ident(st):
    return (st.st_dev, st.st_ino, getattr(st, 'st_birthtime', None))


_HELD = set()                       # the DirLocks this process holds right now (every thread)
_HELD_LOCK = threading.Lock()
_FIRST = []                         # what release_all runs before it lets go of any lock (before_release)
_STOPPING = threading.Event()       # set by release_all: from then on this process takes none of Claude Code's locks


def _note_held(lock, held):
    with _HELD_LOCK:
        if held:
            _HELD.add(lock)
        else:
            _HELD.discard(lock)


def before_release(fn):
    """Have release_all call fn() first, before it lets go of any lock (the Keychain helper ends its children there)."""
    if fn not in _FIRST:
        _FIRST.append(fn)


def stopping():
    """True once release_all has run: this process is on its way out."""
    return _STOPPING.is_set()


def release_all():
    """Let go of everything of Claude Code's this process holds, on its way out. First each function given to
    before_release (the Keychain helper ends every `security` it started, so no write of the item lands after its lock
    is let go), then every lock folder this process holds, each only while it is still the very folder this process
    made (one another process took over meanwhile is never removed). From then on this process takes none of Claude
    Code's locks, and live.write_live refuses a lock that is no longer held; other Keychain calls still run, so a
    renewal's answer can still be saved. Registered with atexit, which runs after tokens.py's wait for a renewal
    under way; a stop path that may leave without the interpreter's exit handlers calls it on its way out. Never
    raises. Returns how many lock folders it removed."""
    _STOPPING.set()
    for fn in list(_FIRST):
        try:
            fn()
        except Exception:  # noqa: BLE001 - letting go never stops half-way
            pass
    with _HELD_LOCK:
        held = list(_HELD)
    removed = 0
    for lock in held:
        try:
            ours = lock.still_held()
            lock.release()
            removed += 1 if ours else 0
        except Exception:  # noqa: BLE001 - the next lock is let go all the same
            pass
    return removed


atexit.register(release_all)


def held_save_lock():
    """Claude Code's save lock on its Keychain item (storage_lock) when the calling thread holds it, else None. The
    Keychain helper shortens every call made while it is held (keychain.UNDER_LOCK_S)."""
    me = threading.get_ident()
    with _HELD_LOCK:
        mine = [lock for lock in _HELD if lock.save and lock.owner == me]
    return max(mine, key=lambda lock: lock.held_for()) if mine else None


class DirLock:
    """One of Claude Code's mkdir locks, held by us. Never re-entrant; acquire() then release() (or `with`)."""

    save = False                    # Claude Code's save lock on its Keychain item (storage_lock)
    owner = None                    # the thread that took it
    taken_mono = None               # when it was taken, on this lock's monotonic clock

    def __init__(self, path, stale_s, budget_s=facts.LOCK_BUDGET_S, touch_s=3.0, wall=time.time,
                 mono=time.monotonic, sleep=time.sleep, log=None, rand=random.random):
        self.path = path
        self.stale_s = stale_s
        self.budget_s = budget_s
        self.touch_s = touch_s
        self._wall, self._mono, self._sleep, self._rand = wall, mono, sleep, rand
        self._log = log or (lambda text: None)
        self._held = None
        self._lost = False
        self._stop = None
        self._thread = None
        self.last_touch_mono = None

    def acquire(self, budget_s=None):
        deadline = self._mono() + (self.budget_s if budget_s is None else budget_s)
        made_parent = False
        while True:
            if _STOPPING.is_set():
                raise LockBusy(self.path)                   # on its way out (release_all): take nothing new
            try:
                os.mkdir(self.path)
                self._took()
                return self
            except FileExistsError:
                pass
            except FileNotFoundError:
                if made_parent:
                    raise
                os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
                made_parent = True
                continue
            left = deadline - self._mono()
            if left <= 0:
                raise LockBusy(self.path)
            try:
                st = os.lstat(self.path)
            except FileNotFoundError:
                continue                                    # let go of just now: try again at once
            if stat.S_ISLNK(st.st_mode) and not os.path.exists(self.path):
                if self._wall() - st.st_mtime <= self.stale_s:
                    self._sleep(min(0.05, left))            # a dangling link: mkdir says "exists", stat says missing
                    continue
            if self._wall() - st.st_mtime > self.stale_s and self._take_over(st):
                continue
            self._sleep(max(0.0, min(left, 0.25 + 0.25 * self._rand())))

    def _took(self):
        now = self._wall()
        try:
            os.utime(self.path, (now, now))
        except OSError:
            pass
        st = os.lstat(self.path)
        self._held = _ident(st)
        self._lost = False
        self.last_touch_mono = self.taken_mono = self._mono()
        self.owner = threading.get_ident()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._toucher, name='lock-toucher', daemon=True)
        self._thread.start()
        _note_held(self, True)
        if _STOPPING.is_set():                              # release_all ran while this was being taken
            self.release()
            raise LockBusy(self.path)

    def held_for(self):
        """Seconds since this lock was taken, on its own clock (0 when it is not held)."""
        return max(0.0, self._mono() - self.taken_mono) if self._held is not None else 0.0

    def _take_over(self, judged):
        """Move a stale lock aside, one taker at a time (a sibling .takeover folder). True when it is gone."""
        guard = self.path + '.takeover'
        try:
            os.mkdir(guard)
        except FileExistsError:
            try:
                g = os.lstat(guard)
                if self._wall() - g.st_mtime > 5.0:         # its taker died part-way
                    self._sweep(guard, g)
            except FileNotFoundError:
                pass
            return False
        except OSError:
            return False
        try:
            try:
                cur = os.lstat(self.path)
            except FileNotFoundError:
                return True
            if (cur.st_dev, cur.st_ino) != (judged.st_dev, judged.st_ino) or \
                    self._wall() - cur.st_mtime <= self.stale_s:
                return False                                # changed hands or was touched: not stale any more
            aside = '%s.stale.%d.%s' % (self.path, os.getpid(), secrets.token_hex(4))
            try:
                os.rename(self.path, aside)
            except FileNotFoundError:
                return True
            moved = os.lstat(aside)
            if (moved.st_dev, moved.st_ino) != (cur.st_dev, cur.st_ino):
                # someone made a fresh lock between the look and the move: put it back if the name is still free
                if not os.path.lexists(self.path):
                    try:
                        os.rename(aside, self.path)
                    except OSError:
                        pass
                self._log('A lock changed hands while being taken over: %s' % self.path)
                return False
            self._remove(aside)
            self._log('Took over a stale lock (%.0f s old): %s' % (self._wall() - cur.st_mtime, self.path))
            return True
        finally:
            try:
                os.rmdir(guard)
            except OSError:
                pass

    def _sweep(self, guard, judged):
        aside = '%s.dead.%s' % (guard, secrets.token_hex(4))
        try:
            os.rename(guard, aside)
            moved = os.lstat(aside)
            if (moved.st_dev, moved.st_ino) == (judged.st_dev, judged.st_ino):
                self._remove(aside)
            elif not os.path.lexists(guard):
                os.rename(aside, guard)
        except OSError:
            pass

    def _remove(self, path):
        try:
            if stat.S_ISDIR(os.lstat(path).st_mode):
                os.rmdir(path)                              # a lock folder is empty; anything else is left alone
            else:
                os.unlink(path)
        except OSError:
            self._log('Left %s in place (not an empty lock folder)' % path)

    def _toucher(self):
        failing_since = None
        warned = False
        while not self._stop.wait(self.touch_s):
            held = self._held
            if held is None or self._stop.is_set():
                return                                      # let go just now (release() runs)
            try:
                st = os.lstat(self.path)
                if _ident(st)[:2] != held[:2]:
                    self._lost = True
                    return
                now = self._wall()
                os.utime(self.path, (now, now))
                self.last_touch_mono = self._mono()
                failing_since, warned = None, False
            except FileNotFoundError:
                self._lost = True
                return
            except OSError:
                failing_since = failing_since or self._mono()
                if not warned and self._mono() - failing_since > self.stale_s:
                    self._log('Could not keep the lock %s fresh for %.0f s' % (self.path, self.stale_s))
                    warned = True

    def record(self):
        """What a journal keeps about a lock we hold, so a later start can clear it if we die holding it."""
        return {'path': self.path, 'ident': list(self._held) if self._held else None}

    def still_held(self):
        """Is the lock folder still the one we made? (A Mac that slept may come back to find it taken over.)"""
        if self._held is None or self._lost:
            return False
        try:
            st = os.lstat(self.path)
        except OSError:
            return False
        return _ident(st)[:2] == self._held[:2] and (self._held[2] is None or _ident(st)[2] == self._held[2])

    def release(self):
        if self._held is None:
            return
        if self._stop is not None:
            self._stop.set()
        ours = self.still_held()
        self._held = None
        _note_held(self, False)
        if ours:
            try:
                os.rmdir(self.path)
            except FileNotFoundError:
                pass
            except OSError:
                self._log('Could not let go of %s' % self.path)
        else:
            self._log('A lock was taken over as stale while held: %s' % self.path)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False


class CredentialLocks:
    """Claude Code's two credential locks, held together in its order."""

    def __init__(self, first, second):
        self.first, self.second = first, second

    def still_held(self):
        return self.first.still_held() and self.second.still_held()

    def records(self):
        return [self.first.record(), self.second.record()]


def clear_orphans(records, log=None):
    """Remove lock folders a dead Switchboard process left (only when each is still exactly the folder it made, so a
    lock Claude Code has since taken is never touched). Returns how many were cleared."""
    cleared = 0
    for rec in records or []:
        path, ident = rec.get('path'), rec.get('ident')
        if not path or not ident:
            continue
        try:
            st = os.lstat(path)
        except OSError:
            continue
        mine = [st.st_dev, st.st_ino] == list(ident[:2]) and (ident[2] is None or
                                                               getattr(st, 'st_birthtime', None) == ident[2])
        if mine and stat.S_ISDIR(st.st_mode):
            try:
                os.rmdir(path)
                cleared += 1
                if log:
                    log('Cleared a lock left by a cut-off switch: %s' % path)
            except OSError:
                pass
    return cleared


@contextmanager
def credential_locks(env, home, budget_s=2 * facts.LOCK_BUDGET_S, wall=time.time, mono=time.monotonic,
                     sleep=time.sleep, log=None, rand=random.random):
    """Take .oauth_refresh.lock then the legacy lock, as Claude Code does: when the second is held, let go of the first,
    wait a moment and try the pair again, all within budget_s. LockBusy when they stay held."""
    folder = facts.secure_dir(env, home)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    kw = dict(stale_s=facts.CRED_LOCK_STALE_S, wall=wall, mono=mono, sleep=sleep, log=log, rand=rand)
    first = DirLock(facts.refresh_lock(env, home), **kw)
    second = DirLock(facts.legacy_lock(env, home), **kw)
    deadline = mono() + budget_s
    while True:
        left = deadline - mono()
        if left <= 0:
            raise LockBusy(first.path)
        first.acquire(budget_s=min(facts.LOCK_BUDGET_S, left))
        try:
            second.acquire(budget_s=min(1.0, max(0.0, deadline - mono())))
            break
        except LockBusy:
            first.release()
            left = deadline - mono()
            if left <= 0:
                raise
            sleep(min(left, 1.0 + rand()))
    try:
        yield CredentialLocks(first, second)
    finally:
        second.release()
        first.release()


@contextmanager
def config_lock(env, home, budget_s=facts.LOCK_BUDGET_S, wall=time.time, mono=time.monotonic, sleep=time.sleep,
                log=None, rand=random.random):
    """Claude Code's lock on its global config (<config>.lock, stale after 10 s)."""
    lock = DirLock(facts.config_lock(env, home), stale_s=facts.CONFIG_LOCK_STALE_S, wall=wall, mono=mono,
                   sleep=sleep, log=log, rand=rand)
    lock.acquire(budget_s=budget_s)
    try:
        yield lock
    finally:
        lock.release()


@contextmanager
def storage_lock(env, home, budget_s=facts.STORAGE_LOCK_BUDGET_S, wall=time.time, mono=time.monotonic,
                 sleep=time.sleep, log=None, rand=random.random):
    """Claude Code's save lock on its Keychain item (<secure-storage folder>/.storage-write.lock, stale after 15 s).
    Claude Code saves the item only while it holds this lock, so a write made while we hold it never lands beside one
    of those saves. Take it last (after the credential locks, as Claude Code does) and let go of it first; never wait
    for another lock or for the network while holding it, and never take it twice (DirLock is not re-entrant).
    LockBusy when a live holder keeps it past budget_s: nothing was changed. While the calling thread holds it, each
    Keychain call is short and all of them together are bounded (keychain.UNDER_LOCK_S, UNDER_LOCK_TOTAL_S)."""
    lock = DirLock(facts.storage_lock(env, home), stale_s=facts.STORAGE_LOCK_STALE_S, wall=wall, mono=mono,
                   sleep=sleep, log=log, rand=rand)
    lock.save = True
    lock.acquire(budget_s=budget_s)
    try:
        yield lock
    finally:
        lock.release()
