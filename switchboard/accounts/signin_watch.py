"""The sign-in watch: a small process that notices the moment Claude Code's sign-in may have changed, and has the
account engine look at once.

    python -m switchboard.accounts.signin_watch [--once] [--status] [--tidy]

--tidy (the uninstaller's step) exits 0, or EXIT_AGAIN (3) when it left Claude Code's plain login file and its note
in place because Claude Code's Keychain gave no answer; it then prints "Run the uninstaller again to finish." at the
end of its line, and the uninstaller must stop before it removes the program or the data folder.

Why it exists. Claude Code (as of 2.1.329) keeps its login in one Keychain item, and each of its
processes saves the whole item. When two of them save within about 100 ms, macOS refuses one save, and Claude Code
answers a refused save by writing the login to its plaintext file and deleting the Keychain item. A chat that sends a
request while the login is gone shows its sign-in screen, and only a person brings it back. So a look has to come
within a fraction of a second of the change, at any hour, whatever the automatic loop is doing.

Three things say "something changed" at no cost, and this module watches them:
- the keychain files in <home>/Library/Keychains: every save replaces the file (a new inode and mtime, macOS 26.5),
  and a kqueue event on its folder comes before the saving `security` process has even exited;
- Claude Code's plaintext login file: Claude Code writes it just before it deletes its Keychain item, so its
  appearing is the earliest sign that the login is about to be lost, and its disappearing the second half of it;
- Claude Code's settings file: a switch, a sign-in, a sign-out.

The watch never reads those files' contents and never runs a program to notice. It never decides and never writes a
login: it calls one function of the engine (the look) and paces the next look by the answer. A second function of the
engine (the hold, api.watch_hold) is told at once, in the watch's own loop, when the login file appears, changes or
goes and when the settings change: what it does with that is the engine's (putback.hold_seen).

- Changes: kqueue on the three folders and on the watched files themselves; an event only says "compare the stamps"
  (inode, mtime in ns, size), and a change counts only when a stamp really moved. The stamps are compared every
  SWEEP_S anyway, so a missed event costs seconds, never a sign-in. Without kqueue it compares them every POLL_S
  and tries kqueue again every KQUEUE_RETRY_S.
- Watch: when to look. A short settle joins a burst of events into one look; changes during a look are joined into
  one look after it; a login file that appeared is looked at with no settle. An answer can ask for a faster pace for
  a while. With no change at all, one look every TICK_S and one when the Mac wakes. Never two looks at once, never
  more than LOOKS_PER_MIN in any minute, and a look that raises never ends the watch; one that hangs past
  LOOK_GIVE_UP_S ends it cleanly, and launchd starts a fresh one.
- PageCheck: the watch brings a stopped page back through launchd, and never harms a running one.

Files in the data folder: signin-watch.lease (one watch per data folder), signin-watch.json (the heartbeat, every
BEAT_S), signin-record.jsonl (every change seen and every look that did something: numbers and fixed words only, so it
can be kept long after Claude Code's own logs are gone) and signin-watch.log (plain lines).
"""
import argparse
import collections
import json
import math
import os
import re
import select
import signal
import socket
import subprocess
import sys
import threading
import time

from . import autoloop, context, facts, usage

LEASE = 'signin-watch.lease'
HEARTBEAT = 'signin-watch.json'
RECORD = 'signin-record.jsonl'
LOG = 'signin-watch.log'
# The page's launchd job: the same label as AGENT in switchboard/__main__.py (a test holds the two equal). Not
# imported from there: that module is the whole command line, and this process stays small.
PAGE_AGENT = 'io.github.amyjainberkeley.switchboard'

SETTLE_S = 0.15          # one save is a burst of events (the folder twice, the old file once): one look for the burst
MIN_GAP_S = 1.0          # the quiet pace: looks start at least this far apart (one look is about a third of a second
                         # of `security` on a busy Mac)
FAST_GAP_S = 0.2         # the fastest pace an answer can ask for, and the least gap before a look at a login file
                         # that appeared (in pile-ups of stand-ins that save in Claude Code 2.1.329's order, that
                         # file was removed 11 to 673 ms after it was written, and the Keychain item deleted 4 to
                         # 343 ms after the removal)
WATCH_NEXT_S = 1.0       # an answer that says `watch` without a `next_s` is looked at again after this
FAST_FOR_S = 120.0       # answers that keep asking for the fast pace get it this long in a row, then the quiet pace
TICK_S = 300.0           # with no change at all, one look this often: for a loss no event and no stamp showed
WAKE_GAP_S = 60.0        # the wall clock moved this much more than awake time: the Mac slept, or its clock was set
SWEEP_S = 5.0            # the stamps are compared this often even while events work: a missed event costs this much
POLL_S = 1.0             # without kqueue, the stamps are compared this often
KQUEUE_RETRY_S = 60.0    # without kqueue, it is tried again this often
LOOKS_PER_MIN = 40       # never more looks than this in any 60 s: what keeps a bug of ours from hammering the Keychain
CAP_GAP_S = 3.0          # once half the cap has started in the last 60 s, looks start at least this far apart. Half
                         # the cap, then one every 3 s, can never pass the cap in any 60 s (20 + 60 / 3 = 40), and a
                         # change still waits at most 3 s. On a busy Mac (keychain.busy_mac) a look for a change of the
                         # Keychain or the settings alone keeps this pace from the first: each look is one more
                         # `security` call beside Claude Code's own, whose reads of its item run out after 2 s
LOOK_DEADLINE_S = 45.0   # a look running longer is said in the log and the heartbeat; no new look starts meanwhile
LOOK_GIVE_UP_S = 300.0   # a look still running after this stops the watch cleanly (exit 0, its last beat says it
                         # stopped): launchd starts a fresh one, where a watch left waiting would beat but never look
BEAT_S = 10.0            # the heartbeat is written this often, and after each look
MAX_WAIT_S = 1.0         # the loop wakes at least this often (a stop is seen within it), and no more often than this
                         # while nothing happens
ERROR_LOG_EVERY_S = 600.0  # one failure of the look is logged once per this long
STOP_WAIT_S = 10.0       # at a stop, a look under way gets this long to finish (it may be writing a login); launchd
                         # waits 20 s before it kills a job
BUSY_WAIT_S = 15.0       # a second copy, beside the watch that holds the lease, waits this long before it exits
RECORD_MAX = 1024 * 1024
RECORD_KEEP = 3
RECORD_CHANGES_PER_MIN = 60  # change lines in any minute; past it they are counted into the next line
HOLD_PUTS_PER_MIN = 60   # files the hold may put back in any 60 s. Past it, a login file that goes is not handed to
                         # the hold until the minute has room: it keeps a bug of ours from writing files in a loop.
                         # Every other change is always handed on: none of them makes a file, a change of the settings
                         # is what ends a hold when Claude Code signs out, and Claude Code rewrites its settings file
                         # dozens of times a minute while chats work
HOLD_SLOW_S = 0.25       # a call of the hold that takes longer is said (it should take milliseconds: files only)

