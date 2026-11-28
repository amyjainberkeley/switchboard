import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The menu bar (switchboard/menubar.py and its wrapper app, menubar_wrap.py), without AppKit: the state as the item
# and the menu rows, their words, the bars' geometry, the red rule, the refresh timing, the page's own POSTs, starting
# the page, one menu bar at a time, the wrapper app, the command and doctor's lines. The S and the clean menu have
# their own file (test_menubar_s_and_menu.py), and so do on, off and staying on (test_menubar_off_on.py).
#
# Made-up accounts only. Nothing here draws, opens a menu, puts an item in the menu bar or runs launchctl; a running
# page is a stand-in on a free port, or the real server in demo mode on a free port.
import copy
import io
import json
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
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
from switchboard import core, demo, menubar, menubar_wrap, server, term

TZ = timezone(timedelta(hours=-8))                                      # Fri Nov 13 2026 is in winter time
NOW = datetime(2026, 11, 13, 15, 31, 25, tzinfo=TZ).timestamp()          # Fri 3:31 PM
REAL_PAGES = (7798, 7799)                                                # a real page's and the demo's: never reached from here
SAT_NOON = '2026-11-14T19:59:59.651000+00:00'                            # 11:59:59.65 as Anthropic sends it: noon


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ).isoformat()


def w(pct, resets=None):
    return {'pct': pct, 'resets': resets, 'pace': None, 'lasts': None}


def acct(slot, email, session=None, weekly=None, scoped=(), **more):
    """One account as the page's state has it (server.render)."""
    return dict({'email': email, 'name': email, 'alias': '', 'slot': slot, 'active': False, 'needs_login': False,
                 'disabled': False, 'unmanaged': False, 'trusted': True, 'line': None,
                 'usage': {'session': session, 'weekly': weekly, 'scoped': list(scoped), 'spend': None},
                 'usage_at': NOW - 60}, **more)


def view(**more):
    """Five made-up accounts, listed in queue order as the engine has them (not slot
    order): 5 is in use, 4 is next."""
    return dict({'now': NOW, 'active': 'ana@mail.example', 'next': 'ana@lab.example',
                 'auto': {'available': True, 'enabled': True, 'on': True, 'session_at': 90.0, 'weekly_at': 95.0,
                          'model_at': 90.0, 'weekly_set': True},
                 'setup': {'engine': True, 'accounts': True},
                 'accounts': [
                     acct(5, 'ana@mail.example', weekly=w(27, SAT_NOON), active=True,
                          scoped=[{'name': 'Fable', 'pct': 22, 'resets': SAT_NOON}]),
                     acct(4, 'ana@lab.example', weekly=w(65, at(15, 14)),
                          scoped=[{'name': 'Fable', 'pct': 90, 'resets': at(15, 14)}]),
                     acct(3, 'ana@harbor.example', session=w(0), weekly=w(17, at(16, 15)),
                          scoped=[{'name': 'Fable', 'pct': 30, 'resets': at(16, 15)}]),
                     acct(1, 'ana@northwind.example', weekly=w(0, at(19, 17))),
                     acct(2, 'ana@fernhill.example', weekly=w(94, at(16, 13)),
                          scoped=[{'name': 'Fable', 'pct': 81, 'resets': at(16, 13)}])]}, **more)


def find(state, email):
    return next(a for a in state['accounts'] if a['email'] == email)


def row_of(state, email):
    return next(r for r in menubar.rows(state, TZ) if r['email'] == email)


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def guarded(real):
    def urlopen(url, *a, **k):
        port = urllib.parse.urlparse(getattr(url, 'full_url', url)).port
        if port in REAL_PAGES:
            raise AssertionError('a test reached for the page on port %d' % port)
        return real(url, *a, **k)
    return urlopen


def guarded_open(real):
    """The same guard for an opener's open() (the menu bar's calls go through one that skips proxies)."""
    def open_(self, url, *a, **k):
        port = urllib.parse.urlparse(getattr(url, 'full_url', url)).port
        if port in REAL_PAGES:
            raise AssertionError('a test reached for the page on port %d' % port)
        return real(self, url, *a, **k)
    return open_


