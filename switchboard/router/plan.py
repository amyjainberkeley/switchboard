"""The plan: one small file (<data folder>/router/plan.json) that every app's adapter reads, and nothing else.

build() makes it from what Switchboard already knows (pure: the time, the routes, the order and the room are given).
write() puts it in place whole or not at all, mode 0600, at most MAX_BYTES. read() gives it back, or None for a
missing file, one that cannot be read, one too big, or one that is not an object; it never raises. gather() asks the
modules that already know each part (the routes table, the person's order, each provider's room and numbers, the
switches) and calls build().

A plan never holds an email, an account id, a name, a file path or a token: an app is named by its provider id, the
account in use by its number in the list, and every other value is a word from a fixed table or the list of models.
write() refuses a plan with a string that holds "@", "/" or "\\".
"""
import json
import math
import re
from typing import Mapping, Optional

from .. import core
from ..providers import contract
from ..providers import kinds as _kinds
from . import settings as _settings
from . import words

FILE = 'router/plan.json'
V = 1
MAX_BYTES = 16 * 1024
GOOD_FOR_S = 180            # a plan is used for three minutes after it was made; Switchboard writes a new one sooner
HAND_MAX_S = 900            # the longest a hand-over may run, as `switchboard hand` allows a job
# Helper types whose kind is known without reading any words: Claude Code's Explore and Plan, Codex's explorer.
TYPES = {'Explore': 'find', 'Plan': 'plan', 'explorer': 'find'}
LINE_KEYS = {300: 'five_hour', 10080: 'week'}       # a window's length in minutes, as the plan names it
_LINE_LABELS = {'5-hour': 'five_hour', 'weekly': 'week'}
_WORD_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}\Z')     # a model or effort as the list spells it
_SLOT_RE = re.compile(r'^[1-9][0-9]{0,2}\Z')
_NOT_PRIVATE = re.compile(r'[@/\\]')
_CANNOT = {contract.NOT_ADDED: 'not_added', contract.NOT_INSTALLED: 'not_installed',
           contract.SIGNED_OUT: 'signed_out'}
_WHY_NOT = ('not_added', 'not_installed', 'signed_out', 'too_old', 'never')


class PlanError(ValueError):
    """A plan write() will not put in place (too big, or a string that could be private). Nothing was written."""


def path() -> str:
    return core.data_path(FILE)


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _plain(x):
    return int(x) if float(x).is_integer() else x


def _word(x) -> Optional[str]:
    return x if isinstance(x, str) and _WORD_RE.match(x) else None


def _id(x) -> bool:
    return isinstance(x, str) and bool(contract.ID_RE.match(x))


def kinds_block() -> dict:
    """The word table of providers/kinds.py as data, so every adapter reads the kind of a piece of work the same way."""
    return {'order': list(_kinds.KINDS), 'light': _kinds.LIGHT, 'cut': _kinds.CUT,
            'table': {k: list(_kinds.TABLE[k]) for k in _kinds.KINDS if k in _kinds.TABLE}}


def _agent(pid, added, room, at_lines, lines, in_use) -> dict:
    a = {'can': True, 'why_not': None, 'room': 'unknown', 'back_at': None, 'at_lines': at_lines.get(pid) is True}
    state = room.state if isinstance(room, contract.Room) else room
    if not added:
        state = contract.NOT_ADDED
    if state in _CANNOT or state in _WHY_NOT:
        a.update(can=False, why_not=_CANNOT.get(state, state))
    elif state == contract.ROOM:
        a['room'] = 'room'
    elif state == contract.OUT:
        a['room'] = 'out'
        back = room.back_at if isinstance(room, contract.Room) else None
        a['back_at'] = _plain(back) if _num(back) else None
    got = lines.get(pid)
    line = {k: _plain(got[k]) for k in ('five_hour', 'week')
            if isinstance(got, Mapping) and _num(got.get(k)) and 0 <= got[k] <= 100}
    if line:
        a['line'] = line
    slot = in_use.get(pid)
    slot = str(slot) if isinstance(slot, (int, str)) and not isinstance(slot, bool) else None
    if slot is not None and _SLOT_RE.match(slot):
        a['in_use'] = slot
    return a


