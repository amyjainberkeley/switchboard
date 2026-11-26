"""Paths, settings, saved state and the small helpers every part of Switchboard shares.

Nothing in here talks to the network. state.json holds emails, browser profile
names and artifact links. It never holds a login or a token.
"""
import errno
import fcntl
import json
import os
import re
import sys
import secrets
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime

VERSION = '0.2.0'
SHORT_NAME = 'sb'    # the command's short name, the same command as switchboard (the installer adds it when free)


def _in_tests():
    return 'unittest' in sys.modules or 'pytest' in sys.modules


if not os.environ.get('SWITCHBOARD_HOME') and _in_tests():
    # A test run must never touch the real data folder.
    raise RuntimeError('Tests must set SWITCHBOARD_HOME to a temporary folder before importing switchboard.')

PACKAGE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(PACKAGE, 'web')
SHIM_DIR = os.path.join(PACKAGE, 'shim')
BUILD_FILE = 'build.txt'        # in the program's folder: the commit this copy was made from (build_of)


# ---------- which build this is: "switchboard 0.2.0 (d635e47)" ----------

def _short_commit(text):
    return isinstance(text, str) and 4 <= len(text) <= 40 and all(c in '0123456789abcdef' for c in text)


def build_of(package=PACKAGE):
    """The commit a copy of the program was made from, short ('d635e47'), or None when neither way knows it. A `git
    archive` download (the one-line install from GitHub, a tester's .tar.gz) has it in build.txt, which git fills in
    as it makes the archive (.gitattributes: export-subst of $Format:%h$); install.sh writes it there too when it
    installs from a checkout. A copy run from a checkout itself asks git."""
    try:
        with open(os.path.join(package, BUILD_FILE), encoding='ascii', errors='replace') as f:
            text = f.read().strip()
    except OSError:
        text = ''
    if _short_commit(text):
        return text
    root = os.path.dirname(os.path.abspath(package))
    return _git_short(root) if os.path.exists(os.path.join(root, '.git')) else None


def _git_short(root):
    """`git rev-parse --short HEAD` in a checkout, or None. Never Apple's stand-in /usr/bin/git without the command
    line tools: it would ask to install them. A GIT_DIR of the shell's own is not used."""
    import shutil
    try:
        git = shutil.which('git')
        if not git:
            return None
        if os.path.realpath(git) == '/usr/bin/git' and \
                subprocess.run(['/usr/bin/xcode-select', '-p'], capture_output=True, timeout=5).returncode != 0:
            return None
        env = dict((k, v) for k, v in os.environ.items() if not k.startswith('GIT_'))
        r = subprocess.run([git, '-C', root, 'rev-parse', '--short', 'HEAD'], capture_output=True, timeout=5, env=env)
        out = r.stdout.decode('ascii', 'replace').strip()
    except Exception:  # noqa: BLE001 - the version line never fails for want of git
        return None
    return out if r.returncode == 0 and _short_commit(out) else None


_BUILD = {}


def build():
    """build_of this copy of the program, worked out once."""
    if 'commit' not in _BUILD:
        _BUILD['commit'] = build_of(PACKAGE)
    return _BUILD['commit']


def version_text():
    """'0.2.0 (d635e47)', or '0.2.0' when the build is not known: `switchboard version` and doctor's first line."""
    b = build()
    return '%s (%s)' % (VERSION, b) if b else VERSION

SETTLE = 90     # seconds around a switch when "which account was this" is only a guess
DEMO = os.environ.get('SWITCHBOARD_DEMO') == '1'


# ---------- where things are: read from the environment every time, never fixed at import ----------

def home():
    """The home folder ($HOME)."""
    return os.path.expanduser('~')


def data_dir():
    """Switchboard's own folder: $SWITCHBOARD_HOME, else ~/.switchboard."""
    d = os.environ.get('SWITCHBOARD_HOME')
    if not d:
        if _in_tests():
            raise RuntimeError('Tests must set SWITCHBOARD_HOME to a temporary folder.')
        d = os.path.join(home(), '.switchboard')
    return d