PAGE_CHECK_S = 30.0      # the page is checked this often
PAGE_CONNECT_S = 2.0     # a connection to its port gets this long
PAGE_FAILS = 3           # this many failed checks in a row,
PAGE_FAIL_SPAN_S = 90.0  # at least this far apart from the first to the last, before launchd is asked to start it
PAGE_MIN_AGE_S = 120.0   # a watch younger than this never starts the page: they may be starting together
PAGE_TRIES = 3           # at most this many tries
PAGE_TRIES_WINDOW_S = 1800.0  # in this long

EXIT_NOTHING, EXIT_ERROR, EXIT_DID, EXIT_BUSY = 0, 1, 4, 5
EXIT_AGAIN = 3           # --tidy: the plain login file and its note were left because the Keychain gave no answer

# kqueue events on a folder or a watched file. A folder's write event comes when an entry is added, removed or
# renamed; a file's write or extend event when it is written in place; delete, rename and revoke mean the folder or
# file is not the one opened any more, so it is opened again.
VNODE_EVENTS = 0
for _name in ('KQ_NOTE_WRITE', 'KQ_NOTE_EXTEND', 'KQ_NOTE_ATTRIB', 'KQ_NOTE_LINK', 'KQ_NOTE_RENAME',
              'KQ_NOTE_DELETE', 'KQ_NOTE_REVOKE'):
    VNODE_EVENTS |= getattr(select, _name, 0)
# Opened for events only (macOS O_EVTONLY; Python 3.9 has no name for it): the folder can still be unmounted.
O_EVTONLY = getattr(os, 'O_EVTONLY', 0x8000 if sys.platform == 'darwin' else 0)

NOTHING = frozenset(('nothing', 'none', 'ok', 'stay', 'leave', ''))
# The only strings the record keeps (and the heartbeat's last action). Anything else becomes 'other' in `action`
# and is dropped elsewhere, so no email, token, path or name can reach those files.
WORDS = frozenset((
    'change', 'look', 'page', 'cap', 'start', 'stop', 'error', 'other',
    'events', 'polling', 'appeared', 'gone', 'changed', 'keychain', 'login_file', 'settings', 'tick', 'wake',
    'nothing', 'none', 'ok', 'stay', 'leave', 'watch', 'wait', 'waiting', 'busy', 'skipped', 'held', 'failed',
    'refused',
    'put_back', 'putback', 'restored', 'restore', 'recreated', 'imported', 'moved', 'move', 'switched', 'kept',
    'removed', 'cleaned', 'present', 'absent', 'empty', 'wiped', 'unreadable', 'unknown', 'locked', 'timeout',
    'dialog', 'denied', 'signed_in', 'signed_out', 'no_login', 'fallback', 'plaintext', 'file', 'item',
    'kickstart', 'bootstrap', 'up', 'down', 'true', 'false',
    # the engine's look (api.watch_look): the actions it answers and the words its `seen` holds that are not above
    'promote', 'limit', 'oauth', 'api_key', 'unparseable', 'sealed', 'split', 'cache', 'missing', 'incomplete',
    # the hold (api.watch_hold): its record lines and the reasons it takes a file of its own away
    'hold', 'stays', 'nobody', 'another', 'looked', 'spare', 'followed', 'mirrored', 'adopted',
))
_KEY = re.compile(r'[a-z][a-z_]{0,31}')
_FINGERPRINT = re.compile(r'[0-9a-f]{16}')
_DROP = object()


# ---------- hints: what changed ----------

def no_hint():
    """The hint with nothing in it (a look the pace asked for). login_file_written: the login file was written (it
    appeared or changed) since the last look, whatever happened to it after."""
    return {'keychain': False, 'login_file': None, 'settings': False, 'tick': False, 'login_file_written': False}


_TELLING = {None: 0, 'changed': 1, 'appeared': 2, 'gone': 2}


def merge(a, b):
    """Two hints as one: each flag or-ed; for the login file 'appeared' and 'gone' beat 'changed', and of two that
    tell as much the later one wins (it is how the file is now). A write of the file is kept through a later 'gone'
    (a hint from a caller without login_file_written counts 'appeared' and 'changed' as a write)."""
    def _written(hint):
        return bool(hint.get('login_file_written') or hint.get('login_file') in ('appeared', 'changed'))
    if a is None:
        out = dict(no_hint(), **(b or {}))
        out['login_file_written'] = _written(out)
        return out
    if b is None:
        return dict(a)
    la, lb = a.get('login_file'), b.get('login_file')
    return {'keychain': bool(a.get('keychain') or b.get('keychain')),
            'login_file': la if _TELLING.get(la, 0) > _TELLING.get(lb, 0) else lb,
            'settings': bool(a.get('settings') or b.get('settings')),
            'tick': bool(a.get('tick') or b.get('tick')),
            'login_file_written': _written(a) or _written(b)}


def clean(value, depth=0):
    """What the record may keep of a value: numbers, booleans, None, words from WORDS, 16-hex fingerprints, and dicts
    of those (two levels, keys of lowercase letters and _). Anything else is dropped (_DROP)."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 3) if math.isfinite(value) else None
    if isinstance(value, str):
        return value if value in WORDS or _FINGERPRINT.fullmatch(value) else _DROP
    if isinstance(value, dict) and depth < 2:
        out = {}
        for k, v in value.items():
            if isinstance(k, str) and _KEY.fullmatch(k):
                c = clean(v, depth + 1)
                if c is not _DROP:
                    out[k] = c
        return out
    return _DROP


def word(action):
    """An answer's action as the record keeps it."""
    if action is None:
        return 'nothing'
    return action if isinstance(action, str) and action in WORDS else 'other'


# ---------- Changes: what changed, cheaply ----------

def _stamp(path):
    """(inode, mtime in ns, size) of a file, or None when it is not there. One stat; the file is never opened."""
    try:
        s = os.stat(path)
    except (OSError, ValueError):
        return None
    return (s.st_ino, s.st_mtime_ns, s.st_size)


