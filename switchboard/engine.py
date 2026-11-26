"""The app's side of Switchboard's account engine (switchboard/accounts, reached only through accounts.api).

Everything about Claude Code's logins, switching, readings and automatic switching is the engine's. This module
turns its answers into what the page, the menu bar and the terminal draw, and its refusals into one plain line, and
runs the engine's loop as a child of the page (AutoHost): it reads every account whether auto-switch is on or off,
and switches only while it is on. It never reads or writes a login itself.

Rows (one per account, in the person's order), our own shape:
    id, number (1-based place in the order), email, org_id, org_name, plan, alias, status (the engine's: ok,
    needs_login, expiring, unreadable, claimed, conflict), active, unmanaged (Claude Code's login that is not kept yet),
    usage ({'session', 'weekly', 'scoped', 'spend'} as the page draws it, or None), usage_at (its oldest number's
    time), checked_at (when the account was last answered for: what "checked 3 min ago" says), source
    ('status_line', 'endpoint' or 'none'), frozen, usage_error, models_seen, emptied (Claude Code emptied this
    account's live login after Anthropic refused it: shown in use, needing a sign-in)
"""
import math
import os
import plistlib
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from . import core
from .accounts import api
from .core import SwitchError, claude_json, data_path, home, log, read_json

# ---------- automatic switching's choices, as the page offers them ----------

STRATEGIES = ('refills-first', 'most-room', 'stay-then-refills')    # what refills first (the default), the most
# room, or stay on the account in use until a line and then go to what refills first
DEFAULT_STRATEGY = 'refills-first'
RULES = ('in', 'last', 'out')       # an account in auto-switch: used as usual, used last, kept out of auto-switch
DEFAULT_SESSION, DEFAULT_WEEKLY, DEFAULT_MODEL_LINE = 90.0, 95.0, 95.0
LINE_MIN, LINE_MAX = 50.0, 99.9
MODEL_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$')
GUARD = "Another program is also switching Claude Code's login (%s). Turn it off first."
RULE_WORDS = 'Pick in, last or out for an account.'
OWN_KEYS = ('session_at', 'weekly_at')          # the lines an account may have its own of
RANK_MIN, RANK_MAX = 1, 99
RANK_WORDS = 'Pick a whole number from %d to %d.' % (RANK_MIN, RANK_MAX)


class Refused(SwitchError):
    """The engine said no, in one plain line. `kind` names the one right action for the page: 'retry' (busy or slow:
    nothing changed), 'unlock' (the Keychain is locked), 'signin' (that account needs a fresh sign-in), 'guard'
    (another program switches too), or None."""

    def __init__(self, text, kind=None, detail=None):
        SwitchError.__init__(self, text)
        self.kind, self.detail = kind, detail


FIX = {'keychain_locked': 'unlock', 'keychain_slow': 'retry', 'keychain_denied': None, 'claude_busy': 'retry',
       'dead_login': 'signin', 'network': 'retry', 'rate_limited': 'retry', 'unreadable_live': 'retry',
       'claimed': None, 'disk': None}


def refused(e, fallback='That did not work.'):
    """An engine refusal (AccountsError) as a Refused: its own sentence, which says what to do next."""
    text = str(e).strip() or fallback
    kind = getattr(e, 'kind', 'other')
    return Refused(text, FIX.get(kind), kind)


def _call(fn, *args, **kw):
    """One engine call. An AccountsError becomes a Refused; the engine not being built yet (a development skeleton) is
    said plainly, never as a traceback on the page."""
    try:
        return fn(*args, **kw)
    except api.AccountsError as e:
        raise refused(e)
    except NotImplementedError:
        raise Refused("Switchboard's account engine is not ready in this copy. Install it again.", None, 'todo')


# ---------- Claude Code's own identity block (its config file): who it says it is ----------

def identity_block():
    """Claude Code's identity block in its global config (~/.claude.json): emailAddress, organizationUuid,
    accountUuid, ... A label, not proof: the engine proves whose a login is."""
    block = read_json(claude_json()).get('oauthAccount')
    return block if isinstance(block, dict) else {}


def identity():
    """(email, organization) Claude Code's config names, or None."""
    b = identity_block()
    email = (b.get('emailAddress') or '').strip().lower()
    return (email, b.get('organizationUuid') or '') if email else None


def signed_in_now():
    """The account Claude Code's config names, as one row (not kept: unmanaged), or []."""
    b = identity_block()
    email = (b.get('emailAddress') or '').strip().lower()
    if not email:
        return []
    return [_row(id=b.get('accountUuid') or None, number=None, email=email, org_id=b.get('organizationUuid') or None,
                 org_name=b.get('organizationName') or None, plan=None, alias=None, status='ok', active=True,
                 unmanaged=True)]


# ---------- readings, in the page's shape ----------

def _iso(t):
    try:
        return datetime.fromtimestamp(float(t), timezone.utc).isoformat() if _finite(t) is not None else None
    except (OverflowError, OSError, ValueError):
        return None


