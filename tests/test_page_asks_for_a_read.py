import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page asks for a read itself (POST /api/refresh), in headless Chrome: when it is opened and the account in use's
# numbers are over a minute old, every account at once, saying "checking…" until the loop answers that request (the
# state's auto.asked) or 8 s have passed; the same when another account the loop can read is over 5 minutes old, at
# most once a minute; nothing when they are fresh; while it stays open, the account in use alone, quietly, once its
# numbers are about 3 minutes old, at most once a minute, and 5 minutes after an answer that read nothing back; never
# for an account with a standing note; never in the demo. The header's tooltip says how often the numbers are read,
# in two wordings (Claude Code's own numbers coming in, or not), and a row with no 5-hour line says why: no 5-hour limit
# reported for the account, or not known.
#
# The demo's made-up state, served by the real handler, made to look like a real one (demo false) where the page's
# own asking is tested; /api/refresh is answered in the page with a made-up request time, and the loop's answer is
# put into the state the page reads. The demo's clock stands still, so every age is exactly what the test set.
import os
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import demo
from test_app import CHROME, ChromePage

ASKED_AT = 1000.0
LIVE = ('The account in use is live while you work: its numbers come from Claude Code itself. The others are read '
        'every 5 to 15 minutes, and at once on Read again.')
READ = ('The account in use is read every 5 minutes, about every 3 while this page is open, the others every 5 to 15, '
        'and all at once on Read again. Not more often: Anthropic limits reads, so Switchboard keeps to about 20 an '
        'hour for each account.')
NO_FIVE = 'Anthropic reports no 5-hour limit for this account'


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class AskingPage(ChromePage):
    PRELUDE = r"""
(function () {
  const sent = [], realFetch = window.fetch;
  const json = (x) => new Response(JSON.stringify(x), {status: 200, headers: {'Content-Type': 'application/json'}});
  window.__asked = %(asked)s;       // what /api/refresh answers as its request's time (null: the server answers)
  window.__hook = null;             // (state) => state: what the loop has done since, put into the page's state
  window.fetch = function (u, o) {
    const url = String(u);
    if (o && o.method === 'POST') {
      sent.push([url, JSON.parse(o.body || '{}'), Date.now()]);
      if (url === '/api/refresh' && window.__asked != null) return Promise.resolve(json({ok: true, asked: window.__asked}));
    }
    if (url === '/api/state' && window.__hook) return realFetch('/api/state').then((r) => r.json()).then((s) => json(window.__hook(s)));
    return realFetch.apply(this, arguments);
  };
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const $ = (q) => document.querySelector(q), all = (q) => [].slice.call(document.querySelectorAll(q));
  const done = (x) => { const p = document.createElement('pre'); p.id = 'result'; p.textContent = JSON.stringify(x); document.body.appendChild(p); };
  const header = () => $('#top .checked').textContent, posts = () => sent.filter((x) => x[0] === '/api/refresh').map((x) => [x[1], x[2]]);
  // the loop answered request `at`; `read`: the account in use was read just now (else it was held back); `counts`:
  // what the answer says it read
  const answer = (at, read = true, counts = {read: 5, ok: 5, held: 0}) => { window.__hook = (s) => { s.auto = Object.assign({}, s.auto, {asked: Object.assign({at, done: at + 1}, counts)});
    if (read) s.accounts.find((x) => x.email === s.active).checked_at = s.now - 2;
    return s; }; };
  // the page's clock moved on by `ms` (its timers still fire each second of Chrome's own time)
  const realNow = Date.now.bind(Date);
  let skewed = 0;
  Date.now = () => realNow() + skewed;
  const skew = (ms) => { skewed += ms; };
  (async () => {
    for (let i = 0; i < 120 && !document.querySelector('main[data-drawn]'); i++) await wait(50);
    try {
%(body)s
    } catch (e) { done({error: String(e && e.stack || e)}); }
  })();
})();
"""

    def setUp(self):
        super().setUp()
        self.now = time.time()
        clock = mock.patch.object(demo, 'time', mock.Mock(time=lambda: self.now, sleep=time.sleep))
        clock.start()
        self.addCleanup(clock.stop)
        demo.view()

    def page(self, body, asked=ASKED_AT, budget=12000):
        self.PRELUDE = AskingPage.PRELUDE.replace('%(asked)s', 'null' if asked is None else repr(asked)) \
            .replace('%(body)s', '%s')
        return self.run_page(body, budget=budget)

    def in_use(self, age, real=True):
        """The account in use read `age` seconds ago; `real`: the state says it is not the demo."""
        demo.FACTS['demo'] = not real
        a = demo.STATE['accounts'][demo.STATE['active']]
        a['usage_at'] = self.now - age
        return a

    def others(self, age, each=()):
        """Every other account read `age` seconds ago; `each`: one dict of changes per other account, in order."""
        rest = [a for e, a in demo.STATE['accounts'].items() if e != demo.STATE['active']]
        for i, a in enumerate(rest):
            a['usage_at'] = self.now - age
            a.update(each[i] if i < len(each) else {})
        return rest


