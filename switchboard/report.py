"""`switchboard status --json` and `switchboard owner LINK --json`: what Switchboard knows, as JSON, for scripts,
status bars and coding assistants.

A contract, unlike the page's own state (GET /api/state), which promises nothing. `schema` comes first and counts the
shape: a change that removes or renames a field raises it; a new field does not. tests/test_cli_status_json.py pins
every key.

  {"schema": 1, "version": "0.2.0", "read_at": "2026-11-25T06:10:00Z",
   "in_use": "maya@northwind.example", "next": "maya@fernhill.example",
   "auto_switch": {"on": true, "order": "refills-first", "five_hour_line": 90, "week_line": 95, "model_line": 95,
                   "models": ["Fable"]},
   "accounts": [{"number": 1, "email": "maya@northwind.example", "short_name": "work", "plan": "Team",
                 "organization": "Northwind", "in_use": true, "next": false, "needs_sign_in": false,
                 "in_switchboard": true, "sign_in_ends_by": "2026-12-18T04:53:00Z",
                 "limits": {"five_hour": {"used_percent": 62, "refills_at": "2026-11-25T08:00:00Z"},
                            "week": {"used_percent": 70, "refills_at": "2026-11-28T06:00:00Z",
                                     "pace": {"even_percent": 57, "ahead": true, "full_at": "2026-11-27T01:40:00Z"}},
                            "models": [], "spend": null},
                 "checked_at": "2026-11-25T06:09:20Z"}],
   "this_folder": {"account": "maya@fernhill.example", "folder": "/Users/maya/work/client"}}

An account with a switch line of its own (`switchboard auto line`) also has "five_hour_line" or "week_line", or both,
each only when it has that line of its own; `order` is one of engine.STRATEGIES.

The rules, each the terminal's own (term.py), so the two never disagree:
- Accounts in the order `switchboard status` prints them; `number` is the one a person types (`switchboard 2`), null
  for a login Claude Code is on that Switchboard does not hold yet (`in_switchboard` false).
- Times are UTC, ISO 8601, to the nearest second, ending in Z. Percents are whole numbers, halves up, as shown.
- What is not known is null, never 0: a reading too old to show (server.reading_trusted) has null percents, as the
  terminal shows a dash. A limit the account does not have (no 5-hour limit on some plans) is null itself.
- `refills_at` is null when the window has not started, or its refill time has passed.
- `next` is marked only while auto-switch runs, which is in the page: without the page it is null.
- `week.pace` is pace.week, the rule the terminal, the menu bar and the page say words from: where an even week is
  now, whether the account is ahead of it, and when it would be full. Null when the reading can't tell (too old to
  show or over an hour old, no number, no reset ahead); `ahead` null while it is too early to say.
- `spend` is a spend limit where the reading carries one (spend.py: only the account in use, from Claude Code's own
  numbers): {"spent", "limit", "currency", "used_percent", "refills_at"}; null for an account with none.
- Accounts are named by their whole email. No login, token, fingerprint, account id or organization id is in it.
  Two accounts with one email (one person in two organizations) are told apart by `number` and `organization`.
- `this_folder`: the account `switchboard run` without NAME starts as in the folder the command ran in, and the
  folder that set it (`switchboard folder NAME`, the nearest above); null when none applies.
- `kind` says what an account is: 'sign_in' (a browser sign-in), 'one_year' (a one-year token from `claude setup-token`,
  with a name of its own: `label`) or 'api_key' (an API key, billed per token). A token or a key has `email` null,
  `added_at` (when Switchboard kept it; a token carries no end date) and null limits: a token's percents are not
  known, and an API key has no plan limits (`five_hour` and `week` null). A sign-in that also has a one-year token
  kept for it says when in `one_year_added_at`. While Claude Code is on a token, `in_use` is null and that token's
  own `in_use` is true. On a login no account here is (a token no record owns), `in_use` is null and no row is in use.

Pure: the page's state goes in (server.render's shape, the view `switchboard status` prints), a plain dict comes out.
"""
import time

from . import bearers, engine, names, pace, server, spend, term
from .core import parse_iso

SCHEMA = 1


def when(t):
    """A time in seconds as '2026-11-25T06:10:00Z' (UTC, to the nearest second), or None when it is not a sane time
    (term._when_ok)."""
    if not term._when_ok(t):
        return None
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(term.jsround(t)))


def _text(v):
    return v.strip() if isinstance(v, str) and v.strip() else None


def _limit(w, now):
    """One limit as term.window reads it: {'used_percent', 'refills_at'}."""
    x = term.window(w, now, 100, '')
    return {'used_percent': x['pct'], 'refills_at': when(x['t']) if x['t'] is not None and x['t'] > now else None}


