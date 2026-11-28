import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# One log of what happened (history.jsonl), `switchboard log`, the page's History, the accounts as
# folders of artifacts, Open claude.ai and Chrome setup, the welcome, and few words: the engine in one line.
#
# The engine is a fake, every folder a throwaway one; accounts are made up (*.example).
import collections
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import artifacts, core, demo, engine, keep, server, statusline
import test_app  # its headless-Chrome helper

D, H = 86400, 3600
N, L, M = 'maya@northwind.example', 'maya@ivy.example', 'maya@home.example'


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec='seconds')


def row(n, email, active, week=40, five=10, week_in=3 * D, five_in=2 * H, **more):
    """One account as engine.listing describes it (the app's own row shape)."""
    now = time.time()
    r = engine._row(id='acct-%d' % n, number=n, email=email, active=active,
                    usage={'session': {'pct': five, 'resets': iso(now + five_in), 'pace': None, 'lasts': None},
                           'weekly': {'pct': week, 'resets': iso(now + week_in), 'pace': None, 'lasts': None},
                           'scoped': [], 'spend': None},
                    usage_at=now - 1, source='endpoint')
    r.update(more)
    return r


def history():
    from switchboard import history as h
    return h


def lines_of(path):
    with open(path) as f:
        return [json.loads(x) for x in f if x.strip()]


