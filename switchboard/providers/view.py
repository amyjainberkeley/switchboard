"""Every provider on one board, for the page, `switchboard status`, the menu bar and doctor: built from files only.

view() asks each provider's adapter what `switchboard providers` asks it (which program is here, its accounts and its
saved numbers, how much room it has, through place.room_of), with one difference: the adapters run under a Reach
that can start nothing. Its run and open_url answer that the program is missing, its pipe and exec refuse, and it is
never a person at a terminal. So a program's version is the one kept in contract.PROGRAMS_FILE (a copy never seen
before has no version yet), numbers are the saved ones (contract.READINGS_FILE, the app's own files, the account
code's readings), nothing is asked of any app, no network is reached and no login file is opened. A provider whose
adapter fails in any way is "not known" on the board; view() itself never raises.

The shape (v 1):
    {"v": 1, "now": seconds, "order": [ids, the person's order], "carry": bool,
     "providers": [{"id", "name", "vendor", "app", "added", "account_list", "installed", "signed_in", "version",
                    "state", "why", "back_at", "read_at", "plan", "count", "with_room", "can_carry", "seats",
                    "error"}],
     "add": [{"id", "name", "app", "version"}]}
"providers" holds the added ones, in the person's order. A provider whose seats are the accounts Switchboard's own
account list keeps ("account_list": true) has no seats here: those accounts are the rows above it on every surface, and
"count" and "with_room" say how many there are and how many have room. Every other provider has one seat per account,
each {"label", "plan", "in_use", "windows": [{"label", "used_pct", "resets_at", "length_min", "line"}]}. "carry": there
is somewhere to carry a chat on (two or more added providers can start on a note, and one of them has room or numbers
not known). "add": a provider on this Mac that is not added yet.

state_words() and summary() say a provider in the words `switchboard providers` uses, at the moment given.
"""
import copy
import dataclasses
from typing import List, Optional

from . import contract, words
from .contract import NOT_ADDED, NOT_INSTALLED, OUT, ROOM, SIGNED_OUT, UNKNOWN, ProviderError, Ran, Reach

V = 1
FRESH_WORDS_S = 3600.0          # "has room" only for numbers read within the hour; older ones say how old (cli's rule)
FIVE_HOUR = '5-hour'            # the window named from a 300-minute length (contract.Window's label)


def _missing(*args, **kwargs):
    return Ran(code=None, missing=True)


def _no_pipe(*args, **kwargs):
    raise ProviderError('refused', 'The board starts no program.')


def _no_exec(*args, **kwargs):
    raise OSError('the board starts no program')


def quiet(reach: Reach) -> Reach:
    """reach, through which no program can start: run and open_url answer that the program is missing, pipe and
    execve refuse, and tty is False (the board is never a person at a terminal)."""
    return dataclasses.replace(reach, run=_missing, pipe=_no_pipe, execve=_no_exec, open_url=_missing,
                               tty=lambda: False)


def view(reach: Optional[Reach] = None, kit=None) -> dict:
    """The board (the shape above). reach: the Reach to read through (made quiet here); kit: a cli.Kit to use whole
    (tests), whose Reach is made quiet too. Never raises."""
    try:
        from . import cli
        if kit is None:
            from . import run
            kit = cli.default_kit(quiet(reach or run.default_reach()))
        else:
            kit = dataclasses.replace(kit, reach=quiet(kit.reach), load=_quiet_load(kit.load))
        ctx = cli._Ctx(kit)
        order = ctx.order()
        added = ctx.added()
        entries, add = [], []
        for pid in order + [p for p in ctx.ids() if p not in order]:
            if pid in added:
                entries.append(_entry(ctx, pid))
            else:
                found = _offer(ctx, pid)
                if found is not None:
                    add.append(found)
        return {'v': V, 'now': ctx.now, 'order': order, 'providers': entries, 'add': add,
                'carry': _carry(entries)}
    except Exception as e:  # noqa: BLE001 - the board is never the reason a surface does not draw
        _log('providers view: %s' % type(e).__name__)
        return {'v': V, 'now': None, 'order': [], 'providers': [], 'add': [], 'carry': False, 'error': 'not known'}


def _quiet_load(load):
    """kit.load, each adapter it gives copied with its Reach made quiet (the kit's own adapter is left as it was)."""
    def quiet_load(pid):
        prov = load(pid)
        if prov is None:
            return None
        prov = copy.copy(prov)
        prov.reach = quiet(prov.reach)
        return prov
    return quiet_load


def _log(text):
    try:
        from switchboard import core
        core.log(text)
    except Exception:  # noqa: BLE001
        pass