class OnOpen(AskingPage):
    def test_another_account_over_5_minutes_old_is_asked_for_though_the_one_in_use_is_fresh_and_once_a_minute(self):
        self.in_use(20)
        self.others(100)[-1]['usage_at'] = self.now - 400
        got = self.page("""
      await wait(300);
      const first = {posts: posts().map((p) => p[0]), header: header()};
      answer(%r, false);                                 // answered, and that account held back: it stays old
      await wait(1600);
      const after = header();
      document.dispatchEvent(new Event('visibilitychange'));   // back in view a moment later: not asked again yet
      await wait(1500);
      done({first, after, posts: posts().map((p) => p[0])});
""" % ASKED_AT)
        self.assertEqual(got['first'], {'posts': [{'all': True}], 'header': 'checking…'})
        self.assertEqual(got['after'], 'checked just now')
        self.assertEqual(got['posts'], [{'all': True}])                 # asked once

    def test_other_accounts_under_5_minutes_old_are_not_asked_for(self):
        self.in_use(20)
        self.others(280)
        got = self.page("""
      await wait(3000);
      done({posts: posts(), header: header()});
""")
        self.assertEqual(got, {'posts': [], 'header': 'checked just now'})

    def test_an_old_account_the_loop_cannot_read_is_not_asked_for(self):
        self.in_use(20)
        self.others(3600, each=({'needs_login': True}, {'frozen': True}, {'status': 'conflict'},
                                {'status': 'unreadable'}))
        got = self.page("""
      await wait(3000);
      done({posts: posts(), header: header()});
""")
        self.assertEqual(got, {'posts': [], 'header': 'checked just now'})

    def test_an_old_account_with_a_standing_note_is_not_asked_for(self):
        self.in_use(20)
        self.others(3600, each=[{'note': 'Anthropic did not answer. These are the last numbers.'}] * 9)
        got = self.page("""
      await wait(3000);
      done({posts: posts(), header: header()});
""")
        self.assertEqual(got, {'posts': [], 'header': 'checked just now'})

    def test_back_in_view_with_the_account_in_use_over_a_minute_old_every_account_is_asked_for(self):
        self.in_use(20)
        got = self.page("""
      await wait(300);
      const before = posts().length;
      window.__hook = (s) => { s.accounts.find((x) => x.email === s.active).usage_at = s.now - 90; return s; };
      document.dispatchEvent(new Event('visibilitychange'));
      await wait(1500);
      done({before, posts: posts().map((p) => p[0])});
""")
        self.assertEqual(got, {'before': 0, 'posts': [{'all': True}]})


    def test_numbers_over_a_minute_old_are_asked_for_at_once_and_checking_until_the_loop_answers(self):
        self.in_use(120)
        got = self.page("""
      await wait(300);
      const first = {posts: posts().map((p) => p[0]), header: header()};
      answer(%r);
      await wait(1600);                                  // the state is read each second while checking
      done({first, after: header(), posts: posts().map((p) => p[0])});
""" % ASKED_AT)
        self.assertEqual(got['first'], {'posts': [{'all': True}], 'header': 'checking…'})
        self.assertEqual(got['after'], 'checked just now')
        self.assertEqual(got['posts'], [{'all': True}])                 # asked once

    def test_fresh_numbers_are_not_asked_for(self):
        self.in_use(20)
        got = self.page("""
      await wait(3000);
      done({posts: posts(), header: header()});
""")
        self.assertEqual(got, {'posts': [], 'header': 'checked just now'})

    def test_with_no_answer_checking_ends_after_8_s_and_is_not_asked_again_at_once(self):
        self.in_use(120)
        got = self.page("""
      await wait(1000);
      const during = header();
      await wait(9000);
      done({during, after: header(), posts: posts().length});
""", budget=14000)
        self.assertEqual((got['during'], got['after'], got['posts']), ('checking…', 'checked 2 min ago', 1))

    def test_the_demo_never_asks(self):
        self.in_use(600, real=False)
        got = self.page("""
      await wait(3000);
      done({posts: posts(), header: header()});
""", asked=None)
        self.assertEqual(got, {'posts': [], 'header': 'checked 10 min ago'})

    def test_read_again_in_the_demo_reads_nothing_and_says_the_age_again(self):
        self.in_use(600, real=False)
        got = self.page("""
      $('#top .checked').click();
      await wait(1500);
      done({posts: posts().map((p) => p[0]), header: header()});
""", asked=None)
        self.assertEqual(got, {'posts': [{'all': True}], 'header': 'checked 10 min ago'})


