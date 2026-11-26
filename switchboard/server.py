"""The page at http://localhost:7799 and the loop that keeps it current.

The server listens on this Mac only (127.0.0.1) and answers only its own page:
requests from other websites are refused.
"""
import collections
import copy
import errno
import functools
import hashlib
import hmac
import json
import os
import queue
import re
import signal
import socket
import sys
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer as StdThreadingHTTPServer
from pwd import getpwuid
from time import monotonic      # durations; a test that swaps this module's `time` never stops them

from . import (artifacts, awake, browser, chats, claudecode, connect, core, engine, history, keep, lanes, linkapp,
               memorypage, models, more, notify, plan, resume, statusline, term, usage)
from . import signins
from . import previews
from . import bearers
from . import insights
from .accounts import api
from . import notify_hooks
from . import watch
from . import autorules
from . import newer
from . import pace
from . import spend
from . import names
from .core import (VERSION, data_path, WEB, SwitchError, ensure_account, event, load, locked, log, name_of,
                   port, set_active, set_setting, settings)

TICK = 60           # seconds between readings
MAC_TICK = 20       # seconds between looks at whether Claude Code is working (to hold the Mac awake)
WAKE = threading.Event()
connect.ON_END = WAKE.set   # a sign-in that ends is handed to the engine at once (tick), not at the next reading
signins.WAKE = WAKE.set     # Sign in to all's closing line is drawn at once
EMAIL_RE = re.compile(r'^[^@\s"]+@[^@\s"]+\.[A-Za-z]{2,}$')
ALIAS_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,19}$')
OPENABLE = ('https://claude.ai/',)


# ---------- keeping the state current ----------

def apply_rows(st, rows, trust_active=True):
    """Copy the engine's view of every account (engine.listing's rows) into the page's state.

    trust_active is False when you switched after these rows were read: their idea of the active account is then
    older than what the state already knows.
    """
    seen = set()
    for row, email in zip(rows, names.keys(rows)):      # its email; with its organization when two share one (names.py)
        if not email:
            continue
        seen.add(email)
        a = ensure_account(st, email)
        if row.get('id'):                   # Anthropic's account id (api.accounts), kept for the owner map and hooks
            a['account_id'] = row['id']
        taken_gone(a, row.get('elsewhere'))
        a.update(connected=True, id=row.get('id') or a.get('id'), slot=row.get('number'),
                 alias=row.get('alias') or None, needs_login=engine.needs_sign_in(row),
                 status=row.get('status') or 'ok', org=row.get('org_name') or a.get('org'),
                 org_id=row.get('org_id') or a.get('org_id'), plan=row.get('plan') or a.get('plan'),
                 # what names the plan beside the login's own word: the row's, and none when the row knows none
                 tier=row.get('tier'), org_type=row.get('org_type'),
                 disabled=False, note=engine.note_of(row), usage=row.get('usage') or a.get('usage'),
                 usage_at=row.get('usage_at') or a.get('usage_at'),
                 checked_at=row.get('checked_at') or a.get('checked_at'), unmanaged=bool(row.get('unmanaged')),
                 models_seen=row.get('models_seen') or a.get('models_seen') or [], source=row.get('source'),
                 frozen=bool(row.get('frozen')),
                 usage_status='frozen' if row.get('frozen') else 'error' if row.get('usage_error') else 'ok',
                 usage_error=row.get('usage_error'), elsewhere=bool(row.get('elsewhere')),
                 # when Anthropic ends this sign-in: a renewal keeps the date, a new sign-in replaces it, and a row
                 # that knows none clears it (never the old one kept)
                 signin_ends_at=_seconds(row.get('refresh_expires_at')))
        for gone in ('threshold', 'priority'):       # the engine's rows never set these: dropped
            a.pop(gone, None)
        if trust_active and row.get('active') and st.get('active') != email:
            if st.get('active'):                # a switch the engine recorded is said already; else it was Claude Code's
                since = (st.get('timeline') or [[None, 0]])[-1][1]
                facts = history.noticed(st['active'], email, since)
                if not facts.get('recorded'):
                    event(st, 'Claude Code is now on %s' % email, 'switch', **facts)
            set_active(st, email)
    for email in st['order']:
        if email not in seen:
            st['accounts'][email]['connected'] = False
    if trust_active and not any(r.get('active') for r in rows):
        st['active'] = None


def taken_gone(a, still):
    """Numbers taken from another Mac (readings.py) never outlive their time: once the engine stops giving them, they
    go, also when this Mac has no numbers of its own for the account to show instead."""
    if a.get('elsewhere') and not still:
        a.update(usage=None, usage_at=None, checked_at=None, elsewhere=False)


def _text(v):
    return v.strip() if isinstance(v, str) else ''


def _seconds(v):
    """A time as a number of seconds, or None: not a number, or not a sane time (term._when_ok: after 1970, before
    the year 5000; a corrupt saved date is not known)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not 0 < v < 1e11:
        return None
    return v


# ---------- an account's plan and organisation, in words ----------
#
# Each account is named by its email; under it, its plan and organisation ("Max 20x · Personal", "Team · Northwind").
# The engine gives the login's own word for the plan ('max', 'team': written with its capital), the account's rate
# tier and kind of organisation when Claude Code's settings said them, and the organisation's name. The tier names
# Max 20x and Max 5x; "Ultra" (Anthropic's newer name for the 20x tier) is written "Max 20x".

TEAM_TYPES = ('claude_team', 'claude_enterprise')
PERSONAL_ORG_RE = re.compile(r"['’]s organi[sz]ation$", re.I)
PLAN_NAMES = {'Ultra': 'Max 20x'}


def plan_label(v):
    """A plan's label as it is written: a login's own word for it (one word, all lower case: 'max', 'team') gets its
    capital; anything else is already a name and is left as it is."""
    label = _text(v)
    return label.capitalize() if label.isalpha() and label.islower() else label


def plan_name(a):
    """The plan's words: 'Max 20x', 'Max 5x', 'Team', 'Pro', ... or ''."""
    label = plan_label(a.get('plan'))
    if _text(a.get('org_type')) in TEAM_TYPES:
        return label or ('Team' if a.get('org_type') == 'claude_team' else 'Enterprise')
    tier = _text(a.get('tier')).lower()
    if tier.endswith('max_20x'):
        return 'Max 20x'
    if tier.endswith('max_5x'):
        return 'Max 5x'
    return PLAN_NAMES.get(label, label)


def org_name(a):
    """The organisation's words: 'Personal' for an account's own organisation ("you@mail.example's Organization"),
    otherwise its name, or ''."""
    name = _text(a.get('org'))
    if not name:
        return ''
    return 'Personal' if PERSONAL_ORG_RE.search(name) else name


def plan_line(a):
    """'Max 20x · Personal', 'Team · Northwind': what the page and the terminal write under an account's email."""
    return ' · '.join(x for x in (plan_name(a), org_name(a)) if x)


def auto_link(st):
    """Give each account with no browser profile the one its own claude.ai data points to."""
    if not browser.present():
        return
    need = {e: a.get('org_id') for e, a in st['accounts'].items()
            if a.get('connected') and not a.get('profile') and a.get('profile_src') != 'none' and a.get('org_id')}
    if not need:
        return
    names = browser.profiles()
    taken = {a.get('profile') for a in st['accounts'].values() if a.get('profile')}
    for email, profile in browser.guess_links(need, taken).items():
        st['accounts'][email].update(profile=profile, profile_src='hint')
        event(st, 'Matched %s to the %s profile "%s"' % (email, browser.app(), names.get(profile, profile)))


def owner_map_now():
    """~/.switchboard/owners.json brought up to date now (artifacts.write_owner_map: written only when it changed), so
    the link app, a go helper and the session hooks see a switch or a new profile at once. Never fails the caller."""
    if core.DEMO:
        return False
    try:
        return artifacts.write_owner_map()
    except Exception as e:  # noqa: BLE001 - a fallback file: the next tick writes it
        log('the owner map was not written: %r' % e)
        return False


def tick():
    """Read every account from the engine and bring the state up to date. Returns what was read, for keep_tick:
    {'rows' (None when the engine did not answer), 'asked'}."""
    # A finished sign-in goes to the engine first (never under the state lock), so the rows read next already hold
    # its new login. The other way round the rows were older than the sign-in, and put "Sign in" back on its row
    # until the reading after.
    done = connect.collect()
    # The engine reads its own files and at most one Keychain item; it is asked outside the state's lock.
    asked = time.time()
    rows, error, disagree = None, None, False
    # For the loop's early reading (wake_if_behind). The stamp is taken before the rows, so a change after it is one
    # they may not hold, and after collect(), so what a finished sign-in wrote is not counted as one. Until the rows
    # say otherwise, this reading said nothing new.
    ROWS.update(stamp=rows_stamp(), same=True)
    try:
        got = engine.listing()
        rows, disagree = got['rows'], got.get('disagree', False)
    except SwitchError as e:
        error = str(e)
    if rows is not None:
        said = rows_said(rows)
        ROWS.update(same=said == ROWS.get('said'), said=said)
    with locked() as st:
        before = changes_view(st)
        connect.finish(st, done)
        if rows is not None:
            apply_rows(st, rows, trust_active=st.get('switched_at', 0) < asked)
            st['disagree'] = bool(disagree)
            ROWS['by_email'] = dict(((r.get('email') or '').lower(), r) for r in rows if r.get('email'))
        apply_live_numbers(st, time.time())     # the account in use: Claude Code's own numbers when they are newer
        auto_link(st)
        if error and error != st.get('error'):
            log(error)
        st['error'] = error
        st['tick_at'] = time.time()
        st.pop('weekly_log', None)          # an earlier build kept weekly readings; nothing reads them now
        st.pop('repair', None)              # a cut-off switch's record; nothing reads it
        st.pop('kept_notice', None)
        after = changes_view(st)
        live = feed_view(st)
        took = insights.take_readings(st)   # the numbers just taken in, for the readings log below
    auto = engine.auto_settings()           # outside the state lock: it asks the engine and its loop's host
    lines = plan_settings(auto)
    nxt = plan_view(st, lines, st['tick_at'])['next']
    moved = before.get('active') != after.get('active')
    for step, args in ((owner_map_now, ()) if moved else (None, ()),     # the link app and hooks see a switch at once
                       (feed_live, (live,)), (note_history, (st, before, after, lines, auto, error)),
                       (write_status_file, (st, nxt)), (tell, (before, after, lines, st['tick_at'])),
                       (artifacts.refresh, ())):
        if step is None:
            continue
        if step is artifacts.refresh and (DISK['low'] or easing()):
            owner_map_now()             # the scan waits (a disk almost full, a Mac very busy); the map
            continue                    # is small and stays current
        try:                            # one failing step must not skip the others
            step(*args)
        except Exception as e:  # noqa: BLE001
            log('%s failed: %r' % (getattr(step, '__name__', step), e))
    try:                                # each account's readings over time, for the week under the accounts
        insights.note_readings(took)
    except Exception as e:  # noqa: BLE001 - it never raises; this is in case
        log('the readings log failed: %r' % e)
    return {'rows': rows, 'asked': asked}


def note_history(st, before, after, lines, auto, error):
    """Limits reached or refilled, problems begun: written into the state (its own short lock)."""
    with locked() as now:
        history.note_tick(now, before, after, lines, auto, error)
        pace.note_history(now)          # a week full before it refills at this pace: once a week in History


def keep_tick(looked, now=None):
    """What follows the engine's rows in the page's background tick (keep.py): a login Claude Code was signed in to
    that the engine does not keep is kept by itself (and the engine keeps renewed logins); with two accounts to switch
    between, auto-switch on by itself; and when the page's first run ends, notifications on by themselves. Each step
    on its own: one failing never stops the page."""
    looked = looked or {}
    rows = looked.get('rows')
    run = NOTIFY_RUN if helper_settings()['notify'] and not core.DEMO else None
    kept = None
    try:
        kept = keep.look(rows, now=now, run=run)
        if kept:
            WAKE.set()                  # read the accounts again at once, so the page shows it
    except Exception as e:  # noqa: BLE001 - keep the page alive
        log('keeping a login failed: %r' % e)
    if rows is None or core.DEMO:
        return
    try:
        if keep.auto_by_itself(keep.switchable(rows) + (1 if kept else 0), now=now, run=run):
            WAKE.set()
    except Exception as e:  # noqa: BLE001
        log('turning auto-switch on: %r' % e)
    try:
        keep.first_run_tick(rows, kept)
    except Exception as e:  # noqa: BLE001
        log('turning notifications on: %r' % e)


# ---------- the plan: what to use first, what is next, and the week ahead ----------

MARGIN = 5.0                # never land within this many points of a line (the engine's policy keeps the same)


def plan_settings(auto):
    """What automatic switching runs on, from engine.auto_settings(): each window's line (5-hour, weekly, the counted
    model's week), the model weeks that count, the order, the landing margin, and, while an account has one, each
    account's rule ({account id: 'last' or 'out'}: plan_accounts puts it on the account)."""
    def num(v, default):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else default
    session = num(auto.get('session_at'), engine.DEFAULT_SESSION)
    weekly = num(auto.get('weekly_at'), engine.DEFAULT_WEEKLY)
    out = {'session_line': session, 'weekly_line': weekly,
           'model_line': num(auto.get('model_at'), engine.DEFAULT_MODEL_LINE),
           'models': list(auto.get('models') or []), 'weekly_set': True, 'margin': MARGIN, 'landing_room': MARGIN,
           'strategy': auto.get('strategy') if auto.get('strategy') in plan.STRATEGIES else engine.DEFAULT_STRATEGY}
    rules = rules_of(auto)
    if rules:
        out['rules'] = rules
    for key, got in (('own_lines', engine.own_lines_of(auto)), ('ranks', engine.ranks_of(auto))):
        if got:                                 # each account's own lines and rank: plan_accounts puts them on it
            out[key] = got
    return out


def rules_of(auto):
    """{account id: 'last' or 'out'} from the auto settings; an account used as usual has no entry."""
    rules = auto.get('rules') if isinstance(auto.get('rules'), dict) else {}
    return dict((k, v) for k, v in rules.items() if isinstance(k, str) and v in ('last', 'out'))


# How long a reading counts: for what the page draws (older is "not known": no bar, never a guessed number), and for
# where the plan may land (the engine's policy lands only on fresh numbers).
SHOWN_FOR, PLANNED_FOR, AHEAD_OK = 3600.0, 900.0, 5.0


def reading_trusted(a, now, models=(), limit=SHOWN_FOR):
    """Can this account's reading be shown (or planned on, with limit=PLANNED_FOR)? It must have numbers and a time,
    not be dated ahead of the clock, and be at most `limit` seconds old. A reading held back by a rate limit
    (frozen) counts: its numbers are a floor, and the page says so."""
    u, at = a.get('usage'), a.get('usage_at')
    if not u or isinstance(at, bool) or not isinstance(at, (int, float)):
        return False
    age = now - at
    return -AHEAD_OK <= age <= limit


def plan_accounts(st, now=None, lines=None):
    """The accounts the page shows, in the shape plan.py reads (used percents, epoch times), each with its rule in
    auto-switch when it has one."""
    out = []
    rules = (lines or {}).get('rules') or {}
    # the person's order (the engine's) breaks ties
    for email in sorted(st['order'], key=lambda e: (st['accounts'].get(e) or {}).get('slot') or 99):
        a = st['accounts'].get(email)
        if not a or not a.get('connected'):
            continue
        u = a.get('usage') or {}
        w, s = u.get('weekly') or {}, u.get('session') or {}
        out.append({'name': email, 'weekly_used_pct': w.get('pct'), 'weekly_resets_at': core.parse_iso(w.get('resets')),
                    'five_hour_used_pct': s.get('pct'), 'five_hour_resets_at': core.parse_iso(s.get('resets')),
                    'scoped': [{'name': x.get('name'), 'used_pct': x.get('pct'), 'resets_at': core.parse_iso(x.get('resets'))}
                               for x in u.get('scoped') or [] if isinstance(x, dict)],
                    # An account the engine does not keep cannot be switched to, so the plan never picks it.
                    'disabled': bool(a.get('unmanaged')),
                    'needs_login': bool(a.get('needs_login')), 'active': email == st.get('active'),
                    # a reading taken from another Mac is shown, never decided on (readings.py): not trusted here
                    'trusted': reading_trusted(a, time.time() if now is None else now, (lines or {}).get('models'),
                                               PLANNED_FOR) and not (a.get('elsewhere') and not a.get('live'))})
        rule = rules.get(a.get('id') or a.get('account_id'))
        if rule:
            out[-1]['rule'] = rule
        own = ((lines or {}).get('own_lines') or {}).get(a.get('id') or a.get('account_id')) or {}
        for key, line in (('session_at', 'session_line'), ('weekly_at', 'weekly_line')):
            if key in own:
                out[-1][line] = own[key]
        rank = ((lines or {}).get('ranks') or {}).get(a.get('id') or a.get('account_id'))
        if rank:
            out[-1]['rank'] = rank
    return out


def models_seen(st, auto):
    """The per-model windows the accounts report (for "Count a model"), and whatever is counted now by name ("all"
    and "none" are not models)."""
    seen = {}
    for name in [n for a in st['accounts'].values() if a.get('connected') for n in a.get('models_seen') or []] \
            + list(auto.get('models') or []):
        if isinstance(name, str) and name.strip() and name.strip().lower() not in seen \
                and name.strip().lower() not in ('all', 'none'):
            seen[name.strip().lower()] = name.strip()
    return sorted(seen.values(), key=str.lower)


def plan_view(st, lines, now):
    """{order: [{email, rank, usable, reason, left, back_at}], next: email or None, why, back_at}.
    The page draws its order and its Next from this, so the two never disagree."""
    accounts = plan_accounts(st, now, lines)
    ranked = plan.order(accounts, lines, now)
    nxt = plan.next_account(accounts, lines, now)
    return {'order': [{'email': j['name'], 'rank': j['rank'], 'usable': j['usable'], 'reason': j['reason'],
                       'left': None if j['left_pct'] is None else round(j['left_pct'], 1), 'back_at': j['back_at']}
                      for j in ranked],
            'next': nxt['account']['name'] if nxt['account'] else None,
            'why': nxt['reason'], 'back_at': nxt['back_at'], 'lines': dict(lines)}


def marked_next(accounts, planned, auto):
    """The account marked Next (on the page, in the menu bar and in the terminal): the plan's next, the one
    automatic switching moves to, and only while automatic switching runs. Never the account in use or one that
    needs a fresh login. None otherwise."""
    if not (auto or {}).get('on'):
        return None
    email = (planned or {}).get('next')
    a = next((x for x in accounts if x['email'] == email), None)
    if not a or a['active'] or a['needs_login'] or a['unmanaged']:
        return None
    return email


def forecast_view(st, lines, now):
    """For the account in use: {email, lasts, out_at (ISO)} from plan.week_ahead on its own trusted reading, or
    None when there is nothing to say."""
    email = st.get('active')
    a = st['accounts'].get(email) if email else None
    if not a or not a.get('connected') or not reading_trusted(a, now, lines.get('models')):
        return None
    w = (a.get('usage') or {}).get('weekly') or {}
    est = plan.week_ahead(w.get('pct'), core.parse_iso(w.get('resets')), a.get('usage_at'), now)
    if est is None:
        return None
    return {'email': email, 'lasts': est['lasts'], 'out_at': iso(est['out_at']) if est['out_at'] else None}


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


# ---------- the status line's own small file ----------

STATUS_LOCK = threading.Lock()
STATUS_FRESH = 300          # seconds: the file's "at" is renewed this often, so the line can tell a page that stopped


def status_file(st, nxt, now=None):
    """What the status line in Claude Code shows besides Claude Code's own numbers: the account in use and
    the next one, each named by its email, and when the page last said so ('at', whole seconds rounded down, so it is
    never dated past the clock that wrote it). Shaped like the state file, so statusline.py reads either."""
    return {'v': 1, 'active': st.get('active'), 'next': nxt, 'at': int(time.time() if now is None else now)}


def write_status_file(st, nxt, now=None):
    """Written apart from the big state file, atomically, and only when what is on disk differs, or its 'at' is older
    than STATUS_FRESH (or dated ahead: the clock went back). Always data/statusline.json: $SWITCHBOARD_STATE only tells
    the status line where to read, never the server where to write (pointed at the state file, it would overwrite
    it)."""
    now = time.time() if now is None else now
    data = status_file(st, nxt, now)
    path = data_path('statusline.json')
    with STATUS_LOCK:
        disk = core.read_json(path)
        at = disk.get('at')
        same = dict(disk, at=None) == dict(data, at=None)
        fresh = isinstance(at, (int, float)) and not isinstance(at, bool) and 0 <= now - at < STATUS_FRESH
        if not (same and fresh):
            core.write_json(path, data)


# ---------- the account in use: the newer reading, Claude Code's own or the engine's ----------
#
# The status line saves the limits Claude Code hands it (statusline.py, ~/.switchboard/live/<session>.json), on every
# refresh. The newest live record that is under ten minutes old, written after the last switch had settled, and that
# is this account's stands in for the engine's reading of the account in use unless that reading is newer (one dated
# exactly as the record is the record itself), and when that reading is missing or failed. So the page, the menu bar
# and the terminal (which all draw the state) show it; the page and the menu bar get it on every look (view), not only
# on the tick. Each call starts again from the engine's own reading, so the numbers never outlive their record. The
# tick also hands that record to the engine (api.feed_status_line, once per record): Claude Code's own numbers are its
# live source for the account in use, and every look takes the engine's store (fresh_readings), that record included.

LIVE_FRESH = 600            # a live record counts while under ten minutes old
LIVE_SETTLE = 30            # after a switch, a running Claude Code may use the old login ~30 s more (its Keychain cache)
LIVE_NOW = 60               # "now" for the account in use, on Claude Code's own numbers of the last minute
SAME_WINDOW = 300           # two readings of one window carry reset times a few seconds apart (the engine's own slack)
AHEAD = 5                   # a record dated further than this past the clock is not trusted
LIVE_KEYS = ('usage', 'usage_at', 'checked_at', 'usage_status', 'usage_error', 'note')