def _blank(ctx, pid) -> dict:
    return {'id': pid, 'name': ctx.name(pid), 'vendor': ctx.vendor(pid), 'app': ctx.app(pid), 'added': True,
            'account_list': bool(getattr(ctx.prov(pid), 'account_list', False)), 'installed': None, 'signed_in': None,
            'version': None, 'state': UNKNOWN, 'why': words.STATE_UNKNOWN, 'back_at': None, 'read_at': None,
            'plan': None, 'count': 0, 'with_room': 0, 'can_carry': ctx.can(pid, contract.START), 'seats': [],
            'error': None}


def _entry(ctx, pid) -> dict:
    """One added provider. Anything its adapter raises leaves it "not known", with the error's type name only."""
    out = _blank(ctx, pid)
    try:
        if ctx.prov(pid) is None:
            out['error'] = ctx.kit.load_errors.get(pid) or 'not loaded'
            return out
        found = ctx.found(pid)
        if found is not None:
            out['installed'] = bool(found.installed)
            out['signed_in'] = found.signed_in
            out['version'] = found.program.version if found.program else None
        room = ctx.room(pid)
        out.update(state=room.state, back_at=room.back_at, read_at=room.read_at, why=ctx.state_words(pid, room))
        if room.state in (NOT_INSTALLED, SIGNED_OUT):
            return out
        lims = ctx.limits(pid)
        seats = ctx.seats(pid)
        out['plan'] = ctx.plan_label(pid)
        if out['account_list']:
            out['count'] = len(seats)
            out['with_room'] = sum(1 for s in seats if _seat_room(ctx, pid, lims, s.key) == ROOM)
            return out
        out['seats'] = [_seat(s, lims) for s in seats]
    except Exception as e:  # noqa: BLE001 - one provider that fails is "not known"; the others still show
        out.update(state=UNKNOWN, why=words.STATE_UNKNOWN, back_at=None, error=type(e).__name__)
        _log('providers view %s: %s' % (pid, type(e).__name__))
    return out


def _seat_room(ctx, pid, lims, key) -> str:
    from . import cli
    mine = tuple(lim for lim in lims if lim.seat == key)
    return ctx.kit.place.room_of(pid, ctx.name(pid), mine, ctx.now, cli._fresh(mine)).state


def _seat(seat, lims) -> dict:
    lim = next((x for x in lims if x.seat == seat.key), None)
    return {'label': words.one_line(seat.label, 80), 'plan': seat.plan, 'in_use': bool(seat.in_use),
            'read_at': lim.read_at if lim is not None else None,
            'windows': [{'label': words.one_line(w.label, 30), 'used_pct': w.used_pct, 'resets_at': w.resets_at,
                         'length_min': w.length_min, 'line': w.line} for w in (lim.windows if lim else ())]}


def _offer(ctx, pid) -> Optional[dict]:
    """A provider that is on this Mac (a copy that may be chosen) and not added: how to add it. None otherwise."""
    try:
        if ctx.prov(pid) is None:
            return None
        found = ctx.found(pid)
        if found is None or not found.installed or found.program is None:
            return None
        return {'id': pid, 'name': ctx.name(pid), 'app': ctx.app(pid), 'version': found.program.version}
    except Exception as e:  # noqa: BLE001
        _log('providers view %s: %s' % (pid, type(e).__name__))
        return None


def _carry(entries) -> bool:
    starts = [p for p in entries if p.get('can_carry') and p.get('state') not in (NOT_INSTALLED, SIGNED_OUT)]
    return len(starts) >= 2 and any(p.get('state') in (ROOM, UNKNOWN) for p in starts)


# ---------- the same board in words, at a given moment (the terminal, the menu bar, doctor) ----------


def state_words(p: dict, now: float) -> str:
    """'has room', 'had room when last read 2 hours ago', 'is out until Wed 2:00 AM', 'every account is out until
    4:10 PM', 'numbers not known', 'not signed in', 'not on this Mac'."""
    state = p.get('state')
    if state == ROOM:
        read = p.get('read_at')
        if isinstance(read, (int, float)) and now - read > FRESH_WORDS_S:
            return words.STATE_HAD_ROOM.format(ago=words.ago(now - read))
        return words.STATE_ROOM
    if state == OUT:
        if not isinstance(p.get('back_at'), (int, float)):
            return words.STATE_OUT_NOT_KNOWN
        many = (p.get('count') or len(p.get('seats') or ())) > 1
        return (words.STATE_OUT_ALL if many else words.STATE_OUT).format(when=words.when(p['back_at'], now))
    return {SIGNED_OUT: words.STATE_SIGNED_OUT, NOT_INSTALLED: words.STATE_NOT_INSTALLED,
            NOT_ADDED: words.STATE_NOT_ADDED}.get(state, words.STATE_UNKNOWN)


