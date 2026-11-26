import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A spend limit, where Anthropic sends one: some plans limit what an account may spend in a period. When a reading
# carries one with numbers, it is one of the row's lines ("spend  ━━━  41%  $412 of $1,000 · resets Dec 1"), with the
# same bar and no switch line (no mark, never red), in the terminal, the menu bar and `switchboard status --json`
# (limits.spend). With no spend limit: nothing at all.
#
# A row has at most two limit lines: weekly always, and the other is whichever of 5-hour and spend has more used (the
# 5-hour one on a tie), in the order 5-hour, weekly, spend. A check over many made-up cases holds that, and that a
# reading with no spend limit gives every line it gave before.
#
# No account with a spend limit exists to read here: these are stand-ins shaped like what Claude Code hands its status
# line (statusline.live_record keeps its spend_limit). Made-up *.example accounts only. No network, no engine.
import json
import os
import random
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, menubar, report, server, spend, statusline, term

TZ = timezone(timedelta(hours=-8))
NOW = datetime(2026, 11, 13, 15, 31, 25, tzinfo=TZ).timestamp()          # Fri 3:31 PM
DEC_1 = datetime(2026, 12, 1, 0, 0, tzinfo=TZ).timestamp()
SAT_NOON = datetime(2026, 11, 14, 12, 0, tzinfo=TZ).timestamp()
D, H = 86400, 3600
TEAM, ULTRA, NONE = 'ana@team.example', 'ana@ultra.example', 'ana@none.example'


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def w(pct, resets):
    return {'pct': pct, 'resets': iso(resets) if resets else None, 'pace': None, 'lasts': None}


def money(pct=41.2, used=412.0, limit=1000.0, resets=DEC_1):
    return {'pct': pct, 'used': used, 'limit': limit, 'currency': 'USD', 'resets': iso(resets) if resets else None}


def acct(slot, email, weekly, session=None, cost=None, **more):
    return dict({'email': email, 'name': email, 'alias': '', 'slot': slot, 'plan': 'Team', 'org': '', 'active': False,
                 'needs_login': False, 'disabled': False, 'unmanaged': False, 'trusted': True,
                 'usage': {'session': session, 'weekly': weekly, 'scoped': [], 'spend': cost},
                 'usage_at': NOW - 60, 'checked_at': NOW - 60}, **more)


def view(**more):
    """TEAM: 5-hour 20%, weekly 10%, spend 41% (spend is the second line). ULTRA: no 5-hour limit, spend 41%. NONE: no
    spend limit."""
    return dict({'now': NOW, 'active': TEAM, 'next': None, 'setup': {'engine': True, 'accounts': True},
                 'auto': {'available': True, 'enabled': True, 'on': True, 'session_at': 90.0, 'weekly_at': 95.0,
                          'model_at': 95.0, 'weekly_set': True, 'models': []},
                 'accounts': [acct(1, TEAM, w(10, SAT_NOON + 2 * D), session=w(20, NOW + 2 * H), cost=money(),
                                   active=True),
                              acct(2, ULTRA, w(10, SAT_NOON + 2 * D), cost=money()),
                              acct(3, NONE, w(10, SAT_NOON + 2 * D), session=w(20, NOW + 2 * H))]}, **more)


class TheNumbers(unittest.TestCase):
    def test_claude_codes_spend_limit_in_the_states_shape(self):
        raw = {'spend_limit': {'used_percentage': 41.2, 'resets_at': DEC_1, 'used_usd': 412, 'limit_usd': 1000,
                               'period': 'monthly'}}
        self.assertEqual(spend.from_live(raw), {'pct': 41.2, 'used': 412.0, 'limit': 1000.0, 'currency': 'USD',
                                                'resets': iso(DEC_1)})
        self.assertIsNone(spend.from_live({}))
        self.assertIsNone(spend.from_live({'spend_limit': {'period': 'monthly'}}))          # no number
        self.assertEqual(spend.from_live({'spend_limit': {'used_percentage': 5}})['pct'], 5.0)

    def test_its_words(self):
        self.assertEqual(spend.amounts(spend.of({'spend': money()})), '$412 of $1,000')
        self.assertEqual(spend.amounts(spend.of({'spend': {'used': 9.04, 'limit': 70.84, 'currency': 'USD'}})),
                         '$9.04 of $70.84')
        self.assertAlmostEqual(spend.of({'spend': {'used': 9.04, 'limit': 70.84}})['pct'], 12.76, places=2)
        self.assertEqual(spend.amounts(spend.of({'spend': money(used=None)})), '')
        self.assertEqual(spend.money(412, 'EUR'), '412 EUR')
        self.assertIsNone(spend.of({'spend': None}))
        self.assertIsNone(spend.of({'spend': {'used': 3, 'limit': 0}}))


