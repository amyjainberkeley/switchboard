"""The one Next across makers, and the one notification for the moment automatic switching has nowhere left to go:
every Claude account is at the person's own switch lines, and another provider they added has room.

with_next(state) marks that Next for every face (the terminal's list, the menu bar's menu, the page), from the page's
state alone: the accounts, the switch lines and the provider rows ('provider_rows'; the state's 'providers' is the
board of providers/view.py, never read or changed here). note(state, board) is the one notification's words for the
server to send after a build of the board. Neither moves anything: across makers a person presses (a number in the
terminal's list, or Continue here, which opens the other provider's own app on the chat; the person sends it there).
"""
from .core import parse_iso
from . import menubar, term

# Where to type matters: the key press carries the newest chat of the folder the terminal is in.
NOTE_PRESS = 'In a terminal in your project folder, type switchboard and press {number}.'


def _unusable(a, auto, now):
    """(unusable, back_at) of one Claude account by automatic switching's own rule (policy.unusable_by_windows): its
    5-hour and weekly windows, each against its line (the account's own where it has one), never a model's own week.
    A window not known (no reading, one too old to trust, or refilled by the clock since it was read) never holds it.
    back_at: when every window that holds it has reset; None when one of those resets is not known."""
    from .accounts import policy
    u = a.get('usage') if a.get('trusted') is not False else None
    u = u if isinstance(u, dict) else {}
    clock = isinstance(now, (int, float))
    args = []
    for key in ('session', 'weekly'):
        w = u.get(key) if isinstance(u.get(key), dict) else {}
        pct, t = w.get('pct'), parse_iso(w.get('resets'))
        refilled = clock and t is not None and t <= now
        args += [pct if menubar._num(pct) and not refilled else None, t]
    five_line, week_line = menubar.lines_of(a, auto)
    # with no clock in the state, nothing has reset by it: -inf is before every reset
    return policy.unusable_by_windows(now if clock else float('-inf'), *args, session_line=five_line,
                                      weekly_line=week_line)


def _out(a, auto, now):
    """Is this Claude account at its lines (_unusable). None when its numbers are not known (a reading too old to
    trust, or none): it is then not counted as out."""
    if _unusable(a, auto, now)[0]:
        return True
    five, week, _ = menubar.split(menubar.limits(a, auto))
    return None if week['unknown'] and (five is None or five['unknown']) else False


def _back_at(a, auto, now):
    """When this account has room again (_unusable): None when it is not at its lines or that time is not known."""
    return _unusable(a, auto, now)[1]


def claude(state):
    """The Claude accounts Switchboard keeps (not one it only sees Claude Code on)."""
    return [a for a in state.get('accounts') or [] if isinstance(a, dict) and not a.get('unmanaged')]


def kept_out(state, a):
    """The person keeps this account out of automatic switching (its rule 'out', on the account or in the state's
    auto rules by its id): it is never next and is not counted at all."""
    rules = (state.get('auto') or {}).get('rules')
    return a.get('rule') == 'out' or (isinstance(rules, dict) and a.get('id') is not None and
                                      rules.get(a.get('id')) == 'out')


def usable(state):
    """The kept Claude accounts that can be used: not one that needs a sign-in again (it is not "at your lines"; it
    has no numbers to be at), and not one kept out of automatic switching."""
    return [a for a in claude(state) if not a.get('needs_login') and not kept_out(state, a)]


def signing_in(state):
    """How many kept Claude accounts, not kept out, need a sign-in again."""
    return len([a for a in claude(state) if a.get('needs_login') and not kept_out(state, a)])


def at_lines(state):
    """Every usable Claude account is at its lines (at least one is usable, and every one's numbers are known):
    automatic switching has nowhere left to go. The out spell lasts while this holds."""
    auto = state.get('auto') or {}
    accounts = usable(state)
    return bool(accounts) and all(_out(a, auto, state.get('now')) is True for a in accounts)


def holds(state):
    """at_lines, and a provider row can take over: it has room by numbers read within the hour, it can be continued
    to now (as far as files can tell, and its folder lets a chat go there; on the server's rows, a chat would go too),
    and it is not already in use. The provider row, or None."""
    if not at_lines(state):
        return None
    for p in state.get('provider_rows') or []:
        if isinstance(p, dict) and p.get('state') == 'room' and p.get('fresh') is True and p.get('can') is True and \
                p.get('in_use') is not True and isinstance(p.get('provider'), str):
            return p
    return None


