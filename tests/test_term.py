import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The terminal: what `switchboard` prints and asks (the view, the question, setup, add, update).
#
# Made-up accounts only. The engine is tests/fakes_accounts_api.py, in accounts.api's place in this process; a running
# page, where one is needed, is a stand-in on a free port. No test can reach a real page (a guard refuses the ports
# a real page may be on), a real login or Claude Code.
import io
import json
import os
import plistlib
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import connect, core, engine, linkapp, newer, notify_hooks, server, statusline, term
from switchboard.accounts import api
from fakes_accounts_api import install

TZ = timezone(timedelta(hours=-8))                                      # Fri Nov 13 2026 is in winter time
NOW = datetime(2026, 11, 13, 15, 31, 25, tzinfo=TZ).timestamp()          # Fri 3:31 PM
REAL_PAGES = (7798, 7799)                                                # a real page's and the demo's: never reached from here
ESC = re.compile(r'\x1b\[[0-9;]*m')
A, B, C = 'ana@work.example', 'ana@home.example', 'cy@lab.example'


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ).isoformat()


def w(pct, resets=None):
    return {'pct': pct, 'resets': resets, 'pace': None, 'lasts': None}


def acct(slot, email, plan, org, session=None, weekly=None, scoped=(), age=60, **more):
    """One account as the page's state has it (server.render)."""
    return dict({'email': email, 'name': email.split('@')[0], 'alias': '', 'slot': slot, 'plan': plan, 'org': org,
                 'active': False, 'needs_login': False, 'disabled': False, 'unmanaged': False,
                 'usage': {'session': session, 'weekly': weekly, 'scoped': list(scoped), 'spend': None},
                 'usage_at': NOW - age}, **more)


def personal(email):
    return "%s's Organization" % email


# Five made-up accounts for the terminal view.
SAT_NOON = '2026-11-14T19:59:59.651000+00:00'          # how Anthropic sends a reset: 11:59:59.65, which is noon
VIEW = {'now': NOW, 'active': 'ana@mail.example', 'engine': {'installed': True},
        'auto': {'enabled': True, 'on': True, 'session_at': 90.0, 'weekly_at': 95.0, 'model_at': 90.0},
        'plan': {'next': 'ana@lab.example'},
        'accounts': [
            acct(1, 'ana@northwind.example', 'Ultra', personal('ana@northwind.example'), weekly=w(0, at(19, 17)), age=120),
            acct(2, 'ana@fernhill.example', 'Ultra', personal('ana@fernhill.example'), weekly=w(94, at(16, 13)),
                 scoped=[{'name': 'Fable', 'pct': 81, 'resets': at(16, 13)}], age=600),
            acct(3, 'ana@harbor.example', 'Team', 'Harbor', session=w(0), weekly=w(17, at(16, 15)),
                 scoped=[{'name': 'Fable', 'pct': 30, 'resets': at(16, 15)}], age=300),
            acct(4, 'ana@lab.example', 'Ultra', personal('ana@lab.example'), weekly=w(65, at(15, 14)),
                 scoped=[{'name': 'Fable', 'pct': 90, 'resets': at(15, 14)}], age=300),
            acct(5, 'ana@mail.example', 'Ultra', personal('ana@mail.example'), weekly=w(27, SAT_NOON),
                 scoped=[{'name': 'Fable', 'pct': 22, 'resets': SAT_NOON}], age=180, active=True)]}

PLAIN = """\
Claude Code is on ana@mail.example · auto-switch on at 90% 5-hour · 95% weekly

1  ana@northwind.example  Max 20x · Personal  checked 2 min ago
   weekly  ────────────────────────    0%  resets Thu 5:00 PM · in 6 days

2  ana@fernhill.example   Max 20x · Personal  checked 10 min ago
   weekly  ━━━━━━━━━━━━━━━━━━━━━━━─   94%  resets Mon 1:00 PM · in 3 days · at this pace, full by 9:38 PM

3  ana@harbor.example     Team · Harbor       checked 5 min ago
   5-hour  ────────────────────────    0%  not started
   weekly  ━━━━────────────────────   17%  resets Mon 3:00 PM · in 3 days

4  ana@lab.example        Max 20x · Personal  next · checked 5 min ago
   weekly  ━━━━━━━━━━━━━━━━────────   65%  resets Sun 2:00 PM · in 2 days  Fable 90%

5  ana@mail.example       Max 20x · Personal  in use · checked 3 min ago
   weekly  ━━━━━━──────────────────   27%  resets Sat 12:00 PM · in 20 hours"""

NARROW = """\
Claude Code is on ana@mail.example
auto-switch on at 90% 5-hour · 95% weekly

1  ana@northwind.example  Max 20x · Personal
                          checked 2 min ago
   weekly  ────────    0%  resets Thu 5:00 PM · in 6 days

2  ana@fernhill.example   Max 20x · Personal
                          checked 10 min ago
   weekly  ━━━━━━━─   94%  full by 9:38 PM

3  ana@harbor.example     Team · Harbor
                          checked 5 min ago
   5-hour  ────────    0%  not started
   weekly  ━───────   17%  resets Mon 3:00 PM · in 3 days

4  ana@lab.example        Max 20x · Personal
                          next · checked 5 min ago
   weekly  ━━━━━───   65%  resets Sun 2:00 PM  Fable 90%

5  ana@mail.example       Max 20x · Personal
                          in use · checked 3 min ago
   weekly  ━━──────   27%  resets Sat 12:00 PM · in 20 hours"""


def lines(view=VIEW, width=None, colour=False, live=None):
    return term.view(term.read(view, live=live, tz=TZ), width=width, colour=colour)


# ---------- the view ----------