class TheTerminal(unittest.TestCase):
    def rows(self, v=None):
        return dict((a['email'], a) for a in term.read(v or view(), tz=TZ)['accounts'])

    def blocks(self, v=None, width=None):
        got = term.view(term.read(v or view(), tz=TZ), width=width)
        return dict((next(e for e in (TEAM, ULTRA, NONE) if e in b.split('\n')[0]), b)
                    for b in '\n'.join(got).split('\n\n')[1:])

    def test_a_spend_limit_is_a_line_of_the_row(self):
        rows = self.rows()
        self.assertEqual([(l['label'], l['pct']) for l in rows[TEAM]['limits']], [('weekly', 10), ('spend', 41)])
        self.assertEqual([(l['label'], l['pct']) for l in rows[ULTRA]['limits']], [('weekly', 10), ('spend', 41)])
        line = self.blocks()[ULTRA].split('\n')[-1]
        self.assertEqual(line, '   spend   ' + '━' * 10 + '─' * 14 + '   41%  $412 of $1,000 · resets Dec 1')
        self.assertFalse(rows[ULTRA]['limits'][-1]['over'])

    def test_never_red_even_full(self):
        v = view()
        v['accounts'][1]['usage']['spend'] = money(pct=100, used=1000)
        coloured = term.view(term.read(v, tz=TZ), colour=True)
        line = next(x for x in coloured if 'spend' in x and '$1,000 of' in x)
        self.assertNotIn('\x1b[91m', line)

    def test_the_two_nearest_their_limits_in_the_usual_order(self):
        v = view()
        v['accounts'][0]['usage']['session'] = w(62, NOW + 2 * H)              # 5-hour 62% beats spend 41%
        self.assertEqual([l['label'] for l in self.rows(v)[TEAM]['limits']], ['5-hour', 'weekly'])
        v['accounts'][0]['usage']['session'] = w(41, NOW + 2 * H)              # a tie keeps the 5-hour
        self.assertEqual([l['label'] for l in self.rows(v)[TEAM]['limits']], ['5-hour', 'weekly'])

    def test_with_no_spend_limit_nothing_changes(self):
        self.assertEqual([l['label'] for l in self.rows()[NONE]['limits']], ['5-hour', 'weekly'])
        self.assertNotIn('spend', self.blocks()[NONE])
        self.assertNotIn('$', self.blocks()[NONE])

    def test_a_reading_too_old_to_show_shows_no_spend(self):
        v = view()
        v['accounts'][1]['trusted'] = False
        self.assertEqual([l['label'] for l in self.rows(v)[ULTRA]['limits']], ['weekly'])

    def test_over_many_made_up_cases_at_most_two_lines_and_nothing_new_without_spend(self):
        rnd = random.Random(20261125)
        for _ in range(3000):
            five = rnd.choice([None, w(rnd.choice([None, 0, 41, rnd.uniform(0, 100)]), NOW + H)])
            cost = rnd.choice([None, None, money(pct=rnd.choice([None, 41, rnd.uniform(0, 100)]),
                                                 used=rnd.choice([None, 5.0]), limit=rnd.choice([None, 50.0]))])
            a = acct(1, TEAM, w(rnd.uniform(0, 100), SAT_NOON), session=five, cost=cost,
                     trusted=rnd.choice([True, True, False]))
            lims = term.limits(a, NOW, 90, 95, TZ)
            labels = [l['label'] for l in lims]
            self.assertLessEqual(len(labels), 2)
            self.assertIn('weekly', labels)
            self.assertEqual(labels, sorted(labels, key=['5-hour', 'weekly', 'spend'].index))
            bare = term.limits(dict(a, usage=dict(a['usage'], spend=None)), NOW, 90, 95, TZ)
            if 'spend' not in labels:
                self.assertEqual(lims, bare)                                     # with no spend line: as before
            else:
                self.assertEqual(lims[0], [l for l in bare if l['label'] == 'weekly'][0])
                five_shown = [l for l in bare if l['label'] == '5-hour']
                self.assertTrue(not five_shown or lims[1]['pct'] > five_shown[0]['pct'])