class Changes(object):
    """Watches the keychain folder, the login file's folder and the settings file's folder, and says what changed.

    wait(timeout) blocks up to `timeout` seconds and returns a hint dict when a stamp moved, else None. nudge() ends a
    wait early (a look that finished, a stop). sweep() compares the stamps now. view() gives the stamps as plain
    numbers for the record. `kqueue` is select.kqueue unless a test hands another (or None: no kqueue)."""

    _DEFAULT = object()

    def __init__(self, keychain_dir, login_file, settings_file, clock=None, log=None, kqueue=_DEFAULT):
        self.keychain_dir = keychain_dir
        self.login_file = login_file
        self.settings_file = settings_file
        self.clock = clock or usage.Clock()
        self.log = log or (lambda text: None)
        self._kqueue = getattr(select, 'kqueue', None) if kqueue is Changes._DEFAULT else kqueue
        self.kq = None
        self.mode = 'polling'
        self.watched = {}          # path -> (fd, inode) of each folder and file registered with kqueue
        self.said_polling = False
        self.r, self.w = os.pipe()
        for fd in (self.r, self.w):
            os.set_blocking(fd, False)
        self.stamps = self._stamps()
        now = self.clock.mono()
        self.next_sweep = now + SWEEP_S
        self.next_poll = now + POLL_S
        self.kq_retry_at = None
        self._start_kqueue()

    # the stamps

    def folders(self):
        out = []
        for p in (self.keychain_dir, os.path.dirname(self.login_file), os.path.dirname(self.settings_file)):
            if p not in out:
                out.append(p)
        return out

    def _stamps(self):
        out = {}
        try:
            names = os.listdir(self.keychain_dir)
        except FileNotFoundError:
            names = []                                      # no folder, so no keychain in it to miss
            self._listed(None)
        except (OSError, ValueError) as e:
            names = []
            self._listed(type(e).__name__)
        else:
            self._listed(None)
        for name in names:
            if name.endswith('.keychain-db'):
                s = _stamp(os.path.join(self.keychain_dir, name))
                if s is not None:
                    out['kc:' + name] = s
        out['login'] = _stamp(self.login_file)
        out['settings'] = _stamp(self.settings_file)
        return out

    def _listed(self, why):
        """`unreadable`: why the keychain folder could not be listed (an error's name), or None. A folder that can't
        be listed hides every save in it, so that is said once, and once more when it can be listed again."""
        was = getattr(self, 'unreadable', None)
        self.unreadable = why
        if why and not was:
            self.log('the sign-in watch: the Keychain folder cannot be read (%s); a save there is not seen until it can'
                     % why)
        elif was and not why:
            self.log('the sign-in watch: the Keychain folder can be read again')

    def _compare(self):
        old, new = self.stamps, self._stamps()
        self.stamps = new
        if self.mode == 'events':
            self._rewatch()
        keychain = any(old.get(k) != new.get(k) for k in set(old) | set(new) if k.startswith('kc:'))
        o, n = old.get('login'), new.get('login')
        login = 'appeared' if o is None and n is not None else 'gone' if o is not None and n is None else \
            'changed' if o != n else None
        settings = old.get('settings') != new.get('settings')
        if not (keychain or login or settings):
            return None
        return {'keychain': keychain, 'login_file': login, 'settings': settings, 'tick': False,
                'login_file_written': login in ('appeared', 'changed')}

    def sweep(self):
        """Compare the stamps now (after a wake). A hint, or None."""
        return self._compare()

    def view(self, fresh=False):
        """The stamps as plain numbers: the keychain file's size and mtime (login.keychain-db, else the newest), the
        login file's presence, size and mtime, the settings file's mtime. fresh: stat now (the stamps the next
        compare starts from are not touched)."""
        s = self._stamps() if fresh else self.stamps
        kc = s.get('kc:login.keychain-db')
        if kc is None:
            others = [v for k, v in s.items() if k.startswith('kc:')]
            kc = max(others, key=lambda v: v[1]) if others else None
        login, settings = s.get('login'), s.get('settings')
        return {'kc_size': kc[2] if kc else None, 'kc_mtime': round(kc[1] / 1e9, 3) if kc else None,
                'login': login is not None, 'login_size': login[2] if login else None,
                'login_mtime': round(login[1] / 1e9, 3) if login else None,
                'settings_mtime': round(settings[1] / 1e9, 3) if settings else None}

    # kqueue

    def _start_kqueue(self):
        if self._kqueue is None:
            self._fall_back('kqueue is not available here')
            return
        try:
            kq = self._kqueue()
            kq.control([select.kevent(self.r, filter=select.KQ_FILTER_READ, flags=select.KQ_EV_ADD)], 0, 0)
        except (OSError, ValueError, TypeError, AttributeError) as e:
            self._fall_back('kqueue failed (%s)' % type(e).__name__)
            return
        self.kq = kq
        self.mode = 'events'
        self.kq_retry_at = None
        if self.said_polling:
            self.log('the sign-in watch: kqueue works again; back to events')
        self.said_polling = False
        self._rewatch()

    def _fall_back(self, why):
        self._close_kqueue()
        self.mode = 'polling'
        self.kq_retry_at = self.clock.mono() + KQUEUE_RETRY_S
        if not self.said_polling:
            self.log('the sign-in watch: %s; comparing the files every %g s, and trying kqueue again every %d s'
                     % (why, POLL_S, KQUEUE_RETRY_S))
            self.said_polling = True

    def _close_kqueue(self):
        for fd, _ in self.watched.values():
            try:
                os.close(fd)
            except OSError:
                pass
        self.watched = {}
        if self.kq is not None:
            try:
                self.kq.close()
            except OSError:
                pass
        self.kq = None

    def _rewatch(self):
        """Register each folder and each watched file that is there, again when it is not the one opened before (it
        was replaced, or did not exist yet). One stat each; nothing is read."""
        want = {}
        for p in self.folders():
            want[p] = True
        for k in self.stamps:
            if k.startswith('kc:'):
                want[os.path.join(self.keychain_dir, k[3:])] = True
        want[self.login_file] = True
        want[self.settings_file] = True
        for path in list(self.watched):
            if path not in want:
                self._unwatch(path)
        for path in want:
            try:
                ino = os.stat(path).st_ino
            except (OSError, ValueError):
                self._unwatch(path)
                continue
            have = self.watched.get(path)
            if have is not None and have[1] == ino:
                continue
            self._unwatch(path)
            try:
                fd = os.open(path, os.O_RDONLY | O_EVTONLY)
            except (OSError, ValueError):
                continue
            try:
                self.kq.control([select.kevent(fd, filter=select.KQ_FILTER_VNODE,
                                               flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                                               fflags=VNODE_EVENTS)], 0, 0)
            except (OSError, ValueError, TypeError, AttributeError) as e:
                os.close(fd)
                self._fall_back('kqueue failed (%s)' % type(e).__name__)
                return
            self.watched[path] = (fd, ino)

    def _unwatch(self, path):
        have = self.watched.pop(path, None)
        if have is not None:
            try:
                os.close(have[0])            # closing the descriptor removes its kqueue registration
            except OSError:
                pass

    # waiting

    def nudge(self):
        try:
            os.write(self.w, b'.')
        except OSError:
            pass

    def _drain(self):
        try:
            while os.read(self.r, 512):
                pass
        except OSError:
            pass

    def wait(self, timeout):
        timeout = max(0.0, timeout)
        now = self.clock.mono()
        if self.mode == 'events':
            fired = False
            try:
                for ev in self.kq.control(None, 64, min(timeout, max(0.0, self.next_sweep - now))):
                    if ev.ident != self.r:
                        fired = True
            except (OSError, ValueError, AttributeError) as e:
                self._fall_back('kqueue failed (%s)' % type(e).__name__)
            self._drain()
            now = self.clock.mono()
            if now >= self.next_sweep:
                self.next_sweep = now + SWEEP_S
                fired = True
            return self._compare() if fired else None
        try:
            select.select([self.r], [], [], min(timeout, max(0.0, self.next_poll - now)))
        except (OSError, ValueError):
            time.sleep(min(timeout, POLL_S))
        self._drain()
        now = self.clock.mono()
        if self.kq_retry_at is not None and now >= self.kq_retry_at:
            self._start_kqueue()
        if now >= self.next_poll:
            self.next_poll = now + POLL_S
            return self._compare()
        return None

    def close(self):
        self._close_kqueue()
        for fd in (self.r, self.w):
            try:
                os.close(fd)
            except OSError:
                pass