def data_path(name):
    return os.path.join(data_dir(), name)


def folder_mark(folder=None):
    """A short mark of the data folder this process uses. The page puts it in its answers, and a command compares it
    with its own before it changes anything through the page, so a command run with a folder of its own never changes
    the settings of a page that serves another. Made from the folder's real path and its file number on the disk, so
    the mark says nothing a reader could turn back into a name."""
    import hashlib
    d = os.path.realpath(folder or data_dir())
    try:
        s = os.stat(d)
        ident = '%d:%d' % (s.st_dev, s.st_ino)
    except OSError:
        ident = 'none'
    raw = 'switchboard data folder\0%s\0%s' % (d, ident)
    return hashlib.sha256(raw.encode('utf-8', 'surrogateescape')).hexdigest()[:16]


def claude_dir():
    """Claude Code's folder: $CLAUDE_CONFIG_DIR, else ~/.claude."""
    return os.environ.get('CLAUDE_CONFIG_DIR') or os.path.join(home(), '.claude')


def claude_json():
    return os.path.join(os.environ.get('CLAUDE_CONFIG_DIR') or home(), '.claude.json')


def projects():
    """Claude Code's chat history."""
    return os.path.join(claude_dir(), 'projects')


# The old names still read as before (core.DATA, core.STATE_PATH, ...), but each is worked out when it is read.
_LATE = {'HOME': home, 'DATA': data_dir, 'STATE_PATH': lambda: data_path('state.json'),
         'SETTINGS_PATH': lambda: data_path('settings.json'), 'LOCK_PATH': lambda: data_path('.lock'),
         'LOG_PATH': lambda: data_path('switchboard.log'), 'LOGIN_DIR': lambda: data_path('logins'),
         'PORT_FILE': lambda: data_path('port'), 'HOME_PROFILE_FILE': lambda: data_path('home-profile.txt'),
         'CLAUDE_DIR': claude_dir, 'CLAUDE_JSON': claude_json, 'PROJECTS': projects}


def __getattr__(name):
    if name in _LATE:
        return _LATE[name]()
    raise AttributeError('module %r has no attribute %r' % (__name__, name))


class SwitchError(Exception):
    """Something the person should read. The page shows the text as it is."""


