"""The numbered rows of the one list, after the accounts: each added provider besides the account list's, derived from
the board (providers/view.py), never a second reading of the world. Every face draws these rows: the terminal's list,
the menu bar's menu and the page.

from_board(board, folder, kit) gives one row per seat of each added provider whose seats are not the account list's
and that can be continued to as far as files can tell (the board's fit_why is None; on a board from a page started
before that field, installed and signed in), in the person's order; one row with no windows and the state 'unknown'
for such a provider with no seat yet (signed in, numbers never read), so its number can still be pressed. A provider
with a fit_why has no row: view.under_list says why, under the list.

    {'provider', 'name', 'vendor', 'email', 'plan' (a short plan word or None),
     'windows': [{'label', 'used_pct', 'resets_at', 'line'}], 'read_at', 'state' ('room' | 'out' | 'unknown'),
     'back_at', 'fresh', 'why', 'can', 'app', 'in_use', 'next', 'chat'}

state, back_at and read_at are the seat's own. fresh: it has room by numbers read within the hour (cli.WORDS_FRESH_S;
older ones say "had room when last read ..."). The folder's facts are file reads added here, for the folder a row is
drawn for: why is a stay of that folder on another provider or a never for this one (else None), can is why is None,
and in_use is that folder's newest move started this provider's app there within the last few hours (IN_USE_S) and
has not come back (the ledger's open continue, in the state 'started'; a printed line to run, or a note handed to the
app's message box, started nothing yet). With no folder they are left out. next is never decided here
(comeup.with_next decides it for every face, from the whole state) and chat is the server's to fill (the chat a press
would carry); both start False and None.
"""
import os
import time
from typing import List, Optional

from . import cli, contract, words

IN_USE_S = 4 * 3600.0           # a start of another app in a folder counts as in use there for this long


def from_board(board, folder: Optional[str] = None, kit=None, now: Optional[float] = None) -> List[dict]:
    """The rows (the shape above) from a board, for folder (None: no folder facts). kit: where the folder's choices
    and ledger are read (the real ones when not given); now: the moment fresh is told at (the kit's clock, else the
    wall clock). Never raises: a damaged board gives the rows it can."""
    if not isinstance(board, dict) or not isinstance(board.get('providers'), (list, tuple)):
        return []
    entries = [p for p in board['providers'] if isinstance(p, dict) and isinstance(p.get('id'), str)
               and not p.get('account_list') and _fit(p)]
    if not entries:
        return []
    if kit is None and folder is not None:
        kit = cli.default_kit()
    if now is None:
        now = kit.reach.wall() if kit is not None else time.time()
    here = _folder_facts(board, entries, folder, kit, now)
    out = []
    for p in entries:
        why, in_use = here.get(p['id'], (None, False))
        seats = [s for s in p.get('seats') if isinstance(s, dict)] if isinstance(p.get('seats'), list) else []
        for seat in seats or [None]:
            out.append(_row(p, seat, now, why, in_use))
    return out


def rows(kit=None) -> List[dict]:
    """The rows for the folder the kit is in (the current folder by default), from the board view.view builds from
    files: what the terminal's list prints. [] when none is added or they cannot be worked out (logged)."""
    try:
        from . import view
        board = view.view(kit=kit)
        if board.get('error'):
            raise ValueError(board['error'])
        folder = kit.cwd() if kit is not None else os.getcwd()
        return from_board(board, folder=folder, kit=kit)
    except Exception as e:  # noqa: BLE001 - the list shows without them
        try:
            from switchboard import core
            core.log('the provider rows: %s' % type(e).__name__)
        except Exception:  # noqa: BLE001
            pass
        return []


def _fit(p: dict) -> bool:
    """It can be continued to as far as files can tell: the board's fit_why is None. A board from a page started
    before that field (no fit_why key at all) is read from what every board has: installed and signed in, and a state
    that says neither is not so."""
    if 'fit_why' in p:
        return p['fit_why'] is None
    return p.get('installed') is True and p.get('signed_in') is True and \
        p.get('state') not in (contract.NOT_INSTALLED, contract.SIGNED_OUT)


def _row(p: dict, seat: Optional[dict], now: float, why: Optional[str], in_use: bool) -> dict:
    seat = seat or {}
    windows = [w for w in seat.get('windows') if isinstance(w, dict)] if isinstance(seat.get('windows'), list) else []
    state = seat.get('state') if seat.get('state') in ('room', 'out') else 'unknown'
    read_at = _number(seat.get('read_at'))
    return {'provider': p['id'], 'name': words.one_line(p.get('name') or p['id'], 32),
            'vendor': words.one_line(p.get('vendor') or p.get('name') or p['id'], 32),
            'email': p.get('email') if isinstance(p.get('email'), str) else None,
            'plan': plan_word(seat.get('plan')) or None,
            'windows': [{'label': words.one_line(w.get('label') or '', 30), 'used_pct': _number(w.get('used_pct')),
                         'resets_at': _number(w.get('resets_at')), 'line': _number(w.get('line'))} for w in windows],
            'read_at': read_at, 'state': state, 'back_at': _number(seat.get('back_at')) if state == 'out' else None,
            'fresh': state == 'room' and read_at is not None and 0 <= now - read_at <= cli.WORDS_FRESH_S,
            'why': why, 'can': why is None, 'app': p.get('has_app') is True, 'in_use': in_use, 'next': False,
            'chat': None}


def _folder_facts(board, entries, folder, kit, now) -> dict:
    """{provider id: (why, in_use)} for folder: a stay on another provider or a never for this one, and whether this
    folder's newest move started this provider's app there within IN_USE_S (the move's record stays open longer, so
    `back` still finds it). {} with no folder."""
    if folder is None or kit is None:
        return {}
    folder = os.path.realpath(folder)
    basename = words.one_line(os.path.basename(folder) or folder, 60)
    stay = kit.choices.stays(folder)
    hop = kit.tasks.open_continue(folder)
    at = _number(getattr(hop, 'at', None))
    started = hop.to_provider if hop is not None and hop.state == 'started' and at is not None and \
        now - at <= IN_USE_S else None
    out = {}
    for p in entries:
        why = None
        if stay and stay != p['id']:
            why = words.ROW_STAYS.format(basename=basename, p=_name(board, stay))
        elif kit.choices.consent(folder, p['id']) == 'never':
            why = words.ROW_NEVER.format(basename=basename, to=_name(board, p['id']))
        out[p['id']] = (why, started == p['id'])
    return out


def _name(board, pid) -> str:
    """pid's name as the board says it, else as its adapter does."""
    from . import name_of
    got = next((p.get('name') for p in board['providers'] if isinstance(p, dict) and p.get('id') == pid), None)
    return words.one_line(got if isinstance(got, str) and got else name_of(pid), 32)


def _number(v) -> Optional[float]:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def plan_word(plan) -> str:
    """A ChatGPT plan in the list's words: 'Plus', 'Pro', 'Pro Lite' (the plan table's name without 'ChatGPT');
    '' when it is not known or not one the table names."""
    name = words.PLAN_NAMES.get(plan) if isinstance(plan, str) else None
    return name.split(' ', 1)[1] if name and ' ' in name else ''