def _finite(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and x == x \
        and abs(x) != float('inf') else None


def live_records(now):
    """The status line's live records that may be the account in use's: under ten minutes old and not dated ahead,
    from a Claude Code on the default profile, with a window still open (what tells whose numbers they are).
    [{'at', 'account', 'usage'}], newest first; usage in the state's shape. A window whose reset has passed has
    refilled: 0%, its reset kept, which every view draws as not started. A window that is not there is None: the
    account may have no such limit (an Ultra account has no 5-hour one)."""
    out = []
    try:
        entries = list(os.scandir(statusline.live_dir()))
    except OSError:
        return out
    for e in entries:
        try:
            if not e.name.endswith('.json') or not e.is_file(follow_symlinks=False) \
                    or now - e.stat(follow_symlinks=False).st_mtime > LIVE_FRESH + AHEAD:
                continue
        except OSError:
            continue
        rec = core.read_json(e.path)
        at, rl = _finite(rec.get('at')), rec.get('rate_limits')
        if rec.get('v') != 1 or at is None or not isinstance(rl, dict) or rec.get('own_profile') is True \
                or not -AHEAD <= now - at < LIVE_FRESH:
            continue

        def window(w):
            """(the window, True while it is still open)"""
            pct = _finite(w.get('used_percentage')) if isinstance(w, dict) else None
            if pct is None:
                return None, False
            resets = _finite(w.get('resets_at'))
            if resets is not None and resets <= now:
                return refilled(iso(resets)), False
            return {'pct': min(100.0, max(0.0, pct)), 'resets': iso(resets) if resets is not None else None,
                    'pace': None, 'lasts': None}, True
        (session, five_open), (weekly, week_open) = window(rl.get('five_hour')), window(rl.get('seven_day'))
        if not (five_open or week_open):
            continue
        account = rec.get('account')
        out.append({'at': at, 'account': account.lower() if isinstance(account, str) else None,
                    'usage': {'session': session, 'weekly': weekly, 'spend': spend.from_live(rl)}, 'raw': rl,
                    'session_id': rec.get('session_id') if isinstance(rec.get('session_id'), str) else None})
    return sorted(out, key=lambda r: -r['at'])


def refilled(resets):
    """A window whose reset has passed, in the state's shape: 0%, its reset kept (not started, on every view)."""
    return {'pct': 0.0, 'resets': resets, 'pace': None, 'lasts': None}


def _fits(st, email, own, live, now):
    """The record's windows are this account's: one its last reading has still open has the same reset time, and none
    is another account's open window (the numbers a running Claude Code kept from before a switch). A window that has
    refilled since tells nothing either way."""
    for key in ('session', 'weekly'):
        resets = core.parse_iso((live.get(key) or {}).get('resets'))
        if resets is None or resets <= now:
            continue
        mine = core.parse_iso(((own or {}).get(key) or {}).get('resets'))
        if mine is not None and mine > now:
            if abs(resets - mine) > SAME_WINDOW:
                return False
            continue
        for other, b in st['accounts'].items():
            theirs = core.parse_iso((((b.get('usage') or {}).get(key)) or {}).get('resets')) \
                if other != email and b.get('connected') else None
            if theirs is not None and theirs > now and abs(resets - theirs) <= SAME_WINDOW:
                return False
    return True


def _engines_own(a):
    """An account shown on Claude Code's own numbers (its 'live' mark) back to the engine's own reading, kept under
    them ('was'), unless its numbers changed since they were put there."""
    prev = a.pop('live', None)
    if prev and a.get('usage') == prev.get('usage') and a.get('usage_at') == prev.get('at'):
        was = prev.get('was') or {}
        a.update(usage=was.get('usage'), usage_at=was.get('usage_at'), checked_at=was.get('checked_at'))
        if (a.get('usage_status'), a.get('usage_error'), a.get('note')) == ('ok', None, None):
            a.update(usage_status=was.get('usage_status'), usage_error=was.get('usage_error'), note=was.get('note'))


def apply_live_numbers(st, now):
    """Give the account in use Claude Code's own numbers when they are newer than the engine's reading (see above).
    Runs after the engine's rows are applied (apply_rows), on every tick, on every look at the state (view) and in the
    terminal's own look; calling it again without new rows gives the same result."""
    for a in st['accounts'].values():                  # first, back to the engine's own reading
        _engines_own(a)
    email = st.get('active')
    a = st['accounts'].get(email) if email else None
    if not a or not a.get('connected') or st.get('disagree'):
        return                                         # its config and its login disagree: whose numbers is not known
    u, at = a.get('usage'), _finite(a.get('usage_at'))
    failed = bool(a.get('usage_error')) or a.get('usage_status') not in (None, 'ok')
    timeline = st.get('timeline') or []
    if not timeline or timeline[-1][0] != email:
        return                                         # when Claude Code came to this account is not known
    since = max(_finite(timeline[-1][1]) or 0, _finite(st.get('switched_at')) or 0) + LIVE_SETTLE
    for rec in live_records(now):
        if rec['at'] <= since or (rec['account'] and rec['account'] != email.lower()) \
                or not _fits(st, email, u, rec['usage'], now):
            continue
        # The engine's own reading stays when it is the newer one. One dated exactly as the record is that record, which
        # the tick handed the engine (feed_live): Claude Code's own numbers still, and "now" for their minute.
        if u and at is not None and at > rec['at'] and not failed:
            return

        def kept(key):
            """A window the record has not (Claude Code drops one once its reset passes; an Ultra account has no
            5-hour one): the engine's while still open, refilled once its reset passed, None when neither has one."""
            w = (u or {}).get(key)
            if rec['usage'][key] is not None or not isinstance(w, dict):
                return rec['usage'][key]
            t = core.parse_iso(w.get('resets'))
            return dict(w) if t is None or t > now else refilled(w.get('resets'))
        usage = dict(session=kept('session'), weekly=kept('weekly'),
                     scoped=[x for x in (u or {}).get('scoped') or [] if isinstance(x, dict)
                             and (core.parse_iso(x.get('resets')) or now + 1) > now],
                     spend=rec['usage'].get('spend') or (u or {}).get('spend'))
        a['live'] = {'at': rec['at'], 'usage': usage, 'was': dict((k, a.get(k)) for k in LIVE_KEYS),
                     'raw': rec.get('raw'), 'session_id': rec.get('session_id')}
        a.update(usage=usage, usage_at=rec['at'], checked_at=max(rec['at'], _finite(a.get('checked_at')) or 0.0),
                 usage_status='ok', usage_error=None, note=None)
        return


FED = {}                    # the last record handed to the engine: {'key': (account id, session, at)}


def feed_view(st):
    """The live record the account in use is on now (apply_live_numbers), as the engine takes it, or None:
    (account id, Claude Code's rate_limits as it handed them, when)."""
    email = st.get('active')
    a = st['accounts'].get(email) if email else None
    live = (a or {}).get('live') or {}
    if not a or not a.get('id') or a.get('unmanaged') or not isinstance(live.get('raw'), dict):
        return None
    return a['id'], live['raw'], live.get('at'), live.get('session_id')


def feed_live(live):
    """Hand the account in use's newest live record to the engine (api.feed_status_line), once per record."""
    if not live:
        return False
    account_id, raw, at, session = live
    key = (account_id, session, at)
    if FED.get('key') == key:
        return False
    try:
        api.feed_status_line(account_id, raw, at)
    except NotImplementedError:
        return False
    FED['key'] = key
    return True


def live_now(st, email, now):
    """The account in use is on Claude Code's own numbers (apply_live_numbers) from the last minute: the page and the
    menu bar say "now" for it."""
    a = st['accounts'].get(email) or {}
    at = _finite(a.get('usage_at'))
    return email == st.get('active') and bool(a.get('live')) and at is not None and -AHEAD <= now - at <= LIVE_NOW


# ---------- the readings store between ticks ----------
#
# The loop that reads the accounts saves each answer in usage.json, in its own process. The tick takes the numbers in
# with the engine's listing, every one to four minutes (ease_tick). Every look at the state (view) and every pass of
# the page's loop take them sooner: one stat of usage.json, and only when it changed, its readings again (no Keychain,
# no listing). The numbers go into the state that is drawn, in the fields apply_rows fills from a reading, never into
# the state file: what a notification or the history compares is the tick's own before and after, which must still
# see a number cross a line.

FRESH = {'sig': None, 'got': {}}
FRESH_LOCK = threading.Lock()
ROWS = {'by_email': {}}             # the tick's last rows, by email: what a reading's note is worked out with
# Also in ROWS, for the loop's early reading (wake_if_behind): 'stamp' is accounts.json as it was when the tick last
# asked the engine for its rows (no key before the first tick); 'said' is what those rows said (rows_said); 'same' is
# whether that reading said what the one before it said; 'woke' is when a change in accounts.json last woke the loop.
ROW_FACTS = ('id', 'number', 'email', 'org_id', 'org_name', 'plan', 'tier', 'org_type', 'alias', 'status', 'active',
             'unmanaged', 'refresh_expires_at', 'emptied')


def rows_stamp():
    """accounts.json as it is now (the engine's rows are read from it), or None when it is not there."""
    try:
        s = os.stat(data_path('accounts.json'))
    except (OSError, TypeError, ValueError):
        return None
    return (s.st_mtime_ns, s.st_size, s.st_ino)


def rows_said(rows):
    """What the engine's rows say about the accounts themselves: who is kept, in what state, until when, and which
    one is in use. Not a reading's numbers, which are new at every reading."""
    return [tuple(r.get(k) for k in ROW_FACTS) for r in rows]


def rows_behind():
    """Did accounts.json change after the tick last asked the engine for its rows? Then a row can be older than what
    the engine holds. A login kept after the rows were read (by the pass of login keeping that follows them in the
    same tick, or by the loop that reads the accounts, which is another process) left its row saying "Sign in again",
    with the old end date, until the next regular reading: a minute later, or four on a busy Mac."""
    return 'stamp' in ROWS and rows_stamp() != ROWS['stamp']


def wake_if_behind(every, now=None):
    """Wake the loop when the rows are behind the engine's accounts (rows_behind), so they are read again now and not
    at the next regular reading. `every` is the seconds between two regular readings. Returns True when it woke the
    loop. Never raises.

    How often: after a reading that said something new (a sign-in is going on), at most once in a quarter of `every`.
    After a reading that said what the one before it said, or that failed, at most once in `every`, the regular gap
    itself. So a file that is written again and again with nothing new for a row in it (a login the Keychain will not
    give up is noted at every pass) never makes the page read more often than it always did. A change inside that
    time is not lost: it wakes the loop when the time is over."""
    try:
        if not rows_behind():
            return False
        now = monotonic() if now is None else now
        woke = ROWS.get('woke')
        if woke is not None and 0 <= now - woke < (every if ROWS.get('same') else every / 4.0):
            return False
        ROWS['woke'] = now
        WAKE.set()
        return True
    except Exception as e:  # noqa: BLE001 - keep the page alive
        log('looking at the accounts file failed: %r' % e)
        return False


def fresh_readings():
    """(changed, {account id: engine.reading_fields of its reading}) from the readings store, read again only when
    usage.json changed since the last look: one stat otherwise."""
    try:
        s = os.stat(data_path('usage.json'))
        sig = (s.st_ino, s.st_mtime_ns, s.st_size)
    except OSError:
        sig = None
    from . import readings as taken
    sig = (sig, taken.signature())          # readings taken from another Mac (readings.py) come and go too
    with FRESH_LOCK:
        if sig == FRESH['sig']:
            return False, FRESH['got']
    got = {}
    if sig != (None, None):
        try:
            got = dict((aid, engine.reading_fields(r)) for aid, r in (api.readings() or {}).items())
        except (api.AccountsError, NotImplementedError, OSError, ValueError) as e:
            log('reading the readings: %r' % e)
            return False, FRESH['got']
    with FRESH_LOCK:
        FRESH.update(sig=sig, got=got)
    return True, got


def apply_readings(st, got):
    """The store's readings (fresh_readings) into the state, account by account, in the fields apply_rows fills from
    a row's reading, with the note worked out as the tick does. Only accounts the tick has seen (ROWS): their other
    facts (a login refused, a conflict) come from it. Then apply_live_numbers, as after apply_rows."""
    rows = ROWS['by_email']
    for email, a in st['accounts'].items():
        row = rows.get(email)
        fields = got.get(a.get('id') or a.get('account_id')) if row and a.get('connected') else None
        if row and a.get('connected'):
            taken_gone(a, (fields or {}).get('elsewhere'))
        if not fields:
            continue
        r = dict(row, **fields)
        _engines_own(a)                 # the engine's own reading under the store's; apply_live_numbers takes the newer
        a.update(usage=r.get('usage') or a.get('usage'), usage_at=r.get('usage_at') or a.get('usage_at'),
                 checked_at=r.get('checked_at') or a.get('checked_at'), source=r.get('source'),
                 frozen=bool(r.get('frozen')), usage_error=r.get('usage_error'), note=engine.note_of(r),
                 usage_status='frozen' if r.get('frozen') else 'error' if r.get('usage_error') else 'ok',
                 models_seen=r.get('models_seen') or a.get('models_seen') or [], elsewhere=bool(r.get('elsewhere')))


def readings_pass():
    """Each pass of the page's loop, whatever the load: a reading saved since the last look makes the next GET
    /api/state a new one (view takes it in). True when there was one."""
    changed, _ = fresh_readings()
    if changed:
        SNAPSHOT.invalidate()
    return changed


# ---------- notifications and keeping the Mac awake ----------

MAC_LOCK = threading.Lock()
HELPERS = {'awake': None}          # tests put an Awake with a fake process here: no test starts caffeinate
NOTIFY_RUN = notify._run            # and a fake here: no test ever shows a notification


def mac_facts():
    """The switches' rows. 'notify' is the Notifications switch: on only when Claude Code's "done" and "needs you"
    hooks are in and can run as well (notify_hooks.facts); Switchboard's own notes follow helper_settings()."""
    sl, h = statusline_status(), helper_settings()
    return dict(h, statusline=sl['on'], statusline_theirs=sl['theirs'], **notify_hooks.facts(h['notify']))


def helper_settings():
    """Keep the Mac awake, and notifications: both on unless turned off."""
    s = settings()
    return {'awake': s.get('awake') is not False, 'notify': s.get('notify') is not False}


def awake_helper():
    if HELPERS['awake'] is None:
        HELPERS['awake'] = awake.Awake()
    return HELPERS['awake']


def changes_view(st):
    """The parts of the state a notification is about: the account in use, names, limits, and when each sign-in ends
    (and whether the account needs a sign-in)."""
    return {'active': st.get('active'),
            'accounts': dict((e, {'alias': a.get('alias'), 'slot': a.get('slot'), 'usage': copy.deepcopy(a.get('usage')),
                                  'signin_ends_at': a.get('signin_ends_at'), 'needs_login': bool(a.get('needs_login'))})
                             for e, a in st['accounts'].items() if a.get('connected'))}


def tell(before, after, lines, now):
    """One notification for each switch the engine made by itself, each account that crossed its line
    (the landing room under it) while Switchboard watched, and each refill of a window that had. A switch
    made from here is already in `before`, so it is not told; an account first seen already near is not
    told either (no burst of notes the first time Switchboard runs)."""
    if core.DEMO or not helper_settings()['notify']:
        return
    near = {'seven_day': lines['weekly_line'] - lines['landing_room'],
            'five_hour': lines['session_line'] - lines['landing_room']}
    groups = by_own_line(before, after, lines)
    already = dict((n.key, n.instance) for b, _, line in groups for n in notify.from_states(b, b, line=line, now=now)
                   if n.key.startswith('near:'))

    def same_window(key, instance):
        if key not in already:
            return False
        was = already[key]
        if isinstance(was, (int, float)) and isinstance(instance, (int, float)):
            return abs(was - instance) < notify.SAME_WINDOW          # a reset time that moved a little is the same window
        return was == instance
    known = set()
    for email, a in (before.get('accounts') or {}).items():
        u = (a or {}).get('usage') or {}
        for window, key in (('five_hour', 'session'), ('seven_day', 'weekly')):
            if notify.window_of(u.get(key))[0] is not None:
                known.add('near:%s:%s' % (notify.account_label(before, email), window))
    notes = [n for n in notify.from_states(before, after, line=near, now=now) if n.key.startswith('switch:')]
    notes += [n for b, a, line in groups for n in notify.from_states(b, a, line=line, now=now)]
    for note in notes:
        if note.key.startswith('near:') and (note.key not in known or same_window(note.key, note.instance)):
            continue
        if note.key.startswith('switch:'):
            note = with_switch_lines(note, after)
        try:
            notify.send(note, run=NOTIFY_RUN, now=now)
        except Exception as e:  # noqa: BLE001 - one note that fails must not stop the others
            log('notification failed: %r' % e)
    due = signin_notes(before, after, now)
    if due:
        try:                            # several due in one tick are one note: four ending the same week are no burst
            notify.send_together([n for n, _ in due], lambda new: signins_together(new, dict(due), after),
                                 run=NOTIFY_RUN, now=now)
        except Exception as e:  # noqa: BLE001
            log('notification failed: %r' % e)


def by_own_line(before, after, lines):
    """[(before, after, near)]: the accounts grouped by where "near its limit" starts for them (each window's switch
    line, the account's own where it has one, less the landing room), each group as states of its own with no account
    in use, so a switch is said once (from the whole states) and each account is near at its own line."""
    own = lines.get('own_lines') if isinstance(lines.get('own_lines'), dict) else {}
    groups = {}
    for email, a in (after.get('accounts') or {}).items():
        mine = own.get((a or {}).get('id')) or {}
        at = (mine.get('weekly_at', lines['weekly_line']) - lines['landing_room'],
              mine.get('session_at', lines['session_line']) - lines['landing_room'])
        groups.setdefault(at, []).append(email)

    def only(st, emails):
        accounts = st.get('accounts') or {}
        return {'accounts': dict((e, accounts[e]) for e in emails if e in accounts)}
    return [(only(before, emails), only(after, emails), {'seven_day': week, 'five_hour': five})
            for (week, five), emails in sorted(groups.items())]


# Notes about sign-ins: Anthropic ends each by a set date, which renewing does not move, and it can end any time from
# term.SIGNIN_WINDOW before that date. So each is said ahead of the earliest it can end, at these many seconds before
# it (the 3-day note, then the 1-day note), and once when it has ended.
SIGNIN_STAGES = ((86400, 1), (term.SIGNIN_NEAR, 3))
SIGNIN_AGAIN = term.SIGNIN_TODO         # every sign-in note ends with the one thing to do: Sign in to all


def signin_notes(before, after, now):
    """[(Note, what it says of one account: {label, when, ended, in_use})] for each sign-in due a note now (changes_view
    states). The 3-day and the 1-day note once each, measured from the earliest it can end (term.signin_earliest):
    keyed by the account and the stage, the end date its instance,
    so a restart or a page left open never says one again and a new sign-in's date brings them back. The account in
    use is said like any other (it signs in again from the page too). The ended note once, keyed by its date, and only
    for an account seen to go from working to ended between two ticks: one that already needed a sign-in when this build first
    looks (an older build's state, or no state yet) or was refused before its date is not told again. An account that
    needs a sign-in for another reason (refused before its date) gets none, as before. Soonest first."""
    out = []
    was = before.get('accounts') or {}
    for email, a in (after.get('accounts') or {}).items():
        a = a or {}
        at, need = _seconds(a.get('signin_ends_at')), bool(a.get('needs_login'))
        label = notify.account_label(after, email)
        if at is None:
            continue
        if need:
            b = was.get(email)
            if at <= now and b is not None and not b.get('needs_login'):
                out.append((notify.Note('signin:%s:ended' % label, 'Switchboard', 'Sign-in for %s ended. %s'
                                        % (label, SIGNIN_AGAIN), at, None),
                            {'label': label, 'at': at, 'ended': True, 'in_use': email == after.get('active')}))
            continue
        earliest = term.signin_earliest(at)
        stage = next((n for ahead, n in SIGNIN_STAGES if at > now and earliest - now <= ahead), None)
        if stage is None:
            continue
        when = term.signin_words(at, now)[len('Sign-in ends '):]
        in_use = email == after.get('active')
        out.append((notify.Note('signin:%s:%d' % (label, stage), 'Switchboard', 'Sign-in for %s ends %s. %s'
                                % (label, when, SIGNIN_AGAIN), at, None),
                    {'label': label, 'at': at, 'when': when, 'ended': False, 'in_use': in_use}))
    return sorted(out, key=lambda x: x[1]['at'])


SIGNIN_NAMED = 3            # a note about several sign-ins names this many at most, then how many more


def signins_together(notes, said, after):
    """Several sign-in notes due in one tick, as one: 'Sign-ins end soon: <email> <day and time>; <email> <day and
    time>; <email> <day and time>; and 2 more. Open Switchboard and press Sign in to all.' (an ended one: '<email>
    ended'), soonest first. At most three named, fewer when the emails are long, so the body fits the room a note has
    (NOTE_ROOM) and what to do is never cut off."""
    items = sorted((said[n] for n in notes), key=lambda x: x['at'])
    head = 'Sign-ins: ' if any(x['ended'] for x in items) else 'Sign-ins end soon: '
    words = ['%s ended' % x['label'] if x['ended'] else '%s %s' % (x['label'], x['when']) for x in items]
    how = SIGNIN_AGAIN
    for k in range(min(SIGNIN_NAMED, len(words)), 0, -1):
        body = '%s%s. %s' % (head, '; '.join(words[:k] + (['and %d more' % (len(words) - k)] if len(words) > k else [])),
                             how)
        if len(body) <= NOTE_ROOM:
            break
    return notify.Note('signin:together', 'Switchboard', body, None, None)


NOTE_ROOM = 240             # notify.py cuts a body past this many characters


def with_switch_lines(note, after):
    """A switch's notification with what the person should know about the account it went to (no profile signed in
    as it, or Claude in Chrome without its extension there; artifacts.switch_note, naming it as the notification does,
    by its email) and, the first time, the desktop app's line (artifacts.desktop_note), when it fits."""
    to = after.get('active')
    if not to:
        return note
    body = note.body
    extra = _switch_note(load(), to, label=notify.account_label(after, to))
    if extra and len(body) + 1 + len(extra) <= NOTE_ROOM:
        body = '%s %s' % (body, extra)
    if len(body) + 1 + len(artifacts.DESKTOP_NOTE) <= NOTE_ROOM:
        first = artifacts.desktop_note()            # said once: only when it fits, so it is never cut off
        if first:
            body = '%s %s' % (body, first)
    return note._replace(body=body)


def mac_tick(now=None):
    """Hold the Mac awake while Claude Code works, let go after; say "plug in" when it matters."""
    if core.DEMO:
        return None
    on = helper_settings()
    helper = awake_helper()
    status = None
    if on['awake']:
        status = helper.tick(now)
    elif helper.holding():
        helper.release()
    if on['notify']:
        status = status or helper.status(now)
        if helper.should_warn(status, now):
            notify.send(notify.plug_in(helper.warning), run=NOTIFY_RUN)
    return status


# ---------- History's work, last 7 days (read in the background; a request never waits for it) ----------

USAGE_DAYS = 7
USAGE_EVERY = 300           # read again at most every five minutes on its own
USAGE_ASKED = 30            # and at most every half minute when the page asks for fresh numbers
USAGE_STALE = 3600          # a week this old is read again even while the Mac is very busy
WEEK_FILE = 'week.json'     # the last week read, kept so that a restart shows it at once
WEEK_KEPT_FOR = 86400       # a kept week read longer ago than this is not shown after a restart: it is read first
WEEK_AHEAD = 600            # nor one dated further ahead than this (the clock was set back)
USAGE = {'page': None, 'at': 0, 'running': False}
USAGE_LOCK = threading.Lock()


def usage_view(fresh=False):
    """What History's Work draws, or {'state': 'reading'} until the first read is done. A read dated in the future (the
    clock was set back) counts as old, so a clock change never stops the reads; a disk almost full reads no more."""
    if core.DEMO:
        from . import demo
        return dict(demo.usage(), state='ready')
    now = time.time()
    with USAGE_LOCK:
        page, age = USAGE['page'], now - USAGE['at']
        if age < 0:
            age = float('inf')
        if not USAGE['running'] and (page is None or age >= USAGE_EVERY or (fresh and age >= USAGE_ASKED)) \
                and not (page is not None and (DISK['low'] or (easing() and age < USAGE_STALE))):
            USAGE['running'] = True
            threading.Thread(target=read_usage, daemon=True).start()
    return dict(page, state='ready') if page is not None else {'state': 'reading'}


def read_usage():
    extras = None
    try:
        until = time.time()
        summary = usage.summarize(core.projects(), until - USAGE_DAYS * 86400, until,
                                  cache_path=data_path('usage-cache.json'), series_since=insights.series_start(until))
        page = dict(usage.for_page(summary, USAGE_DAYS), at=until)
        extras = models_from_history(summary)
        page['claude_mem'] = claudecode.share(until)        # None unless claude-mem is managed and installed
        page.update(insights.read_week(summary, until, page))   # the week under the accounts (never raises)
    except Exception as e:  # noqa: BLE001 - the page says "not measured" instead
        log('reading the history failed: %r' % e)
        page = {'days': USAGE_DAYS, 'cuts': {}, 'moved': None, 'failed': True}
    done = time.time()
    if not page.get('failed'):
        try:
            core.write_json(data_path(WEEK_FILE), {'page': page, 'extras': extras, 'at': done})
        except (OSError, TypeError, ValueError) as e:
            log('the week was not kept: %r' % e)
    with USAGE_LOCK:
        last = USAGE['page']
        if page.get('failed') and isinstance(last, dict) and not last.get('failed'):
            page, extras = last, USAGE.get('extras')        # one read that failed: the last week read stays on the page
        USAGE.update(page=page, extras=extras, at=done, running=False)


def load_week(now=None):
    """The last week read, kept on disk: a restart shows it at once, with its own dates, and usage_view reads it again
    when it is old. A week read more than a day ago, or in a shape this page does not draw, waits for the first read
    instead. One whose hours do not say where they went (an earlier build kept it) counts as old. True when one was
    taken."""
    now = time.time() if now is None else now
    try:
        with open(data_path(WEEK_FILE), encoding='utf-8') as f:
            kept = json.load(f)
        page, at, extras = kept.get('page'), kept.get('at'), kept.get('extras')
    except (OSError, ValueError, AttributeError):
        return False
    if not isinstance(page, dict) or page.get('failed') or not isinstance(page.get('cuts'), dict) \
            or not isinstance(page.get('series'), dict) or not isinstance(at, (int, float)) or isinstance(at, bool) \
            or not -WEEK_AHEAD <= now - at < WEEK_KEPT_FOR:           # NaN fails this too
        return False
    if not _says_where(page):
        at = min(at, now - USAGE_STALE)         # kept by an earlier build: shown now, read again at the first chance
    with USAGE_LOCK:
        if USAGE['page'] is not None:
            return False
        USAGE.update(page=page, extras=extras if isinstance(extras, dict) else None, at=float(at))
    return True


def _says_where(page):
    """Whether a kept week's hours say where they went (their projects, models and the rest): one kept by a build from
    before the hours did has only their weight and who."""
    hours = (page.get('series') or {}).get('hours')
    used = [h for h in hours if isinstance(h, dict) and h.get('weight')] if isinstance(hours, list) else []
    return not used or any('projects' in h for h in used)


# ---------- Chats: what is going on in each account (chats.py), read in the background ----------
#
# A look at every chat is started from the loop, never for a request: GET /api/chats answers with the last view, and
# each account's `chats` block in GET /api/state comes from it too. Once the first look is done, a look runs every
# minute; while it is still reading, a few bounded passes run back to back each time the loop comes round.

CHATS_EVERY = 60.0          # seconds (monotonic) between looks once the first one is done
CHATS_PASSES = 6            # passes in one go while the first look is still reading
CHATS_GAP = 0.5             # seconds between those passes
CHATS = {'view': None, 'at': None, 'running': False}
CHATS_LOCK = threading.Lock()


def chats_tick(now=None):
    """Start a look at the chats when one is due (chats_look, on a thread of its own). True when it started one.
    Never while the disk is almost full or the Mac is under load, once there is a view to show."""
    if core.DEMO:
        return False
    now = monotonic() if now is None else now
    with CHATS_LOCK:
        v, at = CHATS['view'], CHATS['at']
        reading = v is None or not (v.get('reading') or {}).get('done')
        due = not CHATS['running'] and (at is None or reading or not 0 <= now - at < CHATS_EVERY)
        if not due or (v is not None and (DISK['low'] or easing())):
            return False
        CHATS['running'] = True
    # The folders and the accounts as they are now: the look keeps to them however long it takes.
    where = {'st': load(), 'root': core.projects(), 'folder': core.data_dir()}
    try:
        threading.Thread(target=chats_look, kwargs=where, name='switchboard-chats', daemon=True).start()
    except RuntimeError:
        with CHATS_LOCK:
            CHATS['running'] = False
        return False
    return True


def chats_look(st=None, root=None, folder=None, passes=CHATS_PASSES, gap=CHATS_GAP):
    """Look at the chats (chats.look, a bounded pass at a time, up to `passes` while there is more to read), then
    keep the view for GET /api/chats and the state."""
    v = None
    try:
        st = load() if st is None else st
        for i in range(passes):
            _, side = chats.look(st=st, root=root, folder=folder)
            if not side['look'].get('left'):
                break
            if i + 1 < passes:
                time.sleep(gap)
        v = chats.build(st, folder=folder)
    except Exception as e:  # noqa: BLE001 - the last view stays; the next tick tries again
        log('looking at the chats failed: %r' % e)
    with CHATS_LOCK:
        CHATS.update(view=v if v is not None else CHATS['view'], at=monotonic(), running=False)
    SNAPSHOT.invalidate()


def chats_view():
    """GET /api/chats: the last look's view (chats.view), or {'state': 'reading'} before the first one ends."""
    if core.DEMO:
        from . import demo
        return demo.chats()
    with CHATS_LOCK:
        v = CHATS['view']
    return v if v is not None else {'state': 'reading', 'accounts': [], 'chats': []}


def chats_counts():
    """{email: {'on', 'working', 'elsewhere'}} for each account's row in the state, from the last view; {} before
    the first look ends (the page then shows no counts)."""
    with CHATS_LOCK:
        v = CHATS['view']
    return chats.counts(v) if v is not None else {}


# ---------- every assistant on one board (providers/view.py), built in the background ----------
#
# The board is built from files only (it starts no program and asks no app), on a thread of its own started from the
# loop: the first time, every PROVIDERS_EVERY seconds, and at once when one of its files changes. A request never
# waits for it: the state carries the last board (facts' `providers`), from its next build on. After each build, the
# one notification the board gives (view.out_note) goes through notify.send, which never shows the same out spell
# twice.

PROVIDERS_EVERY = 300.0     # seconds (monotonic) between two builds when nothing changed
PROVIDERS = {'view': None, 'at': None, 'sig': None, 'running': False}
PROVIDERS_LOCK = threading.Lock()


def providers_files():
    """The files the board is built from: the providers' settings, readings and programs, and the account engine's
    accounts and readings (the first provider's line is worked out from them)."""
    from .providers import contract as pc
    return [data_path(pc.SETTINGS_FILE), data_path(pc.READINGS_FILE), data_path(pc.PROGRAMS_FILE),
            data_path('accounts.json'), data_path('usage.json')]


def _stat_sig(paths):
    out = []
    for p in paths:
        try:
            s = os.stat(p)
            out.append((s.st_mtime_ns, s.st_size, s.st_ino))
        except OSError:
            out.append(None)
    return tuple(out)


def _start_thread(target, kwargs):
    threading.Thread(target=target, kwargs=kwargs, name='switchboard-providers', daemon=True).start()


def providers_tick(now=None, start=None):
    """Start a build of the board when one is due (providers_look; start runs it, on a thread of its own unless a
    test says otherwise). True when it started one. Never two at once, never in the demo."""
    if core.DEMO:
        return False
    now = monotonic() if now is None else now
    sig = _stat_sig(providers_files())
    with PROVIDERS_LOCK:
        at = PROVIDERS['at']
        due = not PROVIDERS['running'] and (at is None or sig != PROVIDERS['sig'] or
                                            not 0 <= now - at < PROVIDERS_EVERY)
        if not due:
            return False
        PROVIDERS['running'] = True
    try:
        (start or _start_thread)(providers_look, {'sig': sig, 'now': now})
    except RuntimeError:                    # no thread could start: the next pass tries again
        with PROVIDERS_LOCK:
            PROVIDERS['running'] = False
        return False
    return True


def providers_look(sig=None, now=None):
    """Build the board (providers.view) and keep it for the state; a build that fails keeps the last one. Then its
    notification, when one is due."""
    v = None
    try:
        from .providers import view as provider_view
        v = provider_view.view()
    except Exception as e:  # noqa: BLE001 - the last board stays; the next pass tries again
        log('building the board of assistants failed: %r' % e)
    with PROVIDERS_LOCK:
        PROVIDERS.update(view=v if v is not None else PROVIDERS['view'], running=False,
                         at=monotonic() if now is None else now, sig=sig)
    if v is not None:
        providers_note(v)
    # No SNAPSHOT.invalidate() here: the state is built again at the next look past its age (Snapshot.MAX_AGE), and
    # a forced build from a background look lands in the middle of whatever the page is asking for.


def providers_view():
    """The last board built, for the state; None before the first build ends (the page then shows no group)."""
    with PROVIDERS_LOCK:
        return PROVIDERS['view']


def providers_note(board, now=None):
    """The board's one notification (view.out_note): the first provider in the person's order is out and another
    added one has room. Once per out spell (notify.send keeps the provider and its refill time). Never in the demo or
    with notifications off. True when it was shown."""
    if core.DEMO or not helper_settings()['notify']:
        return False
    from .providers import view as provider_view, words as provider_words
    now = time.time() if now is None else now
    got = provider_view.out_note(board, now)
    if got is None:
        return False
    key, body, instance = got
    try:
        return notify.send(notify.Note(key, provider_words.NOTE_OUT_TITLE, body, instance, None), run=NOTIFY_RUN,
                           now=now)
    except Exception as e:  # noqa: BLE001 - a note that fails never stops the board
        log('notification failed: %r' % e)
        return False


# ---------- models by the job: one setting in Claude Code's user settings ----------
#
# The one change offered: CLAUDE_CODE_SUBAGENT_MODEL=sonnet in the `env` of Claude Code's user settings, so
# helpers and workflow steps that name no model run on Sonnet (models.py, preset "by-the-job" without its
# Explore file). Off puts the file back as it was (models.undo), or, when Claude Code or you changed the file
# since, takes out only that one key (preset "off"). It never waits for chats to finish: a change reaches only
# helpers started afterwards (docs, env_running: "A running session applies new and changed values to its
# environment when you save the file ... Removing a variable from the file doesn't unset it in a running
# session; the removal takes effect the next time you launch `claude`.").

MODELS_KW = {}                  # tests point these at made-up folders: environ, managed_dir, versions
HELPER_NAMES = {'workflow agents': 'Workflow steps', 'general-purpose': 'Helpers', 'forks': 'Forks',
                'claude': 'Claude', '(unknown)': 'Unknown'}


def models_from_history(summary):
    """From the week's history: the share "Sonnet for helpers" would have moved, and what each kind of
    helper ran on (whole percents)."""
    moved = models.would_have_moved(summary, lookups=False)
    log_rows = []
    for h in models.seen(summary).get('helpers') or []:
        share = int(round(100 * h['share']))
        if share < 1:
            continue
        fams = [{'family': m['family'], 'share': int(round(100 * m['share']))} for m in h['models'][:2]]
        log_rows.append({'helper': HELPER_NAMES.get(h['helper'], h['helper']), 'share': share,
                         'models': [m for m in fams if m['share'] >= 1]})
    return {'moved': usage.moved_share(summary), 'exact': bool(moved['exact']), 'log': log_rows[:5]}


def _models_state():
    """(the picture models.py reads, whether Switchboard's setting is in place, what else decides it)."""
    pic = models.current(check_busy=False, **MODELS_KW)
    key = 'env.' + models.SUBAGENT_VAR
    on = any(o['what'] == 'setting' and o['key'] == key for o in pic['ours'])
    decided = None
    if pic['force']:
        decided = {'what': 'force', 'where': pic['force']}
    else:
        yours = dict((k, v['value']) for k, v in pic['env'].items() if v['set_by'] == 'you')
        for where, env in (('managed settings', pic['managed']['env']), ('your user settings', yours),
                           ('the environment Claude Code starts from', pic['environ'])):
            if (env.get(models.SUBAGENT_VAR) or '').strip() not in ('', 'inherit'):
                decided = {'what': 'own', 'where': where}
                break
    return pic, on, decided


def _refusal(text):
    """A refusal from models.py as one short line for the page (its own text explains more, for the log)."""
    log('models: %s' % text)
    if 'changed since' in text or 'changed after' in text:
        return "Claude Code's settings changed just now. Nothing was written; try again."
    if 'could not make this edit' in text:
        return 'Switchboard could not make that change. Nothing was written.'
    if any(k in text for k in ('read-only', 'is older than', 'no Claude Code folder', 'record')):
        return _blocked_why(text)
    return 'Switchboard could not make that change. Nothing was written.'


def _blocked_why(text):
    """A plan's refusal, as one short line."""
    t = text or ''
    if 'read-only' in t:
        return "Claude Code's settings file is set read-only, so Switchboard leaves it alone."
    if 'is older than' in t:
        return 'Needs Claude Code %s or newer.' % models._vs(models.NEEDS_ORDER)
    if 'no Claude Code folder' in t:
        return 'Claude Code is not set up here yet.'
    if "can't be read" in t and 'record' in t:
        return "Switchboard's record of this setting can't be read."
    return "Claude Code's settings file can't be read." if t else None


def models_view():
    """GET /api/models: on or off, what else decides it (by name), what it would have moved last week, and
    what each kind of helper ran on. Never waits for the history read."""
    if core.DEMO:
        from . import demo
        return demo.models()
    with USAGE_LOCK:
        extras = USAGE.get('extras')
    try:
        pic, on, decided = _models_state()
        why = None if on else _models_left(pic)
        unknown = why is not None                                # its key may be there: never shown as off
        blocked = None
        if not on and not decided and not unknown:
            blocked = _blocked_why(models.plan('by-the-job', lookups=False, **MODELS_KW)['blocked'])
    except OSError as e:
        unknown = True
        why = _models_record_line() if e.filename and os.path.abspath(e.filename) == os.path.abspath(_models_record()) \
            else "Claude Code's settings file can't be read."
    if unknown:
        out = {'enabled': None, 'unreadable': True, 'decided_by': None, 'blocked': None, 'model': 'Sonnet',
               'why': why}                                       # the one line `switchboard models` says
        out.update(extras or {'moved': None, 'exact': None, 'log': []})
        return out
    out = {'enabled': on, 'decided_by': decided, 'blocked': blocked, 'model': 'Sonnet'}
    out.update(extras or {'moved': None, 'exact': None, 'log': []})
    return out


def models_turn(on):
    """POST /api/models: put Switchboard's one setting in place, or take it back. Returns {'changed', 'note'}:
    note 'next' when open chats will use it for their next helpers, 'restart' when they keep the old value
    until they restart, None when no chat is open (or nothing changed)."""
    try:
        return _models_turn(on)
    except OSError as e:
        if e.filename and os.path.abspath(e.filename) == os.path.abspath(_models_record()):
            raise SwitchError(_models_record_line())
        raise SwitchError("Claude Code's settings file can't be read.")


def _models_record():
    return MODELS_KW.get('state_path') or models.default_state_path()


def _models_record_line():
    h, p = os.path.normpath(core.home()), os.path.normpath(_models_record())
    return "Switchboard's record of this setting (%s) can't be read." % (
        '~' + p[len(h):] if p.startswith(h + os.sep) else p)


def _models_owned():
    """What Switchboard's record says its Models setting owns in this Claude Code folder ({'settings', 'agents'}).
    Raises models.Refused, or OSError, when the record can't be read or understood."""
    st, _ = models._load_state(_models_record())
    return models._dir_state(st, models._cd(MODELS_KW.get('claude_dir')))['owned']


def models_recorded():
    """For the uninstaller: does Switchboard's record say its Models setting (or a helper file of its own) is in this
    Claude Code folder? A record that can't be read counts as yes, the cautious answer."""
    try:
        owned = _models_owned()
        return bool(owned.get('settings') or owned.get('agents'))
    except Exception:  # noqa: BLE001 - Refused, or the file can't be opened
        return True


def _models_left(pic):
    """Why Switchboard's key may still be in Claude Code's settings with no way to take it out now, or None. Either
    its record says it set the key while the settings file can't be understood, or the record itself can't be read
    or understood (the cautious answer: it may have set it) while the file holds that key or can't be understood:
    Switchboard can't tell its own key from one the person set. A key that is plainly not there leaves nothing."""
    if pic['state_problem']:
        if pic['settings_problem'] or models.SUBAGENT_VAR in pic['env']:
            return _models_record_line()
        return None
    if not pic['settings_problem']:
        return None
    try:
        owned = _models_owned()['settings']
    except models.Refused:                      # it changed since current() read it, and can't be read now
        return _models_record_line()
    if 'env.' + models.SUBAGENT_VAR not in owned:
        return None
    return ("Claude Code's settings file isn't valid JSON." if 'is not valid JSON' in pic['settings_problem']
            else "Claude Code's settings file can't be read.")


def _models_turn(on):
    pic, now_on, decided = _models_state()
    if not on and not now_on:
        why = _models_left(pic)
        if why:                                 # never "off" while its key may still be there
            raise SwitchError(why)
    if on == now_on:
        return {'changed': False, 'note': None}
    try:
        if on:
            if decided:
                raise SwitchError('Already set in %s.' % decided['where'])
            pl = models.plan('by-the-job', lookups=False, **MODELS_KW)
            if pl['blocked']:
                raise SwitchError(_blocked_why(pl['blocked']))
            if not any(e['key'] == 'env.' + models.SUBAGENT_VAR for e in pl['edits']):
                raise SwitchError((pl['skipped'][0]['why'] if pl['skipped'] else 'Nothing to change.'))
            r = models.apply(pl, busy_ok=True)
        else:
            r = None
            if pic['preset'] == 'by-the-job':
                try:
                    r = models.undo(busy_ok=True, **dict((k, v) for k, v in MODELS_KW.items() if k == 'state_path'))
                    r = {'changed': r['restored']}
                except models.Refused:
                    r = None                    # the file changed since: take out only Switchboard's key
            if r is None:
                pl = models.plan('off', **MODELS_KW)
                if pl['blocked']:
                    raise SwitchError(_blocked_why(pl['blocked']))
                r = models.apply(pl, busy_ok=True)
    except models.Refused as e:
        raise SwitchError(_refusal(str(e)))
    changed = bool(r.get('changed'))
    open_chats = changed and models.busy(MODELS_KW.get('claude_dir')) > 0
    return {'changed': changed, 'note': ('next' if on else 'restart') if open_chats else None}


# ---------- the status line in Claude Code: one explicit change to its settings, undone exactly ----------

def statusline_command():
    """How Claude Code runs the line: this very Python (its real path, not Apple's /usr/bin/python3 stand-in,
    which looks the real one up on every run), importing statusline.py alone from its folder, so nothing
    else of Switchboard loads and its bytecode can be cached (statusline.precompile)."""
    return statusline.command(python=sys.executable)


def statusline_status():
    """{'on': Switchboard's line is in Claude Code's settings, 'theirs': another status line is there}, or
    {'on': None, 'theirs': None, 'unreadable': True} when the file can't be read or understood: unknown is
    never reported as off."""
    f = statusline._settings_file(statusline.settings_path())
    if f.get('error'):
        return {'on': None, 'theirs': None, 'unreadable': True}
    cur = f['obj'].get('statusLine')
    on = statusline.is_ours(cur)
    return {'on': on, 'theirs': 'statusLine' in f['obj'] and not on}


def statusline_plan(on, replace=True):
    """The exact change to Claude Code's settings file (statusline.install_plan / uninstall_plan). Changes nothing."""
    path = statusline.settings_path()
    if on:
        return statusline.install_plan(path, replace=replace, command_line=statusline_command())
    return statusline.uninstall_plan(path)


def statusline_every_minute():
    """A status line Switchboard put in before it ran every minute gets its "refreshInterval", while it is still
    exactly Switchboard's (statusline.every_minute): when the page starts (an update restarts it) and at the end of a
    first run in the terminal (`switchboard setup`). Nothing is said; a change is logged, and a failure too. Returns
    the paths changed."""
    try:
        done = statusline.every_minute()
    except Exception as e:  # noqa: BLE001 - only the line's minute waits for the next start
        log('the status line was not set to run every minute: %r' % e)
        return []
    for path in done:
        log('the status line in %s runs every minute now' % path)
    return done


def statusline_turn(on, replace=True, plan=None):
    """Turn Switchboard's status line on or off in Claude Code's settings file: one change, with a backup, that
    off undoes exactly (whatever status line was there before comes back byte for byte). The page's switch
    replaces another status line, because off puts it back."""
    p = plan or statusline_plan(on, replace)
    try:
        r = statusline.apply(p)
    except statusline.PlanError as e:
        raise SwitchError(str(e))
    if on:
        statusline.precompile()
        st = load()
        write_status_file(st, plan_view(st, plan_settings(engine.auto_settings()), time.time())['next'])
    return dict(r, kept_theirs=bool(p.get('kept_theirs')))


# ---------- what the page shows ----------

def segments(st, now, days=7):
    """Who Claude Code was on, as [email, from, until], for the last week."""
    start, tl, out = now - days * 86400, st['timeline'], []
    for i, (email, t) in enumerate(tl):
        end = tl[i + 1][1] if i + 1 < len(tl) else now
        if end > start:
            out.append([email, max(t, start), end])
    return out[-400:]


HISTORY_SHOWN = 12          # the newest lines of the log in every state; the page shows about ten
HISTORY_ALL = 200           # GET /api/history


def history_view():
    """GET /api/history: the newest lines of the log, newest first, and the work of the last 7 days (the usage
    cuts)."""
    if core.DEMO:
        from . import demo
        return demo.history()
    return dict(history.page(HISTORY_ALL), work=usage_view())


FIRST_NAME = {}             # {'at', 'name'}: this Mac's account, read at most once a minute
TITLES = frozenset(('dr', 'mr', 'mrs', 'ms', 'mx', 'miss', 'prof', 'sir', 'dame', 'rev', 'fr', 'lord', 'lady', 'hon'))
SUFFIXES = frozenset(('jr', 'sr', 'ii', 'iii', 'iv', 'phd', 'md', 'esq', 'dds', 'mba', 'cpa'))


def first_name(now=None):
    """The first name of this Mac's account, from the full name in the user database, for the page's "Welcome, Maya".
    It is only ever in this Mac's own page. None when the account has no full name."""
    now = time.time() if now is None else now
    if FIRST_NAME and 0 <= now - FIRST_NAME['at'] < 60:
        return FIRST_NAME['name']
    try:
        full = getpwuid(os.getuid()).pw_gecos or ''
    except (KeyError, OSError):
        full = ''
    name = name_first(full)
    FIRST_NAME.update(at=now, name=name)
    return name


def name_first(full):
    """The first name in a full name, 40 characters at most: 'Maya Chen', 'Chen, Maya' (last name first) and 'Dr. Maya
    Chen' (a title first) all give 'Maya'. Control, format and other invisible characters (U+200B, U+202E, ...) are
    taken out first. After a name, a comma starts what is not part of it ('Maya Reyes, PhD'; 'Maya Reyes,Room 4')."""
    text = ''.join(c for c in full if not unicodedata.category(c).startswith('C'))
    parts = [p.split() for p in text.split(',')]
    words = parts[0]
    if len(words) == 1 and len(parts) > 1 and parts[1] and parts[1][0][:1].isalpha() \
            and _bare(parts[1][0]) not in SUFFIXES:
        words = parts[1]                                            # 'Chen, Maya'
    while words and _bare(words[0]) in TITLES:
        words = words[1:]
    return words[0][:40] if words else None


def _bare(word):
    """'Dr.' -> 'dr': a word as TITLES and SUFFIXES list it."""
    return word.rstrip('.').lower()


def artifacts_view():
    """GET /api/artifacts: the accounts as folders, each with the artifacts it published (artifacts.folders)."""
    if core.DEMO:
        from . import demo
        return demo.files()
    return artifacts.folders(load(), browser.profiles())


GUARD = {'at': -1e9, 'label': None}       # another program that switches Claude Code's login, looked for each minute
GUARD_EVERY = 60.0


def guard_label(now=None):
    """The login item of another program that switches Claude Code's login too (engine.foreign_switcher), or None.
    Looked for at most once a minute."""
    now = time.time() if now is None else now
    if not 0 <= now - GUARD['at'] < GUARD_EVERY:
        try:
            GUARD.update(at=now, label=engine.foreign_switcher())
        except Exception as e:  # noqa: BLE001 - the page shows without it
            log('looking for another switcher: %r' % e)
            GUARD.update(at=now, label=None)
    return GUARD['label']


def guard_line():
    label = guard_label()
    return engine.GUARD % label if label else None


def facts():
    """Everything about this Mac the page needs that isn't in the saved state. It starts no program: the link app's
    status is the copy the background loop keeps (link_status)."""
    present = browser.present()
    return {'profiles': browser.profiles(),
            'browser': {'name': browser.app(), 'present': present,
                        'tabs_blocked': time.time() < browser.BLIND['until'], 'tabs': tab_view()},
            'engine': {'installed': True, 'claude': bool(connect.claude_bin())},
            'guard': guard_line(),
            'links': dict(link_status(), home=linkapp.home_profile(), key=core.key(), **link_extras()),
            'auto': auto_view(), 'setup_hidden': bool(settings().get('setup_hidden')),
            'mac': mac_facts(), 'keys': lanes.brief(), 'history': history.page(HISTORY_SHOWN)['entries'],
            'extensions': browser.extensions() if present else {}, 'host': host_ok() if present else None,
            'first_name': first_name(), 'check': check_view(), 'pictures': previews.known(),
            'consent': consent_view(), 'disk': disk_view(), 'notify_check': notify_check(), 'load': load_view(),
            'providers': providers_view(),
            'chats': chats_counts(), 'now': time.time(), 'demo': False}


def tab_view():
    """{'state', 'text'}: may Switchboard read the browser's tabs (browser.tab_access), from macOS's answer as the
    background look (or the button) last had it: building the page's state never asks macOS itself."""
    with CONSENT_LOCK:
        said = CONSENT['state']
    try:
        return browser.tab_access(said=said)
    except Exception as e:  # noqa: BLE001 - the page shows without it
        log('the tab access line: %r' % e)
        return None


def host_ok():
    """Claude Code's way into the browser is there and its program can run (browser.native_host), or None when that
    can't be read. A small file read; nothing runs."""
    try:
        return bool(browser.native_host().get('ok'))
    except Exception as e:  # noqa: BLE001
        log('the browser extension host: %r' % e)
        return None


def notify_check():
    """{'seen': the person's answer to "did a notification appear?" after the last test (True, False), or None.}"""
    seen = settings().get('notify_seen')
    return {'seen': seen if isinstance(seen, bool) else None}


# ---------- automatic switching in one plain line: what it waits for (the page and the menu bar) ----------
#
# The loop's heartbeat (api.auto_status, through engine.auto_settings) says when it found no network, when the Mac just
# woke, and what a late stage waits for; the policy (api.decide) says why it stays: an optional switch held ("Waiting:
# Claude Code is working.") or every account out. api.decide() runs in the page's background loop (decide_tick),
# never for a request: outside the auto loop it may read Claude Code's login once.

DECIDE = {'at': None, 'decision': None}
DECIDE_FRESH = 180.0        # seconds (monotonic): an older answer is not shown
LATE_STAGE = 60.0           # seconds a heartbeat is overdue before the line says what the loop waits for


def decide_tick(enabled=None, now=None):
    """The policy's answer while automatic switching is on (for auto_line). Returns it, or None."""
    now = monotonic() if now is None else now
    if enabled is None:
        try:
            enabled = bool((api.auto_settings() or {}).get('enabled'))
        except (api.AccountsError, NotImplementedError):
            enabled = False
    if not enabled:
        DECIDE.update(at=None, decision=None)
        return None
    try:
        d = api.decide()
    except NotImplementedError:
        d = None
    except Exception as e:  # noqa: BLE001 - the line waits for the next tick
        log('asking automatic switching what it waits for: %r' % e)
        d = None
    DECIDE.update(at=now if d is not None else None, decision=d)
    return d


def _clock_words(t):
    """'5:19 PM' (this Mac's time zone), as the page says a time."""
    try:
        return time.strftime('%-I:%M %p', time.localtime(t))
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def _sentence(text):
    text = ' '.join(str(text).split())
    return text if text.endswith(('.', '!', '?')) else text + '.'


def auto_line(a, now=None):
    """One plain line about what automatic switching is waiting for, or None (off, nothing to say, or a problem the
    page says on its own line: stuck, stopped, restarted)."""
    if not a.get('enabled') or not a.get('on') or a.get('problem') or a.get('restarted'):
        return None
    if _finite(a.get('offline_since')) is not None:
        since = _clock_words(a['offline_since'])
        return 'No internet since %s. Automatic switching waits for it.' % since if since else \
            'No internet. Automatic switching waits for it.'
    if a.get('settling'):
        return 'The Mac just woke. Automatic switching waits for the network.'
    late = _finite(a.get('stuck_for'))
    if late is not None and late >= LATE_STAGE and a.get('waiting_for'):
        return 'Automatic switching is waiting for %s (%d s so far).' % (a['waiting_for'], late)
    now = monotonic() if now is None else now
    d, at = DECIDE['decision'], DECIDE['at']
    if d is None or at is None or not 0 <= now - at < DECIDE_FRESH or getattr(d, 'action', None) != 'stay':
        return None
    if getattr(d, 'kind', None) == 'forced' and getattr(d, 'reason', None):
        return _sentence(d.reason)                  # "Every account is out. Back at 4:10 PM (...)."
    if getattr(d, 'hold', None):
        hold = ' '.join(str(d.hold).split())
        return 'Waiting: %s' % _sentence(hold[:1].upper() + hold[1:])
    return None


def auto_view():
    """engine.auto_settings() and its one line (auto_line)."""
    a = engine.auto_settings()
    stage = engine.stage_words(a.get('stage')) if a.get('enabled') and a.get('running') else ''
    return dict(a, line=auto_line(a), stage_words=stage or None, **watch_words(a))


# ---------- the link app's status, kept by the background loop (no program starts on the request path) ----------

LINKS = {'value': None, 'at': None, 'running': False, 'loop': False, 'fast_until': 0.0, 'banner': None,
         'handlers': None, 'holder': None}
LINKS_LOCK = threading.Lock()
LINK_EVERY = 20.0           # seconds (monotonic) between two looks
LINK_EVERY_FAST = 3.0       # for a while after the page turned it on or off (macOS asks; the answer comes later)
LINKS_WANTED = 'links_wanted'   # settings: True once links opened as their owner (turned on, or seen on); False when
#                                 the person turned them off on purpose (`switchboard links off`); unset: never set up


def link_extras():
    """{'banner': the line and its one button while links don't open as their owner (the link app is built and not
    the default browser, and was not turned off on purpose: _links_banner), 'handlers': the line about other apps
    that take clicked links}, as the background look last made them (both read files only, and never on the request
    path)."""
    with LINKS_LOCK:
        banner, handlers, holder = LINKS['banner'], LINKS['handlers'], LINKS['holder']
    return {'banner': dict(banner) if banner else None, 'handlers': handlers,
            'holder': {'name': holder.get('name'), 'kind': holder.get('kind')} if holder else None,
            'forwards': passes_links(holder)}


# Who holds the default-browser seat decides what the page's links line says (an old router in the seat can pass
# clicked links to the page, and then they do open as their owner while it runs). A link it passes on arrives at
# POST /api/go with its own header (Handler._old_router); the last one is noted here, and in settings.json at most once
# an hour, so a page started again still knows.
FORWARDED = {'at': None, 'by': None, 'read': False, 'saved': None, 'saved_by': None}
FORWARDED_KEY = 'links_forwarded'
FORWARDED_EVERY = 3600


def forwarded():
    """{'at', 'by' (the bundle identifier of the app in the seat then)} for the last link another app passed to this
    page, or None. A note from an earlier build may have 'by' None: no app is known for it, so it counts for none."""
    if not FORWARDED['read']:
        v = settings().get(FORWARDED_KEY)
        if isinstance(v, dict) and _finite(v.get('at')) is not None:
            by = v.get('by') if isinstance(v.get('by'), str) else None
            FORWARDED.update(at=_finite(v['at']), by=by, saved=_finite(v['at']), saved_by=by)
        FORWARDED['read'] = True
    return {'at': FORWARDED['at'], 'by': FORWARDED['by']} if FORWARDED['at'] is not None else None


def note_forwarded(now=None):
    """Another app passed a clicked link here to open as its owner: noted for the app in the default-browser seat. When
    that app is not known (the page's first look at the seat is under way, a look failed) or is not a link handler
    (Switchboard Links, a browser), nothing is noted: a link noted for no app would count for any app that takes the
    seat later. The next link it passes on is noted."""
    now = time.time() if now is None else now
    with LINKS_LOCK:
        holder = LINKS['holder'] or {}
    by = holder.get('id') if holder.get('kind') == 'handler' else None
    if not isinstance(by, str) or not by:
        return
    forwarded()                                 # what settings.json holds, read once
    FORWARDED.update(at=now, by=by)
    saved = FORWARDED['saved']
    if saved is None or FORWARDED['saved_by'] != by or not 0 <= now - saved < FORWARDED_EVERY:
        try:
            set_setting(FORWARDED_KEY, {'at': round(now), 'by': by})
            FORWARDED.update(saved=now, saved_by=by)
        except Exception as e:  # noqa: BLE001 - the page still knows until it restarts
            log('noting a link passed on by another app: %r' % e)


def passes_links(holder):
    """True when the app in the default-browser seat is not a browser and has passed clicked links to this page: links
    open as their owner while the page runs (the page's links line)."""
    if not holder or holder.get('kind') != 'handler':
        return False
    f = forwarded()
    return bool(f) and f['by'] is not None and f['by'] == holder.get('id')


def _links_banner(value):
    """The links banner for the status just read, or None. Opening links as their owner is always on (it is what
    Switchboard is for), so the banner and its one button show whenever the link app is built and is not the
    default browser: never set up, or the seat went to another app since (macOS reset it, a browser took it). Its
    words are the page's links line for whoever holds the seat, so an app that passes clicked links to this page is
    never said to lose them. Only a person who turned links off on purpose (LINKS_WANTED False, `switchboard links
    off`) is never asked again."""
    if not isinstance(value, dict) or not value.get('installed') or value.get('on'):
        return None
    if settings().get(LINKS_WANTED) is False:
        return None
    where = linkapp.seat(look=False)                    # the status just read: nothing run
    return linkapp.banner(value, where, forwards=passes_links(where.get('holder')))


def links_lost_note(banner, run=None, now=None):
    """The one notification when the seat leaves Switchboard Links, in the banner's words, once per time it
    happens."""
    if run is None or not banner or core.DEMO or not helper_settings()['notify']:
        return False
    try:
        notify.send(notify.Note('links-lost', 'Switchboard', banner['text'], round(time.time() if now is None
                                                                                       else now), None), run=run)
        return True
    except Exception as e:  # noqa: BLE001
        log('notification failed: %r' % e)
        return False


def link_status():
    """The link app's status as the background loop last saw it. Before its first look: installed or not (read from
    the app's Info.plist), not yet known as the default browser. A process with no loop (a command in a terminal)
    asks the link app itself."""
    with LINKS_LOCK:
        background, value = LINKS['loop'], LINKS['value']
    if not background:
        return linkapp.status()
    if value is not None:
        return dict(value)
    return {'installed': linkapp.installed(), 'on': False, 'browser': None}


def refresh_link_status(now=None, force=False):
    """Look at the link app's status on a thread of its own (it starts the link app with --status, up to 10 s), at
    most every LINK_EVERY seconds (LINK_EVERY_FAST after a change), one look at a time."""
    now = monotonic() if now is None else now
    with LINKS_LOCK:
        every = LINK_EVERY_FAST if now < LINKS['fast_until'] else LINK_EVERY
        if not LINKS['loop'] or LINKS['running'] or \
                (not force and LINKS['at'] is not None and 0 <= now - LINKS['at'] < every):
            return False
        LINKS['running'] = True

    def look():
        value, banner, handlers, lost, holder = None, None, None, False, None
        try:
            value = linkapp.status(fresh=True)
            # On now, and never set: the person chose it (macOS asked them; an install from before this). Never over
            # an off said on purpose: after `switchboard links off` the seat is still held while macOS asks.
            if isinstance(value, dict) and value.get('on') and settings().get(LINKS_WANTED) is None:
                set_setting(LINKS_WANTED, True)
            banner = _links_banner(value)
            holder = linkapp.seat(look=False).get('holder')     # who has the seat, from the status just read
        except Exception as e:  # noqa: BLE001 - the last value stays
            log('the link app status could not be read: %r' % e)
        try:                                            # Info.plists only, at most every 10 minutes
            seen = forwarded()                          # never the seat's app, nor one that sends links here
            skip = set(x for x in ((holder or {}).get('id'), (seen or {}).get('by')) if isinstance(x, str))
            handlers = linkapp.handlers_note(on=isinstance(value, dict) and bool(value.get('on')), skip=skip)
        except Exception as e:  # noqa: BLE001
            log('looking for other link handlers: %r' % e)
        with LINKS_LOCK:
            if isinstance(value, dict):
                lost = bool(banner) and bool((LINKS['value'] or {}).get('on'))
                LINKS.update(value=dict(value), banner=banner,
                             holder=dict(holder) if isinstance(holder, dict) and holder.get('kind') else None)
            LINKS['handlers'] = handlers
        if lost:
            links_lost_note(banner, NOTIFY_RUN)
        with LINKS_LOCK:                        # the look is over only once all of it is done
            LINKS.update(at=monotonic(), running=False)
        SNAPSHOT.invalidate()
    try:
        threading.Thread(target=look, name='switchboard-links', daemon=True).start()
    except RuntimeError:
        with LINKS_LOCK:
            LINKS['running'] = False
        return False
    return True


# ---------- may Switchboard read Chrome's tabs? macOS's Automation consent check ----------
#
# Asked without a prompt in the background, and with macOS's own question only from the button the person presses
# ("Let Switchboard check links"): never guessed from a timeout. The check itself is browser.py's: browser.consent()
# and browser.ask_consent() -> 'allowed', 'denied', 'would_ask' (macOS has not asked yet), 'not_running' (Chrome is
# closed) or 'unknown'. Without them, consent_check() is None and the page says it can't tell.

CONSENT = {'state': None, 'at': None, 'asking': False, 'running': False, 'wall': None}
CONSENT_LOCK = threading.Lock()
CONSENT_EVERY = 300.0       # seconds (monotonic) between two quiet looks
CONSENT_WAIT = 1.5          # a press waits this long for macOS's answer before it says "macOS is asking"
CONSENT_QUIET_WAIT = 6.0    # ... and this long for a quiet look under way to end (the consent check's own limit: 5 s)
CONSENT_STATES = ('allowed', 'denied', 'would_ask', 'not_running', 'unknown')
CONSENT_LINES = {
    'allowed': 'Switchboard may check which profile opened a link.',
    'denied': 'macOS said no. Allow it in System Settings › Privacy & Security › Automation › %(app)s.',
    'would_ask': 'macOS has not asked yet. Press Let Switchboard check links, then choose OK.',
    'not_running': 'Open %(app)s, then press Let Switchboard check links.',
    'unknown': "Switchboard can't tell yet whether macOS lets it check links.",
    'asking': 'macOS is asking. Choose OK in its window.',
}


CONSENT_ASKING_FOR = 125.0  # seconds a press waits out another question macOS is already showing (ask_consent: 120)


def consent_check():
    """macOS's answer, through what browser.py provides: check(ask) -> 'allowed', 'denied', 'would_ask', 'not_running',
    'unknown', or 'asking' (macOS's question is already up for another press). browser.consent() looks without a
    question; browser.ask_consent() shows macOS's question, only when it has not asked yet, and only for a press (ask).
    None when browser.py has no consent check."""
    if not callable(getattr(browser, 'consent', None)):
        return None

    def check(ask=False):
        state = browser.consent(fresh=True)
        if ask and state == browser.WOULD_ASK and callable(getattr(browser, 'ask_consent', None)):
            state = browser.ask_consent()
        return state
    return check


def _asked_elsewhere():
    """Wait for the question another press put up (browser.ask_consent answers 'asking' meanwhile), then its answer."""
    until, pause = monotonic() + CONSENT_ASKING_FOR, threading.Event()
    while monotonic() < until:
        with browser._CONSENT_LOCK:
            asking, state = browser.CONSENT.get('asking'), browser.CONSENT.get('state')
        if not asking:
            return state or 'unknown'
        pause.wait(0.5)
    return 'unknown'


def _consent_run(ask):
    check = consent_check()
    state = 'unknown'
    if check is not None:
        try:
            got = check(ask=ask)
            if got == 'asking':
                got = _asked_elsewhere()
            state = got if got in CONSENT_STATES else 'unknown'
        except Exception as e:  # noqa: BLE001 - not known is said as such
            log('the Automation check failed: %r' % e)
    with CONSENT_LOCK:
        CONSENT.update(state=state, at=monotonic(), wall=time.time(), running=False, asking=False)
    SNAPSHOT.invalidate()
    return state


def _consent_from_browser():
    """After a check that may have asked macOS (the Check button's): the page's line follows browser.py's answer."""
    with browser._CONSENT_LOCK:
        state = browser.CONSENT.get('state')
    if state in CONSENT_STATES:
        with CONSENT_LOCK:
            if not CONSENT['running']:
                CONSENT.update(state=state, at=monotonic(), wall=time.time())


# ---------- "Check": which profile is signed in as which account, on its own thread ----------
#
# It opens one page of each account's in the background and reads the tab (artifacts.check_profiles): seconds to
# minutes. The press answers at once and the page reads the result from the state (check_view). macOS may ask for the
# Automation permission here, only because the person pressed the button (browser.can_see_tabs(ask=True)).

CHECK = {'running': False, 'started': None, 'at': None, 'tabs': None, 'results': None, 'error': None}
CHECK_LOCK = threading.Lock()
CHECK_WORDS = {'ok': 'signed in, checked', 'signin': 'signed out of claude.ai: sign in once in that profile',
               'other': 'that profile is signed in as a different account', 'none': 'no profile could open its pages',
               'no_page': 'no page of its own is known yet, so it is checked the first time you open one',
               'no_profile': 'no %(app)s profile is signed in as it: press Set up for it on the page',
               'trouble': 'claude.ai is having trouble right now (it showed its own error page): check again later',
               'unchecked': 'the browser was too slow to answer'}


def check_start(ask=True):
    """POST /api/check: start the check on its own thread (one at a time) and answer at once."""
    with CHECK_LOCK:
        if CHECK['running']:
            return {'running': True}
        CHECK.update(running=True, started=time.time(), error=None)
    try:
        threading.Thread(target=_check_run, args=(bool(ask),), name='switchboard-check', daemon=True).start()
    except RuntimeError:
        with CHECK_LOCK:
            CHECK['running'] = False
        raise SwitchError('Switchboard is busy. Try again in a moment.')
    SNAPSHOT.invalidate()
    return {'started': True}


def _check_run(ask):
    tabs, results, error = None, [], None
    try:
        tabs = browser.can_see_tabs(ask=ask)
        if tabs is not False:
            results = artifacts.check_profiles()
    except Exception as e:  # noqa: BLE001 - said in one line, the details in the log
        log('the profile check failed: %r' % e)
        error = "Switchboard couldn't reach %s just now. Try again in a moment." % browser.app()
    finally:
        with CHECK_LOCK:
            CHECK.update(running=False, at=time.time(), tabs=tabs, results=results, error=error)
        _consent_from_browser()
        SNAPSHOT.invalidate()


def check_line(c):
    """The check's result in one line: '2 checked · 1 signed out', or why it could not look."""
    if c.get('error'):
        return c['error']
    if c.get('tabs') is False:
        return 'macOS has not allowed Switchboard to read %s tabs. Press Let Switchboard check links.' % browser.app()
    n = collections.Counter(r.get('result') for r in c.get('results') or [])
    bits = [(n['ok'], 'checked'), (n['signin'], 'signed out'), (n['other'] + n['none'], 'need another profile'),
            (n['no_profile'], 'no profile yet'), (n['trouble'], 'claude.ai had trouble'),
            (n['unchecked'], 'not checked (the browser was slow)'), (n['no_page'], 'checked on first use')]
    return ' · '.join('%d %s' % (k, w) for k, w in bits if k) or 'Nothing to check yet.'


def check_view():
    """{'running', 'started', 'at', 'tabs', 'results': [{email, result, profile, words}], 'line'} for the page."""
    with CHECK_LOCK:
        c = dict(CHECK)
    if c['at'] is None and not c['running']:
        return None
    rows = [dict(r, words=CHECK_WORDS.get(r.get('result'), r.get('result') or '') % {'app': browser.app()})
            for r in c['results'] or [] if isinstance(r, dict)]
    return {'running': c['running'], 'started': c['started'], 'at': c['at'], 'tabs': c['tabs'], 'results': rows,
            'line': None if c['running'] else check_line(c)}


def consent_refresh(ask=False, now=None):
    """Start one look on its own thread (never two at once). Quiet looks at most every CONSENT_EVERY seconds; a press
    (ask=True) always looks. Returns the thread, or None when none started."""
    now = monotonic() if now is None else now
    with CONSENT_LOCK:
        if CONSENT['running'] or (not ask and CONSENT['at'] is not None and 0 <= now - CONSENT['at'] < CONSENT_EVERY):
            return None
        CONSENT.update(running=True, asking=bool(ask))
    t = threading.Thread(target=_consent_run, args=(bool(ask),), name='switchboard-consent', daemon=True)
    try:
        t.start()
    except RuntimeError:
        with CONSENT_LOCK:
            CONSENT.update(running=False, asking=False)
        return None
    return t


def consent_view():
    """{'state', 'line', 'checked' (wall time or None), 'can_ask'} for the page."""
    with CONSENT_LOCK:
        state = 'asking' if CONSENT['asking'] else (CONSENT['state'] or 'unknown')
        wall = CONSENT['wall']
    return {'state': state, 'line': CONSENT_LINES[state] % {'app': browser.app()}, 'checked': wall,
            'can_ask': consent_check() is not None}


def consent_ask():
    """POST /api/consent, the button: macOS's own question when it has not asked yet. Waits up to CONSENT_WAIT for its
    answer; past that, "macOS is asking", and the page reads the answer from the state when it comes."""
    if consent_check() is None:
        return {'state': 'unknown', 'line': "Switchboard can't ask macOS about this in this copy yet."}
    t = consent_refresh(ask=True)
    end = monotonic() + CONSENT_QUIET_WAIT
    while t is None and monotonic() < end:  # a quiet look is under way (seconds): the press asks once it has ended
        with CONSENT_LOCK:
            quiet = CONSENT['running'] and not CONSENT['asking']
        if not quiet:
            break                           # another press is asking already: its answer comes in the state
        time.sleep(0.05)
        t = consent_refresh(ask=True)
    if t is not None:
        t.join(CONSENT_WAIT)
    return {k: v for k, v in consent_view().items() if k in ('state', 'line')}


# ---------- a Mac under load: the page's own loop does less, and says so ----------
#
# Only in the page's own process (serve() sets 'aware'): from a load of 1.5 per core the page reads the accounts every
# 2 minutes instead of every minute, from 3 every 4 minutes, and the extras (the artifact scan, History's work) wait.
# The page says so in one line. Commands in a terminal and tests never ease.

EASE = {'aware': False, 'on': False, 'load': 0.0}
EASE_SLOWER, EASE_PAUSE = 1.5, 3.0      # load per core


def load_per_core():
    try:
        return os.getloadavg()[0] / (os.cpu_count() or 1)
    except (OSError, AttributeError):
        return 0.0


def easing():
    """True while the page's own loop is doing less because the Mac is very busy."""
    return EASE['aware'] and EASE['on']


def ease_tick(load=None):
    """Each pass of the loop: the load now, and how long until the next reading of the accounts."""
    if not EASE['aware']:
        return TICK
    EASE['load'] = load_per_core() if load is None else load
    EASE['on'] = EASE['load'] >= EASE_PAUSE
    return TICK * (4 if EASE['load'] >= EASE_PAUSE else 2 if EASE['load'] >= EASE_SLOWER else 1)


def load_view():
    """{'per_core', 'easing'} for the page, or None outside the page's own process."""
    return {'per_core': round(EASE['load'], 1), 'easing': easing()} if EASE['aware'] else None


# ---------- the disk ----------

DISK = {'low': False, 'said': False}


def disk_view():
    """{'low': True when the disk is almost full, 'free': bytes or None, 'line': the page's sentence when it is too
    full for a switch to begin (core.DISK_FLOOR), else None} (the free space is looked at every 30 s)."""
    free = core.disk_free()
    return {'low': free is not None and free < core.DISK_LOW, 'free': free,
            'line': autoloop_words('DISK_FULL_WORDS') if free is not None and free < core.DISK_FLOOR else None}


def autoloop_words(name):
    """One of the automatic loop's own sentences, so the page says what History says."""
    from .accounts import autoloop
    return getattr(autoloop, name)


def disk_tick(run=None):
    """Each pass of the loop: when the disk falls under DISK_LOW, one notification and one line in the history; when it
    has room again, what was paused (notify.DISK_PAUSED) starts again by itself."""
    low = core.disk_low()
    if low and not DISK['said']:
        DISK['said'] = True
        try:
            with locked() as st:
                event(st, 'Your disk is almost full, so %s. Free some disk space.' % notify.DISK_PAUSED, 'warn')
        except OSError as e:                # a disk too full for even that line: the notification still goes
            log('the disk line was not saved: %r' % e)
        if run is not None:
            try:
                notify.send(notify.disk_full(), run=run)
            except Exception as e:  # noqa: BLE001
                log('notification failed: %r' % e)
    elif not low and DISK['said']:
        DISK['said'] = False
    DISK['low'] = low
    return low


def chrome_setup(a, extensions):
    """An account's Chrome setup, done once: {profile, signed_in, extension (the Claude extension is in that profile;
    None without a profile, or without a browser to look in)}. signed_in is True only when it was shown: a sign-in
    there linked the profile (profile_src checked), or a page of the account's loaded there (signed_in_profile names
    it, whoever picked the profile: the person, a "New profile", a guess); False when it was seen signed out; None
    while not known. A profile matched from the claude.ai data it keeps (profile_src hint) is only a guess until then."""
    p = a.get('profile') or None
    signed = None
    if p and a.get('signed_out'):
        signed = False
    elif p and (a.get('profile_src') == 'checked' or a.get('signed_in_profile') == p):
        signed = True
    return {'profile': p, 'signed_in': signed, 'extension': extensions.get(p) if p and p in extensions else None}


def render(st, f):
    """The state plus the facts, in the shape the page draws."""
    names, now = f['profiles'], f['now']
    pictures = f.get('pictures') or ()      # an artifact's picture comes seconds after it: the page then reads again
    owned = {}
    for x in st['artifacts']:
        owned[x.get('owner')] = owned.get(x.get('owner'), 0) + 1
    exts = f.get('extensions') or {}
    # Claude in Chrome is in use once its extension is in some profile: only then does each account get its line
    # (and the native host's state, which is the same for every account).
    in_chrome = any(v is True for v in exts.values())
    links = link_rows(st, names, now, exts, f.get('host') if in_chrome else None)
    accounts = []
    for email in sorted(st['order'], key=lambda e: st['accounts'][e].get('slot') or 99):
        a = st['accounts'][email]
        if not a.get('connected'):
            continue
        p = a.get('profile')
        accounts.append({
            'email': email, 'name': name_of(st, email), 'alias': a.get('alias') or '', 'active': email == st['active'],
            'slot': a.get('slot'), 'plan': a.get('plan') or '', 'org': a.get('org') or '',
            'tier': a.get('tier') or '', 'org_type': a.get('org_type') or '', 'plan_line': plan_line(a),
            'needs_login': bool(a.get('needs_login')), 'disabled': False, 'id': a.get('id'),
            'status': a.get('status') or 'ok', 'frozen': bool(a.get('frozen')),
            'unmanaged': bool(a.get('unmanaged')), 'usage': a.get('usage'), 'usage_at': a.get('usage_at'),
            'checked_at': a.get('checked_at'), 'signin_ends_at': a.get('signin_ends_at'), 'note': a.get('note'),
            'elsewhere': bool(a.get('elsewhere')) and not a.get('live'),
            'profile': p,
            'profile_name': names.get(p, p) if p else '', 'profile_src': a.get('profile_src'),
            'profile_missing': bool(p) and bool(names) and p not in names,
            'signed_out': bool(a.get('signed_out')), 'pages': owned.get(email, 0),
            'trusted': reading_trusted(a, now, f['auto'].get('models')), 'live': live_now(st, email, now),
            'chrome': chrome_setup(a, exts), 'link': links.get(email), 'chats': (f.get('chats') or {}).get(email)})
        accounts[-1]['pace'] = pace.week(accounts[-1], now)    # its week at this pace (pace.py): the page's words
    lines = plan_settings(f['auto'])
    planned = plan_view(st, lines, now)
    # A browser profile per account matters only to links, so it is a step only while links are in use (the link app
    # installed and on); the account swap needs none, and an account kept with no profile finishes the first run.
    links_in_use = bool(f['links'].get('installed')) and bool(f['links'].get('on'))
    steps = {'engine': f['engine']['installed'],
             'accounts': len([a for a in accounts if not a['unmanaged']]) >= 1,
             'profiles': not links_in_use or (bool(accounts) and all(a['profile'] and not a['signed_out']
                                                                      for a in accounts)),
             'links': bool(f['links'].get('on')) or not f['links'].get('installed')}   # no link app: nothing to wait for
    return {'version': VERSION, 'page': page_id(), 'demo': f['demo'], 'accounts': accounts, 'active': st['active'],
            'newer': newer.line(),          # one quiet line when a newer version is out (only while its look is on)
            'now': now, 'tick_at': st['tick_at'], 'error': st.get('error'), 'events': st['events'][-30:][::-1],
            'artifacts': [{'id': x.get('id'), 'url': x['url'], 'title': x.get('title') or x.get('name') or '',
                           'owner': x.get('owner'), 'src': x.get('src'), 't': x.get('t'),
                           'profile_name': names.get(x.get('profile'), '') if x.get('profile') else '',
                           'preview': x['id'] in pictures}
                          for x in st['artifacts'] if x.get('id')],
            'profiles': [{'dir': d, 'name': n} for d, n in sorted(names.items(), key=lambda x: x[1].lower())],
            'browser': f['browser'], 'engine': f['engine'], 'links': f['links'],
            'connecting': f.get('connecting', []), 'timeline': segments(st, now),
            'signins': f.get('signins') or {'state': 'idle', 'due': []},
            'tokens': f['tokens'] if 'tokens' in f else bearers.rows(),     # one-year tokens and API keys kept
            'setup': dict(steps, done=all(steps.values()), hidden=f['setup_hidden']),
            'guard': f.get('guard'), 'disagree': bool(st.get('disagree')),
            'auto': dict(f['auto'], models_seen=models_seen(st, f['auto']),       # rules: each account's, by its id
                         rules=dict((a['id'], lines.get('rules', {}).get(a['id'], 'in')) for a in accounts if a['id']),
                         own_lines=dict((a['id'], lines['own_lines'][a['id']]) for a in accounts
                                        if a['id'] in lines.get('own_lines', {})),
                         ranks=dict((a['id'], lines['ranks'][a['id']]) for a in accounts
                                    if a['id'] in lines.get('ranks', {}))),
            'plan': planned, 'next': marked_next(accounts, planned, f['auto']),
            'forecast': forecast_view(st, lines, now),
            'mac': dict(f.get('mac') or {'awake': True, 'notify': True, 'statusline': False}),
            'keys': dict(f.get('keys') or {'count': 0, 'on': False, 'command': lanes.COMMAND}),
            'history': list(f.get('history') or []), 'first_name': f.get('first_name') or None,
            'consent': f.get('consent'), 'disk': f.get('disk'), 'notify_check': f.get('notify_check'),
            'providers': f.get('providers'),
            'load': f.get('load'), 'check': f.get('check'), 'in_chrome': in_chrome}


def link_rows(st, names, now, extensions, host):
    """artifacts.link_rows for the page: each account's link to its profile (proved and when, Set up, Sign in there)
    and its Claude in Chrome line, or {} when it can't be worked out (the page shows without it)."""
    if not names:                           # no browser profiles known: nothing to say per account
        return {}
    try:
        rows = artifacts.link_rows(st, names, now, extensions=extensions, host=host)
    except Exception as e:  # noqa: BLE001
        log('the link rows: %r' % e)
        return {}
    if not any(v is True for v in (extensions or {}).values()):
        for r in rows.values():
            r['chrome'] = None              # Claude in Chrome is not in use here: no line about it
    return rows


KEPT_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$')     # never an option: no leading "-"

# What the page has been told before a destructive yes: (what, which) -> when. A yes is taken only after its own
# question was answered by the server within ASKED_FRESH seconds, whatever a request says.
ASKED = {}
ASKED_LOCK = threading.Lock()
ASKED_FRESH = 600


def asked(what, which):
    with ASKED_LOCK:
        ASKED[(what, which)] = time.time()


def answered(what, which):
    """True once, for a question the server asked lately; then it is spent."""
    with ASKED_LOCK:
        t = ASKED.pop((what, which), None)
    return t is not None and 0 <= time.time() - t < ASKED_FRESH     # a question dated ahead is no question


def more_view():
    """GET /api/more: the More area's lists and settings, read when it is opened."""
    if core.DEMO:
        from . import demo
        return demo.more()
    return more.view()


def claudecode_view():
    """GET /api/claudecode: the status line and the three switches beside it."""
    if core.DEMO:
        from . import demo
        return demo.claudecode()
    s = statusline_status()
    return claudecode.view({'on': s['on'], 'theirs': s['theirs']})


def keys_view():
    """GET /api/keys: the lanes (name and last four only) and the gateway."""
    if core.DEMO:
        from . import demo
        return demo.keys()
    return lanes.view()


PAGE = {'key': None, 'id': None}
PAGE_MARK = b'<meta charset="utf-8">'     # the page's own name goes in right after this, as it is sent


def _page_name(data):
    return hashlib.sha256(data).hexdigest()[:12]


def page_id():
    """A short name for the page's file as it is on disk now (hashed once per change of its size or time), or None.
    An update replaces that file while a tab may be open on the old page: the tab compares this with the name it was
    sent with (page_bytes) and loads itself again, once."""
    path = os.path.join(WEB, 'index.html')
    try:
        info = os.stat(path)
        key = (info.st_mtime_ns, info.st_size)
        if PAGE['key'] != key:
            with open(path, 'rb') as f:
                PAGE.update(key=key, id=_page_name(f.read()))
    except OSError:
        return None
    return PAGE['id']


def page_bytes(name):
    """A page's file as it is sent. The main page carries the name of the file it was made from (what page_id says
    while that file is the one on disk), so a tab knows which page it is from its first moment, whatever the server
    answers next."""
    with open(os.path.join(WEB, name), 'rb') as f:
        data = f.read()
    if name == 'index.html' and PAGE_MARK in data:
        mark = b'\n<meta name="switchboard-page" content="' + _page_name(data).encode('ascii') + b'">'
        data = data.replace(PAGE_MARK, PAGE_MARK + mark, 1)
    return data


def view():
    if core.DEMO:
        from . import demo
        return demo.view()
    st = load()
    f = dict(facts(), connecting=connect.view(st), signins=signins_view(st, time.time()))
    # The store's newest readings, not only the tick's; read after the facts, so a read the loop's heartbeat says it
    # answered (auto's 'asked') is in them too. Then Claude Code's newest numbers for the account in use.
    apply_readings(st, fresh_readings()[1])
    apply_live_numbers(st, f['now'])
    return dict(render(st, f), **heal_view())


def selfcheck_tick():
    """From the page's loop: the self-check (selfcheck.tick), which starts a run on a thread of its own every ten
    minutes and returns at once, so a slow check never holds the loop up. Never in the demo."""
    if core.DEMO:
        return None
    from . import selfcheck
    return selfcheck.tick()


def heal_view():
    """Two keys of the state answer: 'folder', the mark of the data folder this page serves (core.folder_mark: a
    command changes nothing through a page that serves another), and 'repairs', what Switchboard repaired by itself
    lately or left out on purpose, one line each (selfcheck.notices)."""
    out = {'folder': core.folder_mark(), 'repairs': []}
    try:
        from . import selfcheck
        out['repairs'] = selfcheck.notices()
    except Exception as e:  # noqa: BLE001 - the page draws without them
        log('what was repaired: %r' % e)
    return out


# ---------- Sign in to all (signins.py): its queue lives in this process, so every tab draws the same ----------

def signins_accounts(st):
    """The page's accounts as signins.pick reads them."""
    return [dict(a, email=e) for e, a in st['accounts'].items() if a.get('connected')]


def signins_view(st, now):
    """Where Sign in to all is, for /api/state (signins.view). Never fails the page."""
    try:
        return signins.view(signins_accounts(st), now)
    except Exception as e:  # noqa: BLE001 - the page shows without it
        log('sign in to all: %r' % e)
        return {'state': 'idle', 'due': []}


def signin_start(email):
    """One account's sign-in, started the way its row's Sign in starts it (POST /api/connect), in its own browser
    profile (a new one when it has none, or its profile is gone). Returns the sign-in's key."""
    a = load()['accounts'].get(email) or {}
    p = a.get('profile')
    return _act('/api/connect', {'email': email, 'profile': p if p and p in browser.profiles() else '+new'})['key']


def signin_say(text, kind='info'):
    """A line in History, from the queue's own thread."""
    with locked() as st:
        event(st, text, kind)


def signins_act(what):
    """POST /api/signins: 'start' (Sign in to all: the accounts signins.pick takes now, one after another; a press
    while it runs starts nothing more), 'stop' or 'hide' (the closing line). Answers with where it is now."""
    line = None
    if what == 'start':
        q = signins.start(signins.pick(signins_accounts(load()), time.time()))
        line = None if q.emails else q.line
    elif what == 'stop':
        signins.stop()
    elif what == 'hide':
        signins.hide()
    else:
        raise SwitchError('Unknown request.')
    return {'signins': signins_view(load(), time.time()), 'line': line}


signins.START = signin_start
signins.SAY = signin_say


# ---------- what the page can ask for ----------

def account_of(email):
    """The engine's id for an account (its email when the state has no id yet). Only accounts the engine keeps can be
    switched to or changed."""
    a = load()['accounts'].get(email) or {}
    if not a.get('connected') or not a.get('slot') or a.get('unmanaged'):
        raise SwitchError('That account is not set up here.')
    return a.get('id') or email


def switch_to(email, source='page'):
    """Move Claude Code to the account. The engine records the switch itself; the state learns the account in use at
    once (and says the switch only when the engine did not)."""
    since = time.time()
    engine.switch(account_of(email), source=source, reason='Picked on the %s.' % (
        'menu bar' if source == 'menu' else 'page'))
    with locked() as st:
        if history.recorded_switch(email, since) is None:
            event(st, 'Switched Claude Code to %s' % email, 'switch', frm=st.get('active'), by='you')
        set_active(st, email)
        st['switched_at'] = time.time()
    owner_map_now()
    write_status_file(st, plan_view(st, plan_settings(engine.auto_settings()), time.time())['next'])
    if source == 'menu':                # the menu bar shows no line: the one-time desktop line waits for one that does
        return {}
    # What the person should know about where Claude Code went, said once under the switch they made (the page, a
    # terminal; the tick's notification is for switches made elsewhere): its pages' profile or Claude in Chrome, and
    # the desktop app's line the first time.
    return {'note': ' '.join(x for x in (_switch_note(st, email), artifacts.desktop_note()) if x) or None}


def _switch_note(st, email, label=None):
    """artifacts.switch_note for a switch to `email`, or None when it can't be worked out."""
    try:
        return artifacts.switch_note(st, email, label=label)
    except Exception as e:  # noqa: BLE001
        log('the switch note: %r' % e)
        return None


def text(body, key):
    v = body.get(key)
    return v.strip() if isinstance(v, str) else ''


def number(body, key, lo=engine.LINE_MIN, hi=engine.LINE_MAX):
    """A number the page sent, exactly as sent: a finite number from lo to hi, or a plain refusal. Never clamped."""
    v = body.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float('inf'), float('-inf')) \
            or not lo <= v <= hi:
        raise SwitchError('Pick a number from %g to %g.' % (lo, hi))
    return v