class TheView(unittest.TestCase):
    def test_it_is_the_design_line_for_line(self):
        got = lines()
        self.assertEqual('\n'.join(got), PLAIN)
        self.assertEqual(max(len(x) for x in got), 105)                 # fernhill's weekly line, with its pace words
        self.assertEqual(term.prompt([1, 2, 3, 4, 5]), 'Switch to 1–5, or Enter to stay › ')

    def test_colours_are_the_designs_and_nothing_else(self):
        got = lines(colour=True)
        self.assertEqual([ESC.sub('', x) for x in got], PLAIN.split('\n'))       # the same text, coloured
        self.assertEqual(got[0], 'Claude Code is on \x1b[94mana@mail.example\x1b[0m'
                                 '\x1b[2m · auto-switch on at 90% 5-hour · 95% weekly\x1b[0m')
        found = dict((ESC.sub('', x).strip(), x) for x in got)
        self.assertEqual(found['5  ana@mail.example       Max 20x · Personal  in use · checked 3 min ago'],
                         '5  \x1b[1mana@mail.example\x1b[0m       \x1b[2mMax 20x · Personal\x1b[0m  \x1b[94min use\x1b[0m'
                         '\x1b[2m · \x1b[0m\x1b[2mchecked 3 min ago\x1b[0m')
        # the account in use: its used part blue, the rest dim, its % and label plain, the reset words dim
        self.assertIn('   weekly  \x1b[94m' + '━' * 6 + '\x1b[0m\x1b[2m' + '─' * 18 + '\x1b[0m   27%  '
                      '\x1b[2mresets Sat 12:00 PM · in 20 hours\x1b[0m', got)
        # a counted model week at or over its line (Fable's is 90): a red note at the end of the weekly line
        self.assertIn('   weekly  ' + '━' * 16 + '\x1b[2m' + '─' * 8 + '\x1b[0m   65%  '
                      '\x1b[2mresets Sun 2:00 PM · in 2 days\x1b[0m  \x1b[91mFable 90%\x1b[0m', got)
        # under its line (weekly at 95) and not in use: the used part in the default colour
        self.assertIn('   weekly  ' + '━' * 23 + '\x1b[2m─\x1b[0m   94%  \x1b[2mresets Mon 1:00 PM · in 3 days\x1b[0m'
                      '\x1b[2m · \x1b[0mat this pace, full by 9:38 PM', got)
        self.assertIn('2  \x1b[1mana@fernhill.example\x1b[0m   \x1b[2mMax 20x · Personal\x1b[0m  '
                      '\x1b[2mchecked 10 min ago\x1b[0m', got)
        self.assertIn('4  \x1b[1mana@lab.example\x1b[0m        \x1b[2mMax 20x · Personal\x1b[0m  \x1b[2mnext\x1b[0m'
                      '\x1b[2m · \x1b[0m\x1b[2mchecked 5 min ago\x1b[0m', got)
        self.assertEqual(term.prompt([1, 5], colour=True), 'Switch to 1–5, or Enter to stay \x1b[2m›\x1b[0m ')
        codes = set(re.findall(r'\x1b\[([0-9;]*)m', '\n'.join(got)))
        self.assertEqual(codes, {'0', '1', '2', '91', '94'})

    def test_no_colour_with_no_color_set_or_off_a_terminal(self):
        class Tty(io.StringIO):
            def isatty(self):
                return True
        self.assertTrue(term.colour_ok(Tty(), {}))
        self.assertFalse(term.colour_ok(Tty(), {'NO_COLOR': '1'}))
        self.assertFalse(term.colour_ok(Tty(), {'TERM': 'dumb'}))
        self.assertFalse(term.colour_ok(io.StringIO(), {}))
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(cli, 'identity', lambda: None):
            cli.show(VIEW)
        self.assertNotIn('\x1b', out.getvalue())

    def test_a_narrow_terminal_gets_narrower_bars_and_60_columns_still_reads(self):
        got = lines(width=60)
        self.assertEqual('\n'.join(got), NARROW)
        self.assertLessEqual(max(len(x) for x in got), 60)
        got = lines(width=77)
        self.assertEqual(got[:2], ['Claude Code is on ana@mail.example', 'auto-switch on at 90% 5-hour · 95% weekly'])
        # 76 columns fit: full bars; lab's line, with its note, says its reset without how far off, and fernhill's,
        # with its pace words, says those alone, shorter
        self.assertEqual(got[3:], [x.replace(' · in 2 days  Fable', '  Fable').replace(
            'resets Mon 1:00 PM · in 3 days · at this pace, full by', 'full by') for x in PLAIN.split('\n')[2:]])
        self.assertEqual(lines(width=200), PLAIN.split('\n'))
        self.assertEqual(lines(width=105), PLAIN.split('\n'))        # just room for the pace words and the whole reset
        # what does not fit goes under its own column, or under the email when even that is too wide
        a = term.read(VIEW, tz=TZ)['accounts'][1]
        heads = term.head_lines(a, 1, 21, 18, width=40)
        self.assertEqual([term.paint(x, False) for x in heads],
                         ['2  ana@fernhill.example', '   Max 20x · Personal', '   checked 10 min ago'])

    def test_states(self):
        view = {'now': NOW, 'engine': {'installed': True},
                'auto': {'enabled': False, 'session_at': 90, 'weekly_at': 95}, 'plan': {'next': 'bo@two.example'},
                'accounts': [
                    acct(1, 'bo@one.example', 'Pro', personal('bo@one.example'), session=w(0), weekly=w(40, at(18, 9)),
                         needs_login=True),
                    acct(2, 'bo@two.example', {'label': 'Max 5x', 'tier': 'default_claude_max_5x'}, 'Two Ltd',
                         usage=None, age=0),
                    acct(3, 'bo@three.example', 'Max 20x', 'Three', session=w(92, at(13, 18, 10)),
                         weekly=w(50, at(17, 9)), scoped=[{'name': 'Fable', 'pct': 10, 'resets': at(18, 9)}],
                         active=True, age=90 * 60),
                    acct(4, 'bo@four.example', 'Max 20x', 'Four', session=w(31, at(13, 17, 40)), weekly=w(96, at(16, 8)),
                         age=2 * 86400)]}
        self.assertEqual('\n'.join(lines(view)), """\
Claude Code is on bo@three.example · auto-switch off at 90% 5-hour · 95% weekly

1  bo@one.example    Pro · Personal    sign in again · checked 1 min ago
   5-hour  ────────────────────────    0%  not started
   weekly  ━━━━━━━━━━──────────────   40%  resets Wed 9:00 AM · in 5 days · at this pace, full by Tue 1:16 AM

2  bo@two.example    Max 5x · Two Ltd  checked just now
   weekly  ────────────────────────     —

3  bo@three.example  Max 20x · Three   in use · checked 2 hours ago
   5-hour  ━━━━━━━━━━━━━━━━━━━━━━──   92%  resets 6:10 PM · in 3 hours
   weekly  ━━━━━━━━━━━━────────────   50%  resets Tue 9:00 AM · in 4 days

4  bo@four.example   Max 20x · Four    checked 2 days ago
   5-hour  ━━━━━━━─────────────────   31%  resets 5:40 PM · in 2 hours
   weekly  ━━━━━━━━━━━━━━━━━━━━━━━─   96%  resets Mon 8:00 AM · in 3 days""")
        coloured = lines(view, colour=True)
        # red beats blue: the account in use at its 5-hour line
        self.assertIn('   5-hour  \x1b[91m' + '━' * 22 + '\x1b[0m\x1b[2m──\x1b[0m   \x1b[91m92%\x1b[0m  '
                      '\x1b[2mresets 6:10 PM · in 3 hours\x1b[0m', coloured)
        self.assertIn('   weekly  \x1b[94m' + '━' * 12 + '\x1b[0m\x1b[2m' + '─' * 12 + '\x1b[0m   50%  '
                      '\x1b[2mresets Tue 9:00 AM · in 4 days\x1b[0m', coloured)
        self.assertIn('   weekly  \x1b[2m' + '─' * 24 + '\x1b[0m     —', coloured)        # not known: a bare bar
        self.assertNotIn('   5-hour  \x1b[2m' + '─' * 24 + '\x1b[0m     —', coloured)     # and no 5-hour line at all
        self.assertIn('1  \x1b[1mbo@one.example\x1b[0m    \x1b[2mPro · Personal\x1b[0m    sign in again'
                      '\x1b[2m · \x1b[0m\x1b[2mchecked 1 min ago\x1b[0m', coloured)
        # no account, so none with a 5-hour limit: the header names the weekly line alone
        self.assertEqual(lines({'now': NOW, 'accounts': [], 'auto': {}}),
                         ['Claude Code is not signed in · auto-switch off at 95% weekly'])
        self.assertEqual(lines({'now': NOW, 'accounts': [], 'auto': {}}, live='ana@work.example')[0][:35],
                         'Claude Code is on ana@work.example ')

    def test_bars(self):
        self.assertEqual([term.cells(p, 24) for p in (None, 0, 1, 4, 50, 94, 99, 100, 130)],
                         [0, 0, 1, 1, 12, 23, 23, 24, 24])
        self.assertEqual([term.cells(p, 8) for p in (1, 27, 94, 99, 100)], [1, 2, 7, 7, 8])

    def test_times_and_words(self):
        clock = lambda iso: term.clock(core.parse_iso(iso), NOW, TZ)        # noqa: E731
        self.assertEqual([clock(at(13, 18, 10)), clock(at(14, 12)), clock(SAT_NOON), clock(at(16, 0, 5))],
                         ['6:10 PM', 'Sat 12:00 PM', 'Sat 12:00 PM', 'Mon 12:05 AM'])
        until = lambda iso: term.until(core.parse_iso(iso), NOW)            # noqa: E731
        self.assertEqual([until(SAT_NOON), until(at(15, 14)), until(at(16, 13)), until(at(19, 17)),
                          until(at(13, 18, 10)), until(at(13, 17, 40)), until(at(13, 16, 31)), until(at(13, 15, 32)),
                          until(at(14, 15, 20))],
                         ['in 20 hours', 'in 2 days', 'in 3 days', 'in 6 days', 'in 3 hours', 'in 2 hours',
                          'in 1 hour', 'in 1 min', 'in 1 day'])
        # how old a reading is, at every age now: under a minute, just now
        self.assertEqual([term.age(NOW - m * 60, NOW) for m in (0, 1, 5, 6, 59, 61, 150, 1500)],
                         ['just now', '1 min ago', '5 min ago', '6 min ago', '59 min ago', '1 hour ago', '3 hours ago',
                          '1 day ago'])
        self.assertEqual(term.age(None, NOW), '')
        plan = lambda **a: term.plan_words(a)                                # noqa: E731
        self.assertEqual([plan(plan='Ultra', org=personal('x@y.example')), plan(plan='Team', org='Harbor'),
                          plan(plan={'label': 'Ultra', 'tier': 'default_claude_max_20x'}, org=''),
                          plan(plan='Max', tier='default_claude_max_5x', org='Lab'), plan(plan='', org='Lab'),
                          plan(plan=None, org=None)],
                         ['Max 20x · Personal', 'Team · Harbor', 'Max 20x', 'Max 5x · Lab', 'Lab', ''])

    def test_a_model_week_has_no_line_of_its_own(self):
        # (it had a line under the weekly one; now only a counted one at or over its line is said, as a note at the
        # end of the weekly line: tests/test_rows_two_limits.py)
        rows = term.read(VIEW, tz=TZ)['accounts']
        self.assertEqual([[l['label'] for l in a['limits']] for a in rows],
                         [['weekly'], ['weekly'], ['5-hour', 'weekly'], ['weekly'], ['weekly']])
        self.assertEqual([a['limits'][-1]['note'] for a in rows],
                         [None, None, None, {'label': 'Fable', 'pct': 90}, None])

    def test_the_pages_own_rules(self):
        # The page (index.html) and the terminal must say the same thing about the same state.
        base = dict(acct(1, 'x@y.example', 'Ultra', personal('x@y.example'), session=w(62, at(13, 18, 10)),
                         weekly=w(70, at(16, 12))), active=True)
        read = lambda a, **v: term.read(dict({'now': NOW, 'auto': VIEW['auto']}, accounts=[a], **v), tz=TZ)['accounts'][0]  # noqa: E731
        # a reading the engine would not decide on is not known: a dash, never numbers (its age still shows)
        # (and, as on the page now, no 5-hour line: it is drawn only when there is a 5-hour number to show)
        got = read(dict(base, trusted=False, usage_at=NOW - 7200))
        self.assertEqual([(l['label'], l['pct'], l['words']) for l in got['limits']], [('weekly', None, '')])
        self.assertEqual(got['age'], 'checked 2 hours ago')
        # a 5-hour window Claude sends with no number has no line; a weekly one it does not send at all has not started
        got = read(dict(base, usage={'session': {'pct': None, 'resets': at(13, 18)}, 'weekly': None, 'scoped': []}))
        self.assertEqual([(l['label'], l['pct'], l['words']) for l in got['limits']], [('weekly', 0, 'not started')])
        # each window turns red at automatic switching's own line for it; a line of the account's own is not read
        self.assertEqual([l['over'] for l in read(dict(base, line=60))['limits']], [False, False])
        self.assertEqual([l['over'] for l in read(base)['limits']], [False, False])
        auto = dict(VIEW['auto'], session_at=70.0, weekly_at=70.0)
        got = term.read({'now': NOW, 'auto': auto, 'accounts': [base]}, tz=TZ)
        self.assertEqual([l['over'] for l in got['accounts'][0]['limits']], [False, True])
        # the plan's words as the server writes them, when it sends them
        self.assertEqual(read(dict(base, plan_line='Team · Harbor'))['plan'], 'Team · Harbor')
        self.assertEqual(read(dict(base, tier='default_claude_max_5x', plan='Max'))['plan'], 'Max 5x · Personal')
        self.assertEqual(read(dict(base, org_type='claude_team', plan='', org='Harbor'))['plan'], 'Team · Harbor')
        self.assertEqual(read(dict(base, org="Someone's organisation"))['plan'], 'Max 20x · Personal')
        # "an hour or more" counts from the exact age
        self.assertEqual([term.ago(m) for m in (59.4, 59.7, 60, 1439, 1440)],
                         ['59 min', '60 min', '1 hour', '24 hours', '1 day'])

    def test_next_is_the_one_the_server_marks(self):
        # The server marks Next only while automatic switching runs; the terminal shows exactly that one.
        self.assertEqual([a['email'] for a in term.read(dict(VIEW, next='ana@harbor.example'))['accounts']
                          if a['next']], ['ana@harbor.example'])
        self.assertFalse(any(a['next'] for a in term.read(dict(VIEW, next=None))['accounts']))
        # an older server sends no mark: the plan's next, only while automatic switching runs
        stopped = dict(VIEW, auto=dict(VIEW['auto'], on=False))
        self.assertFalse(any(a['next'] for a in term.read(stopped)['accounts']))
        self.assertEqual([a['email'] for a in term.read(VIEW)['accounts'] if a['next']], ['ana@lab.example'])

    def test_next_only_while_automatic_switching_is_on(self):
        view = dict(VIEW, auto=dict(VIEW['auto'], enabled=False))
        self.assertEqual([a['next'] for a in term.read(view)['accounts']], [False] * 5)
        self.assertIn('auto-switch off at 90% 5-hour', lines(view)[0])
        self.assertEqual([a['email'] for a in term.read(VIEW)['accounts'] if a['next']], ['ana@lab.example'])
        # never the account in use, never one that needs a fresh sign-in, whatever the plan says
        view = dict(VIEW, plan={'next': 'ana@mail.example'})
        self.assertFalse(any(a['next'] for a in term.read(view)['accounts']))