def read_json(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


# ---------- writing a file: whole or not at all ----------
#
# A reader sees the old bytes or the new ones, never part of a file: the bytes go to a private temporary file beside it
# (owner-only from its first byte, a name of its own so two writers never share one), every byte is written, the file
# is flushed to the disk, and only then renamed over the old one. When anything fails on the way (a full disk, a folder
# that can't be written, a process killed) the temporary file goes and the old file stays as it was. Nothing that can
# fail comes after the rename.

class DiskFull(OSError):
    """A write refused because the disk (or the person's quota) is full. The file that was there is untouched."""


_WRITES = {'n': 0}
_WRITES_LOCK = threading.Lock()


def writes():
    """How many files this process has written through write_bytes (write_json, write_text). The page's snapshot of
    its state is built again when this moves (server.Snapshot)."""
    return _WRITES['n']


def _wrote():
    with _WRITES_LOCK:
        _WRITES['n'] += 1


def _through_links(path):
    """A symlinked file stays a link: the file it leads to is the one replaced."""
    try:
        return os.path.realpath(path) if os.path.islink(path) else path
    except OSError:
        return path


TMP_SUFFIX = '.tmp'
_NO_FSYNC = (errno.EINVAL, getattr(errno, 'ENOTSUP', -1), getattr(errno, 'EOPNOTSUPP', -1),
             getattr(errno, 'ENOSYS', -1))      # a disk that can't flush: written all the same


def write_bytes(path, data, mode=0o600):
    """Write `data` to `path` whole or not at all (see above). mode is the file's mode once it is in place. Raises
    DiskFull (an OSError) when the disk is full, any other OSError as it came; the old file is untouched either way."""
    target = _through_links(path)
    folder = os.path.dirname(target) or '.'
    os.makedirs(folder, mode=0o700, exist_ok=True)
    tmp = os.path.join(folder, '.%s.%s%s' % (os.path.basename(target)[:40], secrets.token_hex(4), TMP_SUFFIX))
    fd = None
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        try:
            os.fsync(fd)
        except OSError as e:
            if e.errno not in _NO_FSYNC:
                raise
        if mode != 0o600:
            os.fchmod(fd, mode)
        os.close(fd)
        fd = None
        os.replace(tmp, target)
    except BaseException as e:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        if isinstance(e, OSError) and not isinstance(e, DiskFull) and e.errno in (errno.ENOSPC, errno.EDQUOT):
            raise DiskFull(e.errno, e.strerror, path)
        raise
    _wrote()


def write_json(path, data, mode=0o600):
    """JSON whole or not at all. The text is made before any file is touched, so data that can't be written as JSON
    leaves the old file as it was."""
    write_bytes(path, json.dumps(data, indent=1).encode('utf-8'), mode)


def write_text(path, text, mode=0o600):
    write_bytes(path, str(text).encode('utf-8'), mode)


def sweep_temporary(folder=None, older_than=3600, now=None):
    """Temporary files a killed write left behind (".<name>.<random>.tmp"), older than an hour, in Switchboard's
    folder: removed. Returns how many went."""
    folder = folder or data_dir()
    now = time.time() if now is None else now
    gone = 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    for name in names:
        if not (name.startswith('.') and name.endswith(TMP_SUFFIX)):
            continue
        p = os.path.join(folder, name)
        try:
            st = os.lstat(p)
            if os.path.isfile(p) and not os.path.islink(p) and now - st.st_mtime > older_than:
                os.unlink(p)
                gone += 1
        except OSError:
            pass
    return gone


# ---------- the disk ----------

DISK_LOW = 200 * 1024 * 1024      # below this, Switchboard pauses what it can do without, and says so
DISK_FLOOR = 50 * 1024 * 1024     # below this, no switch should begin (it could not be read back and undone)
DISK_EVERY = 30.0                 # seconds (monotonic) between two looks
_DISK = {'at': None, 'free': None}
_DISK_LOCK = threading.Lock()


def disk_free(fresh=False, clock=time.monotonic):
    """Free bytes on the disk that holds Switchboard's folder (its nearest folder that exists), looked at most every
    30 s; None when it can't be told."""
    now = clock()
    with _DISK_LOCK:
        if not fresh and _DISK['at'] is not None and 0 <= now - _DISK['at'] < DISK_EVERY:
            return _DISK['free']
    where = data_dir()
    while where and not os.path.isdir(where) and os.path.dirname(where) != where:
        where = os.path.dirname(where)
    try:
        free = shutil.disk_usage(where).free
    except OSError:
        free = None
    with _DISK_LOCK:
        _DISK.update(at=now, free=free)
    return free


def disk_low(fresh=False):
    """True when the disk is almost full (under DISK_LOW free). Not known counts as not low."""
    free = disk_free(fresh)
    return free is not None and free < DISK_LOW


def read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


def settings():
    return _settings_now()


# ---------- settings.json and state.json damaged: never read as "nothing set" ----------
#
# settings.json holds whether auto-switch is on, the switch lines and the order; read as {} it would mean auto-switch
# off, and the next change would write that over it. So each write also keeps the same bytes as settings.json.last-good,
# and a read tells "not there" (a fresh start: {}) from "can't be read". A file that can't be read or understood is set
# aside as settings.json.damaged-<time> and the last good copy is written back; with no good copy it starts from what a
# new install has (no settings file). A read error that may pass by itself (EIO) uses the copy and moves nothing. Each
# is said once: the log, History, and repairs.json for the page and doctor.
#
# state.json (the page's own: each account's browser profile, the timeline, the list of events) is read without its
# lock in many places, so a damaged one is only copied aside (once per damage: its name carries a digest of its bytes)
# and the last good copy is used; the next write under locked() puts a good file back. The copy is kept at most every
# STATE_COPY_EVERY seconds, from save().

SETTINGS_COPY = 'settings.json.last-good'
STATE_COPY = 'state.json.last-good'
STATE_COPY_EVERY = 600.0        # seconds: state.json is written every minute; a copy this old loses little
_SETTINGS_TL = threading.local()
_SETTINGS_FALLBACK = {'sig': None, 'value': None}   # a damaged file that could not be restored: its copy, in memory
_NO_RESTORE = {'n': 0}


@contextmanager
def no_restore():
    """Within the block a damaged settings.json or state.json is answered from its copy and nothing is written: doctor
    only looks."""
    _NO_RESTORE['n'] += 1
    try:
        yield
    finally:
        _NO_RESTORE['n'] -= 1


def file_state(name):
    """'missing', 'fine' or 'damaged' for settings.json or state.json, by reading it only."""
    try:
        with open(data_path(name), 'rb') as f:
            raw = f.read()
    except (FileNotFoundError, NotADirectoryError):
        return 'missing'
    except OSError:
        return 'damaged'
    return 'fine' if _parsed(raw) is not None else 'damaged'


def keep_settings_copy():
    """The last good copy of settings.json made current when settings.json reads fine and differs from it (changed by
    hand, or from before copies were kept). True when it was written."""
    with _settings_held():
        try:
            raw = _settings_bytes(data_path('settings.json'))
        except OSError:
            return False
        if raw is None or _parsed(raw) is None:
            return False
        try:
            with open(data_path(SETTINGS_COPY), 'rb') as f:
                if f.read() == raw:
                    return False
        except OSError:
            pass
        write_bytes(data_path(SETTINGS_COPY), raw)
        return True


def _settings_bytes(path):
    """settings.json's bytes, or None when it is not there. Other errors are raised (OSError)."""
    try:
        with open(path, 'rb') as f:
            return f.read()
    except (FileNotFoundError, NotADirectoryError):
        return None


def _parsed(raw):
    """A JSON object from bytes, or None when they are not one."""
    try:
        obj = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError, RecursionError, AttributeError):
        return None
    return obj if isinstance(obj, dict) else None