def set_profile(email, choice):
    """Link an account to a browser profile: a folder name, '' for none, or '+new' for a fresh one."""
    with locked() as st:
        a = st['accounts'].get(email)
        if a is None:
            raise SwitchError('That account is not set up here.')
        if choice == '':
            a.update(profile=None, profile_src='none')
            a.pop('signed_out', None)
            return {}
        fresh = choice == '+new'
        if fresh and not browser.present():             # no browser here to make it in, or to open it later
            raise SwitchError('A new profile needs %s. Install it and open it once.' % browser.app())
        profile = browser.new_profile_name(artifacts.profile_label(email, artifacts.page_accounts(st, email))) \
            if fresh else choice
        if not browser.PROFILE_RE.match(profile) or (not fresh and profile not in browser.profiles()):
            raise SwitchError('That browser profile does not exist.')
        for other in st['accounts'].values():                   # one profile, one account
            if other is not a and other.get('profile') == profile:
                other.update(profile=None, profile_src=None)
        a.update(profile=profile, profile_src='you')
        a.pop('signed_out', None)
        core.profile_unproven(st, profile)      # its link changed hands: "Signed in" again once a page loads there
        if fresh:
            browser.open_url(profile, 'https://claude.ai/login')
            event(st, 'Made the %s profile "%s" for %s' % (browser.app(), profile, email))
            return {'opened': True, 'profile_name': profile}
        return {}


