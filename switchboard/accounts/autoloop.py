"""The automatic loop process: stages, a heartbeat, deadlines, wake and load awareness, one lease.

    python -m switchboard.accounts.autoloop [--once] [--dry-run] [--after-restart]

One process reads usage, watches Claude Code's activity and moves Claude Code's login when the policy says so. It is
built so that it never goes silent, alive but saying nothing:

- Stages and deadlines. A pass is a row of stages (the live login, the accounts, keeping logins, the status line,
  usage, activity, deciding, verifying, switching, upkeep). Each runs in a worker thread under its own deadline; a
  stage that overruns is left to finish on its own and the pass carries on (never two of one stage at once). The one
  exception is a switch in progress: it is never abandoned (a credential write cut off is the worst case), only
  waited for, and the heartbeat stops beating meanwhile, so a switch that hangs shows as a stale heartbeat.
- The heartbeat, <data>/auto-heartbeat.json: the stage and when it began, the last beat on both clocks, the last
  finished pass, and since when the Mac has been offline. The loop beats at every stage and at least every 30 s
  while a stage is within its deadline or while it sleeps. status() measures the beat's age in awake time (the
  monotonic clock stops while the Mac sleeps, so a sleep never looks like a hang): older than 180 s means stuck, and
  status() names the stage. The page's host (engine.AutoHost) restarts its own loop when it is stuck. watchdog() is
  for a stuck loop that is NOT the page's child and holds the lease, so the page's fresh loops keep exiting "busy":
  the host calls it after the third such exit in a row. It stops that loop by its recorded process id (only while
  that process holds the lease and its heartbeat is stale, so a reused id or a healthy loop is never touched) and
  records why; the next fresh loop's first pass reads every account again and makes no optional switch. A stuck
  switch gets 60 s more.
- Wake. A wall clock that moved more than awake time (by 60 s or more) means the Mac slept or the clock was changed:
  the loop waits for the network, then 30 s more, before any read or switch, then reads every account once.
- Load and battery. Above 4 per core, transcripts are not read, no optional switch is made and idle saved logins wait
  (reads go on as often: usage climbs fastest while the Mac is busy); on battery under 25%, reads slow down 3x and no
  optional switch is made; under 5% and not charging, no credential is written unless the account in use is truly
  blocked.
- Reads someone asked for. The page writes <data>/read-request.json ({'at', 'all'}) when it is opened, when someone
  presses Read again, and when a setting changes. While the loop sleeps it looks at that file every 2 s (one stat); a
  new request ends the sleep, and the pass reads the account in use (with 'all', every account) as a read someone
  asked for (usage.py: not for an account read in the last minute, never past the pace or inside a wait), before its
  regular reads. The heartbeat's 'asked' then says which request was answered and how many accounts it read and held
  back, so the page stops saying "checking". A request within 20 s of the last one answered with a pass is answered
  with no pass of its own (unless it asks for every account after one that asked for the account in use alone), so a
  page that asks too often never makes the loop spin. A request older than 2 minutes, or written before this loop
  started, is not read for. One that comes while the Mac has just woken waits for the first pass after the network
  settles.
- One lease. <data>/auto.lease is locked (flock) by the running loop for its whole life; a second loop exits at once,
  naming the first. The kernel lets go of the lock when the process dies, so a dead loop never blocks a new one.
- Offline. A pass whose every read failed for want of a connection is offline: nothing is decided except staying,
  and the heartbeat says since when, so the page can say "No internet since 17:19" instead of "stuck".

Its own log, <data>/auto.log (1 MB, 3 kept), has one line per pass (outcome, each stage's seconds, load, battery).
Accounts are named by their number there, never by email. Durations come from the monotonic clock throughout.
"""
import argparse
import dataclasses
import errno
import json
import os
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

from . import api, policy, sessions, tokens, usage
from .. import awake, core

HEARTBEAT = 'auto-heartbeat.json'
LEASE = 'auto.lease'
STATE = 'auto-state.json'
STATE_LOCK = 'auto-state.json.lock'
LOG = 'auto.log'
RESTARTED = 'auto-restarted.json'
REQUEST = 'read-request.json'

PASS_EVERY = 60.0
BEAT_EVERY = 30.0
STUCK_AFTER = 180.0          # awake seconds without a beat: stuck
SWITCH_GRACE = 60.0          # a switch in progress gets this much more before the watchdog acts
WAKE_GAP = 60.0              # the wall clock moved this much more than awake time: a sleep or a clock change
NET_SETTLE = 30.0            # after the network is back from a wake, wait this long more
NET_CHECK_EVERY = 5.0
MIN_SLEEP = 15.0
MAX_SLEEP = 600.0
UPKEEP_EVERY = 600.0
UPKEEP_BUDGET = 60.0
NEED_EVERY = 600.0           # a need switch for one reason at most this often
VERIFY_FRESH = 60.0          # before a switch, numbers older than this are read again
LIVE_CACHE = 120.0           # api.decide uses the loop's view of the live login when it is this recent
OUTSIDE_LIVE_DEADLINE = 10.0 # else one live read, outside the loop, bounded by this
DISK_FLOOR = 50 * 1024 * 1024
BATTERY_LOW = 25.0
BATTERY_NO_WRITE = 5.0
LOAD_HIGH = 4.0
LOG_MAX = 1024 * 1024
LOG_KEEP = 3
TIMELINE_KEEP = 20
SWITCHES_KEEP = 20
REQUEST_LOOK = 2.0           # while it sleeps, the loop looks at the request file this often (one stat)
REQUEST_FRESH = 120.0        # a request older than this is not read for: whoever asked stopped waiting long ago
REQUEST_GAP = 20.0           # a request this soon after the last one answered with a pass gets no pass of its own
WATCH_AFTER_SWITCH_S = 180.0 # after a switch (any source) or a put-back, Claude Code's login is looked at more often
WATCH_EVERY_S = 5.0          # ... this often: one Keychain read each, and nothing else
SKIPS_EXIT = 10              # passes in a row a stage is skipped because the last one is still running before the loop
                             # ends (about ten minutes at its usual pace): that thread is not coming back, and a fresh
                             # process is the only way to run the stage again. Its host starts one within seconds.
JOURNAL = 'switch-journal.json'     # the engine's record of a switch under way (accounts/switcher.py)
JOURNAL_AGAIN = 300.0        # a cut-off switch that could not be settled yet is tried again this often, not every pass

EXIT_SWITCHED, EXIT_ERROR, EXIT_BLOCKED, EXIT_NOTHING, EXIT_BUSY = 0, 1, 3, 4, 5
EXIT_STUCK_STAGE = 6         # the loop ended itself: a stage never came back (SKIPS_EXIT)
DISK_FULL_WORDS = ('Your disk is almost full (under 50 MB free), so Switchboard switches accounts again once you free '
                   'some space.')

STAGE_DEADLINE = {'journal': 60, 'live': 35, 'accounts': 10, 'keeping': 30, 'status-line': 10, 'usage': 70,
                  'asked': 70, 'activity': 20, 'verifying': 30, 'switching': 120, 'upkeep': UPKEEP_BUDGET + 15,
                  'power': 8, 'waking': 8, 'network': 8, 'noting': 10, 'putting back': 60}
STAGE_WORDS = {'starting': 'starting', 'journal': 'finishing a switch that was cut off',
               'waking': 'waiting for the network after the Mac woke', 'live': "reading Claude Code's login",
               'accounts': 'reading the saved accounts', 'keeping': 'keeping new logins',
               'status-line': "taking Claude Code's own numbers", 'usage': 'reading usage',
               'asked': 'reading usage, as the page asked', 'activity': 'checking whether Claude Code is busy',
               'deciding': 'deciding',
               'verifying': 're-reading the numbers before a switch', 'switching': 'switching accounts',
               'upkeep': 'refreshing idle saved logins', 'power': 'reading the battery',
               'network': 'checking the network', 'noting': 'writing the history',
               'sleeping': 'waiting for the next pass', 'stopped': 'stopped',
               'putting back': 'checking that Claude Code is still signed in'}
STAGE_WAITS = {'journal': 'the Keychain', 'waking': 'the network', 'network': 'the network', 'live': 'the Keychain', 'keeping': 'the Keychain',
               'usage': 'Anthropic', 'asked': 'Anthropic', 'verifying': 'Anthropic', 'switching': 'the Keychain',
               'upkeep': 'Anthropic', 'sleeping': 'the next pass', 'putting back': 'the Keychain'}


# ---------- small helpers ----------

def _num(x):
    return usage.num(x)


def _err_words(e):
    """A failure in plain words for the log. Never an exception's text from outside (it might carry a secret), except
    AccountsError, whose text is written to be shown."""
    if isinstance(e, NotImplementedError):
        return 'not built yet'
    if isinstance(e, api.AccountsError):
        return '%s: %s' % (e.kind, str(e)[:200])
    return type(e).__name__


def load_per_core():
    try:
        return os.getloadavg()[0] / float(os.cpu_count() or 1)
    except (OSError, AttributeError):
        return None


def power(timeout=3.0):
    """(on AC, battery percent) from `pmset -g batt`, (None, None) when it can't say. Bounded by `timeout`."""
    try:
        out = subprocess.run(awake.PMSET, capture_output=True, text=True, timeout=timeout).stdout or ''
    except (OSError, subprocess.SubprocessError):
        return None, None
    return awake.parse_batt(out)


def disk_free():
    try:
        return shutil.disk_usage(core.data_dir()).free
    except OSError:
        return None


def net_ready(clock=None):
    """Anthropic's name resolves within 5 s (the cheapest proof the network is back)."""
    try:
        usage.with_deadline(lambda: socket.getaddrinfo('api.anthropic.com', 443, proto=socket.IPPROTO_TCP), 5.0,
                            clock or usage.Clock(), name='dns')
        return True
    except Exception:  # noqa: BLE001 - not ready, whatever the reason
        return False


def history_note(text, kind='info', **facts):
    """One line in the page's history (core.event through history.note). Never stops what it records."""
    try:
        from .. import history
        history.note(text, kind, **facts)
    except Exception as e:  # noqa: BLE001
        core.log('auto: history note failed (%s)' % type(e).__name__)


