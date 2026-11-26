import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# No false 5-hour reading for an account that has no 5-hour limit.
#
# Anthropic renamed Max 20x "Ultra" and dropped its 5-hour limit for verified accounts. The usage endpoint then sends
# no five_hour (or five_hour null), the engine's reading has no 5-hour window, and the page's state keeps session
# None. A Team account still gets one: {utilization 0, resets_at null} while nothing was used in the last five hours.
# The readings below are those answers as the engine's api hands them (accounts.api.Reading), drawn the way the page
# draws them (engine.usage_of).
#
# Made-up *.example accounts only. No network, no real engine, Claude Code or browser profile; the page runs in
# headless Chrome with a throwaway profile, served on a free port by the real handler in demo mode.
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, demo, engine, history, menubar, notify, server, statusline, term
from switchboard.accounts import api
import test_app  # its headless-Chrome helper

TZ = timezone(timedelta(hours=-8))                                      # Fri Nov 13 2026 is in winter time
NOW = datetime(2026, 11, 13, 15, 31, 25, tzinfo=TZ).timestamp()          # Fri 3:31 PM
SAT_NOON = '2026-11-14T19:59:59.651000+00:00'                            # 11:59:59.65 as Anthropic sends it: noon
D, H = 86400, 3600
ULTRA, IDLE, BUSY, UNREAD = 'ana@ultra.example', 'ana@team.example', 'ana@busy.example', 'ana@unread.example'
NOT_STARTED = {'pct': 0.0, 'resets': None, 'pace': None, 'lasts': None}


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ).isoformat()


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


# The engine's readings (accounts.api.Reading), with this file's numbers and times.
def _t(text):
    return core.parse_iso(text) if text else None


def ultra_row(week=27.0, fable=22.0, resets=SAT_NOON):
    """A verified Ultra (Max 20x) account: no 5-hour window at all."""
    return api.Reading('ultra', (api.Window('weekly', week, _t(resets)), api.Window('model:Fable', fable, _t(resets))),
                       NOW - 30, 'endpoint')


def team_row(five=0.0, five_resets=None, week=17.0, resets=at(16, 15)):
    """A Team account: a 5-hour window at 0 with no reset until something is used in a five-hour window."""
    return api.Reading('team', (api.Window('5-hour', five, _t(five_resets)), api.Window('weekly', week, _t(resets))),
                       NOW - 30, 'endpoint')


def usage(row):
    return engine.usage_of(row)


def acct(slot, email, plan, u, **more):
    """One account as server.render hands it to the page, the menu bar and the terminal."""
    return dict({'email': email, 'name': email, 'alias': '', 'slot': slot, 'plan': plan, 'org': '', 'active': False,
                 'needs_login': False, 'disabled': False, 'unmanaged': False, 'trusted': True, 'line': None,
                 'usage': u, 'usage_at': NOW - 30}, **more)


def view(**more):
    """An Ultra account in use, a Team account that has not started its five hours, one that has, and an Ultra account
    whose reading is too old to trust."""
    return dict({'now': NOW, 'active': ULTRA, 'next': None, 'setup': {'engine': True, 'accounts': True},
                 'auto': {'available': True, 'enabled': True, 'on': True, 'session_at': 90.0, 'weekly_at': 95.0,
                          'weekly_set': True},
                 'accounts': [acct(1, ULTRA, 'Ultra', usage(ultra_row()), active=True),
                              acct(2, IDLE, 'Team', usage(team_row())),
                              acct(3, BUSY, 'Team', usage(team_row(40.0, at(13, 18, 10), 70.0, at(16, 12)))),
                              acct(4, UNREAD, 'Ultra', usage(ultra_row(5.0, 1.0)), trusted=False,
                                   usage_at=NOW - 7200)]}, **more)


def in_use(v, email):
    for a in v['accounts']:
        a['active'] = a['email'] == email
    v['active'] = email
    return v


class TheReadingsAsTheEngineGivesThem(unittest.TestCase):
    """The shapes the rest of this file stands on (a guard)."""

    def test_an_ultra_reading_has_no_5_hour_window_and_a_team_one_not_started_is_at_0_with_no_reset(self):
        self.assertIsNone(usage(ultra_row())['session'])
        self.assertEqual(usage(team_row())['session'], NOT_STARTED)
        self.assertEqual(usage(team_row(40.0, at(13, 18, 10)))['session']['pct'], 40.0)


# ---------- the terminal ----------