def act(path, body):
    if core.DEMO:
        from . import demo
        return demo.act(path, body)
    return _act(path, body)


NOT_MINE = object()
LANE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$')


def _extras(path, body):
    """The Claude Code switches, the keys and the gateway. NOT_MINE for any other path."""
    if path == '/api/pack':
        pack = body.get('pack')
        if body.get('do') is not None:                  # repair; map or refresh a project folder (packs/install.py)
            return claudecode.act(pack, body, say=_note_event)
        if 'on' not in body and 'cap' in body and pack == 'claudemem':
            r = claudecode.turn(pack, cap=body.get('cap'))
        elif isinstance(body.get('on'), bool):
            r = claudecode.turn(pack, on=body['on'], cap=body.get('cap') if body['on'] and 'cap' in body else None,
                                consent=body.get('consent'), say=_note_event)
        else:
            raise SwitchError('Say on or off.')
        if r.get('state') == 'plan':                    # the install's plan, shown for consent: nothing changed yet
            return r
        names = {'house': 'House rules', 'codemap': 'Code map', 'claudemem': 'Watching chats with claude-mem'}
        with locked() as st:
            event(st, 'Installing %s' % ('claude-mem' if pack == 'claudemem' else 'codegraph') if r.get('state') == 'installing'
                  else '%s %s' % (names.get(pack, pack), 'cap set' if 'on' not in body else
                                  'on' if body.get('on') is True else 'off'), 'info')
        return r
    if path == '/api/keys':
        if 'key' in body:                       # one key, once: straight to the Keychain, never kept here
            key = body.pop('key')
            r = lanes.add(body.get('name'), key)
            key = None
            with locked() as st:
                event(st, 'Added the key %s (…%s)' % (r['name'], r['last4']), 'info')
            return {'name': r['name'], 'last4': r['last4'], 'keys': lanes.view()}
        name = body.get('name')
        if not isinstance(name, str) or not LANE_RE.match(name):
            raise SwitchError('There is no key by that name.')
        if body.get('remove') is True:
            if body.get('confirm') is not True or not answered('key', name):
                asked('key', name)
                return {'state': 'confirm', 'line': 'Remove the key %s? It is deleted from your Keychain here; the '
                        'key itself stays valid with its provider.' % name}
            lanes.remove(name)
            if not lanes.lanes() and lanes.view()['on']:
                lanes.turn(False)                # nothing left behind it
            with locked() as st:
                event(st, 'Removed the key %s' % name, 'warn')
            return {'keys': lanes.view()}
        if body.get('step') in (-1, 1):
            lanes.move(name, body['step'])
            return {'keys': lanes.view()}
        raise SwitchError('Unknown action')
    if path == '/api/gateway':
        if body.get('on') is True and not lanes.lanes():
            raise SwitchError('Add a key first.')
        r = lanes.turn(body.get('on') is True)
        with locked() as st:
            event(st, 'Keys gateway %s' % ('on' if r['on'] else 'off'), 'info')
        return {'keys': r}
    return NOT_MINE