class Sandbox(unittest.TestCase):
    """A throwaway home, data folder and Claude Code folder; a fake engine; no notification ever shown."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-history-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        self.claude = os.path.join(self.home, '.claude')
        os.makedirs(os.path.join(self.claude, 'projects'))
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': self.claude})
        env.start()
        self.addCleanup(env.stop)
        for k in ('SWITCHBOARD_STATE', 'SWITCHBOARD_NOTIFY_STATE', 'SWITCHBOARD_ENGINE', 'SWITCHBOARD_PORT'):
            os.environ.pop(k, None)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.shown = []
        self.patch(server, 'NOTIFY_RUN', lambda cmd: self.shown.append(cmd) or 0)
        for target, values in ((server.HELPERS, {'awake': None}),
                               (server.USAGE, {'page': None, 'at': 0, 'running': False})):
            p = mock.patch.dict(target, values)
            p.start()
            self.addCleanup(p.stop)
        self.rows = []
        self.auto = {'available': True, 'service': True, 'enabled': True, 'session_at': 90, 'weekly_at': 95,
                     'weekly_set': True, 'strategy': 'refills-first'}
        self.patch(engine, 'rows', lambda: [dict(r) for r in self.rows])
        self.patch(engine, 'listing', lambda: {'rows': [dict(r) for r in self.rows], 'live': None, 'disagree': False})
        self.patch(engine, 'auto_settings', lambda: dict(self.auto))
        self.patch(engine, 'foreign_switcher', lambda folder=None: None)
        self.patch(artifacts, 'refresh', lambda: None)
        self.patch(statusline, 'precompile', lambda *a, **k: None)
        self.host = engine.AutoHost()                 # the auto loop's events, as the page's host keeps them
        self.patch(engine, 'HOST', self.host)
        keep.reset()
        self.addCleanup(keep.reset)

    def patch(self, target, name, value):
        p = mock.patch.object(target, name, value)
        p.start()
        self.addCleanup(p.stop)

    def log(self):
        """Every line of history.jsonl, oldest first."""
        path = os.path.join(self.data, 'history.jsonl')
        return lines_of(path) if os.path.exists(path) else []

    def kinds(self):
        return [(x['kind'], x['text']) for x in self.log()]


# ---------- 1. one log of what happened ----------

class OneLog(Sandbox):
    def test_every_event_also_goes_to_the_log_one_json_object_per_line(self):
        with core.locked() as st:
            core.event(st, 'Keep the Mac awake: off')
            core.event(st, 'Removed %s from Switchboard' % M, 'warn')
        got = self.log()
        self.assertEqual(len(got), 2)
        for x in got:
            self.assertIsInstance(x['t'], float)
            self.assertLess(abs(x['t'] - time.time()), 30)
        self.assertEqual([(x['kind'], x['text']) for x in got],
                         [('setting', 'Keep the Mac awake: off'), ('removed', 'Removed %s from Switchboard' % M)])
        self.assertEqual((got[0]['by'], got[1]['by'], got[1]['account']), ('you', 'you', M))
        # the page's own list keeps its shape and its kinds
        self.assertEqual([(e['kind'], e['text']) for e in core.load()['events']],
                         [('info', 'Keep the Mac awake: off'), ('warn', 'Removed %s from Switchboard' % M)])

    def test_the_file_is_readable_by_you_only_and_only_grows(self):
        h = history()
        h.record('setting', 'one')
        size = os.path.getsize(h.path())
        h.record('setting', 'two')
        self.assertGreater(os.path.getsize(h.path()), size)
        self.assertEqual(os.stat(h.path()).st_mode & 0o777, 0o600)
        with open(h.path()) as f:
            self.assertEqual(f.read().count('\n'), 2)

    def test_each_sentence_switchboard_says_has_its_kind_and_facts(self):
        h = history()
        cases = [
            ('Claude Code is now on %s' % L, 'switch', {'to': L, 'account': L, 'by': 'claude-code'}),
            ('Switched Claude Code to %s' % L, 'switch', {'to': L, 'account': L, 'by': 'you'}),
            ('Kept %s · 3 accounts' % M, 'kept', {'account': M, 'by': 'auto', 'reason': '3 accounts'}),
            ('Kept %s · 1 account' % M, 'kept', {'account': M, 'by': 'auto', 'reason': '1 account'}),
            (keep.AUTO_ON, 'auto', {'on': True, 'by': 'auto'}),
            ('Could not keep %s: macOS\'s Keychain is locked. Trying again in 5 minutes.' % M,
             'problem', {'account': M, 'reason': "could not keep it: macOS's Keychain is locked"}),
            ('Could not keep %s: macOS\'s Keychain is slow to answer. Trying again in a minute.' % M,
             'problem', {'account': M, 'reason': "could not keep it: macOS's Keychain is slow to answer"}),
            ('Restarted automatic switching (it was stuck waiting for the network)', 'auto', {'by': 'auto'}),
            ('Added %s' % M, 'signin', {'account': M, 'by': 'you', 'reason': 'added'}),
            ('Signed in as %s, not %s. It was already here, so nothing new was added.' % (L, M), 'signin',
             {'account': L, 'by': 'you'}),
            ('Added %s, not %s: the browser was signed in as %s.' % (L, M, L), 'signin', {'account': L, 'by': 'you'}),
            ('Opened the sign-in for %s in Google Chrome (Claude home)' % M, 'signin',
             {'account': M, 'by': 'you', 'reason': 'sign-in opened in Google Chrome (Claude home)'}),
            ('Opened the sign-in in your browser', 'signin', {'by': 'you', 'reason': 'sign-in opened in your browser'}),
            ('Matched %s to the Google Chrome profile "Claude Ivy"' % L, 'signin',
             {'account': L, 'by': 'auto', 'reason': 'Google Chrome profile "Claude Ivy"'}),
            ('Made the Google Chrome profile "Claude home" for %s' % M, 'signin', {'account': M, 'by': 'you'}),
            ('Learned that the Google Chrome profile "Work" is signed in as %s' % N, 'signin',
             {'account': N, 'by': 'auto'}),
            ('The browser profile for %s is signed out of claude.ai' % N, 'signin', {'account': N, 'by': 'auto'}),
            ('Learned that "Q4 pricing review" opens as %s' % N, 'info', {'account': N, 'by': 'auto'}),
            ('The sign-in timed out. Press Add account to try again.', 'problem', {}),
            ('The sign-in did not finish. Press Add account to try again.', 'problem', {}),
            ('Removed every account from Switchboard', 'removed', {'by': 'you'}),
            ('Removed %s from Switchboard' % M, 'removed', {'account': M, 'by': 'you'}),
            ('Added the key console (…9f2c)', 'setting', {'by': 'you'}),
            ('Removed the key console', 'setting', {'by': 'you'}),
            ('Keys gateway on', 'setting', {'by': 'you'}),
            ('Notifications: on', 'setting', {'by': 'you'}),
            ('Sonnet for helpers: off', 'setting', {'by': 'you'}),
            ('Models by the job: on', 'setting', {'by': 'you'}),
            ('Status line in Claude Code: on', 'setting', {'by': 'you'}),
            ('Code map on', 'setting', {'by': 'you'}),
        ]
        for text, kind, facts in cases:
            with self.subTest(text=text):
                got_kind, got = h.classify(text)
                self.assertEqual(got_kind, kind)
                for k, v in facts.items():
                    self.assertEqual(got.get(k), v, k)
        # anything else: a warning is a problem, the rest is noted as it is
        self.assertEqual(h.classify('Something new went wrong', 'warn')[0], 'problem')
        self.assertEqual(h.classify('Something new happened')[0], 'info')

    def test_facts_given_by_the_caller_win_over_the_sentence(self):
        with core.locked() as st:
            core.event(st, 'Automatic switching off (5-hour 90%, weekly 95%)', h='auto', on=False, by='you')
            core.event(st, 'Automatic switching on (5-hour 85%, weekly 95%)', h='setting', by='you')
        got = self.log()
        self.assertEqual([(x['kind'], x.get('on'), x['by']) for x in got], [('auto', False, 'you'),
                                                                          ('setting', None, 'you')])

    def test_rotates_at_five_megabytes_keeping_one(self):
        h = history()
        self.assertEqual(h.MAX, 5 * 1024 * 1024)
        self.patch(h, 'MAX', 600)
        for i in range(40):
            h.record('setting', 'line %d' % i)
        self.assertTrue(os.path.exists(h.path() + '.1'))
        self.assertFalse(os.path.exists(h.path() + '.2'))
        self.assertLessEqual(os.path.getsize(h.path()), 600 + 200)
        older, newer = lines_of(h.path() + '.1'), lines_of(h.path())
        self.assertEqual(newer[-1]['text'], 'line 39')
        self.assertEqual(int(older[-1]['text'].split()[1]) + 1, int(newer[0]['text'].split()[1]))   # nothing lost between
        self.assertEqual([x['text'] for x in h.read(5)], ['line %d' % i for i in range(35, 40)])

    def test_reads_back_across_the_two_files_oldest_first(self):
        h = history()
        self.patch(h, 'MAX', 400)
        for i in range(12):
            h.record('setting', 'line %d' % i)
        both = len(lines_of(h.path() + '.1')) + len(lines_of(h.path()))
        got = h.read(both)
        self.assertEqual([x['text'] for x in got], ['line %d' % i for i in range(12 - both, 12)])
        self.assertEqual([x['text'] for x in h.newest(3)], ['line 11', 'line 10', 'line 9'])

    def test_never_a_token_nor_a_control_character_and_never_too_long(self):
        h = history()
        token = 'sk-ant-oat01-' + 'A' * 80
        with core.locked() as st:
            core.event(st, 'The engine said: %s refused\x1b[31m\nnext' % token, 'warn')
            core.event(st, 'x' * 2000)
        got = self.log()
        self.assertNotIn('AAAAAAAAAA', json.dumps(got))
        self.assertIn('sk-ant-…', got[0]['text'])
        self.assertNotIn('\x1b', got[0]['text'])
        self.assertNotIn('\n', got[0]['text'])
        self.assertLessEqual(len(got[1]['text']), 300)
        # an unknown fact is not written; known ones are cleaned the same way
        h.record('info', 'x', reason='y\x07' + token, secret='nope')
        last = h.read(1)[0]
        self.assertNotIn('secret', last)
        self.assertEqual(last['reason'], 'y sk-ant-…')

    def test_the_demo_writes_no_history(self):
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        demo.act('/api/switch', {'email': 'maya@ivy.example'})
        self.assertFalse(os.path.exists(os.path.join(self.data, 'history.jsonl')))

    def test_a_running_switchboard_rewrites_its_history_by_itself(self):
        # tests/_isolate.py's write watch must not count the real Switchboard's own appends as this run's doing.
        self.assertIn('history.jsonl', _isolate.LIVE_REWRITES)
        self.assertIn('history.jsonl.1', _isolate.LIVE_REWRITES)


class Switches(Sandbox):
    """A switch made here says who made it and from where; one the engine made records itself (its automatic loop,
    a hook, a terminal) and is said once; one nobody here made is Claude Code's."""

    def on_n(self):
        """Claude Code has been on N for an hour."""
        self.rows = [row(1, N, True), row(5, L, False)]
        server.tick()
        with core.locked() as st:
            st['timeline'][-1][1] = time.time() - H

    def recorded(self, t, to=L, by='auto', reason='5-hour at 91%'):
        """The engine's own record of a switch it made, at time t."""
        history().record('switch', 'Switched Claude Code to %s' % to, t=t, to=to, account=to, by=by, reason=reason,
                         **{'from': N})

    def test_a_switch_made_here_says_from_where_and_by_you(self):
        self.on_n()
        self.patch(engine, 'switch', lambda target, **kw: None)
        server.switch_to(L)
        last = self.log()[-1]
        self.assertEqual((last['kind'], last['from'], last['to'], last['by'], last['account']),
                         ('switch', N, L, 'you', L))

    def test_a_switch_the_engine_recorded_is_said_once_with_its_reason(self):
        self.on_n()
        now = time.time()
        self.recorded(now - 8)
        self.rows = [row(1, N, False), row(5, L, True)]
        server.tick()
        switches = [x for x in self.log() if x['kind'] == 'switch']
        self.assertEqual([(x['to'], x['by'], x.get('reason')) for x in switches], [(L, 'auto', '5-hour at 91%')])
        self.assertNotIn('Claude Code is now on %s' % L, [e['text'] for e in core.load()['events']])
        self.assertEqual(core.load()['active'], L)

    def test_a_switch_from_before_the_last_change_is_not_this_one(self):
        self.on_n()
        now = time.time()
        self.recorded(now - 590)
        with core.locked() as st:
            st['timeline'][-1][1] = now - 120               # Claude Code came to N after that switch
        self.rows = [row(1, N, False), row(5, L, True)]
        server.tick()
        self.assertEqual(self.log()[-1]['by'], 'claude-code')

    def test_a_switch_noticed_without_the_loop_is_claude_codes(self):
        self.on_n()
        self.rows = [row(1, N, False), row(5, L, True)]
        server.tick()
        last = self.log()[-1]
        self.assertEqual((last['kind'], last['from'], last['to'], last['by']), ('switch', N, L, 'claude-code'))
        self.assertNotIn('reason', last)

    def test_an_old_or_other_switch_record_is_not_this_ones(self):
        self.on_n()
        now = time.time()
        self.recorded(now - 3 * H)                          # long ago
        self.recorded(now - 15, to=M)                       # to another account
        self.recorded(now - 10, by='claude-code')           # noticed, not made, by someone here
        self.rows = [row(1, N, False), row(5, L, True)]
        server.tick()
        self.assertEqual(self.log()[-1]['by'], 'claude-code')