def write_exe(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    os.chmod(path, 0o755)


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-menubar-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        os.makedirs(self.home)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'SWITCHBOARD_PORT': str(free_port())})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(menubar.HOPPED, None)
        self.apps = os.path.join(self.tmp, 'Applications')          # never the real /Applications
        os.makedirs(self.apps)
        for p in (mock.patch.object(urllib.request, 'urlopen', guarded(urllib.request.urlopen)),
                  mock.patch.object(urllib.request.OpenerDirector, 'open',
                                    guarded_open(urllib.request.OpenerDirector.open)),
                  mock.patch.object(menubar_wrap, 'APPLICATIONS', self.apps)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(menubar.release_lock)


# ---------- the state, as the menu rows ----------

class Rows(unittest.TestCase):
    def test_slot_order_and_the_email_as_the_name(self):
        rows = menubar.rows(view(), TZ)
        self.assertEqual([(r['slot'], r['email']) for r in rows],
                         [(1, 'ana@northwind.example'), (2, 'ana@fernhill.example'), (3, 'ana@harbor.example'),
                          (4, 'ana@lab.example'), (5, 'ana@mail.example')])

    def test_in_use_next_and_use(self):
        marks = dict((r['email'], r['marks']) for r in menubar.rows(view(), TZ))
        self.assertEqual(marks['ana@mail.example'], [('In use', 'in-use')])
        self.assertEqual(marks['ana@lab.example'], [('Next', 'next')])           # one word; a click still uses it
        self.assertEqual(marks['ana@northwind.example'], [('Use', 'use')])
        rows = dict((r['email'], r) for r in menubar.rows(view(), TZ))
        self.assertIsNone(rows['ana@mail.example']['action'])          # the account in use is not a button
        self.assertEqual(rows['ana@lab.example']['action'], 'use')

    def test_next_only_while_auto_switch_is_on_and_never_the_one_in_use_or_one_to_sign_in_again(self):
        off = view()
        off['auto']['enabled'] = False
        self.assertEqual([r for r in menubar.rows(off, TZ) if r['next']], [])
        self.assertIsNone(menubar.next_email(view(next='ana@mail.example')))
        s = view()
        find(s, 'ana@lab.example')['needs_login'] = True
        self.assertIsNone(menubar.next_email(s))
        older = view()                  # an older server leaves Next to the plan
        del older['next']
        older['plan'] = {'next': 'ana@harbor.example'}
        self.assertEqual(menubar.next_email(older), 'ana@harbor.example')

    def test_sign_in_again_and_an_account_switchboard_does_not_hold(self):
        s = view()
        find(s, 'ana@harbor.example')['needs_login'] = True
        find(s, 'ana@northwind.example')['unmanaged'] = True
        harbor, north = row_of(s, 'ana@harbor.example'), row_of(s, 'ana@northwind.example')
        self.assertEqual((harbor['marks'], harbor['action']), ([('Sign in again', 'use')], 'signin'))
        self.assertEqual((north['marks'], north['action']), ([], None))
        self.assertIsNone(menubar.click(harbor))
        self.assertIsNone(menubar.click(north))
        self.assertEqual(menubar.click(row_of(s, 'ana@lab.example')), ('/api/switch', {'email': 'ana@lab.example'}))

    def test_two_limits_with_their_percent_and_no_models_week(self):
        harbor = row_of(view(), 'ana@harbor.example')
        self.assertEqual((harbor['five']['label'], menubar.pct_text(harbor['five'])), ('5-hour', '0%'))
        self.assertEqual((harbor['week']['label'], menubar.pct_text(harbor['week'])), ('Weekly', '17%'))
        lab = row_of(view(), 'ana@lab.example')
        self.assertIsNone(lab['five'])                  # no 5-hour window (Ultra): no 5-hour limit, no 5-hour row
        self.assertEqual((lab['week']['label'], menubar.pct_text(lab['week'])), ('Weekly', '65%'))
        self.assertNotIn('Fable', repr(menubar.row_ops(lab)))      # a model's week at its line is the page's to say
        self.assertEqual([l['kind'] for l in menubar.limits(find(view(), 'ana@northwind.example'), view()['auto'])],
                         ['week'])                                          # no 5-hour window: no 5-hour limit

    def test_a_reading_not_known_is_a_dash_and_a_bare_track_with_no_reset_line(self):
        s = view()
        find(s, 'ana@fernhill.example')['trusted'] = False           # the engine would not decide on it
        find(s, 'ana@northwind.example')['usage'] = None
        find(s, 'ana@harbor.example')['usage']['weekly'] = {'resets': at(16, 15)}
        # (no 5-hour bar either: it is drawn only when there is a 5-hour number to show. A reading too old to show still
        # tells the plan: fernhill's has no 5-hour window, so its 5-hour place says "No 5-hour limit"; northwind was
        # never read, so its row is a bar row less)
        for email, height in (('ana@fernhill.example', 72.0), ('ana@northwind.example', 54.0)):
            r = row_of(s, email)
            self.assertEqual((r['five'], menubar.pct_text(r['week'])), (None, '\u2014'))
            self.assertEqual(menubar.row_height(r), height)
            self.assertFalse([op for op in menubar.row_ops(r) if op[0] == 'box' and op[-1] in ('fill', 'accent', 'red')])
        self.assertEqual(menubar.pct_text(row_of(s, 'ana@harbor.example')['week']), '\u2014')
        self.assertEqual(menubar.row_height(row_of(s, 'ana@lab.example')), 72.0)      # no 5-hour limit: its words

    def test_the_same_numbers_and_next_as_the_terminal(self):
        s = view()
        find(s, 'ana@lab.example')['usage']['session'] = w(89.6, at(13, 17))
        ours = menubar.rows(s, TZ)
        theirs = term.read(s, tz=TZ)['accounts']
        self.assertEqual([r['email'] for r in ours], [a['email'] for a in theirs])
        for r, a in zip(ours, theirs):
            bars = [(x['pct'], x['over']) for x in (r['five'], r['week']) if x is not None]
            self.assertEqual(r['five'] is None and not r['no_five'], a['limits'][0]['label'] != '5-hour')  # the same
            self.assertEqual(r['no_five'].lower(), a['limits'][0]['words'] if a['limits'][0].get('none') else '')
            drawn = [l for l in a['limits'] if not l.get('none')]                       # the words have no bar
            self.assertEqual(bars, [(l['pct'], l['over']) for l in drawn[:len(bars)]])
            self.assertEqual(drawn[len(bars):], [])                          # the terminal draws no model week
            self.assertEqual((r['next'], r['in_use']), (a['next'], a['active']))


class RedRule(unittest.TestCase):
    def lims(self, a, auto=None):
        return menubar.limits(a, view()['auto'] if auto is None else auto)

    def test_red_only_at_or_over_a_switch_line(self):
        # A model's week comes after the weekly limit only when it is at or over its line (the note), so it is red
        # whenever it is there; under its line it is not there at all.
        a = acct(1, 'a@x.example', session=w(89.6), weekly=w(94.4), scoped=[{'name': 'Fable', 'pct': 89.4}])
        five, week = self.lims(a)
        self.assertEqual((five['pct'], five['over']), (90, True))        # 89.6 reads 90%, and 90 is the line
        self.assertEqual((week['pct'], week['over']), (94, False))
        five, week, fable = self.lims(acct(1, 'a@x.example', session=w(12), weekly=w(95),
                                           scoped=[{'name': 'Fable', 'pct': 90}]))
        self.assertEqual((five['over'], week['over'], fable['over']), (False, True, True))     # the model line set: 90
        lims = self.lims(acct(1, 'a@x.example', session=w(12), weekly=w(10), scoped=[{'name': 'Fable', 'pct': 94}]), {})
        self.assertEqual([l['kind'] for l in lims], ['five', 'week'])                          # none set: 95, under it
        fable = self.lims(acct(1, 'a@x.example', session=w(12), weekly=w(10), scoped=[{'name': 'Fable', 'pct': 92}]),
                          dict(view()['auto'], model_at=92))[2]
        self.assertEqual((fable['line'], fable['over']), (92, True))                           # or the one set

    def test_each_window_against_automatic_switchings_own_line(self):
        a = acct(1, 'a@x.example', session=w(85), weekly=w(88), line=80)       # a line of its own is not read
        five, week = self.lims(a)[:2]
        self.assertEqual((five['line'], five['over'], week['line'], week['over']), (90.0, False, 95.0, False))
        five, week = self.lims(acct(1, 'a@x.example', session=w(91), weekly=w(94)), {})[:2]   # no lines: 90 and 95
        self.assertEqual((five['line'], five['over'], week['line'], week['over']), (90.0, True, 95.0, False))

    def test_red_beats_blue_in_a_row_and_the_item_turns_red(self):
        s = view()
        find(s, 'ana@mail.example')['usage']['session'] = w(92, at(13, 18, 10))
        r = row_of(s, 'ana@mail.example')
        fills = [op[-1] for op in menubar.row_ops(r) if op[0] == 'box' and op[-1] in ('fill', 'accent', 'red')]
        self.assertEqual(fills, ['red', 'accent'])
        pcts = [op[5][0] for op in menubar.row_ops(r) if op[0] == 'text' and op[6] == 'right']
        self.assertEqual(pcts, [('92%', 12.0, 500, 'red'), ('27%', 12.0, 500, 'ink')])
        it = menubar.reading(s)
        self.assertEqual(it['dot'], 'orange')                 # the item's S gets its orange dot
        self.assertEqual([op[-1] for op in menubar.item_ops(it) if op[0] == 'dot'], ['ink', 'orange'])

    def test_a_models_week_never_puts_a_dot_on_the_item(self):
        self.assertIsNone(menubar.reading(view())['dot'])      # ana@lab.example's Fable week is at its line


# ---------- the item ----------

class Item(unittest.TestCase):
    def test_the_fill(self):
        self.assertEqual(menubar.fill_width(0, 18.0, 4.0), 0.0)
        self.assertEqual(menubar.fill_width(None, 18.0, 4.0), 0.0)
        self.assertEqual(menubar.fill_width(1, 18.0, 4.0), 4.0)      # at least one round end when anything is used
        self.assertEqual(menubar.fill_width(0.4, 18.0, 4.0), 4.0)
        self.assertEqual(menubar.fill_width(50, 18.0, 4.0), 9.0)
        self.assertEqual(menubar.fill_width(100, 18.0, 4.0), 18.0)
        self.assertEqual(menubar.fill_width(150, 18.0, 4.0), 18.0)
        self.assertEqual(menubar.fill_width(True, 18.0, 4.0), 0.0)

    def test_the_account_in_use_and_its_words(self):
        r = menubar.reading(view())
        self.assertEqual(r['label'], 'Switchboard: ana@mail.example, weekly 27%')     # no 5-hour limit: no 5-hour word
        self.assertEqual((r['email'], r['dot'], r['dim']), ('ana@mail.example', None, False))
        r = menubar.reading(view(active='ana@harbor.example'))
        self.assertEqual(r['label'], 'Switchboard: ana@harbor.example, 5-hour 0%, weekly 17%')

    def test_not_known_with_no_account_and_with_no_page(self):
        s = view()
        find(s, 'ana@mail.example')['trusted'] = False
        r = menubar.reading(s)
        self.assertEqual(r['label'], 'Switchboard: ana@mail.example, weekly \u2014')
        self.assertEqual((r['dot'], r['dim']), (None, True))          # not known: the S dimmed
        down = {'kind': 'down', 'stale': False, 'line': "Switchboard isn't running"}       # Watch: refused twice
        for state, status, words in ((None, down, "Switchboard isn't running"),
                                     (view(accounts=[]), None, 'Switchboard: no accounts yet'),
                                     (view(active='someone@else.example'), None, 'Switchboard: no account in use')):
            r = menubar.reading(state, status)
            self.assertEqual((r['label'], r['dot']), (words, 'red'))


# ---------- the menu ----------

class Menu(unittest.TestCase):
    def test_rows_then_auto_switch_add_account_and_open_switchboard(self):
        items = menubar.menu(view(), tz=TZ)
        self.assertEqual([x[1]['slot'] for x in items if x[0] == 'row'], [1, 2, 3, 4, 5])
        self.assertEqual(items[5:], [('separator',), ('auto', True, 'at 90% 5-hour \u00b7 95% weekly', True),
                                     ('separator',), ('do', 'Add account', 'add'), ('do', 'Open Switchboard', 'open')])

    def test_auto_switch_words_and_state(self):
        s = view()
        s['auto'].update(enabled=False, session_at=92.5, weekly_at=97)
        self.assertIn(('auto', False, 'at 92.5% 5-hour \u00b7 97% weekly', True), menubar.menu(s))
        s['auto']['available'] = False                        # no engine: it can't be changed
        self.assertIn(('auto', False, 'at 92.5% 5-hour \u00b7 97% weekly', False), menubar.menu(s))

    def test_when_the_page_is_not_running(self):
        self.assertEqual(menubar.menu(None), [('line', "Switchboard isn't running"), ('do', 'Start', 'start')])

    def test_before_there_is_an_account(self):
        first = view(accounts=[acct(None, 'new@mail.example', unmanaged=True, active=True)],
                     setup={'engine': True, 'accounts': False})
        self.assertEqual(menubar.menu(first), [('line', 'Claude Code is on new@mail.example'), ('separator',),
                                               ('do', 'Add account', 'add'), ('do', 'Open Switchboard', 'open')])
        self.assertEqual(menubar.menu(view(accounts=[]))[0], ('line', 'Add your first account'))
        self.assertEqual(menubar.menu(view(setup={'engine': False}))[0], ('line', 'Finish installing Switchboard'))

    def test_a_failed_click_is_one_line_at_the_top(self):
        items = menubar.menu(view(), note="Couldn't switch: That account is not set up here.")
        self.assertEqual(items[0], ('note', "Couldn't switch: That account is not set up here."))
        long = menubar.failed("Couldn't switch", 'x' * 200)
        self.assertEqual((len(long), long[-1]), (72, '\u2026'))

    def test_a_menu_whose_shape_is_the_same_changes_in_place(self):
        a, b = view(), view()
        find(b, 'ana@lab.example')['usage']['weekly']['pct'] = 66
        self.assertEqual(menubar.shape(menubar.menu(a)), menubar.shape(menubar.menu(b)))
        find(b, 'ana@harbor.example')['trusted'] = False               # a row grows or shrinks: made again
        self.assertNotEqual(menubar.shape(menubar.menu(a)), menubar.shape(menubar.menu(b)))
        self.assertNotEqual(menubar.shape(menubar.menu(a)), menubar.shape(menubar.menu(view(accounts=view()['accounts'][:4]))))

    def test_a_rows_layout_follows_spec_section_8(self):
        s = view()
        find(s, 'ana@lab.example')['usage']['session'] = w(0)          # a 5-hour window, so both bar rows
        r = row_of(s, 'ana@lab.example')
        ops = menubar.row_ops(r, hover=True)
        self.assertEqual(ops[0], ('box', 6.0, 0.0, 348.0, 72.0, 8.0, 'hover'))
        self.assertEqual(ops[1], ('pair', 16.0, 8.0, 328.0, 20.0, [('ana@lab.example', 13.0, 500, 'ink')],
                                  [[('Next', 12.0, 400, 'ink2')]], 8.0))
        labels = [op for op in ops if op[0] == 'text' and op[6] == 'left']
        self.assertEqual([(op[1], op[2], op[3], op[4], op[5][0][0]) for op in labels],
                         [(16.0, 28.0, 48.0, 18.0, '5-hour'), (16.0, 46.0, 48.0, 18.0, 'Weekly')])
        tracks = [op for op in ops if op[0] == 'box' and op[-1] == 'track']
        self.assertEqual([op[1:6] for op in tracks], [(72.0, 35.0, 232.0, 4.0, 2.0), (72.0, 53.0, 232.0, 4.0, 2.0)])
        pcts = [op for op in ops if op[0] == 'text' and op[6] == 'right']
        self.assertEqual([op[1:5] for op in pcts], [(312.0, 28.0, 32.0, 18.0), (312.0, 46.0, 32.0, 18.0)])
        self.assertNotIn('hover', [op[-1] for op in menubar.row_ops(row_of(view(), 'ana@mail.example'), hover=True)])
        self.assertEqual(menubar.row_label(r), 'Use ana@lab.example, next, 5-hour 0%, weekly 65%')


# ---------- when to read ----------

class Pace(unittest.TestCase):
    def test_every_15_s_closed_and_every_2_s_open(self):
        p = menubar.Pace()
        self.assertEqual(p.wait(100.0, False), 0.0)               # the first reading at once
        p.started(100.0)
        self.assertEqual(p.wait(100.0, False), 15.0)
        self.assertEqual(p.wait(110.0, False), 5.0)
        self.assertEqual(p.wait(100.5, True), 1.5)               # opened half a second after a reading: never sooner
        self.assertEqual(p.wait(110.0, True), 0.0)               # opened ten seconds after: at once
        self.assertTrue(p.due(115.0, False))

    def test_never_more_often_than_every_2_s(self):
        p = menubar.Pace()
        p.started(100.0)
        p.soon()                                                 # a click right after a reading
        self.assertEqual(p.wait(100.5, False), 1.5)
        self.assertEqual(p.wait(100.5, True), 1.5)
        p.started(102.0)
        self.assertEqual(p.wait(103.0, False), 14.0)             # read: back to the closed pace
        p.soon()
        self.assertEqual(p.wait(110.0, False), 0.0)
        for now in (102.0, 102.5, 103.9):
            self.assertGreater(p.wait(now, True), 0.0)

    def test_after_start_it_looks_at_the_open_pace_for_a_while(self):
        p = menubar.Pace()
        p.started(100.0)
        p.hurry(100.0)
        self.assertEqual(p.wait(100.0, False), 2.0)
        self.assertEqual(p.every(100.0 + menubar.STARTING + 1, False), 15.0)


# ---------- the page: reading its state, and its own POSTs ----------

class StandIn(ThreadingHTTPServer):
    def __init__(self, answer, code=200):
        ThreadingHTTPServer.__init__(self, ('127.0.0.1', 0), StandInHandler)
        self.answer, self.code, self.posts = answer, code, []
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def stop(self):
        self.shutdown()
        self.server_close()


class StandInHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, body, code):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send(self.server.answer, self.server.code if self.path == '/api/state' else 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        self.server.posts.append((self.path, body, self.headers.get('X-Switchboard'), self.headers.get('Host'),
                                  self.headers.get('Origin')))
        self.send(self.server.answer, self.server.code)


class Page(Sandbox):
    def stand_in(self, answer, code=200):
        page = StandIn(answer, code)
        self.addCleanup(page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(page.server_address[1])
        return page

    def test_it_reads_the_running_pages_state(self):
        self.stand_in(view())
        self.assertEqual(menubar.get_state(), view())

    def test_no_page_or_an_answer_that_is_not_a_state_is_none(self):
        self.assertIsNone(menubar.get_state())                   # nothing listens on this free port
        self.stand_in({'error': 'forbidden'}, 403)
        self.assertIsNone(menubar.get_state())
        self.stand_in({'accounts': 'no'})
        self.assertIsNone(menubar.get_state())

    def test_a_post_is_the_pages_own_json_with_its_header_from_this_mac(self):
        page = self.stand_in({'ok': True})
        self.assertEqual(menubar.post('/api/switch', {'email': 'ana@lab.example'}), (True, {'ok': True}))
        path, body, header, host, origin = page.posts[0]
        self.assertEqual((path, body, header, origin), ('/api/switch', {'email': 'ana@lab.example'}, '1', None))
        self.assertEqual(host, '127.0.0.1:%d' % page.server_address[1])

    def test_a_refusal_comes_back_in_the_pages_words(self):
        self.stand_in({'error': 'That account is not set up here.'}, 400)
        self.assertEqual(menubar.post('/api/switch', {'email': 'x@y.example'}),
                         (False, 'That account is not set up here.'))
        os.environ['SWITCHBOARD_PORT'] = str(free_port())
        self.assertEqual(menubar.post('/api/auto', {'enabled': False}), (False, "Switchboard isn't running"))


class TheRealServer(Sandbox):
    """The page's own server (demo mode, on a free port) takes the menu bar's POSTs, under its own rules."""

    def setUp(self):
        super().setUp()
        demo._ready()
        saved = (copy.deepcopy(demo.STATE), copy.deepcopy(demo.FACTS))

        def put_back():
            demo.STATE.clear()
            demo.STATE.update(saved[0])
            demo.FACTS.clear()
            demo.FACTS.update(saved[1])
        self.addCleanup(put_back)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        os.environ['SWITCHBOARD_PORT'] = str(httpd.server_address[1])

    def test_switch_and_auto_switch_go_through(self):
        before = menubar.get_state()
        r = menubar.reading(before)
        self.assertEqual(r['email'], before['active'])
        # the first account to use whose sign-in is not about to end (the demo's fernhill says when, beside In use)
        other = next(x['email'] for x in menubar.rows(before) if x['action'] == 'use' and not x['signin'])
        self.assertEqual(menubar.post(*menubar.click(next(x for x in menubar.rows(before) if x['email'] == other)))[0],
                         True)
        after = menubar.get_state()
        self.assertEqual(after['active'], other)
        self.assertEqual([x[1]['marks'] for x in menubar.menu(after) if x[0] == 'row' and x[1]['email'] == other],
                         [[('In use', 'in-use')]])
        self.assertTrue(menubar.post('/api/auto', {'enabled': False})[0])
        # the lines the rows show, as the page's switch line says them: no model week (Settings says that one)
        self.assertIn(('auto', False, 'at 90% 5-hour \u00b7 95% weekly', True),
                      menubar.menu(menubar.get_state()))

    def test_without_its_header_the_page_says_no(self):
        req = urllib.request.Request(menubar.page_url('/api/switch'), data=b'{}', headers={'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        e.exception.close()
        self.assertEqual(e.exception.code, 403)


# ---------- starting the page, as its LaunchAgent does ----------

class StartThePage(Sandbox):
    def setUp(self):
        super().setUp()
        self.calls, self.answers = [], []
        p = mock.patch.object(menubar, 'launchctl', lambda *a: self.calls.append(list(a)) or self.answers.pop(0))
        p.start()
        self.addCleanup(p.stop)

    def plist(self):
        path = menubar.agent_plist()
        os.makedirs(os.path.dirname(path))
        with open(path, 'w') as f:
            f.write('<plist/>')
        return path

    def test_through_the_pages_job_when_the_installer_added_it(self):
        plist = self.plist()
        self.answers = [0]
        with mock.patch.object(menubar.subprocess, 'Popen') as popen:
            self.assertEqual(menubar.start_page(), 'job')
        self.assertEqual(self.calls, [['kickstart', 'gui/%d/io.github.amyjainberkeley.switchboard' % os.getuid()]])
        self.assertFalse(popen.called)
        self.calls, self.answers = [], [3, 0]                    # not loaded: loaded again, and it starts at load
        self.assertEqual(menubar.start_page(), 'job')
        self.assertEqual(self.calls, [['kickstart', 'gui/%d/io.github.amyjainberkeley.switchboard' % os.getuid()],
                                      ['bootstrap', 'gui/%d' % os.getuid(), plist]])

    def test_without_the_job_switchboard_serve_in_the_background_with_its_path_and_log(self):
        launcher = os.path.join(self.data, 'bin', 'switchboard')
        write_exe(launcher, '#!/bin/sh\nexit 0\n')
        with mock.patch.object(menubar.subprocess, 'Popen') as popen:
            self.assertEqual(menubar.start_page(), 'serve')
        args, kw = popen.call_args
        self.assertEqual(args[0], [launcher, 'serve'])
        self.assertTrue(kw['start_new_session'])
        self.assertEqual(kw['stdin'], subprocess.DEVNULL)
        self.assertEqual(kw['stdout'].name, os.path.join(self.data, 'agent.log'))
        self.assertTrue(kw['env']['PATH'].startswith(os.path.join(self.home, '.local', 'bin') + ':'))
        self.assertIn(':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin', kw['env']['PATH'])
        self.assertEqual(self.calls, [])

    def test_from_a_checkout_it_runs_this_program(self):
        with mock.patch.object(menubar.subprocess, 'Popen') as popen:
            menubar.start_page()
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[1:5], ['-I', '-B', '-c', menubar.RUNPY])
        self.assertEqual(cmd[5:], [os.path.dirname(core.PACKAGE), 'serve'])


# ---------- one menu bar at a time ----------

HOLD = '''import sys, time
sys.path.insert(0, sys.argv[1])
from switchboard import menubar
print('held' if menubar.take_lock() else 'not', flush=True)
sys.stdin.read()
'''


def hold(test):
    """Another menu bar: a child process that takes the lock and keeps it until its input closes."""
    child = subprocess.Popen([sys.executable, '-B', '-c', HOLD, ROOT], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             text=True)
    test.addCleanup(child.wait, 10)
    test.addCleanup(child.stdout.close)
    test.addCleanup(child.stdin.close)
    test.assertEqual(child.stdout.readline().strip(), 'held')
    return child


class OneAtATime(Sandbox):
    def test_a_second_menu_bar_steps_back_and_doctor_sees_the_first(self):
        self.assertIsNone(menubar.running_pid())
        self.assertFalse(os.path.exists(menubar.lock_path()))     # looking makes nothing
        child = hold(self)
        self.assertEqual(menubar.running_pid(), child.pid)
        self.assertFalse(menubar.take_lock())
        child.stdin.close()
        child.wait(10)
        self.assertIsNone(menubar.running_pid())
        self.assertTrue(menubar.take_lock())
        self.assertEqual(core.read_json(menubar.stamp_path())['pid'], os.getpid())


# ---------- `switchboard menubar` ----------

class Command(Sandbox):
    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(['menubar'] + list(args))
        return code, out.getvalue()

    def test_the_help_names_it(self):
        self.assertIn('switchboard menubar [on | off | --applications]', cli.__doc__)

    def test_words_it_does_not_know(self):
        self.assertEqual(self.run_cli('--nope'), (2, 'switchboard menubar [on | off | --applications]\n'))

    def test_without_pyobjc_it_goes_to_the_menu_bars_launcher(self):
        launcher = menubar.launcher()
        write_exe(launcher, '#!/bin/sh\nexit 0\n')
        ran = []
        with mock.patch.object(menubar, 'appkit_here', lambda: False), \
                mock.patch.object(menubar.os, 'execv', lambda exe, argv: ran.append((exe, argv))):
            self.run_cli('--applications')
        self.assertEqual(ran, [(launcher, [launcher, '--applications'])])
        self.assertEqual(os.environ.get(menubar.HOPPED), '1')

    def test_with_neither_one_line_names_the_command_that_adds_it(self):
        ran = []
        with mock.patch.object(menubar, 'appkit_here', lambda: False), mock.patch.object(menubar, 'terminal', lambda: True), \
                mock.patch.object(menubar.os, 'execv', lambda exe, argv: ran.append(exe)):
            self.assertEqual(self.run_cli(),
                             (1, 'The menu bar is not set up here. Add it with: switchboard update --menu-app\n'))
            write_exe(menubar.launcher(), '#!/bin/sh\nexit 0\n')
            os.environ[menubar.HOPPED] = '1'                    # its Python has no PyObjC either: no loop
            self.assertEqual(self.run_cli()[0], 1)
        self.assertEqual(ran, [])

    def test_without_a_terminal_and_without_pyobjc_it_exits_0(self):
        # under its job it first waits (test_menubar_off_on.WhenItCannotRun): launchd starts it again on any exit
        with mock.patch.object(menubar, 'appkit_here', lambda: False), mock.patch.object(menubar, 'terminal', lambda: False):
            self.assertEqual(self.run_cli(), (0, menubar.NOT_HERE + '\n'))

    def job(self, program):
        path = menubar.agent_plist(menubar.AGENT)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            plistlib.dump({'Label': menubar.AGENT, 'ProgramArguments': [program], 'RunAtLoad': True}, f)
        return path

    def test_in_a_terminal_with_its_job_the_job_starts_it(self):
        self.job(menubar.launcher())
        calls, ran = [], []
        with mock.patch.object(menubar, 'appkit_here', lambda: True), mock.patch.object(menubar, 'terminal', lambda: True), \
                mock.patch.object(menubar, 'launchctl', lambda *a: calls.append(list(a)) or 0), \
                mock.patch.object(menubar, 'run', lambda: ran.append(1) or 0):
            self.assertEqual(self.run_cli(), (0, 'The menu bar is on.\n'))
            self.assertEqual(calls,
                             [['kickstart', 'gui/%d/io.github.amyjainberkeley.switchboard.menubar' % os.getuid()]])
            self.assertEqual(ran, [])
            self.job('/Users/someone/.switchboard/bin/switchboard-menubar')   # the job of another data folder
            self.assertEqual(self.run_cli(), (0, ''))
        self.assertEqual((len(calls), ran), (1, [1]))                       # not started instead: it ran here

    def test_otherwise_it_runs_here(self):
        ran = []
        with mock.patch.object(menubar, 'appkit_here', lambda: True), mock.patch.object(menubar, 'terminal', lambda: False), \
                mock.patch.object(menubar, 'run', lambda: ran.append(1) or 0):
            self.assertEqual(self.run_cli(), (0, ''))
        self.assertEqual(ran, [1])

    def test_applications(self):
        apps = self.apps
        with mock.patch.object(menubar, 'appkit_here', lambda: True), \
                mock.patch.object(menubar_wrap, 'make', lambda where, executable=None, prefix=None: where):
            self.assertEqual(self.run_cli('--applications'),
                             (0, "The menu bar's app is in %s.\n" % os.path.join(apps, 'Switchboard.app')))
            os.makedirs(os.path.join(apps, 'Switchboard.app', 'Contents'))       # someone else's Switchboard.app
            code, out = self.run_cli('--applications')
        # left alone; the app goes to ~/Applications instead, as the installer puts it
        self.assertEqual((code, out), (0, "%s is another app, so it was left alone.\nThe menu bar's app is in "
                                          "~/Applications/Switchboard.app.\n" % os.path.join(apps, 'Switchboard.app')))
        self.assertEqual(os.listdir(os.path.join(apps, 'Switchboard.app')), ['Contents'])


# ---------- the wrapper app ----------

class Wrapper(Sandbox):
    def fake_python(self, version='3.12'):
        """A made-up install: bin/python3.12 (a few bytes), its lib/libpython3.12.dylib, and an environment."""
        base = os.path.join(self.tmp, 'python')
        exe = os.path.join(base, 'bin', 'python' + version)
        write_exe(exe, '#!/bin/sh\necho made-up\n')
        os.utime(exe, (1700000000, 1700000000))
        os.makedirs(os.path.join(base, 'lib'), exist_ok=True)
        with open(os.path.join(base, 'lib', 'libpython%s.dylib' % version), 'w') as f:
            f.write('lib')
        env = os.path.join(self.tmp, 'env')
        os.makedirs(os.path.join(env, 'lib', 'python' + version, 'site-packages'), exist_ok=True)
        return exe, env

    def test_it_builds_the_contract_app(self):
        exe, env = self.fake_python()
        where = os.path.join(self.tmp, 'apps')
        python = menubar_wrap.make(where, executable=exe, prefix=env)
        app = os.path.join(where, 'Switchboard.app')
        self.assertEqual(python, os.path.join(app, 'Contents', 'MacOS', 'python'))
        info = menubar_wrap.info(app)
        self.assertEqual((info['CFBundleIdentifier'], info['CFBundleName'], info['CFBundleExecutable'], info['LSUIElement']),
                         ('io.github.amyjainberkeley.switchboard.menubar', 'Switchboard', 'python', True))
        with open(os.path.join(app, 'Contents', 'pyvenv.cfg')) as f:
            self.assertEqual(f.read(), 'home = %s\ninclude-system-site-packages = false\n'
                             % os.path.dirname(os.path.realpath(exe)))
        lib = os.path.join(app, 'Contents', 'lib')
        self.assertEqual(sorted(os.listdir(lib)), ['libpython3.12.dylib', 'python3.12'])
        self.assertEqual(os.readlink(os.path.join(lib, 'python3.12')), os.path.join(env, 'lib', 'python3.12'))
        self.assertEqual(os.readlink(os.path.join(lib, 'libpython3.12.dylib')),
                         os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(exe))), 'lib', 'libpython3.12.dylib'))
        with open(python, 'rb') as a, open(exe, 'rb') as b:
            self.assertEqual(a.read(), b.read())                 # a byte copy, not a link
        self.assertFalse(os.path.islink(python))
        self.assertEqual(os.stat(python).st_mtime_ns, os.stat(exe).st_mtime_ns)
        self.assertTrue(menubar_wrap.inside_app(python))
        self.assertFalse(menubar_wrap.inside_app(exe))

    def test_a_second_build_writes_nothing_that_is_right_and_drops_what_went_stale(self):
        exe, env = self.fake_python()
        where = os.path.join(self.tmp, 'apps')
        python = menubar_wrap.make(where, executable=exe, prefix=env)
        contents = os.path.dirname(os.path.dirname(python))
        files = [python, os.path.join(contents, 'Info.plist'), os.path.join(contents, 'pyvenv.cfg')]
        before = [(os.stat(p).st_ino, os.stat(p).st_mtime_ns) for p in files]
        os.symlink(os.path.join(self.tmp, 'an-older-env', 'lib', 'python3.11'), os.path.join(contents, 'lib', 'python3.11'))
        time.sleep(0.01)
        menubar_wrap.make(where, executable=exe, prefix=env)
        self.assertEqual([(os.stat(p).st_ino, os.stat(p).st_mtime_ns) for p in files], before)
        self.assertNotIn('python3.11', os.listdir(os.path.join(contents, 'lib')))
        with open(exe, 'a') as f:                                 # a new Python: copied again
            f.write('# newer\n')
        menubar_wrap.make(where, executable=exe, prefix=env)
        with open(python, 'rb') as a, open(exe, 'rb') as b:
            self.assertEqual(a.read(), b.read())

    def test_where_it_lives(self):
        apps, data = self.apps, os.path.join(self.data, 'menubar')
        exe, env = self.fake_python()
        self.assertEqual(menubar_wrap.home(), data)
        self.assertIsNone(menubar_wrap.present())
        menubar_wrap.make(executable=exe, prefix=env)
        self.assertEqual(menubar_wrap.present(), os.path.join(data, 'Switchboard.app'))
        self.assertEqual(menubar_wrap.install_in_applications(executable=exe, prefix=env),
                         os.path.join(apps, 'Switchboard.app'))
        self.assertEqual(menubar_wrap.home(), apps)              # put there when asked, it is used from then on
        self.assertEqual(menubar_wrap.present(), os.path.join(apps, 'Switchboard.app'))
        os.chmod(apps, 0o555)                                    # /Applications that can't be written to
        try:
            self.assertEqual(menubar_wrap.home(), data)
            self.assertIsNone(menubar_wrap.install_in_applications(executable=exe, prefix=env))
        finally:
            os.chmod(apps, 0o755)
        shutil.rmtree(os.path.join(apps, 'Switchboard.app'))
        os.makedirs(os.path.join(apps, 'Switchboard.app', 'Contents'))     # someone else's Switchboard.app
        with self.assertRaises(menubar_wrap.NotOurs):
            menubar_wrap.install_in_applications(executable=exe, prefix=env)
        self.assertEqual(menubar_wrap.home(), data)
        self.assertEqual(os.listdir(os.path.join(apps, 'Switchboard.app')), ['Contents'])   # left as it was

    def test_when_it_stays_put(self):
        with mock.patch.object(menubar_wrap, 'framework', lambda build=None: False):
            self.assertEqual(menubar_wrap.relaunch_reason('linux', {}), 'not macOS')
            self.assertEqual(menubar_wrap.relaunch_reason('darwin', {'SWITCHBOARD_MENUBAR_NO_BUNDLE': '1'}),
                             'SWITCHBOARD_MENUBAR_NO_BUNDLE is set')
            self.assertIsNone(menubar_wrap.relaunch_reason('darwin', {}))
        with mock.patch.object(menubar_wrap, 'framework', lambda build=None: True):
            self.assertEqual(menubar_wrap.relaunch_reason('darwin', {}), 'a framework build of Python')

    def test_it_execs_into_the_app_or_says_why_not(self):
        ran, err = [], io.StringIO()
        with mock.patch.object(menubar_wrap, 'relaunch_reason', lambda: None), \
                mock.patch.object(menubar_wrap, 'make', lambda: '/made/up/Switchboard.app/Contents/MacOS/python'), \
                mock.patch.object(menubar_wrap.os, 'execv', lambda exe, argv: ran.append((exe, argv))), \
                mock.patch.object(menubar_wrap.sys, 'stderr', err):
            with mock.patch.object(menubar_wrap, 'python_problem', lambda p: None):
                menubar_wrap.relaunch_inside(['menubar'])
            with mock.patch.object(menubar_wrap, 'python_problem', lambda p: 'dyld: Library not loaded'):
                self.assertEqual(menubar_wrap.relaunch_inside(['menubar']),
                                 "the app's interpreter does not start: dyld: Library not loaded")
        exe = '/made/up/Switchboard.app/Contents/MacOS/python'
        self.assertEqual(ran, [(exe, [exe, '-I', '-B', '-c', menubar_wrap.RUNPY, os.path.dirname(core.PACKAGE), 'menubar'])])
        self.assertIn('menu bar managers will not see it', err.getvalue())