# ---------- the question ----------

class TheQuestion(unittest.TestCase):
    def setUp(self):
        for target, name, value in ((cli, 'identity', lambda: None), (cli, 'width', lambda: None),
                                    (connect, 'claude_bin', lambda: '/made/up/claude')):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def ask(self, keys, view=VIEW):
        keys = list(keys)
        switched, signed = [], []
        out = io.StringIO()
        with mock.patch.object(cli, 'read_one_key', lambda: keys.pop(0)), \
                mock.patch.object(cli, 'switch', lambda email, view: switched.append(email)), \
                mock.patch.object(cli, 'sign_in', lambda email: signed.append(email) or 0), redirect_stdout(out):
            code = cli.show(view, ask=True)
        return code, out.getvalue(), switched, signed

    def test_a_number_switches(self):
        code, out, switched, _ = self.ask('3')
        self.assertEqual((code, switched), (0, ['ana@harbor.example']))
        self.assertTrue(out.endswith('27%  resets Sat 12:00 PM · in 20 hours\n\n'
                                     'Switch to 1–5, or Enter to stay › 3\nClaude Code is on ana@harbor.example.\n'), out)

    def test_enter_and_q_leave_everything_as_it_is(self):
        for key in ('\r', '\n', 'q', '\x1b', '\x03', ''):
            with self.subTest(key=key):
                code, out, switched, signed = self.ask([key])
                self.assertEqual((code, switched, signed), (0, [], []))
                self.assertTrue(out.endswith('Switch to 1–5, or Enter to stay › \n'), out)

    def test_other_keys_wait_for_one_that_means_something(self):
        code, out, switched, _ = self.ask(['x', '9', '0', ' ', '2'])
        self.assertEqual(switched, ['ana@fernhill.example'])
        self.assertTrue(out.endswith('› 2\nClaude Code is on ana@fernhill.example.\n'))

    def test_the_account_in_use_stays_and_says_so(self):
        code, out, switched, _ = self.ask('5')
        self.assertEqual(switched, [])
        self.assertTrue(out.endswith('› 5\nClaude Code is on ana@mail.example.\n'))

    def test_a_number_for_an_account_that_needs_a_fresh_sign_in_starts_it(self):
        view = dict(VIEW, accounts=[dict(a, needs_login=a['slot'] == 2) for a in VIEW['accounts']])
        code, out, switched, signed = self.ask('2', view)
        self.assertEqual((switched, signed), ([], ['ana@fernhill.example']))
        self.assertIn('2  ana@fernhill.example   Max 20x · Personal  sign in again · checked 10 min ago', out)

    def test_past_nine_accounts_a_number_is_typed(self):
        for typed, picked in (('11\n', 11), ('\n', None), ('13\n', None), ('', None)):
            with mock.patch.object(sys, 'stdin', io.StringIO(typed)), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.pick(list(range(1, 13))), picked)
            self.assertEqual(out.getvalue(), 'Switch to 1\u201312, or Enter to stay \u203a ')

    def test_no_question_without_a_terminal(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.show(VIEW), 0)
        self.assertNotIn('Switch to', out.getvalue())
        with mock.patch.object(cli, 'terminal', lambda: False), mock.patch.object(cli, 'current_view', lambda **k: VIEW), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main([]), 0)
        self.assertNotIn('Switch to', out.getvalue())
        self.assertTrue(out.getvalue().startswith('Claude Code is on ana@mail.example'))

    def test_what_is_missing_is_said_last(self):
        one = dict(VIEW, accounts=[VIEW['accounts'][4]])
        code, out, _, _ = self.ask([], one)                     # nothing to switch to: no question
        self.assertTrue(out.endswith('in 20 hours\n\nSign in to another account with /login in Claude Code, then type '
                                     'switchboard to keep it.\n'), out)
        first = dict(one, accounts=[dict(VIEW['accounts'][4], unmanaged=True)])
        self.assertTrue(self.ask([], first)[1].endswith('\n\nAdd it with switchboard setup.\n'))
        with self.assertRaises(core.SwitchError) as e:          # nothing added yet: nothing to switch to
            cli.switch('ana@mail.example', dict(first, accounts=[dict(first['accounts'][0], active=False)]))
        self.assertEqual(str(e.exception), 'ana@mail.example is not in Switchboard yet. Add it with: switchboard setup')
        none = dict(VIEW, active=None, accounts=[])
        self.assertTrue(self.ask([], none)[1].endswith('weekly\n\nSign in to Claude Code first, then run switchboard '
                                                      'setup.\n'))
        with mock.patch.object(connect, 'claude_bin', lambda: None):
            self.assertTrue(self.ask([], none)[1].endswith('\n\nInstall Claude Code first (https://claude.com/claude-code'
                                                          '), then run switchboard setup.\n'))
        guarded = dict(one, guard="Another program is also switching Claude Code's login (com.example.x). Turn it "
                                  'off first.')
        self.assertTrue(self.ask([], guarded)[1].endswith("\n\nAnother program is also switching Claude Code's login "
                                                          '(com.example.x). Turn it off first.\n'))
        elsewhere = dict(VIEW, active=None, accounts=[dict(a, active=False) for a in VIEW['accounts'][:2]])
        with mock.patch.object(cli, 'identity', lambda: (C, '')):
            code, out, switched, _ = self.ask('1', elsewhere)
        self.assertIn('Claude Code is on cy@lab.example', out)
        self.assertIn('\n\ncy@lab.example is not in Switchboard yet. Add it with switchboard add.\n\nSwitch to 1–2', out)
        self.assertEqual(switched, ['ana@northwind.example'])