# ---------- the record and the jobs ----------

class Record(object):
    """<data>/signin-record.jsonl: one JSON line per change seen and per look that did something, owner-only from the
    first byte; past RECORD_MAX it is moved aside, and RECORD_KEEP older files are kept. Every line goes through
    clean(). `sink` (tests) takes each line instead of the file."""

    def __init__(self, path=None, clock=None, sink=None, max_bytes=RECORD_MAX, keep=RECORD_KEEP):
        self.path = path
        self.clock = clock or usage.Clock()
        self.sink = sink
        self.max_bytes = max_bytes
        self.keep = keep
        self.failed = False

    def add(self, kind, **fields):
        rec = clean(dict(fields, kind=kind, t=round(self.clock.wall(), 3)))
        line = json.dumps(rec, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n'
        if self.sink is not None:
            self.sink(line)
            return
        p = self.path
        try:
            os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
            if os.path.exists(p) and os.path.getsize(p) > self.max_bytes:
                for i in range(self.keep - 1, 0, -1):
                    if os.path.exists('%s.%d' % (p, i)):
                        os.replace('%s.%d' % (p, i), '%s.%d' % (p, i + 1))
                os.replace(p, p + '.1')
            fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line.encode('utf-8'))
            finally:
                os.close(fd)
        except OSError:
            self.failed = True


class _Job(object):
    """fn() in a daemon thread. done() once it returned or raised; outcome() is (value, error)."""

    def __init__(self, fn, on_done=None, name='switchboard-signin-watch'):
        self.fn = fn
        self.on_done = on_done
        self.box = {}
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)

    def _run(self):
        try:
            self.box['value'] = self.fn()
        except BaseException as e:  # noqa: BLE001 - handed to the watch as the look's failure
            self.box['error'] = e
        finally:
            self.box['done'] = True
            if self.on_done is not None:
                self.on_done()

    def start(self):
        self.thread.start()
        return self

    def done(self):
        return self.box.get('done') is True

    def outcome(self):
        return self.box.get('value'), self.box.get('error')


def _answer(value):
    """(action, watch, next_s, seen) from a look's answer: any object (or dict) with `action` and `watch`, optionally
    `next_s` and `seen`, or a bare word. None for None ("nothing to do")."""
    if value is None:
        return None
    if isinstance(value, str):
        return value, False, None, None              # a bare word is the action
    if isinstance(value, dict):
        get = value.get
    else:
        def get(k, default=None):
            return getattr(value, k, default)
    action = get('action', None)
    nxt = get('next_s', None)
    if isinstance(nxt, bool) or not isinstance(nxt, (int, float)) or not math.isfinite(nxt) or nxt < 0:
        nxt = None
    seen = get('seen', None)
    return action, get('watch', False) is True, nxt, seen if isinstance(seen, dict) else None


def did_something(answer):
    if answer is None or answer[0] is None:
        return False
    return not (isinstance(answer[0], str) and answer[0] in NOTHING)


# ---------- the page ----------

def launchctl(*args):
    """launchctl's exit status (127 when it could not run). The one way this module reaches launchd."""
    try:
        return subprocess.run(['/bin/launchctl'] + list(args), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=20).returncode
    except (OSError, subprocess.SubprocessError):
        return 127


def page_answers(port, timeout=PAGE_CONNECT_S):
    """A TCP connection to the page's port on 127.0.0.1 opens within `timeout`. Nothing is sent."""
    try:
        s = socket.create_connection(('127.0.0.1', int(port)), timeout)
    except (OSError, ValueError):
        return False
    try:
        s.close()
    except OSError:
        pass
    return True


