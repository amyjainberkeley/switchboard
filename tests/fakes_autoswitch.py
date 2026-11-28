"""Fakes for the automatic-switching tests: a clock with wall and monotonic time apart, a scripted network (429 with
Retry-After, timeouts, hangs), a token source, and the account engine's functions (accounts, live, switch, ...).

Nothing here touches the real home, the Keychain or the network. Account emails are made up (*.example).
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone

from switchboard.accounts import api, autoloop, sessions, usage

REAL_WAIT = 30.0         # real seconds a fake join gives a healthy worker (a starved test run can be very slow);
                         # a worker that blocks on purpose registers itself (FakeClock.blocked) and is cut short

# Tests use epoch seconds and compare among themselves, so no zone is needed.
T0 = 1794837600.0        # a fixed wall time (Nov 2026)


def iso(t):
    return None if t is None else datetime.fromtimestamp(t, timezone.utc).isoformat()


class FakeClock(object):
    """Wall and monotonic time that move only when told. slept(s): the Mac slept s seconds (wall moves, monotonic
    does not). step(s): the clock was changed. wait/sleep/join advance both, as if that time passed awake."""

    def __init__(self, wall=T0, mono=50000.0, boot='boot-A'):
        self.w, self.m, self.b = float(wall), float(mono), boot
        self.lock = threading.Lock()
        self.real_wait = REAL_WAIT
        self.blocked = set()             # worker threads that block on purpose (a Keychain read that never answers)

    def block_here(self, event, limit=60):
        """Called inside a fake that hangs: marks this thread as hanging on purpose, then waits on `event`."""
        self.blocked.add(threading.current_thread())
        event.wait(limit)

    def wall(self):
        return self.w

    def mono(self):
        return self.m

    def boot(self):
        return self.b

    def advance(self, s):
        with self.lock:
            self.w += s
            self.m += s

    def slept(self, s):
        with self.lock:
            self.w += s

    def step(self, s):
        with self.lock:
            self.w += s

    def sleep(self, s):
        self.advance(max(0.0, s))

    def wait(self, event, s):
        if event.is_set():
            return True
        self.advance(max(0.0, s))
        return event.is_set()

    def join(self, thread, s):
        end = time.monotonic() + self.real_wait
        while True:
            thread.join(0.02)
            if not thread.is_alive():
                return True
            if thread in self.blocked or time.monotonic() >= end:
                self.advance(max(0.0, s))         # as if `s` seconds passed with no answer
                return False


def usage_body(five=None, week=None, models=None, five_reset=None, week_reset=None, null_five=False):
    d = {}
    if five is not None:
        d['five_hour'] = {'utilization': five, 'resets_at': iso(five_reset)}
    elif null_five:
        d['five_hour'] = None
    if week is not None:
        d['seven_day'] = {'utilization': week, 'resets_at': iso(week_reset)}
    d['limits'] = [{'kind': 'weekly_scoped', 'scope': {'model': {'display_name': name}}, 'percent': pct,
                    'resets_at': iso(r)} for name, (pct, r) in (models or {}).items()]
    d['extra_usage'] = {'is_enabled': True, 'used_credits': 500, 'monthly_limit': 1000, 'utilization': 50.0}
    return d


class FakeNet(object):
    """Scripted answers per token, consumed in order (the last repeats). An answer is (status, headers, body), a
    NetError kind ('network', 'timeout', 'tls-cert'), 'hang' (blocks until released), 'boom' (an unexpected
    exception), or a callable(clock) returning one of those."""

    def __init__(self, clock=None):
        self.clock = clock
        self.script = {}
        self.calls = []
        self.release = threading.Event()
        self.lock = threading.Lock()

    def set(self, token, *answers):
        self.script[token] = list(answers)

    def count(self, token=None):
        return sum(1 for t, _ in self.calls if token is None or t == token)

    def __call__(self, url, headers, timeout):
        assert url == usage.ENDPOINT
        tok = headers['Authorization'].split(' ', 1)[1]
        with self.lock:
            self.calls.append((tok, dict(headers)))
            seq = self.script.get(tok) or [(200, {}, usage_body(week=10.0, week_reset=None))]
            a = seq.pop(0) if len(seq) > 1 else seq[0]
        if callable(a):
            a = a(self.clock)
        if a == 'hang':
            if self.clock is not None:
                self.clock.block_here(self.release, 30)
            else:
                self.release.wait(30)
            raise usage.NetError('network')
        if a == 'boom':
            raise RuntimeError('unexpected')
        if isinstance(a, str):
            raise usage.NetError(a)
        status, hdrs, body = a
        raw = json.dumps(body).encode() if isinstance(body, (dict, list)) else (body or b'')
        return usage.Response(status, hdrs, raw)


class FakeTokens(object):
    """access_token(account_id, fresh=False): 'tok-<id>', or what `answers` says per account."""

    def __init__(self, clock=None):
        self.answers = {}
        self.calls = []
        self.fresh_value = {}
        self.clock = clock
        self.hang = {}                   # account id -> Event: its token fetch hangs until released

    def __call__(self, aid, fresh=False):
        self.calls.append((aid, fresh))
        if aid in self.hang:
            self.clock.block_here(self.hang[aid])
            raise api.AccountsError('keychain_slow', 'no answer')
        if fresh and aid in self.fresh_value:
            return api.Read(api.PRESENT, value=self.fresh_value[aid], provenance='keychain')
        a = self.answers.get(aid)
        if a is not None:
            return a
        return api.Read(api.PRESENT, value='tok-%s' % aid, provenance='keychain')


def account(aid, n, status='ok', email=None):
    return api.Account(id=aid, email=email or '%s@%s.example' % (aid, 'acct'), org_id='org-' + aid, org_name=None,
                       plan='Max', alias=None, order=n, status=status, refresh_expires_at=None, added_at=T0 - 86400,
                       fingerprint='f' * 16)


class FakeEngine(object):
    """The account engine's functions for the loop: accounts, live, switch, keep_pass, upkeep_pass, resolve_journal."""

    def __init__(self, accts, active, clock=None):
        self.accts = list(accts)
        self.active = active
        self.clock = clock
        self.switches = []
        self.live_calls = 0
        self.block_live = None        # an Event: live() waits on it (a Keychain read that never answers)
        self.block_switch = None
        self.live_state = api.PRESENT
        self.degraded = False
        self.fail_switch = None

    def accounts(self):
        return list(self.accts)

    def live(self):
        self.live_calls += 1
        if self.block_live is not None:
            self._block(self.block_live)
        if self.live_state != api.PRESENT:
            return api.Live(state=self.live_state, account_id=None, email=None, proof='none', why='locked')
        a = next((x for x in self.accts if x.id == self.active), None)
        return api.Live(state=api.PRESENT, account_id=self.active, email=a.email if a else None, proof='fingerprint',
                        degraded=self.degraded)

    def switch(self, target, kind='manual', source='cli', reason=''):
        if self.block_switch is not None:
            self._block(self.block_switch)
        if self.fail_switch:
            raise api.AccountsError('keychain_slow', self.fail_switch)
        frm = self.active
        self.switches.append({'to': target, 'kind': kind, 'source': source, 'reason': reason, 'from': frm,
                              'at': self.clock.wall() if self.clock else time.time()})
        self.active = target
        return api.SwitchResult(ok=True, from_id=frm, to_id=target, kind=kind, source=source, reason=reason)

    def _block(self, event):
        if self.clock is not None:
            self.clock.block_here(event)
        else:
            event.wait(60)

    def keep_pass(self):
        return None

    def upkeep_pass(self, budget_s=60.0):
        return []

    def resolve_journal(self, older_than=None):
        return None