# The first run keeps each login as you sign in, and auto-switch turns on by itself with a second account.
AUTO_ON = ('Auto-switch is on: Claude Code moves to another account before a limit. Turn it off with '
           'switchboard auto off.')
SIGN_IN = 'Sign in to your other accounts with /login in Claude Code, then type switchboard to keep each one.'

# ---------- a sandbox: throwaway folders, the stand-in engine, no page unless a test starts its stand-in ----------

def write_exe(path, text):
    with open(path, 'w') as f:
        f.write(text)
    os.chmod(path, 0o755)


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-term-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        os.makedirs(os.path.join(self.home, '.claude'))
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude'),
                                           'SWITCHBOARD_PORT': str(free_port()), 'TZ': 'America/Los_Angeles'})
        env.start()
        self.addCleanup(env.stop)
        time.tzset()
        self.addCleanup(time.tzset)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.fake = install(self)
        host = engine.AutoHost()
        os.makedirs(os.path.join(self.tmp, 'managed'))         # stands in for Claude Code's managed settings folder
        for target, name, value in ((engine, 'HOST', host), (server.artifacts, 'refresh', lambda: None),
                                    (server, 'tell', lambda *a, **k: None), (cli, '_sleep', lambda s: None),
                                    (connect, 'claude_bin', lambda: '/made/up/claude'),
                                    (urllib.request, 'urlopen', self.guarded(urllib.request.urlopen)),
                                    (cli, '_page_open', self.guarded(cli._page_open)),
                                    (notify_hooks, 'MANAGED', os.path.join(self.tmp, 'managed')),
                                    (notify_hooks, 'precompile', lambda *a, **k: None),
                                    (engine, 'foreign_switcher', lambda folder=None: None),
                                    (statusline, 'precompile', lambda *a, **k: None)):   # no bytecode in the source
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(host.stop, 5)

    @staticmethod
    def guarded(real):
        def urlopen(url, *a, **k):
            port = urllib.parse.urlparse(getattr(url, 'full_url', url)).port
            if port in REAL_PAGES:
                raise AssertionError('a test reached for the page on port %d' % port)
            return real(url, *a, **k)
        return urlopen

    def calls(self, *names):
        """The engine calls that change something (or the ones named), in order."""
        names = names or ('switch', 'add_current', 'remove', 'set_alias', 'reorder', 'set_auto', 'run_as')
        return [c for c in self.fake.calls if c[0] in names]

    def emails(self):
        return [r['email'] for r in self.fake.saved]

    def signed_in(self, email, org=''):
        with open(os.path.join(self.home, '.claude', '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': {'emailAddress': email, 'organizationUuid': org}}, f)

    def run_cli(self, *args, tty=None):
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(cli, 'open_tty', lambda: tty):
            code = cli.main(list(args))
        return code, out.getvalue()