def _good_copy(name):
    """(the copy as a dict, its time) for a last good copy that can be read, else (None, None)."""
    path = data_path(name)
    try:
        with open(path, 'rb') as f:
            obj = _parsed(f.read())
        return (obj, os.stat(path).st_mtime) if obj is not None else (None, None)
    except OSError:
        return None, None


def _sig(path):
    try:
        s = os.stat(path)
        return (s.st_ino, s.st_size, s.st_mtime_ns)
    except OSError:
        return None


def _settings_now():
    path = data_path('settings.json')
    try:
        raw = _settings_bytes(path)
    except IsADirectoryError:
        raw = b''                                   # not a file at all: damaged
    except PermissionError:
        raw = b''                                   # can't be read: as damaged (the bytes are kept aside as they are)
    except OSError as e:                            # may pass by itself: the copy for now, nothing moves
        copy, _ = _good_copy(SETTINGS_COPY)
        log('settings.json could not be read just now (%s); its last good copy is used' % (e.strerror or e))
        return copy or {}
    if raw is None:
        return {}
    obj = _parsed(raw)
    if obj is not None:
        return obj
    if _SETTINGS_FALLBACK['sig'] is not None and _SETTINGS_FALLBACK['sig'] == _sig(path):
        return dict(_SETTINGS_FALLBACK['value'] or {})
    if _NO_RESTORE['n']:
        return _good_copy(SETTINGS_COPY)[0] or {}
    if getattr(_SETTINGS_TL, 'held', False):
        return _settings_restore(path)
    try:
        with _settings_held():
            return _settings_restore(path)
    except LockTimeout:                             # another process holds the settings: their last good copy for
        return _good_copy(SETTINGS_COPY)[0] or {}   # now, and the restore is made at the next read