class Home(unittest.TestCase):
    """Each test gets its own data folder (SWITCHBOARD_HOME) and Claude Code folder, and nothing carries over."""

    def setUp(self):
        self._old_env = dict((k, os.environ.get(k)) for k in ('SWITCHBOARD_HOME', 'CLAUDE_CONFIG_DIR'))
        self.tmp = tempfile.mkdtemp(prefix='sbauto-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.environ['CLAUDE_CONFIG_DIR'] = os.path.join(self.tmp, 'claude')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        os.makedirs(os.path.join(os.environ['CLAUDE_CONFIG_DIR'], 'projects'))
        sessions._reset_for_tests()
        with usage._HANGING_LOCK:
            del usage._HANGING[:]
            del usage._HANGING_TOKENS[:]
        self.releases = []

    def tearDown(self):
        for ev in self.releases:
            ev.set()
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sessions._reset_for_tests()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def blocker(self):
        ev = threading.Event()
        self.releases.append(ev)
        return ev

    def data(self, name):
        return os.path.join(os.environ['SWITCHBOARD_HOME'], name)


def make_loop(engine, clock, net=None, tokens=None, **deps):
    """A Loop with every outside thing faked (the note of a move that keeps Claude Code signed in goes to loop.moves)."""
    notes = []
    moves = []
    d = autoloop.Deps(accounts=engine.accounts, live=engine.live, switch=engine.switch, keep_pass=engine.keep_pass,
                      upkeep_pass=engine.upkeep_pass, resolve_journal=engine.resolve_journal,
                      access_token=tokens or FakeTokens(), net=net or FakeNet(clock),
                      net_ready=deps.pop('net_ready', lambda: True), load=deps.pop('load', lambda: 0.5),
                      power=deps.pop('power', lambda: (True, 100)), procs=deps.pop('procs', lambda: 1),
                      disk_free=deps.pop('disk_free', lambda: 10 ** 10),
                      history_note=deps.pop('history_note', lambda text, kind='info', **f: notes.append((text, kind))),
                      # the put-back reads Claude Code's login itself: with the live read faked, it is faked too
                      put_back=deps.pop('put_back', lambda: None), notify=deps.pop('notify', lambda res: None),
                      notify_move=deps.pop('notify_move', moves.append))
    loop = autoloop.Loop(deps=d, clock=clock, rand=lambda: 0.5, **deps)
    loop.notes = notes
    loop.moves = moves
    return loop


def set_policy(**kw):
    """Automatic switching settings in settings.json (auto on unless said)."""
    enabled = kw.pop('enabled', True)
    d = dict(session_at=90.0, weekly_at=95.0, model_at=95.0, model=None, strategy='refills-first')
    d.update(kw)
    d['model'] = d['model'] or ''
    autoloop.save_settings(enabled=enabled, **d)