class FakeTty(object):
    """A terminal that answers from a list; what it was asked is kept."""

    def __init__(self, answers):
        self.answers, self.asked = list(answers), []
        self.writer = io.StringIO()

    def ask(self, text):
        sys.stdout.write(text)
        self.asked.append(text)
        if not self.answers:
            raise EOFError
        said = self.answers.pop(0)
        if isinstance(said, BaseException):
            raise said
        sys.stdout.write(said + '\n')                   # a terminal shows what was typed
        return said.strip().lower()

    yes = cli.Tty.yes


def hold(fake, email, active=False, five=None, week=None, scoped=(), plan=None, org='', status='ok', age=30):
    """A made-up account the engine keeps, with its reading; `active`: Claude Code is signed in to it."""
    aid = fake.hold(email, 'org-' + email.replace('@', '-'), plan=plan, org_name=org, status=status)
    t = time.time()
    fake.reading(aid, five=(five[0], core.parse_iso(five[1])) if five else None,
                 week=(week[0], core.parse_iso(week[1])) if week else None,
                 models=dict((n, (p, core.parse_iso(r))) for n, p, r in scoped), at=t - age)
    if active:
        fake.sign_in(email, 'org-' + email.replace('@', '-'))
    return aid


def iso_in(seconds):
    return datetime.fromtimestamp(time.time() + seconds, timezone.utc).isoformat()


# ---------- the page's numbers, whether the page runs or not ----------

class Page(ThreadingHTTPServer):
    """A stand-in for the running page: GET /api/state answers `view`; each POST is noted and answered."""

    def __init__(self, view, on_post=None, answers=None):
        ThreadingHTTPServer.__init__(self, ('127.0.0.1', 0), PageHandler)
        if isinstance(view, dict) and 'folder' not in view:    # a page of this test's data folder (core.folder_mark),
            os.makedirs(core.data_dir(), exist_ok=True)        # which a real page has made before it answers
            view = dict(view, folder=core.folder_mark())
        self.view, self.posts, self.states, self.on_post = view, [], [], on_post
        self.answers = answers or {}
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def stop(self):
        self.shutdown()
        self.server_close()


class PageHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, body, code=200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path != '/api/state':
            return self.send({'error': 'not found'}, 404)
        got = self.server.states.pop(0) if self.server.states else self.server.view
        if isinstance(got, dict) and 'folder' not in got:      # a page of this test's data folder: a change may go
            got = dict(got, folder=core.folder_mark())         # to it (a command checks: core.folder_mark)
        self.send(got)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        self.server.posts.append((self.path, body, self.headers.get('X-Switchboard')))
        if self.server.on_post:
            self.server.on_post(self.path, body)
        self.send(dict(self.server.answers.get(self.path) or {}, ok=True))