class WhileOpen(AskingPage):
    def test_the_account_in_use_alone_quietly_once_about_3_minutes_old_at_most_once_a_minute(self):
        self.in_use(200)
        got = self.page("""
      answer(%r, false);                                 // answered, and held back: the numbers stay old
      await wait(100000);
      const p = posts();
      done({asks: p.map((x) => x[0]), gaps: p.slice(1).map((x, i) => x[1] - p[i][1])});
""" % ASKED_AT, budget=110000)
        self.assertEqual(got['asks'][:2], [{'all': True}, {'all': False}])     # on open every account, then quietly
        self.assertTrue(all(g >= 60000 for g in got['gaps']), got['gaps'])
        self.assertLessEqual(len(got['asks']), 3)

    def test_after_an_answer_that_read_nothing_back_the_next_quiet_ask_waits_5_minutes(self):
        self.in_use(200)
        got = self.page("""
      answer(%r, false, {read: 0, ok: 0, held: 2});     // answered: both held back (Anthropic said wait)
      await wait(130000);
      const within = posts().length;
      skew(180000);                                      // over 5 minutes since that answer
      await wait(3000);
      done({within, asks: posts().map((x) => x[0])});
""" % ASKED_AT, budget=140000)
        self.assertEqual(got['within'], 1)                               # the ask on open, not one a minute
        self.assertEqual(got['asks'], [{'all': True}, {'all': False}])   # then quietly again

    def test_read_again_asks_for_every_account(self):
        self.in_use(20)
        got = self.page("""
      $('#top .checked').click();
      await wait(200);
      const during = header();
      answer(%r);
      await wait(1600);
      done({posts: posts().map((p) => p[0]), during, after: header()});
""" % ASKED_AT)
        self.assertEqual(got, {'posts': [{'all': True}], 'during': 'checking…', 'after': 'checked just now'})


class TheWords(AskingPage):
    LOOK = """
      await wait(300);
      const old = all('#acc .note[data-kind="age"]').map((n) => n.title);
      const five = all('#acc .arow').map((b) => { const c = b.querySelector('.c-five'); return [b.querySelector('.em').textContent, c ? c.title : null]; });
      const why = all('#acc .arow').map((b) => [b.querySelector('.em').textContent, (b.querySelector('.c-gone, .c-nofive') || {}).textContent || null,
        b.querySelector('.c-week').title]);
      done({title: $('#top .checked').title, old, five, why});
"""

    def test_how_often_the_numbers_are_read_while_claude_codes_own_come_in(self):
        a = self.in_use(5, real=False)
        a['live'] = {'at': self.now - 5, 'usage': a['usage'], 'was': {}}
        got = self.page(self.LOOK, asked=None)
        self.assertEqual(got['title'], 'Read the limits again (R). ' + LIVE)

    def test_how_often_the_numbers_are_read_from_anthropic(self):
        self.in_use(30, real=False)
        for e, a in demo.STATE['accounts'].items():
            if e != demo.STATE['active']:
                a['usage_at'] = self.now - 20 * 60           # old enough to say their age, with the same tooltip
        got = self.page(self.LOOK, asked=None)
        self.assertEqual(got['title'], 'Read the limits again (R). ' + READ)
        self.assertTrue(got['old'])
        self.assertEqual(set(got['old']), {READ})

    def test_with_no_5_hour_line_the_row_says_no_limit_reported_or_not_known(self):
        # (it was the 5-hour dash's hover: with no 5-hour number to show there is no 5-hour line now; a hidden cell says
        # why to a screen reader, and the weekly line's hover says there is no 5-hour limit)
        self.in_use(30, real=False)
        st = demo.STATE['accounts']
        st['maya@fernhill.example']['usage_at'] = self.now - 2 * 3600     # too old to show: its numbers are not known
        got = self.page(self.LOOK, asked=None)
        five, why = dict(got['five']), dict((e, (gone, week)) for e, gone, week in got['why'])
        self.assertIsNone(five['maya.reyes@mail.example'])                # its reading has no 5-hour window
        self.assertEqual(why['maya.reyes@mail.example'][0], 'No 5-hour limit on this plan')   # said, in its place
        self.assertEqual(why['maya.reyes@mail.example'][1].split('\n')[-1], NO_FIVE)
        self.assertIsNone(five['maya@fernhill.example'])
        self.assertEqual(why['maya@fernhill.example'][0], 'Not known')
        self.assertTrue(why['maya@fernhill.example'][1].startswith('Not known'), why['maya@fernhill.example'][1])
        self.assertTrue(five['maya@northwind.example'].startswith('62% used'), five['maya@northwind.example'])


if __name__ == '__main__':
    unittest.main()