def _finite(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def usage_of(reading):
    """A Reading's windows as the page draws them: {'session', 'weekly', 'scoped': [{name, pct, resets}], 'spend'},
    or None when it has none. A percent the engine does not know stays None (never 0)."""
    if reading is None:
        return None
    out = {'session': None, 'weekly': None, 'scoped': [], 'spend': None}
    for w in getattr(reading, 'windows', ()) or ():
        name = getattr(w, 'name', '')
        pct = _finite(getattr(w, 'pct', None))
        win = {'pct': None if pct is None else min(100.0, max(0.0, pct)), 'resets': _iso(getattr(w, 'resets_at', None)),
               'pace': None, 'lasts': None}
        if name == '5-hour':
            out['session'] = win
        elif name == 'weekly':
            out['weekly'] = win
        elif isinstance(name, str) and name.startswith('model:') and name[6:].strip():
            out['scoped'].append({'name': name[6:].strip(), 'pct': win['pct'], 'resets': win['resets']})
    if not (out['session'] or out['weekly'] or out['scoped']):
        return None
    return out


def reading_fields(reading):
    """What a row takes from an account's reading (api.Reading, or None): usage, usage_at (its oldest number's time,
    which every estimate goes by), checked_at (when the account was last answered for; at least usage_at), source,
    frozen, usage_error, models_seen. The listing's rows and the page's look between its ticks (server.fresh_readings)
    take the same."""
    r = reading
    return {'usage': usage_of(r), 'usage_at': _finite(getattr(r, 'read_at', None)) if r else None,
            'checked_at': _finite(getattr(r, 'checked_at', None)) if r else None,
            'source': getattr(r, 'source', 'none') if r else 'none',
            'frozen': bool(getattr(r, 'frozen', False)) if r else False,
            'usage_error': getattr(r, 'error', None) if r else None, 'models_seen': models_of(r),
            'elsewhere': bool(getattr(r, 'elsewhere', False)) if r else False}


def models_of(reading):
    """The per-model weeks a reading reports, by name (even at 0%): ['Fable']."""
    names = []
    for w in getattr(reading, 'windows', ()) or ():
        name = getattr(w, 'name', '')
        if isinstance(name, str) and name.startswith('model:') and name[6:].strip() not in names and name[6:].strip():
            names.append(name[6:].strip())
    return names


def needs_sign_in(row):
    """Does this account need a fresh sign-in before Claude Code can be moved to it? Its login was refused or lapsed,
    or (not while it is the one in use) Anthropic said the login kept for it is another account's."""
    return row.get('status') == 'needs_login' or (row.get('status') == 'conflict' and not row.get('active'))


def note_of(row):
    """One plain line under an account when its numbers are not current, or when it can't be switched to."""
    if row.get('emptied'):
        return ("Anthropic refused Claude Code's login for this account, and Claude Code emptied it. Sign in to it "
                "again, or switch to another account.")
    if row.get('status') == 'conflict':
        return ("Anthropic says the login kept for this account belongs to another one, so Switchboard won't switch "
                "to it. Sign in to it again to keep its own.")
    if row.get('frozen'):
        return 'Anthropic is holding back new numbers for a while: the real ones can only be higher.'
    if row.get('usage_error'):
        return '%s These are the last numbers.' % _sentence(row['usage_error'])
    if row.get('status') == 'unreadable':
        return "macOS's Keychain did not answer for this account just now. It is tried again by itself."
    return None


def _sentence(text):
    text = str(text).strip()
    return text if text.endswith(('.', '!', '?')) else text + '.'


# ---------- reading the accounts ----------

def _row(**kw):
    row = {'id': None, 'number': None, 'email': '', 'org_id': None, 'org_name': None, 'plan': None, 'tier': None,
           'org_type': None, 'alias': None,
           'status': 'ok', 'active': False, 'unmanaged': False, 'usage': None, 'usage_at': None, 'checked_at': None,
           'source': 'none',
           'frozen': False, 'usage_error': None, 'models_seen': [], 'refresh_expires_at': None, 'emptied': False,
           'elsewhere': False}
    row.update(kw)
    return row


def listing():
    """{'rows': every account the engine keeps, in order, and Claude Code's login when it is not kept yet;
    'live': the engine's Live (None when it could not be asked); 'disagree': Claude Code's config names one account
    and its login is another's}. Reads metadata and the readings store, and at most one Keychain read (api.live).
    Raises Refused when the engine can't be asked at all."""
    saved = _call(api.accounts)
    try:
        live = api.live()
    except (api.AccountsError, NotImplementedError) as e:
        log('reading the live login: %r' % e)
        live = None
    try:
        got = api.readings() or {}
    except (api.AccountsError, NotImplementedError) as e:
        log('reading the readings: %r' % e)
        got = {}
    known = live is not None and live.state == api.PRESENT
    live_id = live.account_id if known else None
    wiped = live.account_id if live is not None and live.state == 'wiped' else None     # emptied after a refusal
    label = identity()                  # when the login itself can't be read, the config's label marks the one in use
    rows = []
    for i, a in enumerate(saved):
        r = got.get(a.id)
        email = (a.email or '').lower()
        if known:
            active = bool(live_id) and a.id == live_id
        elif wiped:
            active = a.id == wiped
        else:
            active = live is None or live.state == api.UNREADABLE
            active = active and label is not None and (email, a.org_id or '') == label
        rows.append(_row(id=a.id, number=i + 1, email=email, org_id=a.org_id, org_name=a.org_name,
                         plan=a.plan, tier=a.tier, org_type=a.org_type, alias=a.alias,
                         status='needs_login' if wiped and active else a.status,
                         active=active, emptied=bool(wiped and active), refresh_expires_at=a.refresh_expires_at,
                         **reading_fields(r)))
    if known and live_id and not any(r['active'] for r in rows):
        b = identity_block()
        email = (live.email or b.get('emailAddress') or '').strip().lower()
        if email:
            rows.append(_row(id=live_id, number=None, email=email, org_id=b.get('organizationUuid') or None,
                             org_name=b.get('organizationName') or None, active=True, unmanaged=True))
    elif not known and not any(r['active'] for r in rows) and (live is None or live.state == api.UNREADABLE):
        rows += signed_in_now()         # its login can't be read: the config's label, shown as not kept yet
    return {'rows': rows, 'live': live, 'disagree': bool(getattr(live, 'disagree', False))}


def rows():
    return listing()['rows']


def held(rows_):
    """The accounts the engine keeps (not Claude Code's login waiting to be kept)."""
    return [r for r in rows_ or [] if not r.get('unmanaged')]


def find(target):
    return _call(api.find, str(target))


def saved_identities():
    """(email, organization) of every account the engine keeps: what "held" means to keep.py and the history."""
    try:
        return set(((a.email or '').lower(), a.org_id or '') for a in api.accounts())
    except (api.AccountsError, NotImplementedError) as e:
        log('reading the accounts: %r' % e)
        return set()


# ---------- changing things: each one is the engine's own ----------

def switch(target, source='page', kind='manual', reason=''):
    """Move Claude Code to an account (id, email, alias or number). Returns the SwitchResult. A refusal, or a switch
    the engine had to undo, is a Refused: Claude Code is then where it was."""
    r = _call(api.switch, str(target), kind=kind, source=source, reason=reason)
    if getattr(r, 'rolled_back', False) or not getattr(r, 'ok', True):
        raise Refused(r.note or r.reason or 'The switch did not hold, so Claude Code stays where it was.', 'retry')
    return r


def add_current():
    """Keep the login Claude Code is on (or bring its saved copy up to date). Returns the Account."""
    return _call(api.add_current)


def add_login(config_dir):
    """Keep the login a throwaway config folder holds (the page's Add signs in there, so Claude Code's live login is
    never touched). The engine reads the folder's own item (api.add_login); no secret passes through here."""
    fn = getattr(api, 'add_login', None)
    try:
        return _call(fn, config_dir) if fn is not None else _not_ready()
    except Refused as e:
        if e.detail == 'todo':
            _not_ready()
        raise


def remove_sign_in(config_dir):
    """Remove the Keychain item a throwaway sign-in folder's Claude Code left (api.remove_sign_in). True when it is gone;
    False when it could not be removed now (the folder's sweep at the next start tries again). Never raises."""
    fn = getattr(api, 'remove_sign_in', None)
    if fn is None:
        return False
    try:
        return bool(fn(config_dir))
    except (api.AccountsError, NotImplementedError) as e:
        log('removing a sign-in folder\'s Keychain item: %r' % e)
        return False


def _not_ready():
    raise Refused('Adding an account from the page is not ready in this copy. In Claude Code, run /login as that '
                  'account: Switchboard keeps it by itself.')


def remove(target, everything=False):
    """Forget a saved account (api.remove). everything: the uninstaller's --all is removing every account, after
    which nothing switches, so an account goes even while Claude Code is on a one-year token kept for it."""
    if everything:
        _call(api.remove, str(target), everything=True)
    else:
        _call(api.remove, str(target))


def remove_all():
    """For the uninstaller's --all, once each account was removed: every login of Switchboard's left in the Keychain
    (api.remove_all). One plain line per kind of thing removed."""
    return _call(api.remove_all) or []


def set_alias(target, alias):
    _call(api.set_alias, str(target), alias or None)


def reorder(ids):
    _call(api.reorder, list(ids))


def run_as(target, argv, **kw):
    return _call(api.run_as, str(target), list(argv), **kw)


def accounts():
    """Every account the engine keeps (api.Account: id, email, org_id, ...), in the person's order."""
    return _call(api.accounts) or []


def best():
    """The account auto-switch would land on now, for a move a person asks for (api.best): a Decision, 'switch' with
    its target, or 'stay' with why there is none."""
    return _call(api.best)


def live_now():
    """Claude Code's live login as the engine sees it (api.Live), or None when it can't be asked."""
    try:
        return api.live()
    except (api.AccountsError, NotImplementedError) as e:
        log('reading the live login: %r' % e)
        return None


# ---------- the engine's own health check ----------

HEALTH_WORDS = {'keychain': "macOS's Keychain", 'facts': "Claude Code's conventions", 'journal': 'A cut-off switch',
                'store': "The accounts' file", 'claims': 'Accounts a terminal is using'}


def health():
    """The engine's own check (api.health): its Keychain state, Claude Code's conventions, a cut-off switch, the
    store's version, accounts claimed by a terminal."""
    return _call(api.health) or {}


def _health_known(key, v, said):
    """(good, line) for a part of the engine's health check in the shape roster.health() gives it, or None for a shape
    this does not know (then health_items says it the general way). `said`: the facts check's own warnings, said on its
    line, so the warnings line does not say them again. good None: worth knowing, nothing to fix."""
    n = lambda count, one, many: '%d %s' % (count, one if count == 1 else many)    # noqa: E731
    if key == 'claude_code' and isinstance(v, dict) and 'checked' in v:
        checked, here = v.get('checked'), v.get('installed')
        if v.get('ok') is True:
            return True, "Claude Code's conventions: checked against %s, the version here" % checked
        if here:
            return None, "Claude Code's conventions: checked against %s; this Mac has %s, newer" % (checked, here)
        return None, ("Claude Code's conventions: checked against %s; the version on this Mac is not known"
                      % checked)
    if key == 'keychain' and isinstance(v, dict) and ('searchable' in v or 'counts' in v):
        why = v.get('resting_all')
        needs_you = ('locked', 'no_tool', 'dialog')     # a NO only for what needs the person: unlock the Mac, answer
        if why:                                         # macOS's dialog, or the tool itself is gone
            return (False if why in needs_you else None), "macOS's Keychain: %s" % {
                'locked': 'locked (unlock the Mac)', 'no_tool': 'its tool, /usr/bin/security, is missing',
                'dialog': 'waiting for you to answer its dialog',
                'timeout': 'slow to answer just now', 'busy': 'busy just now',
                'no_keychain': 'not reachable from here (a terminal on the Mac itself reaches it)'}.get(why, why)
        if v.get('waiting_on_dialog'):          # a call still waits on the dialog after the rest is over
            return False, "macOS's Keychain: waiting for you to answer its dialog"
        waiting = v.get('needs_approval_or_slow') or []
        if waiting:
            return None, "macOS's Keychain: waiting on %s (a dialog to answer, or a slow answer)" % n(
                len(waiting), 'item', 'items')
        if v.get('searchable') is True:
            return True, "macOS's Keychain: answering"
        return None, "macOS's Keychain: not asked yet"
    if key == 'keychain_argv' and isinstance(v, dict) and isinstance(v.get('count'), int):
        return None, ("Logins written to the Keychain in its command line: %d, each too long for its standard input "
                      "(Claude Code does the same). Other users of this Mac could see one while it was written"
                      % v['count'])
    if key == 'store' and isinstance(v, dict) and 'readable' in v:
        if not v.get('readable'):
            return False, "The accounts' file: can't be read (its last good copies are beside it)"
        return True, "The accounts' file: %s, %s" % (n(v.get('accounts') or 0, 'account', 'accounts'),
                                                     n(v.get('backups') or 0, 'backup', 'backups'))
    if key == 'journal' and isinstance(v, dict):
        if v.get('damaged'):
            return False, "A cut-off switch: its record can't be read"
        return None, 'A cut-off switch: waiting to be settled (Switchboard settles it at its next start or switch)'
    if key == 'claims' and isinstance(v, list) and all(isinstance(c, dict) for c in v):
        return (True, 'Accounts a terminal is using: none') if not v else (
            None, 'Accounts a terminal is using: %d (each while its `switchboard run` lasts)' % len(v))
    if key == 'refreshing' and isinstance(v, dict):
        return (True, 'Logins being renewed: none') if not v else (None, 'Logins being renewed: %d' % len(v))
    if key == 'recovery' and isinstance(v, list):
        return (True, 'Recovery copies: none') if not v else (
            None, 'Recovery copies: %s kept nowhere else yet' % n(len(v), 'login', 'logins'))
    if key == 'warnings' and isinstance(v, list):
        rest = [w for w in v if isinstance(w, str) and w not in said]
        return (True, 'Warnings: none') if not rest else (None, 'Warnings: %s' % ' '.join(rest))
    return None


def health_items(h):
    """[(good: True, False or None for not known, one plain line)] for each thing the health check names."""
    out = []
    h = h or {}
    said = set((h.get('claude_code') or {}).get('warnings') or []) if isinstance(h.get('claude_code'), dict) else set()
    for key, v in sorted(h.items(), key=lambda kv: str(kv[0])):
        known = _health_known(key, v, said)
        if known is not None:
            out.append(known)
            continue
        label = HEALTH_WORDS.get(key, str(key).replace('_', ' ').capitalize())
        if isinstance(v, dict):
            good = v.get('ok') if isinstance(v.get('ok'), bool) else None
            text = v.get('line') or v.get('message') or v.get('state') or ('ok' if good else 'not known')
            out.append((good, '%s: %s' % (label, text) if not str(text).startswith(label) else str(text)))
        elif isinstance(v, bool):
            out.append((v, '%s: %s' % (label, 'ok' if v else 'needs a look')))
        elif isinstance(v, str):
            out.append((v.lower() in ('ok', 'unlocked', 'none', 'clear'), '%s: %s' % (label, v)))
        elif v is None:
            out.append((True if key == 'journal' else None, '%s: none' % label))
        elif isinstance(v, (list, tuple)):
            out.append((True, '%s: %s' % (label, ', '.join(str(x) for x in v) if v else 'none')))
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append((True, '%s: %g' % (label, v)))
    return out


def health_lines(h):
    """Only what the health check says is wrong, one plain line each."""
    return [text for good, text in health_items(h) if good is False]


# ---------- another program that switches Claude Code's login too ----------

OUR_LABELS = ('io.github.amyjainberkeley.switchboard',)
SWITCH_WORDS = ('switch', 'swap', 'rotate', 'account')


def foreign_switcher(folder=None):
    """The login item (launch agent label) of another program that switches Claude Code's login, or None. Known by
    what it does: a job of the person's that names Claude and switching, swapping or accounts. Ours never counts."""
    folder = folder or os.path.join(home(), 'Library', 'LaunchAgents')
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None
    for name in names:
        if not name.endswith('.plist'):
            continue
        try:
            with open(os.path.join(folder, name), 'rb') as f:
                job = plistlib.load(f)
        except Exception:  # noqa: BLE001 - a plist that can't be read is not one we can name
            continue
        if not isinstance(job, dict):
            continue
        label = job.get('Label') if isinstance(job.get('Label'), str) else name[:-len('.plist')]
        if label.startswith(OUR_LABELS) or job.get('Disabled') is True:
            continue
        args = job.get('ProgramArguments') if isinstance(job.get('ProgramArguments'), list) else []
        words = ' '.join([label] + [a for a in args if isinstance(a, str)] +
                         ([job['Program']] if isinstance(job.get('Program'), str) else [])).lower()
        if 'claude' in words and any(w in words for w in SWITCH_WORDS) and 'switchboard' not in words:
            return label
    return None


# ---------- automatic switching ----------

def _num(v, default):
    v = _finite(v)
    return default if v is None else v


def auto_settings():
    """What automatic switching runs on and whether it runs, for the page, the menu bar and the terminal:
    {'available', 'enabled', 'running', 'on', 'starting', 'problem', 'stage', 'waiting_for', 'restarted',
     'offline_since' (the loop found no network since then), 'settling' (the Mac just woke), 'stuck_for' (seconds its
     heartbeat is overdue), 'session_at', 'weekly_at', 'model_at', 'weekly_set', 'strategy', 'model', 'models',
     'rules' ({account id: 'last' or 'out'}, no entry for an account used as usual), 'own_lines' ({account id:
     {'session_at', 'weekly_at'}}, each only when the account has its own), 'ranks' ({account id: 1 to 99}), 'service',
     'reading', 'reading_wanted', 'reading_problem', 'asked'}.

    One loop reads the accounts and, while auto-switch is on, switches (loop_wanted). `on`: auto-switch is on and that
    loop is healthy; `problem` and `restarted` speak of automatic switching while it is on. `reading`: the loop runs
    and beats, so the accounts are being read, on or off; `reading_wanted`: it should (auto-switch on, or an account
    kept); `reading_problem`, only while auto-switch is off: why the accounts are not being read (stuck, stopped), in
    words of reading, and then `restarted` speaks of reading too. `asked`: the last read someone asked for that the
    loop answered ({'at' (request_read's time), 'done', 'read', 'ok', 'held'}), or None."""
    try:
        a = api.auto_settings() or {}
        available = True
    except (api.AccountsError, NotImplementedError) as e:
        log('reading automatic switching: %r' % e)
        a, available = {}, False
    enabled = bool(a.get('enabled')) and available
    wanted = loop_wanted(enabled if available else None)
    if wanted is None:                                  # the engine did not answer: the loop stays as it is
        wanted = HOST.running()
    host = HOST.status(wanted=wanted)
    model = a.get('model') if isinstance(a.get('model'), str) and a.get('model').strip() else None
    strategy = a.get('strategy') if a.get('strategy') in STRATEGIES else DEFAULT_STRATEGY
    rules = a.get('rules') if isinstance(a.get('rules'), dict) else {}
    return {'available': available, 'enabled': enabled, 'service': host['running'], 'running': host['running'],
            'on': host['on'] and enabled, 'starting': host['starting'], 'problem': host['problem'] if enabled else None,
            'stage': host['stage'], 'waiting_for': host['waiting_for'],
            'restarted': host['restarted'] if enabled else host.get('reading_restarted'),
            'offline_since': host.get('offline_since'), 'settling': bool(host.get('settling')),
            'stuck_for': host.get('stuck_for'),
            'reading': bool(host['on']), 'reading_wanted': bool(wanted),
            'reading_problem': host.get('reading_problem') if wanted and not enabled else None,
            'asked': host.get('asked'),
            'session_at': _num(a.get('session_at'), DEFAULT_SESSION), 'weekly_at': _num(a.get('weekly_at'), DEFAULT_WEEKLY),
            'model_at': _num(a.get('model_at'), DEFAULT_MODEL_LINE), 'weekly_set': True,
            'strategy': strategy, 'model': model, 'models': counted_models(a, model),
            'rules': dict((k, v) for k, v in rules.items() if isinstance(k, str) and v in RULES[1:]),
            'own_lines': own_lines_of(a), 'ranks': ranks_of(a)}


def counted_models(a, model):
    """Every model whose week counts: the engine's 'models' when it gives several, else the one in 'model'."""
    more = a.get('models') if isinstance(a.get('models'), list) else []
    more = [m.strip() for m in more if isinstance(m, str) and m.strip()]
    return more if len(more) > 1 else [model] if model else []


def own_lines_of(a):
    """{account id: {'session_at' and/or 'weekly_at': line}} from the engine's settings: only the lines that are
    numbers in range."""
    d = a.get('own_lines') if isinstance(a.get('own_lines'), dict) else {}
    out = {}
    for k, v in d.items():
        lines = dict((key, float(v[key])) for key in OWN_KEYS if isinstance(v, dict) and _finite(v.get(key)) is not None
                     and LINE_MIN <= v[key] <= LINE_MAX)
        if isinstance(k, str) and lines:
            out[k] = lines
    return out


def ranks_of(a):
    """{account id: rank} from the engine's settings: only whole numbers from 1 to 99."""
    d = a.get('ranks') if isinstance(a.get('ranks'), dict) else {}
    return dict((k, v) for k, v in d.items() if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
                and RANK_MIN <= v <= RANK_MAX)


def switching_on():
    """The auto-switch setting: True, False, or None when the engine did not answer."""
    try:
        return bool((api.auto_settings() or {}).get('enabled'))
    except (api.AccountsError, NotImplementedError):
        return None


def loop_wanted(enabled):
    """Whether the engine's loop should run: while auto-switch is on (`enabled`, the setting as switching_on gives
    it), or while the engine keeps at least one account, since the loop is what reads the accounts: auto-switch off
    stops switching, never reading (the policy answers "stay" while it is off). Never in the demo. None when it can't
    be told (the setting not known and the accounts not readable just now): the caller leaves the loop as it is, so a
    look that fails never stops it."""
    if core.DEMO:
        return False
    if enabled:
        return True
    try:
        return bool(api.accounts())
    except (api.AccountsError, NotImplementedError):
        return None


def check_line(value):
    """A switch line as the person typed it: a finite number from 50 to 99.9, never clamped."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or not LINE_MIN <= value <= LINE_MAX:
        raise SwitchError('Pick a number from %g to %g.' % (LINE_MIN, LINE_MAX))
    return float(value)


def set_auto(enabled=None, session_at=None, weekly_at=None, model_at=None, model=None, strategy=None, rule=None,
             line=None, rank=None):
    """Change automatic switching (every value checked before anything is set), then let the loop's host match it:
    the loop starts when it was not running (it is wanted while auto-switch is on, or while an account is kept), and
    a loop that runs already is left as it is, since it reads the setting again at each pass; a request for a read
    (request_read) starts that pass now. model '' counts no model's week; names with commas between them, or a list,
    count several. rule: (account, 'in' | 'last' | 'out'), the account named as everywhere (number, email, alias or
    id). line: (account, {'session_at': N, 'weekly_at': N}), that account's own lines (a key with None, or None in
    place of the dict, puts it back on the shared line). rank: (account, 1 to 99 or None). Turning it on is refused
    while another program switches Claude Code's login."""
    session_at, weekly_at, model_at = check_line(session_at), check_line(weekly_at), check_line(model_at)
    if strategy is not None and strategy not in STRATEGIES:
        raise SwitchError('Unknown order.')
    if model is not None:
        model = check_models(model)
    if rule is not None:
        if not isinstance(rule, (tuple, list)) or len(rule) != 2 or rule[1] not in RULES:
            raise SwitchError(RULE_WORDS)
        rule = (find(rule[0]).id, rule[1])
    if line is not None:
        line = check_own(line)
        line = (find(line[0]).id, line[1])
    if rank is not None:
        rank = check_rank(rank)
        rank = (find(rank[0]).id, rank[1])
    if enabled:
        other = foreign_switcher()
        if other:
            raise Refused(GUARD % other, 'guard')
    more = dict((k, v) for k, v in (('rule', rule), ('line', line), ('rank', rank)) if v is not None)
    got = _call(api.set_auto, enabled=None if enabled is None else bool(enabled), session_at=session_at,
                weekly_at=weekly_at, model_at=model_at, model=model,
                strategy=strategy, **more)
    if enabled is not None:
        HOST.ensure()
    request_read(all_accounts=False)    # the running loop takes the new setting now, not at its next pass
    return got


