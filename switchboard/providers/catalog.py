"""What each provider's app can be asked for when Switchboard starts it for a person: its models (the word its own
model option takes, the name people use, the maker's own line about it), the efforts it takes, and how each is given on
its command line, from which version on.

The list ships beside this module (catalog.json), filled only from each app's own help and each maker's own pages:
every entry says where it was read (`source`) and on which day (`read`), and a file holding an entry without them is
refused whole, with one plain sentence, never half used. A person's own file of the same shape in the data folder
(OWN_FILE) is checked the same way and merged over it: a model of the same id is replaced, a new one is added, its
efforts replace the shipped ones. How an app is started (`start`) comes only from the shipped list.

start_args() gives the extra words for one start, and gives none for a version older than the one an option was read
in, or a version not known: never a guess. Nothing here runs a program or reads anything but the two files.
"""
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .. import core
from . import words
from .run import version_key

SHIPPED = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'catalog.json')
SHIPPED_WHERE = words.CATALOG_SHIPPED
OWN_FILE = 'providers/catalog.json'     # the person's own, under the data folder
STARTS = ('model', 'effort')            # what a start can be given

# A model word goes into an argument list right after its option: it never begins with '-', and holds no blank.
MODEL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}\Z')
EFFORT_RE = re.compile(r'^[a-z][a-z0-9]{0,15}\Z')
DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\Z')
ARG_RE = re.compile(r'^[A-Za-z0-9_\-=."{}]{1,80}\Z')
NAME_MAX, SAYS_MAX, SOURCE_MAX = 60, 300, 600


class CatalogError(ValueError):
    """A list that cannot be used, or a word it does not know: str() is one plain sentence for the person."""


@dataclass(frozen=True)
class Model:
    id: str                     # the word the app's own model option takes
    name: str                   # what a person calls it
    says: Optional[str]         # the maker's own line about it
    source: str
    read: str


@dataclass(frozen=True)
class Start:
    """How an app is given a model or an effort when it starts: args, with '{model}' or '{effort}' in one of them,
    from version `since` on (the version whose help or docs it was read in)."""
    args: Tuple[str, ...]
    since: str
    source: str
    read: str


@dataclass(frozen=True)
class Entry:
    provider: str
    models: Tuple[Model, ...] = ()
    efforts: Tuple[str, ...] = ()
    start: Tuple[Tuple[str, Optional[Start]], ...] = ()


def _name(pid: str) -> str:
    from . import name_of           # the adapter's own name: each adapter holds it
    return name_of(pid)


def _known_ids() -> Tuple[str, ...]:
    from . import ids
    return ids()


def _sourced(item: dict, where: str, what: str) -> Tuple[str, str]:
    source, read = item.get('source'), item.get('read')
    if not isinstance(source, str) or not source.strip() or read is None or read == '':
        raise CatalogError(words.CATALOG_NO_SOURCE.format(where=where, what=what))
    if len(source) > SOURCE_MAX or not isinstance(read, str) or not DAY_RE.match(read):
        raise CatalogError(words.CATALOG_BAD.format(where=where, what=what))
    return source.strip(), read


def _models(raw, pid: str, where: str) -> Tuple[Model, ...]:
    if not isinstance(raw, list):
        raise CatalogError(words.CATALOG_BAD.format(where=where, what=words.CATALOG_WHAT_ENTRY.format(name=_name(pid))))
    out = []
    for m in raw:
        mid = m.get('id') if isinstance(m, dict) else None
        what = words.CATALOG_WHAT_MODEL.format(model=words.one_line(mid if isinstance(mid, str) else '?', 40),
                                               name=_name(pid))
        if not isinstance(m, dict) or not isinstance(mid, str) or not MODEL_RE.match(mid):
            raise CatalogError(words.CATALOG_BAD.format(where=where, what=what))
        source, read = _sourced(m, where, what)
        name, says = m.get('name'), m.get('says')
        if not isinstance(name, str) or not name.strip() or len(name) > NAME_MAX or \
                not (says is None or (isinstance(says, str) and len(says) <= SAYS_MAX)):
            raise CatalogError(words.CATALOG_BAD.format(where=where, what=what))
        out.append(Model(mid, ' '.join(name.split()), says, source, read))
    return tuple(out)