def _more(path, body, email):
    """The opened line's and the More area's actions (more.py). NOT_MINE for any other path."""
    if path == '/api/purge':
        gone = [(e, a.get('org_id') or '') for e, a in load()['accounts'].items()
                if a.get('connected') and not a.get('unmanaged')]
        gone += [x for x in engine.saved_identities() if x not in gone]
        more.purge(body.get('typed'))
        me = keep.live()
        keep.let_go(*(gone + ([me] if me else [])))     # removed here: none of them is kept again by itself
        with locked() as st:
            event(st, 'Removed every account from Switchboard', 'warn')
        WAKE.set()
        return {}
    if path == '/api/resume':                   # the Chats view's "Reopen at home"
        return resume_chat(body.get('chat'))
    if path not in ('/api/remove', '/api/terminal', '/api/move'):
        return NOT_MINE
    if not EMAIL_RE.match(names.email_of(email)):
        raise SwitchError('That does not look like an email address.')
    target = account_of(email)
    if path == '/api/remove':
        if body.get('confirm') is not True or not answered('remove', email):
            asked('remove', email)              # the page shows this line, then sends the yes
            return {'state': 'confirm', 'line': more.remove_line(email, email == load().get('active'))}
        who = (email, (load()['accounts'].get(email) or {}).get('org_id') or '')
        more.remove(target, True)
        keep.let_go(who)                        # this account (email and organization) is never kept again by itself:
                                                # only add keeps it again
        with locked() as st:
            event(st, 'Removed %s from Switchboard' % email, 'warn')
    elif path == '/api/terminal':
        more.terminal(target, email)
        return {}
    elif path == '/api/move':
        more.move(target, body.get('to'))
    WAKE.set()
    return {}


