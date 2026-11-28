"""The person's routes: for each kind of job, which providers to try in turn, and the model and effort to ask each for.

A route is a kind (kinds.KINDS) and an ordered list of choices. A choice is written as words a person can type:
`codex`, `claude:sonnet`, `claude:sonnet:high` (a provider, then a model, then an effort; each model and effort as
the list of models knows them, catalog.py). A kind with no route follows the person's order (`switchboard order`)
and each app's own default model. A fresh install has no routes.

The table is one file in the data folder (ROUTES_FILE), written whole under the providers lock, mode 0600, the way
choices.py writes its own; a file that is there and cannot be read is never written over. An empty table is no file.

Presets ship beside this module (route_presets.json), each off until the person turns one on. Each row of a preset
says where it was read and on which day, and names only providers and models the list knows; a preset with a row that
does not is refused. Turning a preset on fills only kinds the person has no route for: a row the person set is never
replaced. Turning it off takes out exactly what it wrote, except a kind the person set or reset since, which stays as
the person left it. A row the preset filled names a model inside each app and never decides between the apps: it is
tried in the person's own order (route_for), while a row the person typed is tried in the order they typed.
"""
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from .. import core
from . import contract, words
from .job import RouteChoice
from .kinds import KINDS

ROUTES_FILE = 'providers/routes.json'
PRESETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'route_presets.json')
_PART_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}\Z')
_DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\Z')


class RouteError(ValueError):
    """A route, a choice or a preset that cannot be used: str() is one plain sentence for the person."""


@dataclass(frozen=True)
class Table:
    """The routes by kind (only kinds that have one), the preset that is on, if any, and the kinds whose row that
    preset filled (and the person has not changed since)."""
    routes: Mapping[str, Tuple[RouteChoice, ...]]
    preset: Optional[str] = None
    from_preset: FrozenSet[str] = frozenset()

    def get(self, kind: str) -> Tuple[RouteChoice, ...]:
        return tuple(self.routes.get(kind, ()))

    def route_for(self, kind: str, order: Sequence[str]) -> Tuple[RouteChoice, ...]:
        """The choices to try for kind, in turn: a row the person set, in the order they typed it; a row the preset
        filled, in the person's order (each provider with the model and effort the preset names for it), and a
        provider not in that order after, as the preset lists them."""
        got = self.get(kind)
        if kind not in self.from_preset:
            return got
        rank = {p: i for i, p in enumerate(order)}
        return tuple(sorted(got, key=lambda c: rank.get(c.provider, len(rank))))


def _catalog(given):
    if given is not None:
        return given
    from . import catalog
    return catalog


def _wrap(fn, *args):
    """A catalog's refusal as a route's: the same sentence."""
    from .catalog import CatalogError
    try:
        return fn(*args)
    except CatalogError as e:
        raise RouteError(str(e))


def check_kind(kind) -> str:
    if kind not in KINDS:
        raise RouteError(words.UNKNOWN_KIND.format(word=words.one_line(kind, 40), kinds=', '.join(KINDS)))
    return kind


def parse(word, catalog=None) -> RouteChoice:
    """One choice from the words a person typed, its model and effort as the list spells them; RouteError with one
    sentence (listing the known ones) for anything the list does not know."""
    if isinstance(word, RouteChoice):
        parts = [word.provider] + [x for x in (word.model, word.effort) if x]
        if word.effort and not word.model:
            parts = []
    else:
        parts = str(word or '').strip().split(':')
    if not 1 <= len(parts) <= 3 or not all(isinstance(p, str) and _PART_RE.match(p) for p in parts):
        raise RouteError(words.ROUTE_WORD.format(word=words.one_line(word, 60)))
    cat = _catalog(catalog)
    from . import ids
    pid = parts[0].lower()
    known = [p for p in _wrap(cat.providers) if p in ids()]
    if pid not in known:
        raise RouteError(words.UNKNOWN_PROVIDER.format(word=words.one_line(parts[0], 32), ids=', '.join(known)))
    model = _wrap(cat.find_model, pid, parts[1]).id if len(parts) > 1 else None
    effort = _wrap(cat.find_effort, pid, parts[2]) if len(parts) > 2 else None
    return RouteChoice(pid, model, effort)


def word_of(choice: RouteChoice) -> str:
    """A choice as a person types it: codex, claude:sonnet, claude:sonnet:high (each part on one line, so a value
    from a file can never move the screen)."""
    return ':'.join(words.one_line(x, 64) for x in (choice.provider, choice.model,
                                                    choice.effort if choice.model else None) if x)


def check_choices(kind: str, given: Sequence, catalog=None) -> Tuple[RouteChoice, ...]:
    """A whole route for kind: at least one choice, each provider once."""
    check_kind(kind)
    out = tuple(parse(w, catalog) for w in given)
    if not out:
        raise RouteError(words.ROUTE_EMPTY.format(kind=kind))
    seen = set()
    for c in out:
        if c.provider in seen:
            from . import name_of   # the adapter's own name: each adapter holds it
            raise RouteError(words.ROUTE_TWICE.format(name=name_of(c.provider, c.provider)))
        seen.add(c.provider)
    return out