class TheMenuBar(unittest.TestCase):
    def rows(self, v=None):
        return dict((r['email'], r) for r in menubar.rows(v or view(), TZ))

    def test_a_spend_limit_is_a_bar_row(self):
        rows = self.rows()
        ultra = rows[ULTRA]
        self.assertIsNone(ultra['five'])
        self.assertEqual((ultra['spend']['label'], menubar.pct_text(ultra['spend'])), ('Spend', '41%'))
        ops = menubar.row_ops(ultra)
        labels = [op[5][0][0] for op in ops if op[0] == 'text']
        self.assertEqual(labels[0::2], ['Weekly', 'Spend'])
        self.assertEqual(menubar.row_height(ultra), 88.0)                      # two bar rows, as with a 5-hour one
        self.assertNotIn('red', repr([op for op in ops if op[0] == 'box']))
        foot = [op for op in ops if op[0] == 'pair'][-1]
        self.assertIn([('$412 of $1,000', 11.0, 400, 'ink2')], foot[6])
        self.assertIn('spend 41%', menubar.row_label(ultra))

    def test_none_without_a_spend_limit(self):
        none = self.rows()[NONE]
        self.assertIsNone(none['spend'])
        self.assertNotIn('Spend', repr(menubar.row_ops(none)))
        self.assertNotIn('$', repr(menubar.row_ops(none)))


class TheJson(unittest.TestCase):
    def test_limits_spend(self):
        got = dict((a['email'], a) for a in report.document(view(), '0.0.0')['accounts'])
        self.assertEqual(got[ULTRA]['limits']['spend'], {'spent': 412.0, 'limit': 1000.0, 'currency': 'USD',
                                                         'used_percent': 41, 'refills_at': report.when(DEC_1)})
        self.assertEqual(got[TEAM]['limits']['spend']['used_percent'], 41)    # in the document even when not a line
        self.assertIsNone(got[NONE]['limits']['spend'])


class FromClaudeCode(unittest.TestCase):
    """The spend limit reaches the state from Claude Code's own record (server.live_records, apply_live_numbers)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-spend-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, 'home')
        env = mock.patch.dict(os.environ, {'HOME': home, 'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data'),
                                           'CLAUDE_CONFIG_DIR': os.path.join(home, '.claude')})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.now = time.time()

    def test_the_account_in_use_gets_its_spend_limit(self):
        week = self.now + 2 * D
        st = core.blank_state()
        a = core.ensure_account(st, ULTRA)
        a.update(connected=True, slot=1, usage_status='ok', usage_error=None, note=None, usage_at=self.now - 1200,
                 usage={'session': None, 'weekly': w(20, week), 'scoped': [], 'spend': None})
        st['active'] = ULTRA
        st['timeline'] = [[ULTRA, self.now - 3 * H]]
        rl = {'seven_day': {'used_percentage': 30, 'resets_at': week},
              'spend_limit': {'used_percentage': 41.2, 'resets_at': week, 'used_usd': 412, 'limit_usd': 1000}}
        rec = statusline.live_record({'session_id': 's1', 'rate_limits': rl}, {'active': ULTRA}, self.now - 60, env={})
        os.makedirs(statusline.live_dir(), exist_ok=True)
        with open(os.path.join(statusline.live_dir(), 's1.json'), 'w') as f:
            json.dump(rec, f)
        server.apply_live_numbers(st, self.now)
        self.assertEqual(st['accounts'][ULTRA]['usage']['spend'],
                         {'pct': 41.2, 'used': 412.0, 'limit': 1000.0, 'currency': 'USD', 'resets': iso(week)})


if __name__ == '__main__':
    unittest.main()
