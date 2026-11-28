"""`switchboard run --roomiest`: a run as the account with the most room that auto-switch is not about to leave, in
place of a named one. The pick (runas.roomiest) is the landing of a move a person asks for (policy.best), ranked by
room, with the account Claude Code is on among the candidates; the command names the account on standard error and
runs as it, as `switchboard run NAME` does. Made-up accounts only."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import io
import os
import shutil
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import __main__ as cli
from switchboard import core
from switchboard.accounts import api, autoloop, policy, runas
from fakes_accounts_api import install
from fakes_autoswitch import account
from test_autoswitch_policy import ACCTS, NOW, reading, snap

D = 86400.0
# a is the account Claude Code is on. Room is the least of (line - percent) over its windows (90 for the 5-hour
# window, 95 for the week): a 70, b 35, c 75.
R = {'a': reading('a', five=20.0, week=20.0),
     'b': reading('b', five=10.0, week=60.0, week_reset=NOW + 1 * D),
     'c': reading('c', five=10.0, week=20.0, week_reset=NOW + 3 * D)}


def on(**kw):
    return policy.Settings(enabled=True, **kw)


def pick(readings=R, **kw):
    d = runas.roomiest(snap(readings, **kw))
    return d.target if d.action == 'switch' else None


class ThePick(unittest.TestCase):
    def test_the_most_room_wins_whatever_order_auto_switch_uses_and_with_auto_switch_off(self):
        for settings in (on(), on(strategy='refills-first'), on(strategy='stay-then-refills'), policy.Settings()):
            with self.subTest(settings=settings):
                self.assertEqual(pick(settings=settings), 'c')     # refills-first alone would land on b

    def test_the_account_claude_code_is_on_counts_unless_auto_switch_is_about_to_leave_it(self):
        self.assertEqual(pick(dict(R, a=reading('a', five=5.0, week=5.0))), 'a')
        self.assertEqual(pick(dict(R, a=reading('a', five=5.0, week=5.0), c=reading('c', five=92.0, week=1.0))), 'a')
        for near in (88.0, 92.0, 100.0):                           # within 5 points of its 5-hour line, or past it
            with self.subTest(five=near):
                self.assertEqual(pick(dict(R, a=reading('a', five=near, week=1.0), c=reading('c', five=89.0,
                                                                                              week=1.0))), 'b')

    def test_it_keeps_every_rule_a_landing_keeps(self):
        self.assertEqual(pick(settings=on(rules=(('c', 'out'),))), 'a')                 # never one kept out
        self.assertEqual(pick(settings=on(rules=(('c', 'last'),))), 'a')                # used last only ...
        self.assertEqual(pick(settings=on(rules=(('a', 'last'), ('b', 'out'), ('c', 'last')))), 'c')   # ... then
        self.assertEqual(pick(settings=on(ranks=(('b', 1),))), 'b')                     # a lower rank first
        self.assertEqual(pick(settings=on(own=policy.own_from({'c': {'weekly_at': 60}}))), 'a')   # its own line
        self.assertEqual(pick(accounts=(ACCTS[0], ACCTS[1], account('c', 3, status='claimed'))), 'a')  # another run
        self.assertEqual(pick(accounts=(ACCTS[0], ACCTS[1], account('c', 3, status='needs_login'))), 'a')
        self.assertEqual(pick(dict(R, c=reading('c', five=1.0, week=1.0, age=2000))), 'a')   # numbers too old

    def test_with_none_it_says_why_and_when_the_first_one_refills(self):
        full = {'a': reading('a', five=100.0, week=20.0, five_reset=NOW + 2 * 3600),
                'b': reading('b', five=10.0, week=93.0, week_reset=NOW + 30 * 3600)}
        d = runas.roomiest(snap(full, accounts=ACCTS[:2]))
        self.assertEqual((d.action, d.target, d.hold), ('stay', None, 'no account has room'))
        self.assertTrue(d.reason.startswith('No account has room. First refill: '), d.reason)
        d = runas.roomiest(snap(dict(R, b=reading('b', five=1.0, week=1.0, age=2000)), accounts=ACCTS[:2],
                                settings=on(rules=(('a', 'out'),))))
        self.assertEqual(d.reason, 'No account has fresh numbers with room: b@acct.example was not read in the last '
                                   '15 minutes.')
        d = runas.roomiest(snap(R, settings=on(rules=(('a', 'out'), ('b', 'out'), ('c', 'out')))))
        self.assertEqual(d.reason, runas.NO_RUN_ACCOUNT)

    def test_a_strict_run_leaves_out_the_account_claude_code_is_on(self):
        roomy = dict(R, a=reading('a', five=5.0, week=5.0))           # a has the most room, then c
        self.assertEqual(runas.roomiest(snap(roomy), strict=True).target, 'c')
        d = runas.roomiest(snap(roomy, accounts=ACCTS[:1]), strict=True)
        self.assertEqual(d.reason, 'No account but the one Claude Code is on can take a run: each is kept out of '
                                   'auto-switch, needs a fresh sign-in, or is held by another run.')
        d = runas.roomiest(snap(dict(roomy, b=reading('b', five=1.0, week=1.0, age=2000)), accounts=ACCTS[:2]),
                           strict=True)
        self.assertEqual(d.reason, 'No account but the one Claude Code is on has fresh numbers with room: '
                                   'b@acct.example was not read in the last 15 minutes.')

    def test_the_pick_on_what_is_on_disk_now_is_the_same_pick(self):
        with mock.patch.object(autoloop, 'gather', lambda **kw: snap(R)):
            self.assertEqual(runas.roomiest_now().target, 'c')
            self.assertEqual(runas.roomiest_now(strict=True).target, 'c')


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class TheCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-roomiest-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, 'home')
        os.makedirs(os.path.join(home, '.claude', 'projects'))
        env = mock.patch.dict(os.environ, {'HOME': home, 'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data'),
                                           'CLAUDE_CONFIG_DIR': os.path.join(home, '.claude'),
                                           'SWITCHBOARD_PORT': str(free_port()), 'NO_COLOR': '1'})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.fake = install(self)
        self.a = self.fake.hold('ana@work.example', 'org-w')
        self.b = self.fake.hold('bo@lab.example', 'org-l')
        self.fake.sign_in('ana@work.example', 'org-w')
        for target, name, value in ((cli.connect, 'claude_bin', lambda: '/usr/local/bin/claude'),
                                    (cli, 'terminal', lambda: False)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_cli(self, *args, decision=None):
        out, err = io.StringIO(), io.StringIO()
        self.asked = []
        with mock.patch.object(runas, 'roomiest_now', lambda **kw: self.asked.append(kw) or decision), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except core.SwitchError as e:
                code = 'refused: %s' % e
        return code, out.getvalue(), err.getvalue()

    def runs(self):
        return [c for c in self.fake.calls if c[0] == 'run_as']

    def test_it_runs_as_the_account_with_the_most_room_and_says_which_on_standard_error(self):
        self.fake.run_code = 3
        d = api.Decision('switch', target=self.b, kind='manual',
                         reason='bo@lab.example has room (75 points before its nearest line)')
        code, out, err = self.run_cli('run', '--roomiest', '--bare', '--', '-p', 'hi', decision=d)
        self.assertEqual(code, 3)
        self.assertEqual(self.runs(), [('run_as', self.b, ['-p', 'hi'])])
        self.assertTrue(self.fake.run_kw['bare'])
        self.assertEqual(out, '')                                  # a -p answer stays alone on standard output
        self.assertEqual(err, 'Picked bo@lab.example: it has room (75 points before its nearest line).\n')
        self.assertEqual(self.asked, [{'strict': False}])

    def test_with_strict_or_no_history_the_pick_leaves_out_the_account_claude_code_is_on(self):
        d = api.Decision('switch', target=self.b, kind='manual', reason='bo@lab.example has room (35 points)')
        for flag in ('--strict', '--no-history'):
            with self.subTest(flag=flag):
                self.run_cli('run', '--roomiest', flag, '--', decision=d)
                self.assertEqual(self.asked, [{'strict': True}])

    def test_with_no_account_with_room_it_says_why_and_starts_nothing(self):
        d = api.Decision('stay', kind='manual', hold='no account has room',
                         reason='No account has room. First refill: 4:10 PM (bo@lab.example, weekly).')
        code, _, _ = self.run_cli('run', '--roomiest', '--', decision=d)
        self.assertEqual(code, 'refused: No account has room. First refill: 4:10 PM (bo@lab.example, weekly). '
                               'Name one to run as it all the same: switchboard run NAME')
        self.assertEqual(self.runs(), [])

    def test_a_name_and_roomiest_together_or_a_word_it_does_not_take_show_how(self):
        for args in (('run', 'bo@lab.example', '--roomiest', '--'), ('run', '--roomiest', '--fast', '--'),
                     ('run', '--roomiest', '--roomiest', '--')):
            with self.subTest(args=args):
                code, out, _ = self.run_cli(*args)
                self.assertEqual((code, out), (2, cli.ROOMIEST_USAGE + '\n'))
        self.assertEqual(self.runs(), [])

    def test_roomiest_after_the_dashes_is_claudes(self):
        code, _, _ = self.run_cli('run', 'bo@lab.example', '--', '--roomiest')
        self.assertEqual(self.runs(), [('run_as', 'bo@lab.example', ['--roomiest'])])

    def test_the_help_names_it(self):
        self.assertIn(cli.ROOMIEST_USAGE, cli.command_help('run'))


if __name__ == '__main__':
    unittest.main()