class TheTerminal(unittest.TestCase):
    def limits(self, v=None):
        return dict((a['email'], [(l['label'], l['pct'], l['words']) for l in a['limits']])
                    for a in term.read(v or view(), tz=TZ)['accounts'])

    def test_no_5_hour_line_for_an_account_with_no_5_hour_limit(self):
        got = self.limits()
        self.assertEqual(got[ULTRA], [('weekly', 27, 'resets Sat 12:00 PM · in 20 hours')])   # Fable's week: no line
        blocks = '\n'.join(term.view(term.read(view(), tz=TZ))).split('\n\n')[1:]      # after the header line
        ultra = next(b for b in blocks if ULTRA in b)
        self.assertNotRegex(ultra, r'(?m)^\s+5-hour\b')
        self.assertRegex(ultra, r'(?m)^   weekly  ━{6}─{18}   27%  resets Sat 12:00 PM')
        self.assertRegex(next(b for b in blocks if IDLE in b), r'(?m)^   5-hour  ─{24}    0%  not started$')

    def test_a_5_hour_window_at_0_with_no_reset_has_not_started(self):
        got = self.limits()
        self.assertEqual(got[IDLE][0], ('5-hour', 0, 'not started'))
        self.assertEqual(got[BUSY][0], ('5-hour', 40, 'resets 6:10 PM · in 3 hours'))
        v = view()
        v['accounts'][2]['usage']['session']['resets'] = None       # used, its reset not known: started
        self.assertEqual(self.limits(v)[BUSY][0], ('5-hour', 40, 'started'))

    def test_a_reading_not_known_is_a_weekly_dash_alone(self):
        # (it was a dash for both: the 5-hour line is now drawn only when there is a 5-hour number to show)
        self.assertEqual(self.limits()[UNREAD], [('weekly', None, '')])
        v = view()
        v['accounts'][0]['usage'] = None                             # no reading at all
        self.assertEqual(self.limits(v)[ULTRA], [('weekly', None, '')])


# ---------- the menu bar ----------

class TheMenuBar(unittest.TestCase):
    def row(self, email, v=None):
        return next(r for r in menubar.rows(v or view(), TZ) if r['email'] == email)

    def test_a_row_with_no_5_hour_limit_has_no_5_hour_bar(self):
        r = self.row(ULTRA)
        self.assertIsNone(r['five'])
        ops = menubar.row_ops(r)
        self.assertEqual([(op[2], op[5][0][0]) for op in ops if op[0] == 'text'], [(28.0, 'Weekly'), (28.0, '27%')])
        self.assertEqual([op[2] for op in ops if op[0] == 'pair'], [8.0, 46.0])      # the reset line under the weekly bar
        self.assertEqual(menubar.row_height(r), 70.0)
        self.assertEqual(menubar.row_label(r), 'ana@ultra.example, in use, checked just now, weekly 27%')
        v = view()
        v['accounts'][0]['usage'].update(weekly={'pct': None, 'resets': SAT_NOON}, scoped=[])   # weekly not known
        r = self.row(ULTRA, v)
        self.assertEqual((r['foot'], r['models'], menubar.row_height(r)), ('', [], 54.0))

    def test_a_5_hour_window_at_0_with_no_reset_has_not_started(self):
        r = self.row(IDLE)
        self.assertEqual((r['five']['label'], menubar.pct_text(r['five'])), ('5-hour', '0%'))
        self.assertEqual(menubar.reset_line(r['five'], NOW, TZ), 'Not started')
        self.assertEqual(menubar.row_height(r), 88.0)
        self.assertEqual(menubar.row_label(r), 'Use ana@team.example, checked just now, 5-hour 0%, weekly 17%')
        busy = self.row(BUSY)
        self.assertEqual(busy['foot'], '5-hour resets 6:10 PM · weekly Mon 12:00 PM')

    def test_a_reading_not_known_is_a_weekly_dash_alone(self):
        # (it kept both dashes: the 5-hour row is now drawn only when there is a 5-hour number to show)
        r = self.row(UNREAD)
        self.assertEqual((r['five'], menubar.pct_text(r['week'])), (None, '—'))
        self.assertEqual(menubar.row_height(r), 54.0)

    def test_the_item_shows_the_weekly_bar_alone_in_the_middle(self):
        r = menubar.reading(view())
        self.assertIsNone(r['five'])
        self.assertEqual(r['label'], 'Switchboard: ana@ultra.example, weekly 27%')
        self.assertEqual(menubar.item_ops(r), [('box', 0.0, 3.0, 18.0, 4.0, 2.0, 'ink30'),
                                               ('box', 0.0, 3.0, menubar.fill_width(27.0), 4.0, 2.0, 'ink')])
        v = view()
        v['accounts'][0]['usage']['weekly']['pct'] = 96.0              # at its weekly line: red, from that bar alone
        r = menubar.reading(v)
        self.assertTrue(r['red'])
        self.assertEqual(menubar.item_ops(r)[-1][-1], 'sysred')
        # an account with a 5-hour limit keeps its two bars, 5-hour on top
        r = menubar.reading(in_use(view(), BUSY))
        self.assertEqual([op[2] for op in menubar.item_ops(r) if op[-1] == 'ink30'], [0.0, 6.0])
        self.assertEqual(r['label'], 'Switchboard: ana@busy.example, 5-hour 40%, weekly 70%')
        # no account in use: two bare tracks
        r = menubar.reading(in_use(view(), None))
        self.assertEqual(menubar.item_ops(r), [('box', 0.0, 0.0, 18.0, 4.0, 2.0, 'ink30'),
                                               ('box', 0.0, 6.0, 18.0, 4.0, 2.0, 'ink30')])