def _routes(table, order):
    routes, why = {}, {}
    if table is None:
        return routes, why
    preset = getattr(table, 'from_preset', frozenset())
    for kind in _kinds.KINDS:
        choices = []
        for c in table.route_for(kind, order):
            model, effort = _word(c.model), _word(c.effort)
            if not _id(c.provider) or (c.model is not None and model is None) or \
                    (c.effort is not None and effort is None):
                continue                    # a word the plan cannot carry: the choice is left out, not changed
            choices.append({'agent': c.provider, 'model': model, 'effort': effort})
        if choices:
            routes[kind] = choices
            why[kind] = (words.PRESET_WHY if kind in preset else words.ROUTE_WHY)[kind]
    return routes, why


def _models(got) -> Optional[dict]:
    """A default's models by agent id, keeping only ids and words the plan can carry; None when none is left."""
    if not isinstance(got, Mapping):
        return None
    out = {a: _word(m) for a, m in got.items() if _id(a) and _word(m) is not None}
    return out or None


def _defaults(s) -> dict:
    """The defaults block: for each place work is started, the effort word and the models by agent the person set,
    each None when unset or when it is not something the plan can carry. A model is given per agent
    ({"claude": "haiku"}) so an adapter never applies another agent's model name."""
    out = {}
    for where in _settings.WHERE:
        out[where] = {'model': _models(_settings.get(s, 'defaults.%s.model' % where)),
                      'effort': _word(_settings.get(s, 'defaults.%s.effort' % where))}
    return out


def build(now, table, order, rooms, at_lines, lines, in_use, settings, version) -> dict:
    """The plan, in the shape every adapter reads. now: epoch seconds. table: the routes (providers/routes.py Table),
    or None for none. order: the provider ids the person added, in their order. rooms: each provider's
    contract.Room, or one of 'too_old', 'never' when it cannot take work for that reason; a provider not in order is
    not added. at_lines: provider id -> every account at the person's lines. lines: provider id -> {'five_hour',
    'week'} percent lines for the account in use. in_use: provider id -> the list number of the account in use.
    settings: the switches and defaults (settings.read()), all off and unset when None. version: 'switchboard 0.2.0 (abc1234)'."""
    seen, ids = set(), []
    for p in order or ():
        if _id(p) and p not in seen:
            seen.add(p)
            ids.append(p)
    added = list(ids)
    rooms = dict(rooms or {})
    ids += sorted(p for p in rooms if _id(p) and p not in seen)
    at_lines, lines, in_use = dict(at_lines or {}), dict(lines or {}), dict(in_use or {})
    routes, why = _routes(table, added)
    s = settings if isinstance(settings, dict) else _settings.defaults()

    def on(name):
        return _settings.switch(s, name)

    by = version if isinstance(version, str) and not _NOT_PRIVATE.search(version) else 'switchboard'
    return {
        'v': V, 'made_at': float(now), 'good_for_s': GOOD_FOR_S, 'on': on('on'), 'dry': on('dry'),
        'by': by[:80], 'order': added,
        'agents': {p: _agent(p, p in seen, rooms.get(p), at_lines, lines, in_use) for p in ids},
        'routes': routes, 'why': why,
        'defaults': _defaults(s),
        'helpers': {'force': on('helpers.force')},
        'chat': {'effort': on('chat.effort'), 'model_on_first_turn': on('chat.model_on_first_turn')},
        'hand': {'on': on('hand.on'), 'write': on('hand.write'), 'max_s': HAND_MAX_S},
        'carry_on': on('carry_on'),
        'kinds': kinds_block(),
        'types': dict(TYPES),
    }


# ---------- the file ----------

def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _strings(v)


