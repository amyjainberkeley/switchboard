import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page when Switchboard is busy or not running: it keeps
# the last good numbers, says "Switchboard is busy (last answer N s ago)" or "Switchboard isn't running", backs off,
# puts one line up after three misses, and has the "Let Switchboard check links" and "Send a test" buttons.
# The page is read as text, and run in headless Chrome (its own throwaway profile, no window) against the demo's
# made-up state; each failure of the server is a stand-in fetch inside the page.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from test_app import CHROME, ChromePage

PAGE = os.path.join(HERE, '..', 'switchboard', 'web', 'index.html')


def read():
    with open(PAGE, encoding='utf-8') as f:
        return f.read()


class TheWords(unittest.TestCase):
    def setUp(self):
        self.html = read()

    def test_busy_and_not_running_are_told_apart_never_cant_reach(self):
        self.assertIn("'Switchboard is busy (last answer ' + secs(busyFor()) + ' ago)'", self.html)
        self.assertIn("return 'Switchboard isn’t running';", self.html)
        self.assertNotIn('Can’t reach Switchboard', self.html)
        self.assertIn("down = !!e && e.name === 'TypeError'", self.html)          # refused: nothing listens
        self.assertIn('goodAt = Date.now() - age * 1000', self.html)              # the server's own answer age

    def test_one_look_at_a_time_never_longer_than_8_s_and_backing_off(self):
        self.assertIn("fetch('/api/state', {signal: timed(8000)})", self.html)
        self.assertIn('return [2000, 5000, 10000, 20000][Math.min(misses, 4) - 1];', self.html)
        self.assertIn('if (!loading && (Date.now() >= nextLoad || asking))', self.html)   # each second while checking
        self.assertNotIn("setInterval(() => { if (!document.hidden) { load(); loadExtras(); } }, 5000);", self.html)

    def test_the_buttons_ask_only_their_own_server(self):
        self.assertIn("post('/api/consent')", self.html)
        self.assertIn("post('/api/notifytest')", self.html)
        self.assertIn("post('/api/notifytest', {seen: !!seen})", self.html)
        self.assertIn('Let Switchboard check links', self.html)
        self.assertIn('Did a notification appear?', self.html)


STALL = r"""
      await wait(300);
      const base = window.fetch, posted = [];
      let mode = 'ok', extra = {};
      window.fetch = function (u, o) {
        const url = String(u);
        if (url === '/api/state' && mode !== 'ok') {
          if (mode === 'refused') return Promise.reject(new TypeError('Failed to fetch'));
          if (mode === 'timeout') return Promise.reject(Object.assign(new Error('signal timed out'), {name: 'TimeoutError'}));
          if (mode === '503') return Promise.resolve(new Response(JSON.stringify({error: 'busy', busy: true, retry: 2}),
                                                                   {status: 503, headers: {'Content-Type': 'application/json'}}));
          if (mode === 'old') return base('/api/state').then((r) => r.json()).then((s) =>
            new Response(JSON.stringify(Object.assign(s, {answer: {age: 40, stale: true, building: true}}, extra)),
                         {status: 200, headers: {'Content-Type': 'application/json'}}));
        }
        if (url === '/api/state' && Object.keys(extra).length) return base('/api/state').then((r) => r.json()).then((s) =>
          new Response(JSON.stringify(Object.assign(s, extra)), {status: 200, headers: {'Content-Type': 'application/json'}}));
        return base.apply(this, arguments);
      };
      const look = async (ms) => { document.dispatchEvent(new Event('visibilitychange')); await wait(ms); };
      const header = () => $('#top .checked').textContent;
      const notes = () => $('#notes').textContent;
      const cards = () => all('#acc .arow').length;
      const settings = async () => { location.hash = '#settings'; await wait(700); };   // its own view
"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class BusyOrNotRunning(ChromePage):
    def test_refused_is_not_running_a_timeout_is_busy_and_the_numbers_stay(self):
        got = self.run_page(STALL + r"""
      const first = cards(), pct = $('#acc .pct').textContent;
      mode = 'refused';
      await look(300);
      const refused = header(), refusedCards = cards();
      mode = 'timeout';
      await wait(3500);                                  // the next look comes 2 s after a refusal (each second's beat)
      const busy = header(), busyCards = cards(), busyPct = $('#acc .pct').textContent;
      mode = '503';
      await wait(6500);                                  // and 5 s after the second miss
      done({first, pct, refused, refusedCards, busy, busyCards, busyPct, busy503: header()});
""", budget=12000)
        self.assertGreater(got['first'], 0)
        self.assertEqual(got['refused'], 'Switchboard isn’t running')
        self.assertEqual(got['refusedCards'], got['first'])
        self.assertRegex(got['busy'], r'^Switchboard is busy \(last answer \d+ s ago\)$')
        self.assertEqual((got['busyCards'], got['busyPct']), (got['first'], got['pct']))      # the last good numbers
        self.assertRegex(got['busy503'], r'^Switchboard is busy \(last answer \d+ s ago\)$')

    def test_three_misses_put_one_line_up_and_it_goes_once_switchboard_answers(self):
        got = self.run_page(STALL + r"""
      mode = 'timeout';
      await look(300);                                   // misses: 1 now, 2 after 2 s, 3 after 5 more (each second's beat)
      await wait(9500);
      const banner = notes();
      mode = 'ok';
      await wait(12000);                                 // the next look, 10 s after the third miss
      done({banner, back: header(), backNotes: notes()});
