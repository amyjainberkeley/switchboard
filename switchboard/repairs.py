"""What Switchboard repaired by itself, kept so the page and doctor can say it: <data>/repairs.json.

{'entries': [...], 'watch': {...}}
- entries, newest last: {'what' ('accounts', 'settings', 'state', 'statusline', 'notify', 'session_hooks', ...),
  'kind' ('restored', 'defaults', 'put_back', 'gone_again', ...), 'at' (epoch seconds), 'text' (the one sentence the
  page and doctor show) and the facts behind it ('aside': the damaged file kept, 'copy': the copy used, 'copy_at').
  An entry is said for a day (SHOWN) and kept for a week (KEEP).
- watch: what selfcheck keeps between its looks (when it last put each entry back in Claude Code's settings, and
  when it stopped putting one back), by name.

Written whole (a private temporary file renamed over it) under its own lock, held at most LOCK_WAIT. Standard library
only, so the account engine, core and the page can all use it. Nothing here ever raises: a repair is made whether
or not it could be noted.
"""
import fcntl
import json
import os
import secrets
import time

NAME = 'repairs.json'
LOCK = 'repairs.lock'
SHOWN = 86400               # the page and doctor say a repair for a day after it was made
KEEP = 7 * 86400            # entries older than this are dropped at the next write
MAX_ENTRIES = 50            # and never more than this many (a fault that comes back each minute can't grow the file)
LOCK_WAIT = 2.0             # seconds a note waits for another writer before it is given up (only the note is lost)
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
MONTHS = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def when_words(t):
    """'Tue Nov 24 at 3:12 PM' in this Mac's time: a moment that stays true on any day it is read."""
    lt = time.localtime(t)
    return '%s %s %d at %d:%02d %s' % (DAYS[lt.tm_wday], MONTHS[lt.tm_mon - 1], lt.tm_mday, lt.tm_hour % 12 or 12,
                                       lt.tm_min, 'AM' if lt.tm_hour < 12 else 'PM')


def _path(folder):
    return os.path.join(folder, NAME)


def _read(folder):
    try:
        with open(_path(folder), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {'entries': [], 'watch': {}}
    if not isinstance(data, dict):
        return {'entries': [], 'watch': {}}
    entries = data.get('entries') if isinstance(data.get('entries'), list) else []
    watch = data.get('watch') if isinstance(data.get('watch'), dict) else {}
    return {'entries': [e for e in entries if isinstance(e, dict)], 'watch': watch}


def _write(folder, data):
    path = _path(folder)
    tmp = os.path.join(folder, '.%s.%s.tmp' % (NAME, secrets.token_hex(4)))
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(data, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _changing(folder, change, now):
    """Read, change and write repairs.json under its lock. Returns what `change` returned, or None when the lock or the
    write failed."""
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(folder, LOCK), os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError:
        return None
    try:
        end = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, PermissionError):
                if time.monotonic() >= end:
                    return None
                time.sleep(0.05)
        data = _read(folder)
        out = change(data)
        data['entries'] = [e for e in data['entries'] if isinstance(e.get('at'), (int, float))
                           and 0 <= now - e['at'] <= KEEP][-MAX_ENTRIES:]
        _write(folder, data)
        return out
    except (OSError, TypeError, ValueError):
        return None
    finally:
        os.close(fd)


def note(folder, what, kind, text, now=None, **facts):
    """Add one entry (see the module's docstring) and return it, or None when it could not be written."""
    now = time.time() if now is None else now
    entry = dict(facts, what=what, kind=kind, text=text, at=now)

    def add(data):
        data['entries'].append(entry)
        return entry
    return _changing(folder, add, now)


def recent(folder, within=SHOWN, now=None, what=None):
    """The entries of the last `within` seconds (of one `what` when given), newest first."""
    now = time.time() if now is None else now
    out = [e for e in _read(folder)['entries'] if isinstance(e.get('at'), (int, float)) and 0 <= now - e['at'] <= within
           and (what is None or e.get('what') == what)]
    return sorted(out, key=lambda e: e['at'], reverse=True)


def watch(folder):
    """selfcheck's notes between looks: {name: dict}."""
    return dict(_read(folder)['watch'])


def set_watch(folder, name, value, now=None):
    """Keep selfcheck's note `name` (a dict), or drop it (None). True when written."""
    now = time.time() if now is None else now

    def put(data):
        if value is None:
            data['watch'].pop(name, None)
        else:
            data['watch'][name] = value
        return True
    return bool(_changing(folder, put, now))