def _keep_aside(path):
    """Keep a damaged file beside itself as <path>.damaged-<time>: a copy of its bytes, or the file itself moved when
    they can't be read. The new name, or None when it could not be kept."""
    stamp = int(time.time())
    aside = '%s.damaged-%d' % (path, stamp)
    n = 1
    while os.path.exists(aside):
        n += 1
        aside = '%s.damaged-%d-%d' % (path, stamp, n)
    try:
        with open(path, 'rb') as f:
            write_bytes(aside, f.read())
        return aside
    except OSError:
        try:
            os.replace(path, aside)
            return aside
        except OSError:
            return None


def said_repair(what, kind, text, **facts):
    """A repair said once: the log, History, and repairs.json for the page and doctor."""
    log(text)
    try:
        from . import history, repairs
        history.record('repair', text)
        repairs.note(data_dir(), what, kind, text, **facts)
    except Exception as e:  # noqa: BLE001 - the repair is made whether or not it is noted
        log('a repair was not noted: %r' % e)


def _settings_restore(path):
    """Under the settings lock: settings.json damaged (see above). Returns the settings to use."""
    try:
        raw = _settings_bytes(path)
    except OSError:
        raw = b''
    if raw is None:
        return {}
    obj = _parsed(raw)
    if obj is not None:
        return obj                                  # it reads fine now (restored meanwhile): never over it
    copy, made = _good_copy(SETTINGS_COPY)
    aside = _keep_aside(path)
    if aside is None:                               # it could not be kept: nothing is written over it
        log('settings.json is damaged and could not be kept aside, so it was left as it is; its last good copy is '
            'used')
        _SETTINGS_FALLBACK.update(sig=_sig(path), value=copy or {})
        return dict(copy or {})
    try:
        if copy is not None:
            _write_settings(copy)
        else:
            try:
                os.unlink(path)                     # a new install has no settings file
            except FileNotFoundError:
                pass
    except OSError as e:
        log('settings.json could not be written back (%s); its last good copy is used' % (e.strerror or e))
        _SETTINGS_FALLBACK.update(sig=_sig(path), value=copy or {})
        return dict(copy or {})
    from .repairs import when_words
    if copy is not None:
        said_repair('settings', 'restored', "Switchboard's settings file was damaged. It was restored from a copy "
                     "made %s." % when_words(made), aside=aside, copy_at=made)
        return dict(copy)
    said_repair('settings', 'defaults', "Switchboard's settings file was damaged and no good copy of it was found, so "
                 "Switchboard started again from the settings of a new install. Check auto-switch and the switch "
                 "lines in Settings.", aside=aside)
    return {}


def _write_settings(s):
    """settings.json and its last good copy, the same bytes (write_json's), each written whole."""
    data = json.dumps(s, indent=1).encode('utf-8')
    write_json(data_path('settings.json'), s)
    try:
        write_bytes(data_path(SETTINGS_COPY), data)
    except OSError as e:
        log('the copy of settings.json was not kept: %s' % (e.strerror or e))


def _state_now():
    """state.json as a dict ({} when it is not there), or the last good copy when it is damaged (see above)."""
    path = data_path('state.json')
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except (FileNotFoundError, NotADirectoryError):
        return {}
    except OSError:
        raw = b''
    obj = _parsed(raw)
    if obj is not None:
        return obj
    copy, made = _good_copy(STATE_COPY)
    import hashlib
    aside = '%s.damaged-%s' % (path, hashlib.sha256(raw).hexdigest()[:10])
    if not os.path.exists(aside) and not _NO_RESTORE['n']:  # once per damage, whichever process sees it first
        try:
            write_bytes(aside, raw)
        except OSError:
            return copy or {}
        from .repairs import when_words
        said_repair('state', 'restored' if copy is not None else 'defaults',
                     "Switchboard's page file was damaged. %s" % (
                         'What you set there was taken from a copy made %s.' % when_words(made) if copy is not None
                         else 'No good copy of it was found; the accounts and their logins are not in it and are '
                              'as they were.'), aside=aside, copy_at=made)
    return copy or {}