class KeptAndAuto(Sandbox):
    def test_a_login_kept_by_itself(self):
        self.rows = [row(1, N, False)]
        self.patch(keep, 'live', lambda: (M, 'org-m'))
        added = []
        self.patch(engine, 'add_current', lambda: added.append(1))
        t0 = time.time()
        keep.look(self.rows, now=t0)
        keep.look(self.rows, now=t0 + 6)
        self.assertEqual(added, [1])
        last = self.log()[-1]
        self.assertEqual((last['kind'], last['account'], last['by'], last['reason']), ('kept', M, 'auto', '2 accounts'))

    def test_a_login_kept_by_add_through_the_page(self):
        roster = {'1': (N, 'org-n')}
        self.patch(engine, 'saved_identities', lambda: set(roster.values()))
        self.patch(keep, 'live', lambda: (M, 'org-m'))
        self.patch(engine, 'add_current', lambda: roster.update({'2': (M, 'org-m')}))
        server.act('/api/engine', {'add': True})
        last = self.log()[-1]
        self.assertEqual((last['kind'], last['account'], last['by'], last['reason']), ('kept', M, 'you', '2 accounts'))
        n = len(self.log())
        server.act('/api/engine', {'add': True})            # the same login again: brought up to date, not kept anew
        self.assertEqual(len(self.log()), n)

    def test_auto_switch_on_by_itself_says_why(self):
        self.auto['enabled'] = False
        turned = []
        keep.auto_by_itself(2, turn_on=lambda: turned.append(1))
        last = self.log()[-1]
        self.assertEqual((last['kind'], last['on'], last['by'], last['reason']),
                         ('auto', True, 'auto', '2 accounts to switch between'))

    def test_on_or_off_by_you_and_a_new_line_is_a_setting(self):
        self.patch(engine, 'set_auto', lambda **k: None)
        server.act('/api/auto', {'enabled': False})
        server.act('/api/auto', {'session_at': 85})
        got = self.log()[-2:]
        self.assertEqual([(x['kind'], x.get('on'), x['by']) for x in got], [('auto', False, 'you'), ('setting', None, 'you')])


class Limits(Sandbox):
    def tick(self, five, five_in=2 * H, week=40):
        self.rows = [row(1, N, True, week=week, five=five, five_in=five_in), row(5, L, False)]
        server.tick()
        return [x for x in self.log() if x['kind'] == 'limit']

    def test_reaching_a_switch_line_is_said_once(self):
        self.assertEqual(self.tick(80), [])
        got = self.tick(91)
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]['account'], got[0]['reason'], got[0]['window'], got[0]['pct'], got[0]['line']),
                         (N, '5-hour at 91%', '5-hour', 91, 90))
        self.assertEqual(len(self.tick(95)), 1)              # still over: nothing new
        self.assertEqual(len(self.tick(80)), 1)              # back under in the same window (another reading)
        self.assertEqual(len(self.tick(92)), 1)              # and over again: the same window was said already

    def test_an_account_seen_first_already_over_is_not_a_crossing(self):
        self.assertEqual(self.tick(97), [])

    def test_each_window_against_its_own_line(self):
        self.tick(70, week=90)
        self.rows = [row(1, N, True, week=94, five=89), row(5, L, False)]        # under 95 and under 90
        server.tick()
        self.assertEqual([x['reason'] for x in self.log() if x['kind'] == 'limit'], [])
        self.rows = [row(1, N, True, week=96, five=89), row(5, L, False)]
        server.tick()
        got = [x['reason'] for x in self.log() if x['kind'] == 'limit']
        self.assertEqual(got, ['weekly at 96%'])
        self.rows = [row(1, N, True, week=96, five=91), row(5, L, False)]
        server.tick()
        got = [x['reason'] for x in self.log() if x['kind'] == 'limit']
        self.assertEqual(got, ['weekly at 96%', '5-hour at 91%'])

    def test_a_window_that_had_reached_its_line_refills(self):
        self.tick(80)
        self.tick(93)
        got = self.tick(4, five_in=5 * H)                    # the reset moved on: a fresh window
        self.assertEqual([x['reason'] for x in got], ['5-hour at 93%', '5-hour refilled'])
        self.assertEqual(len(self.tick(91, five_in=5 * H)), 3)    # the new window reaching the line is said again


class Problems(Sandbox):
    def test_the_engines_error_is_said_once_when_it_starts(self):
        def broken():
            raise core.SwitchError('The engine did not answer: timed out')
        self.patch(engine, 'listing', broken)
        server.tick()
        server.tick()
        got = [x for x in self.log() if x['kind'] == 'problem']
        self.assertEqual([x['text'] for x in got], ['The engine did not answer: timed out'])

    def test_automatic_switchings_own_problem_too(self):
        self.rows = [row(1, N, True)]
        self.auto['problem'] = "Automatic switching waits: macOS's Keychain is locked. Unlock the Mac."
        server.tick()
        server.tick()
        got = [x['text'] for x in self.log() if x['kind'] == 'problem']
        self.assertEqual(got, ["Automatic switching waits: macOS's Keychain is locked. Unlock the Mac."])


class FromTheTerminal(Sandbox):
    """With the page not running, the terminal's own switch, keep and auto-switch are logged the same way."""

    def setUp(self):
        super().setUp()
        self.patch(cli, 'ask_page', mock.Mock(side_effect=OSError('not running')))
        self.patch(cli, 'page_running', lambda: False)          # never asks a real port
        self.patch(cli, 'page_state', lambda: None)

    def test_a_switch(self):
        self.patch(engine, 'switch', lambda target, **kw: None)
        view = {'accounts': [{'email': N, 'slot': 1, 'active': True}, {'email': L, 'slot': 5, 'active': False}]}
        cli.switch(L, view)
        last = self.log()[-1]
        self.assertEqual((last['kind'], last['from'], last['to'], last['by']), ('switch', N, L, 'you'))
        st = core.load()
        self.assertEqual(st['active'], L)                    # the page, when it starts, does not note it again
        self.rows = [row(1, N, False), row(5, L, True)]
        server.tick()
        self.assertEqual(len([x for x in self.log() if x['kind'] == 'switch']), 1)

    def test_auto_switch_on_or_off(self):
        self.patch(engine, 'set_auto', lambda **k: None)
        with redirect_stdout(io.StringIO()):
            cli.auto_command(['off'])
        last = self.log()[-1]
        self.assertEqual((last['kind'], last['on'], last['by']), ('auto', False, 'you'))

    def test_a_login_kept_by_add(self):
        roster = {'1': (N, 'org-n')}
        self.patch(engine, 'saved_identities', lambda: set(roster.values()))
        self.patch(engine, 'add_current', lambda: roster.update({'2': (M, 'org-m')}))
        cli.save_current()
        last = self.log()[-1]
        self.assertEqual((last['kind'], last['account'], last['by']), ('kept', M, 'you'))

    def test_kept_at_once_by_switchboard_alone_is_by_itself(self):
        roster = {'1': (N, 'org-n')}
        self.patch(engine, 'saved_identities', lambda: set(roster.values()))
        self.patch(engine, 'add_current', lambda: roster.update({'2': (M, 'org-m')}))
        self.patch(cli, 'identity', lambda: (M, 'org-m'))
        self.patch(cli, 'auto_here', lambda say=True: False)
        with redirect_stdout(io.StringIO()):
            cli.keep_now()
        last = self.log()[-1]
        self.assertEqual((last['kind'], last['account'], last['by']), ('kept', M, 'auto'))