# ---------- the file ----------

def _path() -> str:
    return core.data_path(ROUTES_FILE)


def _row(c: RouteChoice) -> dict:
    row = {'provider': c.provider}
    if c.model:
        row['model'] = c.model
        if c.effort:
            row['effort'] = c.effort
    return row


def _choice(row) -> Optional[RouteChoice]:
    if not isinstance(row, dict) or not isinstance(row.get('provider'), str):
        return None
    model, effort = row.get('model'), row.get('effort')
    return RouteChoice(row['provider'], model if isinstance(model, str) else None,
                       effort if isinstance(effort, str) and isinstance(model, str) else None)


def _rows_of(data: dict) -> Dict[str, list]:
    raw = data.get('routes') if isinstance(data.get('routes'), dict) else {}
    return {k: v for k, v in raw.items() if k in KINDS and isinstance(v, list) and v}


def _bad() -> RouteError:
    return RouteError(words.ROUTES_BAD.format(path=_path()))


def _check_rows(kind, rows, catalog) -> None:
    """One kind's rows as set_kind writes them: a kind there is, at least one choice, each provider once, each
    provider, model and effort spelt as the list of models spells it."""
    if kind not in KINDS or not isinstance(rows, list) or not rows:
        raise _bad()
    seen = set()
    for row in rows:
        c = _choice(row)
        if c is None or set(row) - {'provider', 'model', 'effort'} or c.provider in seen or \
                not all(isinstance(x, str) and _PART_RE.match(x) for x in (c.provider, c.model, c.effort) if x):
            raise _bad()
        seen.add(c.provider)
        try:
            if parse(word_of(c), catalog) != c:
                raise _bad()
        except RouteError:
            raise _bad()


def check_file(data, catalog=None) -> None:
    """RouteError (ROUTES_BAD, naming the file) unless every part of a routes file is one this Switchboard knows:
    kinds, providers, models and efforts as the list of models has them, and a preset the shipped presets have. A
    list of models that cannot be used says so in its own sentence first."""
    cat = _catalog(catalog)
    _wrap(cat.providers)
    if not isinstance(data, dict) or not isinstance(data.get('routes', {}), dict):
        raise _bad()
    for kind, rows in data.get('routes', {}).items():
        _check_rows(kind, rows, cat)
    record = data.get('preset')
    if record is None:
        return
    if not isinstance(record, dict) or not isinstance(record.get('name'), str) or \
            record['name'] not in presets(cat) or not isinstance(record.get('wrote', {}), dict) or \
            not isinstance(record.get('before', {}), dict):
        raise _bad()
    for kind, rows in record.get('wrote', {}).items():
        _check_rows(kind, rows, cat)
    for kind, rows in record.get('before', {}).items():
        if rows is not None:
            _check_rows(kind, rows, cat)


def _read_file() -> dict:
    """The file's object, {} when there is none; ROUTES_BAD when it is there and cannot be read."""
    try:
        with open(_path(), encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError):
        raise _bad()


def load(catalog=None) -> Table:
    """The table as it is now, checked (check_file): a file that cannot be read, or names something not known, is
    RouteError with one sentence that names it, never "no routes"."""
    data = _read_file()
    check_file(data, catalog)
    rows = _rows_of(data)
    table = {}
    for kind in KINDS:
        got = tuple(c for c in (_choice(r) for r in rows.get(kind, ())) if c is not None)
        if got:
            table[kind] = got
    record = data.get('preset') if isinstance(data.get('preset'), dict) else {}
    name = record.get('name') if isinstance(record.get('name'), str) else None
    wrote = record.get('wrote') if isinstance(record.get('wrote'), dict) else {}
    filled = frozenset(k for k, r in wrote.items() if name and k in table and rows.get(k) == r)
    return Table(table, name, filled)


@contextmanager
def _changing(check=True, catalog=None):
    """Read, change and write the whole file under the providers lock: {'routes': {kind: [row]}, 'preset': record or
    None}. Written in one order every time, so the same table is the same bytes; an empty table with no preset on is
    no file. An exception in the block writes nothing. check: a file naming something not known is refused too
    (ROUTES_BAD), so a change never keeps it."""
    from .choices import read_whole
    with core.file_lock(contract.LOCK_NAME):
        try:
            data = read_whole(_path(), words.ROUTES_UNREADABLE)
        except contract.ProviderError as e:
            raise RouteError(str(e))
        if check:
            check_file(data, catalog)
        record = data.get('preset') if isinstance(data.get('preset'), dict) else None
        state = {'routes': _rows_of(data), 'preset': record}
        yield state
        rows = {k: state['routes'][k] for k in KINDS if state['routes'].get(k)}
        if not rows and not state['preset']:
            try:
                os.remove(_path())
            except FileNotFoundError:
                pass
            return
        doc = {'v': 1, 'routes': rows}
        if state['preset']:
            doc['preset'] = state['preset']
        core.write_json(_path(), doc, 0o600)


def _forget(state: dict, kind: str) -> None:
    """A kind the person changes is theirs from now on: a preset that is on no longer puts anything back for it."""
    record = state['preset']
    if record:
        for key in ('before', 'wrote'):
            if isinstance(record.get(key), dict):
                record[key].pop(kind, None)