# The page's own locks wait this long for another process (or thread) to let go, then give up and say so. Every
# holder reads and writes one small file (well under a second, even on a very busy Mac), so a minute is only ever
# reached by a holder that hangs; and a minute is far inside the page's own watchdog (watch.PAGE_STUCK), so a lock
# held for ever costs one minute of one step, never the page.
LOCK_DEADLINE = 60.0


class LockTimeout(SwitchError):
    """Another Switchboard process held one of the page's locks past LOCK_DEADLINE. Nothing was changed."""


def _take(fd, name):
    """An exclusive lock on fd (the file <data>/<name>), waited for LOCK_DEADLINE seconds at most: LockTimeout then,
    with one line in the log."""
    end = time.monotonic() + LOCK_DEADLINE
    pause = 0.01
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as e:
            if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise
        left = end - time.monotonic()
        if left <= 0:
            log('waited %d s for %s, which another Switchboard process held; went on without it'
                % (LOCK_DEADLINE, name))
            raise LockTimeout('Switchboard is busy with another change just now, so this was not saved. '
                              'Try again in a moment.')
        time.sleep(min(pause, left))
        pause = min(pause * 2, 0.25)


@contextmanager
def file_lock(name):
    """One at a time across Switchboard's processes (the page, a command in a terminal, the menu bar) and their
    threads: an exclusive lock on <data>/<name> for the length of the block, waited for LOCK_DEADLINE seconds at most
    (LockTimeout)."""
    os.makedirs(data_dir(), mode=0o700, exist_ok=True)
    fd = os.open(data_path(name), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        _take(fd, name)
        yield
    finally:
        os.close(fd)                        # closing it lets the lock go


_SETTINGS_LOCK = threading.Lock()


@contextmanager
def _settings_held():
    """settings.json read, changed and written back as one step: two requests at once, or the page and a command in
    a terminal at once, must not lose one change."""
    with _SETTINGS_LOCK, file_lock('settings.json.lock'):
        _SETTINGS_TL.held = True                    # a damaged file found inside is restored here, not locked again
        try:
            yield
        finally:
            _SETTINGS_TL.held = False


def set_setting(key, value):
    with _settings_held():
        s = settings()
        s[key] = value
        _write_settings(s)


UNSET = object()          # what update_setting's change returns to take the key out of settings.json


def update_setting(key, change):
    """One setting changed from its own value in one step: `change(the value now, None when unset)` returns the new
    value (it must not take a lock or write a setting itself), or UNSET to take the key out. Written only when it
    changed. Returns the new value (None once taken out)."""
    with _settings_held():
        s = settings()
        new = change(s.get(key))
        if new is UNSET:
            if key in s:
                del s[key]
                _write_settings(s)          # the copy too: a damaged file never brings the key back
            return None
        if new != s.get(key):
            s[key] = new
            _write_settings(s)
        return new


def key():
    """A secret made once per install. Only your own bookmark carries it, so only it opens links without a click."""
    if DEMO:
        return 'demo'
    return settings().get('key') or update_setting('key', lambda k: k or secrets.token_urlsafe(16))


def port():
    try:
        return int(os.environ.get('SWITCHBOARD_PORT') or settings().get('port') or 7799)
    except (TypeError, ValueError):
        return 7799


# ---------- the log ----------
#
# switchboard.log: one line per thing that happened, rotated at 2 MB. A line said again and again (a failure retried
# every pass) is written once; the repeats are counted and said as one line when something else is logged, when
# REPEAT_EVERY seconds have passed, or at log_flush(). Nothing that looks like a token is ever written: redact() takes
# out Anthropic keys and tokens, JSON web tokens and long runs of key-like characters, wherever they hide.

LOG_MAX = 2000000
REPEAT_EVERY = 300.0            # seconds (monotonic): a line still repeating is counted aloud this often
_TOKEN_RES = (re.compile(r'sk-ant-[A-Za-z0-9_\-]{6,}'),
              re.compile(r'\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}'),
              re.compile(r'(?i)\b(bearer|token|secret|password|api[_-]?key)(["\']?\s*[:=]\s*["\']?|\s+)'
                         r'[A-Za-z0-9_\-.+/=]{12,}'),
              re.compile(r'(?<![A-Za-z0-9_\-+/])(?=[A-Za-z0-9_\-+/]*\d)(?=[A-Za-z0-9_\-+/]*[A-Za-z])'
                         r'[A-Za-z0-9_\-+]{40,}={0,2}(?![A-Za-z0-9_\-+/])'))
EMAIL_RE_ANY = re.compile(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}')


def redact(text, emails=False):
    """text with anything shaped like a secret replaced by [hidden]: Anthropic keys and tokens (sk-ant-...), JSON web
    tokens, a value after bearer/token/secret/password/api_key, and runs of 40 or more key-like characters with letters
    and digits (paths, with their slashes and dots, are left alone). emails=True also hides email addresses, for text
    that leaves the Mac."""
    text = str(text)
    for i, pattern in enumerate(_TOKEN_RES):
        if i == 2:
            text = pattern.sub(lambda m: m.group(1) + m.group(2) + '[hidden]', text)
        else:
            text = pattern.sub('[hidden]', text)
    if emails:
        text = EMAIL_RE_ANY.sub('[email]', text)
    return text


_LOG_LOCK = threading.RLock()
_REPEAT = {'line': None, 'count': 0, 'said_at': 0.0}


def _log_write(path, line):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    try:
        if os.path.getsize(path) > LOG_MAX:
            os.replace(path, path + '.1')
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, ('%s %s\n' % (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), line)).encode('utf-8', 'replace'))
    finally:
        os.close(fd)


