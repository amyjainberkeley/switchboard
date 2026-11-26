"""The ledger of the other assistants' moves (contract.LEDGER_FILE), written whole under the providers lock.

A task is one piece of work in one folder: the chat it began in and every chat a move led to. A hop is one move
(contract.Hop): a `continue` to another assistant, a `back`, or a `hand`. The ledger holds ids, paths, times, sizes
and counts, never a chat's words:

    {"v": 1, "tasks": {"t-…": {"id", "folder", "made_at", "origin": {"provider", "chat"}}},
     "hops": [Hop as a dict, oldest first]}

At most MAX_HOPS hops are kept (the oldest go first) and prune() drops hops older than the notes they point at. The
lock is held only to read, decide and write: never while a question is asked, a program runs or an exec happens. A
ledger that is there and cannot be read reads as empty and is never written over: a change raises
ProviderError('unreadable').
"""
import dataclasses
import re
import secrets
import time
import unicodedata
from typing import List, Optional

from .. import core
from . import choices, contract
from .contract import Hop, ProviderError

HOP_ID_RE = re.compile(r'^h-[0-9a-f]{8}\Z')
TASK_ID_RE = re.compile(r'^t-[0-9a-f]{8}\Z')
KINDS = ('continue', 'back', 'hand')
STATES = ('made', 'started', 'in_app', 'printed', 'answered', 'failed', 'returned')
CLOSED = ('returned', 'failed')
MAX_HOPS = 500
KEEP_S = contract.NOTE_KEEP_DAYS * 86400
_FIELDS = tuple(f.name for f in dataclasses.fields(Hop))


def new_hop_id() -> str:
    return 'h-' + secrets.token_hex(4)


def new_task_id() -> str:
    return 't-' + secrets.token_hex(4)


def _path() -> str:
    return core.data_path(contract.LEDGER_FILE)


# Said when a change finds the ledger there and not readable. {path}.
UNREADABLE = ("Switchboard's record of moves between assistants ({path}) cannot be read, so nothing was changed. "
              "Move it aside and run this again.")


def _read(changing: bool = False) -> dict:
    """The ledger, its parts checked. For a change (changing=True) a ledger that is there and cannot be read raises
    ProviderError('unreadable'); otherwise it reads as empty."""
    data = choices.read_whole(_path(), UNREADABLE) if changing else core.read_json(_path())
    tasks = data.get('tasks') if isinstance(data.get('tasks'), dict) else {}
    hops = [h for h in data.get('hops', []) if isinstance(h, dict)] if isinstance(data.get('hops'), list) else []
    return {'v': 1, 'tasks': {k: v for k, v in tasks.items() if TASK_ID_RE.match(str(k)) and isinstance(v, dict)},
            'hops': hops}


def _write(data: dict) -> None:
    data['v'] = 1
    core.write_json(_path(), data, 0o600)


def _hop(d: dict) -> Optional[Hop]:
    try:
        h = Hop(**{k: d[k] for k in _FIELDS if k in d})
    except TypeError:
        return None
    return h if HOP_ID_RE.match(str(h.id)) else None


def _same_folder(a, b) -> bool:
    return isinstance(a, str) and isinstance(b, str) and \
        unicodedata.normalize('NFC', a) == unicodedata.normalize('NFC', b)


def _check(h: Hop) -> None:
    if not isinstance(h, Hop) or not HOP_ID_RE.match(str(h.id)) or not TASK_ID_RE.match(str(h.task)):
        raise ValueError('a hop needs an h- id and a t- task')
    if h.kind not in KINDS or h.state not in STATES:
        raise ValueError('a hop kind or state this ledger does not know')


def task_for(folder: str, provider: str, chat_id: Optional[str]) -> str:
    """The task this chat belongs to: the task of a hop that led to it or left from it (newest first), or the task
    it began; else a new task with this chat as its origin."""
    with core.file_lock(contract.LOCK_NAME):
        data = _read(changing=True)
        if chat_id:
            for d in reversed(data['hops']):
                if (d.get('to_provider') == provider and d.get('to_chat') == chat_id) or \
                        (d.get('from_provider') == provider and d.get('from_chat') == chat_id):
                    if TASK_ID_RE.match(str(d.get('task'))):
                        return d['task']
            for tid, t in data['tasks'].items():
                if t.get('origin') == {'provider': provider, 'chat': chat_id}:
                    return tid
        tid = new_task_id()
        while tid in data['tasks']:
            tid = new_task_id()
        data['tasks'][tid] = {'id': tid, 'folder': folder, 'made_at': time.time(),
                              'origin': {'provider': provider, 'chat': chat_id}}
        _write(data)
        return tid