def request_read(all_accounts=True):
    """Ask the loop that reads the accounts to read now (api.request_read): the account in use, and with all_accounts
    every account. Returns the request's time, or None in the demo or when it could not be written (said in the log;
    the loop then reads at its own pace)."""
    if core.DEMO:
        return None
    try:
        return api.request_read(all_accounts=bool(all_accounts))
    except (OSError, ValueError, NotImplementedError) as e:
        log('asking for a read now: %r' % e)
        return None


def model_names(value):
    """The model weeks a setting counts: model display names, with commas between them, or none."""
    names = [n.strip() for n in value.split(',')] if isinstance(value, str) else []
    return [n for n in names if n and n.lower() not in ('none', 'all')]


def check_models(value):
    """The model setting as given (a name, names with commas between them, or a list of names), checked: '' for none,
    one name as itself, several as a list. Refuses anything that is not a model name."""
    names = value.split(',') if isinstance(value, str) else value if isinstance(value, list) else None
    if names is None or not all(isinstance(n, str) for n in names):
        raise SwitchError('That is not a model name.')
    names = [n.strip() for n in names if n.strip()]
    if any(not MODEL_RE.match(n) for n in names):
        raise SwitchError('That is not a model name.')
    return '' if not names else names[0] if len(names) == 1 else names