def _repeats_line():
    n = _REPEAT['count']
    return '(the line above %d more time%s)' % (n, '' if n == 1 else 's')


def log(msg, clock=time.monotonic):
    """One line in switchboard.log. Never raises; a full disk only loses the line."""
    if DEMO:                                # the demo writes nothing, not even ~/.switchboard
        return
    try:
        line = ' '.join(redact(msg).split('\n'))
        now = clock()
        with _LOG_LOCK:
            path = data_path('switchboard.log')
            if line == _REPEAT['line']:
                _REPEAT['count'] += 1
                if not 0 <= now - _REPEAT['said_at'] < REPEAT_EVERY:
                    _log_write(path, _repeats_line())
                    _REPEAT.update(count=0, said_at=now)
                return
            if _REPEAT['count']:
                _log_write(path, _repeats_line())
            _REPEAT.update(line=line, count=0, said_at=now)
            _log_write(path, line)
    except Exception:  # noqa: BLE001 - logging never stops what it records
        pass


def log_flush():
    """Say the repeats counted so far (the page's loop calls this each pass, so a count is never lost for long)."""
    try:
        with _LOG_LOCK:
            if _REPEAT['count']:
                _log_write(data_path('switchboard.log'), _repeats_line())
                _REPEAT.update(count=0, said_at=time.monotonic())
    except Exception:  # noqa: BLE001
        pass


# agent.log is where launchd sends the page job's own output (its StandardOutPath and StandardErrorPath). launchd never
# rotates it, so the page does: when this process's output is that file and it has grown past AGENT_MAX, it becomes
# agent.log.1 and a fresh agent.log takes its place on standard output and error.
AGENT_LOG = 'agent.log'
AGENT_MAX = 1024 * 1024


def rotate_own_output(path=None, max_bytes=AGENT_MAX):
    """Rotate agent.log when it is this process's standard output or error (see above). Returns True when it did."""
    path = path or data_path(AGENT_LOG)
    try:
        st = os.stat(path)
    except OSError:
        return False
    if st.st_size <= max_bytes:
        return False
    ours = []
    for fd in (1, 2):
        try:
            s = os.fstat(fd)
        except OSError:
            continue
        if (s.st_dev, s.st_ino) == (st.st_dev, st.st_ino):
            ours.append(fd)
    if not ours:
        return False
    with _LOG_LOCK:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:  # noqa: BLE001
                pass
        try:
            os.replace(path, path + '.1')
            new = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        except OSError:
            return False
        try:
            for fd in ours:
                os.dup2(new, fd)
        finally:
            os.close(new)
    return True