def add_hop(hop: Hop) -> Hop:
    """Record a move. Its task is made when the ledger lacks it. The oldest hops past MAX_HOPS go."""
    _check(hop)
    with core.file_lock(contract.LOCK_NAME):
        data = _read(changing=True)
        if any(d.get('id') == hop.id for d in data['hops']):
            raise ValueError('that hop is in the ledger already')
        if hop.task not in data['tasks']:
            data['tasks'][hop.task] = {'id': hop.task, 'folder': hop.folder, 'made_at': hop.at,
                                       'origin': {'provider': hop.from_provider, 'chat': hop.from_chat}}
        data['hops'].append(dataclasses.asdict(hop))
        data['hops'] = data['hops'][-MAX_HOPS:]
        _write(data)
    return hop


def update_hop(hop_id: str, **changes) -> Hop:
    """Change a hop's own fields (never its id). Raises ProviderError('not_found') when it is not in the ledger."""
    bad = [k for k in changes if k not in _FIELDS or k == 'id']
    if bad:
        raise ValueError('not a field a hop has')
    if 'state' in changes and changes['state'] not in STATES or 'kind' in changes and changes['kind'] not in KINDS:
        raise ValueError('a hop kind or state this ledger does not know')
    with core.file_lock(contract.LOCK_NAME):
        data = _read(changing=True)
        for d in data['hops']:
            if d.get('id') == hop_id:
                d.update(changes)
                h = _hop(d)
                if h is None:
                    raise ValueError('that change does not make a hop')
                _write(data)
                return h
    raise ProviderError('not_found', 'That move is not in the record any more.')


def hops(folder: Optional[str] = None, limit: int = 50) -> List[Hop]:
    """Hops, newest first (in this folder when given)."""
    out = []
    for d in reversed(_read()['hops']):
        if folder is not None and not _same_folder(d.get('folder'), folder):
            continue
        h = _hop(d)
        if h is not None:
            out.append(h)
            if len(out) >= limit:
                break
    return out


def _open_continues(folder: str):
    for d in reversed(_read()['hops']):
        if d.get('kind') == 'continue' and d.get('state') not in CLOSED and _same_folder(d.get('folder'), folder):
            h = _hop(d)
            if h is not None:
                yield h


def open_continue(folder: str, to_chat: Optional[str] = None) -> Optional[Hop]:
    """The newest `continue` in this folder not yet returned or failed; with to_chat, the one that led to that chat."""
    for h in _open_continues(folder):
        if to_chat is None or h.to_chat == to_chat:
            return h
    return None


def continued_from(folder: str, from_chat: str) -> Optional[Hop]:
    """The newest open `continue` that left this chat (the "already carried" check)."""
    for h in _open_continues(folder):
        if h.from_chat == from_chat:
            return h
    return None


def prune(now: float) -> int:
    """Drop hops older than the notes they point at (contract.NOTE_KEEP_DAYS), and tasks left with no hop that are as
    old. Returns how many hops went. The notes themselves are handoff.prune's."""
    cutoff = now - KEEP_S
    with core.file_lock(contract.LOCK_NAME):
        data = _read(changing=True)
        kept = [d for d in data['hops'] if isinstance(d.get('at'), (int, float)) and d['at'] >= cutoff]
        gone = len(data['hops']) - len(kept)
        used = {d.get('task') for d in kept}
        tasks = {k: v for k, v in data['tasks'].items()
                 if k in used or not isinstance(v.get('made_at'), (int, float)) or v['made_at'] >= cutoff}
        if gone or len(tasks) != len(data['tasks']):
            data['hops'], data['tasks'] = kept, tasks
            _write(data)
    return gone