def check_own(line):
    """(account, {'session_at': N or None, 'weekly_at': N or None} or None), as given: each line checked as the shared
    ones are (check_line), never clamped."""
    if not isinstance(line, (tuple, list)) or len(line) != 2 or not (
            line[1] is None or (isinstance(line[1], dict) and line[1] and set(line[1]) <= set(OWN_KEYS))):
        raise SwitchError('Give an account its own 5-hour or weekly line.')
    return (line[0], None if line[1] is None else dict((k, check_line(v)) for k, v in line[1].items()))


def check_rank(rank):
    """(account, a whole number from 1 to 99, or None to take the rank away), as given."""
    n = rank[1] if isinstance(rank, (tuple, list)) and len(rank) == 2 else 0
    if n is not None and (isinstance(n, bool) or not isinstance(n, int) or not RANK_MIN <= n <= RANK_MAX):
        raise SwitchError(RANK_WORDS)
    return (rank[0], n)


# ---------- the loop's host: runs it, watches its heartbeat, restarts it when it is stuck ----------

LOOP_MODULE = 'switchboard.accounts.autoloop'
RUNPY = ('import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); '
         'runpy.run_module(%r, run_name="__main__", alter_sys=True)' % LOOP_MODULE)


def program_dir():
    """The folder that holds the switchboard package (what the loop's Python puts first on its path)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class AutoHost(object):
    """Runs the engine's loop (python -m switchboard.accounts.autoloop) as a child of the page, in a process group of
    its own, while it is wanted (loop_wanted): while auto-switch is on, or while an account is kept. The loop reads
    the accounts either way and switches only while auto-switch is on; it reads the setting at each pass, so turning
    auto-switch on or off never restarts it. The words it gives (status) speak of automatic switching or of reading
    the accounts, and auto_settings says the one that is true.

    The watchdog: the loop writes a heartbeat (api.auto_status). When it has not moved for more than STALE seconds of
    time the Mac was awake, the loop is stuck: it gets SIGTERM (its group), TERM_WAIT, then SIGKILL, and a fresh one starts
    at once; the log and the page name the stage it was stuck in. A loop in the middle of a switch ('switching') is
    given SWITCH_GRACE more seconds first. Time the Mac slept never counts: durations are monotonic, and a wall-clock
    jump bigger than the monotonic step (a sleep) starts the count again. A loop that exits by itself is started again
    after a pause that doubles (30 s up to 15 min); a watchdog restart is not counted as such an exit.

    stop() sends SIGTERM: the loop ends its pass after the stage under way (a switch in progress finishes; no new
    switch or refresh starts), then exits (killed after `wait` seconds). Killing its group never reaches a Keychain
    helper, which runs in a session of its own. A pid file lets a page that starts again end a loop an earlier one
    lost.

    Every wait before a SIGKILL (STOP_WAIT, TERM_WAIT) is longer than the loop's own wait at exit for a refresh already
    sent (40 s: Anthropic's answer, then the save; accounts/tokens.py), so a kill never loses a renewed login whose old
    one is already spent."""

    STALE = 180.0
    SWITCH_GRACE = 60.0
    STOP_WAIT = 45.0                # SIGTERM, then this long before SIGKILL
    TERM_WAIT = STOP_WAIT           # the same for a stuck loop: its refresh worker may still be saving an answer
    SLEPT = 30.0                    # a wall-clock step this much bigger than the monotonic one is a sleep
    PAUSES = (2, 10, 60, 300, 900)  # a first or second death starts again in seconds; 15 min only for one that keeps dying
    SAID_FOR = 600.0                # "Restarted ... at 10:42" stays on the page this long
    # Awake seconds with no finished pass, past the wait the loop planned after its last one, before a loop that still
    # beats is replaced while auto-switch is on. Every stage's deadline added up is 584 s (autoloop.STAGE_DEADLINE),
    # so a pass that is only slow never reaches it.
    NO_PASS = 720.0
    PLAN_MAX = 600.0                # the longest wait a loop plans between two passes (autoloop.MAX_SLEEP)
    GIVE_UP = 3                     # watchdog restarts within GIVE_UP_WINDOW: past them a fourth would not help (a
    GIVE_UP_WINDOW = 1800.0         # heartbeat that can't be written looks stuck to every fresh loop too)
    BUSY_RUNS = 2                   # "busy" exits in a row (another loop holds the lease) before a stuck one is ended
    EXIT_BUSY = 5                   # the loop's exit code when another loop holds the lease (autoloop.EXIT_BUSY)

    def __init__(self, monotonic=None, wall=None):
        self.lock = threading.RLock()
        self.proc = None
        self.started = 0.0          # monotonic
        self.failures = 0
        self.retry_at = 0.0         # monotonic
        self.exited = None          # the exit code of a loop that ended by itself, until the next one starts
        self.stopping = 0
        self.looked_for_stale = False
        self.popen = subprocess.Popen       # tests put fakes here
        self.killpg = os.killpg
        self.monotonic = monotonic or time.monotonic
        self.wall = wall or time.time
        self.beat = (None, None, None)  # the heartbeat as last seen: (pid, stage, last_pass_at); none yet
        self.beat_at = 0.0          # monotonic time it last moved (or the loop started, or the Mac woke)
        self.clock = None           # (wall, monotonic) at the last look: a sleep shows as a wall-clock jump
        self.last = {}              # the last api.auto_status() answer
        self.restarted = None       # {'at' (wall), 'stage', 'mono'}: the last watchdog restart
        self.deferred_at = None     # monotonic: when a kill was put off for a switch in progress
        self.woke_at = 0.0          # monotonic: when the Mac last woke (seen by a look)
        self.stopper = None         # the thread that waits for a loop told to stop (stop_soon)
        self.passed = None          # the last finished pass as last seen (the heartbeat's last_pass_at)
        self.passed_at = 0.0        # monotonic time it last moved (or the loop started)
        self.kills = []             # monotonic times of watchdog restarts, for GIVE_UP
        self.gave_up = None         # {'at', 'why'}: the watchdog stopped restarting (GIVE_UP), until the beat moves
        self.why = None             # the watchdog's reason for its next restart: 'stuck', or the no-pass words
        self.on_exit = None         # called (on a thread) when a loop ends by itself: the page wakes its loop
        self.busy = 0               # "busy" exits in a row
        self.stranger_due = False   # a stuck loop that is not ours holds the lease: end it (outside the lock)
        self.stranger_watchdog = None   # tests put a stand-in here; else autoloop.watchdog
        self.no_python = False      # no Python could start the loop at the last try
        self.start_error = None     # why the last start failed, until one starts

    # ---------- whether it should run ----------

    def wanted(self):
        """loop_wanted, with what can't be told just now read as "as it is"."""
        w = loop_wanted(switching_on())
        return self.running() if w is None else w

    def pid_file(self):
        return data_path('auto.pid')

    def log_file(self):
        return data_path('auto.log')

    def running(self):
        with self.lock:
            return self.proc is not None and self.proc.poll() is None

    def recorded_pid(self):
        """The pid the running loop's page wrote down, when that process is alive (doctor runs apart from the page)."""
        try:
            with open(self.pid_file()) as f:
                pid = int(f.read().strip() or 0)
            if pid <= 1:
                return None
            os.kill(pid, 0)
        except (OSError, ValueError):
            return None
        return pid

    # ---------- the heartbeat ----------

    def _status(self):
        try:
            s = api.auto_status() or {}
        except (api.AccountsError, NotImplementedError) as e:
            log('the loop that reads the accounts: its heartbeat could not be read: %r' % e)
            s = {}
        self.last = s if isinstance(s, dict) else {}
        return self.last

    def _slept(self):
        """True when the Mac slept since the last look (the wall clock moved further than the monotonic one)."""
        now_w, now_m = self.wall(), self.monotonic()
        prev, self.clock = self.clock, (now_w, now_m)
        if prev is None:
            return False
        return (now_w - prev[0]) - (now_m - prev[1]) > self.SLEPT

    def stale_for(self, s=None):
        """Seconds of awake time the loop's heartbeat has not moved (0 while it is fresh). Nothing from before this loop
        started, or before the Mac last woke, counts."""
        s = self.last if s is None else s
        mono = self.monotonic()
        pid = self.proc.pid if self.proc is not None else None
        beat = (s.get('pid'), s.get('stage'), s.get('last_pass_at'))
        if beat != self.beat:
            self.beat, self.beat_at = beat, mono
        floor = max(self.started, self.woke_at)
        ours = mono - max(self.beat_at, floor)
        theirs = _finite(s.get('stuck_for_s'))
        if theirs is None or theirs < 0 or (pid is not None and s.get('pid') not in (None, pid)):
            return max(0.0, ours)               # no heartbeat of this loop's yet: from when it started
        return max(0.0, min(theirs, mono - floor))

    def undecided_for(self, s=None):
        """Seconds of awake time since the loop last finished a pass (its heartbeat's last_pass_at moved), counted from
        no earlier than this loop's start or the Mac's last wake."""
        s = self.last if s is None else s
        mono = self.monotonic()
        pid = self.proc.pid if self.proc is not None else None
        passed = s.get('last_pass_at') if s.get('pid') in (None, pid) else None
        if passed != self.passed:
            self.passed, self.passed_at = passed, mono
        return max(0.0, mono - max(self.passed_at, self.started, self.woke_at))

    def planned(self, s=None):
        """The wait the loop planned after its last pass (next_pass_at - last_pass_at), at most PLAN_MAX."""
        s = self.last if s is None else s
        last, nxt = _finite(s.get('last_pass_at')), _finite(s.get('next_pass_at'))
        if last is None or nxt is None:
            return 0.0
        return min(self.PLAN_MAX, max(0.0, nxt - last))

    def _gave_up_words(self):
        """Why the watchdog stopped restarting, with what may stop the heartbeat being written."""
        cause = ''
        try:
            if core.disk_low(fresh=True):
                cause = ' The disk is almost full.'
            elif not os.access(core.data_dir(), os.W_OK):
                cause = " Switchboard's folder can't be written."
        except Exception:  # noqa: BLE001 - the words go without a cause
            cause = ''
        return ('it was restarted %d times in %d minutes and still did not answer, so Switchboard stopped restarting '
                'it.%s Run: switchboard doctor' % (self.GIVE_UP, self.GIVE_UP_WINDOW // 60, cause))

    def status(self, wanted=None):
        """{'wanted', 'running', 'on', 'starting', 'last_pass', 'problem', 'stage', 'waiting_for', 'restarted',
        'reading_problem', 'reading_restarted', 'asked'}. `on` only while the heartbeat is fresh. `wanted`: whether the
        loop should run (loop_wanted), when the caller worked it out already. `problem` and `restarted` say what is
        wrong in words of automatic switching, `reading_problem` and `reading_restarted` the same in words of reading
        the accounts (auto_settings picks the ones that are true). `asked`: the heartbeat's last read someone asked for
        that the loop answered."""
        with self.lock:
            running = self.running()
            s = self._status() if running else {}
            stale = self.stale_for(s) if running else 0.0
            if running and s.get('pid') not in (None, self.proc.pid):
                s = {}                              # an older loop's heartbeat: this one has not written one yet
            starting = running and not s.get('last_pass_at') and self.monotonic() - self.started < self.STALE
            stage = s.get('stage') if isinstance(s.get('stage'), str) else None
            said = {}
            for key, noun in (('', 'Automatic switching'), ('reading_', 'Reading the accounts')):
                problem = None
                if self.gave_up:
                    problem = '%s is stuck: %s' % (noun, self.gave_up['why'])
                elif self.no_python:
                    problem = ("%s can't start: the Python it runs on is gone. Run: switchboard update" % noun)
                elif self.start_error:
                    problem = "%s can't start (%s). It tries again by itself." % (noun, self.start_error)
                elif self.exited is not None:
                    problem = '%s stopped (exit %s). It starts again by itself.' % (noun, self.exited)
                elif running and stale > self.STALE:
                    problem = '%s is stuck%s. Switchboard restarts it.' % (noun, ' (%s)' % stage_words(stage)
                                                                           if stage else '')
                restarted = None
                if self.restarted and self.monotonic() - self.restarted['mono'] < self.SAID_FOR:
                    restarted = 'Restarted %s at %s (%s).' % (
                        noun[0].lower() + noun[1:], time.strftime('%H:%M', time.localtime(self.restarted['at'])),
                        self.restarted.get('said') or 'it was stuck%s' % (
                            ' ' + stage_words(self.restarted['stage']) if self.restarted.get('stage') else ''))
                said.update({key + 'problem': problem, key + 'restarted': restarted})
            waiting = s.get('waiting_for') if isinstance(s.get('waiting_for'), str) else None
            return dict(said, wanted=self.wanted() if wanted is None else bool(wanted), running=running,
                        on=running and stale <= self.STALE, starting=bool(starting),
                        last_pass=_finite(s.get('last_pass_at')), next_pass=_finite(s.get('next_pass_at')),
                        stage=stage, waiting_for=waiting,
                        offline_since=_finite(s.get('offline_since')), settling=s.get('settling') is True,
                        stuck_for=_finite(s.get('stuck_for_s')) if running else None,
                        asked=s.get('asked') if isinstance(s.get('asked'), dict) else None)

    # ---------- start, watch, stop ----------

    def ensure(self):
        """Start, watch or stop the loop to match loop_wanted. Called on every tick and when the setting changes: a loop
        that runs already is only watched then, never restarted (it takes the new setting at its next pass)."""
        restart = None
        with self.lock:
            if not self.looked_for_stale:           # once per page: a loop an earlier page lost, on or off
                self.looked_for_stale = True
                if self.proc is None:
                    self._end_stale()
            if self.proc is not None and self.proc.poll() is not None:
                self._ended()
            stranger, self.stranger_due = self.stranger_due, False
            wanted = self.wanted()
            if wanted and not stranger:
                if self.proc is None and not self.stopping and self.monotonic() >= self.retry_at:
                    self.start()
                elif self.proc is not None:
                    restart = self._watch()
                if restart is None:
                    return
        if not wanted:
            self.stop_soon()        # nothing kept, auto-switch off. Never waits: this runs on a request and the tick
            return
        if stranger:
            self._end_stranger()    # outside the lock: it may wait TERM_WAIT
            return
        self._restart(restart)

    def revive(self):
        """Between ticks (the page's loop calls it at every pass): a loop that ended by itself, or could not start, is
        started again as soon as its pause is over, not at the next tick. A loop stopped on purpose is left."""
        with self.lock:
            if self.proc is not None and self.proc.poll() is not None:
                self._ended()
            due = self.proc is None and not self.stopping and (self.exited is not None or self.start_error
                                                                or self.no_python) \
                and self.monotonic() >= self.retry_at
        if due:
            self.ensure()

    def due_in(self):
        """Seconds until a loop that ended (or could not start) may start again; None when none waits."""
        with self.lock:
            if self.proc is not None or not (self.exited is not None or self.start_error or self.no_python):
                return None
            return max(0.0, self.retry_at - self.monotonic())

    def _end_stranger(self):
        """Fresh loops keep exiting "busy": another loop holds the lease. When that one is stuck (its heartbeat is
        stale while it holds the lease) autoloop.watchdog ends it, and ours starts at once. A healthy one is left."""
        try:
            if self.stranger_watchdog is not None:
                said = self.stranger_watchdog(wait_s=self.TERM_WAIT)
            else:
                from .accounts import autoloop
                said = autoloop.watchdog(wait_s=self.TERM_WAIT)
        except Exception as e:  # noqa: BLE001 - the next busy exits try again
            log('the loop that holds the lease could not be looked at: %r' % e)
            return
        if said:
            log('another loop held the lease and was stuck; it was ended (%s)' % said)
            with self.lock:
                self.retry_at = 0.0
        else:
            log('another loop holds the lease and is not stuck; it is left to run')

    def _watch(self):
        """The watchdog's look: None, or the stage the loop is stuck in ('' when it named none)."""
        s = self._status()
        if self._slept():                           # the Mac slept: it gets a full STALE of awake time again
            self.woke_at = self.monotonic()
            self.deferred_at = None
            return None
        stale = self.stale_for(s)
        undecided = self.undecided_for(s)
        stage = s.get('stage') if isinstance(s.get('stage'), str) else ''
        if stale <= self.STALE:
            self.deferred_at = None
            if self.gave_up and self.proc is not None and s.get('pid') == self.proc.pid and stale == 0:
                log('the loop that reads the accounts answers again; its watchdog works again')
                self.gave_up = None
            if stage == 'switching' or undecided <= self.NO_PASS + self.planned(s) or not switching_on():
                return None
            self.why = 'it had made no decision for %d min' % (undecided // 60)
            return self._unless_given_up(stage)
        if stage == 'switching':
            if self.deferred_at is None:
                self.deferred_at = self.monotonic()
                log('no heartbeat from the loop for %d s, in the middle of a switch; waiting up to %d s'
                    % (stale, self.SWITCH_GRACE))
            if self.monotonic() - self.deferred_at < self.SWITCH_GRACE:
                return None
        self.why = 'stuck'
        return self._unless_given_up(stage)

    def _unless_given_up(self, stage):
        """The stage to restart from, unless GIVE_UP restarts were made within GIVE_UP_WINDOW: then None, said once,
        and none again until the loop's heartbeat moves (see _watch)."""
        now = self.monotonic()
        self.kills = [t for t in self.kills if 0 <= now - t < self.GIVE_UP_WINDOW]
        if len(self.kills) < self.GIVE_UP and not self.gave_up:
            return stage
        if not self.gave_up:
            self.gave_up = {'at': self.wall(), 'why': self._gave_up_words()}
            log('the loop that reads the accounts: %s' % self.gave_up['why'])
        return None

    def _restart(self, stage):
        """Stop a stuck loop (its group: SIGTERM, TERM_WAIT, SIGKILL) and start a fresh one at once."""
        with self.lock:
            proc, self.proc = self.proc, None
            if proc is None:
                return
            self.stopping += 1
        said = self.why if self.why and self.why.startswith('it had made no decision') else None
        try:
            self._end(proc, self.TERM_WAIT)
            if said:
                log('the loop that reads the accounts beat but made no decision for over %d s of awake time; '
                    'restarted it' % self.NO_PASS)
            else:
                log('the loop that reads the accounts was stuck%s for over %d s of awake time; restarted it'
                    % (' in stage "%s"' % stage if stage else '', self.STALE))
            what = 'automatic switching' if switching_on() else 'reading the accounts'     # what it was doing
            try:
                with core.locked() as st:
                    core.event(st, 'Restarted %s (%s)' % (what, said or 'it was stuck%s' % (
                        ' ' + stage_words(stage) if stage else '')), 'warn', h='auto', by='auto',
                        reason=said or 'stuck in %s' % (stage or 'an unknown stage'))
            except Exception as e:  # noqa: BLE001 - the restart goes on either way
                log('history: %r' % e)
        finally:
            with self.lock:
                self.stopping -= 1
                self.restarted = {'at': self.wall(), 'stage': stage, 'mono': self.monotonic(), 'said': said}
                self.kills.append(self.monotonic())
                self.deferred_at = None
                self.why = None
                self.start()                        # at once: a watchdog restart is not a failure

    def _ended(self):
        code, pid = self.proc.returncode, self.proc.pid
        self.proc = None
        _remove_pid_file(self.pid_file(), pid)
        now = self.monotonic()
        if now - self.started >= 600:
            self.failures = 0
        self.failures += 1
        pause = self.PAUSES[min(self.failures - 1, len(self.PAUSES) - 1)]
        self.retry_at = now + pause
        self.exited = code
        self.busy = self.busy + 1 if code == self.EXIT_BUSY else 0
        if self.busy > self.BUSY_RUNS:
            self.busy, self.stranger_due = 0, True
        log('the loop that reads the accounts stopped (exit %s); next try in %d s' % (code, pause))

    def command(self, python=None):
        return [python or sys.executable, '-I', '-B', '-c', RUNPY, program_dir()]

    def candidates(self):
        """The Pythons the loop may start on, in order: this page's own, the page launcher's (the app's, then the
        program's), then the ones the installer recorded. Paths only: whether each is there is looked at by start()."""
        out = [sys.executable]
        try:
            with open(data_path(os.path.join('bin', 'switchboard-page'))) as f:
                for line in f:
                    for key in ('APP_PY="', 'PY="'):
                        if line.startswith(key) and line.rstrip().endswith('"'):
                            out.append(line.rstrip()[len(key):-1])
        except OSError:
            pass
        rec = core.read_json(data_path(os.path.join('runtime', 'python.json')))
        out += [rec.get('page_python'), rec.get('python')]
        seen, order = set(), []
        for p in out:
            if isinstance(p, str) and p and '$' not in p and p not in seen:
                seen.add(p)
                order.append(p)
        return order

    def _failed_start(self, why):
        """No loop could be started: counted as a death, so the next try waits its pause instead of every tick."""
        now = self.monotonic()
        self.failures += 1
        pause = self.PAUSES[min(self.failures - 1, len(self.PAUSES) - 1)]
        self.retry_at = now + pause
        log('the loop that reads the accounts could not start (%s); next try in %d s' % (why, pause))

    def _reap(self, proc):
        """Wait for the loop to end, then wake the page's loop (on_exit), so it starts again within seconds."""
        try:
            proc.wait()
        except Exception:  # noqa: BLE001 - a stand-in, or a loop waited for elsewhere: the tick still sees it end
            return
        if self.on_exit is not None and self.proc is proc:
            try:
                self.on_exit()
            except Exception as e:  # noqa: BLE001
                log('waking the page: %r' % e)

    def start(self):
        self._end_stale()
        path = self.log_file()
        try:
            if os.path.getsize(path) > 1 << 20:
                os.replace(path, path + '.1')
        except OSError:
            pass
        try:
            out = open(path, 'a')
        except OSError:
            out = subprocess.DEVNULL
        env = dict(os.environ, SWITCHBOARD_AUTO_HOST='page')
        for k in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP'):
            env.pop(k, None)
        tried, errors, passed_over = [], [], []
        try:
            for python in self.candidates():
                if not (os.path.isfile(python) and os.access(python, os.X_OK)):
                    passed_over.append(python)      # gone (the app moved, an update, a clean-up)
                    continue
                tried.append(python)
                try:
                    self.proc = self.popen(self.command(python), stdin=subprocess.DEVNULL, stdout=out,
                                           stderr=subprocess.STDOUT, env=env, start_new_session=True, close_fds=True)
                    break
                except OSError as e:
                    errors.append('%s: %s' % (python, e.strerror or e))
                    passed_over.append(python)
        finally:
            if out is not subprocess.DEVNULL:
                out.close()
        if self.proc is None:
            self.no_python = not tried
            self.start_error = None if self.no_python else (errors[-1] if errors else 'not known')
            self._failed_start('no Python to run it on' if self.no_python else '; '.join(errors))
            return
        if passed_over:
            log('gone or not starting: %s; the loop that reads the accounts starts on %s'
                % (', '.join(passed_over), tried[-1]))
        self.no_python, self.start_error = False, None
        self.started = self.monotonic()
        self.beat, self.beat_at, self.clock, self.woke_at = (None, None, None), self.started, None, 0.0
        self.passed, self.passed_at = None, self.started
        self.exited = None
        if self.on_exit is not None:
            threading.Thread(target=self._reap, args=(self.proc,), name='switchboard-auto-reap', daemon=True).start()
        try:
            with open(self.pid_file(), 'w') as f:
                f.write(str(self.proc.pid))
        except OSError:
            pass
        log('the loop that reads the accounts started (pid %s)' % self.proc.pid)

    def _end(self, proc, wait):
        """SIGTERM to the loop's group, `wait` seconds, then SIGKILL. Never another process's: only this child's."""
        if proc.poll() is None:
            try:
                self.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                proc.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                try:
                    self.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    log('the loop that reads the accounts (pid %s) did not end after SIGKILL' % proc.pid)
        _remove_pid_file(self.pid_file(), proc.pid)

    def stop(self, wait=STOP_WAIT):
        """SIGTERM: the loop ends after the stage under way (a switch in progress finishes), then exits; killed if still
        there after `wait` s. Also waits for a loop stop_soon is still ending (the page's own exit)."""
        with self.lock:
            proc, self.proc = self.proc, None
            self.failures, self.retry_at = 0, 0.0
            stopper = self.stopper
            if proc is not None:
                self.stopping += 1
        if proc is None:
            if stopper is not None and stopper.is_alive():
                stopper.join(wait + 10)
            return
        try:
            self._end(proc, wait)
            log('the loop that reads the accounts stopped')
        finally:
            with self.lock:
                self.stopping -= 1

    def stop_soon(self, wait=STOP_WAIT):
        """stop() without waiting: SIGTERM now; the wait for the loop to end (then SIGKILL) on a thread of its own, so
        turning it off answers at once. Nothing new starts until the old loop is gone (`stopping`)."""
        with self.lock:
            proc, self.proc = self.proc, None
            self.failures, self.retry_at = 0, 0.0
            if proc is None:
                return
            self.stopping += 1
        if proc.poll() is None:
            try:
                self.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
        if proc.poll() is not None:                 # it ended at once
            _remove_pid_file(self.pid_file(), proc.pid)
            log('the loop that reads the accounts stopped')
            with self.lock:
                self.stopping -= 1
            return

        def finish():
            try:
                self._end(proc, wait)
                log('the loop that reads the accounts stopped')
            finally:
                with self.lock:
                    self.stopping -= 1
        t = threading.Thread(target=finish, name='switchboard-auto-stop', daemon=True)
        with self.lock:
            self.stopper = t
        t.start()

    def _end_stale(self):
        """A loop left by a page that died without stopping it: ended, if that pid is still such a loop."""
        try:
            with open(self.pid_file()) as f:
                pid = int(f.read().strip() or 0)
        except (OSError, ValueError):
            return
        if pid > 1 and pid != os.getpid() and (self.proc is None or self.proc.pid != pid):
            try:
                said = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='], capture_output=True,
                                      text=True, timeout=5).stdout
            except (OSError, subprocess.SubprocessError):
                said = ''
            if LOOP_MODULE in said:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        _remove(self.pid_file())


STAGE_WORDS = {'network': 'waiting for the network', 'keychain': "waiting for macOS's Keychain",
               'reading': 'reading the accounts', 'deciding': 'deciding', 'switching': 'switching',
               'sleeping': 'between checks', 'waking': 'waking up', 'upkeep': 'renewing logins'}


def stage_words(stage):
    """A loop stage in words: 'waiting for the network'. Unknown stages are named as the loop named them."""
    if not isinstance(stage, str) or not stage.strip():
        return ''
    s = stage.strip()
    for key, words in STAGE_WORDS.items():
        if s == key or s.startswith(key + ':') or s.startswith(key + ' '):
            return words
    return 'at "%s"' % s[:40]


def _remove(p):
    try:
        os.remove(p)
    except OSError:
        pass


def _remove_pid_file(p, pid):
    """Remove the pid file only while it still names this pid."""
    try:
        with open(p) as f:
            if f.read().strip() != str(pid):
                return
    except OSError:
        return
    _remove(p)


HOST = AutoHost()