def run(cmd, stdin=None, timeout=30, env=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout, env=env)


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


# ---------- saved state ----------

def blank_state():
    return {'v': 2, 'accounts': {}, 'order': [], 'active': None, 'events': [], 'timeline': [],
            'artifacts': [], 'scan': {}, 'tick_at': 0, 'error': None, 'engine': None}


def load():
    st = _state_now() or blank_state()
    for k, v in blank_state().items():
        st.setdefault(k, v)
    return st


def save(st):
    data = json.dumps(st, indent=1).encode('utf-8')
    write_bytes(data_path('state.json'), data)
    try:                                            # the last good copy, at most every STATE_COPY_EVERY seconds
        recent = 0 <= time.time() - os.stat(data_path(STATE_COPY)).st_mtime < STATE_COPY_EVERY
    except OSError:
        recent = False
    if not recent:
        try:
            write_bytes(data_path(STATE_COPY), data)
        except OSError as e:
            log('the copy of state.json was not kept: %s' % (e.strerror or e))


@contextmanager
def locked():
    """Read the state, let the caller change it, save it. One writer at a time, waited for LOCK_DEADLINE seconds at
    most (LockTimeout: nothing is read or saved)."""
    os.makedirs(data_dir(), mode=0o700, exist_ok=True)
    with open(data_path('.lock'), 'w') as lock:
        _take(lock.fileno(), '.lock')
        try:
            st = load()
            yield st
            save(st)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def ensure_account(st, email):
    a = st['accounts'].get(email)
    if a is None:
        a = st['accounts'][email] = {'email': email, 'connected': False, 'profile': None, 'profile_src': None}
        st['order'].append(email)
    return a


def signed_in_here(st, email, profile):
    """A page of this account's loaded in browser profile `profile`, or it signed in there: the profile is signed in to
    claude.ai as it (the account's `signed_in_profile`, what Chrome setup's "Signed in" reads). One profile holds one
    claude.ai login, so any other account's note of that profile goes."""
    profile_unproven(st, profile)
    if email in st['accounts']:
        st['accounts'][email]['signed_in_profile'] = profile


def profile_unproven(st, profile):
    """Which account `profile` is signed in as is not known any more (it was seen signed out, or its link changed
    hands): every account's note of it goes, until a page load shows it again."""
    for a in st['accounts'].values():
        if profile and a.get('signed_in_profile') == profile:
            a.pop('signed_in_profile', None)


def event(st, text, kind='info', **facts):
    """One line on the page's list, in switchboard.log, and in the history (history.py), with the facts the caller
    knows beyond its sentence (h: the history kind; account, frm, to, by, reason, on, ...)."""
    st['events'].append({'t': time.time(), 'kind': kind, 'text': text})
    del st['events'][:-80]
    log(text)
    try:
        from . import history
        history.from_event(text, kind, facts)
    except Exception as e:  # noqa: BLE001 - the history never stops what it records
        log('history: %r' % e)


def set_active(st, email):
    st['active'] = email
    if not st['timeline'] or st['timeline'][-1][0] != email:
        st['timeline'].append([email, time.time()])
        del st['timeline'][:-800]


def owner_seg(st, t):
    """(the account Claude Code was on at time t, True when t is far enough from a switch to trust)."""
    owner, start, nxt = None, None, None
    for email, begin in st['timeline']:
        if begin <= t:
            owner, start = email, begin
        else:
            nxt = begin
            break
    if owner is None:
        return None, False
    # An open chat follows a switch within half a minute, and an automatic switch
    # is noticed up to a minute late. Near a switch, the account is only a guess.
    return owner, (t - start >= SETTLE) and (nxt is None or nxt - t >= SETTLE)


def name_of(st, email):
    """The name people see for an account: its email, on every surface. Short aliases ("work", or a part of the
    email) read as made-up titles, so nothing shows them; they still work as input."""
    return email or ''