# ---------- the status line, notifications and the history ----------

class TheStatusLine(unittest.TestCase):
    """Claude Code's own numbers: an Ultra account's carry no five_hour (a guard)."""

    def test_no_5_hour_part_without_a_5_hour_window(self):
        for rl in ({'seven_day': {'used_percentage': 30, 'resets_at': NOW + 2 * D}},
                   {'five_hour': None, 'seven_day': {'used_percentage': 30, 'resets_at': NOW + 2 * D}}):
            with self.subTest(rl=sorted(rl)):
                line = statusline.build({'rate_limits': rl, 'model': {'display_name': 'Opus'}},
                                        {'active': ULTRA, 'next': None}, now=NOW, colour=False)
                self.assertTrue(line.startswith('● ana@ultra.example on Opus · 70% left · refills '), line)
                self.assertNotIn('5h', line)


class NotesAndHistory(unittest.TestCase):
    """No notification and no history line about a 5-hour limit an account does not have (a guard)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-readings-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {'HOME': os.path.join(self.tmp, 'home'),
                                           'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data')})
        env.start()
        self.addCleanup(env.stop)

    def states(self, before, after):
        return ({'active': ULTRA, 'accounts': {ULTRA: {'alias': 'ultra', 'slot': 1, 'usage': usage(ultra_row(before))}}},
                {'active': ULTRA, 'accounts': {ULTRA: {'alias': 'ultra', 'slot': 1, 'usage': usage(ultra_row(after))}}})

    def test_only_the_weekly_limit_is_ever_near(self):
        before, after = self.states(80.0, 96.0)
        notes = notify.from_states(before, after, line={'five_hour': 80, 'seven_day': 85}, now=NOW)
        self.assertEqual([n.key for n in notes], ['near:%s:seven_day' % ULTRA])     # by its email, not its short name
        self.assertNotIn('5-hour', ' '.join(n.title + ' ' + n.body for n in notes))

    def test_the_history_says_only_the_weekly_line(self):
        before, after = self.states(80.0, 96.0)
        st = core.blank_state()
        core.ensure_account(st, ULTRA)
        history.note_tick(st, before, after, {'session_line': 90, 'weekly_line': 95, 'weekly_set': True}, now=NOW)
        said = [e['text'] for e in st['events']]
        self.assertEqual(said, ['ana@ultra.example reached its weekly switch line: 96%'])


# ---------- the account in use, on Claude Code's own live numbers ----------

class LiveNumbers(unittest.TestCase):
    """server.apply_live_numbers: the status line's live record stands in for the engine's older reading of the account
    in use. Claude Code leaves a window out once its reset has passed, and an Ultra account never has a 5-hour one, so a
    record with no five_hour says nothing by itself: the account's own reading tells which it is."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-readings-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, 'home')
        env = mock.patch.dict(os.environ, {'HOME': home, 'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data'),
                                           'CLAUDE_CONFIG_DIR': os.path.join(home, '.claude')})
        env.start()
        self.addCleanup(env.stop)
        for k in ('SWITCHBOARD_PORT', 'SWITCHBOARD_STATE', 'SWITCHBOARD_ENGINE'):
            os.environ.pop(k, None)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.now = time.time()
        self.week = self.now + 2 * D

    def state(self, row):
        """The account in use, its engine reading twenty minutes old (so a fresh live record stands in for it)."""
        st = core.blank_state()
        a = core.ensure_account(st, ULTRA)
        a.update(connected=True, slot=1, usage_status='ok', usage_error=None, note=None, usage=usage(row),
                 usage_at=self.now - 1200)
        st['active'] = ULTRA
        st['timeline'] = [[ULTRA, self.now - 3 * H]]
        return st

    def live(self, rate_limits, age=60):
        rec = statusline.live_record({'session_id': 's1', 'rate_limits': rate_limits}, {'active': ULTRA},
                                     self.now - age, env={})
        os.makedirs(statusline.live_dir(), exist_ok=True)
        with open(os.path.join(statusline.live_dir(), 's1.json'), 'w') as f:
            json.dump(rec, f)

    def shown(self, st):
        """The account's limits as the terminal prints them, from server.render (the menu bar and the page read it)."""
        facts = {'profiles': {}, 'browser': {'name': 'Google Chrome', 'present': False, 'tabs_blocked': False},
                 'engine': {'installed': True, 'claude': True}, 'links': {'installed': False, 'on': False},
                 'auto': {'available': True, 'enabled': False, 'session_at': 90, 'weekly_at': 95},
                 'setup_hidden': True, 'now': self.now, 'demo': False}
        return [(l['label'], l['pct'], l['words'].split(' · ')[0][:6])
                for l in term.read(server.render(st, facts))['accounts'][0]['limits']]

    def test_an_ultra_account_in_use_has_no_5_hour_row_on_its_live_numbers(self):
        for five in ('absent', 'null'):
            with self.subTest(five_hour=five):
                st = self.state(ultra_row(resets=iso(self.week)))
                rl = {'seven_day': {'used_percentage': 30, 'resets_at': self.week}}
                if five == 'null':
                    rl['five_hour'] = None
                self.live(rl)
                server.apply_live_numbers(st, self.now)
                u = st['accounts'][ULTRA]['usage']
                self.assertEqual((u['session'], u['weekly']['pct']), (None, 30.0))     # the live number stands in
                self.assertEqual(self.shown(st), [('weekly', 30, 'resets')])          # Fable's week: no line

    def test_a_5_hour_window_that_has_reset_since_has_not_started(self):
        # The engine read 40% twenty minutes ago; that window reset half a minute ago, and Claude Code dropped it.
        st = self.state(team_row(40.0, iso(self.now - 30), 17.0, iso(self.week)))
        was = st['accounts'][ULTRA]['usage']['session']['resets']
        self.live({'seven_day': {'used_percentage': 18, 'resets_at': self.week}})
        server.apply_live_numbers(st, self.now)
        # refilled: 0%, its passed reset kept (one rule for both windows), never None
        self.assertEqual(st['accounts'][ULTRA]['usage']['session'], dict(NOT_STARTED, resets=was))
        self.assertEqual(self.shown(st), [('5-hour', 0, 'not st'), ('weekly', 18, 'resets')])

    def test_a_5_hour_window_still_open_is_carried_over_like_a_models_week(self):
        st = self.state(team_row(40.0, iso(self.now + 2 * H), 17.0, iso(self.week)))
        self.live({'seven_day': {'used_percentage': 18, 'resets_at': self.week}})
        server.apply_live_numbers(st, self.now)
        self.assertEqual(st['accounts'][ULTRA]['usage']['session']['pct'], 40.0)
        self.assertEqual(self.shown(st), [('5-hour', 40, 'resets'), ('weekly', 18, 'resets')])

    def test_a_live_5_hour_number_still_wins(self):
        st = self.state(team_row(40.0, iso(self.now + 2 * H), 17.0, iso(self.week)))
        self.live({'five_hour': {'used_percentage': 45, 'resets_at': self.now + 2 * H},
                   'seven_day': {'used_percentage': 18, 'resets_at': self.week}})
        server.apply_live_numbers(st, self.now)
        self.assertEqual(self.shown(st), [('5-hour', 45, 'resets'), ('weekly', 18, 'resets')])