# ---------- 2. switchboard log ----------

def _default_interrupt():
    """In a `log -f` child, just before it starts: the interrupt signal's default handling. A child inherits an
    ignored interrupt from whatever started this run (a job started with & from a shell that is not interactive
    ignores it), and then the test's Ctrl-C would never reach it; the result must not depend on how a run was
    started."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)


class LogCommand(Sandbox):
    def fill(self, n, start=None):
        h = history()
        t0 = time.time() - 3 * H if start is None else start
        for i in range(n):
            h.record('setting', 'Keep the Mac awake: %s' % ('on' if i % 2 else 'off'), t=t0 + 60 * i, by='you')

    def run_log(self, *args):
        out = io.StringIO()
        code = history().main(list(args), out)
        return code, out.getvalue()

    def test_the_last_twenty_oldest_first_so_the_newest_is_at_the_bottom(self):
        self.fill(25)
        code, out = self.run_log()
        lines = out.splitlines()
        self.assertEqual((code, len(lines)), (0, 20))
        every = history().read(25)
        self.assertEqual(sorted(x['t'] for x in every), [x['t'] for x in every])
        self.assertTrue(lines[0].startswith(history().when(every[5]['t']) + '  setting  '), lines[0])
        self.assertTrue(lines[-1].startswith(history().when(every[24]['t']) + '  setting  '), lines[-1])
        self.assertTrue(lines[-1].endswith('  you'))

    def test_one_aligned_line_each_like_an_engineers_log(self):
        h = history()
        t = time.time() - H
        h.record('switch', 'Claude Code is now on %s' % L, t=t, account=L, by='auto', reason='5-hour at 91%, refills first',
                 **{'from': N, 'to': L})
        code, out = self.run_log()
        self.assertEqual(out, '%s  switch  %s → %s  auto: 5-hour at 91%%, refills first\n' % (
            time.strftime('%a %H:%M', time.localtime(t)), N, L))
        h.record('kept', 'Kept %s · 3 accounts' % M, t=t + 60, account=M, by='auto', reason='3 accounts')
        h.record('limit', '%s reached its 5-hour switch line: 92%%' % L, t=t + 120, account=L, reason='5-hour at 92%')
        h.record('auto', AUTO_ON_TEXT, t=t + 180, on=True, by='auto', reason='2 accounts to switch between')
        h.record('setting', 'Keep the Mac awake: off', t=t + 240, by='you')
        h.record('problem', 'The sign-in timed out. Press Add account to try again.', t=t + 300)
        code, out = self.run_log()
        lines = out.splitlines()
        self.assertEqual([x.split()[2] for x in lines], ['switch', 'kept', 'limit', 'auto', 'setting', 'problem'])
        tails = ['auto: 5-hour at 91%, refills first', 'auto: 3 accounts', '5-hour at 92%',
                 'auto: 2 accounts to switch between', 'you']
        cols = set(line.index(tail) for line, tail in zip(lines, tails))
        self.assertEqual(len(cols), 1, out)                     # every reason starts in one column
        self.assertIn('  on  ', lines[3])
        self.assertTrue(lines[5].endswith('problem  The sign-in timed out. Press Add account to try again.'), lines[5])

    def test_an_older_line_says_its_date(self):
        h = history()
        t = time.time() - 9 * D
        h.record('setting', 'Notifications: off', t=t, by='you')
        lt = time.localtime(t)
        self.assertTrue(self.run_log()[1].startswith('%s %d %s  ' % (time.strftime('%b', lt), lt.tm_mday,
                                                                     time.strftime('%H:%M', lt))))

    def test_n_lines(self):
        self.fill(8)
        self.assertEqual(len(self.run_log('-n', '5')[1].splitlines()), 5)
        self.assertEqual(len(self.run_log('-n3')[1].splitlines()), 3)
        self.assertEqual(self.run_log('-n', '0'), (0, ''))
        self.assertEqual(len(self.run_log('-n', '100')[1].splitlines()), 8)
        for bad in (['-n'], ['-n', 'x'], ['--bogus'], ['-n', '-3'], ['extra']):
            with self.subTest(args=bad):
                self.assertEqual(self.run_log(*bad), (2, 'switchboard log [-n N] [-f] [--json]\n'))

    def test_json_prints_the_lines_as_they_are_kept(self):
        self.fill(3)
        code, out = self.run_log('--json', '-n', '2')
        got = [json.loads(x) for x in out.splitlines()]
        self.assertEqual(got, history().read(2))
        self.assertEqual(set(got[0]), {'t', 'kind', 'text', 'by'})

    def test_nothing_yet(self):
        self.assertEqual(self.run_log(), (0, 'Nothing yet.\n'))
        self.assertEqual(self.run_log('--json'), (0, ''))

    def test_follow_prints_each_new_line_through_a_rotation(self):
        h = history()
        self.patch(h, 'MAX', 700)
        self.fill(3)
        got, stop = [], threading.Event()
        widths = (9, 7, 30)
        t = threading.Thread(target=h.follow, args=(got.append,), kwargs={'every': 0.02, 'stop': stop, 'widths': widths})
        t.start()
        try:
            time.sleep(0.2)
            for i in range(30):
                h.record('setting', 'line %d' % i, by='you')
                time.sleep(0.01)
            for _ in range(200):
                if len(got) >= 30:
                    break
                time.sleep(0.02)
        finally:
            stop.set()
            t.join(5)
        self.assertTrue(os.path.exists(h.path() + '.1'))         # it rotated meanwhile
        self.assertEqual([x.split('  ')[2].strip() for x in got], ['line %d' % i for i in range(30)])   # none lost, none twice

    def test_follow_as_json(self):
        h = history()
        got, stop = [], threading.Event()
        t = threading.Thread(target=h.follow, args=(got.append,), kwargs={'as_json': True, 'every': 0.02, 'stop': stop})
        t.start()
        try:
            time.sleep(0.15)
            h.record('setting', 'Notifications: on', by='you')
            for _ in range(100):
                if got:
                    break
                time.sleep(0.02)
        finally:
            stop.set()
            t.join(5)
        self.assertEqual([json.loads(x)['text'] for x in got], ['Notifications: on'])

    def child_env(self):
        return dict(os.environ, HOME=self.home, SWITCHBOARD_HOME=self.data, CLAUDE_CONFIG_DIR=self.claude,
                    PYTHONDONTWRITEBYTECODE='1')

    def test_ctrl_c_ends_it_cleanly(self):
        import signal
        import subprocess
        self.fill(1)
        env = self.child_env()
        p = subprocess.Popen([sys.executable, '-m', 'switchboard', 'log', '-f'], cwd=os.path.join(HERE, '..'), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=_default_interrupt)
        try:
            first = p.stdout.readline()                          # it printed the log and now follows it
            p.send_signal(signal.SIGINT)
            out, err = p.communicate(timeout=20)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
        self.assertIn(b'Keep the Mac awake', first)
        self.assertEqual(p.returncode, 0)
        self.assertNotIn(b'Traceback', err)

    def test_a_reader_that_stops_reading_ends_it_quietly(self):
        import subprocess
        self.fill(1)
        env = self.child_env()
        p = subprocess.Popen([sys.executable, '-m', 'switchboard', 'log', '-f'], cwd=os.path.join(HERE, '..'), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=_default_interrupt)
        try:
            p.stdout.readline()
            p.stdout.close()                                    # `switchboard log -f | head -1` once head has its line
            for i in range(3):
                history().record('setting', 'Notifications: %s' % ('on' if i % 2 else 'off'), by='you')
                time.sleep(0.6)
            err = p.stderr.read()
            p.wait(timeout=20)
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
        self.assertEqual(p.returncode, 0)
        self.assertNotIn(b'Traceback', err)
        self.assertNotIn(b'BrokenPipe', err)

    def test_the_command_its_help_and_the_readme(self):
        self.fill(2)
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(['log', '-n', '1'])
        self.assertEqual((code, len(out.getvalue().splitlines())), (0, 1))
        self.assertIn('switchboard log [-n N] [-f] [--json]', cli.__doc__)
        with open(os.path.join(HERE, '..', 'README.md'), encoding='utf-8') as f:
            readme = f.read()
        commands = readme[readme.index('## Usage'):readme.index('## How fresh the numbers are')]
        self.assertIn('switchboard log', commands)
        with open(os.path.join(HERE, '..', 'docs', 'commands.md'), encoding='utf-8') as f:
            self.assertIn('| `switchboard log` |', f.read())           # and the reference, with its flags


AUTO_ON_TEXT = keep.AUTO_ON


# ---------- 3. History for the page ----------

class Served(Sandbox):
    """The real handler on a free port of this Mac, asked like the page asks it."""

    def setUp(self):
        super().setUp()
        from http.server import ThreadingHTTPServer
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        port = self.httpd.server_address[1]
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(port)})
        env.start()
        self.addCleanup(env.stop)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:%d' % port

    def get(self, path):
        import urllib.request
        import pagekey_help                     # the page key, as Switchboard's own programs send it
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(self.base + path, headers=pagekey_help.with_key()), timeout=10) as r:
            return json.load(r)


class HistoryLinesWrap(unittest.TestCase):
    def test_what_happened_wraps_and_is_never_cut(self):
        # At phone width a cut "a@x.example → b@x…" hid the account switched to (seen at 390 px).
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            page = f.read()
        rule = page[page.index('.hist .w {'):]
        rule = rule[:rule.index('}')]
        self.assertNotIn('nowrap', rule)
        self.assertNotIn('ellipsis', rule)
        self.assertIn('overflow-wrap: anywhere', rule)


class HistoryApi(Served):
    def test_the_last_two_hundred_newest_first_and_where_the_work_went(self):
        h = history()
        t0 = time.time() - 2 * D
        for i in range(250):
            h.record('setting', 'line %d' % i, t=t0 + i, by='you')
        self.patch(server, 'usage_view', lambda fresh=False: {'days': 7, 'cuts': {'work': [{'label': 'Website', 'share': 80}]},
                                                             'state': 'ready'})
        got = self.get('/api/history')
        self.assertEqual(len(got['entries']), 200)
        self.assertEqual([x['text'] for x in got['entries'][:2]], ['line 249', 'line 248'])
        self.assertEqual(got['entries'][-1]['text'], 'line 50')
        self.assertEqual((got['entries'][0]['subject'], got['entries'][0]['tail']), ('line 249', 'you'))
        self.assertEqual(got['work']['cuts']['work'][0]['label'], 'Website')

    def test_the_state_carries_the_newest_lines_for_the_page(self):
        h = history()
        for i in range(15):
            h.record('setting', 'line %d' % i, by='you')
        st = self.get('/api/state')
        self.assertEqual([x['text'] for x in st['history']], ['line %d' % i for i in range(14, 2, -1)])   # 12, newest first
        self.assertEqual(set(st['history'][0]) >= {'t', 'kind', 'text', 'subject', 'tail'}, True)

    def test_the_demo_has_a_made_up_history(self):
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        got = self.get('/api/history')
        self.assertGreaterEqual(len(got['entries']), 10)
        ts = [x['t'] for x in got['entries']]
        self.assertEqual(ts, sorted(ts, reverse=True))
        kinds = set(x['kind'] for x in got['entries'])
        self.assertTrue({'switch', 'kept', 'auto', 'limit', 'signin', 'problem'} <= kinds, kinds)
        auto = next(x for x in got['entries'] if x['kind'] == 'switch' and x.get('by') == 'auto')
        self.assertIn('refills first', auto['tail'])
        for x in got['entries']:
            for k in ('account', 'from', 'to'):
                self.assertTrue(x.get(k) is None or x[k].endswith('.example'), x)
        again = demo.usage()                    # the made-up week moves with the clock: the cuts and the keys don't
        self.assertEqual(set(got['work']), set(again))
        self.assertEqual(dict((k, got['work'][k]) for k in ('days', 'cuts', 'moved', 'claude_mem')),
                         dict((k, again[k]) for k in ('days', 'cuts', 'moved', 'claude_mem')))
        self.assertEqual([x['text'] for x in self.get('/api/state')['history']], [x['text'] for x in got['entries'][:12]])


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class HistoryInThePage(test_app.ChromePage):
    """The page's History, in headless Chrome (a fresh profile each run) on the demo's made-up accounts."""

    def test_history_comes_first_then_where_the_work_went(self):
        # History is the Activity view (#activity); Work is in your week, under the accounts
        got = self.run_page("""
      await wait(800);
      location.hash = '#activity'; await wait(1200);
      const sec = $('#history');
      const lines = all('#history-log > li');
      done({head: sec.querySelector('h2').textContent, n: lines.length,
            first: lines[0] && [lines[0].querySelector('.k').textContent, lines[0].querySelector('.w').textContent,
                                lines[0].querySelector('.why').textContent],
            switchLine: (lines.find((l) => l.dataset.kind === 'switch' && /auto:/.test(l.textContent)) || {}).textContent || null,
            more: ($('#history-more') || {}).textContent || null,
            work: ($('#wk-mults') || {}).textContent || '', inActivity: !!$('#sec-activity #work, #sec-activity .mults'),
            weekUnderAccounts: !!$('#sec-accounts #week'),
            went: !!$('#h-went'), oldWords: $('main').textContent.includes('Where it went')});