# ---------- doctor ----------

class Doctor(Sandbox):
    def lines(self):
        got = []
        cli._doctor_menubar(lambda good, text, fix='': got.append((good, text, fix)))
        return got

    def engine_python(self, said, code=0):
        write_exe(os.path.join(self.data, 'runtime', 'env', 'bin', 'python'), '#!/bin/sh\necho "%s"\nexit %d\n' % (said, code))

    def test_not_installed(self):
        self.assertEqual(self.lines(), [(None, 'Menu bar: not installed', 'Add it with: switchboard update --menu-app')])

    def test_installed_not_running_no_job_no_app_yet(self):
        write_exe(menubar.launcher(), '#!/bin/sh\nexit 0\n')
        self.engine_python('13.0 3.12.13 0')
        ran = []
        real = cli.subprocess.run
        with mock.patch.object(cli.subprocess, 'run', lambda cmd, **k: ran.append(cmd) or real(cmd, **k)):
            got = self.lines()
        self.assertEqual([g[:2] for g in got], [
            (True, 'Menu bar on Python 3.12.13, PyObjC 13.0 (%s)' % menubar.launcher()),
            (None, 'Menu bar job (starts it at login)'), (None, 'Menu bar item: installed, not running'),
            (None, 'Menu bar app: not made yet')])
        self.assertEqual(got[3][2], 'Put it in Applications with: switchboard menubar --applications')
        self.assertFalse([c for c in ran if c and c[0] == 'launchctl'])        # no job added: launchd is not asked

    def test_its_packages_do_not_load(self):
        write_exe(menubar.launcher(), '#!/bin/sh\nexit 0\n')
        self.engine_python('Traceback', 1)
        self.assertEqual(self.lines()[0][:2], (False, 'Menu bar (%s)' % menubar.launcher()))

    def test_a_framework_python_says_what_to_do(self):
        write_exe(menubar.launcher(), '#!/bin/sh\nexit 0\n')
        self.engine_python('13.0 3.14.2 1')
        self.assertEqual(self.lines()[3][2], menubar.FRAMEWORK)
        # a command anyone can type (a curl | sh install has no installer file to give --own-python to): the
        # installer moves the menu bar off a framework Python whenever it is added
        self.assertTrue(menubar.FRAMEWORK.endswith('. Run: switchboard update'), menubar.FRAMEWORK)
        self.assertNotIn('--own-python', menubar.FRAMEWORK)

    def test_running_with_its_app(self):
        write_exe(menubar.launcher(), '#!/bin/sh\nexit 0\n')
        self.engine_python('13.0 3.12.13 0')
        exe = os.path.join(self.tmp, 'python', 'bin', 'python3.12')
        write_exe(exe, '#!/bin/sh\n')
        os.makedirs(os.path.join(self.tmp, 'env', 'lib'))
        menubar_wrap.make(self.apps, executable=exe, prefix=os.path.join(self.tmp, 'env'))   # where the installer puts it
        child = hold(self)
        got = self.lines()
        self.assertEqual(got[2][:2], (True, 'Menu bar item: on (pid %d)' % child.pid))
        self.assertEqual(got[3][:2], (True, 'Menu bar app (%s)' % os.path.join(self.apps, 'Switchboard.app')))
        child.stdin.close()