class TheSameNumbersAsThePage(Sandbox):
    def start_page(self, view, answers=None):
        page = Page(view, answers=answers)
        self.addCleanup(page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(page.server_address[1])
        return page

    def test_the_view_is_the_running_pages_own(self):
        page = self.start_page(VIEW)
        self.assertEqual(cli.current_view(), page.view)
        code, out = self.run_cli('status')
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('Claude Code is on ana@mail.example · auto-switch on at 90% 5-hour'), out)
        self.assertIn('4  ana@lab.example        Max 20x · Personal  next · checked 5 min ago\n', out)
        self.assertEqual(self.fake.calls, [])                   # the engine was not asked: the page was

    def test_a_switch_goes_through_the_running_page(self):
        page = self.start_page(dict(VIEW, accounts=[dict(a, alias='harbor' if a['slot'] == 3 else '')
                                                    for a in VIEW['accounts']]))
        code, out = self.run_cli('3')
        self.assertEqual((code, out), (0, 'Claude Code is on ana@harbor.example.\n'))
        self.assertEqual(page.posts, [('/api/switch', {'email': 'ana@harbor.example'}, '1')])
        self.assertEqual(self.run_cli('ana@fernhill.example')[0], 0)
        self.assertEqual(page.posts[-1][:2], ('/api/switch', {'email': 'ana@fernhill.example'}))
        self.assertEqual(self.run_cli('use', 'harbor')[0], 0)      # a name still works as input
        self.assertEqual(page.posts[-1][:2], ('/api/switch', {'email': 'ana@harbor.example'}))
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('7')
        self.assertIn('No account 7', str(e.exception))
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('someone@else.example')
        self.assertIn('someone@else.example is not in Switchboard', str(e.exception))
        self.assertEqual(self.run_cli('switchboard-nonsense')[0], 2)

    def test_without_the_page_the_numbers_are_worked_out_the_way_the_page_works_them_out(self):
        hold(self.fake, A, five=(12, iso_in(3 * 3600)), week=(40, iso_in(3 * 86400)), org='Work', plan='Ultra')
        hold(self.fake, B, active=True, five=(95, iso_in(3600)), week=(60, iso_in(86400)),
             scoped=[('Fable', 91, iso_in(86400))], org=personal(B))
        hold(self.fake, C, five=(0, None), week=(10, iso_in(5 * 86400)), org='Lab', status='needs_login')
        self.fake.auto['enabled'] = True
        with mock.patch.object(engine.HOST, 'status', lambda wanted=None: {
                'wanted': True, 'running': True, 'on': True, 'starting': False, 'last_pass': 1, 'problem': None,
                'stage': None, 'waiting_for': None, 'restarted': None}):
            here = cli.current_view()
            server.tick()                                       # the page's own look, then the page's own state
            theirs = server.view()
        mine, page = term.read(here), term.read(theirs)
        self.assertEqual([a['email'] for a in mine['accounts']], [A, B, C])
        for x, y in zip(mine['accounts'], page['accounts']):
            self.assertEqual((x['plan'], x['active'], x['next'], x['needs_login'], [(l['label'], l['pct'], l['over'])
                                                                                    for l in x['limits']]),
                             (y['plan'], y['active'], y['next'], y['needs_login'], [(l['label'], l['pct'], l['over'])
                                                                                    for l in y['limits']]))
        self.assertEqual(here['plan']['next'], theirs['plan']['next'])
        self.assertEqual([a['next'] for a in mine['accounts']], [True, False, False])
        self.assertEqual(mine['accounts'][0]['plan'], 'Max 20x · Work')
        self.assertEqual(mine['accounts'][1]['plan'], 'Personal')
        self.assertEqual(mine['head']['email'], B)
        code, out = self.run_cli('1')
        self.assertEqual((code, out), (0, 'Claude Code is on ana@work.example.\n'))
        self.assertEqual([c[1:4] for c in self.calls('switch')], [(self.fake.saved[0]['id'], 'manual', 'cli')])

    def test_a_fact_the_page_reads_and_the_terminal_does_not_send_falls_back_to_the_pages_own(self):
        hold(self.fake, A, active=True, week=(5, iso_in(86400)))
        real = server.render

        def render(st, f):
            f['mac']['awake']                   # one only the page's own list of facts has
            return real(st, f)
        with mock.patch.object(server, 'render', render):
            self.assertEqual([a['email'] for a in cli.local_view()['accounts']], [A])

    def test_pages_check_and_open_name_each_account_by_its_email(self):
        core.save(dict(core.blank_state(), order=[A], accounts={A: {'email': A, 'alias': 'work', 'profile': 'Profile 1',
                                                                    'connected': True}},
                       artifacts=[{'id': 'x1', 'url': 'https://claude.ai/artifact/x1', 'title': 'Notes', 'owner': A,
                                   'src': 'seen'}]))
        self.assertEqual(self.run_cli('pages'), (0, 'ana@work.example  seen       https://claude.ai/artifact/x1  Notes\n'))
        # The page names an account by its email (core.name_of); `open` prints artifacts.open_link's one line, for a
        # link it takes (an artifact id is 16 characters or more).
        self.start_page(VIEW, answers={'/api/check': {'tabs': True, 'results': [{'email': A, 'result': 'ok'}]},
                                       '/api/go': {'email': A, 'name': A, 'checked': True, 'title': 'Notes'}})
        code, out = self.run_cli('check')
        self.assertEqual(out, 'ana@work.example  -                        signed in, checked\n')
        code, out = self.run_cli('open', 'https://claude.ai/artifact/x1x1x1x1x1x1x1x1')
        self.assertEqual(out, 'Opened as ana@work.example: Notes.\n')

    def test_a_look_writes_nothing(self):
        hold(self.fake, A, active=True, week=(5, iso_in(86400)))
        hold(self.fake, B, week=(9, iso_in(2 * 86400)))
        self.run_cli('status')
        self.assertFalse(os.path.exists(os.path.join(self.data, 'state.json')))
        self.assertFalse(os.path.exists(os.path.join(self.data, 'switchboard.log')))


# ---------- the first run ----------