""")
        entries = demo.history()['entries']
        self.assertEqual(got['head'], 'History')
        self.assertEqual(got['n'], 10)                                   # about ten lines
        self.assertEqual(got['first'], [entries[0]['kind'], entries[0]['subject'], entries[0]['tail']])
        self.assertIn('auto: ', got['switchLine'])
        self.assertIn('refills first', got['switchLine'])
        self.assertIn('switchboard log', got['more'])
        self.assertTrue(got['more'].startswith('All'), got['more'])
        self.assertIn('Website', got['work'])                            # the week's work is under the accounts
        self.assertFalse(got['inActivity'])
        self.assertTrue(got['weekUnderAccounts'])
        self.assertFalse(got['went'])
        self.assertFalse(got['oldWords'])

    def test_all_shows_every_line_the_server_keeps(self):
        got = self.run_page("""
      await wait(800);
      location.hash = '#activity'; await wait(800);
      $('#history-more [data-do="histAll"]').click(); await wait(1500);
      const n = all('#history-log > li').length, label = $('#history-more [data-do="histAll"]').textContent;
      $('#history-more [data-do="histAll"]').click(); await wait(400);
      done({n, label, after: all('#history-log > li').length});
""")
        self.assertEqual(got['n'], len(demo.history()['entries']))
        self.assertEqual(got['label'], 'Fewer')
        self.assertEqual(got['after'], 10)


# ---------- 4. accounts as folders ----------

def art(i, title, owner, age, src='seen', profile=None):
    now = time.time()
    return {'id': 'art%013d' % i, 'url': 'https://claude.ai/artifact/art%013d000' % i, 'title': title, 'name': None,
            'owner': owner, 'src': src if owner else None, 't': now - age, 'first': now - age, 'ot': now,
            'profile': profile}


class FoldersApi(Served):
    def setUp(self):
        super().setUp()
        self.rows = [row(1, N, True), row(2, L, False), row(3, M, False)]
        server.tick()
        with core.locked() as st:
            st['accounts'][N]['profile'] = 'Profile 1'
            st['accounts'][L]['profile'] = 'Claude Ivy'
            st['artifacts'] = [art(1, 'Q4 pricing review', N, 1 * H), art(2, 'Trip budget', L, 3 * H),
                               art(3, 'Weekly sync', N, 2 * D, profile='Profile 9'), art(4, 'Launch notes', None, 5 * H),
                               art(5, 'Old plan', 'gone@old.example', 9 * D), art(6, '', N, 3 * D, src='guess')]
        self.patch(browser_mod(), 'profiles', lambda: {'Profile 1': 'Work', 'Claude Ivy': 'Claude Ivy',
                                                       'Profile 9': 'Spare'})

    def test_one_folder_per_account_and_one_not_matched_yet(self):
        got = self.get('/api/artifacts')
        self.assertEqual([f['name'] for f in got['folders']], [N, L, M, 'gone@old.example', 'Not matched yet'])
        self.assertEqual([f['count'] for f in got['folders']], [3, 1, 0, 1, 1])
        self.assertEqual(got['count'], 6)
        north = got['folders'][0]
        self.assertEqual(north['email'], N)
        self.assertAlmostEqual(north['changed'], time.time() - H, delta=60)              # its newest change
        self.assertEqual([x['title'] for x in north['files']], ['Q4 pricing review', 'Weekly sync', ''])   # newest first
        self.assertIsNone(got['folders'][2]['changed'])                                   # an account with nothing yet
        self.assertEqual((got['folders'][-1]['email'], got['folders'][-1]['id']), (None, 'unmatched'))

    def test_each_file_has_its_title_change_link_owner_and_profile(self):
        files = dict((x['title'], x) for f in self.get('/api/artifacts')['folders'] for x in f['files'])
        q4 = files['Q4 pricing review']
        self.assertEqual(set(q4) >= {'id', 'title', 'changed', 'url', 'owner', 'profile', 'profile_name'}, True)
        self.assertEqual((q4['owner'], q4['profile'], q4['profile_name']), (N, 'Profile 1', 'Work'))   # its owner's
        self.assertEqual(q4['url'], 'https://claude.ai/artifact/art0000000000001000')
        self.assertEqual((files['Weekly sync']['profile'], files['Weekly sync']['profile_name']), ('Profile 9', 'Spare'))
        self.assertEqual((files['Launch notes']['owner'], files['Launch notes']['profile_name']), (None, ''))
        self.assertEqual(files['']['sure'], False)                                         # only a guess near a switch
        self.assertEqual(q4['sure'], True)

    def test_the_demo_has_folders_too(self):
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        got = self.get('/api/artifacts')
        self.assertEqual([f['email'] for f in got['folders']][:5], ['maya@northwind.example', 'maya.reyes@mail.example',
                                                                      'maya@fernhill.example', 'm.reyes@harbor.example',
                                                                      'maya@ivy.example'])
        self.assertEqual(got['folders'][-1]['name'], 'Not matched yet')
        self.assertEqual(sum(f['count'] for f in got['folders']), len(demo.STATE['artifacts']))


def browser_mod():
    from switchboard import browser
    return browser


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class FoldersInThePage(test_app.ChromePage):
    """The folders where past-chat search was; a folder's files, each opened as its owner in one press; the find box."""

    def test_folders_where_the_search_was_and_the_search_under_more_settings(self):
        # The folders are the Artifacts view, each named by its account's whole email (its Chrome profile in the
        # tooltip); past-chat search is in Activity
        got = self.run_page("""
      await wait(800);
      location.hash = '#artifacts'; await wait(1200);
      const folders = all('#files-list .folder').map((b) => [b.querySelector('.fname').textContent, b.querySelector('.fcount').textContent, b.title]);
      const head = $('#h-art').textContent, icons = all('#files-list .folder svg').length, placeholder = $('#files-find').placeholder;
      location.hash = '#activity'; await wait(600);
      const mem = $('#memory'), memShown = !mem.hidden && !mem.closest('[hidden]');
      const q = $('#mq'); q.focus(); q.value = 'login'; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(1500);
      done({head, folders, icons, memShown, inActivity: !!mem.closest('#sec-activity'), results: all('#results .res').length, placeholder});
""")
        self.assertEqual(got['head'], 'Artifacts')
        names = [f[0] for f in got['folders']]
        self.assertEqual(names, ['maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                                 'm.reyes@harbor.example', 'maya@ivy.example', 'Not matched yet'])
        self.assertEqual([f[2].split(',')[0] for f in got['folders'][:5]], ['maya@northwind.example', 'maya.reyes@mail.example',
                                                                             'maya@fernhill.example', 'm.reyes@harbor.example',
                                                                             'maya@ivy.example'])
        self.assertEqual(got['folders'][0][1], '4 artifacts')
        self.assertEqual(got['icons'], 6)
        self.assertTrue(got['memShown'])
        self.assertTrue(got['inActivity'])
        self.assertGreater(got['results'], 0)                       # past-chat search still works there
        self.assertEqual(got['placeholder'], 'Find an artifact, or paste its link')

    def test_a_folder_opens_on_its_files_and_one_press_opens_a_file_as_its_owner(self):
        # A press on an artifact opens it, as Open as owner did; nothing unfolds first.
        got = self.run_page("""
      await wait(800);
      location.hash = '#artifacts'; await wait(1200);
      all('#files-list .folder').find((b) => b.title.startsWith('maya@northwind.example')).click(); await wait(400);
      const files = all('#files-list a.tile').map((b) => b.querySelector('.tt span').textContent);
      const title = [$('#h-art').textContent, $('#art-lede').textContent];
      all('#files-list a.tile')[0].click(); await wait(1200);
      const opened = sent.filter((x) => x[0] === '/api/go').map((x) => x[1]);
      const preview = !!$('#files-list .preview'), say = $('#toasts').textContent;
      $('#h-art .crumb').click(); await wait(400);
      done({files, title, opened, preview, say, back: all('#files-list .folder').length});
""")
        own = sorted((x for x in demo.STATE['artifacts'] if x['owner'] == 'maya@northwind.example'), key=lambda x: -x['t'])
        self.assertEqual(got['files'], [x['title'] for x in own])
        self.assertEqual(got['title'][0], 'Artifacts›maya@northwind.example')   # the folder is its owner's
        self.assertIn('each opens as maya@northwind.example', got['title'][1])
        self.assertEqual(got['opened'], [{'url': own[0]['url']}])
        self.assertFalse(got['preview'])
        self.assertIn('Opened as maya@northwind.example.', got['say'])
        self.assertEqual(got['back'], 6)

    def test_the_find_box_filters_and_takes_a_pasted_link(self):
        got = self.run_page("""
      await wait(800);
      location.hash = '#artifacts'; await wait(1200);
      const f = $('#files-find');
      const type = async (v) => { f.value = v; f.dispatchEvent(new Event('input', {bubbles: true})); await wait(250); };
      await type('brand');
      const found = all('#files-list a.tile').map((b) => [b.querySelector('.tt span').textContent, b.getAttribute('aria-label')]);
      await type('zzzz');
      const none = $('#files-list').textContent;
      await type('https://claude.ai/artifact/0f1e2d3c4b5a69788796a5b4c3d2e1f0');
      const pasted = $('#files-list').textContent, btn = $('#files-list [data-do="open"]').dataset.url;
      await type('');
      done({found, none, pasted, btn, folders: all('#files-list .folder').length});
""")
        self.assertEqual(got['found'], [['Brand guide draft', 'Open Brand guide draft as maya@fernhill.example']])
        self.assertEqual(got['none'], 'Nothing matches')
        self.assertIn('Not matched yet', got['pasted'])                 # one tile for it, opened as its owner on a click
        self.assertEqual(got['btn'], 'https://claude.ai/artifact/0f1e2d3c4b5a69788796a5b4c3d2e1f0')
        self.assertEqual(got['folders'], 6)


# ---------- 5. Open claude.ai, the reading's age, Chrome setup ----------

EXT = 'fcoeoabgfenejglbffodgkkbkcdhcgfn'


class ChromeSetupFacts(Sandbox):
    def chrome(self, profiles, with_extension=()):
        from switchboard import browser
        d = os.path.join(browser.support(), browser.BROWSERS['chrome']['dir'])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'Local State'), 'w') as f:
            json.dump({'profile': {'info_cache': dict((p, {'name': n}) for p, n in profiles.items())}}, f)
        for p in profiles:
            os.makedirs(os.path.join(d, p, 'Extensions'), exist_ok=True)
        for p in with_extension:
            os.makedirs(os.path.join(d, p, 'Extensions', EXT, '1.0.102_0'))
        app = os.path.join(self.home, 'Applications', 'Google Chrome.app')
        os.makedirs(app, exist_ok=True)
        browser.APPS.clear()
        self.addCleanup(browser.APPS.clear)
        return d

    def test_which_profiles_have_the_claude_extension(self):
        from switchboard import browser
        self.chrome({'Profile 1': 'Work', 'Claude Ivy': 'Claude Ivy'}, with_extension=['Profile 1'])
        self.assertEqual(browser.CLAUDE_EXTENSION, EXT)
        self.assertEqual(browser.extensions(), {'Profile 1': True, 'Claude Ivy': False})

    def test_each_account_says_its_profile_its_sign_in_and_the_extension(self):
        self.chrome({'Profile 1': 'Work', 'Claude Ivy': 'Claude Ivy', 'P3': 'Three'}, with_extension=['Profile 1'])
        self.rows = [row(1, N, True), row(2, L, False), row(3, M, False), row(4, 'ana@four.example', False)]
        server.tick()
        with core.locked() as st:
            st['accounts'][N].update(profile='Profile 1', profile_src='checked')
            st['accounts'][L].update(profile='Claude Ivy', profile_src='hint', signed_out=time.time())
            st['accounts'][M].update(profile='P3', profile_src='you')
        got = dict((a['email'], a['chrome']) for a in server.view()['accounts'])
        self.assertEqual(got[N], {'profile': 'Profile 1', 'signed_in': True, 'extension': True})
        self.assertEqual(got[L], {'profile': 'Claude Ivy', 'signed_in': False, 'extension': False})
        self.assertEqual(got[M], {'profile': 'P3', 'signed_in': None, 'extension': False})     # chosen, not proven yet
        self.assertEqual(got['ana@four.example'], {'profile': None, 'signed_in': None, 'extension': None})

    def test_open_the_extensions_page_in_the_accounts_own_profile(self):
        from switchboard import browser
        self.rows = [row(1, N, True)]
        server.tick()
        with core.locked() as st:
            st['accounts'][N].update(profile='Profile 1', profile_src='checked')
        opened = []
        self.patch(browser, 'open_url', lambda profile, url, background=False: opened.append((profile, url)))
        server.act('/api/open', {'email': N, 'extension': True})
        server.act('/api/open', {'email': N})
        self.assertEqual(opened, [('Profile 1', 'https://chromewebstore.google.com/detail/claude/' + EXT),
                                  ('Profile 1', 'https://claude.ai/new')])
        with self.assertRaises(core.SwitchError):
            server.act('/api/open', {'email': N, 'url': 'https://chromewebstore.google.com/detail/other/x'})


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class OpenClaudeAiAndChromeSetupInThePage(test_app.ChromePage):
    def test_each_account_opens_claude_ai_in_its_profile_from_its_card(self):
        # From the row's menu (and a signed-out profile's own line on the row)
        got = self.run_page("""
      await wait(1200);
      const item = (e) => { document.querySelector('[data-do="menu"][data-email="' + e + '"]').click(); return $('#menu [data-do="site"]'); };
      const words = ['maya@northwind.example', 'm.reyes@harbor.example'].map((e) => { const b = item(e); const w = b ? b.firstElementChild.textContent : null; document.body.click(); return w; });
      item('maya@fernhill.example').click(); await wait(800);
      done({words, sent: sent.map((x) => x[0] + ' ' + JSON.stringify(x[1])), h1: $('h1').textContent});
""")
        self.assertEqual(got['words'], ['Open claude.ai', 'Sign in to claude.ai'])   # harbor's profile is signed out
        self.assertEqual(got['sent'], ['/api/open {"email":"maya@fernhill.example"}'])   # it opened; it did not switch
        self.assertEqual(got['h1'], 'Claude Code is on maya@northwind.example')

    def test_a_reading_seven_minutes_old_says_so(self):
        demo._ready()
        # a reading under 15 minutes old is covered by the title line; past that, it says its age under its numbers
        demo.STATE['accounts']['maya@fernhill.example']['usage_at'] = time.time() - 7 * 60
        demo.STATE['accounts']['maya@ivy.example']['usage_at'] = time.time() - 17 * 60
        got = self.run_page("""
      await wait(800);
      const age = (e) => (all('#acc .arow').find((b) => b.querySelector('.em').textContent === e).querySelector('.note[data-kind="age"]') || {}).textContent || '';
      done([age('maya@fernhill.example'), age('maya@ivy.example')]);
""")
        self.assertEqual(got, ['', 'checked 17 min ago'])

    def test_chrome_setup_once_per_account_under_more_settings(self):
        # Settings › Links › Chrome profiles, one line per account: a matched profile is a fact (its link and
        # Claude in Chrome there in its tooltip), anything that needs the person says so with one action; Claude in
        # Chrome's own row offers the extension where it is missing.
        got = self.run_page("""
      location.hash = '#settings'; await wait(1200);
      const rows = all('#sec-settings .pairs li').map((r) => ({email: r.querySelector('.pk').textContent,
        pick: !!r.querySelector('select[data-change="profile"]'), ok: !!r.querySelector('.pv > svg'),
        steps: (r.querySelector('.pv').title || '').split('\\n'), said: (r.querySelector('.pv .ln') || {}).textContent || '',
        act: [].slice.call(r.querySelectorAll('.pv button')).map((b) => b.textContent)}));
      const add = all('#row-chrome [data-do="addExtension"]').find((b) => b.textContent === 'maya@fernhill.example');
      add.click(); await wait(800);
      done({item: [$('#gh-links').textContent, $('#row-profiles .r-title').textContent], rows,
            sent: sent.filter((x) => x[0] === '/api/extension').map((x) => x[1]), check: !!$('#sec-settings [data-do="check"]')});
""")
        self.assertEqual(got['item'], ['Links', 'Chrome profiles'])
        rows = dict((r['email'], r) for r in got['rows'])
        self.assertEqual(list(rows), ['maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                                      'm.reyes@harbor.example', 'maya@ivy.example'])
        self.assertTrue(all(r['pick'] for r in got['rows']))
        self.assertEqual(rows['maya@northwind.example']['steps'],
                         ['Opens in the Google Chrome profile "Work".',
                          'Claude in Chrome: ready in "Work" (keep Google Chrome open in that profile).'])
        self.assertTrue(rows['maya@northwind.example']['ok'])
        self.assertEqual(rows['maya@fernhill.example']['steps'],
                         ['Opens in the Google Chrome profile "Claude Fernhill".',
                          'Claude in Chrome: the extension is not in "Claude Fernhill".'])
        client = rows['m.reyes@harbor.example']
        self.assertFalse(client['ok'])
        self.assertEqual(client['said'], 'The Google Chrome profile "Claude Harbor" is signed out of claude.ai. Sign in there once.')
        self.assertEqual(client['act'], ['Sign in there'])
        self.assertIn('Claude in Chrome: the profile "Claude Harbor" is signed out of claude.ai.', client['steps'])
        self.assertEqual(got['sent'], [{'profile': 'Claude Fernhill'}])
        self.assertTrue(got['check'])

    def test_activity_is_the_history_and_settings_lists_nothing_recent(self):
        got = self.run_page("""
      location.hash = '#settings'; await wait(900);
      const subs = $('#g-accounts').textContent;
      location.hash = '#activity'; await wait(900);
      done({recent: !!document.getElementById('sLog'), subs, history: all('#history-log > li').length,
            nav: ($('#nav a[href="#activity"]') || {}).textContent || ''});
""")
        self.assertFalse(got['recent'])
        self.assertNotIn('Recent', got['subs'])
        self.assertEqual(got['history'], 10)
        self.assertTrue(got['nav'].startswith('Activity'), got['nav'])         # its own place in the sidebar


# ---------- 6. the welcome ----------

class Welcome(Sandbox):
    def who(self, gecos):
        import pwd
        real = pwd.getpwuid(os.getuid())
        fake = collections.namedtuple('P', 'pw_name pw_gecos pw_dir pw_uid')('maya', gecos, real.pw_dir, real.pw_uid)
        self.patch(server, 'getpwuid', lambda uid: fake)
        server.FIRST_NAME.clear()

    def test_the_first_word_of_this_macs_full_name(self):
        for gecos, name in (('Maya Reyes', 'Maya'), ('Maya Reyes,Room 4,555-0100,', 'Maya'), ('  Jean-Luc  Picard', 'Jean-Luc'),
                            ('', None), (',,,', None), ('Ma\x1bya\x07 R', 'Maya')):
            with self.subTest(gecos=gecos):
                self.who(gecos)
                self.assertEqual(server.first_name(), name)

    def test_the_state_carries_it(self):
        self.who('Maya Reyes')
        self.assertEqual(server.view()['first_name'], 'Maya')
        self.who('')
        self.assertIsNone(server.view()['first_name'])

    def test_the_demo_is_maya(self):
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        self.assertEqual(server.view()['first_name'], 'Maya')


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class WelcomeInThePage(test_app.ChromePage):
    def test_the_top_line_says_welcome_with_the_first_name(self):
        # The top line starts with Switchboard's mark and name. The board is drawn
        # without a welcome line: the mark and name head the sidebar, and the welcome greets the first run.
        got = self.run_page("""
      await wait(800);
      done({brand: $('#brand .brand').textContent, hello: !!$('#hello'), h1: $('#h-live').textContent});
""")
        self.assertEqual(got, {'brand': 'Switchboard', 'hello': False, 'h1': 'Claude Code is on maya@northwind.example'})
        demo._ready()
        for e, a in demo.STATE['accounts'].items():
            a['connected'] = e == 'maya@northwind.example'
        demo.STATE['accounts']['maya@northwind.example']['unmanaged'] = True
        demo.FACTS.update(setup_hidden=False)
        got = self.run_page("""
      await wait(800);
      done({first: $('#hello').textContent, brand: $('#brand .brand').textContent});
""")
        self.assertEqual(got, {'first': 'Welcome, Maya', 'brand': 'Switchboard'})

    def test_without_a_name_it_is_just_welcome_and_the_first_run_has_it_too(self):
        demo._ready()
        demo.FACTS['first_name'] = None
        for e, a in demo.STATE['accounts'].items():
            a['connected'] = e == 'maya@northwind.example'
        demo.STATE['accounts']['maya@northwind.example']['unmanaged'] = True
        demo.FACTS.update(setup_hidden=False)
        got = self.run_page("""
      await wait(800);
      done({first: $('#hello').textContent, brand: $('#brand .brand').textContent,
            title: ($('.fr-title') || {}).textContent});
""")
        self.assertEqual(got['first'], 'Welcome')
        self.assertEqual(got['brand'], 'Switchboard')
        self.assertTrue(got['title'].startswith('Claude Code is on'), got)


# ---------- 7. few words: the engine in one line, settings no more than their controls ----------

class FewWords(Sandbox):
    def test_the_engine_is_one_line(self):
        from switchboard import more
        self.patch(engine, 'saved_identities', lambda: {(N, ''), (L, '')})
        self.patch(engine, 'health', lambda: {'keychain': 'ok', 'journal': None})
        self.assertEqual(more.status(), ['Switchboard %s keeps 2 accounts.' % core.VERSION])
        self.assertEqual(demo.more()['status'], ['Switchboard %s keeps 5 accounts.' % core.VERSION])
        self.patch(engine, 'health', lambda: {'keychain': {'ok': False, 'line': "macOS's Keychain is locked."}})
        self.assertEqual(more.status()[1:], ["macOS's Keychain is locked."])        # only what is wrong is said

    def test_more_settings_says_no_more_than_its_controls(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            page = f.read()
        for gone in ('For when every account is out', 'plain <code>claude</code> stays on your accounts',
                     'A subscription login is always refused', 'is never shown again',
                     'A terminal started in that folder, or below it, opens as the account',
                     'A login a switch or a repair had to replace is kept here',
                     'Takes every account and its saved login out of Switchboard'):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, page)
        for kept in ('Only <code>', 'Kept in your Keychain', 'Remove everything'):    # what a control cannot show
            self.assertIn(kept, page)


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class FewWordsInThePage(test_app.ChromePage):
    def test_engine_settings_ends_on_one_engine_line(self):
        got = self.run_page("""
      location.hash = '#settings'; await wait(1200);
      const g = $('#g-accounts');
      done({subs: [g.querySelector('.g-note').textContent], hints: [...g.querySelectorAll('.hint')].map((x) => x.textContent)});
""")
        self.assertIn('Switchboard %s keeps 5 accounts.' % core.VERSION, got['subs'])
        self.assertFalse([h for h in got['hints'] if 'Python' in h or 'Keychain;' in h or 'process' in h], got['hints'])


if __name__ == '__main__':
    unittest.main()