""", budget=24000)
        self.assertIn('Switchboard didn’t answer for', got['banner'])
        self.assertNotIn('Switchboard', got['back'])                 # answering again: its own words come back
        self.assertNotIn('didn’t answer', got['backNotes'])

    def test_an_answer_the_server_could_not_renew_says_how_old_it_is(self):
        got = self.run_page(STALL + r"""
      mode = 'old';
      await look(800);
      done({header: header(), cards: cards()});
""", budget=3000)
        self.assertRegex(got['header'], r'^Switchboard is busy \(last answer 4\d s ago\)$')
        self.assertGreater(got['cards'], 0)

    def test_three_refusals_say_how_to_start_it(self):
        got = self.run_page(STALL + r"""
      mode = 'refused';
      await look(300);
      await wait(10000);                                 // misses: 1 now, 2 after 2 s, 3 after 5 more (each second's beat)
      done({notes: notes(), header: header()});
""", budget=12000)
        self.assertIn('Switchboard isn’t running. It starts again by itself', got['notes'])
        self.assertIn('switchboard serve', got['notes'])
        self.assertEqual(got['header'], 'Switchboard isn’t running')


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheButtons(ChromePage):
    def test_let_switchboard_check_links_is_asked_only_from_the_button(self):
        got = self.run_page(STALL + r"""
      extra = {consent: {state: 'would_ask', line: 'macOS has not asked yet. Press Let Switchboard check links, then choose OK.', can_ask: true}};
      await look(500);
      await settings();
      const before = sent.filter((x) => x[0] === '/api/consent').length;
      const btn = $('[data-do="consent"]');
      const label = btn ? btn.textContent : null, hint = all('#sec-settings .hint').map((x) => x.textContent).join(' | ');
      if (btn) btn.click();
      await wait(800);
      done({before, label, hint, after: sent.filter((x) => x[0] === '/api/consent').length});
""", budget=4000)
        self.assertEqual(got['label'], 'Let Switchboard check links')
        self.assertIn('macOS has not asked yet', got['hint'])
        self.assertEqual((got['before'], got['after']), (0, 1))

    def test_no_button_once_macos_said_yes(self):
        got = self.run_page(STALL + r"""
      extra = {consent: {state: 'allowed', line: 'Switchboard may check which profile opened a link.', can_ask: true}};
      await look(500);
      await settings();
      done({button: !!$('[data-do="consent"]'), shown: !!$('#sec-settings .pairs')});
""", budget=3000)
        self.assertFalse(got['button'])
        self.assertTrue(got['shown'])                                     # the view it would be in was drawn

    def test_send_a_test_then_did_you_see_it(self):
        got = self.run_page(STALL + r"""
      const asked = [];
      const was = window.fetch;
      window.fetch = function (u, o) {
        if (String(u) === '/api/notifytest') {
          const body = JSON.parse((o && o.body) || '{}');
          asked.push(body);
          const answer = 'seen' in body ? {line: body.seen ? 'Notifications work.' : 'macOS shows Switchboard’s notifications as Script Editor’s, and they are turned off.', ok: true}
            : {sent: true, line: 'Sent. Did a notification appear?', ok: true};
          return Promise.resolve(new Response(JSON.stringify(answer), {status: 200, headers: {'Content-Type': 'application/json'}}));
        }
        return was.apply(this, arguments);
      };
      await settings();
      const btn = $('[data-do="noteTest"]');
      if (!btn) return done({error: 'no Test button'});
      btn.click();
      await wait(500);
      const question = $('#sec-settings .ask') ? $('#sec-settings .ask').textContent : '';
      $('[data-do="noteSeen"][data-seen=""]').click();
      await wait(500);
      done({asked, question, said: $('#toasts').textContent});
""", budget=3000)
        self.assertEqual(got['asked'], [{}, {'seen': False}])
        self.assertIn('Did a notification appear?', got['question'])
        self.assertIn('Script Editor', got['said'])

    def test_the_settings_say_when_macos_hides_them(self):
        got = self.run_page(STALL + r"""
      extra = {notify_check: {seen: false}};
      await look(800);
      await settings();
      done($('#sec-settings').textContent);
""", budget=3000)
        self.assertIn('macOS hides them: turn on Script Editor in System Settings › Notifications', got)

    def test_a_disk_almost_full_and_a_mac_very_busy_are_one_line_each(self):
        got = self.run_page(STALL + r"""
      extra = {disk: {low: true, free: 157286400}, load: {per_core: 21.3, easing: true}};
      await look(800);
      done(notes());
""", budget=3000)
        self.assertIn('Your disk is almost full (150 MB free), so Switchboard paused new previews and updates to '
                      'Artifacts, Chats and Your week. Free some disk space.', got)
        self.assertIn('Your Mac is very busy, so Switchboard does its other work less often for now. Your limits are '
                      'read as usual.', got)


if __name__ == '__main__':
    unittest.main()