# A chat opened again as the account it started under, from the Chats view: Terminal runs `switchboard resume <id>`
# (more.chat_terminal; resume.py says which account in that window). A chat that a window holds or is writing is
# refused here first, in resume.py's words, so no window opens only to say so.
TWO_WINDOWS = resume.TWO_WRITERS.replace(', or add --fork to continue in a copy', ' first')


def resume_chat(given):
    cid = given.strip().lower() if isinstance(given, str) else ''
    if not more.CHAT_RE.match(cid):
        raise SwitchError('That is not a chat id.')
    cid, path = resume.find(cid)
    if resume.in_use(cid, path):
        raise SwitchError(TWO_WINDOWS)
    more.chat_terminal(cid)
    return {}


# What an older copy of the page may still ask: said plainly, never "unknown".
RETIRED = ('/api/repair', '/api/kept', '/api/bringover', '/api/hold', '/api/line', '/api/rank', '/api/token',
           '/api/folder', '/api/backup', '/api/restore', '/api/clear', '/api/setting')


def _act(path, body):
    email = text(body, 'email').lower()
    if ' (' in email:                   # an account named with its organization (names.py): the state's own key
        email = names.key_of(load()['accounts'], email)
    if path in RETIRED:
        raise SwitchError('Switchboard does not do that any more. Reload the page.')
    if path == '/api/refresh':                  # Read again: the loop that reads the accounts reads now (all: every
        at = engine.request_read(all_accounts=body.get('all', True) is not False)   # account, else the one in use)
        WAKE.set()
        return {'asked': at} if at is not None else None
    if path == '/api/go':
        link = artifacts.parse_link(text(body, 'url'))      # every form the link app routes, one rule for both
        if not link:
            raise SwitchError('Paste a claude.ai artifact link.')
        url = link['url']
        if body.get('wait') is False:       # a clicked link: answer at once, open in the background
            # The link app's own timer opens the link from the owner map when no answer came in time: a request that
            # reaches the page after that is not opened a second time (artifacts.go_quietly reads both times).
            threading.Thread(target=artifacts.go_quietly, args=(url,), daemon=True,
                             kwargs={'sent_at': body.get('sent_at'), 'fallback_after': body.get('fallback_after')}
                             ).start()
            return
        r = artifacts.go(url, wait=artifacts.GO_PAGE_WAIT)     # a busy browser is said, never waited out here
        return dict(r, name=name_of(load(), r.get('email')),
                    profile_name=browser.profiles().get(r.get('profile'), r.get('profile') or ''))
    if path == '/api/setup-profile':                # "Set up": a new profile for the account, claude.ai's sign-in in it
        email = text(body, 'email').lower()
        if not EMAIL_RE.match(email):
            raise SwitchError('That does not look like an email address.')
        r = artifacts.setup_profile(email)
        WAKE.set()
        return r
    if path == '/api/extension':                    # "Add to this profile": the Claude extension's store page there
        return artifacts.add_extension(text(body, 'profile'))
    if path == '/api/links':
        if 'home' in body:
            linkapp.set_home_profile(text(body, 'home'))
        if 'on' not in body:
            return
        # Opening links as their owner is always on: the banner's one button and `switchboard links on`
        # ask macOS to make the link app the default browser; `switchboard links off` is the way out, and no banner
        # offers it again. macOS is asked only when the seat would change hands, so off never hands a seat the link
        # app does not hold to a browser chosen here. Without the link app, on says what it needs; off is only noted.
        on = bool(body['on'])
        st = linkapp.status(fresh=True)
        asked = on != bool(st.get('installed') and st.get('on'))
        if asked:
            linkapp.turn(on)
        set_setting(LINKS_WANTED, on)           # off on purpose: no banner about it
        with LINKS_LOCK:                        # macOS asks; its answer is read every few seconds for a while
            LINKS['banner'] = None
            LINKS['fast_until'] = monotonic() + 30
        refresh_link_status(force=True)
        return {'asked': asked}
    if path == '/api/auto':
        enabled = body.get('enabled')
        strategy = body.get('strategy') if 'strategy' in body else None
        model = body.get('model') if 'model' in body else None
        rule = body.get('rule') if 'rule' in body else None
        if strategy is not None and (not isinstance(strategy, str) or strategy not in engine.STRATEGIES):
            raise SwitchError('Unknown order.')
        if model is not None and not isinstance(model, (str, list)):
            raise SwitchError('That is not a model name.')
        who = None
        unsent = object()
        line = body.get('own_lines') if 'own_lines' in body else unsent     # None: back on the shared lines
        rank = body.get('rank') if 'rank' in body else unsent               # None: no rank
        if rule is not None:                    # one account's rule: {'rule': 'in' | 'last' | 'out', 'account': NAME}
            if not isinstance(rule, str) or rule not in engine.RULES:
                raise SwitchError(engine.RULE_WORDS)
        if line is not unsent:                  # its own lines: {'own_lines': {'session_at', 'weekly_at'}, 'account'}
            engine.check_own((None, line))
        if rank is not unsent:                  # its rank: {'rank': 1 to 99 or None, 'account': NAME}
            engine.check_rank((None, rank))
        if rule is not None or line is not unsent or rank is not unsent:
            name = body.get('account')
            if isinstance(name, bool) or not isinstance(name, (str, int)) or not str(name).strip():
                raise SwitchError('Name an account: its number, email, short name or id.')
            if not set(body) - {'rule', 'account'} and bearers.find(str(name)) is not None:
                try:
                    engine.find(name)
                except SwitchError:             # a one-year token or an API key kept as an account (a key: refused)
                    said = bearers.set_rule(str(name), rule)
                    with locked() as st:
                        event(st, said, by='you', h='setting')
                    return
            who = engine.find(name)             # a name no account has: its own plain sentence, as everywhere
        change = functools.partial(engine.set_auto, enabled=None if enabled is None else bool(enabled),
                                   session_at=number(body, 'session_at'), weekly_at=number(body, 'weekly_at'),
                                   model_at=number(body, 'model_at'), strategy=strategy,
                                   model=None if model is None else model.strip() if isinstance(model, str) else model,
                                   rule=None if who is None or rule is None else (who.id, rule),
                                   **dict((k, (who.id, v)) for k, v in (('line', line), ('rank', rank))
                                          if v is not unsent))
        if enabled is not None:                 # the page's switch, the menu bar's, `switchboard auto on|off`: yours,
            keep.by_you(change)                 # marked before it changes, so the tick never turns it back on
        else:
            change()
        a = engine.auto_settings()              # the engine is asked outside the state lock
        owner_map_now()                         # the session hooks read whether it is on from the map
        label = names.of_account(who, engine.accounts()) if who is not None else None   # two may share an email
        with locked() as st:
            if who is not None and rule is not None:
                event(st, term.rule_said(label, rule), by='you', h='setting')
            if line is not unsent:
                event(st, autorules.line_said(label, autorules.own_lines_of(a, who.id), autorules.rule_of(a, who.id)),
                      by='you', h='setting')
            if rank is not unsent:
                event(st, autorules.rank_said(label, autorules.rank_of(a, who.id), autorules.rule_of(a, who.id)),
                      by='you', h='setting')
            if who is None or set(body) - {'rule', 'account', 'own_lines', 'rank'}:
                event(st, 'Automatic switching %s (5-hour %g%%, weekly %g%%)' % (
                    'on' if a['enabled'] else 'off', a['session_at'], a['weekly_at']), by='you',
                    **({'h': 'auto', 'on': bool(enabled)} if enabled is not None else {'h': 'setting'}))
        WAKE.set()                              # what it waits for, on the page now (set_auto asked the loop already)
        return
    if path == '/api/check':                        # "Check": runs on its own thread, the answer comes in the state
        return check_start()
    if path == '/api/engine':
        if body.get('add'):                     # the page's Add (and `switchboard add`, `setup` through the page)
            with keep.ADDING:                   # a keep in the tick's look waits, and this waits for one
                before = engine.saved_identities()
                me = keep.live()
                engine.add_current()
                history.kept_by_add(before, 'auto' if body.get('by') == 'auto' else 'you')   # `switchboard` alone: by itself
            if me:
                keep.forget(me)                 # kept on purpose: it may be kept by itself again
            WAKE.set()
        return
    r = _extras(path, body)
    if r is not NOT_MINE:
        return r
    r = _more(path, body, email)
    if r is not NOT_MINE:
        return r
    if path == '/api/setup':
        set_setting('setup_hidden', bool(body.get('hidden')))
        return
    if path == '/api/connect':
        if email and not EMAIL_RE.match(email):
            raise SwitchError('That does not look like an email address.')
        choice = text(body, 'profile') or '+new'
        under_way = connect.profile_of(email or 'new') if choice == '+new' else None
        if not browser.present():
            choice = None                         # no Chrome, Brave or Edge here yet: the default browser
        elif under_way:
            choice = under_way                    # pressed again: the profile its sign-in is in, not one more
        elif choice == '+new':                  # named as the page will name the account once it is added
            accounts = artifacts.page_accounts(load(), email)
            if email:
                accounts.setdefault(email, {})
            choice = browser.new_profile_name(artifacts.profile_label(email, accounts) if email else 'account')
        elif choice not in browser.profiles():
            raise SwitchError('That browser profile does not exist.')
        key = connect.start(email, choice)        # waits for Claude Code's link, so not under the lock
        # Pressed again while one is under way: a sign-in that had finished is kept as it is, and nothing opened; one
        # still waiting opened again in the profile it is in, whatever this press named.
        if getattr(key, 'did', 'opened') in ('opened', 'again'):
            where = getattr(key, 'profile', choice)
            with locked() as st:
                event(st, 'Opened the sign-in%s in %s' % (' for ' + email if email else '', '%s (%s)' % (
                    browser.app(), browser.profiles().get(where, where)) if where else 'your browser'))
        return {'key': key}
    if path == '/api/cancel':
        connect.stop(text(body, 'key'))
        return
    if path == '/api/signins':
        return signins_act(text(body, 'do'))
    if path in ('/api/awake', '/api/notify'):
        what, on = path.rsplit('/', 1)[1], bool(body.get('on'))
        said = None
        if what == 'notify':                    # Claude Code's "done" and "needs you" hooks go in or out with it

            def change():                       # the setting is written in the same turn as the hooks, so a second
                done = notify_hooks.turn(on)    # switch waits for both, and the last one decides both
                set_setting(what, on)
                return done
            try:                                # yours first: they never go on by themselves again, even if this fails
                said = keep.notify_by_you(change)
            except notify_hooks.PlanError as e:
                raise SwitchError(str(e))
        else:
            set_setting(what, on)
        if what == 'awake' and not on:
            with MAC_LOCK:
                awake_helper().release()
        with locked() as st:
            event(st, '%s: %s' % ('Keep the Mac awake' if what == 'awake' else 'Notifications', 'on' if on else 'off'))
        return said
    if path == '/api/models':
        on = body.get('enabled')
        if not isinstance(on, bool):
            raise SwitchError('That needs on or off.')
        r = models_turn(on)
        if r['changed']:
            with locked() as st:
                event(st, 'Sonnet for helpers: %s' % ('on' if on else 'off'))
        return r
    if path == '/api/statusline':
        on = bool(body.get('on'))
        r = keep.statusline_by_you(lambda: statusline_turn(on))     # yours first: it never goes on by itself again
        if r['changed']:
            with locked() as st:
                event(st, 'Status line in Claude Code: %s' % ('on' if on else 'off'))
        return {'changed': r['changed']}
    if path == '/api/consent':                  # "Let Switchboard check links": macOS asks, only from this press
        return consent_ask()
    if path == '/api/notifytest':               # "Send a test", then "did you see it?"
        if isinstance(body.get('seen'), bool):
            set_setting('notify_seen', body['seen'])
            return {'line': 'Notifications work.' if body['seen'] else notify.SCRIPT_EDITOR_OFF}
        sent, line = notify.send_test(run=NOTIFY_RUN)
        return {'sent': sent, 'line': line}
    if not EMAIL_RE.match(names.email_of(email)):
        raise SwitchError('That does not look like an email address.')
    if path == '/api/switch':
        said = switch_to(email, source='menu' if body.get('from') == 'menu' else 'page')
        WAKE.set()
        return said
    elif path == '/api/profile':
        r = set_profile(email, text(body, 'profile'))
        owner_map_now()                         # the link app finds the account's new profile at once
        return r
    elif path == '/api/open':
        url = text(body, 'url') or 'https://claude.ai/new'
        if body.get('extension') is True:           # Chrome setup: the Claude extension's own page, in this profile
            url = browser.CLAUDE_EXTENSION_PAGE
        elif not url.startswith(OPENABLE):
            raise SwitchError('Only claude.ai links open from here.')
        profile = (load()['accounts'].get(email) or {}).get('profile')
        if not profile:
            raise SwitchError('Pick a browser profile for this account first.')
        browser.open_url(profile, url)
    elif path == '/api/alias':
        alias = text(body, 'alias')
        if alias and not ALIAS_RE.match(alias):
            raise SwitchError('Names start with a letter or number and can use letters, numbers, - and _, up to 20.')
        engine.set_alias(account_of(email), alias)
        WAKE.set()
    else:
        raise SwitchError('Unknown action')


# ---------- GET /api/state from a snapshot built off the request path ----------
#
# The page asks every 5 s, the menu bar every 15 s, the terminal on every command. Building the answer reads the state,
# Chrome's profiles, Claude Code's settings and the history; under load that can take longer than a tab would wait.
# So one thread builds it at a time (single flight) and every request takes the newest
# one: at once while nothing it shows has changed and it is under MAX_AGE seconds old; else a build starts (after a
# change at once, otherwise at most every MIN_GAP seconds) and the request waits for it a short, bounded time: ACTION_
# WAIT after an action (a POST), CHANGED_WAIT after another change (a file Switchboard keeps, a write by this process),
# STALE_WAIT when it is only older, FIRST_WAIT when there is no answer yet (7 s, under the page's own 8 s). Past that it gets the last good answer as it
# is, with its age in "answer" (the page then says Switchboard is busy and keeps showing it), or, with none at all, 503
# and a retry hint. Nothing here starts a program or waits on the Keychain or the network. Durations are monotonic.

def _finite_json(x):
    """x with every float that is not a finite number made null: JSON has no NaN, and one would break the page's
    parse."""
    if isinstance(x, float):
        return x if x == x and x not in (float('inf'), float('-inf')) else None
    if isinstance(x, dict):
        return dict((k, _finite_json(v)) for k, v in x.items())
    if isinstance(x, (list, tuple)):
        return [_finite_json(v) for v in x]
    return x


def to_json(body):
    """Bytes of JSON any browser parses: never NaN or Infinity."""
    try:
        return json.dumps(body, allow_nan=False).encode()
    except ValueError:
        return json.dumps(_finite_json(body), allow_nan=False).encode()


