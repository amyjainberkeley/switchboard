"""The person's choices for the other assistants, in one file (contract.SETTINGS_FILE), written whole under the
providers lock: the order to carry on in, which providers are added, the yes (or never) per folder and provider, a
folder's `stay`, the one-time yeses to asking a provider's app for its numbers and to its first job, how each app
opens, and the note's size.

    {"v": 1, "order": [P1, P2], "added": {P1: 0, P2: 1795600000.0},
     "folders": {"/Users/maya/site": {"id": [16777220, 1234567],
                                      "consent": {P2: {"state": "yes", "at": 1795600000.0}},
                                      "stay": null, "stay_set": []}},
     "open_with": {P2: "terminal"}, "handoff_budget": 96000,
     "ask_ok": {P2: 1795600000.0}, "hand_ack": {P2: 1795600000.0}}

P1 and P2 stand for provider ids from the registry (switchboard/providers/__init__.py). The registry's first provider
(the one Switchboard's own account list serves) is always added, at 0, and always in the order.

A folder is kept under its real path in Unicode NFC, with its (st_dev, st_ino): the same folder spelt another way (a
case APFS ignores, an NFD name) finds its entry by that identity, but only while the entry's own path is still that
same folder, so a new folder that was given a gone folder's number never inherits its yes. Reading never writes; a
file that cannot be read is the defaults. A change never writes over a file that is there and cannot be read (a `never`
or a `stay` in it would be lost): it raises ProviderError('unreadable'). No question is asked and no program runs while
the lock is held.
"""
import json
import os
import unicodedata
from contextlib import contextmanager
from typing import Dict, List, Optional

from .. import core
from . import contract
from . import ids as provider_ids
from .contract import ProviderError

STATES = ('yes', 'never')
BUDGET, BUDGET_MIN, BUDGET_MAX = 96000, 32000, 400000
OPEN_WITH = (contract.IN_TERMINAL, contract.IN_APP)


def _path() -> str:
    return core.data_path(contract.SETTINGS_FILE)


# Said when a change finds the file there and not readable. {path}.
UNREADABLE = ("Switchboard's file of choices for other assistants ({path}) cannot be read, so nothing was changed. "
              "Move it aside and run this again.")


def _read() -> dict:
    data = core.read_json(_path())
    return data if isinstance(data, dict) else {}


def read_whole(path: str, unreadable: str) -> dict:
    """The JSON object in path for a change: {} when there is no file; ProviderError('unreadable', unreadable) when
    one is there and cannot be read or holds something other than an object, so nothing writes over it."""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError):
        data = None
    if not isinstance(data, dict):
        raise ProviderError('unreadable', unreadable.format(path=path))
    return data


@contextmanager
def _changing():
    """Read, change and write the whole file under the providers lock. An exception in the block writes nothing, and
    a file that is there and cannot be read is never written over."""
    with core.file_lock(contract.LOCK_NAME):
        data = read_whole(_path(), UNREADABLE)
        yield data
        data['v'] = 1
        core.write_json(_path(), data, 0o600)


def _known(pid) -> bool:
    return isinstance(pid, str) and pid in provider_ids()


def _check(pid) -> None:
    if not _known(pid):
        raise ValueError('not a provider this Switchboard knows')


# ---------- providers and their order ----------

def first() -> str:
    """The provider that is always added and always in the order: the registry's first, the one Switchboard's own
    account list serves."""
    return provider_ids()[0]


def _added(data: dict) -> Dict[str, float]:
    raw = data.get('added') if isinstance(data.get('added'), dict) else {}
    out = {p: t for p, t in raw.items() if _known(p) and isinstance(t, (int, float)) and not isinstance(t, bool)}
    out.setdefault(first(), 0)
    return out


def _order(data: dict) -> List[str]:
    added = _added(data)
    stored = data.get('order') if isinstance(data.get('order'), list) else []
    out = []
    for p in stored:
        if p in added and p not in out:
            out.append(p)
    out += [p for p in provider_ids() if p in added and p not in out]
    return out


def added() -> Dict[str, float]:
    """Each added provider and when it was added (the first provider always, at 0)."""
    return _added(_read())


def order() -> List[str]:
    """The added providers in the person's order: as set, then any added later in the registry's order."""
    return _order(_read())


def add(pid: str, now: float) -> None:
    """Add a provider (`switchboard add PROVIDER`); it goes last in an order the person set."""
    _check(pid)
    with _changing() as data:
        current = _added(data)
        if pid not in current:
            current[pid] = now
        data['added'] = current
        if isinstance(data.get('order'), list) and pid not in data['order']:
            data['order'].append(pid)


def set_order(ids: List[str]) -> None:
    """The named providers first, in that order; the added ones left out keep their places after them. Every id must
    be known and added, and named once (ValueError otherwise)."""
    ids = list(ids)
    if len(set(ids)) != len(ids) or not ids:
        raise ValueError('each provider is named once')
    with _changing() as data:
        current = _added(data)
        for p in ids:
            _check(p)
            if p not in current:
                raise ValueError('not added')
        data['order'] = ids + [p for p in _order(data) if p not in ids]


# ---------- per folder ----------

def _key(folder: str) -> str:
    return unicodedata.normalize('NFC', os.path.realpath(folder))