def encode(plan: dict) -> bytes:
    """The plan's bytes, the same for the same plan."""
    return json.dumps(plan, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def write(plan: dict) -> int:
    """Put the plan in place whole or not at all, mode 0600; its size in bytes. PlanError when it is over MAX_BYTES
    or a string in it holds "@", "/" or "\\" (nothing is written); OSError as core.write_bytes raises it."""
    if not isinstance(plan, dict):
        raise PlanError('a plan is an object')
    if any(_NOT_PRIVATE.search(s) for s in _strings(plan)):
        raise PlanError('a plan holds no email and no path')
    data = encode(plan)
    if len(data) > MAX_BYTES:
        raise PlanError('a plan is at most %d bytes' % MAX_BYTES)
    core.write_bytes(path(), data, 0o600)
    return len(data)


def _no_constants(word):
    raise ValueError(word)


def read() -> Optional[dict]:
    """The plan in place, or None: no file, a file that cannot be read, over MAX_BYTES, or not an object."""
    try:
        with open(path(), 'rb') as f:
            data = f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return None
        got = json.loads(data.decode('utf-8'), parse_constant=_no_constants)
    except Exception:  # noqa: BLE001 - missing, unreadable, not UTF-8, not JSON: no plan
        return None
    return got if isinstance(got, dict) else None


# ---------- what Switchboard already knows ----------

def _lines_of(seat_key, limits) -> dict:
    out = {}
    for lim in limits:
        if lim.seat != seat_key:
            continue
        for w in lim.windows:
            key = LINE_KEYS.get(w.length_min) or _LINE_LABELS.get(w.label)
            if key and key not in out and _num(w.line):
                out[key] = w.line
    return out


def gather(now, kit=None) -> dict:
    """The plan for now, from the modules that already know each part: the routes table (providers/routes.py), the
    person's order (choices.py), each provider's room (place.py through the providers' own context, as the list of
    providers works it out), whether every account is at the person's lines (out by place.py's rule), the lines and
    the number of the account in use (the provider's own seats and numbers), and the switches (settings.py). kit: the
    providers' Kit (tests give one of stand-ins). A part that cannot be read is left out, never guessed."""
    from ..providers import cli, ids as provider_ids, view, words as provider_words
    ctx = cli._Ctx(kit if kit is not None else cli.default_kit())
    ctx.now = now
    routes_module = ctx.kit.routes
    if routes_module is None:
        from ..providers import routes as routes_module
    try:
        table = routes_module.load(ctx.kit.catalog)
    except Exception as e:  # noqa: BLE001 - routes that cannot be read are no routes in the plan
        core.log('router plan: routes %s' % type(e).__name__)
        table = None
    order = [p for p in ctx.order() if _id(p)]
    rooms, at_lines, lines, in_use = {}, {}, {}, {}
    for pid in provider_ids():
        try:
            if ctx.prov(pid) is None:
                continue
            room = ctx.room(pid)
            rooms[pid] = room
            if room.state in _CANNOT:
                continue
            why = view.fit_why(ctx.found(pid))
            if why == provider_words.STATE_TOO_OLD:
                rooms[pid] = 'too_old'
            elif why == provider_words.STATE_NO_COPY:
                rooms[pid] = 'not_installed'
            at_lines[pid] = room.state == contract.OUT
            seats = ctx.seats(pid)
            mine = [i for i, s in enumerate(seats) if s.in_use] or ([0] if len(seats) == 1 else [])
            if not mine:
                continue
            try:
                lims = ctx.limits(pid)
            except contract.ProviderError:
                lims = ()
            lines[pid] = _lines_of(seats[mine[0]].key, lims)
            if not ctx.one_login(pid):
                in_use[pid] = mine[0] + 1
        except Exception as e:  # noqa: BLE001 - one provider's failure leaves only its own part out
            core.log('router plan: %s %s' % (pid, type(e).__name__))
            rooms[pid] = contract.Room(pid, contract.UNKNOWN)
    return build(now, table, order, rooms, at_lines, lines, in_use, _settings.read(),
                 'switchboard ' + core.version_text())