def limits(a, now):
    """{'five_hour', 'week', 'models'} of an account in the page's state, as term.limits reads it: a reading too old to
    show (trusted False) or none at all is not known; a reading with no 5-hour window is an account with no 5-hour
    limit, however old the reading (as the page's row says "No 5-hour limit" from it), unless its saved readings ever
    held a 5-hour number (five_seen): then its 5-hour limit is not known, as the rows say; a model's own week (Fable)
    is listed only with a number."""
    u = a.get('usage') if a.get('trusted') is not False else None
    if not isinstance(u, dict):
        held = a.get('usage')
        no_five = isinstance(held, dict) and bool(held) and not isinstance(held.get('session'), dict) \
            and not a.get('five_seen')
        return {'five_hour': None if no_five else {'used_percent': None, 'refills_at': None},
                'week': {'used_percent': None, 'refills_at': None, 'pace': None}, 'models': [], 'spend': None}
    models = [dict({'name': str(x.get('name') or 'Model')}, **_limit(x, now)) for x in u.get('scoped') or []
              if isinstance(x, dict) and term._num(x.get('pct'))]
    return {'five_hour': _limit(u['session'], now) if isinstance(u.get('session'), dict)
            else {'used_percent': None, 'refills_at': None} if a.get('five_seen') else None,
            'week': dict(_limit(u.get('weekly'), now), pace=week_pace(a, now)), 'models': models,
            'spend': spend_limit(u, now)}


def spend_limit(u, now):
    """limits.spend: {'spent', 'limit' (in its currency; null when not known), 'currency' ('USD'), 'used_percent',
    'refills_at'} when the reading carries a spend limit with a number (spend.of), whether or not the row shows it as a
    line; null otherwise."""
    s = spend.of(u)
    if s is None:
        return None
    t = parse_iso(s['resets'])
    return {'spent': s['used'], 'limit': s['limit'], 'currency': s['currency'], 'used_percent': term.jsround(s['pct']),
            'refills_at': when(t) if t is not None and t > now else None}


def week_pace(a, now):
    """limits.week.pace (pace.week, the rule every surface says): {'even_percent': where an even week is now,
    'ahead': at the pace of its use so far the week is full before it refills (null: too little to say yet),
    'full_at': when (null unless ahead)}; null when the reading can't tell (too old, no reset ahead, no number)."""
    p = pace.week(a, now)
    if p is None:
        return None
    return {'even_percent': p['even'], 'ahead': p['ahead'], 'full_at': when(p['full_at']) if p['ahead'] else None}


def account(a, row, now, own=None):
    """One account: `a` is its entry in the page's state, `row` the terminal's reading of it (term.read). `own`: its
    own switch lines ({'session_at', 'weekly_at'}, each only when set), given only when it has them."""
    held = not row['unmanaged']
    slot = row['slot']
    lines = dict((name, own[key]) for key, name in (('session_at', 'five_hour_line'), ('weekly_at', 'week_line'))
                 if isinstance(own, dict) and term._num(own.get(key)))
    return dict({'number': slot if held and isinstance(slot, int) and not isinstance(slot, bool) else None,
            'email': names.email_of(row['email']), 'short_name': _text(a.get('alias')),
            'plan': _text(server.plan_name(a)), 'organization': _text(server.org_name(a)),
            'in_use': row['active'], 'next': row['next'], 'needs_sign_in': row['needs_login'], 'in_switchboard': held,
            'sign_in_ends_by': when(a.get('signin_ends_at')) if held else None,
            'limits': limits(a, now), 'checked_at': when(term.checked_at(a)),
            'from_another_mac': bool(a.get('elsewhere'))}, **lines)


def auto_switch(view, head):
    """Whether auto-switch is on, its order, and its lines, as `switchboard status` and `switchboard auto` say them."""
    auto = view.get('auto') or {}
    order = auto.get('strategy') if auto.get('strategy') in term.ORDER_WORDS else engine.DEFAULT_STRATEGY
    models = [m.strip() for m in auto.get('models') or [] if isinstance(m, str) and m.strip()
              and m.strip().lower() not in ('all', 'none')] if isinstance(auto.get('models'), (list, tuple)) else []
    return {'on': bool(head['auto']), 'order': order, 'five_hour_line': head['line5'], 'week_line': head['line_w'],
            'model_line': auto.get('model_at') if term._num(auto.get('model_at')) else term.MODEL_LINE,
            'models': models}