def put_back_note(res):
    """The notification for a put-back (api.PutBack): the account by its whole email. It promises nothing about open
    chats (Claude Code 2.1.329): a process that was only reading picks the login up when its cached read ends (30 s),
    but a chat that asked for something while the login was gone shows the sign-in screen until it is cancelled there.
    Each put-back is its own note."""
    from .. import notify
    return notify.Note('put-back', 'Switchboard', 'Claude Code lost its sign-in. Switchboard put %s back.'
                       % notify.account_label(None, res.account),
                       '%.0f' % (res.at or 0), None)


def notify_put_back(res):
    """Send put_back_note the way every notification is sent (notify.send: once each, within the rate limit), unless
    notifications are turned off or this is the demo. Never raises."""
    try:
        from .. import notify
        if core.DEMO or core.settings().get('notify') is False:
            return False
        return notify.send(put_back_note(res))
    except Exception as e:  # noqa: BLE001 - the History line is said either way
        core.log('auto: the put-back notification failed (%s)' % type(e).__name__)
        return False


def command(python=None):
    """How the page starts the loop: [python, '-m', 'switchboard.accounts.autoloop']."""
    return [python or sys.executable, '-m', 'switchboard.accounts.autoloop']


class AutoLog(object):
    """<data>/auto.log: plain lines with a time, rotated at 1 MB (3 kept). A line that repeats (digits aside) is
    written once, then counted."""

    def __init__(self, path=None, clock=None):
        self.path = path
        self.clock = clock or usage.Clock()
        self.last_key = None
        self.repeats = 0
        self.failed = False

    def _file(self):
        return self.path or core.data_path(LOG)

    def line(self, msg):
        key = re.sub(r'\d+', '#', msg)
        if key == self.last_key:
            self.repeats += 1
            if self.repeats % 100:
                return
            msg = '%s (repeated %d times)' % (msg, self.repeats)
        elif self.repeats:
            self._write('(the line before repeated %d more times)' % self.repeats)
            self.repeats = 0
        self.last_key = key
        self._write(msg)

    def _write(self, msg):
        p = self._file()
        try:
            os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
            if os.path.exists(p) and os.path.getsize(p) > LOG_MAX:
                for i in range(LOG_KEEP - 1, 0, -1):
                    if os.path.exists('%s.%d' % (p, i)):
                        os.replace('%s.%d' % (p, i), '%s.%d' % (p, i + 1))
                os.replace(p, p + '.1')
            fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                stamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.clock.wall()))
                os.write(fd, ('%s %s\n' % (stamp, msg)).encode('utf-8', 'replace'))
            finally:
                os.close(fd)
        except OSError:
            self.failed = True


# ---------- the loop's own state: switches, the live login's timeline, need stamps ----------

def state_path():
    return core.data_path(STATE)


def load_state():
    st = usage.read_json(state_path()) or {}
    if st.get('v') != 1:
        st = {'v': 1}
    for key, kind in (('switches', list), ('timeline', list), ('live', dict), ('need', dict)):
        if not isinstance(st.get(key), kind):
            st[key] = kind()
    if not isinstance(st.get('all_out'), dict):
        st['all_out'] = None
    return st


def update_state(change):
    """Change the loop's state under its lock, atomically. Returns the new state, or None when it could not be
    written (the change is then lost; nothing depends on it more than one pass)."""
    try:
        with usage.held(STATE_LOCK):
            st = load_state()
            change(st)
            usage.write_atomic(state_path(), st)
            return st
    except (usage.LockBusy, OSError, ValueError) as e:
        core.log('auto: could not save its state (%s)' % type(e).__name__)
        return None


def _set_live(st, clock, account_id, first_since=None, **extra):
    """Note the live login. A change starts a new entry (the old one goes to the timeline). The very first one, with
    nothing seen before it, is dated `first_since` when given (the history's last switch): it was live since then, not
    since this loop first looked."""
    live = st.get('live') or {}
    if account_id and live.get('id') != account_id:
        if live.get('id'):
            st['timeline'] = (st.get('timeline') or [])[-(TIMELINE_KEEP - 1):] + [[live.get('t'), live.get('id')]]
            live = dict(usage.stamp(clock), id=account_id)
        else:
            live = dict(usage.stamp(clock), id=account_id)
            if first_since is not None and first_since <= clock.wall():
                live.update(t=round(first_since, 3), mono=None)
    live.update(extra, seen_at=round(clock.wall(), 3))
    st['live'] = live


def record_switch(st, clock, kind, source, frm, to):
    rec = dict(usage.stamp(clock), kind=kind, source=source, frm=frm, to=to)
    st['switches'] = (st.get('switches') or [])[-(SWITCHES_KEEP - 1):] + [rec]
    _set_live(st, clock, to)


def timeline_of(st):
    """[(since, account id)] oldest first, the live one last: whose an obligation is (sessions.owner_at)."""
    out = [(usage.num(t), aid) for t, aid in (x for x in st.get('timeline') or [] if isinstance(x, list) and len(x) == 2)
           if isinstance(aid, str)]
    live = st.get('live') or {}
    if live.get('id'):
        out.append((usage.num(live.get('t')), live['id']))
    return [x for x in out if x[0] is not None]


def last_history_switch(path=None, tail=65536):
    """The time of the newest switch in history.jsonl (any source: the page, a terminal, Claude Code), or None."""
    p = path or core.data_path('history.jsonl')
    try:
        with open(p, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail))
            data = f.read()
    except OSError:
        return None
    best = None
    for line in data.split(b'\n'):
        if b'"switch"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = usage.num(d.get('t')) if isinstance(d, dict) and d.get('kind') == 'switch' else None
        if t is not None and (best is None or t > best):
            best = t
    return best


def since_switch(st, clock, history_t=None):
    """Awake seconds since the last switch from any source: the loop's own and need switches, any change of the live
    login the loop saw, and the history's switch lines. None when none is known."""
    ages = [usage.since_stamp(r, clock) for r in st.get('switches') or []]
    live = st.get('live') or {}
    if live.get('id') and (st.get('timeline') or []):
        ages.append(usage.since_stamp(live, clock))         # the live login changed then
    if history_t is not None:
        ages.append(max(0.0, clock.wall() - history_t))
    ages = [a for a in ages if a is not None]
    return min(ages) if ages else None


def optional_recent(st, clock, within=policy.OPTIONAL_CAP_WINDOW):
    return sum(1 for r in st.get('switches') or [] if r.get('kind') == 'optional'
               and (usage.since_stamp(r, clock) or 0.0) <= within)


# ---------- settings ----------

POLICY_KEY = 'auto_policy'
ONE_CHECK = []      # the settings one check decides on (autocheck.py), in this process only: never stored


def load_settings():
    """policy.Settings from settings.json: 'auto' (on or off, the key the app already uses) and 'auto_policy'."""
    if ONE_CHECK:
        return ONE_CHECK[-1]
    s = core.settings()
    d = dict(s.get(POLICY_KEY)) if isinstance(s.get(POLICY_KEY), dict) else {}
    d['enabled'] = s.get('auto') is True
    return policy.settings_from(d)


def settings_dict(st=None):
    """The settings as a dict, with 'rules': {account id: 'last' | 'out'} only while an account has one (an account
    with no rule is not in it); likewise 'own_lines': {account id: {'session_at': N, 'weekly_at': N}} (each line only
    when the account has its own), 'ranks': {account id: N}, and 'models' (every model counted) only when there are
    several ('model' is the first)."""
    st = st or load_settings()
    out = {'enabled': st.enabled, 'session_at': st.session_at, 'weekly_at': st.weekly_at, 'model_at': st.model_at,
           'model': st.model, 'strategy': st.strategy}
    if st.rules:
        out['rules'] = dict(st.rules)
    if st.own:
        out['own_lines'] = dict((aid, dict(lines)) for aid, lines in st.own)
    if st.ranks:
        out['ranks'] = dict(st.ranks)
    if st.models:
        out['models'] = list(st.models)
    return out