def with_next(state):
    """The one Next across providers, for every face (the menu, the page and the terminal's list): a copy of state in
    which the first provider row that can take over (holds: every usable Claude account at its lines, the row with
    fresh room, able to be continued to, not in use) is the only one marked next, and then no Claude account is
    ('next' None). Otherwise no provider row is, and 'next' is automatic switching's own Claude account, kept in
    'claude_next' so a face that changes the rows (the terminal, for its own folder) can decide again."""
    out = dict(state)
    mine = state.get('claude_next', state.get('next'))
    rows = [dict(p) for p in state.get('provider_rows') or [] if isinstance(p, dict)]
    p = holds(dict(out, provider_rows=rows))
    for r in rows:
        r['next'] = r is p                  # that row only: another seat of its provider may be out
    out.update(provider_rows=rows, claude_next=mine, next=None if p is not None else mine)
    return out


def period_end(state):
    """When this out spell ends: the first moment a Claude account has room again (a limit resets). None when that
    is not known."""
    auto, now = state.get('auto') or {}, state.get('now')
    ends = [t for t in (_back_at(a, auto, now) for a in usable(state)) if t is not None]
    return min(ends) if ends else None


def sentence(n, app, signin=0):
    """'Both Claude accounts are at your lines. Codex has room.': n, the usable accounts, all at their lines ("Both"
    only for two); signin, the accounts that need a sign-in, said after them: 'One Claude account is at your lines
    and the other needs a sign-in.'"""
    if not signin:
        head = 'Your Claude account is' if n == 1 else 'Both Claude accounts are' if n == 2 else \
            'All %d Claude accounts are' % n
        return '%s at your lines. %s has room.' % (head, app)
    head = 'One Claude account is' if n == 1 else '%d Claude accounts are' % n
    tail = 'the other needs a sign-in' if n == 1 and signin == 1 else 'one needs a sign-in' if signin == 1 else \
        '%d need a sign-in' % signin
    return '%s at your lines and %s. %s has room.' % (head, tail, app)


def row_number(state, row):
    """A provider row's number in the terminal's list, by the list's own rule (term.provider_numbers). None when the
    row is not among the state's rows."""
    rows = state.get('provider_rows') if isinstance(state.get('provider_rows'), list) else []
    at = next((i for i, r in enumerate(rows) if r is row), None)
    return None if at is None else term.provider_numbers(state.get('accounts'), rows)[at]


def press_words(state, p, number=None):
    """What the one notification says when provider row p can take over, and what the S in the menu bar says then:
    the moment, then where to type and what to press ('Both Claude accounts are at your lines. Codex has room. In a
    terminal in your project folder, type switchboard and press 3.'). number: the row's number in the list,
    row_number when not given."""
    from .providers import words
    number = row_number(state, p) if number is None else number
    app = words.one_line(p.get('name') or p['provider'], 30)
    return '%s %s' % (sentence(len(usable(state)), app, signing_in(state)), NOTE_PRESS.format(number=number))


def first(board):
    """The first provider on the board, in the person's order (where their chats start), or None."""
    shown = board.get('providers') if isinstance(board, dict) else None
    if not isinstance(shown, (list, tuple)):
        return None
    return next((p for p in shown if isinstance(p, dict) and isinstance(p.get('id'), str)), None)


def note(state, board, number=None, since=None):
    """The one notification, as (key, words, instance) for notify.Note, or None.

    When the first provider in the person's order is the one whose seats are the account list: every usable Claude
    account is at its lines (at_lines) and a provider row that the board says a chat can be carried to holds. The
    words say that moment, then where to type and what to press ('Both Claude accounts are at your lines. Codex has
    room. In a terminal in your project folder, type switchboard and press 3.'; number: that row's number in the list,
    row_number when not given). The key is
    providers:out:<that first provider>; the instance is the end of the out spell when known, else since (when the
    caller first saw the spell begin), else the state's own clock, so notify's own memory says it once per spell.
    When another provider is first: the board's own note (view.out_note), unchanged. Reads state and board only."""
    from .providers import view, words
    top = first(board)
    if top is None:
        return None
    if not top.get('account_list'):
        now = state.get('now') if isinstance(state, dict) and isinstance(state.get('now'), (int, float)) else \
            board.get('now')
        return view.out_note(board, now) if isinstance(now, (int, float)) else None
    if not isinstance(state, dict) or not at_lines(state):
        return None
    carry = {p.get('id') for p in board['providers'] if isinstance(p, dict) and p.get('can_carry')}
    p = holds(dict(state, provider_rows=[r for r in state.get('provider_rows') or []
                                         if isinstance(r, dict) and r.get('provider') in carry]))
    if p is None:
        return None
    said = press_words(state, p, number)
    end = period_end(state)
    instance = end if end is not None else since if since is not None else state.get('now')
    return 'providers:out:%s' % top['id'], said, instance