# ---------- the demo ----------

class TheDemo(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def test_the_ultra_account_has_no_5_hour_window_and_the_idle_team_ones_have_not_started(self):
        v = demo.view()
        got = dict((a['email'], a['usage']['session']) for a in v['accounts'])
        self.assertEqual([a['email'] for a in v['accounts'] if a['plan'] == 'Ultra'], ['maya.reyes@mail.example'])
        self.assertIsNone(got['maya.reyes@mail.example'])
        for email in ('maya@fernhill.example', 'm.reyes@harbor.example'):      # Team and Enterprise, nothing used
            self.assertEqual(got[email], {'pct': 0, 'resets': None, 'pace': None, 'lasts': None}, email)
        for email in ('maya@northwind.example', 'maya@ivy.example'):
            self.assertGreater(got[email]['pct'], 0)

    def test_no_history_line_gives_the_ultra_account_a_5_hour_reading(self):
        demo.view()
        for x in demo.history()['entries']:
            about = x.get('from') if x.get('kind') == 'switch' else x.get('account')
            if about == 'maya.reyes@mail.example':
                self.assertNotIn('5-hour', x['text'] + ' ' + (x.get('reason') or ''), x)

    def test_the_terminal_and_the_menu_bar_draw_it_so(self):
        v = demo.view()
        rows = dict((a['email'], [l['label'] for l in a['limits']]) for a in term.read(v)['accounts'])
        self.assertEqual(rows['maya.reyes@mail.example'], ['weekly'])
        self.assertEqual(rows['maya@fernhill.example'], ['5-hour', 'weekly'])
        bar = next(r for r in menubar.rows(v) if r['email'] == 'maya.reyes@mail.example')
        self.assertIsNone(bar['five'])


# ---------- the page ----------

@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class ThePage(test_app.ChromePage):
    """The page draws an account with no 5-hour limit without a 5-hour row, and 0% with no reset as not started."""

    LIMS = """
      await wait(300);
      // a 5-hour (when there is a number) and a Weekly cell per row, a model's week on the weekly line's hover; when
      // each refills is on hover (the cell's title, its first line), never "Not started"
      const reset = (l) => (l.title.split('\\n')[0].split('. Refills ')[1]) || null;
      done({lims: all('#acc .arow').map((b) => [b.querySelector('.em').textContent,
              [].slice.call(b.querySelectorAll('.c[data-kind]')).map((l) => [l.dataset.kind, l.querySelector('.ml').textContent,
                l.querySelector('.pct').textContent, reset(l)])]),
            hover: all('#acc .arow').map((b) => [b.querySelector('.em').textContent, b.querySelector('.c-week').title]),
            out: all('.nl').some((p) => /Every account is at its line/.test(p.textContent))});
"""

    def setUp(self):
        super().setUp()
        demo.view()
        self.now = time.time()
        self.st = demo.STATE['accounts']

    def window(self, pct, resets_in):
        return {'pct': pct, 'resets': iso(self.now + resets_in), 'pace': None, 'lasts': None}

    def test_the_rows_each_account_has(self):
        self.st['maya.reyes@mail.example']['usage'].update(
            session=None, scoped=[{'name': 'Fable', 'pct': 40, 'resets': iso(self.now + 2 * D + 1800)}])
        self.st['maya.reyes@mail.example']['usage_at'] = self.now - 40      # read just now (the demo's is a day old)
        self.st['maya@fernhill.example']['usage']['session'] = {'pct': 0, 'resets': None, 'pace': None, 'lasts': None}
        self.st['m.reyes@harbor.example']['usage_at'] = self.now - 7200                     # too old to trust
        got = self.run_page(self.LIMS)
        rows, hover = dict(got['lims']), dict(got['hover'])
        ultra = rows['maya.reyes@mail.example']
        self.assertEqual([l[:3] for l in ultra], [['week', 'Weekly', '91%']])     # the Fable week: no line of its own
        self.assertRegex(ultra[0][3] or '', r'^(\w{3} )?\d{1,2}:\d{2} [AP]M')
        # the Fable week resets at another time: said on the weekly line's hover
        self.assertRegex(hover['maya.reyes@mail.example'].split('\n')[1],
                         r'^Fable’s own week: 40% used, switches at 95%\. Refills (\w{3} )?\d{1,2}:\d{2} [AP]M$')
        self.assertEqual(rows['maya@fernhill.example'][0], ['five', '5-hour', '0%', None])   # not started: nothing said
        # too old to show: a weekly dash alone (it was a dash for both: the 5-hour line now needs a number to show)
        self.assertEqual(rows['m.reyes@harbor.example'], [['week', 'Weekly', '—', None]])
        busy = rows['maya@northwind.example'][0]
        self.assertEqual(busy[:3], ['five', '5-hour', '62%'])
        self.assertRegex(busy[3], r'^(\w{3} )?\d{1,2}:\d{2} [AP]M, in ')
        self.assertFalse(got['out'])

    def test_every_account_at_its_line_means_its_5_hour_or_weekly_limit_never_a_models_week(self):
        for a in self.st.values():
            a.update(usage_at=self.now - 40, signed_out=False, usage={
                'session': None, 'weekly': self.window(50, 3 * D), 'spend': None,
                'scoped': [{'name': 'Fable', 'pct': 95, 'resets': iso(self.now + 3 * D)}]})
        self.assertFalse(self.run_page(self.LIMS)['out'])
        for a in self.st.values():
            a['usage']['weekly']['pct'] = 96
        self.assertTrue(self.run_page(self.LIMS)['out'])


if __name__ == '__main__':
    unittest.main()