def _identity(path: str) -> Optional[List[int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return [st.st_dev, st.st_ino]


def _same(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _entry(data: dict, folder: str, make: bool = False) -> Optional[dict]:
    folders = data.get('folders') if isinstance(data.get('folders'), dict) else None
    if folders is None:
        if not make:
            return None
        folders = data['folders'] = {}
    key = _key(folder)
    entry = folders.get(key)
    if not isinstance(entry, dict):
        entry = None
        ident = _identity(folder)
        if ident is not None:
            for k, e in folders.items():
                if isinstance(e, dict) and e.get('id') == ident and _same(k, folder):
                    entry = e
                    break
    if entry is None and make:
        entry = folders[key] = {'id': _identity(folder), 'consent': {}, 'stay': None, 'stay_set': []}
    if entry is not None and make:
        if not isinstance(entry.get('consent'), dict):
            entry['consent'] = {}
        if not isinstance(entry.get('stay_set'), list):
            entry['stay_set'] = []
    return entry


def _state(entry: Optional[dict], pid: str) -> Optional[str]:
    consent = entry.get('consent') if entry else None
    item = consent.get(pid) if isinstance(consent, dict) else None
    state = item.get('state') if isinstance(item, dict) else None
    return state if state in STATES else None


def consent(folder: str, pid: str) -> Optional[str]:
    """'yes', 'never', or None (never asked) for chats of this folder going to pid."""
    return _state(_entry(_read(), folder), pid) if _known(pid) else None


def consent_at(folder: str, pid: str) -> Optional[float]:
    """When the person said yes for this folder and pid (set_consent or allow); None for a never, for never asked,
    and for a yes kept before times were."""
    if not _known(pid):
        return None
    entry = _entry(_read(), folder)
    if _state(entry, pid) != 'yes':
        return None
    at = entry['consent'][pid].get('at')
    return float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None


def set_consent(folder: str, pid: str, state: Optional[str], now: float) -> None:
    """The person's answer for this folder and provider ('yes', 'never'; None forgets it). An answer the person
    gives is theirs: a `stay` no longer counts it as one of its marks."""
    _check(pid)
    if state is not None and state not in STATES:
        raise ValueError('a consent is yes or never')
    with _changing() as data:
        entry = _entry(data, folder, make=True)
        if state is None:
            entry['consent'].pop(pid, None)
        else:
            entry['consent'][pid] = {'state': state, 'at': now}
        entry['stay_set'] = [q for q in entry['stay_set'] if q != pid]


def allow(folder: str, pid: str, now: float) -> None:
    """`switchboard allow PROVIDER`: yes for this folder, lifting a never the person typed. A stay on another
    provider stays (`stay off` lifts that)."""
    set_consent(folder, pid, 'yes', now)


def stays(folder: str) -> Optional[str]:
    """The provider this folder stays on, or None."""
    entry = _entry(_read(), folder)
    pid = entry.get('stay') if entry else None
    return pid if _known(pid) else None


def set_stay(folder: str, pid: Optional[str], now: float) -> None:
    """`switchboard stay PROVIDER`: this folder's chats never go to any other added provider. It sets never for each
    of them that is not never already and keeps that list in stay_set; `stay off` (pid None), or a stay on another
    provider, takes back exactly those marks. A never the person typed stays."""
    if pid is not None:
        _check(pid)
    with _changing() as data:
        entry = _entry(data, folder, make=True)
        for q in entry['stay_set']:
            if _state(entry, q) == 'never':
                entry['consent'].pop(q, None)
        entry['stay_set'] = []
        entry['stay'] = pid
        if pid is None:
            entry.pop('stay_at', None)
            return
        entry['stay_at'] = now
        for q in _order(data):
            if q != pid and _state(entry, q) != 'never':
                entry['consent'][q] = {'state': 'never', 'at': now}
                entry['stay_set'].append(q)


# ---------- once per Mac ----------

def _once(key: str, pid: str) -> bool:
    raw = _read().get(key)
    return isinstance(raw, dict) and isinstance(raw.get(pid), (int, float)) and not isinstance(raw.get(pid), bool)


def _set_once(key: str, now: float, pid: str) -> None:
    _check(pid)
    with _changing() as data:
        raw = data.get(key) if isinstance(data.get(key), dict) else {}
        raw[pid] = now
        data[key] = raw


def ask_ok(pid: str) -> bool:
    """The person's one yes to starting pid's own helper to read its numbers (DESIGN 8.3)."""
    return _once('ask_ok', pid)


def set_ask_ok(now: float, pid: str) -> None:
    _set_once('ask_ok', now, pid)


def hand_ack(pid: str) -> bool:
    """The person's one answer to pid's own question before its first job (the adapter's job_question), asked the
    first time. An answer kept as one number, before it was kept per provider, is not taken for any of them: the
    question is asked once more."""
    return _once('hand_ack', pid)


def set_hand_ack(now: float, pid: str) -> None:
    _set_once('hand_ack', now, pid)


# ---------- how ----------

def open_with(pid: str) -> str:
    """How `continue` opens pid's app when --in is not given: 'terminal' unless the person set 'app'."""
    raw = _read().get('open_with')
    how = raw.get(pid) if isinstance(raw, dict) else None
    return how if how in OPEN_WITH else contract.IN_TERMINAL


def set_open_with(pid: str, how: str) -> None:
    _check(pid)
    if how not in OPEN_WITH:
        raise ValueError('an app opens in a terminal or in its app')
    with _changing() as data:
        raw = data.get('open_with') if isinstance(data.get('open_with'), dict) else {}
        raw[pid] = how
        data['open_with'] = raw


def handoff_budget() -> int:
    """The note's size in bytes: 96,000 unless set, kept within 32,000 and 400,000."""
    raw = _read().get('handoff_budget')
    if not isinstance(raw, int) or isinstance(raw, bool):
        return BUDGET
    return max(BUDGET_MIN, min(BUDGET_MAX, raw))