class FirstRun(Sandbox):
    def setUp(self):
        super().setUp()
        self.signed_in(A, 'org-a')

    def hooks(self):
        """How many of Switchboard's notification hooks are in the throwaway Claude Code settings file."""
        try:
            with open(os.path.join(self.home, '.claude', 'settings.json')) as f:
                return f.read().count(notify_hooks.MARK)
        except OSError:
            return 0

    def test_with_a_terminal(self):
        code, out = self.run_cli('setup', tty=FakeTty([]))
        self.assertEqual((code, out), (0, 'Found Claude Code signed in as ana@work.example. Kept.\n'
                                          'Notifications and the status line are on.\n%s\n' % SIGN_IN))
        self.assertEqual(self.calls(), [('add_current',)])     # the order stays the engine's own: what refills first
        self.assertEqual(self.hooks(), 3)
        code, out = self.run_cli('setup', tty=FakeTty([]))              # the first run again: said once, done once
        self.assertEqual((code, out), (0, 'Found Claude Code signed in as ana@work.example. Kept.\n%s\n' % SIGN_IN))

    def test_without_a_terminal(self):
        self.assertEqual(self.run_cli('setup'), (0, '✓ 1 account. Notifications and the status line are on. Type '
                                                    'switchboard to see it.\n'))

    def test_the_flag_that_changes_nothing_is_taken_and_a_wrong_one_is_not(self):
        self.assertEqual(self.run_cli('setup', '--no-bringover', tty=FakeTty([]))[0], 0)
        self.assertEqual(self.run_cli('setup', '--please')[0], 2)

    def test_notifications_you_turned_off_stay_off(self):
        self.run_cli('notify', 'off')
        hold(self.fake, B)
        code, out = self.run_cli('setup', '--yes')
        self.assertEqual(out.splitlines()[-1], 'Sign in to your other accounts with /login in Claude Code, then type '
                                               'switchboard to keep each one.')
        self.assertIn('Auto-switch is on', out)
        self.assertEqual(self.hooks(), 0)

    def test_with_claude_codes_hooks_turned_off_nothing_is_put_in_or_said(self):
        path = os.path.join(self.home, '.claude', 'settings.json')
        with open(path, 'w') as f:
            f.write('{\n  "disableAllHooks": true\n}\n')
        code, out = self.run_cli('setup')
        self.assertEqual(out.splitlines()[-1], '✓ 1 account. Type switchboard to see it.')
        with open(path) as f:
            self.assertEqual(f.read(), '{\n  "disableAllHooks": true\n}\n')

    def test_not_signed_in(self):
        os.remove(os.path.join(self.home, '.claude', '.claude.json'))
        self.assertEqual(self.run_cli('setup', tty=FakeTty([])),
                         (0, 'Sign in to Claude Code first, then run switchboard setup again.\n'))
        self.assertEqual(self.calls(), [])

    def test_without_claude_code_it_says_to_install_it(self):
        os.remove(os.path.join(self.home, '.claude', '.claude.json'))
        with mock.patch.object(connect, 'claude_bin', lambda: None):
            self.assertEqual(self.run_cli('setup', tty=FakeTty([])),
                             (0, 'Install Claude Code first (https://claude.com/claude-code), then run switchboard '
                                 'setup again.\n'))
            self.assertEqual(self.run_cli('setup'), (0, 'Install Claude Code first (https://claude.com/claude-code), '
                                                        'then run switchboard setup again.\n'))
            with self.assertRaises(core.SwitchError) as e:
                self.run_cli('add', tty=FakeTty([]))
        self.assertIn('Claude Code is not installed on this Mac', str(e.exception))
        self.assertEqual(self.calls(), [])

    def test_through_the_running_page(self):
        page = Page({'accounts': []}, on_post=lambda path, body: path == '/api/engine' and self.fake.add_current())
        self.addCleanup(page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(page.server_address[1])
        code, out = self.run_cli('setup', tty=FakeTty([]))
        self.assertEqual(code, 0, out)
        self.assertEqual([p[0] for p in page.posts], ['/api/engine'])
        self.assertEqual(page.posts[0][1], {'add': True})
        self.assertEqual(self.emails(), [A])

    def test_bare_switchboard_in_a_terminal_starts_with_the_first_run(self):
        with mock.patch.object(cli, 'terminal', lambda: True), mock.patch.object(cli, 'read_one_key', lambda: '\r'):
            code, out = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertTrue(out.startswith('Found Claude Code signed in as ana@work.example. Kept.\n'
                                       'Notifications and the status line are on.\n\n'
                                       'Claude Code is on ana@work.example · auto-switch off'), out)
        # once something is added, it is the view straight away
        with mock.patch.object(cli, 'terminal', lambda: True), mock.patch.object(cli, 'read_one_key', lambda: 'q'):
            code, out = self.run_cli()
        self.assertTrue(out.startswith('Claude Code is on ana@work.example'), out)


# ---------- adding accounts ----------

class AddLoop(Sandbox):
    def setUp(self):
        super().setUp()
        self.signed_in(A, 'org-a')

    def logins(self, *who):
        """Each wait, the person signs in to the next account in Claude Code (/login), as it writes its own file."""
        queue = list(who)

        def sleep(seconds):
            if seconds == cli.POLL and queue:
                nxt = queue.pop(0)
                if isinstance(nxt, BaseException):
                    raise nxt
                self.signed_in(*nxt)
        p = mock.patch.object(cli, '_sleep', sleep)
        p.start()
        self.addCleanup(p.stop)

    def test_one_sign_in_at_a_time(self):
        self.logins((B, ''), (C, 'org-c'))
        code, out = self.run_cli('add', tty=FakeTty(['y', 'n']))
        self.assertEqual(code, 0, out)
        self.assertEqual(out, '✓ Saved ana@work.example\n'
                              'In Claude Code, run /login and sign in to the next account.\n'
                              '✓ Saved ana@home.example\n'
                              '%s\n'
                              'Add another? [y/N] y\n'
                              'In Claude Code, run /login and sign in to the next account.\n'
                              '✓ Saved cy@lab.example\n'
                              'Add another? [y/N] n\n'
                              '3 accounts. Type switchboard to see them.\n' % AUTO_ON)
        # each login kept; with the second account, auto-switch on (once: the engine keeps the setting)
        self.assertEqual(self.calls(), [('add_current',), ('add_current',), ('set_auto', {'enabled': True}),
                                        ('add_current',)])
        self.assertEqual(sorted(self.emails()), [B, A, C])

    def test_the_account_already_here_is_kept_up_to_date_quietly(self):
        self.fake.hold(A, 'org-a')
        self.logins((B, ''))
        code, out = self.run_cli('add', tty=FakeTty(['']))
        self.assertEqual(out, 'In Claude Code, run /login and sign in to the next account.\n'
                              '✓ Saved ana@home.example\n'
                              '%s\n'
                              'Add another? [y/N] \n'
                              '2 accounts. Type switchboard to see them.\n' % AUTO_ON)
        # its newest login kept before Claude Code moves on; with the second account, auto-switch on
        self.assertEqual(self.calls(), [('add_current',), ('add_current',), ('set_auto', {'enabled': True})])

    def test_ctrl_c_leaves_cleanly(self):
        self.logins(KeyboardInterrupt())
        code, out = self.run_cli('add', tty=FakeTty([]))
        self.assertEqual((code, out), (0, '✓ Saved ana@work.example\n'
                                          'In Claude Code, run /login and sign in to the next account.\n\n'))
        self.logins((B, ''))
        code, out = self.run_cli('add', tty=FakeTty([KeyboardInterrupt()]))     # at the question too
        self.assertEqual(code, 0)
        self.assertTrue(out.endswith('✓ Saved ana@home.example\n%s\nAdd another? [y/N] \n' % AUTO_ON), out)

    def test_without_a_terminal_it_saves_the_account_claude_code_is_on(self):
        code, out = self.run_cli('add')
        self.assertEqual((code, out), (0, '✓ Saved ana@work.example\n'
                                          'To add another, run /login in Claude Code, then switchboard add again.\n'))
        os.remove(os.path.join(self.home, '.claude', '.claude.json'))
        with self.assertRaises(core.SwitchError):
            self.run_cli('add')

    def test_a_refusal_is_said_and_nothing_else_happens(self):
        self.fake.fail['add_current'] = api.AccountsError('keychain_locked', "macOS's Keychain is locked. Unlock the "
                                                                             'Mac, then try again.')
        with self.assertRaises(engine.Refused) as e:
            self.run_cli('add', tty=FakeTty([]))
        self.assertEqual(str(e.exception), "macOS's Keychain is locked. Unlock the Mac, then try again.")
        self.assertEqual(self.emails(), [])

    def test_a_fresh_sign_in_for_an_account_whose_login_stopped(self):
        self.fake.hold(A, 'org-a')
        self.fake.hold(B, status='needs_login')
        self.logins((B, ''))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.sign_in(B), 0)
        self.assertEqual(out.getvalue(), 'In Claude Code, run /login and sign in as ana@home.example.\n'
                                         '✓ Saved ana@home.example\n')
        self.assertEqual(self.calls(), [('add_current',), ('add_current',)])
        self.logins((C, ''))
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.sign_in(A), 1)
        self.assertIn('Claude Code is on cy@lab.example now, not ana@work.example. Nothing was saved.', out.getvalue())

    def test_not_in_the_demo(self):
        core.DEMO = True
        for cmd in ('add', 'setup'):
            with self.assertRaises(core.SwitchError):
                self.run_cli(cmd)