def set_kind(kind: str, choices: Sequence, catalog=None) -> Tuple[RouteChoice, ...]:
    """This kind's route, replaced whole; the choices as kept."""
    route = check_choices(kind, choices, catalog)
    with _changing(catalog=catalog) as state:
        state['routes'][kind] = [_row(c) for c in route]
        _forget(state, kind)
    return route


def reset(kind: Optional[str] = None) -> None:
    """No route for this kind (it follows the order), or for every kind; reset of every kind turns a preset off too,
    with nothing to put back."""
    if kind is None:                                # all: the file goes, readable or not (ROUTES_BAD names this)
        with core.file_lock(contract.LOCK_NAME):
            try:
                os.remove(_path())
            except FileNotFoundError:
                pass
        return
    check_kind(kind)
    with _changing(check=False) as state:           # taking a kind out may be what mends the file
        state['routes'].pop(kind, None)
        _forget(state, kind)


# ---------- presets ----------

def check_presets(doc, catalog=None) -> Dict[str, Dict[str, Tuple[RouteChoice, ...]]]:
    """Each preset's rows, checked: every row says where it was read, on which day and what it rests on (rests_on:
    for example the makers' own lines about their models), is for a kind there is, and names providers and models
    the list knows. RouteError with one sentence for the first that does not."""
    if not isinstance(doc, dict) or not isinstance(doc.get('presets'), dict):
        raise RouteError(words.PRESET_ROW.format(name='?', kind='?', why=words.CATALOG_BAD.format(
            where=PRESETS, what=words.CATALOG_WHAT_FILE)))
    out = {}
    for name, preset in doc['presets'].items():
        rows = preset.get('rows') if isinstance(preset, dict) else None
        if not isinstance(name, str) or not _PART_RE.match(name) or not isinstance(rows, dict):
            raise RouteError(words.PRESET_ROW.format(name=words.one_line(name, 40), kind='?', why=words.CATALOG_BAD
                                                     .format(where=PRESETS, what=words.CATALOG_WHAT_FILE)))
        out[name] = {}
        for kind, row in rows.items():
            shown = words.one_line(kind, 40)
            row = row if isinstance(row, dict) else {}
            source, read, rests = row.get('source'), row.get('read'), row.get('rests_on')
            if not isinstance(source, str) or not source.strip() or not isinstance(read, str) or \
                    not _DAY_RE.match(read) or not isinstance(rests, str) or not rests.strip():
                raise RouteError(words.PRESET_NO_SOURCE.format(name=name, kind=shown))
            try:
                out[name][kind] = check_choices(kind, row.get('choices') or (), catalog)
            except RouteError as e:
                raise RouteError(words.PRESET_ROW.format(name=name, kind=shown, why=str(e)))
    return out


def presets(catalog=None) -> Dict[str, Dict[str, Tuple[RouteChoice, ...]]]:
    """The shipped presets, checked (RouteError when one is not fit to use)."""
    try:
        with open(PRESETS, encoding='utf-8') as f:
            doc = json.load(f)
    except (OSError, ValueError):
        doc = None
    return check_presets(doc, catalog)


def _off(state: dict) -> None:
    record = state['preset'] or {}
    before = record.get('before') if isinstance(record.get('before'), dict) else {}
    wrote = record.get('wrote') if isinstance(record.get('wrote'), dict) else {}
    for kind, rows in wrote.items():
        if state['routes'].get(kind) != rows:
            continue
        if before.get(kind):
            state['routes'][kind] = before[kind]
        else:
            state['routes'].pop(kind, None)
    state['preset'] = None


def preset(name: Optional[str], catalog=None) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Turn a preset on (by name), or off (None): (the kinds it filled, the kinds of its rows it left alone because
    the person has a route of their own for them), each in the kinds' order; ((), ()) for off. On again is no change
    and says the same; another preset is turned off first."""
    rows: Dict[str, Tuple[RouteChoice, ...]] = {}
    if name is not None:
        known = presets(catalog)
        if name not in known:
            raise RouteError(words.UNKNOWN_PRESET.format(word=words.one_line(name, 40), names=', '.join(known)))
        rows = known[name]
    with _changing(catalog=catalog) as state:
        record = state['preset'] or {}
        on = record.get('name')
        if on is not None and on == name:
            wrote = record.get('wrote') if isinstance(record.get('wrote'), dict) else {}
            return (tuple(k for k in KINDS if k in wrote),
                    tuple(k for k in KINDS if k in rows and k not in wrote))
        if on is not None:
            _off(state)
        if name is None:
            return (), ()
        wrote: Dict[str, List[dict]] = {}
        kept: List[str] = []
        for kind in KINDS:
            if kind not in rows:
                continue
            if state['routes'].get(kind):           # the person's own route stays
                kept.append(kind)
                continue
            wrote[kind] = [_row(c) for c in rows[kind]]
            state['routes'][kind] = wrote[kind]
        state['preset'] = {'name': name, 'wrote': wrote}
    return tuple(wrote), tuple(kept)