def window_words(w: dict, now: float, with_refill: bool = True) -> str:
    """'weekly 42%, refills Wed 2:00 AM', 'weekly 42%', '5-hour not known'."""
    label = words.one_line(w.get('label') or '', 30)
    used, resets = w.get('used_pct'), w.get('resets_at')
    if not isinstance(used, (int, float)) or (isinstance(resets, (int, float)) and resets <= now):
        return words.BOARD_WINDOW_UNKNOWN.format(label=label)
    pct = int(round(used))
    if not with_refill or not isinstance(resets, (int, float)):
        return words.BOARD_WINDOW_NO_RESET.format(label=label, pct=pct)
    return words.BOARD_WINDOW.format(label=label, pct=pct, when=words.when(resets, now))


def summary(p: dict, now: float) -> str:
    """One provider on one line after its name: its state, then its plan and each window (a provider whose seats are
    the account list's: the accounts above, said once)."""
    parts = [state_words(p, now)]
    if p.get('account_list'):
        parts.append(words.BOARD_ABOVE)
    else:
        if p.get('plan'):
            parts.append(words.one_line(p['plan'], 60))
        refill = p.get('state') != OUT          # an out provider's state already says until when
        for seat in p.get('seats') or ():
            parts += [window_words(w, now, refill) for w in seat.get('windows') or () if _has_words(w, now)]
    return ' · '.join(x for x in parts if x)


def _has_words(w: dict, now: float) -> bool:
    """Every window is said, but a 5-hour one with no number to show (as on the account rows: the 5-hour line only
    with a number; every other window always, "not known" when it has none)."""
    if not isinstance(w, dict) or w.get('label') != FIVE_HOUR:
        return True
    used, resets = w.get('used_pct'), w.get('resets_at')
    return isinstance(used, (int, float)) and not (isinstance(resets, (int, float)) and resets <= now)


def _listed(board: dict, key: str) -> list:
    """The dicts in board[key] when it is a list; [] for anything else (a board from elsewhere may be damaged)."""
    raw = board.get(key)
    return [x for x in raw if isinstance(x, dict)] if isinstance(raw, (list, tuple)) else []


def out_note(board: Optional[dict], now: float) -> Optional[tuple]:
    """(key, words, refill time) for the one notification the board gives: the first provider in the person's order
    (where their chats start) is out until a known time, and place.pick finds another added provider with room that
    a chat can be carried on in. None otherwise. The key and the refill time make it once per out spell."""
    from . import place
    if not isinstance(board, dict):
        return None
    shown = [p for p in _listed(board, 'providers') if isinstance(p.get('id'), str)]
    if not shown:
        return None
    first = shown[0]
    if first.get('state') != OUT or not isinstance(first.get('back_at'), (int, float)):
        return None
    rooms = {p['id']: contract.Room(p['id'], p.get('state') if p.get('can_carry') else NOT_INSTALLED,
                                    back_at=p.get('back_at'), read_at=p.get('read_at')) for p in shown}
    to, why = place.pick([p['id'] for p in shown], first['id'], rooms)
    if to is None or why != 'room':
        return None
    other = next(p for p in shown if p['id'] == to)
    name, other_name = words.one_line(first.get('name') or first['id'], 30), words.one_line(other.get('name') or to, 30)
    return ('providers:out:%s' % first['id'],
            words.NOTE_OUT.format(name=name, when=words.when(first['back_at'], now), other=other_name),
            float(first['back_at']))


def lines(board: Optional[dict], now: float) -> List[tuple]:
    """The board as (kind, name, words): ('provider', its name, summary()) for each added provider in the person's
    order, then ('carry', '', the line to type) when there is somewhere to carry a chat on, then ('add', its name, the
    line that adds it) for each provider on this Mac not added. No provider lines when the board holds only the
    provider whose seats are the account list's own rows: those rows already say all of it. [] for no board."""
    if not isinstance(board, dict):
        return []
    shown = _listed(board, 'providers')
    out = []
    if len(shown) > 1 or any(not p.get('account_list') for p in shown):
        out = [('provider', words.one_line(p.get('name') or p.get('id') or '', 30), summary(p, now)) for p in shown]
    if board.get('carry'):
        out.append(('carry', '', '%s %s' % (words.BOARD_CARRY, words.BOARD_CARRY_COMMAND)))
    for a in _listed(board, 'add'):
        if a.get('id'):
            name = words.one_line(a.get('app') or a.get('name') or a['id'], 40)
            out.append(('add', name, words.BOARD_ADD.format(app=name, pid=words.one_line(a['id'], 16))))
    return out