# ---------- a reply it can't finish, a proxy, a switch, a look at the lock, PyObjC that fails ----------

MY_PORT = 7873          # a port of this file's own; a free one when another run holds it


class RawPage(object):
    """A stand-in on 127.0.0.1:7873 that reads each request whole, then answers these bytes and hangs up: a reply cut
    off part-way (the page restarting under `switchboard update`), or one that is not HTTP (another program). seen:
    the first line of each request. A connection that sends nothing is no request (a look that only connects, as the
    menu bar's watch on the page makes one): it is not in seen and gets no answer. wait_for(n): seen, once it holds
    n requests."""

    def __init__(self, reply):
        self.reply = reply
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(('127.0.0.1', MY_PORT))
        except OSError:
            self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.seen = []
        self.came = threading.Condition()
        threading.Thread(target=self.serve, daemon=True).start()

    def wait_for(self, n, within=60.0):
        """seen, once it holds n requests (or what came within `within` seconds: the assertion then says what)."""
        with self.came:
            self.came.wait_for(lambda: len(self.seen) >= n, within)
            return list(self.seen)

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(10)
                try:
                    data = b''
                    while b'\r\n\r\n' not in data:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        data += chunk
                    head, _, body = data.partition(b'\r\n\r\n')
                    length = next((int(h.split(b':', 1)[1]) for h in head.split(b'\r\n')
                                   if h.lower().startswith(b'content-length:')), 0)
                    while len(body) < length:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        body += chunk
                    line = head.split(b'\r\n')[0].decode()
                    if not line.strip():
                        continue                    # nothing was asked: no request, no answer
                    with self.came:
                        self.seen.append(line)
                        self.came.notify_all()
                    conn.sendall(self.reply)
                    conn.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

    def stop(self):
        self.sock.close()