def document(view, version, live=None, this_folder=None):
    """`switchboard status --json`: the page's state as the document above. `live` is the email Claude Code is signed
    in to, for `in_use` when no account in the state is in use (term.read). this_folder: {'account', 'folder'} or
    None (foldercmd.this_folder)."""
    # The document is the Claude accounts: another provider's rows are left out, and 'next' is auto-switch's own.
    view = dict(view, provider_rows=[], **({'next': view['claude_next']} if 'claude_next' in view else {}))
    state = term.read(view, live=live)
    now = state['now']
    by_email = dict(((a.get('email') or ''), a) for a in view.get('accounts') or [] if isinstance(a, dict))
    rows = state['accounts']
    accounts = [account(by_email.get(r['email']) or {}, r, now, own_lines(view, by_email.get(r['email'])))
                for r in rows]
    tokens = [t for t in view.get('tokens') or [] if isinstance(t, dict)]
    for r, x in zip(rows, accounts):    # a sign-in, and the day a one-year token was kept for it (by the account's id)
        aid = (by_email.get(r['email']) or {}).get('id')
        t = next((t for t in tokens if aid and t.get('for_id') == aid), None)
        x.update(kind='sign_in', label=None, added_at=None,
                 one_year_added_at=when(t.get('added_at')) if t else None)
    accounts += bearers.report_rows(view, when)
    on_token = any(t.get('active') for t in tokens)
    head = names.email_of(state['head']['email']) or None
    if head and not any(r['active'] for r in rows) and any(names.email_of(r['email']) == head for r in rows):
        head = None     # its own row says it is not in use: Claude Code is on a login Switchboard does not keep
    return {'schema': SCHEMA, 'version': version, 'read_at': when(now),
            'in_use': None if on_token else head,
            'next': next((names.email_of(r['email']) for r in rows if r['next']), None),
            'auto_switch': auto_switch(view, state['head']),
            'accounts': accounts,
            'this_folder': dict(this_folder) if this_folder else None}


def own_lines(view, a):
    """An account's own switch lines from the page's state (its 'auto' settings), or None."""
    own = (view.get('auto') or {}).get('own_lines')
    return own.get((a or {}).get('id')) if isinstance(own, dict) else None


def switched(before, after, reason=None, at=None, refused=None, moved=None, numbers=(None, None), on_token=False):
    """`switchboard switch [NAME] --json` and `switchboard use NAME --json`: {'schema', 'moved' (Claude Code is on
    another account than before), 'from' (the account it was on, None when it was signed out), 'to' (the account it
    moved to, None when it did not move), 'in_use' (the account it is on now), 'reason' (the reason History records for
    the switch, None without one), 'at' (when it moved), 'refused' (the sentence the plain command says when it
    refuses, else None), 'from_number' and 'to_number' (the numbers a person types for those two accounts, None when
    Switchboard does not keep one; two accounts with one email differ here)}. `before` and `after` are emails or None;
    `moved`: whether the account changed, when the caller knows it from more than the email; `at` a time in seconds.
    A one-year token kept as an account is named as the terminal names it (its label, or its account's name), with no
    number; while Claude Code is on one (on_token), `in_use` is null, as in the status document."""
    moved = (bool(after) and after != before and refused is None) if moved is None else bool(moved)
    return {'schema': SCHEMA, 'moved': moved, 'from': before or None, 'to': after if moved else None,
            'in_use': None if on_token else after or None, 'reason': (reason or None) if moved else None,
            'at': when(at) if moved and at is not None else None, 'refused': refused,
            'from_number': _whole(numbers[0]) if before else None, 'to_number': _whole(numbers[1]) if moved else None}


def _whole(n):
    return n if isinstance(n, int) and not isinstance(n, bool) else None


def current(email, held, number=None, short_name=None, kind=None, label=None, other=False):
    """`switchboard current --json`: {'schema', 'email' (the account Claude Code is signed in to, None when it is
    signed out or on a one-year token kept as an account of its own), 'signed_in', 'in_switchboard' (Switchboard keeps
    that login), 'number' (the one a person types, None when Switchboard does not keep it), 'short_name', 'kind'
    ('sign_in', or 'one_year' while Claude Code is on a one-year token Switchboard keeps; None when signed out),
    'label' (a token of its own's name, as `status --json` gives it; else None)}. other: Claude Code is on a login no
    account here is (a token no record owns): signed in, with no email, not in Switchboard, and kind None."""
    if other:
        email, held, kind, label = None, False, None, None
    on = bool(email) or bool(label) or bool(other)
    held = on and bool(held)
    return {'schema': SCHEMA, 'email': email or None, 'signed_in': on, 'in_switchboard': held,
            'number': number if held and isinstance(number, int) and not isinstance(number, bool) else None,
            'short_name': _text(short_name) if held else None,
            'kind': (kind or 'sign_in') if on and not other else None, 'label': _text(label) if label else None}


def owner(url, found):
    """`switchboard owner LINK --json`: {'schema', 'link', 'owner' (an email, or None while not known), 'how' (how it
    is known: 'seen' it loaded in the owner's profile, 'listed' the owner's own list of published pages names it,
    'published' Claude Code published it on that account, 'guess' published near a switch; None while not known),
    'open_with'}. `found` is artifacts.owner_of's answer."""
    link = found.get('url') or url
    email = found.get('email') or None
    return {'schema': SCHEMA, 'link': link, 'owner': email, 'how': (found.get('src') or None) if email else None,
            'open_with': 'switchboard open %s' % link}
