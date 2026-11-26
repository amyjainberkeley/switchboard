import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# "Checked" is the last read that was answered for an account, and under a minute it is "just now". A reading whose
# 5-hour window's newer answer was refused (a lower number with no reset while the window is still open) keeps that
# window's own time, two hours old, for every estimate (usage_at); the account was still asked and answered a moment
# ago (checked_at), and the engine's row, the page's state, the terminal and the menu bar say so.
#
# The poller reads through a fake network on a fake clock; the store is written in a throwaway data folder. Made-up
# *.example accounts only.
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, engine, menubar, server, term
from switchboard.accounts import usage
import test_app
import test_menubar
import test_term
from test_app import A, H
from fakes_autoswitch import FakeClock, FakeNet, FakeTokens, T0, usage_body


class CheckedIsTheLastAnsweredRead(unittest.TestCase):
    """A reading whose 5-hour window was refused (a lower number with no reset while it is still open): that window
    keeps its own time, two hours old, yet the account was asked and answered a moment ago."""

    def setUp(self):
        self.clock = FakeClock()
        self.net = FakeNet(self.clock)
        self.net.set('tok-a1', (200, {}, usage_body(five=50.0, week=30.0, five_reset=T0 + 4 * H,
                                                    week_reset=T0 + 5 * 86400)),
                     (200, {}, usage_body(five=10.0, week=31.0, five_reset=None, week_reset=T0 + 5 * 86400)))
        poller = usage.Poller(clock=self.clock, net=self.net, token_for=FakeTokens(), rand=lambda: 0.5)
        ctx = usage.Context(active='a1', order=('a1',))
        poller.poll(ctx)
        self.clock.advance(2 * H)
        poller.poll(ctx)
        self.reading = usage.readings(now=self.clock.wall())['a1']

    def tearDown(self):
        try:
            os.unlink(usage.store_path())
        except OSError:
            pass

    def test_the_reading_has_both_times(self):
        self.assertEqual(self.net.count(), 2)
        self.assertEqual(self.reading.read_at, T0)                     # the oldest number's own time
        self.assertEqual(self.reading.checked_at, T0 + 2 * H)          # the last answer
        five = next(w for w in self.reading.windows if w.name == '5-hour')
        self.assertEqual(five.pct, 50.0)                               # the refused answer changed nothing

    def test_the_engines_row_and_the_pages_state_carry_it(self):
        fields = engine.reading_fields(self.reading)
        self.assertEqual((fields['usage_at'], fields['checked_at']), (T0, T0 + 2 * H))
        st = core.blank_state()
        server.apply_rows(st, [engine._row(id='a1', number=1, email=A, active=True, **fields)])
        view = server.render(st, dict(test_app.Plan.facts(self), now=T0 + 2 * H + 5))
        a = view['accounts'][0]
        self.assertEqual((a['usage_at'], a['checked_at']), (T0, T0 + 2 * H))
        self.assertEqual(term.age_words(dict(a, active=True), T0 + 2 * H + 5), 'checked just now')

    auto = {}


class JustNow(unittest.TestCase):
    def test_under_a_minute_is_just_now(self):
        now = test_term.NOW
        self.assertEqual([term.age(now - s, now) for s in (0, 5, 59, 60, 89, 150)],
                         ['just now', 'just now', 'just now', '1 min ago', '1 min ago', '3 min ago'])
        self.assertEqual(term.age_words({'usage_at': now - 5}, now), 'checked just now')
        self.assertEqual(term.age_words({'usage_at': now - 7200, 'checked_at': now - 5}, now), 'checked just now')
        self.assertEqual(term.age_words({'usage_at': now - 5, 'live': True, 'active': True}, now), 'now')

    def test_in_the_menu_bar(self):
        s = test_menubar.view()
        a = test_menubar.find(s, 'ana@lab.example')
        a.update(usage_at=test_menubar.NOW - 7200, checked_at=test_menubar.NOW - 10)
        r = test_menubar.row_of(s, 'ana@lab.example')
        self.assertEqual(r['marks'][0], ('just now', 'next'))
        self.assertIn('checked just now', menubar.row_label(r))

    def test_in_the_terminal(self):
        view = json.loads(json.dumps(test_term.VIEW))
        mail = next(a for a in view['accounts'] if a['email'] == 'ana@mail.example')
        mail.update(usage_at=test_term.NOW - 7200, checked_at=test_term.NOW - 10)
        rows = [x for x in test_term.lines(view) if x[:1].isdigit()]
        self.assertEqual(rows[-1], '5  ana@mail.example       Max 20x · Personal  in use · checked just now')


if __name__ == '__main__':
    unittest.main()