class PageCheck(object):
    """Brings a stopped page back, and never harms a running one.

    check() (every PAGE_CHECK_S): does the page's port answer? After PAGE_FAILS failures in a row, at least
    PAGE_FAIL_SPAN_S from the first to the last, and only while the page's job file is there (it is not when the page
    is not installed or is being removed), no switch is part-way (`journal()` is false) and the watch is older than
    PAGE_MIN_AGE_S: `launchctl kickstart gui/<uid>/<label>`, which starts a job that is not running and does nothing
    to one that is; when that fails (113: the job is not loaded), `launchctl bootstrap gui/<uid> <plist>`, which does
    nothing to a job already loaded. At most PAGE_TRIES tries in PAGE_TRIES_WINDOW_S. Never `-k`, never bootout, never
    a kill."""

    def __init__(self, port, plist, label=PAGE_AGENT, launchctl=launchctl, journal=None, connect=page_answers,
                 exists=os.path.exists, clock=None, log=None, record=None, started_m=None, uid=None):
        self.port = port
        self.plist = plist
        self.label = label
        self.launchctl = launchctl
        self.journal = journal or (lambda: None)
        self.connect = connect
        self.exists = exists
        self.clock = clock or usage.Clock()
        self.log = log or (lambda text: None)
        self.record = record
        self.started_m = self.clock.mono() if started_m is None else started_m
        self.uid = os.getuid() if uid is None else uid
        self.fails = []
        self.tries = []
        self.said_limit = False

    def check(self):
        """One check. Returns 'absent', 'up', 'down', 'held' or the last launchctl verb it ran."""
        now = self.clock.mono()
        if not self.exists(self.plist):
            self.fails = []
            return 'absent'
        if self.connect(self.port, PAGE_CONNECT_S):
            self.fails = []
            self.said_limit = False
            return 'up'
        self.fails.append(now)
        if len(self.fails) < PAGE_FAILS or self.fails[-1] - self.fails[0] < PAGE_FAIL_SPAN_S:
            return 'down'
        if now - self.started_m < PAGE_MIN_AGE_S:
            return 'down'
        self.tries = [t for t in self.tries if 0 <= now - t < PAGE_TRIES_WINDOW_S]
        if len(self.tries) >= PAGE_TRIES:
            if not self.said_limit:
                self.log('the sign-in watch: the page still does not answer on port %s; %d tries in %d minutes were '
                         'made, so none more for now' % (self.port, PAGE_TRIES, PAGE_TRIES_WINDOW_S // 60))
                self.said_limit = True
            return 'held'
        try:
            part_way = self.journal()
        except Exception:  # noqa: BLE001 - when it cannot be told, a switch may be part-way: hold back
            part_way = True
        if part_way:
            return 'held'
        self.tries.append(now)
        self.fails = []
        target = 'gui/%d/%s' % (self.uid, self.label)
        verb, rc = 'kickstart', self.launchctl('kickstart', target)
        words = 'kickstart %d' % rc
        if rc != 0:
            verb, rc = 'bootstrap', self.launchctl('bootstrap', 'gui/%d' % self.uid, self.plist)
            words += ', bootstrap %d' % rc
        self.log('the sign-in watch: the page did not answer on port %s for %d s; asked launchd to start it (%s)'
                 % (self.port, PAGE_FAIL_SPAN_S, words))
        if self.record is not None:
            self.record.add('page', action=verb, rc=rc, ok=rc == 0)
        return verb


# ---------- Watch: when to look ----------

class Watch(object):
    """The watch's loop. Everything outside it comes through its arguments, so tests drive it on a fake clock:
    `changes` (a Changes or anything with wait, sweep, view, nudge and mode), `look` (hint -> answer), `clock`,
    `log` (text ->), `record` (a Record), `beat_path` and `write` (the heartbeat), `page` (a PageCheck or None) and
    `spawn` and `page_spawn` (fn -> a job with done() and outcome(); a daemon thread by default; only a look's end
    wakes the loop at once). `hold` (event -> a dict of plain facts, or None: no hold) is called in the loop itself,
    at once, for each change of the login file ('appeared', 'changed', 'gone') and of the settings ('settings'), before
    the look is scheduled, and once at the start ('start'). An answer with `look_in` (seconds) has one look made that
    long after it, whatever else is pending (the later time, when two answers ask). `busy` (-> bool, or None: never)
    says the Mac is busy (keychain.busy_mac): a look for a change of the Keychain item or of the settings alone is then
    made CAP_GAP_S after the last, not MIN_GAP_S; a login file that appeared, changed or went, a first look, a wake,
    an answer that asks for the fast pace and a look the hold asks for keep their pace."""

    def __init__(self, changes, look, clock=None, log=None, record=None, beat_path=None, write=None, page=None,
                 spawn=None, page_spawn=None, pid=None, problem=None, hold=None, busy=None):
        self.changes = changes
        self.look = look
        self.hold = hold
        self.busy = busy
        self.hold_puts = collections.deque()       # when the hold put a file back in the last 60 s
        self.hold_look_m = None                    # a look the hold asked for is owed at this time (its `look_in`)
        self.hold_look_job = False                 # the look under way is that one
        self.hold_capped = False
        self.hold_slow_said = False
        self.clock = clock or usage.Clock()
        self.log = log or (lambda text: None)
        self.record = record or Record(sink=lambda line: None)
        self.beat_path = beat_path
        self.write = write or usage.write_atomic
        self.page = page
        self.spawn = spawn or (lambda fn: _Job(fn, on_done=self.changes.nudge).start())
        self.page_spawn = page_spawn or (lambda fn: _Job(fn, name='switchboard-signin-watch-page').start())
        self.stop = threading.Event()
        now = self.clock.mono()
        self.started_m = now
        self.prev = (self.clock.wall(), now)
        self.pending = None           # the hint of changes not looked at yet
        self.pending_m = None         # when the first of them came
        self.urgent = False           # a login file appeared among them: no settle
        self.immediate = False        # the first look, or a wake: no settle
        self.job = None
        self.job_hint = None
        self.job_m = None
        self.job_overdue = False
        self.before = None
        self.last_start_m = None
        self.starts = collections.deque()
        self.fast = None              # {'since', 'next'}: an answer asked for the fast pace
        self.fast_spent = False
        self.capped = None            # when the cap last held a look back
        self.page_job = None
        self.next_page_m = now + PAGE_CHECK_S
        self.next_beat_m = now
        self.errors = {}
        self.change_lines = collections.deque()
        self.skipped = 0
        self.problems = {}
        if problem:
            self.problems['look'] = problem
        self.hb = {'v': 1, 'pid': pid or os.getpid(), 'boot': self.clock.boot(),
                   'started_at': round(self.clock.wall(), 3), 'mode': self.changes.mode, 'looks': 0,
                   'last_look_at': None, 'last_change_at': None, 'last_action': None, 'last_action_at': None,
                   'problem': problem}

    # wanting a look

    def _want(self, hint, now, how='change'):
        self.pending = merge(self.pending, hint)
        if self.pending_m is None:
            self.pending_m = now
        if how == 'urgent':
            self.urgent = True
        elif how == 'now':
            self.immediate = True

    def _trim(self, now):
        while self.starts and not 0 <= now - self.starts[0] < 60.0:
            self.starts.popleft()

    def _due(self, now):
        """(when the next look may start, when it would start without the cap), or (None, None) while one runs."""
        if self.job is not None:
            return None, None
        last = self.last_start_m
        cands = [(last if last is not None else self.started_m) + TICK_S]
        if self.pending is not None:
            if self.urgent:
                cands.append(max(self.pending_m, last + FAST_GAP_S if last is not None else 0.0))
            elif self.immediate:
                cands.append(max(self.pending_m, last + MIN_GAP_S if last is not None else 0.0))
            else:
                gap = CAP_GAP_S if not self.pending.get('login_file') and self._busy() else MIN_GAP_S
                cands.append(max(self.pending_m + SETTLE_S, last + gap if last is not None else 0.0))
        if self.fast is not None and self.fast.get('next') is not None:
            cands.append(self.fast['next'])
        if self.hold_look_m is not None:
            cands.append(max(self.hold_look_m, last + FAST_GAP_S if last is not None else 0.0))
        free = min(cands)
        due = free
        self._trim(now)
        if len(self.starts) >= LOOKS_PER_MIN // 2 and last is not None:
            due = max(due, last + CAP_GAP_S)
        if len(self.starts) >= LOOKS_PER_MIN:
            due = max(due, self.starts[0] + 60.0)
        return due, free

    def _busy(self):
        if self.busy is None:
            return False
        try:
            return bool(self.busy())
        except Exception:  # noqa: BLE001 - a load that can't be read is no reason to wait
            return False

    def _maybe_look(self, now):
        due, free = self._due(now)
        if due is None:
            return
        if self.capped is not None and not 0 <= now - self.capped < 60.0:
            self.capped = None                       # a minute with no look held back: the episode is over
            self.problems.pop('cap', None)
        if now < due:
            if now >= free:
                if self.capped is None:
                    self.problems['cap'] = 'many changes: one look every %g s for now; nothing to do' % CAP_GAP_S
                    self.log('the sign-in watch: %d looks in the last minute; one look every %g s until the minute has '
                             'room' % (len(self.starts), CAP_GAP_S))
                    self.record.add('cap', looks=len(self.starts))
                self.capped = now
            return
        self._start(now)

    def _start(self, now):
        if self.pending is not None:
            hint = self.pending
        elif self.fast is not None and self.fast.get('next') is not None and now >= self.fast['next']:
            hint = no_hint()
        else:
            hint = dict(no_hint(), tick=True)
        self.pending, self.pending_m, self.urgent, self.immediate = None, None, False, False
        if self.fast is not None:
            self.fast['next'] = None
        self.hold_look_job = self.hold_look_m is not None and now >= self.hold_look_m
        if self.hold_look_job:
            self.hold_look_m = None
        self.last_start_m = now
        self.starts.append(now)
        self.job_hint, self.job_m, self.job_overdue = hint, now, False
        self.before = self.changes.view()
        look, given = self.look, dict(hint)
        self.job = self.spawn(lambda: look(given))

    # a look that ended

    def _reap(self, now):
        if self.job is None:
            return
        if not self.job.done():
            if not self.job_overdue and now - self.job_m >= LOOK_DEADLINE_S:
                self.job_overdue = True
                self.problems['deadline'] = ('a look has run for more than %d s; if it runs for %d s, the watch starts '
                                             'again' % (LOOK_DEADLINE_S, LOOK_GIVE_UP_S))
                self.log('the sign-in watch: a look has run for more than %d s; no new look starts until it ends'
                         % LOOK_DEADLINE_S)
                self._beat(now)
            if now - self.job_m >= LOOK_GIVE_UP_S and not self.stop.is_set():
                self.log('the sign-in watch: a look has run for more than %d s; stopping, so that launchd starts a '
                         'fresh watch' % LOOK_GIVE_UP_S)
                self.stop.set()
                self.changes.nudge()
            return
        value, error = self.job.outcome()
        self.job = None
        took = round(now - self.job_m, 3)
        self.problems.pop('deadline', None)
        self.hb['looks'] += 1
        self.hb['last_look_at'] = round(self.clock.wall(), 3)
        if error is not None:
            key = type(error).__name__
            last = self.errors.get(key)
            if last is None or not 0 <= now - last < ERROR_LOG_EVERY_S:
                self.errors[key] = now
                self.log('the sign-in watch: a look failed (%s)' % key)
                self.record.add('look', hint=self.job_hint, action='error', took_s=took)
            if self.hold_look_job and self.hold_look_m is None:
                self.hold_look_m = now + WATCH_NEXT_S      # the look the hold asked for failed: it is still owed
            self._pace(None, now)
            self._beat(now)
            return
        # a look can leave a plain login file for a later look to take away (after it put that file's login back in
        # the Keychain), and so can a look of another process: the hold says when that later look is due
        self._call_hold('looked', now)
        answer = _answer(value)
        self._pace(answer, now)
        if did_something(answer):
            action = word(answer[0])
            self.hb['last_action'] = action
            self.hb['last_action_at'] = round(self.clock.wall(), 3)
            self.log('the sign-in watch: look: %s (%.1f s)' % (action, took))
            fields = dict(hint=self.job_hint, action=action, took_s=took, before=self.before,
                          after=self.changes.view(fresh=True))
            if answer[3] is not None:
                fields['seen'] = answer[3]
            self.record.add('look', **fields)
        self._beat(now)

    def _pace(self, answer, now):
        """The fast pace: an answer with `watch` or `next_s` has the next look after next_s (never under FAST_GAP_S),
        change or no change, for at most FAST_FOR_S in a row; an answer without either ends it."""
        asks = answer is not None and (answer[1] or answer[2] is not None)
        if not asks:
            self.fast, self.fast_spent = None, False
            return
        if self.fast_spent:
            return
        if self.fast is None:
            self.fast = {'since': self.job_m, 'next': None}
        if now - self.fast['since'] >= FAST_FOR_S:
            self.fast, self.fast_spent = None, True
            self.log('the sign-in watch: answers asked for the fast pace for %d s; back to the quiet pace' % FAST_FOR_S)
            return
        gap = answer[2] if answer[2] is not None else WATCH_NEXT_S
        self.fast['next'] = now + max(FAST_GAP_S, min(gap, TICK_S))

    # changes, the clock, the page, the heartbeat

    def _call_hold(self, event, now):
        """One call of the hold, in the loop. A login file that went ('gone') is the one call that can make a file:
        once the hold has put HOLD_PUTS_PER_MIN files back in the last 60 s it is not made until the minute has room
        (said once). Every other call is always made. One that raises is said once per error type per ten minutes,
        one slower than HOLD_SLOW_S once; one record line for a call that did something. Returns its answer (a dict),
        or None."""
        if self.hold is None:
            return None
        while self.hold_puts and not 0 <= now - self.hold_puts[0] < 60.0:
            self.hold_puts.popleft()
        if len(self.hold_puts) < HOLD_PUTS_PER_MIN:
            self.hold_capped = False
        elif event == 'gone':
            if not self.hold_capped:
                self.hold_capped = True
                self.log("the sign-in watch: Claude Code's plain login file was put back %d times in the last minute; "
                         'no more until the minute has room' % len(self.hold_puts))
                self.record.add('hold', event=event, did='cap', made=len(self.hold_puts))
            return None
        try:
            out = self.hold(event)
        except Exception as e:  # noqa: BLE001 - the hold is a best effort; it never stops the watch or its look
            key = 'hold ' + type(e).__name__
            last = self.errors.get(key)
            if last is None or not 0 <= now - last < ERROR_LOG_EVERY_S:
                self.errors[key] = now
                self.log("the sign-in watch: its step for Claude Code's plain login file failed (%s)"
                         % type(e).__name__)
            return None
        took = self.clock.mono() - now
        if took > HOLD_SLOW_S and not self.hold_slow_said:
            self.hold_slow_said = True
            self.log("the sign-in watch: its step for Claude Code's plain login file took %.2f s" % took)
        if not isinstance(out, dict):
            return None
        if event == 'gone' and out.get('did') in ('held', 'removed'):
            self.hold_puts.append(now)                 # a file was put in place (and, for 'removed', taken away again)
        if out.get('late') is True:
            # a removal the hold could not answer from memory: the look that answers it from the Keychain item comes
            # with no settle, as one for a login file that appeared
            self.urgent = True
        nxt = out.get('look_in')
        if isinstance(nxt, (int, float)) and not isinstance(nxt, bool) and nxt >= 0:
            # a file the hold put back is taken away by a look once it is old enough: have one made then. The later
            # of two times asked for counts (a file put back again is old enough later, and an answer that only
            # kept bytes asks for a sooner look than the file that is in place needs)
            at = now + min(float(nxt), TICK_S)
            self.hold_look_m = at if self.hold_look_m is None else max(self.hold_look_m, at)
        if out.get('did'):
            self.record.add('hold', event=event, did=out.get('did'), why=out.get('why'), made=out.get('made'),
                            failed=out.get('failed'), took_s=round(took, 3))
        return out

    def _hold_change(self, hint, now):
        """Hand the changes of the login file and of the settings to the hold, at once and before the look is
        scheduled (settings first: a sign-out's change of the settings ends what a removal of the file would put back).
        A login file the hold itself put there is no change: it leaves the hint (no look for it, never taken for a
        write of Claude Code's). Returns the hint, or None when nothing is left in it to look at."""
        if self.hold is None:
            return hint
        if hint.get('settings'):
            self._call_hold('settings', now)
        lf = hint.get('login_file')
        if lf in ('appeared', 'changed', 'gone'):
            out = self._call_hold(lf, now)
            if lf != 'gone' and isinstance(out, dict) and out.get('ours') is True:
                hint = dict(hint, login_file=None, login_file_written=False)
                if not any(hint.get(k) for k in ('keychain', 'settings', 'tick')):
                    return None
        return hint

    def _on_change(self, hint, now):
        hint = self._hold_change(hint, now)
        if hint is None:
            return
        self.hb['last_change_at'] = round(self.clock.wall(), 3)
        self._want(hint, now, 'urgent' if hint.get('login_file') == 'appeared' else 'change')
        while self.change_lines and not 0 <= now - self.change_lines[0] < 60.0:
            self.change_lines.popleft()
        if len(self.change_lines) >= RECORD_CHANGES_PER_MIN:
            self.skipped += 1
            return
        self.change_lines.append(now)
        fields = dict(hint=hint, stamps=self.changes.view())
        if self.skipped:
            fields['skipped'], self.skipped = self.skipped, 0
        self.record.add('change', **fields)

    def _check_wake(self, now):
        wall = self.clock.wall()
        pw, pm = self.prev
        self.prev = (wall, now)
        if (wall - pw) - (now - pm) >= WAKE_GAP_S:
            self.log('the sign-in watch: the wall clock moved %s past awake time (a sleep or a clock change): looking '
                     'now' % usage.span_text((wall - pw) - (now - pm)))
            try:
                hint = self.changes.sweep()
            except Exception:  # noqa: BLE001 - a wake look goes ahead without the stamps
                hint = None
            hint = self._hold_change(hint, now) if hint else hint
            self._want(dict(merge(hint, None) if hint else no_hint(), tick=True), now, 'now')

    def _maybe_page(self, now):
        if self.page is None:
            return
        if self.page_job is not None and self.page_job.done():
            _, error = self.page_job.outcome()
            if error is not None:
                key = 'page ' + type(error).__name__
                last = self.errors.get(key)
                if last is None or not 0 <= now - last < ERROR_LOG_EVERY_S:
                    self.errors[key] = now
                    self.log('the sign-in watch: checking the page failed (%s)' % type(error).__name__)
            self.page_job = None
        if self.page_job is None and now >= self.next_page_m:
            self.next_page_m = now + PAGE_CHECK_S
            self.page_job = self.page_spawn(self.page.check)

    def problem(self):
        for k in ('deadline', 'cap', 'look', 'folder', 'mode'):
            if self.problems.get(k):
                return self.problems[k]
        return None

    def _beat(self, now, **extra):
        self.next_beat_m = now + BEAT_S
        if self.changes.mode == 'polling':
            self.problems['mode'] = ('the watch cannot be told of changes here, so it compares the files once a '
                                     'second; nothing to do')
        else:
            self.problems.pop('mode', None)
        if getattr(self.changes, 'unreadable', None):
            self.problems['folder'] = 'the Keychain folder cannot be read'
        else:
            self.problems.pop('folder', None)
        self.hb.update(beat_at=round(self.clock.wall(), 3), beat_mono=round(now, 3), mode=self.changes.mode,
                       problem=self.problem(), **extra)
        if self.beat_path is None:
            return
        try:
            self.write(self.beat_path, self.hb)
        except (OSError, ValueError) as e:
            key = 'beat ' + type(e).__name__
            if key not in self.errors:
                self.errors[key] = now
                self.log('the sign-in watch: could not write the heartbeat (%s)' % type(e).__name__)

    def _timeout(self, now):
        # The heartbeat and the page check wait for the next wake-up (within MAX_WAIT_S), so a quiet minute has no
        # wake-up of their own
        cands = [MAX_WAIT_S]
        due, _ = self._due(now)
        if due is not None:
            cands.append(due - now)
        return max(0.001, min(cands))

    # the loop

    def step(self):
        now = self.clock.mono()
        self._reap(now)
        self._check_wake(now)
        self._maybe_look(now)
        self._maybe_page(now)
        if now >= self.next_beat_m:
            self._beat(now)
        hint = self.changes.wait(self._timeout(now))
        if hint:
            self._on_change(hint, self.clock.mono())

    def run(self, until=None):
        """Watch until stop is set (or, for tests, until the monotonic clock reaches `until`). The first look comes at
        once: something may have changed while no watch ran."""
        now = self.clock.mono()
        self.record.add('start', mode=self.changes.mode)
        self.log('the sign-in watch: started (%s)' % self.changes.mode)
        self._call_hold('start', now)          # a file the hold put there before this watch started is looked at first
        self._want(dict(no_hint(), tick=True), now, 'now')
        self._beat(now)
        while not self.stop.is_set():
            if until is not None and self.clock.mono() >= until:
                break
            try:
                self.step()
            except Exception as e:  # noqa: BLE001 - nothing outside a look ends the watch either
                key = 'step ' + type(e).__name__
                now = self.clock.mono()
                last = self.errors.get(key)
                if last is None or not 0 <= now - last < ERROR_LOG_EVERY_S:
                    self.errors[key] = now
                    self.log('the sign-in watch: a step failed (%s)' % type(e).__name__)
                self.clock.sleep(MAX_WAIT_S)
        self._finish()

    def _finish(self):
        if self.stop.is_set() and self.job is not None and until_done(self.job, STOP_WAIT_S):
            self.log('the sign-in watch: stopping; waited for the look under way')
        if self.job is not None:
            self._reap(self.clock.mono())
        now = self.clock.mono()
        self._beat(now, stopped_at=round(self.clock.wall(), 3))
        self.record.add('stop', looks=self.hb['looks'])
        self.log('the sign-in watch: stopped')

    def once(self):
        """One look, under its deadline: EXIT_NOTHING, EXIT_ERROR or EXIT_DID. No heartbeat (it is not a running
        watch); the record gets the look when it did something."""
        hint = dict(no_hint(), tick=True)
        look = self.look
        job = self.spawn(lambda: look(dict(hint)))
        if not until_done(job, LOOK_DEADLINE_S):
            self.log('the sign-in watch: the look did not end within %d s' % LOOK_DEADLINE_S)
            return EXIT_ERROR
        value, error = job.outcome()
        if error is not None:
            self.log('the sign-in watch: the look failed (%s)' % type(error).__name__)
            self.record.add('look', hint=hint, action='error')
            return EXIT_ERROR
        answer = _answer(value)
        if not did_something(answer):
            return EXIT_NOTHING
        fields = dict(hint=hint, action=word(answer[0]), after=self.changes.view(fresh=True))
        if answer[3] is not None:
            fields['seen'] = answer[3]
        self.record.add('look', **fields)
        self.log('the sign-in watch: look: %s' % word(answer[0]))
        return EXIT_DID


def until_done(job, seconds):
    """Wait in real time for a job, at most `seconds`. True when it ended."""
    end = time.monotonic() + seconds
    while not job.done():
        if time.monotonic() >= end:
            return False
        time.sleep(0.02)
    return True


# ---------- the process ----------

def default_look(api_module=None, log=None):
    """(look, problem): the engine's look, found when the process starts. api.watch_look(hint) when the engine has
    it, else api.put_back_pass() (it takes no hint), else a look that does nothing (problem then says so)."""
    log = log or (lambda text: None)
    if api_module is None:
        from . import api as api_module
    fn = getattr(api_module, 'watch_look', None)
    if callable(fn):
        return fn, None
    fn = getattr(api_module, 'put_back_pass', None)
    if callable(fn):
        return (lambda hint: fn()), None
    log('the sign-in watch: this build of the engine has no look; changes are noticed and recorded, and nothing is put '
        'back by this process')
    return (lambda hint: None), 'this build of the engine has no look'


def default_hold(api_module=None):
    """The engine's hold, found when the process starts: api.watch_hold(event) when the engine has it, else None (no
    hold: the watch notices and looks as before)."""
    if api_module is None:
        from . import api as api_module
    fn = getattr(api_module, 'watch_hold', None)
    return fn if callable(fn) else None


def paths(ctx):
    """(keychain folder, Claude Code's plaintext login file, its settings file, the page's job file) for a context."""
    return (os.path.join(ctx.home, 'Library', 'Keychains'), facts.credentials_file(ctx.env, ctx.home),
            facts.global_config(ctx.env, ctx.home),
            os.path.join(ctx.home, 'Library', 'LaunchAgents', PAGE_AGENT + '.plist'))


def build(ctx=None, clock=None, look=None, api_module=None, launch=launchctl, port=None, hold=None):
    """The watch as the process runs it, from the engine's context (context.current(), as api.py takes it). The look
    and the hold are the engine's own (default_look, default_hold) unless given: a caller that hands its own look
    hands its own hold too (none when it gives none)."""
    ctx = ctx or context.current()
    clock = clock or usage.Clock()
    data = ctx.data_dir
    log = autoloop.AutoLog(path=os.path.join(data, LOG), clock=clock)
    record = Record(os.path.join(data, RECORD), clock)
    kc_dir, login, settings, plist = paths(ctx)
    changes = Changes(kc_dir, login, settings, clock=clock, log=log.line)
    problem = None
    if look is None:
        look, problem = default_look(api_module, log.line)
        hold = hold or default_hold(api_module)
    if port is None:
        from .. import core
        port = core.port()

    def journal():
        from . import switcher
        return switcher.read_journal(ctx)[0].kind != 'absent'      # part-way, or it cannot be told: True
    page = PageCheck(port, plist, launchctl=launch, journal=journal, clock=clock, log=log.line, record=record)
    from . import keychain
    return Watch(changes, look, clock=clock, log=log.line, record=record, beat_path=os.path.join(data, HEARTBEAT),
                 page=page, problem=problem, hold=hold, busy=keychain.busy_mac)


def status_lines(data_dir, clock=None):
    """The heartbeat in plain lines (for `--status` and doctor)."""
    clock = clock or usage.Clock()
    owner = autoloop.lease_owner(os.path.join(data_dir, LEASE))
    hb = usage.read_json(os.path.join(data_dir, HEARTBEAT)) or {}
    num = usage.num
    lines = []
    if owner['held']:
        lines.append('The sign-in watch is running%s.' % (' (process %d)' % owner['pid'] if owner['pid'] else ''))
    else:
        lines.append('The sign-in watch is not running.')
    if not hb:
        return lines
    beat = num(hb.get('beat_at'))
    if beat is not None:
        lines.append('Last beat: %s ago%s.' % (usage.span_text(max(0.0, clock.wall() - beat)),
                                                ', noticing by events' if hb.get('mode') == 'events' else
                                                ', comparing the files once a second'
                                                if hb.get('mode') == 'polling' else ''))
    looks = num(hb.get('looks'))
    last = num(hb.get('last_look_at'))
    lines.append('Looks: %d%s.' % (int(looks or 0), ', the last at %s' % usage.clock_text(last, clock.wall())
                                   if last is not None else ''))
    change = num(hb.get('last_change_at'))
    if change is not None:
        lines.append('Last change seen: %s.' % usage.clock_text(change, clock.wall()))
    act, at = hb.get('last_action'), num(hb.get('last_action_at'))
    if isinstance(act, str) and act in WORDS and at is not None:
        lines.append('Last action: %s at %s.' % (act.replace('_', ' '), usage.clock_text(at, clock.wall())))
    if isinstance(hb.get('problem'), str) and hb['problem']:
        lines.append('Problem: %s.' % hb['problem'])
    return lines


def wait_busy(say, stop=None, seconds=None):
    """A second copy, beside the watch that holds the lease, waits before it exits (EXIT_BUSY): launchd starts the
    watch's job again ThrottleInterval (10 s) after every exit (KeepAlive), so a copy that exited at once would be
    started every 10 s for as long as the other one runs. SIGTERM or SIGINT ends the wait at once (launchd kills a job
    20 s after its SIGTERM). `say` is called once the signals are handled, so a stop that comes after the line is never
    lost. True when a signal ended the wait."""
    stop = stop if stop is not None else threading.Event()

    def on_signal(*_):
        stop.set()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    say()
    return stop.wait(BUSY_WAIT_S if seconds is None else seconds)


def main(argv=None, stop=None):
    p = argparse.ArgumentParser(prog='python -m switchboard.accounts.signin_watch',
                                description="Switchboard's sign-in watch: notices when Claude Code's sign-in may "
                                            "have changed and has the engine look at once.")
    p.add_argument('--once', action='store_true', help='one look; exit 0 nothing to do, 1 an error, 4 it did '
                                                       'something, 5 another watch is running')
    p.add_argument('--status', action='store_true', help="print the running watch's heartbeat")
    p.add_argument('--tidy', action='store_true', help="remove a plain login file the watch put back for Claude Code "
                                                       "(for the uninstaller); exit 3 when the Keychain gave no "
                                                       "answer and it was left: run the uninstaller again")
    a = p.parse_args(argv)
    ctx = context.current()
    if a.status:
        for line in status_lines(ctx.data_dir):
            print(line)
        return 0
    if a.tidy:
        from . import putback
        told = {}
        for line in putback.hold_uninstall(ctx, told):
            print(line)
        return EXIT_AGAIN if told.get('again') else 0
    clock = usage.Clock()
    lease = autoloop.Lease(os.path.join(ctx.data_dir, LEASE))
    if not lease.acquire(clock):
        o = autoloop.lease_owner(os.path.join(ctx.data_dir, LEASE))
        line = 'The sign-in watch already runs%s.' % (' (process %s)' % o['pid'] if o.get('pid') else '')
        if a.once:
            print(line, file=sys.stderr)
        else:
            wait_busy(lambda: print(line, file=sys.stderr, flush=True), stop)
        return EXIT_BUSY
    watch = None
    try:
        watch = build(ctx, clock)

        def stop(*_):
            watch.stop.set()
            watch.changes.nudge()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        if a.once:
            return watch.once()
        watch.run()
        return 0
    finally:
        if watch is not None:
            watch.changes.close()
        lease.release()


if __name__ == '__main__':
    sys.exit(main())