# The files whose change means the state may show something new (stat only: size, time, inode). Keep this list in
# step with what view() reads.
SNAPSHOT_FILES = [lambda: data_path('state.json'), lambda: data_path('settings.json'),
                  lambda: data_path('history.jsonl'), lambda: statusline.live_dir(), lambda: statusline.settings_path(),
                  # the account engine's own files: its accounts, the readings, the owner map
                  # the page writes, a switch under way, and the automatic loop's heartbeat
                  lambda: data_path('accounts.json'), lambda: data_path('usage.json'), lambda: data_path('owners.json'),
                  lambda: data_path('switch-journal.json'), lambda: data_path('auto-heartbeat.json')]
# the other assistants' settings, readings and programs (the board, providers_view)
SNAPSHOT_FILES += [lambda: data_path('providers/settings.json'), lambda: data_path('providers/readings.json'),
                   lambda: data_path('providers/programs.json')]


def files_signal():
    out = []
    for where in SNAPSHOT_FILES:
        try:
            p = where()
            s = os.stat(p) if p else None
            out.append((s.st_mtime_ns, s.st_size, s.st_ino) if s else None)
        except (OSError, TypeError, ValueError):
            out.append(None)
    return tuple(out)


def world_key():
    """Which Switchboard the snapshot is of: its folders (a test, or a second copy, never gets another's answer)."""
    return (core.data_dir(), core.home(), core.claude_dir())


class Snapshot(object):
    """GET /api/state's answer (see above). build: what makes it (view); signal: the files' change signal."""
    MAX_AGE = 2.0
    MIN_GAP = 2.0
    STALE_WAIT = 0.15
    CHANGED_WAIT = 1.0
    ACTION_WAIT = 3.0               # right after an action (a POST): its own result is what the next look wants to see
    FIRST_WAIT = 7.0                # under the page's own 8 s: it hears "busy" rather than nothing
    SIGNAL_EVERY = 0.2
    STUCK = 30.0
    RETRY = 2

    def __init__(self, build=None, signal=None, clock=None, wall=None):
        self._build = build
        self._files = signal
        self.clock = clock or monotonic
        self.wall = wall or time.time
        self.cond = threading.Condition()
        self.snap = None            # {'body', 'key', 'signal', 'built', 'wall', 'seq', 'took'}
        self.failed = None          # {'seq', 'key'} of the last build that raised
        self.building = False
        self.thread = None
        self.seq = 0                # builds started
        self.done = 0               # the seq of the last build that ended
        self.again = False          # a change came while a build ran: one more build right after it
        self.running_sig = None     # the signal the running build read the world at (None: not read yet)
        self.last_start = None
        self.gen = 0                # moved by invalidate()
        self.builds = 0             # builds that ended (tests count them)
        self.stuck_said = False
        self._sig = (None, None)    # (when, signal): the files' signal is looked at most every SIGNAL_EVERY

    def invalidate(self):
        """Something changed that the files may not show (an action, an answer from macOS): build again."""
        with self.cond:
            self.gen += 1

    def signal(self):
        now = self.clock()
        at, files = self._sig
        if at is None or not 0 <= now - at < self.SIGNAL_EVERY:
            files = (self._files or files_signal)()
            self._sig = (now, files)
        return (self.gen, core.writes(), files)

    def _start(self):
        """Start one build (the caller holds cond and has checked none runs). The build reads the signal and the
        world itself, outside the lock, before it reads anything else."""
        self.building, self.again, self.running_sig = True, False, None
        self.seq += 1
        self.last_start = self.clock()
        self.stuck_said = False
        t = threading.Thread(target=self._run, args=(self.seq,), name='switchboard-state', daemon=True)
        self.thread = t
        try:
            t.start()
        except RuntimeError as e:   # no thread can start (the process is out of them): the last answer stays
            self.building = False
            self.done = self.seq
            log('the page state could not start a build: %r' % e)

    def _run(self, seq):
        t0 = self.clock()
        body, key, sig = None, None, None
        try:
            key, sig = world_key(), self.signal()
            with self.cond:
                self.running_sig = sig
            body = to_json((self._build or view)())
        except Exception as e:  # noqa: BLE001 - the last good answer stays; the log says what
            log('the page state could not be built: %r' % e)
        with self.cond:
            now = self.clock()
            if body is not None:
                self.snap = {'body': body, 'key': key, 'signal': sig, 'built': now, 'wall': self.wall(),
                             'seq': seq, 'took': now - t0}
                self.failed = None
            else:
                self.failed = {'seq': seq, 'key': key}
            self.building = False
            self.done = seq
            self.builds += 1
            if self.again:
                self._start()
            self.cond.notify_all()

    def _stamp(self, snap, now, stale):
        """The snapshot's bytes with "answer" in front: its age, when it was made, and whether a newer one is on its
        way or was wanted and did not come in time."""
        answer = {'age': round(max(0.0, now - snap['built']), 1), 'built': snap['wall'], 'stale': bool(stale),
                  'building': self.building, 'took': round(snap['took'], 3)}
        if self.building and self.last_start is not None:
            answer['building_for'] = round(max(0.0, now - self.last_start), 1)
        busy = stage_view()
        if busy:
            answer['loop'] = busy                       # e.g. {'stage': 'reading the accounts', 'for': 45.2}
        head = json.dumps({'answer': answer}).encode()[:-1]       # '{"answer": {...}'
        body = snap['body']
        return head + (b'}' if body == b'{}' else b', ' + body[1:])

    def _say_stuck(self, now):
        """A build running past STUCK seconds is said once in the log, with where it waits (file, line, function)."""
        if self.stuck_said or not self.building or self.last_start is None or now - self.last_start < self.STUCK:
            return
        self.stuck_said = True
        where = ''
        frame = sys._current_frames().get(getattr(self.thread, 'ident', None))
        if frame is not None:
            where = ' at ' + ' < '.join('%s:%d %s' % (os.path.basename(f.filename), f.lineno, f.name)
                                        for f in reversed(traceback.extract_stack(frame)[-3:]))
        log('the page state has been building for %d s%s' % (now - self.last_start, where))

    def answer(self):
        """(HTTP status, body bytes) for GET /api/state."""
        if core.DEMO:                           # the demo's state is made up in memory: nothing to wait for
            return 200, to_json(view())
        key, sig = world_key(), self.signal()
        with self.cond:
            now = self.clock()
            snap = self.snap if self.snap and self.snap['key'] == key else None
            if snap and snap['signal'] == sig and now - snap['built'] < self.MAX_AGE:
                return 200, self._stamp(snap, now, False)
            changed = snap is None or snap['signal'] != sig
            acted = snap is not None and snap['signal'][0] != sig[0]       # invalidate() moved since it was built
            wait = self.FIRST_WAIT if snap is None else self.ACTION_WAIT if acted else \
                self.CHANGED_WAIT if changed else self.STALE_WAIT
            if self.building:
                # The running build reads the world as this request sees it, unless it read it before a change.
                later = changed and self.running_sig is not None and self.running_sig != sig
                want = self.seq + 1 if later else self.seq
                if later:
                    self.again = True
                # A build already running this long is late: wait only what is left of the budget (none when it is
                # stuck), so every request during a stuck build gets the last answer at once.
                wait = max(0.0, wait - max(0.0, now - (self.last_start if self.last_start is not None else now)))
            elif changed or self.last_start is None or not 0 <= now - self.last_start < self.MIN_GAP:
                self._start()
                want = self.seq
            else:
                return 200, self._stamp(snap, now, True)     # built moments ago: the next look builds again
            until = monotonic() + wait              # the budget in real seconds, whatever clock the ages are on
            while self.done < want and self.building:
                left = until - monotonic()
                if left <= 0:
                    break
                self.cond.wait(left)
            now = self.clock()
            self._say_stuck(now)
            fresh = self.snap if self.snap and self.snap['key'] == key else None
            if fresh is not None:
                return 200, self._stamp(fresh, now, fresh['seq'] < want)
            if self.failed and self.failed['key'] == key and not self.building:
                return 500, to_json({'error': 'Something went wrong.'})
            return 503, to_json({'error': 'Switchboard is busy. It answers again in a moment.', 'busy': True,
                                 'retry': self.RETRY, 'version': VERSION, 'folder': core.folder_mark()})


SNAPSHOT = Snapshot()


# ---------- the page's own server: a bounded number of request threads, quiet when a client goes away ----------

CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout, TimeoutError)
QUIET = {'at': None, 'count': 0}
QUIET_LOCK = threading.Lock()
QUIET_EVERY = 60.0          # seconds (monotonic): clients that went away are counted in the log at most this often


def client_gone(what=''):
    """A client that closed its side before it read the answer (a tab that gave up, a menu bar that timed out): not an
    error. Counted, and said in one line at most once a minute."""
    now = monotonic()
    with QUIET_LOCK:
        QUIET['count'] += 1
        if QUIET['at'] is not None and 0 <= now - QUIET['at'] < QUIET_EVERY:
            return
        n, QUIET['count'], QUIET['at'] = QUIET['count'], 0, now
    log('%d request%s ended by the other side before the answer%s' % (n, '' if n == 1 else 's',
                                                                       (' (%s)' % what) if what else ''))


class PageServer(StdThreadingHTTPServer):
    """ThreadingHTTPServer with at most MAX_THREADS requests answered at once. One more is refused with 503 and a retry
    hint by one thread of its own (never a pile of threads that each wait, and never the listening thread, which only
    accepts); past REFUSE_QUEUE waiting refusals the listening thread sends that answer itself, without waiting. A
    refused connection is never closed without its answer, and a refusal never waits for its client. A client that goes
    away is never a traceback in the log."""
    daemon_threads = True
    block_on_close = False
    request_queue_size = 128        # macOS's own limit for a listening socket's queue
    MAX_THREADS = 16
    REFUSE_WAIT = 0.5               # seconds a refusal may take to send its answer (a new connection takes it at once)
    REFUSE_QUEUE = 128              # refusals waiting their turn; past that the listening thread answers, never waiting
    REFUSERS = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.slots = threading.BoundedSemaphore(self.MAX_THREADS)
        self.refused = 0
        self.refusals = queue.Queue(self.REFUSE_QUEUE)
        for _ in range(self.REFUSERS):
            threading.Thread(target=self._refuser, name='switchboard-refuser', daemon=True).start()

    def server_close(self):
        super().server_close()
        refusals = getattr(self, 'refusals', None)      # None: the port was taken, so nothing was started
        for _ in range(self.REFUSERS if refusals is not None else 0):
            try:
                refusals.put_nowait(None)       # each refuser ends
            except queue.Full:
                pass

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.refuse(request)
            return
        try:
            threading.Thread(target=self._answer, args=(request, client_address), daemon=True).start()
        except RuntimeError:            # no thread can start
            self.slots.release()
            self.refuse(request)

    def _answer(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:  # noqa: BLE001
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            self.slots.release()

    def refuse(self, request):
        """Hand the connection to the refuser (the listening thread goes back to accepting at once). When REFUSE_QUEUE
        refusals already wait, it answers this one itself, without waiting for anything."""
        self.refused += 1
        try:
            self.refusals.put_nowait(request)
        except queue.Full:
            self.say_busy(request, wait=0.0)

    def _refuser(self):
        while True:
            request = self.refusals.get()
            if request is None:
                return
            self.say_busy(request)

    def say_busy(self, request, wait=None):
        """503 and Retry-After, sent first: a refusal never waits for its request. Under load a client can take seconds
        to send it, and refusals that each waited for it fell behind until connections were closed with no answer at
        all (a reset, which the client takes for no Switchboard). What of the request is already there is read, so the
        close is a plain one; a request that comes later finds its answer already sent (the client reads it before the
        reset its request meets). wait: seconds the answer may take to go (REFUSE_WAIT; 0 never blocks)."""
        body = to_json({'error': 'Switchboard is busy. It answers again in a moment.', 'busy': True,
                        'retry': Snapshot.RETRY, 'version': VERSION, 'folder': core.folder_mark()})
        try:
            request.settimeout(self.REFUSE_WAIT if wait is None else wait)
            request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\n'
                            b'Retry-After: %d\r\nCache-Control: no-store\r\nConnection: close\r\n'
                            b'Content-Length: %d\r\n\r\n' % (Snapshot.RETRY, len(body)) + body)
            request.setblocking(False)
            for _ in range(4):                      # what has arrived, never waiting for more
                if not request.recv(65536):
                    break
        except (OSError, ValueError):
            pass
        self.shutdown_request(request)

    def handle_error(self, request, client_address):
        """One line in the log, never a traceback on standard error (agent.log)."""
        e = sys.exc_info()[1]
        if isinstance(e, CLIENT_GONE):
            client_gone()
        else:
            log('a request failed: %r' % (e,))


# serve() makes the server by this name, as it always has: the bounded one now (a test may put its own in its place).
ThreadingHTTPServer = PageServer


class Handler(BaseHTTPRequestHandler):
    timeout = 30                    # seconds a connection may take to send its request, or to take its answer

    def log_message(self, *args):
        pass

    def handle(self):
        """A client that goes away while its request is read is not an error."""
        try:
            BaseHTTPRequestHandler.handle(self)
        except CLIENT_GONE:
            client_gone()

    def finish(self):
        try:
            BaseHTTPRequestHandler.finish(self)
        except CLIENT_GONE:
            client_gone()

    def _send(self, code, body, ctype='application/json', headers=()):
        """Answer once. A client that already went away is counted, never written to again (no second fault)."""
        data = body if isinstance(body, bytes) else to_json(body)
        self._answered = True
        try:
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('X-Content-Type-Options', 'nosniff')     # each answer is only what its Content-Type says
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            for k, v in headers:
                self.send_header(k, v)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except CLIENT_GONE:
            self.close_connection = True
            client_gone(self.path.split('?')[0])

    def _failed(self, what, e):
        """An answer that failed part-way: logged; a 500 only when nothing was sent yet (never a second answer)."""
        if isinstance(e, CLIENT_GONE):
            client_gone(self.path.split('?')[0])
            return
        log('%s %s failed: %r' % (what, self.path.split('?')[0], e))
        if not getattr(self, '_answered', False):
            self._send(500, {'error': 'Something went wrong.'})

    def _page(self, name):
        self._send(200, page_bytes(name), 'text/html; charset=utf-8')

    def _trusted(self):
        # Only this Mac's own page: blocks other websites and DNS-rebinding tricks.
        hosts = {'localhost:%d' % port(), '127.0.0.1:%d' % port()}
        origin = self.headers.get('Origin')
        return self.headers.get('Host') in hosts and (not origin or origin.split('//', 1)[-1] in hosts)

    def _refuse(self, method):
        """403, and one line in the log saying why: a refused caller is never silent. Repeats of the same line are
        counted by the log itself."""
        hosts = {'localhost:%d' % port(), '127.0.0.1:%d' % port()}
        origin = self.headers.get('Origin')
        if self.headers.get('Host') not in hosts:
            why = 'it asked for another host (%s)' % core.redact(self.headers.get('Host') or 'none')[:60]
        elif origin and origin.split('//', 1)[-1] not in hosts:
            why = 'it came from another website (%s)' % core.redact(origin)[:60]
        else:
            why = 'it did not carry the page\'s own header'
        log('refused %s %s: %s' % (method, self.path.split('?')[0][:80], why))
        return self._send(403, {'error': 'forbidden'})

    def do_GET(self):
        """Every GET is answered, with JSON when it is not a page: a failure is logged, never sent as raw text."""
        self._answered = False
        try:
            self._get()
        except Exception as e:  # noqa: BLE001
            self._failed('GET', e)

    def _get(self):
        if not self._trusted():
            return self._refuse('GET')
        route = self.path.split('?')[0]
        if route in ('/', '/index.html'):
            return self._page('index.html')
        if route == '/go':                      # the "Open as its owner" bookmark lands here
            # Any website can send a browser to this address. Only a visit that carries
            # this install's own key opens the link by itself; any other visit has to be clicked.
            given = urllib.parse.parse_qs(self.path.partition('?')[2]).get('k', [''])[0]
            mine = hmac.compare_digest(given.encode(), core.key().encode())
            with open(os.path.join(WEB, 'go.html'), 'rb') as f:
                page = f.read().replace(b'__AUTO__', b'true' if mine else b'false')
            return self._send(200, page, 'text/html; charset=utf-8')
        # A request another website started carries no Origin when it needs no answer back (an image, a no-cors
        # fetch), so the browser's own Sec-Fetch-Site is what tells it apart; tools that send none are answered.
        if route.startswith('/api/') and self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
            log('refused GET %s: another website asked for it' % route[:80])
            return self._send(403, {'error': 'forbidden'})
        if route == '/api/state':
            code, body = SNAPSHOT.answer()
            return self._send(code, body, headers=(('Retry-After', str(Snapshot.RETRY)),) if code == 503 else ())
        if route == '/api/models':
            return self._send(200, models_view())
        if route == '/api/more':
            return self._send(200, more_view())
        if route in ('/api/memory', '/api/claudecode', '/api/keys'):
            q = urllib.parse.parse_qs(self.path.partition('?')[2])
            if route == '/api/memory':
                return self._send(200, memorypage.view(q.get('q', [''])[0], q.get('fresh', [''])[0] == '1'))
            if route == '/api/claudecode':
                return self._send(200, claudecode_view())
            return self._send(200, keys_view())
        if route == '/api/usage':
            fresh = urllib.parse.parse_qs(self.path.partition('?')[2]).get('fresh', [''])[0] == '1'
            return self._send(200, usage_view(fresh))
        if route == '/api/history':
            return self._send(200, history_view())
        if route == '/api/artifacts':
            return self._send(200, artifacts_view())
        if route == '/api/chats':
            return self._send(200, chats_view())
        if route.startswith('/api/preview/'):
            return self._preview(route[len('/api/preview/'):])
        self._send(404, {'error': 'not found'})

    def _preview(self, aid):
        """GET /api/preview/<id>: the picture of one artifact's page (previews.picture: an id the page lists, never a
        path), else 404. Another website can't show it either: a request one started (Sec-Fetch-Site) is refused."""
        if self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
            log('refused GET /api/preview: another website asked for it')
            return self._send(403, {'error': 'forbidden'})
        data = None if core.DEMO else previews.picture(aid)
        if data is None:
            return self._send(404, {'error': 'not found'})
        return self._send(200, data, 'image/jpeg')

    def _from_here(self):
        """A request from this Mac's own page or apps: they send a header no website can send without asking."""
        if self.headers.get('X-Switchboard') == '1':
            return True
        return self._old_router()

    def _old_router(self):
        """Link Router.app sends its own header. It may only open links, and
        is answered at once (it gives up after a few seconds and opens the link plainly)."""
        return self.path == '/api/go' and self.headers.get('X-Claude-Switch') == '1' \
            and self.headers.get('X-Switchboard') != '1'

    MAX_BODY = 4 << 20              # a POST body larger than this is refused (a backup file is far smaller)

    def do_POST(self):
        self._answered = False
        try:
            if not self._trusted() or not self._from_here():
                return self._refuse('POST')
            try:
                size = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                size = -1
            if not 0 <= size <= self.MAX_BODY:
                return self._send(400, {'error': 'That request is too large.'})
            try:
                try:
                    body = json.loads(self.rfile.read(size) or b'{}')
                except ValueError:                  # not JSON (or not UTF-8): the caller's mistake, not a failure here
                    return self._send(400, {'error': 'That request is not JSON.'})
                body = body if isinstance(body, dict) else {}
                if self._old_router():
                    body['wait'] = False            # answered at once, opened in the background
                    note_forwarded()                # links it passes on open as their owner: the page's links line
                result = act(self.path, body)
            finally:
                SNAPSHOT.invalidate()               # whatever it changed, the next look at the state sees it
            self._send(200, dict(result or {}, ok=True))
        except SwitchError as e:
            fix = getattr(e, 'kind', None)
            self._send(400, dict({'error': str(e)}, **({'fix': fix} if fix else {})))
        except Exception as e:  # noqa: BLE001 - surface anything else on the page; the details go to the log only
            self._failed('action', e)