def _efforts(raw, pid: str, where: str) -> Tuple[str, ...]:
    what = words.CATALOG_WHAT_EFFORTS.format(name=_name(pid))
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise CatalogError(words.CATALOG_BAD.format(where=where, what=what))
    _sourced(raw, where, what)
    values = raw.get('values')
    if not isinstance(values, list) or not all(isinstance(v, str) and EFFORT_RE.match(v) for v in values):
        raise CatalogError(words.CATALOG_BAD.format(where=where, what=what))
    if len(set(values)) != len(values):
        raise CatalogError(words.CATALOG_TWICE.format(where=where, what=what))
    return tuple(values)


def _start(raw, pid: str, given: str, where: str) -> Optional[Start]:
    what = words.CATALOG_WHAT_START.format(name=_name(pid), what=words.CATALOG_GIVEN[given])
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CatalogError(words.CATALOG_BAD.format(where=where, what=what))
    source, read = _sourced(raw, where, what)
    args, since = raw.get('args'), raw.get('since')
    place = '{%s}' % given
    if not isinstance(args, list) or not 1 <= len(args) <= 4 or \
            not all(isinstance(a, str) and ARG_RE.match(a) for a in args) or \
            sum(place in a for a in args) != 1 or any(a.replace(place, '').count('{') for a in args) or \
            not isinstance(since, str) or not version_key(since):
        raise CatalogError(words.CATALOG_BAD.format(where=where, what=what))
    return Start(tuple(args), since, source, read)


def check(doc, where: str) -> Dict[str, Entry]:
    """The entries of one file in the shipped list's shape, each checked; CatalogError with one sentence for the
    first thing wrong."""
    return {pid: entry for pid, (entry, _) in _check(doc, where, own=False).items()}


def _check(doc, where: str, own: bool) -> Dict[str, Tuple[Entry, bool]]:
    """(entry, whether it names its efforts) by provider. own: the person's file, which may hold models and efforts
    and never `start`."""
    if not isinstance(doc, dict) or doc.get('v') != 1 or not isinstance(doc.get('providers'), dict):
        raise CatalogError(words.CATALOG_BAD.format(where=where, what=words.CATALOG_WHAT_FILE))
    known = _known_ids()
    out = {}
    for pid, raw in doc['providers'].items():
        if pid not in known:
            raise CatalogError(words.CATALOG_PROVIDER.format(where=where, word=words.one_line(pid, 32),
                                                             ids=', '.join(known)))
        if not isinstance(raw, dict):
            raise CatalogError(words.CATALOG_BAD.format(where=where, what=words.CATALOG_WHAT_ENTRY.format(
                name=_name(pid))))
        if own and 'start' in raw:
            raise CatalogError(words.CATALOG_OWN_START.format(where=where, name=_name(pid)))
        models = _models(raw.get('models', []), pid, where)
        start = raw.get('start', {}) if not own else {}
        if not isinstance(start, dict):
            raise CatalogError(words.CATALOG_BAD.format(where=where, what=words.CATALOG_WHAT_ENTRY.format(
                name=_name(pid))))
        entry = Entry(pid, models, _efforts(raw.get('efforts'), pid, where),
                      tuple((g, _start(start.get(g), pid, g, where)) for g in STARTS))
        _no_twice(entry, where)
        out[pid] = (entry, 'efforts' in raw)
    return out


def _no_twice(entry: Entry, where: str) -> None:
    """No two models share an id or a name (case ignored), so a word a person types finds one model."""
    seen = set()
    for m in entry.models:
        keys = {m.id.lower(), m.name.lower()}
        if keys & seen:
            raise CatalogError(words.CATALOG_TWICE.format(where=where, what=words.CATALOG_WHAT_MODEL.format(
                model=words.one_line(m.id, 40), name=_name(entry.provider))))
        seen |= keys