CUT_OFF = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n{"accounts": [{"ema'
NOT_HTTP = b'garbage\r\n\r\n'
NOT_JSON = b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 7\r\n\r\n<html/>'


class Replies(Sandbox):
    """menubar-1 (the calls) and menubar-3: whatever answers on the page's port, a reading is a state or None and a
    click is (ok, words); neither ever raises. And the page is never asked through a proxy."""

    def raw(self, reply):
        page = RawPage(reply)
        self.addCleanup(page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(page.port)
        return page

    def test_a_reply_cut_off_or_not_http_is_no_state_and_a_failed_click(self):
        for reply in (CUT_OFF, NOT_HTTP, NOT_JSON):
            page = self.raw(reply)
            self.assertIsNone(menubar.get_state())
            self.assertEqual(menubar.post('/api/switch', {'email': 'ana@lab.example'}),
                             (False, "the page's answer could not be read"))
            # it was asked, and answered: its own two requests, waited for, never a time
            self.assertEqual([x.split()[0] for x in page.wait_for(2)], ['GET', 'POST'])
        self.assertEqual(menubar.failed("Couldn't switch", menubar.UNREADABLE),
                         "Couldn't switch: the page's answer could not be read")

    def test_a_look_that_only_connects_is_no_request(self):
        # The menu bar's watch on the page only opens a connection and sends nothing (watch.page_answers): one that
        # lands while a test talks to the stand-in is not among the requests it counts.
        page = self.raw(NOT_JSON)
        socket.create_connection(('127.0.0.1', page.port), timeout=5).close()
        self.assertIsNone(menubar.get_state())
        self.assertEqual([x.split()[0] for x in page.wait_for(1)], ['GET'])

    def test_nothing_on_the_port_is_still_not_running(self):
        os.environ['SWITCHBOARD_PORT'] = str(free_port())
        self.assertIsNone(menubar.get_state())
        self.assertEqual(menubar.post('/api/auto', {'enabled': True}), (False, "Switchboard isn't running"))

    def test_a_proxy_is_never_used_for_the_page(self):
        page = StandIn(view())
        self.addCleanup(page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(page.server_address[1])
        with mock.patch.dict(os.environ, {'http_proxy': 'http://127.0.0.1:9', 'HTTP_PROXY': 'http://127.0.0.1:9',
                                          'all_proxy': 'http://127.0.0.1:9'}), \
                mock.patch.object(urllib.request, '_opener', None):     # as in a menu bar just started: proxies read now
            for name in ('no_proxy', 'NO_PROXY'):
                os.environ.pop(name, None)
            self.assertEqual(urllib.request.getproxies().get('http'), 'http://127.0.0.1:9')     # a proxy is set
            self.assertEqual(menubar.get_state(), view())
            self.assertEqual(menubar.post('/api/switch', {'email': 'ana@lab.example'}), (True, view()))
        self.assertEqual([p[0] for p in page.posts], ['/api/switch'])


class FreshRows(unittest.TestCase):
    """menubar-2: a row's item is made again when what a click on it does changes (its enabled state, its key)."""

    def test_a_switch_changes_the_shape(self):
        before = menubar.menu(view(), tz=TZ)
        after = menubar.menu(view(active='ana@lab.example'), tz=TZ)          # moved to lab: mail can be used now
        self.assertEqual([x[1]['action'] for x in before if x[0] == 'row' and x[1]['email'] == 'ana@lab.example'], ['use'])
        self.assertEqual([x[1]['action'] for x in after if x[0] == 'row' and x[1]['email'] == 'ana@lab.example'], [None])
        self.assertNotEqual(menubar.shape(before), menubar.shape(after))

    def test_a_new_slot_changes_the_shape(self):
        moved = view()
        find(moved, 'ana@mail.example')['slot'] = 6                       # still last: only its 1-9 key changes
        self.assertNotEqual(menubar.shape(menubar.menu(view())), menubar.shape(menubar.menu(moved)))

    def test_new_numbers_alone_still_change_it_in_place(self):
        a, b = view(), view()
        find(b, 'ana@lab.example')['usage']['weekly']['pct'] = 66
        find(b, 'ana@fernhill.example')['usage']['weekly']['pct'] = 99
        self.assertEqual(menubar.shape(menubar.menu(a)), menubar.shape(menubar.menu(b)))


LOOK = '''import fcntl, os, sys, time
sys.path.insert(0, sys.argv[1])
from switchboard import menubar
fd = os.open(menubar.lock_path(), os.O_RDONLY)
fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)          # what running_pid() holds while it looks
print('looking', flush=True)
time.sleep(float(sys.argv[2]))
'''


class LockWait(Sandbox):
    """menubar-7: a menu bar starting while doctor looks at the lock does not step back."""

    def test_it_waits_for_a_look_to_end(self):
        self.assertTrue(menubar.take_lock())
        menubar.release_lock()                                   # the lock file is there, free
        child = subprocess.Popen([sys.executable, '-B', '-c', LOOK, ROOT, '0.6'], stdout=subprocess.PIPE, text=True)
        self.addCleanup(child.wait, 10)
        self.addCleanup(child.stdout.close)
        self.assertEqual(child.stdout.readline().strip(), 'looking')
        t0 = time.time()
        self.assertTrue(menubar.take_lock())
        self.assertLess(time.time() - t0, menubar.LOCK_WAIT)

    def test_another_menu_bar_still_wins_after_the_wait(self):
        child = hold(self)
        t0 = time.time()
        self.assertFalse(menubar.take_lock())
        self.assertGreaterEqual(time.time() - t0, menubar.LOCK_WAIT - 0.1)
        child.stdin.close()


class AFailedStart(Sandbox):
    """menubar-5: PyObjC that is found but does not load is one line; under its job the exit is 0 (launchd would
    start a job that failed again every 20 s), at a terminal 1."""

    def run_it(self, at_terminal, error):
        out, err = io.StringIO(), io.StringIO()
        # menubar.run() starts the menu bar's watch on the page first: here it is a stand-in, never a thread that
        # outlives the test and opens a connection to whatever port a later test's stand-in holds, every 15 s
        with mock.patch.object(menubar_wrap, 'relaunch_inside', lambda args: 'a test'), \
                mock.patch.object(menubar, '_watch_start', lambda: None), \
                mock.patch.object(menubar, '_ui', mock.Mock(side_effect=error)), \
                mock.patch.object(menubar, 'terminal', lambda: at_terminal), \
                mock.patch.object(menubar.sys, 'stderr', err), redirect_stdout(out):
            code = menubar.run()
        menubar.release_lock()
        return code, out.getvalue()

    def test_a_failed_start_leaves_no_watch_on_the_page_running(self):
        # run() starts the watch first: a test's run must not leave one behind, opening a connection to whatever port
        # a later test's stand-in holds
        def watching():
            return [t for t in threading.enumerate() if t.name == 'switchboard-page-watch' and t.is_alive()]
        before = watching()
        self.run_it(True, ImportError('made up'))
        self.assertEqual(watching(), before)

    def test_one_line_and_the_right_exit(self):
        broken = ImportError("dlopen(_objc.cpython-312-darwin.so, 0x0002): Symbol not found: _PyFoo\n  Referenced from: ...")
        line = ("The menu bar can't start: dlopen(_objc.cpython-312-darwin.so, 0x0002): Symbol not found: _PyFoo. "
                "Run: switchboard update\n")
        self.assertEqual(self.run_it(False, broken), (0, line))
        self.assertEqual(self.run_it(True, broken), (1, line))          # nothing says it is on first
        self.assertEqual(self.run_it(False, AttributeError('NSFontWeightMedium'))[0], 0)

    def test_with_another_menu_bar_running_it_steps_back_first(self):
        child = hold(self)
        with mock.patch.object(menubar, 'LOCK_WAIT', 0.1):
            code, out = self.run_it(False, ImportError('x'))
        self.assertEqual((code, out), (0, 'The menu bar is already running.\n'))
        child.stdin.close()


def stand_in_pyobjc(calls):
    """objc, AppKit, Foundation and PyObjCTools as plain Python, just enough for menubar._ui() to make its classes:
    any name not given here is a MagicMock. Nothing is drawn, no status item is made and no event loop runs;
    AppHelper.callAfter puts each call in `calls`, which the test empties as AppKit's main thread would."""
    import types

    def module(name, **names):
        m = types.ModuleType(name)
        m.__dict__.update(names)

        def missing(attr):
            if attr.startswith('__'):
                raise AttributeError(attr)
            return mock.MagicMock(name='%s.%s' % (name, attr))
        m.__getattr__ = missing
        return m

    class NSObject(object):
        @classmethod
        def alloc(cls):
            return cls.__new__(cls)

        def init(self):
            return self

    class NSView(NSObject):
        def initWithFrame_(self, frame):
            return self

    helper = module('PyObjCTools.AppHelper', callAfter=lambda f, *a: calls.put((f, a)),
                    installMachInterrupt=lambda: None)
    return {'objc': module('objc', super=super, python_method=lambda f: f),
            'AppKit': module('AppKit', NSView=NSView), 'Foundation': module('Foundation', NSObject=NSObject),
            'PyObjCTools': module('PyObjCTools', AppHelper=helper), 'PyObjCTools.AppHelper': helper}


class Workers(Sandbox):
    """menubar-1 (the threads): read(), send() and start_() of the real menu bar class, on stand-in AppKit modules,
    always hear back, so `reading` never stays set and a click always ends in a note or a new reading."""

    def setUp(self):
        super().setUp()
        import queue
        self.calls = queue.Queue()
        p = mock.patch.dict(sys.modules, stand_in_pyobjc(self.calls))
        p.start()
        self.addCleanup(p.stop)
        menubar._UI.clear()
        self.addCleanup(menubar._UI.clear)
        self.bar = menubar._ui()['MenuBar'].alloc().init()
        self.bar.status, self.bar.menu = mock.MagicMock(), mock.MagicMock()

    def main_thread(self):
        """Run the next call handed back to AppKit's main thread."""
        f, args = self.calls.get(timeout=15)
        f(*args)

    def raw(self, reply):
        page = RawPage(reply)
        self.addCleanup(page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(page.port)

    def test_a_reading_cut_off_or_not_http_still_comes_back(self):
        for reply in (CUT_OFF, NOT_HTTP):
            self.raw(reply)
            self.bar.read()
            self.assertTrue(self.bar.reading)
            self.main_thread()
            self.assertFalse(self.bar.reading)              # the next tick reads again
            self.assertIsNone(self.bar.state)
            self.assertIsNotNone(self.bar.timer)            # and the next reading is scheduled

    def test_a_reading_that_raises_anything_still_comes_back(self):
        err = io.StringIO()
        with mock.patch.object(menubar, 'get_state', mock.Mock(side_effect=RuntimeError('made up'))), \
                mock.patch.object(sys, 'stderr', err):
            self.bar.read()
            self.main_thread()
        self.assertEqual((self.bar.reading, self.bar.state), (False, None))
        self.assertIn('RuntimeError: made up', err.getvalue())         # said in its log

    def test_a_click_on_a_page_that_answers_garbage_leaves_a_note(self):
        self.raw(NOT_HTTP)
        self.bar.send("Couldn't switch", '/api/switch', {'email': 'ana@lab.example'})
        self.main_thread()
        self.assertEqual(self.bar.note[0], "Couldn't switch: the page's answer could not be read")
        with mock.patch.object(menubar, 'post', mock.Mock(side_effect=ValueError('made up'))), \
                mock.patch.object(sys, 'stderr', io.StringIO()):
            self.bar.send("Couldn't change Auto-switch", '/api/auto', {'enabled': True})
            self.main_thread()
        self.assertEqual(self.bar.note[0], "Couldn't change Auto-switch: the page's answer could not be read")

    def test_start_always_hears_back(self):
        with mock.patch.object(menubar, 'start_page', mock.Mock(side_effect=RuntimeError('launchctl went away'))), \
                mock.patch.object(sys, 'stderr', io.StringIO()):
            self.bar.start_(None)
            self.main_thread()
        self.assertEqual(self.bar.note[0], "Couldn't start Switchboard: launchctl went away")
        self.bar.note = None
        with mock.patch.object(menubar, 'start_page', lambda: 'job'):
            self.bar.start_(None)
            self.main_thread()                              # handed back even when it worked
        self.assertIsNone(self.bar.note)

    def test_in_background_hands_back_the_answer_or_the_failure(self):
        got = []
        with mock.patch.object(sys, 'stderr', io.StringIO()):
            menubar.in_background(lambda: 42, got.append, lambda e: 'failed', lambda f, *a: f(*a))
            menubar.in_background(lambda: 1 / 0, got.append, lambda e: e.__class__.__name__, lambda f, *a: f(*a))
            deadline = time.time() + 10
            while len(got) < 2 and time.time() < deadline:
                time.sleep(0.01)
        self.assertEqual(sorted(map(str, got)), ['42', 'ZeroDivisionError'])


# ---------- it imports without PyObjC ----------

class NoPyObjC(unittest.TestCase):
    def test_the_pure_parts_never_import_appkit(self):
        tmp = tempfile.mkdtemp(prefix='switchboard-nopyobjc-')
        self.addCleanup(shutil.rmtree, tmp, True)
        for name in ('objc', 'AppKit', 'Foundation', 'PyObjCTools'):
            with open(os.path.join(tmp, name + '.py'), 'w') as f:
                f.write('raise ImportError("%s was imported")\n' % name)
        code = ('import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]\n'
                'from switchboard import menubar, menubar_wrap, __main__\n'
                's = {"accounts": [], "setup": {}}\n'
                'menubar.rows(s); menubar.menu(s); menubar.menu(None); menubar.item_ops(menubar.reading(None))\n'
                'try:\n    menubar._ui()\nexcept ImportError as e:\n    print(e)\n')
        r = subprocess.run([sys.executable, '-B', '-c', code, tmp, ROOT], capture_output=True, text=True,
                           env=dict(os.environ, SWITCHBOARD_HOME=os.path.join(tmp, 'data')))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, 'objc was imported'), r.stderr)


if __name__ == '__main__':
    unittest.main()