# ---------- switchboard update ----------

class Update(Sandbox):
    def setUp(self):
        super().setUp()
        self.args_file = os.path.join(self.tmp, 'installer-args.txt')
        os.environ['FAKE_INSTALLER_ARGS'] = self.args_file
        self.clone = os.path.join(self.tmp, 'clone')
        os.makedirs(self.clone)
        self.script = '#!/bin/sh\nprintf "%s\\n" "$@" > "$FAKE_INSTALLER_ARGS"\nexit 0\n'
        with open(os.path.join(self.clone, 'install.sh'), 'w') as f:
            f.write(self.script)

    def source(self, text):
        os.makedirs(os.path.join(self.data, 'app'), exist_ok=True)
        with open(os.path.join(self.data, 'app', 'source'), 'w') as f:
            f.write(text + '\n')

    def args(self):
        with open(self.args_file) as f:
            return f.read().split('\n')[:-1]

    def after(self):
        """The last line of an update with this stand-in installer, which installs no copy: the version after is not
        known, and it says so."""
        return newer.update_line((core.VERSION, core.build()), None, 0) + '\n'

    def test_from_the_clone_it_was_installed_from_with_the_same_choices(self):
        self.source(self.clone)
        code, out = self.run_cli('update')
        self.assertEqual((code, out), (0, 'Updating from %s\n' % self.clone + self.after()))
        self.assertEqual(self.args(), ['--no-open', '--no-setup', '--no-agent'])    # no choices file: the menu bar too
        # what this install has, it keeps: the background job, its own Python, its port
        agents = os.path.join(self.home, 'Library', 'LaunchAgents')
        os.makedirs(agents)
        open(os.path.join(agents, cli.AGENT + '.plist'), 'w').close()
        os.makedirs(os.path.join(self.data, 'runtime'))
        with open(os.path.join(self.data, 'runtime', 'python.json'), 'w') as f:
            json.dump({'python_source': 'own'}, f)
        core.set_setting('port', 7861)
        self.run_cli('update')
        self.assertEqual(self.args(), ['--no-open', '--no-setup', '--own-python', '--port', '7861'])

    def choices(self, text):
        os.makedirs(os.path.join(self.data, 'app'), exist_ok=True)
        with open(os.path.join(self.data, 'app', 'choices'), 'w') as f:
            f.write(text + '\n')

    def test_the_menu_bar_follows_the_choice_made_at_install(self):
        self.source(self.clone)
        self.choices('--no-agent')              # wanted at install, not there now (its packages failed): tried again
        self.run_cli('update')
        self.assertNotIn('--no-menu-app', self.args())
        self.choices('--no-agent --no-menu-app')    # turned off at install: it stays off
        self.run_cli('update')
        self.assertIn('--no-menu-app', self.args())

    def test_an_install_with_no_choices_file_keeps_the_menu_bar_it_has(self):
        self.source(self.clone)
        os.makedirs(os.path.join(self.data, 'bin'), exist_ok=True)
        with open(os.path.join(self.data, 'bin', 'switchboard-menubar'), 'w') as f:
            f.write('#!/bin/sh\n')
        self.run_cli('update')
        self.assertNotIn('--no-menu-app', self.args())

    def test_link_app_adds_the_link_app_to_the_same_choices(self):
        self.source(self.clone)
        code, _ = self.run_cli('update', '--link-app')
        self.assertEqual(code, 0)
        self.assertEqual(self.args(), ['--no-open', '--no-setup', '--no-agent', '--link-app'])
        code, out = self.run_cli('update', '--other')
        self.assertEqual((code, out), (2, 'switchboard update [--link-app] [--menu-app | --no-menu-app] [--port N]\n'
                                        'switchboard update --check [--json]\n'))

    def test_a_link_app_it_has_is_built_again(self):
        self.source(self.clone)
        self.run_cli('update')
        self.assertFalse({'--link-app', '--no-link-app'} & set(self.args()))     # none: none is added
        app = os.path.join(self.home, 'Applications', 'Switchboard Links.app', 'Contents')
        os.makedirs(os.path.join(app, 'MacOS'))
        with open(os.path.join(app, 'Info.plist'), 'wb') as f:
            plistlib.dump({'CFBundleIdentifier': linkapp.BUNDLE_ID, 'CFBundleExecutable': 'SwitchboardLinks'}, f)
        open(os.path.join(app, 'MacOS', 'SwitchboardLinks'), 'w').close()
        self.run_cli('update')
        self.assertIn('--link-app', self.args())

    def test_from_the_address_it_was_installed_from(self):
        self.source('https://example.invalid/switchboard/install.sh')
        fetched = []
        with mock.patch.object(cli, 'fetch', lambda url: fetched.append(url) or self.script.encode()):
            code, out = self.run_cli('update')
        self.assertEqual((code, out),
                         (0, 'Updating from https://example.invalid/switchboard/install.sh\n' + self.after()))
        self.assertEqual(fetched, ['https://example.invalid/switchboard/install.sh'])
        self.assertEqual(self.args()[:2], ['--no-open', '--no-setup'])

    def test_when_it_cannot_tell_it_says_so(self):
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('update')
        self.assertIn('does not know where it was installed from', str(e.exception))
        self.source(os.path.join(self.tmp, 'moved'))
        with self.assertRaises(core.SwitchError) as e:
            self.run_cli('update')
        self.assertIn('which is not there any more', str(e.exception))
        self.source('http://example.invalid/install.sh')         # only https is fetched
        with self.assertRaises(core.SwitchError):
            self.run_cli('update')
        self.assertFalse(os.path.exists(self.args_file))

    def test_an_installer_that_fails_fails_the_update(self):
        with open(os.path.join(self.clone, 'install.sh'), 'w') as f:
            f.write('#!/bin/sh\nexit 3\n')
        self.source(self.clone)
        self.assertEqual(self.run_cli('update')[0], 3)


class TheHelp(unittest.TestCase):
    def test_it_names_the_new_commands_and_no_alias(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(['help'])
        for words in ('switchboard                 your accounts', 'switchboard 2', 'switchboard status',
                      'switchboard add', 'switchboard setup [--yes]', 'switchboard page',
                      'switchboard use NAME', 'switchboard update', 'switchboard doctor'):
            self.assertIn(words, out.getvalue())


if __name__ == '__main__':
    unittest.main()
