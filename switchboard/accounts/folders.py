"""Folders that run as one account: `switchboard folder NAME` sets one, and `switchboard run` without a NAME, in that
folder or any folder inside it, runs as that account. The nearest folder above wins.

Kept for this Mac only, in <data>/folders.json, owner-only and written whole (core.write_json), under its own lock:

    {"v": 1, "folders": {"/Users/maya/work/client": {"account": "<account id>", "email": "maya@fernhill.example",
                                                     "at": 1795600000.0, "id": [16777220, 1234567]}}}

A folder is kept under its real path (links resolved, no trailing slash) in Unicode NFC. The same folder spelt another
way finds its entry: through a link, with `..` or a trailing slash, in another Unicode form, or in another case on a
disk that ignores case (then it must still be the folder the entry was set for, by its device and inode, so a gone
folder's entry never passes to a new one). The account is kept by its id, so the same email in two organizations stays
two accounts, and by its email, so a line can name it after it is gone. Auto-switch never reads this file.

Reading never writes, and a file that cannot be read is no entries. A change never writes over a file that is there and
cannot be read: it raises Unreadable, and nothing changes.
"""
import json
import os
import unicodedata

from .. import core

FILE = 'folders.json'
LOCK = 'folders.json.lock'


class Unreadable(core.SwitchError):
    """The file is there and cannot be read, so a change would lose what is in it."""


def _path():
    return core.data_path(FILE)


def key(folder):
    """The key a folder is kept under: its real path, in NFC, without a trailing slash."""
    return unicodedata.normalize('NFC', os.path.realpath(os.path.abspath(os.path.expanduser(folder))))


def _entries(data):
    raw = data.get('folders') if isinstance(data, dict) else None
    out = {}
    for path, e in (raw.items() if isinstance(raw, dict) else ()):
        if isinstance(path, str) and os.path.isabs(path) and isinstance(e, dict) and isinstance(e.get('account'), str) \
                and e['account']:
            out[path] = e
    return out


def _read():
    return _entries(core.read_json(_path()))


def _read_for_change():
    try:
        with open(_path(), encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        raise Unreadable("Switchboard's file of folders (%s) cannot be read, so nothing was changed. Move it aside "
                         "and run this again." % _path())
    return _entries(data)


def _write(entries):
    core.write_json(_path(), {'v': 1, 'folders': dict(sorted(entries.items()))}, 0o600)


def _identity(folder):
    try:
        st = os.stat(folder)
    except OSError:
        return None
    return [st.st_dev, st.st_ino]


def same(kept, entry, folder):
    """True when the entry kept under `kept` is for the folder whose key is `folder`. Another case of the same name is
    the same folder only on a disk that ignores case, and only while it is still the folder the entry was set for (its
    device and inode), so a folder made later under the name of a gone one never takes its entry."""
    if kept == folder:
        return True
    if kept.casefold() != folder.casefold():
        return False
    return _identity(folder) is not None and _identity(folder) == entry.get('id')


def _find(entries, folder):
    return next((k for k, e in entries.items() if same(k, e, folder)), None)


def entries():
    """Every entry, by folder: [(folder, {'account', 'email', 'at'})]."""
    return sorted(_read().items())


def own(folder):
    """(kept folder, entry) set for exactly this folder, or None."""
    found = _read()
    k = _find(found, key(folder))
    return (k, found[k]) if k is not None else None


def nearest(folder, found=None):
    """(kept folder, entry) for the folder itself or the nearest folder above it that has one, or None."""
    found = _read() if found is None else found
    if not found:
        return None
    here = key(folder)
    while True:
        k = _find(found, here)
        if k is not None:
            return k, found[k]
        up = os.path.dirname(here)
        if up == here:
            return None
        here = up


def set_account(folder, account, email, now):
    """This folder runs as the account. An entry it had (under any spelling) is replaced. Returns the kept folder."""
    here = key(folder)
    with core.file_lock(LOCK):
        found = _read_for_change()
        old = _find(found, here)
        if old is not None:
            del found[old]
        found[here] = {'account': account, 'email': email, 'at': now, 'id': _identity(here)}
        _write(found)
    return here


def clear(folder):
    """Take out this folder's own entry. Returns the entry taken out, or None when it had none."""
    with core.file_lock(LOCK):
        found = _read_for_change()
        k = _find(found, key(folder))
        if k is None:
            return None
        gone = found.pop(k)
        _write(found)
    return gone


def remove(folders):
    """Take out the entries kept under these folders (exactly as kept). Returns the ones taken out, by folder."""
    with core.file_lock(LOCK):
        found = _read_for_change()
        gone = [(k, found.pop(k)) for k in sorted(set(folders)) if k in found]
        if gone:
            _write(found)
    return gone


def forget_account(account_ids):
    """An account removed from Switchboard takes its folders with it. Never raises: the account is gone either way,
    and an entry for it left behind is listed by `switchboard folders --check`."""
    gone = set(account_ids or ())
    try:
        with core.file_lock(LOCK):
            if not os.path.exists(_path()):
                return
            found = _read_for_change()
            keep = dict((k, e) for k, e in found.items() if e.get('account') not in gone)
            if len(keep) != len(found):
                _write(keep)
    except (OSError, ValueError, core.SwitchError) as e:
        core.log("folders: could not take a removed account's folders away (%s)" % type(e).__name__)