def _read(path: str, where: str):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError):
        raise CatalogError(words.CATALOG_NOT_JSON.format(where=where))


def load(own: bool = True) -> Dict[str, Entry]:
    """Every provider's entry: the shipped list, with the person's own file merged over it (own=False: the shipped
    list alone). Read each time it is asked, so a change to either file counts at once."""
    entries = check(_read(SHIPPED, SHIPPED_WHERE), SHIPPED_WHERE)
    path = core.data_path(OWN_FILE)
    doc = _read(path, path) if own else None
    if doc is None:
        return entries
    for pid, (mine, has_efforts) in _check(doc, path, own=True).items():
        base = entries.get(pid, Entry(pid))
        models = list(base.models)
        for m in mine.models:
            at = next((i for i, x in enumerate(models) if x.id.lower() == m.id.lower()), None)
            if at is None:
                models.append(m)
            else:
                models[at] = m
        merged = Entry(pid, tuple(models), mine.efforts if has_efforts else base.efforts, base.start)
        _no_twice(merged, path)
        entries[pid] = merged
    return entries


def _entry(pid: str, own: bool = True) -> Entry:
    entries = load(own)
    if pid not in entries:
        raise CatalogError(words.UNKNOWN_PROVIDER.format(word=words.one_line(pid, 32), ids=', '.join(sorted(entries))))
    return entries[pid]


def providers() -> Tuple[str, ...]:
    """The providers the list knows, by id."""
    return tuple(sorted(load()))


def models(pid: str, own: bool = True) -> Tuple[Model, ...]:
    return _entry(pid, own).models


def efforts(pid: str, own: bool = True) -> Tuple[str, ...]:
    return _entry(pid, own).efforts


def start_of(pid: str, given: str) -> Optional[Start]:
    """How pid's app is given a model or an effort when it starts, or None when no documented way was read."""
    return dict(_entry(pid).start).get(given)


def find_model(pid: str, word: str) -> Model:
    """The model a person named, by its id or its name, case and blanks around it ignored; CatalogError with the
    known ones listed when there is none."""
    entry = _entry(pid)
    want = ' '.join(str(word or '').split()).lower()
    for key in ('id', 'name'):
        for m in entry.models:
            if getattr(m, key).lower() == want:
                return m
    known = ', '.join(m.id for m in entry.models) or words.NONE_KNOWN
    raise CatalogError(words.UNKNOWN_MODEL.format(name=_name(pid), word=words.one_line(word, 40), known=known))


def find_effort(pid: str, word: str) -> str:
    """The effort a person named, case ignored; CatalogError with the known ones listed when there is none."""
    entry = _entry(pid)
    want = str(word or '').strip().lower()
    if not entry.efforts:
        raise CatalogError(words.NO_EFFORTS.format(name=_name(pid)))
    if want in entry.efforts:
        return want
    raise CatalogError(words.UNKNOWN_EFFORT.format(name=_name(pid), word=words.one_line(word, 40),
                                                   known=', '.join(entry.efforts)))


def takes(pid: str, given: str, version: Optional[str]) -> bool:
    """Does this version of pid's app take a model (or an effort) when it starts? False for a version not known."""
    start = start_of(pid, given) if given in STARTS else None
    have = version_key(version)
    return start is not None and bool(have) and have >= version_key(start.since)


def start_args(pid: str, model: Optional[str], effort: Optional[str], version: Optional[str]) -> List[str]:
    """The extra words for starting pid's app (that version) on a model and an effort: each one only when the version
    takes it, so [] for a version before the option was read, or one not known. model and effort are checked against
    the list first (CatalogError for one it does not know)."""
    out: List[str] = []
    for given, value in (('model', find_model(pid, model).id if model else None),
                         ('effort', find_effort(pid, effort) if effort else None)):
        if value is None or not takes(pid, given, version):
            continue
        out += [a.replace('{%s}' % given, value) for a in start_of(pid, given).args]
    return out