def _line_value(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not policy.LINE_MIN <= v <= policy.LINE_MAX:
        raise api.AccountsError('other', 'Pick a number from %g to %g.' % (policy.LINE_MIN, policy.LINE_MAX))
    return round(float(v), 1)


RULE_WORDS = ('in',) + policy.RULES     # 'in': no rule, used as usual
OWN_WORDS = 'Give an account its own 5-hour or weekly line.'
RANK_WORDS = 'Pick a whole number from %d to %d.' % (policy.RANK_MIN, policy.RANK_MAX)
MODEL_WORDS = 'That is not a model name (letters, digits and spaces, like Fable).'
KEPT_PER_ACCOUNT = ('rules', 'own_lines', 'ranks')      # the stored settings kept for one account each


def _own_value(line):
    """(account id, {key: line or None}) or (account id, None), checked: None takes every own line away, a key with
    None takes that one away."""
    if not (isinstance(line, (tuple, list)) and len(line) == 2 and isinstance(line[0], str)) or \
            not (line[1] is None or (isinstance(line[1], dict) and line[1] and set(line[1]) <= set(policy.OWN_KEYS))):
        raise api.AccountsError('other', OWN_WORDS)
    if line[1] is None:
        return (line[0], None)
    return (line[0], dict((k, None if v is None else _line_value(v)) for k, v in line[1].items()))


def _rank_value(rank):
    """(account id, N or None), checked: None takes the rank away."""
    n = rank[1] if isinstance(rank, (tuple, list)) and len(rank) == 2 and isinstance(rank[0], str) else 0
    if n is not None and (isinstance(n, bool) or not isinstance(n, int) or not policy.RANK_MIN <= n <= policy.RANK_MAX):
        raise api.AccountsError('other', RANK_WORDS)
    return (rank[0], n)


def model_value(model):
    """The stored 'model' for what was given: a name, names with commas between them, or a list of names. None for
    none ('' or []), one name as a string (as it always was), several as a list. Refuses anything that is not a name."""
    names = model.split(',') if isinstance(model, str) else model if isinstance(model, list) else None
    if names is None or not all(isinstance(n, str) for n in names):
        raise api.AccountsError('other', MODEL_WORDS)
    out = []
    for n in names:
        if not n.strip():
            continue
        clean = usage.clean_label(n)
        if clean is None:
            raise api.AccountsError('other', MODEL_WORDS)
        if clean.lower() not in [m.lower() for m in out]:
            out.append(clean)
    return None if not out else out[0] if len(out) == 1 else out


def _saved_ids():
    """The saved accounts' ids, or None when the list can't be read just now or reads as empty (then no rule is dropped
    for it: an empty list is far likelier a list not there for a moment than every account gone, and an account that
    is removed takes its own rule away)."""
    try:
        return set(a.id for a in api.accounts()) or None
    except (api.AccountsError, NotImplementedError, OSError):
        return None


def _list_restored_lately():
    """Was the list of accounts restored from a copy within the last day (repairs.json)? An older copy can miss an
    account that is still kept on this Mac (added after that copy was made): its rules are not dropped as "no longer
    saved" until the day is over. An account removed on purpose takes its rules with it all the same (forget_rules)."""
    try:
        from . import context
        from .. import repairs
        return any(e.get('kind') == 'restored' for e in repairs.recent(context.current().data_dir, what='accounts'))
    except Exception:  # noqa: BLE001 - not known: rules go by the list, as before
        return False


def _policy_change(changes, keep=None, rule=None, line=None, rank=None):
    """The change to settings.json's 'auto_policy' for save_settings and forget_rules. A stored rule stays only when it
    is 'last' or 'out' and keep(its account id) (no `keep`: every id). A rule of 'in' is no entry, and no rule at all
    is no 'rules' key, so a rule set and taken away again leaves the stored settings as they were. A rule of 'last'
    or 'out' is written only if its account is still saved when it is written (the list is read again here, under the
    settings lock): a removal that lands in between wins, and leaves no rule behind. An account's own lines (`line`) and
    its rank (`rank`) are kept the same way, as 'own_lines' and 'ranks'."""
    def change(old):
        setting = [x[0] for x, unset in ((rule, 'in'), (line, None), (rank, None)) if x is not None and x[1] != unset]
        if setting and not set(setting) <= set(a.id for a in api.accounts()):
            raise api.AccountsError('not_found', 'That account is not saved in Switchboard.')
        new = dict(old) if isinstance(old, dict) else {}
        new.update(changes)
        had = any(k in new for k in KEPT_PER_ACCOUNT)
        rules = dict((k, v) for k, v in policy.rules_from(new.pop('rules', None)) if keep is None or keep(k))
        if rule is not None:
            rules.pop(rule[0], None)
            if rule[1] != 'in':
                rules[rule[0]] = rule[1]
        if rules:
            new['rules'] = rules
        own = dict((k, dict(v)) for k, v in policy.own_from(new.pop('own_lines', None)) if keep is None or keep(k))
        if line is not None:
            mine = own.pop(line[0], {})
            for k, v in (line[1] or dict.fromkeys(policy.OWN_KEYS)).items():
                if v is None:
                    mine.pop(k, None)
                else:
                    mine[k] = v
            if mine:
                own[line[0]] = mine
        if own:
            new['own_lines'] = own
        ranks = dict((k, v) for k, v in policy.ranks_from(new.pop('ranks', None)) if keep is None or keep(k))
        if rank is not None:
            ranks.pop(rank[0], None)
            if rank[1] is not None:
                ranks[rank[0]] = rank[1]
        if ranks:
            new['ranks'] = ranks
        new = dict((k, v) for k, v in new.items() if v is not None)
        if not new and not changes:
            return core.UNSET if had else old   # rules were all there was, and none is left: no auto_policy at all
        return new
    return change


def save_settings(enabled=None, session_at=None, weekly_at=None, model_at=None, model=None, strategy=None, rule=None,
                  line=None, rank=None):
    """Change automatic switching. Every value is checked before anything is written (never clamped); model '' clears
    it, and names with commas between them (or a list) count several. rule: (account id, 'in' | 'last' | 'out') for
    one saved account; 'in' takes its rule away. line: (account id, {'session_at': N, 'weekly_at': N}) gives that
    account lines of its own (a key with None puts that line back on the shared one; None in place of the dict puts
    both back). rank: (account id, 1 to 99, or None to take it away). A stored rule, line or rank that is not one of
    those, or whose account is no longer saved, is dropped whenever the policy is written. Returns the new settings as
    a dict."""
    lines = dict((k, _line_value(v)) for k, v in (('session_at', session_at), ('weekly_at', weekly_at),
                                                   ('model_at', model_at)) if v is not None)
    if strategy is not None and strategy not in policy.STRATEGIES:
        raise api.AccountsError('other', 'Pick %s or %s.' % (', '.join(policy.STRATEGIES[:-1]), policy.STRATEGIES[-1]))
    if enabled is not None and not isinstance(enabled, bool):
        raise api.AccountsError('other', 'Automatic switching is either on or off.')
    model = None if model is None else model_value(model) or ''
    ids = None
    if rule is not None:
        if not (isinstance(rule, (tuple, list)) and len(rule) == 2 and isinstance(rule[0], str)) \
                or rule[1] not in RULE_WORDS:
            raise api.AccountsError('other', 'Pick in, last or out for an account.')
        rule = (rule[0], rule[1])
    if line is not None:
        line = _own_value(line)
    if rank is not None:
        rank = _rank_value(rank)
    named = [x[0] for x in (rule, line, rank) if x is not None]
    if named:
        ids = set(a.id for a in api.accounts())                 # a list that can't be read refuses the change
        if not set(named) <= ids:
            raise api.AccountsError('not_found', 'That account is not saved in Switchboard.')
    changes = dict(lines)
    if strategy is not None:
        changes['strategy'] = strategy
    if model is not None:
        changes['model'] = model or None
    if changes or named:
        stored = core.settings().get(POLICY_KEY)
        if ids is None and isinstance(stored, dict) and any(k in stored for k in KEPT_PER_ACCOUNT):
            ids = _saved_ids()                  # only with rules stored: a save with none reads no account list
        if ids is not None and _list_restored_lately():
            ids = None                          # an account missing from a restored list keeps its rules for now
        core.update_setting(POLICY_KEY, _policy_change(changes, None if ids is None else ids.__contains__, rule, line,
                                                       rank))
    if enabled is not None:
        core.set_setting('auto', enabled)
    return settings_dict()


def forget_rules(account_ids=None):
    """Take away the rules of these accounts (every rule with None): an account removed from Switchboard takes its rule
    with it, so the same account kept again later starts with none."""
    gone = set(account_ids or ())
    try:
        core.update_setting(POLICY_KEY, _policy_change({}, (lambda k: False) if account_ids is None else
                                                       (lambda k: k not in gone)))
    except (OSError, ValueError) as e:          # the account is gone either way; its rule is dropped at the next save
        core.log('auto: could not take a removed account\'s rule away (%s)' % type(e).__name__)


# ---------- heartbeat and lease ----------

class Heartbeat(object):
    """Writes <data>/auto-heartbeat.json and notices sleeps and clock changes between beats."""

    def __init__(self, clock, path=None, pid=None, dry_run=False):
        self.clock = clock
        self.path = path or core.data_path(HEARTBEAT)
        self.rec = {'v': 1, 'pid': pid or os.getpid(), 'boot': clock.boot(), 'started_at': round(clock.wall(), 3),
                    'pass_n': 0, 'dry_run': bool(dry_run)}
        self.prev = (clock.wall(), clock.mono())
        self.drift = 0.0
        self.failed = False

    def beat(self, stage=None, deadline=None, note=None):
        now_w, now_m = self.clock.wall(), self.clock.mono()
        d = (now_w - self.prev[0]) - (now_m - self.prev[1])
        if abs(d) >= 2.0:
            self.drift += d                    # the wall clock moved apart from awake time (a sleep, a clock change)
        self.prev = (now_w, now_m)
        if stage is not None and stage != self.rec.get('stage'):
            self.rec.update(stage=stage, words=STAGE_WORDS.get(stage, stage), since=round(now_w, 3),
                            since_mono=round(now_m, 3))
        self.rec.update(wall=round(now_w, 3), mono=round(now_m, 3), deadline_s=deadline,
                        waiting_for=STAGE_WAITS.get(self.rec.get('stage')))
        if note is not None:
            self.rec['note'] = note
        self._write()

    def take_drift(self):
        d, self.drift = self.drift, 0.0
        return d

    def update(self, **kw):
        self.rec.update(kw)
        self._write()

    def _write(self):
        try:
            usage.write_atomic(self.path, self.rec)
        except (OSError, ValueError) as e:
            if not self.failed:
                core.log('auto: could not write the heartbeat (%s)' % type(e).__name__)
            self.failed = True


class Lease(object):
    """The one lock that decides which process switches automatically, held for the loop's whole life."""

    def __init__(self, path=None):
        self.path = path or core.data_path(LEASE)
        self.fd = None

    def acquire(self, clock):
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                return False
            raise
        self.fd = fd
        body = json.dumps({'pid': os.getpid(), 'since': round(clock.wall(), 3), 'boot': clock.boot()}).encode()
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, body)
        return True

    def release(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def lease_owner(path=None):
    """{'held', 'pid', 'since'}: whether a live process holds the lease (tried, never waited for) and who."""
    path = path or core.data_path(LEASE)
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return {'held': False, 'pid': None, 'since': None}
    try:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            held = False
        except OSError:
            held = True
    finally:
        os.close(fd)
    rec = (usage.read_json(path) or {}) if held else {}
    pid = rec.get('pid') if isinstance(rec.get('pid'), int) and not isinstance(rec.get('pid'), bool) else None
    return {'held': held, 'pid': pid, 'since': usage.num(rec.get('since'))}


# ---------- what the page asks ----------

def request_read(all_accounts=True, now=None):
    """Ask the running loop for a read now (the page's "Read again", its look when opened, a changed setting): write
    <data>/read-request.json, atomically. all_accounts False asks for the account in use alone. Returns the request's
    time, which the heartbeat's 'asked' carries once the loop has answered it. A loop that is not running never reads
    for it (one that starts later takes it as old).

    The loop takes only the newest file, so a request asks for no less than one for every account that is still fresh
    and not answered yet, and two written in one millisecond are two (the loop takes each time once)."""
    at = round(time.time() if now is None else now, 3)
    path = core.data_path(REQUEST)
    last = usage.read_json(path) or {}
    last_at = _num(last.get('at'))
    if last_at is not None:
        if at <= last_at < at + 1.0:
            at = round(last_at + 0.001, 3)
        if not all_accounts and last.get('all') is not False and 0 <= at - last_at <= REQUEST_FRESH \
                and not _answered(last_at):
            all_accounts = True
    usage.write_atomic(path, {'at': at, 'all': bool(all_accounts)})
    return at


def _answered(at):
    """The loop's heartbeat says it answered the request made at `at`, or a later one."""
    rec = (usage.read_json(core.data_path(HEARTBEAT)) or {}).get('asked')
    done = _num(rec.get('at')) if isinstance(rec, dict) else None
    return done is not None and done >= at


def _asked_view(rec):
    """The heartbeat's 'asked' as the page reads it: {'at' (the request's time), 'done' (when it was answered), 'read'
    (accounts asked of Anthropic), 'ok' (of them, answered), 'held' (held back: read under a minute ago, inside a wait,
    or no saved read)}, or None."""
    if not isinstance(rec, dict) or _num(rec.get('at')) is None:
        return None

    def count(key):
        return int(_num(rec.get(key)) or 0)
    return {'at': _num(rec.get('at')), 'done': _num(rec.get('done')), 'read': count('read'), 'ok': count('ok'),
            'held': count('held')}


def status(clock=None, path=None):
    """The loop's heartbeat for the page and doctor: {'pid', 'stage', 'words', 'last_pass_at', 'next_pass_at' (the pass
    it planned next), 'pass_age_s', 'stuck_for_s', 'lease',
    'waiting_for', 'running', 'restart', 'why', 'offline_since', 'settling', 'note', 'beat_age_s', 'asked'}.
    pass_age_s is awake time since the last finished pass, in this boot only (None before the first pass after the Mac
    started: no awake time can be measured across a restart). stuck_for_s is awake time since the last beat once a beat is overdue (0 while it beats); restart is True once it
    is past STUCK_AFTER (a switch in progress: plus SWITCH_GRACE) while that process still holds the lease. asked: the
    last read someone asked for that the loop answered (_asked_view)."""
    clock = clock or usage.Clock()
    lease = lease_owner()
    out = {'pid': None, 'stage': None, 'words': None, 'last_pass_at': None, 'next_pass_at': None,
           'pass_age_s': None, 'stuck_for_s': None, 'lease': lease,
           'waiting_for': None, 'running': lease['held'], 'restart': False, 'why': None, 'offline_since': None,
           'settling': False, 'note': None, 'beat_age_s': None, 'asked': None}
    hb = usage.read_json(path or core.data_path(HEARTBEAT))
    if not hb:
        return out
    stage = hb.get('stage') if isinstance(hb.get('stage'), str) else None
    same_boot = hb.get('boot') is not None and hb.get('boot') == clock.boot()
    age = usage.elapsed(_num(hb.get('wall')), _num(hb.get('mono')), hb.get('boot'), clock) if same_boot else None
    age = None if age is None else round(age, 1)     # one number for both the age shown and the verdict
    out.update(pid=hb.get('pid'), stage=stage, words=hb.get('words') or STAGE_WORDS.get(stage),
               last_pass_at=_num(hb.get('last_pass_at')), next_pass_at=_num(hb.get('next_pass_at')),
               waiting_for=hb.get('waiting_for'),
               offline_since=_num(hb.get('offline_since')), settling=hb.get('settling') is True, note=hb.get('note'),
               beat_age_s=age, asked=_asked_view(hb.get('asked')))
    if same_boot and _num(hb.get('last_pass_mono')) is not None:
        passed = usage.elapsed(_num(hb.get('last_pass_at')), _num(hb.get('last_pass_mono')), hb.get('boot'), clock)
        out['pass_age_s'] = None if passed is None else round(passed, 1)
    if age is not None and stage not in (None, 'stopped'):
        out['stuck_for_s'] = age if age > 2 * BEAT_EVERY else 0.0
        limit = STUCK_AFTER + (SWITCH_GRACE if stage == 'switching' else 0.0)
        if age > limit and lease['held'] and lease['pid'] == hb.get('pid'):
            out['restart'] = True
            out['why'] = 'stuck %s%s for %s' % (out['words'] or stage,
                                               ' (waiting for %s)' % out['waiting_for'] if out['waiting_for'] else '',
                                               usage.span_text(age))
    return out


def watchdog(clock=None, kill=os.kill, wait_s=10.0, sleep=time.sleep, start=None):
    """For the page's host (engine.AutoHost._end_stranger), when its fresh loops keep exiting "busy": when status()
    says restart, stop the loop that holds the lease by its recorded process id (SIGTERM, then SIGKILL after wait_s),
    note the stage it was stuck in, leave a marker so the next loop's first pass reads every account and makes no
    optional switch, and call start() if given. Returns the plain note, or None (nothing stuck: nothing is done)."""
    st = status(clock)
    if not st['restart']:
        return None
    pid = st['pid']
    owner = lease_owner()
    if not owner['held'] or owner['pid'] != pid or not isinstance(pid, int) or pid <= 1 or pid == os.getpid():
        return None
    note = 'Restarted automatic switching: it was %s.' % st['why']
    AutoLog().line('watchdog: %s (pid %d)' % (note, pid))
    try:
        kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    end = time.monotonic() + wait_s
    while time.monotonic() < end and lease_owner()['held']:
        sleep(0.25)
    if lease_owner()['held']:
        try:
            kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        usage.write_atomic(core.data_path(RESTARTED), {'at': round(time.time(), 3), 'stage': st['stage'],
                                                       'why': st['why']})
    except (OSError, ValueError):
        pass
    history_note(note, 'problem', h='problem')
    if start:
        start()
    return note


def take_restart_marker():
    p = core.data_path(RESTARTED)
    rec = usage.read_json(p)
    if rec is not None:
        try:
            os.unlink(p)
        except OSError:
            pass
    return rec


# ---------- gathering a snapshot outside the loop (api.decide, need switches) ----------

class Deps(object):
    """Everything outside the loop: the account engine's functions (api.py), the network, the system's
    probes. Tests replace any of them by keyword."""

    def __init__(self, **kw):
        self.accounts = api.accounts
        self.live = api.live
        self.switch = api.switch
        self.keep_pass = api.keep_pass
        self.upkeep_pass = api.upkeep_pass
        self.resolve_journal = api.resolve_journal
        self.put_back = api.put_back_pass
        self.notify = notify_put_back
        self.access_token = api.access_token
        self.net = usage.http_get
        self.net_ready = net_ready
        self.load = load_per_core
        self.power = power
        self.procs = sessions.count_claude_processes
        self.disk_free = disk_free
        self.history_note = history_note
        for k, v in kw.items():
            if not hasattr(self, k):
                raise TypeError('unknown dependency %r' % k)
            setattr(self, k, v)


def _call(fn, default=None, deadline=None, clock=None):
    """fn(), or `default` when it raises or (with a deadline) does not finish in time."""
    try:
        if deadline is None:
            return fn()
        return usage.with_deadline(fn, deadline, clock or usage.Clock(), name='call')
    except Exception:  # noqa: BLE001
        return default


def read_live(deps, clock, st=None):
    """(active id, live ok, why, disagree, email, refused): the loop's view when it saw the live login in the last 2
    minutes, else one bounded read (api.live is at most one Keychain read)."""
    st = st or load_state()
    live = st.get('live') or {}
    seen = usage.num(live.get('seen_at'))
    if live.get('id') and seen is not None and -usage.AHEAD <= clock.wall() - seen <= LIVE_CACHE:
        return (live['id'], live.get('ok') is not False, live.get('why'), live.get('disagree') is True,
                live.get('email'), live.get('refused') is True)
    got = _call(deps.live, deadline=OUTSIDE_LIVE_DEADLINE, clock=clock)
    return interpret_live(got, None, live.get('id'))


KEYCHAIN_WORDS = {'timeout': 'macOS is slow to answer the Keychain',
                  'busy': 'macOS is slow to answer the Keychain',
                  'dialog': 'macOS is waiting for you to answer a Keychain dialog',
                  'locked': "your Mac's login Keychain is locked",
                  'denied': 'macOS refused Switchboard access to the Keychain',
                  'no_keychain': "the login Keychain isn't available to Switchboard"}


def interpret_live(live, error, last_id):
    """(active id, live ok, why, disagree, email, refused) from api.live()'s answer (or the error words when it
    failed). refused: Claude Code emptied the login of the account its settings name (Anthropic refused it), so Claude
    Code can't work there: a forced switch, like any login the server refused (never "can't be read" for ever)."""
    if not isinstance(live, api.Live):
        return (last_id, False, "Claude Code's login can't be read right now (%s)" % (error or 'no answer'), False,
                None, False)
    if live.state == api.PRESENT and live.account_id:
        if live.degraded:
            return (live.account_id, False, "only a fallback copy of Claude Code's login could be read, which is "
                    "never switched from", live.disagree, live.email, False)
        return live.account_id, True, None, bool(live.disagree), live.email, False
    if live.state == api.PRESENT:                     # read fine; whose it is can't be told yet
        return None, False, live.why or "it is not known whose login Claude Code is using", False, None, False
    if live.state == 'wiped':
        if live.account_id and not live.degraded:
            return live.account_id, True, None, False, live.email, True
        return None, False, live.why or "Claude Code's login was emptied after Anthropic refused it", False, None, False
    if live.state == api.ABSENT:
        return None, False, 'Claude Code is not signed in', False, None, False
    if live.state == 'other':
        return None, False, live.why or 'Claude Code is not using an account login', False, None, False
    words = KEYCHAIN_WORDS.get(live.why) or "Claude Code's login can't be read right now (%s)" % (live.why or
                                                                                              'not known')
    return last_id, False, words, False, None, False


def gather(deps=None, clock=None, light=True, activity=None):
    """A policy.Snapshot from what is on disk now plus at most one bounded live read: for api.decide and need switches,
    outside the loop (no network)."""
    deps = deps or Deps()
    clock = clock or usage.Clock()
    st = load_state()
    active, ok, why, disagree, _, refused = read_live(deps, clock, st)
    accounts = tuple(_call(deps.accounts, default=()) or ())
    hb = usage.read_json(core.data_path(HEARTBEAT)) or {}
    if activity is None:
        activity = _call(lambda: sessions.activity(timeline_of(st), now=clock.wall(), light=light), default=None)
    return _snapshot(clock, load_settings(), accounts, active, ok, why, disagree, activity,
                     settling=hb.get('settling') is True, offline=_num(hb.get('offline_since')) is not None,
                     load=_call(deps.load), power=(None, None), st=st, no_optional=None, live_refused=refused)


def refused_now(activity, active, st, readings, now):
    """Claude Code wrote its own usage-limit notice in the last 10 minutes, under the account in use (after it became
    the active one), and no reading since shows that account with room: it is refused requests now. The one way to
    know it when its numbers are frozen or missing (a dead end otherwise)."""
    t = usage.num((activity or {}).get('limited_at'))
    if t is None or not active or now - t > sessions.LIMIT_FRESH:
        return False
    since = usage.num((st.get('live') or {}).get('t'))
    if since is not None and t < since - sessions.SETTLE:
        return False                                 # written under the account before
    r = readings.get(active)
    if r is not None and r.windows:
        newer = [w for w in r.windows if (w.read_at if w.read_at is not None else r.read_at or 0) > t]
        if newer and all(w.pct is not None and w.pct < 100 for w in newer):
            return False                             # read since, with room: it refilled or the notice was another's
    return True


OTHER = '(another organization)'     # an obligation the chat's own file puts under an account Switchboard doesn't keep


def _whose(ob, accounts):
    """The account an obligation belongs to: the one whose organization the chat's own file named (`org`, sessions.py)
    when exactly one saved account has it; OTHER when that organization is none of theirs and every saved account's is
    known; else the account the timeline says Claude Code was on (`account_id`)."""
    org = ob.get('org')
    if not isinstance(org, str) or not org:
        return ob.get('account_id')
    ids = [a.id for a in accounts if getattr(a, 'org_id', None) == org]
    if len(ids) == 1:
        return ids[0]
    if not ids and accounts and all(getattr(a, 'org_id', None) for a in accounts):
        return OTHER
    return ob.get('account_id')


def _snapshot(clock, settings, accounts, active, live_ok, why, disagree, activity, settling, offline, load, power, st,
              no_optional, live_refused=False):
    activity = activity if isinstance(activity, dict) else None
    if activity is None and not no_optional:
        no_optional = 'whether Claude Code is busy is not known'
    obligations = tuple(o for o in (dict(o, account_id=_whose(o, accounts)) for o in
                                    (activity or {}).get('obligations') or () if isinstance(o, dict))
                        if o['account_id'] in (active, None))
    readings = usage.readings(now=clock.wall())
    return policy.Snapshot(
        now=clock.wall(), accounts=tuple(accounts), readings=readings, settings=settings,
        active=active, live_ok=live_ok, live_why=why, disagree=disagree,
        busy=bool((activity or {}).get('busy')), idle_for_s=(activity or {}).get('idle_for_s'),
        obligations=obligations, since_switch_s=since_switch(st, clock, last_history_switch()),
        optional_recent=optional_recent(st, clock), load_per_core=load, battery_pct=power[1], on_ac=power[0],
        settling=settling, offline=offline, claimed=frozenset(a.id for a in accounts if a.status == 'claimed'),
        no_optional=no_optional, rate_limited=refused_now(activity, active, st, readings, clock.wall()),
        live_refused=bool(live_refused), left_forced=left_by_force())


def left_by_force():
    """(account id, wall time) of each account a forced switch left, the latest for each, from the list of accounts'
    own record of switches (it names the account left by an emptied login too); () when it can't be read. For the
    policy's moves on older numbers (policy._left_before_read)."""
    try:
        from . import context, vault
        switches = vault.load(context.current()).get('switches') or []
    except Exception:  # noqa: BLE001 - not known: the policy decides as it did before this was known
        return ()
    out = {}
    for x in switches:
        at = _num(x.get('at')) if isinstance(x, dict) else None
        if at is not None and x.get('kind') == 'forced' and isinstance(x.get('from'), str) and x['from'] != x.get('to'):
            out[x['from']] = max(at, out.get(x['from'], at))
    return tuple(sorted(out.items()))


DECISION_FRESH = 2 * PASS_EVERY


def loop_decision(clock=None):
    """The running loop's last decision, when its pass ended under DECISION_FRESH ago (awake time) and it still holds
    the lease: it saw activity, obligations and fresh numbers that a look from outside would not."""
    clock = clock or usage.Clock()
    hb = usage.read_json(core.data_path(HEARTBEAT)) or {}
    d = hb.get('decision')
    if not isinstance(d, dict) or d.get('action') not in ('stay', 'switch'):
        return None
    if hb.get('boot') is None or hb.get('boot') != clock.boot():
        return None
    age = usage.elapsed(_num(hb.get('last_pass_at')), _num(hb.get('last_pass_mono')), hb.get('boot'), clock)
    owner = lease_owner()
    if age is None or age > DECISION_FRESH or not owner['held'] or owner['pid'] != hb.get('pid'):
        return None
    text = dict((k, d.get(k) if isinstance(d.get(k), str) else None) for k in ('target', 'kind', 'hold'))
    return api.Decision(d['action'], reason=d.get('reason') if isinstance(d.get('reason'), str) else '', **text)


def decide_now(now=None, deps=None, clock=None):
    """api.decide: the running loop's own last decision when it is fresh, else policy.decide on a snapshot
    gathered now (light: file times and at most one bounded live read)."""
    if now is None:
        d = loop_decision(clock)
        if d is not None:
            return d
    snap = gather(deps, clock)
    if now is not None:
        snap = dataclasses.replace(snap, now=float(now))
    return policy.decide(snap)


def best_now(deps=None, clock=None):
    """api.best: policy.best on a snapshot gathered now (light: file times and at most one bounded live read)."""
    return policy.best(gather(deps, clock, activity={}))


def need_switch(account_id, why='', deps=None, clock=None):
    """A switch something needs now (api.request_need_switch): only when that account has room, at most once per
    NEED_EVERY per reason. None when it did not go."""
    deps = deps or Deps()
    clock = clock or usage.Clock()
    key = ' '.join(str(why or 'needed').split())[:80]
    st = load_state()
    last = (st.get('need') or {}).get(key)
    if last is not None and (usage.since_stamp(last, clock) or 0.0) < NEED_EVERY:
        return None
    snap = gather(deps, clock, activity={})            # a need switch does not wait for idle
    d = policy.need(snap, account_id, why)
    log = AutoLog(clock=clock)
    if d.action != 'switch':
        log.line('need switch not made: %s' % _no_emails(d.reason))
        return None

    def stamp_need(s):
        s['need'][key] = usage.stamp(clock)
        for k in [k for k, v in s['need'].items() if (usage.since_stamp(v, clock) or 0.0) > 6 * NEED_EVERY]:
            del s['need'][k]
    update_state(stamp_need)
    try:
        res = deps.switch(account_id, kind='need', source='hook', reason=d.reason)
    except Exception as e:  # noqa: BLE001 - a refused switch is a plain None for the hook
        log.line('need switch failed: %s' % _no_emails(_err_words(e)))
        return None
    if isinstance(res, api.SwitchResult) and res.ok:
        update_state(lambda s: record_switch(s, clock, 'need', 'hook', res.from_id, res.to_id))
        return res
    return None


_EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


def _no_emails(text):
    return _EMAIL.sub('<account>', text or '')


# ---------- the loop ----------

class PassResult(object):
    def __init__(self, decision, switched=None, delay=PASS_EVERY, report=None, offline=False, error=None):
        self.decision = decision
        self.switched = switched
        self.delay = delay
        self.report = report
        self.offline = offline
        self.error = error

    @property
    def code(self):
        if self.error:
            return EXIT_ERROR
        if self.switched is not None:
            return EXIT_SWITCHED
        d = self.decision
        if d is not None and d.action == 'stay' and d.kind == 'forced':
            return EXIT_BLOCKED
        return EXIT_NOTHING


class Loop(object):
    """The automatic loop. Everything outside it comes through `deps` and `clock`, so tests drive it pass by pass."""

    def __init__(self, deps=None, clock=None, dry_run=False, after_restart=False, log=None, rand=None):
        self.clock = clock or usage.Clock()
        self.d = deps or Deps()
        self.dry_run = dry_run
        self.rand = rand or random.random
        self.poller = usage.Poller(clock=self.clock, net=lambda u, h, t: self.d.net(u, h, t),
                                   token_for=lambda aid, fresh=False: self.d.access_token(aid, fresh=fresh),
                                   rand=self.rand)
        self.scanner = sessions.Scanner(clock=self.clock, procs=lambda: self.d.procs())
        self.hb = Heartbeat(self.clock, dry_run=dry_run)
        self.lease = Lease()
        self.stop = threading.Event()
        self.log = log or AutoLog(clock=self.clock)
        self.hanging = {}
        self.no_optional = 'automatic switching was just restarted' if after_restart else None
        self.waking = False
        self.wake_tries = 0
        self.settle_until = None
        self.offline_since = None
        self.last_upkeep = None
        self.power_at = None
        self.power_val = (None, None)
        self.activity = None
        self.accounts = ()
        self.last_active = None
        self.pass_n = 0
        self.looked_m = None        # monotonic: when this pass's usage stage chose its reads (_delay)
        self.request = {'sig': None, 'at': None}    # the request file as last looked at, and the last request taken
        self.asked = None           # a request the sleep found, for the pass it starts
        self.answered_m = None      # monotonic: when the last request was answered with a pass
        self.answered_all = False   # and whether it asked for every account
        self.put_back_m = None      # monotonic: when the last put-back was made
        self.missing_watch = False  # the last look found the login missing and asked to look again soon
        self.history_sig = None     # history.jsonl as last looked at (a switch from the page or a terminal)
        self.skips = {}             # stage -> passes in a row it was skipped because the last one is still running
        self.exit_code = 0          # what main() returns after run(): EXIT_STUCK_STAGE when a stage never came back
        self.journal_at = None      # monotonic: when a pass last tried to settle a cut-off switch
        self._take_request()        # one written before this loop started is old: never read for
        if after_restart:
            self.poller.wake()

    # one stage

    def stage(self, name, fn, deadline=None, abandon=True):
        """(done, value, error words). See the module's docstring for deadlines and the one stage never abandoned."""
        deadline = float(deadline or STAGE_DEADLINE.get(name, 30))
        if self.stop.is_set():
            return False, None, 'stopping'          # asked to stop: no new stage starts (one under way finishes)
        prev = self.hanging.get(name)
        if prev is not None:
            if prev.is_alive():
                self.log.line('%s skipped: the last one is still running' % name)
                self._skipped(name)
                return False, None, 'the last one is still running'
            del self.hanging[name]
        self.skips.pop(name, None)
        self.hb.beat(name, deadline=deadline)
        box = {}

        def run():
            try:
                box['value'] = fn()
            except BaseException as e:  # noqa: BLE001 - handed back as words
                box['error'] = e
        t = threading.Thread(target=run, name='switchboard-auto-%s' % name, daemon=True)
        t.start()
        start = self.clock.mono()
        while True:
            left = deadline - (self.clock.mono() - start)
            if left > 0:
                if self.clock.join(t, min(BEAT_EVERY, left)):
                    break
                if self.clock.mono() - start < deadline:
                    self.hb.beat(name, deadline=deadline)
                continue
            if abandon:
                self.hanging[name] = t
                self.log.line('%s overran its %d s deadline; the pass carries on' % (name, deadline))
                self.hb.update(note='%s took longer than %d s; carried on' % (STAGE_WORDS.get(name, name), deadline))
                return False, None, 'no answer within %d s' % deadline
            if self.clock.join(t, BEAT_EVERY):        # a switch in progress: waited for, no beat (see above)
                break
        if 'error' in box:
            return False, None, _err_words(box['error'])
        return True, box.get('value'), None

    def _skipped(self, name):
        """One more pass with `name` skipped. At SKIPS_EXIT in a row the loop ends after this pass (as on SIGTERM: a
        switch in progress finishes, a renewal already sent saves its answer), so its host starts a fresh one. A stage
        that waits for the Keychain never counts: every Keychain call has its deadline and its helper is never killed,
        so one still running waits on a dialog only the person can answer, and a fresh loop would only ask again (one
        more dialog on the screen)."""
        if STAGE_WAITS.get(name) == 'the Keychain':
            return
        self.skips[name] = self.skips.get(name, 0) + 1
        if self.skips[name] >= SKIPS_EXIT and not self.stop.is_set():
            self.log.line('%s has not come back for %d passes: the loop ends so that a fresh one starts' % (name,
                                                                                                        SKIPS_EXIT))
            self.exit_code = EXIT_STUCK_STAGE
            on_stop(self)

    # the clock, the network, power, load

    def _check_clock(self):
        self.hb.beat()                                # measures the gap since the last beat (a sleep shows here)
        drift = self.hb.take_drift()
        if drift >= WAKE_GAP:
            self.log.line('the wall clock moved %s past awake time (a sleep or a clock change): waiting for the '
                          'network, then reading every account once' % usage.span_text(drift))
            self.waking = True
            self.wake_tries = 0
            self.settle_until = None
            self.poller.wake()
        elif drift <= -WAKE_GAP:
            self.log.line('the clock went back %s: reading every account once' % usage.span_text(-drift))
            self.poller.wake()

    def _settling(self):
        if self.waking:
            ok, ready, _ = self.stage('waking', self.d.net_ready)
            if ok and ready:
                self.waking = False
                self.settle_until = self.clock.mono() + NET_SETTLE
            else:
                self.wake_tries += 1
                return True
        if self.settle_until is not None:
            if self.clock.mono() < self.settle_until:
                return True
            self.settle_until = None
        return False

    def _power(self):
        now_m = self.clock.mono()
        if self.power_at is None or now_m - self.power_at >= 120.0 or now_m < self.power_at:
            ok, val, _ = self.stage('power', self.d.power)
            self.power_val = val if ok and isinstance(val, tuple) and len(val) == 2 else (None, None)
            self.power_at = now_m
        return self.power_val

    @staticmethod
    def _battery_low(pw, under=BATTERY_LOW):
        on_ac, pct = pw
        return on_ac is False and pct is not None and pct < under

    # one pass

    def run_pass(self):
        """One pass. Returns a PassResult; never raises for what happens outside (each stage reports in words)."""
        self.pass_n += 1
        t0 = self.clock.mono()
        times = {}
        self._check_clock()
        settings = load_settings()
        lines = policy.lines_for(settings)
        load = _call(self.d.load)
        pw = self._power()
        low_battery = self._battery_low(pw)
        settling = self._settling()

        s0 = self.clock.mono()
        ok, live, err = self.stage('live', self.d.live)
        times['live'] = self.clock.mono() - s0
        if ok and isinstance(live, api.Live) and live.state in (api.ABSENT, 'other'):
            if self._put_back():                    # put back: the rest of the pass sees the account again
                ok, live, err = self.stage('live', self.d.live)
        elif ok and isinstance(live, api.Live) and live.state == api.PRESENT:
            self.missing_watch = False
        active, live_ok, why, disagree, email, refused = interpret_live(live if ok else None, err, self.last_active)
        if active:
            self.last_active = active
        ok, accts, _ = self.stage('accounts', self.d.accounts)
        if ok and accts is not None:
            self.accounts = tuple(a for a in accts if isinstance(a, api.Account))
        number = dict((a.id, i + 1) for i, a in enumerate(self.accounts))
        first = last_history_switch() or 0.0
        st = update_state(lambda s: _set_live(s, self.clock, active, first_since=first, ok=live_ok, why=why,
                                              disagree=disagree, email=email, refused=refused)
                          if active else None) or load_state()

        if live_ok and not settling:
            ok, note, err = self.stage('keeping', self.d.keep_pass)
            if ok and note:
                self.log.line('kept: %s' % _no_emails(str(note)))
            if ok and note and refused:
                # Claude Code's login was emptied, and the keep pass put a newer kept sign-in of the same account back
                # live (capture._ended_back): read it again, so the account in use is not left for another one
                ok, live, err = self.stage('live', self.d.live)
                active, live_ok, why, disagree, email, refused = interpret_live(live if ok else None, err,
                                                                                self.last_active)
                if active:
                    self.last_active = active
                st = update_state(lambda s: _set_live(s, self.clock, active, first_since=first, ok=live_ok, why=why,
                                                      disagree=disagree, email=email, refused=refused)
                                  if active else None) or load_state()
        live_since = usage.num((st.get('live') or {}).get('t'))
        self.stage('status-line', lambda: usage.collect_status_lines(active, live_since, email, now=self.clock.wall()))

        ctx = usage.Context(active=active, order=[a.id for a in self.accounts if a.status not in ('needs_login', 'conflict')],
                            lines=policy.lines_for(settings, active) if active else lines,   # its own, read near them
                            busy=bool((self.activity or {}).get('busy')),
                            battery_factor=3.0 if low_battery else 1.0, settling=settling)
        asked, self.asked = self.asked or self._take_request(), None
        if asked is not None and settling:
            self.asked = asked                  # just woken: nothing can be read yet, so it is not said answered; the
        elif asked is not None:                 # first pass after the network settles reads for it (if still fresh)
            self._answer(asked, ctx)
        s0 = self.looked_m = self.clock.mono()
        ok, rep, err = self.stage('usage', lambda: self.poller.poll(ctx))
        times['usage'] = self.clock.mono() - s0
        rep = rep if ok else None
        offline = self._offline(rep)

        light = (load is not None and load > LOAD_HIGH) or low_battery
        ok, act, _ = self.stage('activity', lambda: self.scanner.look(timeline_of(st), now=self.clock.wall(),
                                                                       light=light))
        if ok and isinstance(act, dict):
            self.activity = act

        self.hb.beat('deciding')
        taken = self.clock.mono()
        settings = load_settings()      # again: a rule or line changed while this pass read is the one it decides on
        snap = _snapshot(self.clock, settings, self.accounts, active, live_ok, why, disagree, self.activity,
                         settling=settling, offline=offline, load=load, power=pw, st=st, no_optional=self.no_optional,
                         live_refused=refused)
        decision = policy.decide(snap)
        if decision.action == 'stay' and decision.kind == 'forced' and decision.hold == 'no fresh numbers':
            decision, snap = self._read_candidates(snap, ctx)
        elif policy.on_older_numbers(decision, snap):   # older numbers only after one try at fresh ones for every account
            decision, snap = self._read_candidates(snap, ctx)
        elif policy.unread_last(snap, decision):     # an account used last with room comes before the move past a line
            decision, snap = self._read_candidates(snap, ctx, policy.unread_last(snap, decision))
        switched = None
        if decision.action == 'switch' and not self.stop.is_set():
            switched = self._act(decision, snap, ctx, pw, taken)
        self._all_out(decision, snap)
        self._disk_room()
        if not settling and self.no_optional and not offline and rep is not None:
            self.no_optional = None                  # the first full pass after a restart is done
        if not self.stop.is_set():
            self._upkeep(settling, offline, load, low_battery)

        delay = self._delay(decision, snap, settling, low_battery, active)
        self.hb.update(pass_n=self.pass_n, last_pass_at=round(self.clock.wall(), 3),
                       last_pass_mono=round(self.clock.mono(), 3), last_outcome=decision.action,
                       offline_since=self.offline_since, settling=settling,
                       next_pass_at=round(self.clock.wall() + delay, 3),
                       decision=dict((k, getattr(decision, k)) for k in ('action', 'target', 'kind', 'reason', 'hold')))
        self.log.line('pass %d: %s%s%s | %s | live %.1fs usage %.1fs total %.1fs | load %s | battery %s' % (
            self.pass_n, 'switched to account %s' % number.get(switched.to_id, '?') if switched else decision.action,
            ' (%s)' % decision.kind if decision.kind else '', ' held: %s' % _no_emails(decision.hold)
            if decision.hold else '', ('reads %s' % (rep.as_dict() if rep else 'none')) if not offline else 'offline',
            times.get('live', 0), times.get('usage', 0), self.clock.mono() - t0,
            '?' if load is None else '%.1f' % load, '?' if pw[1] is None else '%d%%%s' % (pw[1], '' if pw[0] else
                                                                                          ' on battery')))
        return PassResult(decision, switched, delay, rep, offline)

    def _net_ready(self):
        ok, ready, _ = self.stage('network', self.d.net_ready)
        return bool(ok and ready)

    def _back_online(self):
        self.log.line('back online after %s' % usage.span_text(self.clock.wall() - self.offline_since))
        self.offline_since = None
        self.poller.wake()                            # one catch-up pass, spaced out; network waits lifted

    def _offline(self, rep):
        """True while the Mac looks offline: every read of a pass failed for want of a connection and Anthropic's
        name does not resolve either. Checked again every pass until it does."""
        if rep is not None and rep.ok:
            if self.offline_since is not None:
                self._back_online()
            return False
        if rep is not None and rep.offline:
            if self.offline_since is None:
                if self._net_ready():
                    return False                      # Anthropic's side, not the Mac's network
                self.offline_since = round(self.clock.wall(), 3)
                self.log.line('offline: no read reached Anthropic')
            return True
        if self.offline_since is not None:
            if self._net_ready():
                self._back_online()
                return False
            return True
        return False

    def _act(self, decision, snap, ctx, pw, taken):
        """Verify on fresh numbers and switch. Returns the SwitchResult, or None when it did not go. `taken`: the
        monotonic time the snapshot was made (a switch recorded since then by anyone drops this one)."""
        blocked = decision.kind == 'forced' and policy.active_blocked(snap)
        if self.dry_run:
            self.log.line('dry run: would switch (%s)' % decision.kind)
            return None
        if pw[0] is False and pw[1] is not None and pw[1] < BATTERY_NO_WRITE and not blocked:
            self.log.line('battery at %d%%: no credential is written unless the account in use is blocked' % pw[1])
            return None
        free = _call(self.d.disk_free)
        if free is not None and free < DISK_FLOOR:
            # Never even for a blocked account: the switch itself refuses under this floor (switcher._check_disk), so
            # beginning one would only fail at every pass. Said once in History for the whole spell.
            self.log.line('disk almost full: no switch begins under 50 MB free')
            self._disk_full()
            return None
        ids = [decision.target] + ([snap.active] if decision.kind == 'optional' and snap.active else [])
        ok, rep, err = self.stage('verifying', lambda: self.poller.poll(ctx, only=ids, fresh_within=VERIFY_FRESH))
        snap2 = dataclasses.replace(snap, now=self.clock.wall(), readings=usage.readings(now=self.clock.wall()),
                                    settings=load_settings())       # the settings as they are now, a rule included
        again = policy.decide(snap2)
        if decision.kind == 'optional':
            target_fresh = self._fresh(decision.target, ctx, snap2)
            if again.action != 'switch' or again.target != decision.target or not target_fresh:
                self.log.line('optional switch held after re-reading: %s' % _no_emails(
                    again.hold or again.reason if target_fresh else 'its numbers could not be read again just now'))
                return None
        elif again.action != 'switch':
            self.log.line('switch dropped on fresh numbers: %s' % _no_emails(again.reason))
            return None
        decision = again
        st = load_state()
        latest = [usage.since_stamp(r, self.clock) for r in st.get('switches') or []]
        recorded = last_history_switch()                # the page's, a terminal's, Claude Code's own move
        elsewhere = recorded is not None and snap.now - 1.0 < recorded <= self.clock.wall() + usage.AHEAD
        if elsewhere or any(a is not None and a < self.clock.mono() - taken + 1.0 for a in latest):
            self.log.line('another switch was just made; this one is dropped')
            return None
        note = policy.switch_note(decision, snap2)
        ok, res, err = self.stage('switching', lambda: self.d.switch(decision.target, kind=decision.kind,
                                                                     source='auto', reason=note), abandon=False)
        if not ok:
            self.log.line('switch failed: %s' % _no_emails(err))
            return None
        if not isinstance(res, api.SwitchResult) or not res.ok:
            self.log.line('switch not made%s' % (' (rolled back)' if getattr(res, 'rolled_back', False) else ''))
            return None
        update_state(lambda s: record_switch(s, self.clock, decision.kind, 'auto', res.from_id, res.to_id))
        self.last_active = res.to_id
        if policy.on_older_numbers(decision, snap2):
            self._read_landing(res.to_id, ctx, snap2)
        return res

    def _read_landing(self, aid, ctx, snap):
        """A switch made on older numbers: the account it landed on is read at once, as the account in use now. When a
        wait holds that read back (a 429's, say), it goes the moment the wait ends (Poller.read_first)."""
        now = usage.Context(active=aid, order=ctx.order, lines=policy.lines_for(snap.settings, aid), busy=ctx.busy,
                            battery_factor=ctx.battery_factor)
        ok, rep, _ = self.stage('verifying', lambda: self.poller.poll(now, only=[aid]))
        if ok and rep is not None and aid in rep.ok:
            self.log.line('moved on older numbers: the account it moved to was read at once')
            return
        self.poller.read_first(aid)
        self.log.line('moved on older numbers: the account it moved to is read the moment its wait for a read ends')

    def _read_candidates(self, snap, ctx, ids=None):
        """A forced switch waits for numbers: read the accounts it could go to now (`ids`, else every one with no fresh
        numbers), once, and decide again."""
        ids = policy.unread_candidates(snap) if ids is None else ids
        if ids:
            self.stage('verifying', lambda: self.poller.poll(ctx, only=ids, fresh_within=VERIFY_FRESH))
            snap = dataclasses.replace(snap, now=self.clock.wall(), readings=usage.readings(now=self.clock.wall()),
                                       settings=load_settings())
        return policy.decide(snap), snap

    def _fresh(self, aid, ctx, snap):
        r = snap.readings.get(aid)
        if r is None:
            return False
        lines = ctx.lines
        times = [w.read_at if w.read_at is not None else r.read_at for w in r.windows
                 if policy.window_line(lines, w.name) is not None]
        return bool(times) and all(t is not None and -usage.AHEAD <= snap.now - t <= VERIFY_FRESH for t in times)

    def _all_out(self, decision, snap):
        """One history line when every account is out (not one per pass), cleared when one has room again."""
        out = decision.action == 'stay' and decision.kind == 'forced' and decision.hold == 'every account is out'
        st = load_state()
        if out and not st.get('all_out'):
            update_state(lambda s: s.update(all_out=dict(usage.stamp(self.clock), said=decision.reason)))
            self.stage('noting', lambda: self.d.history_note(decision.reason, 'limit', h='limit',
                                                             reason='every account is out'))
        elif not out and st.get('all_out'):
            update_state(lambda s: s.update(all_out=None))

    def _disk_full(self):
        """One History line when a switch is held back by a nearly full disk (not one a pass); the spell ends when a
        later pass finds room again (_disk_room)."""
        if load_state().get('disk_full'):
            return
        update_state(lambda s: s.update(disk_full=usage.stamp(self.clock)))
        self.stage('noting', lambda: self.d.history_note(DISK_FULL_WORDS, 'problem', h='problem'))

    def _disk_room(self):
        """The nearly full disk's spell over once there is room again (the disk is looked at only during a spell)."""
        if not load_state().get('disk_full'):
            return
        free = _call(self.d.disk_free)
        if free is not None and free >= DISK_FLOOR:
            update_state(lambda s: s.update(disk_full=None))

    def _settle_cut_off(self, settling):
        """A switch cut off part-way whose record is still there (the start could not check it: the Keychain was locked,
        say) is settled by a later pass with the function the start uses, at most every JOURNAL_AGAIN seconds, and not
        while the Mac settles after a wake. The engine waits for a switch under way to end before it looks."""
        if settling or not os.path.exists(core.data_path(JOURNAL)):
            return
        now_m = self.clock.mono()
        if self.journal_at is not None and 0 <= now_m - self.journal_at < JOURNAL_AGAIN:
            return
        self.journal_at = now_m
        ok, note, err = self.stage('journal', self.d.resolve_journal)
        if ok and note:
            self.log.line('journal: %s' % _no_emails(str(note)))
        elif err:
            self.log.line('journal not checked: %s' % _no_emails(err))

    def _upkeep(self, settling, offline, load, low_battery):
        self._settle_cut_off(settling)
        now_m = self.clock.mono()
        if settling or offline or low_battery or (load is not None and load > LOAD_HIGH):
            return
        if self.last_upkeep is not None and 0 <= now_m - self.last_upkeep < UPKEEP_EVERY:
            return
        self.last_upkeep = now_m
        ok, notes, err = self.stage('upkeep', lambda: self.d.upkeep_pass(UPKEEP_BUDGET))
        for n in (notes or []) if ok else []:
            self.log.line('upkeep: %s' % _no_emails(str(n)))

    def _delay(self, decision, snap, settling, low_battery, active):
        base = PASS_EVERY * (0.9 + 0.2 * self.rand())
        if low_battery:
            base *= 3.0
        if settling:
            if self.waking:                            # 5 s, 10, 20, 40, then every pass: the network may be gone
                return min(PASS_EVERY, NET_CHECK_EVERY * 2 ** min(max(0, self.wake_tries - 1), 4))
            return max(1.0, (self.settle_until or self.clock.mono()) - self.clock.mono())
        if decision.action == 'stay' and decision.kind == 'forced' and decision.hold in ('every account is out',
                                                                                       'no account has room'):
            first = policy.earliest_refill(snap)
            if first:
                return min(MAX_SLEEP, max(PASS_EVERY, first[0] - snap.now + usage.RESET_SLACK))
            return max(PASS_EVERY, 300.0)
        # The account in use's next read wakes the loop rather than a whole pass later: one due before the next pass,
        # and one that fell due after this pass's reads were chosen (its later stages ran past it). Never sooner than
        # MIN_SLEEP, and not for a read the usage stage looked at and left (Claude Code's own numbers were fresh, say):
        # that one would wake the loop every MIN_SLEEP.
        nxt = self.poller.next_due(active) if active else None
        if nxt is not None:
            until = nxt - self.clock.mono()
            if until < base and (until > 0 or (self.looked_m is not None and nxt > self.looked_m + usage.PACE_SOON)):
                base = max(MIN_SLEEP, until)
        return base

    # a sign-in Claude Code lost (putback.py)

    def _put_back(self):
        """The put-back stage: one look at Claude Code's login, and the put-back when its rule says so. Logs only what
        the look said; a put-back sends its one notification. True when the login was put back."""
        if self.d.put_back is None:
            return False
        ok, res, err = self.stage('putting back', self.d.put_back)
        if not ok:
            self.log.line('put back not looked at: %s' % _no_emails(err))
            return False
        if not isinstance(res, api.PutBack):
            return False
        self.missing_watch = bool(res.watch) and res.action != 'put_back'
        if res.line:
            self.log.line('put back: %s' % _no_emails(res.line))
        if res.action != 'put_back':
            return False
        self.put_back_m = self.clock.mono()
        _call(lambda: self.d.notify(res))
        return True

    def _history_now(self):
        """history.jsonl's signature (one stat): it changes when the page or a terminal records a switch."""
        try:
            s = os.stat(core.data_path('history.jsonl'))
            return (s.st_ino, s.st_mtime_ns, s.st_size)
        except OSError:
            return None

    def _watch_end(self):
        """The monotonic time until which the login is looked at every WATCH_EVERY_S: WATCH_AFTER_SWITCH_S after the
        last switch from any source (since_switch) or the last put-back. None when neither is that recent."""
        ends = []
        if self.put_back_m is not None:
            ends.append(self.put_back_m + WATCH_AFTER_SWITCH_S)
        self.history_sig = self._history_now()
        since = since_switch(load_state(), self.clock, last_history_switch())
        if since is not None:
            ends.append(self.clock.mono() - since + WATCH_AFTER_SWITCH_S)
        end = max(ends) if ends else None
        return end if end is not None and end > self.clock.mono() else None

    def _watch_end_safely(self):
        """_watch_end for the sleep, which runs outside the pass's guard: a failure is said in the log and the sleep
        goes on without the extra looks (the history is looked at again only when it changes, so it is said once)."""
        try:
            return self._watch_end()
        except Exception as e:  # noqa: BLE001 - a look never ends the loop
            self.history_sig = self._history_now()
            self.log.line("when to look at Claude Code's login again could not be worked out: %s" % _err_words(e))
            return None

    def _sleep_look(self):
        """One look at Claude Code's login from the sleep (the put-back stage alone). The sleep runs outside the pass's
        guard, so a look that fails is said in the log and never ends the loop. True when the login was put back."""
        try:
            return self._put_back()
        except Exception as e:  # noqa: BLE001 - a look never ends the loop
            self.log.line("a look at Claude Code's login failed: %s" % _err_words(e))
            return False

    # reads someone asked for (see the module's docstring)

    def _take_request(self):
        """The page's newest request ({'at', 'all'}) when it is one this loop has not taken yet, else None. The file is
        looked at with one stat, and read only when that changed (each request is a new file: write_atomic)."""
        path = core.data_path(REQUEST)
        try:
            s = os.stat(path)
        except OSError:
            return None
        sig = (s.st_ino, s.st_mtime_ns, s.st_size)
        if sig == self.request['sig']:
            return None
        self.request['sig'] = sig
        rec = usage.read_json(path) or {}
        at = _num(rec.get('at'))
        if at is None or at == self.request['at']:
            return None
        self.request['at'] = at
        return {'at': at, 'all': rec.get('all') is not False}

    def _too_soon(self, req):
        """Within REQUEST_GAP of the last request answered with a pass, and asking for no more than it did."""
        return self.answered_m is not None and 0 <= self.clock.mono() - self.answered_m < REQUEST_GAP \
            and not (req['all'] and not self.answered_all)

    def _said(self, req, rep=None):
        """The heartbeat says the request is answered (rep: what the read did; None: nothing was read for it)."""
        self.hb.update(asked={'at': req['at'], 'done': round(self.clock.wall(), 3),
                              'read': len(rep.tried) if rep else 0, 'ok': len(rep.ok) if rep else 0,
                              'held': len(rep.held) if rep else 0})

    def _answer(self, req, ctx):
        """One pass's answer to a request: the account in use (with 'all', every account the pass may read) read now as
        a read someone asked for, before the regular reads. An old request is dropped unsaid; one too soon after the
        last is said answered with nothing read."""
        if not -usage.AHEAD <= self.clock.wall() - req['at'] <= REQUEST_FRESH:
            return
        if self._too_soon(req):
            self._said(req)
            return
        ids = [a for a in ctx.order if a == ctx.active or req['all']]
        ids.sort(key=lambda a: a != ctx.active)               # the account in use first
        ok, rep, _ = self.stage('asked', lambda: self.poller.poll(ctx, only=ids, asked=True))
        self.answered_m, self.answered_all = self.clock.mono(), req['all']
        self._said(req, rep if ok else None)

    def _asked_now(self):
        """While sleeping: True when a new request wants a pass now. One too soon after the last is answered here."""
        try:
            req = self._take_request()
            if req is None or not -usage.AHEAD <= self.clock.wall() - req['at'] <= REQUEST_FRESH:
                return False
            if self._too_soon(req):
                self._said(req)
                return False
        except Exception as e:  # noqa: BLE001 - the sleep runs outside the pass's guard: a request never ends the loop
            self.log.line('a request for a read could not be taken: %s' % _err_words(e))
            return False
        self.asked = req
        return True

    def sleep(self, seconds):
        """Wait for the next pass, beating every BEAT_EVERY; a stop, a sleep of the Mac or a request for a read cuts it
        short. The request file is looked at every REQUEST_LOOK seconds (one stat); the heartbeat keeps its own
        rhythm. For WATCH_AFTER_SWITCH_S after a switch or a put-back, and while the last look found the login missing,
        Claude Code's login is looked at every WATCH_EVERY_S in between (the put-back stage alone); the pass still
        comes when it was due. A switch the page or a terminal records meanwhile shows in history.jsonl (one stat)."""
        end = self.clock.mono() + max(0.0, seconds)
        beat = None
        watch_end = self._watch_end_safely()
        next_look = self.clock.mono() + WATCH_EVERY_S
        while not self.stop.is_set():
            now = self.clock.mono()
            left = end - now
            if left <= 0:
                return
            if beat is None or not 0 <= now - beat < BEAT_EVERY:
                self.hb.beat('sleeping')
                beat = now
                if self.hb.drift >= WAKE_GAP:
                    return                             # the Mac slept: a pass now, with the wake rules
            if self._asked_now():
                return                                 # someone asked for a read: a pass now
            if self._history_now() != self.history_sig:
                watch_end = self._watch_end_safely()
            watching = self.missing_watch or (watch_end is not None and now < watch_end)
            if watching and now >= next_look:
                if self._sleep_look():
                    watch_end = self._watch_end_safely()
                next_look = self.clock.mono() + WATCH_EVERY_S
                self.hb.beat('sleeping')
                continue
            step = min(REQUEST_LOOK, left, BEAT_EVERY - (now - beat))
            if watching:
                step = min(step, next_look - now)
            # a difference of two clock readings can be smaller than the clock's own resolution (floating point):
            # waiting that long would not move a clock and the wait would never end
            if self.clock.wait(self.stop, max(step, 0.01)):
                return

    def start(self):
        """Once at start: finish or roll back a switch that was cut off (the account engine's journal)."""
        self.hb.beat('starting')
        ok, note, err = self.stage('journal', self.d.resolve_journal)
        if ok and note:
            self.log.line('journal: %s' % _no_emails(str(note)))
        elif err:
            self.log.line('journal not checked: %s' % _no_emails(err))

    def run(self):
        self.start()
        while not self.stop.is_set():
            try:
                delay = self.run_pass().delay
            except Exception as e:  # noqa: BLE001 - a pass that fails never ends the loop
                self.log.line('pass failed: %s' % _err_words(e))
                delay = PASS_EVERY
            self.sleep(delay)
        self.hb.update(stage='stopped', words=STAGE_WORDS['stopped'], wall=round(self.clock.wall(), 3),
                       mono=round(self.clock.mono(), 3))


def on_stop(loop):
    """SIGTERM or SIGINT: the pass under way ends after the stage it is in (no new stage, switch or refresh starts; a
    switch in progress finishes), then the loop exits. A refresh already sent still saves its answer: tokens.py waits
    for it at exit."""
    loop.stop.set()
    tokens.STOPPING.set()


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m switchboard.accounts.autoloop',
                                description="Switchboard's automatic switching loop.")
    p.add_argument('--once', action='store_true', help='one pass; exit 0 switched, 1 error, 3 blocked, 4 nothing to '
                                                       'do, 5 another loop is running')
    p.add_argument('--dry-run', action='store_true', help='decide and say, never switch')
    p.add_argument('--after-restart', action='store_true', help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    marker = take_restart_marker()
    loop = Loop(dry_run=a.dry_run, after_restart=a.after_restart or marker is not None)
    if not loop.lease.acquire(loop.clock):
        o = lease_owner()
        print('Automatic switching already runs%s.' % (' (process %s)' % o['pid'] if o.get('pid') else ''),
              file=sys.stderr)
        return EXIT_BUSY
    if marker:
        loop.log.line('started again by the watchdog: it was %s' % marker.get('why'))

    def stop(*_):
        on_stop(loop)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if a.once:
            loop.start()
            return loop.run_pass().code
        loop.run()
        return loop.exit_code
    finally:
        loop.lease.release()


if __name__ == '__main__':
    sys.exit(main())
