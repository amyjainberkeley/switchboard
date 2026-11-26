import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# An account's rule in auto-switch on the page: in Settings, Each account, one line per account (its whole email, its
# organisation where its row names one) with the page's own pick (Use, Use last, Keep out), one line that says what the
# two rules do, dim while auto-switch is off; and a short note on the account's own row (Used last, Kept out of
# auto-switch). The demo starts with no rule and keeps one in memory. Made-up accounts only (the demo's).
# Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests \
#     -p 'test_page_account_rules.py'
# (its page tests start headless Chrome: run the file under the Chrome lock). With SWITCHBOARD_SHOTS naming an empty
# folder under the temp folder, it also leaves pictures there: Settings and the accounts list, 680 and 1280 px wide,
# light and dark.
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import core, demo
from test_app import CHROME, ChromePage
from test_page_links_and_logo import FramedPage

WORK, MAIL, FERN, HARBOR, IVY = ('maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                                 'm.reyes@harbor.example', 'maya@ivy.example')
LINE = ('Use last: auto-switch goes there only when no other account has room. Keep out: auto-switch never moves '
        'Claude Code there, or away from it.')


def ids():
    return dict((e, a['id']) for e, a in demo.STATE['accounts'].items())


class TheDemo(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def test_it_starts_with_no_rule_and_keeps_one_in_memory(self):
        v = demo.view()
        self.assertEqual(sorted(v['auto']['rules'].values()), ['in'] * 5)
        demo.act('/api/auto', {'rule': 'last', 'account': ids()[IVY]})
        demo.act('/api/auto', {'rule': 'out', 'account': HARBOR})
        v = demo.view()
        self.assertEqual(v['auto']['rules'][ids()[IVY]], 'last')
        self.assertEqual(v['auto']['rules'][ids()[HARBOR]], 'out')
        self.assertNotEqual(v['plan']['next'], HARBOR)
        demo.act('/api/auto', {'rule': 'in', 'account': ids()[IVY]})
        self.assertEqual(demo.view()['auto']['rules'][ids()[IVY]], 'in')

    def test_a_wrong_word_or_account_is_refused_as_on_the_real_page(self):
        demo.view()
        for body, words in (({'rule': 'never', 'account': IVY}, 'Pick in, last or out for an account.'),
                            ({'rule': 'last', 'account': 'nobody@made-up.example'},
                             'That account is not saved in Switchboard.')):
            with self.subTest(body=body), self.assertRaises(core.SwitchError) as e:
                demo.act('/api/auto', body)
            self.assertEqual(str(e.exception), words)
        self.assertEqual(demo.FACTS['auto']['rules'], {})


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class InSettings(ChromePage):
    SETTINGS = r"""
      await wait(600);
      location.hash = '#settings'; await wait(900);
      const row = $('#row-rules'), order = $('#row-order');
      const rows = all('#row-rules .rule').map((r) => {
        const sel = r.querySelector('select'), em = r.querySelector('.rule-em'), c = getComputedStyle(r);
        return {email: em.textContent, org: (r.querySelector('.rule-org') || {}).textContent || '',
                label: sel.getAttribute('aria-label'), value: sel.value,
                options: [...r.querySelectorAll('option')].map((o) => [o.value, o.textContent]),
                mono: getComputedStyle(em).fontFamily.toLowerCase().includes('mono'),
                boxed: c.borderTopStyle !== 'none' || c.backgroundColor !== 'rgba(0, 0, 0, 0)'};
      });
      const before = row && order && (order.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
      const sameGroup = row && order && row.closest('section') === order.closest('section');
      const words = {title: row && row.querySelector('.r-title').textContent,
                     desc: row && row.querySelector('.r-desc').textContent,
                     dim: row && row.classList.contains('dim'), orderDim: order && order.classList.contains('dim'),
                     order: order && order.querySelector('select').value};
    """

    def test_one_line_per_account_under_order_with_the_pages_pick(self):
        demo._ready()
        demo.STATE['accounts'][HARBOR]['org'] = 'Coastal Group'          # an organisation its email does not say
        got = self.run_page(self.SETTINGS + "done({rows, before, sameGroup, words});")
        self.assertTrue(got['before'] and got['sameGroup'])
        self.assertEqual((got['words']['title'], got['words']['desc']), ('Each account', LINE))
        self.assertFalse(got['words']['dim'])
        self.assertEqual([r['email'] for r in got['rows']], [WORK, MAIL, FERN, HARBOR, IVY])     # slot order, whole
        orgs = dict((r['email'], r['org']) for r in got['rows'])
        self.assertEqual(orgs[MAIL], '')                    # its row names no organisation: Personal is left out
        self.assertEqual(orgs[WORK], '')                    # nor where the email says it (Northwind)
        self.assertEqual(orgs[HARBOR], 'Coastal Group')     # where the row names one, it is named here too
        for r in got['rows']:
            with self.subTest(email=r['email']):
                self.assertEqual(r['options'], [['in', 'Use'], ['last', 'Use last'], ['out', 'Keep out']])
                self.assertEqual(r['value'], 'in')
                self.assertEqual(r['label'], 'Auto-switch for ' + r['email'])
                self.assertFalse(r['mono'] or r['boxed'])

    def test_a_pick_sends_the_change_and_the_row_gains_its_note(self):
        demo._ready()
        demo.act('/api/auto', {'rule': 'out', 'account': ids()[HARBOR]})
        got = self.run_page(self.SETTINGS + r"""
      const sel = all('#row-rules select').find((s) => s.getAttribute('aria-label').endsWith('%(ivy)s'));
      sel.value = 'last'; sel.dispatchEvent(new Event('change', {bubbles: true})); await wait(1200);
      const picked = all('#row-rules .rule').map((r) => [r.querySelector('.rule-em').textContent,
                                                         r.querySelector('select').value]);
      const cmds = all('#row-rules .r-code .tok span').map((x) => x.textContent);
      location.hash = '#accounts'; await wait(900);
      const subs = {};
      all('.ag.arow').forEach((r) => { subs[r.dataset.email] = r.querySelector('.sub').textContent; });
      done({posts: sent.filter((x) => x[0] === '/api/auto').map((x) => x[1]), picked, subs, cmds});
""" % {'ivy': IVY})
        self.assertEqual(got['posts'], [{'rule': 'last', 'account': ids()[IVY]}])
        self.assertEqual(dict(got['picked'])[IVY], 'last')
        self.assertEqual(dict(got['picked'])[HARBOR], 'out')
        self.assertEqual(got['cmds'], ['switchboard auto out %s' % HARBOR, 'switchboard auto last %s' % IVY])
        self.assertIn(' · Used last', got['subs'][IVY])
        self.assertIn(' · Kept out of auto-switch', got['subs'][HARBOR])
        for e in (WORK, MAIL, FERN):
            self.assertNotIn('Used last', got['subs'][e])
            self.assertNotIn('Kept out', got['subs'][e])

    def test_an_account_id_the_page_must_escape_reaches_the_server_whole(self):
        demo._ready()
        odd = 'acct-"7"&<b>'
        demo.STATE['accounts'][IVY]['id'] = odd
        got = self.run_page(self.SETTINGS + r"""
      const sel = all('#row-rules select').find((s) => s.getAttribute('aria-label').endsWith('%(ivy)s'));
      const k = sel.dataset.k;
      sel.value = 'last'; sel.dispatchEvent(new Event('change', {bubbles: true})); await wait(1200);
      done({posts: sent.filter((x) => x[0] === '/api/auto').map((x) => x[1]), k});
""" % {'ivy': IVY})
        self.assertEqual(got['posts'], [{'rule': 'last', 'account': odd}])
        self.assertEqual(got['k'], 'sel:' + odd)

    def test_the_account_in_use_kept_out_keeps_its_row_and_its_place_and_nothing_is_next(self):
        demo._ready()
        shown = r"""
      await wait(600); location.hash = '#accounts'; await wait(900);
      done({rows: all('.ag.arow').map((r) => r.dataset.email),
            subs: Object.fromEntries(all('.ag.arow').map((r) => [r.dataset.email, r.querySelector('.sub').textContent])),
            next: all('.badge.next').map((b) => b.closest('.ag.arow').dataset.email)});"""
        before = self.run_page(shown)
        demo.act('/api/auto', {'rule': 'out', 'account': ids()[WORK]})       # the account in use
        got = self.run_page(shown)
        self.assertEqual(demo.STATE['active'], WORK)
        self.assertEqual(got['rows'], before['rows'])
        self.assertEqual(len(before['next']), 1)                # with no rule, another account is Next
        self.assertNotIn(WORK, before['next'])
        self.assertEqual(got['next'], [])
        self.assertIn(' · Kept out of auto-switch', got['subs'][WORK])

    def test_dim_while_auto_switch_is_off_the_way_order_is(self):
        demo._ready()
        demo.FACTS['auto'].update(enabled=False, on=False)
        got = self.run_page(self.SETTINGS + "done(words);")
        self.assertTrue(got['dim'] and got['orderDim'])

    def test_an_order_word_the_page_does_not_know_shows_refills_first(self):
        demo._ready()
        demo.FACTS['auto']['strategy'] = 'made-up-order'
        got = self.run_page(self.SETTINGS + "done(words);")
        self.assertEqual(got['order'], 'refills-first')


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class AtEveryWidth(FramedPage):
    def test_whole_emails_and_no_sideways_scroll(self):
        demo._ready()
        got = self.in_frames(r"""
      const out = [];
      for (const f of document.querySelectorAll('iframe')) {
        const d = f.contentDocument;
        f.contentWindow.location.hash = '#settings'; await wait(1500);
        out.push({width: f.clientWidth, overflow: d.documentElement.scrollWidth - d.documentElement.clientWidth,
                  cut: [...d.querySelectorAll('#row-rules .rule-em')]
                    .filter((el) => el.scrollWidth > el.clientWidth + 1).length,
                  shown: d.querySelectorAll('#row-rules .rule').length,
                  picks: [...d.querySelectorAll('#row-rules select')]
                    .map((s) => s.getBoundingClientRect().right <= f.clientWidth + 1)});
      }
      done(out);""", widths=(390, 680, 1280), budget=30000)
        self.assertEqual([f['width'] for f in got], [390, 680, 1280])
        for frame in got:
            with self.subTest(width=frame['width']):
                self.assertEqual(frame['overflow'], 0)
                self.assertEqual((frame['shown'], frame['cut']), (5, 0))
                self.assertTrue(all(frame['picks']))


@unittest.skipUnless(os.path.exists(CHROME) and os.environ.get('SWITCHBOARD_SHOTS'), 'pictures only when asked for')
class Pictures(ChromePage):
    """Settings and the accounts list from the demo with two rules set, at 680 and 1280 px, light and dark, left in
    $SWITCHBOARD_SHOTS (a folder under the temp folder) for a person to look at."""

    def shot(self, path, out, width, height):
        profile = tempfile.mkdtemp(prefix='switchboard-chrome-')
        self.addCleanup(shutil.rmtree, profile, True)
        p = subprocess.Popen([CHROME, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                              '--disable-extensions', '--use-mock-keychain', '--password-store=basic',
                              '--hide-scrollbars',
                              '--user-data-dir=' + profile, '--window-size=%d,%d' % (width, height),
                              '--virtual-time-budget=8000', '--screenshot=' + out, self.url + path],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            p.wait(timeout=90)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            self.fail('headless Chrome took no picture in 90 s')

    def test_pictures(self):
        folder = os.environ['SWITCHBOARD_SHOTS']
        demo._ready()
        demo.act('/api/auto', {'rule': 'last', 'account': ids()[IVY]})
        demo.act('/api/auto', {'rule': 'out', 'account': ids()[HARBOR]})
        for where, height in (('settings', 1500), ('accounts', 1100)):
            for width in (680, 1280):
                for theme in ('light', 'dark'):
                    out = os.path.join(folder, '%s-%d-%s.png' % (where, width, theme))
                    self.shot('?theme=%s#%s' % (theme, where), out, width, height)
                    with self.subTest(picture=out):
                        self.assertGreater(os.path.getsize(out), 10000)
        demo.act('/api/auto', {'rule': 'out', 'account': ids()[WORK]})       # the account in use, kept out too
        for width in (680, 1280):
            for theme in ('light', 'dark'):
                out = os.path.join(folder, 'accounts-in-use-kept-out-%d-%s.png' % (width, theme))
                self.shot('?theme=%s#accounts' % theme, out, width, 1100)
                with self.subTest(picture=out):
                    self.assertGreater(os.path.getsize(out), 10000)


if __name__ == '__main__':
    unittest.main()