def page_answers(n, timeout=2):
    """True when http://127.0.0.1:n answers GET /api/state as Switchboard's own page (the demo's is not), or says it is
    Switchboard's page and busy (503 with busy and its version). Asked directly, never through a proxy."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open('http://127.0.0.1:%d/api/state' % n, timeout=timeout) as r:
            got = json.loads(r.read(16 << 20).decode('utf-8')) if r.status == 200 else None
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read(1 << 16).decode('utf-8')) if e.code == 503 else None
        except Exception:  # noqa: BLE001
            said = None
        finally:
            e.close()
        return isinstance(said, dict) and said.get('busy') is True and 'version' in said
    except Exception:  # noqa: BLE001 - whatever answers there, if it is not the page it is another program
        return False
    return isinstance(got, dict) and isinstance(got.get('accounts'), list) and 'version' in got \
        and got.get('demo') is False


def port_taken(n):
    """The one line for a port in use by something other than Switchboard's page. The port it suggests is never the
    one in use."""
    if core.DEMO:
        return ("Port %d is in use, so the demo can't start. Is another demo open? Or run: SWITCHBOARD_PORT=%d "
                "switchboard demo" % (n, 7803 if n == 7802 else 7802))
    return ("Port %d is in use by another program, so the page can't start. Pick a free one: switchboard update "
            "--port %d" % (n, 7802 if n == 7801 else 7801))


def serve(ready=None):
    """Run the page on this Mac's own address. `ready` is called once the port is the page's: the demo opens its tab
    then, so a tab never opens on a page that did not start. When Switchboard's own page already holds the port, say
    so and return: nothing is wrong. When another program holds it and launchd runs the page, everything but the page
    runs all the same and the page opens once the port is free (port_wait); run by hand, the plain line instead."""
    try:
        server = ThreadingHTTPServer(('127.0.0.1', port()), Handler)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        if not core.DEMO and page_answers(port()):
            print("Switchboard's page is already running at http://localhost:%d." % port())
            return
        if core.DEMO or not watch.background():
            raise SwitchError(port_taken(port()))
        server = None                           # the loop that reads and switches needs no port
    if server is not None:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    if ready and server is not None:
        ready()
    if core.DEMO:                               # the demo writes nothing to disk
        print('Switchboard demo (made-up accounts) at http://localhost:%d  -  Ctrl-C to stop' % port())
        while True:
            time.sleep(3600)
    os.makedirs(data_path('logins'), mode=0o700, exist_ok=True)
    watch_start(no_port=server is None)         # counted, watched (under launchd), and the port waited for
    more_files()                                # launchd's 256 open files would be few for a page under load
    core.rotate_own_output()                    # agent.log: launchd's file for this job's output, never unbounded
    core.sweep_temporary()                      # what a write cut off by a kill left behind
    with LINKS_LOCK:
        LINKS['loop'] = True                    # the link app's status comes from the loop from now on
    EASE['aware'] = True                        # under load, this process's loop does less
    load_week()                                 # the last week, at once; it is read again soon after
    first_read = threading.Timer(20, usage_view)  # the week is read before anyone asks, after the first answers
    first_read.daemon = True
    first_read.start()
    refresh_link_status(force=True)
    consent_refresh()
    signal.signal(signal.SIGTERM, on_sigterm)   # launchd stopping the job: let a switch in progress finish first
    connect.sweep()                             # a restart may have cut a sign-in short
    resolve_journal()                           # and a switch: the engine finishes or undoes it
    linkapp.announce_port()
    owner_map_now()                             # the link app's fallback is current from the start
    lanes.resume()                              # the keys gateway runs again when it was on
    for line in statusline.repair() + notify_hooks.repair():    # their Python gone: they run on this one
        log(line)
    if statusline_status()['on']:
        statusline.precompile()                 # the file may be newer than its bytecode after an update
    statusline_every_minute()                   # a line from before this ran every minute gets that, once
    if notify_hooks.status()['in']:
        notify_hooks.precompile()               # the same for the notification hooks
    start_session_hooks()                       # and the session hooks
    log('Switchboard %s running at http://localhost:%d' % (VERSION, port()))
    loop()


def start_session_hooks():
    """At the page's start, when the session hooks are in: hooks put in by an older version are brought up to date
    (they then hear every claude.ai connector), and their file is compiled again, as it may be newer than its
    bytecode after an update. Hooks that are not in are never put in here. Nothing here can stop the page."""
    try:
        from . import session_hooks
        if not session_hooks.status()['in']:
            return
        for line in session_hooks.repair():     # their Python gone: they run on this one
            log(line)
        if session_hooks.upgrade():
            log('the session hooks were brought up to date')
        session_hooks.precompile()
    except Exception as e:  # noqa: BLE001 - the hooks as they were still run; their first run compiles them
        log('the session hooks were not made ready: %r' % e)


def resolve_journal():
    """A switch cut off part-way (a crash, a power cut): the engine finishes or undoes it from what Claude Code's live
    login really holds, and the page says what it did in one line."""
    try:
        said = api.resolve_journal()
    except NotImplementedError:
        return None
    except Exception as e:  # noqa: BLE001 - the page starts either way; the engine refuses a switch until it is done
        log('finishing a cut-off switch: %r' % e)
        return None
    if said:
        with locked() as st:
            event(st, said, 'warn', h='repair', by='auto')
    return said


def more_files(want=4096):
    """Raise this process's soft limit of open files (launchd starts a job with 256) toward its hard limit, so a burst
    of connections never runs the page out of them. Returns the new soft limit, or None when it can't be told."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = want if hard == resource.RLIM_INFINITY else min(want, hard)
        if soft != resource.RLIM_INFINITY and soft < target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            return target
        return soft
    except (ImportError, ValueError, OSError):
        return None


def on_sigterm(signum=None, frame=None):
    """The background job is being stopped (launchd's bootout, which the installer and uninstaller use): stop
    the loop that reads the accounts and switches (engine.AutoHost.stop), so a switch in progress finishes (and a
    renewal already sent saves its answer), then exit. The job allows 60 s (ExitTimeOut) before launchd kills what is
    left."""
    try:
        watch.note_stopped()                    # stopped on purpose: the menu bar leaves it stopped
        lanes.stop()
        engine.HOST.stop()
    finally:
        log('Switchboard stopped')
        raise SystemExit(0)


# ---------- the page's start, its watchdog, and a port another program holds (watch.py) ----------

PORT_AGAIN = 60.0           # seconds between two tries of a port another program holds


def watch_start(no_port=False):
    """At the page's start: the start counted, a stop mark cleared (this page runs again), the loop's host told to wake
    the loop below when its loop ends, the watchdog armed when launchd runs the page, and the port waited for when
    another program holds it. Nothing here can stop the page."""
    STAGE.update(name='starting the page', since=monotonic(), thread=threading.get_ident(), said=False)
    try:
        watch.note_start()
        watch.clear_stopped()
    except Exception as e:  # noqa: BLE001
        log('counting the start: %r' % e)
    engine.HOST.on_exit = WAKE.set
    if watch.background():
        try:
            watch_page()
        except Exception as e:  # noqa: BLE001
            log("the page's watchdog could not start: %r" % e)
    if no_port:
        port_wait()
    else:
        watch.clear_port()


def watch_page():
    """The page's watchdog (watch.PageWatch) on a daemon thread: the loop's step (STAGE) and a build of the state that
    is still running (SNAPSHOT)."""
    w = watch.PageWatch(stage=lambda: (STAGE['name'], STAGE['since']),
                        building=lambda: SNAPSHOT.last_start if SNAPSHOT.building else None,
                        switching=watch.switch_in_progress)
    t = threading.Thread(target=w.run, name='switchboard-watchdog', daemon=True)
    t.start()
    return t


def port_wait(n=None):
    """Another program holds the page's port: said once (the log, a notification, the mark the menu bar reads), and
    the port tried again every PORT_AGAIN seconds on a thread of its own, so the page opens by itself once it is
    free. Everything else runs meanwhile."""
    n = port() if n is None else n
    mark = watch.mark_port(n)
    log('Port %d is in use by another program: the page waits for it; reading the accounts and auto-switch go on' % n)
    if helper_settings()['notify'] and not core.DEMO:
        try:
            notify.send(notify.port_taken(watch.port_words(n), mark['since']), run=NOTIFY_RUN)
        except Exception as e:  # noqa: BLE001
            log('notification failed: %r' % e)
    t = threading.Thread(target=_port_again, args=(n,), name='switchboard-port', daemon=True)
    t.start()
    return t


def _port_again(n):
    """Try port n every PORT_AGAIN seconds until the page has it."""
    while True:
        time.sleep(PORT_AGAIN)
        try:
            httpd = ThreadingHTTPServer(('127.0.0.1', n), Handler)
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                log('Port %d: %r' % (n, e))
            continue
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        watch.clear_port()
        log('Port %d is free again: the page is at http://localhost:%d' % (n, n))
        return httpd


def watch_words(a):
    """{'decided', 'restarts'} for the page and the menu bar: "Auto-switch last decided 12 minutes ago." while
    auto-switch is on and its loop has decided nothing for minutes past the pass it planned (watch.decided_late), and
    "Switchboard restarted 4 times in the last 10 minutes." (watch.restart_words); each None otherwise. The last of
    them said ends with the one thing to do (watch.with_todo). Never raises: the page shows without them."""
    out = {'decided': None, 'restarts': None}
    try:
        out['decided'] = watch.decided_words(watch.decided_late(api.auto_status(), a.get('enabled')))
    except Exception as e:  # noqa: BLE001
        log('when auto-switch last decided: %r' % e)
    try:
        out['restarts'] = watch.restart_words()
    except Exception as e:  # noqa: BLE001
        log("the page's starts: %r" % e)
    out['decided'], out['restarts'] = watch.with_todo(out['decided'], out['restarts'])
    return out


SPELLS = watch.Spells()     # the times Switchboard is not doing its job, said once for each spell (watch.troubles)


def watch_tick(now=None):
    """Each tick of the page's loop: what is wrong now (watch.troubles: auto-switch stopped, stuck or deciding nothing;
    the account in use blocked with no switch, or with a disk too full for one; a Keychain dialog unanswered; every
    number an hour old; Claude Code without a sign-in that Switchboard could not put back, from the put-back's own
    record, lost_signin), said once for each spell by a notification with the one thing to do, and "works again" for
    the first two and the last (watch.Spells). Returns the notes it sent."""
    if core.DEMO or not helper_settings()['notify']:
        return []
    from .accounts import autoloop
    a = engine.auto_settings()
    try:
        s = api.auto_status() or {}
    except Exception as e:  # noqa: BLE001 - the rest is still looked at
        log("the loop's heartbeat: %r" % e)
        s = {}
    st = load()
    now = time.time() if now is None else now
    lost, back = _safely("the put-back's record", lost_signin, now, default=(None, None))
    free = core.disk_free()
    found = watch.troubles(a, s, st, DECIDE['decision'], now, keychain_words=autoloop.KEYCHAIN_WORDS['dialog'],
                           lost=lost, disk_full=free is not None and free < core.DISK_FLOOR)
    sent = []
    for note in SPELLS.look(found, active=st.get('active'), back_at=back):
        try:
            notify.send(note, run=NOTIFY_RUN)
            sent.append(note)
        except Exception as e:  # noqa: BLE001 - one note that fails never stops the others
            log('notification failed: %r' % e)
    return sent


LOST_SLACK = 30.0           # seconds past the put-back's own wait before a lost sign-in counts as one it could not put
                            # back: its looks come every few seconds while the login is missing, and its writes take
                            # up to 3 s


def lost_signin(now=None):
    """(lost, back_at) from the put-back's own record (putback.load_record), for watch.troubles and watch.Spells.
    lost: {'account': the whole email Claude Code's settings name, 'since': when its login was first seen missing,
    'hold': True while the put-back's own wait for it (putback.wait_for, plus LOST_SLACK) is not over and its limit is
    not reached}, or None. The record holds a loss only while Claude Code's settings still name a kept account, so a
    sign-out the person chose is never in it, nor a switch part-way; while a sign-in from the page is under way it is
    None too. back_at: when a lost sign-in was last put back, or None. Reads two small files and a folder, never the
    Keychain."""
    from .accounts import context, putback, vault
    ctx = context.current()
    now = time.time() if now is None else now
    rec = putback.load_record(ctx)
    last = rec.get('last') or {}
    back = last.get('at') if isinstance(last.get('at'), (int, float)) else None
    missing = rec.get('missing')
    if not missing or putback._signing_in(ctx, now):
        return None, back
    aid, since = missing
    try:
        email = (vault.load(ctx)['accounts'].get(aid) or {}).get('email')
    except vault.VaultError:
        email = None
    done = [t for t in rec['done'] if 0 <= now - t < putback.PUT_BACK_SPAN_S]
    hold = len(done) < putback.PUT_BACK_MAX and now - since < putback.wait_for(rec['done'], now) + LOST_SLACK
    return {'account': email or aid, 'since': since, 'hold': hold}, back


BAR_KEEPER = []             # the page's watch on the menu bar (watch.MenuBarKeeper), made at its first look


def menubar_tick():
    """Each tick, when launchd runs the page: the menu bar started again through its own job when it has stopped
    without being quit (watch.MenuBarKeeper). Returns what it did."""
    if core.DEMO or not watch.background():
        return None
    from . import menubar
    if not BAR_KEEPER:
        BAR_KEEPER.append(watch.MenuBarKeeper(
            running=lambda: menubar.running_pid() is not None, job=menubar.our_job,
            start=lambda plist: watch.start_job(menubar.AGENT, plist, menubar.launchctl, menubar.domain())))
    return BAR_KEEPER[0].look()


def _note_event(text):
    with locked() as st:
        event(st, text, 'info')


# ---------- where the loop is: a slow pass is one line; a stage stuck for minutes is said once ----------

STAGE = {'name': None, 'since': None, 'thread': None, 'said': False, 'took': {}}
SLOW_PASS = 10.0            # seconds: a pass this slow is one line in the log, with each stage's time and the load
STUCK_STAGE = 120.0         # seconds: a stage this long is said once in the log, with where it waits


def stage(name):
    """The loop starts `name`; the one before it ends (its time is kept for the pass's line)."""
    now = monotonic()
    if STAGE['name'] and STAGE['since'] is not None:
        STAGE['took'][STAGE['name']] = STAGE['took'].get(STAGE['name'], 0.0) + now - STAGE['since']
    STAGE.update(name=name, since=now, said=False)


def stage_view(now=None):
    """{'stage', 'for'} while the loop has been in one stage (not waiting) for more than 5 s, else None. Said once in
    the log, with where it waits, past STUCK_STAGE seconds."""
    now = monotonic() if now is None else now
    name, since = STAGE['name'], STAGE['since']
    if not name or name == 'waiting' or since is None or now - since <= 5:
        return None
    if now - since >= STUCK_STAGE and not STAGE['said']:
        STAGE['said'] = True
        frame = sys._current_frames().get(STAGE['thread'])
        where = ''
        if frame is not None:
            where = ' at ' + ' < '.join('%s:%d %s' % (os.path.basename(f.filename), f.lineno, f.name)
                                        for f in reversed(traceback.extract_stack(frame)[-3:]))
        log("the page's loop has been %s for %d s%s" % (name, now - since, where))
    return {'stage': name, 'for': round(now - since, 1)}


def _pass_said(started):
    took = monotonic() - started
    if took >= SLOW_PASS:
        steps = ', '.join('%s %.1f s' % (k, v) for k, v in sorted(STAGE['took'].items(), key=lambda x: -x[1])
                          if v >= 0.1)
        load = os.getloadavg()[0] / (os.cpu_count() or 1)
        log('a slow pass: %.0f s (%s; load per core %.1f)' % (took, steps or 'no stage', load))
    STAGE['took'] = {}


CLOCK = {'wall': None, 'mono': None}
CLOCK_STEP = 120.0          # seconds the wall clock may move against the monotonic one between two passes


def clock_step(wall=None, mono=None):
    """Seconds the wall clock moved beyond the monotonic one since the last look: a sleep (the monotonic clock stops
    while the Mac sleeps), or the time set by hand or by the network. None the first time."""
    wall = time.time() if wall is None else wall
    mono = monotonic() if mono is None else mono
    before = (CLOCK['wall'], CLOCK['mono'])
    CLOCK.update(wall=wall, mono=mono)
    if before[0] is None:
        return None
    return (wall - before[0]) - (mono - before[1])


def loop_wait():
    """Seconds the page's loop waits before its next pass: MAC_TICK, less for a new login's second look (keep.py), and
    no longer than the pause of a loop that reads the accounts and ended (engine.AutoHost.due_in). When the host can't
    say, the usual wait."""
    wait = keep.wait_for(MAC_TICK)
    due = _safely('when the loop may start again', lambda: engine.HOST.due_in())
    return wait if due is None else max(0.5, min(wait, due))


def python_gone():
    """No Python could start the loop that reads the accounts (engine.AutoHost.no_python): under launchd the page
    ends, so launchd starts it again on the launcher's current Python. Never more than watch.RESTARTS_SAID times in
    watch.RESTARTS_WINDOW: then the page stays and says why (the host's problem line)."""
    if not getattr(engine.HOST, 'no_python', False) or not watch.background():
        return False
    if watch.starts_within(watch.RESTARTS_WINDOW) > watch.RESTARTS_SAID:
        return False
    log('no Python could start the loop that reads the accounts: ending the page so that launchd starts it on the '
        "launcher's Python")
    core.log_flush()
    raise SystemExit(watch.EXIT_PYTHON)


def _safely(what, fn, *args, **kw):
    """fn(*args) for the page's loop: what it returns, or `default` (None unless given) when it raises, said in the
    log as the loop's other steps are. One failing call never ends the loop."""
    default = kw.pop('default', None)
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - keep the page alive
        log('%s failed: %r' % (what, e))
        return default


def _started(what, target, **kw):
    """A daemon thread for the page's loop: True when it started; False, said in the log, when none could (a process
    out of threads), so the loop goes on and tries again next time."""
    try:
        threading.Thread(target=target, kwargs=kw, daemon=True).start()
        return True
    except Exception as e:  # noqa: BLE001
        log('%s failed: %r' % (what, e))
        return False


def loop(stop=None, ticks=None):
    """The regular tick: the limits every minute (sooner when asked), whether Claude Code is working every
    20 seconds, and a reading the loop that reads the accounts saved at every pass (readings_pass). A new login in
    Claude Code is looked at at once, and again a few seconds later, so it is kept as you sign in (keep.py); and the
    accounts are read again when the engine's accounts changed after the rows were read (wake_if_behind), so a login
    that was kept shows on its row within a pass. `stop` and `ticks` (how many passes) are for tests."""
    last, n = None, 0
    for k in ('stamp', 'said', 'same', 'woke'):         # a loop that starts has read nothing yet, and nothing woke it
        ROWS.pop(k, None)
    STAGE['thread'] = threading.get_ident()
    _safely('the clock', clock_step)
    while not (stop and stop.is_set()) and (ticks is None or n < ticks):
        n += 1
        started = monotonic()
        step = _safely('the clock', clock_step)
        if step is not None and abs(step) > CLOCK_STEP:     # one fresh reading, never a burst of catch-ups
            log('the clock moved %+d min against the monotonic clock (the Mac slept, or the time was set): reading '
                'again' % round(step / 60.0))
            WAKE.set()
        stage('looking at the login')
        try:
            if keep.login_changed():            # /login in Claude Code: look now, not in a minute
                WAKE.set()
        except Exception as e:  # noqa: BLE001
            log('looking at the login failed: %r' % e)
        stage('taking in new readings')
        try:
            readings_pass()                     # what the loop that reads the accounts saved, whatever the load
        except Exception as e:  # noqa: BLE001
            log('taking in new readings failed: %r' % e)
        # Monotonic: a clock set back an hour must not stop the readings for an hour. Slower under load.
        every = _safely('easing under load', ease_tick, default=TICK)
        wake_if_behind(every)                   # a login kept by the other process since the rows were read
        if WAKE.is_set() or last is None or not 0 <= monotonic() - last < every or \
                _safely('a new login', keep.due, default=False):
            WAKE.clear()
            last = monotonic()
            looked = None
            stage('reading the accounts')
            try:
                looked = tick()
            except Exception as e:  # noqa: BLE001 - keep the page alive
                log('check failed: %r' % e)
            finally:
                SNAPSHOT.invalidate()
            stage('keeping logins')
            _safely('keeping logins', keep_tick, looked)
            wake_if_behind(every)               # a login this pass kept: the next pass can start now and read them again
            stage('looking after links, logs and the disk')
            for step in (refresh_link_status, consent_refresh, core.log_flush, core.rotate_own_output):
                try:                            # each on its own: one failing never stops the others
                    step()
                except Exception as e:  # noqa: BLE001
                    log('%s failed: %r' % (getattr(step, '__name__', step), e))
            try:
                disk_tick(NOTIFY_RUN if helper_settings()['notify'] and not core.DEMO else None)
                if helper_settings()['notify'] and not core.DEMO:
                    notify.flush_held(run=NOTIFY_RUN)       # the notes held back by the rate limit, as one
            except Exception as e:  # noqa: BLE001
                log('the disk or held notes: %r' % e)
            stage('starting or stopping the loop that reads the accounts')
            try:                                # it runs while an account is kept, auto-switch on or off, and
                engine.HOST.ensure()            # switches only while auto-switch is on
            except Exception as e:  # noqa: BLE001
                log('the loop that reads the accounts: %r' % e)
            _safely("the loop's Python", python_gone)
            stage('asking automatic switching what it waits for')
            try:
                decide_tick()                   # api.decide() here, never for a request (the page's one line)
            except Exception as e:  # noqa: BLE001
                log('automatic switching: %r' % e)
            stage('telling you when Switchboard is not doing its job')
            _safely('the notes when Switchboard is not doing its job', watch_tick)
            _safely('starting the menu bar again', menubar_tick)
            if not core.DEMO and not claudecode.CAP['running'] and time.time() - claudecode.CAP['at'] >= claudecode.CAP_EVERY:
                claudecode.CAP['running'] = True        # before it starts: the thread clears it when it ends
                if not _started('the cap look', claudecode.cap_tick, say=_note_event):
                    claudecode.CAP['running'] = False
            if not core.DEMO and not claudecode.HEALTH['running'] and time.time() - claudecode.HEALTH['at'] >= claudecode.HEALTH_EVERY:
                claudecode.HEALTH['running'] = True         # claude-mem's worker, while its switch is on
                if not _started("claude-mem's look", claudecode.health_tick, say=_note_event):
                    claudecode.HEALTH['running'] = False
            if not core.DEMO:                   # a look for a newer version: only when turned on, once a day at most
                _safely('looking for a newer version', newer.tick)
        stage('starting the loop that reads the accounts again')
        _safely('starting the loop again', lambda: engine.HOST.revive())   # one that ended: in seconds, not a tick
        stage('checking what Switchboard keeps')
        _safely('the self-check', selfcheck_tick)        # every ten minutes, on a thread of its own (selfcheck.py)
        stage('taking in what Claude Code just published')
        try:
            artifacts.published_tick()          # a page made seconds ago opens as its owner; its picture follows
        except Exception as e:  # noqa: BLE001
            log('taking in new pages failed: %r' % e)
        stage('looking at the chats')
        try:
            chats_tick()                        # a look at every chat, on its own thread, when one is due
        except Exception as e:  # noqa: BLE001
            log('looking at the chats: %r' % e)
        stage('looking at the other assistants')
        try:
            providers_tick()                    # the board of assistants, on its own thread, when one is due
        except Exception as e:  # noqa: BLE001
            log('looking at the other assistants: %r' % e)
        stage('looking at whether Claude Code works')
        try:
            with MAC_LOCK:
                mac_tick()
        except Exception as e:  # noqa: BLE001
            log('awake check failed: %r' % e)
        stage('waiting')
        _safely('the pass', _pass_said, started)
        if ticks is None or n < ticks:
            WAKE.wait(_safely('the wait', loop_wait, default=MAC_TICK))
