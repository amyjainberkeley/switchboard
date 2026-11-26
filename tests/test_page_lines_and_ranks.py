import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# An account's own switch lines and its rank on the page: in Settings, on each account's line under Each account,
# after its pick: two line fields (empty: the shared line, shown faint) and a rank field, under one row of small
# names; the models auto-switch counts as ticks; the third order in the Order pick; and on the accounts list, the
# thin mark on an account's bar at its own line. The demo keeps all of it in memory. Made-up accounts only (the
# demo's).
# Its page tests start headless Chrome: run the file under the Chrome lock. With SWITCHBOARD_SHOTS naming an empty
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

    def test_it_starts_with_none_and_keeps_them_in_memory(self):
        v = demo.view()
        self.assertEqual((v['auto']['own_lines'], v['auto']['ranks']), ({}, {}))
        demo.act('/api/auto', {'account': ids()[IVY], 'own_lines': {'session_at': 85}})
        demo.act('/api/auto', {'account': IVY, 'own_lines': {'weekly_at': 90}})
        demo.act('/api/auto', {'account': FERN, 'rank': 1})
        v = demo.view()
        self.assertEqual(v['auto']['own_lines'], {ids()[IVY]: {'session_at': 85.0, 'weekly_at': 90.0}})
        self.assertEqual(v['auto']['ranks'], {ids()[FERN]: 1})
        demo.act('/api/auto', {'account': IVY, 'own_lines': {'session_at': None}})
        demo.act('/api/auto', {'account': FERN, 'rank': None})
        v = demo.view()
        self.assertEqual((v['auto']['own_lines'], v['auto']['ranks']), ({ids()[IVY]: {'weekly_at': 90.0}}, {}))

    def test_wrong_values_are_refused_as_on_the_real_page(self):
        demo.view()
        for body, words in (({'account': IVY, 'own_lines': {'session_at': 120}}, 'Pick a number from 50 to 99.9.'),
                            ({'account': IVY, 'rank': 0}, 'Pick a whole number from 1 to 99.'),
                            ({'account': 'nobody@made-up.example', 'rank': 1},
                             'That account is not saved in Switchboard.')):
            with self.subTest(body=body), self.assertRaises(core.SwitchError) as e:
                demo.act('/api/auto', body)
            self.assertEqual(str(e.exception), words)

    def test_several_models_and_the_third_order(self):
        demo.view()
        demo.act('/api/auto', {'model': ['Fable', 'Opus 5']})
        self.assertEqual((demo.view()['auto']['model'], demo.view()['auto']['models']), ('Fable', ['Fable', 'Opus 5']))
        demo.act('/api/auto', {'model': []})
        self.assertEqual((demo.view()['auto']['model'], demo.view()['auto']['models']), (None, []))
        demo.act('/api/auto', {'strategy': 'stay-then-refills'})
        self.assertEqual(demo.view()['auto']['strategy'], 'stay-then-refills')


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class InSettings(ChromePage):
    ROWS = r"""
      await wait(600);
      location.hash = '#settings'; await wait(900);
      const rows = () => all('#row-rules .rule').map((r) => {
        const ins = [...r.querySelectorAll('input')];
        return {email: r.querySelector('.rule-em').textContent, rule: r.querySelector('select').value,
                values: ins.map((i) => i.value), holders: ins.map((i) => i.placeholder),
                labels: ins.map((i) => i.getAttribute('aria-label')),
                after: ins.every((i) => (r.querySelector('select').compareDocumentPosition(i) &
                                         Node.DOCUMENT_POSITION_FOLLOWING) !== 0)};
      });
      const head = all('#row-rules .rule-head span').map((s) => s.textContent);
    """

    def test_each_account_has_its_lines_and_rank_after_its_pick(self):
        demo._ready()
        demo.act('/api/auto', {'account': IVY, 'own_lines': {'session_at': 85}})
        demo.act('/api/auto', {'account': FERN, 'rank': 2})
        got = self.run_page(self.ROWS + "done({rows: rows(), head, order: [...$('#row-order select').options].map("
                                        "(o) => [o.value, o.textContent])});")
        self.assertEqual(got['head'], ['5-hour', 'Weekly', 'Rank'])
        by = dict((r['email'], r) for r in got['rows'])
        self.assertEqual([r['email'] for r in got['rows']], [WORK, MAIL, FERN, HARBOR, IVY])
        self.assertEqual(by[IVY]['values'], ['85', '', ''])
        self.assertEqual(by[FERN]['values'], ['', '', '2'])
        self.assertEqual(by[WORK]['holders'], ['90', '95', '–'])
        self.assertEqual(by[IVY]['labels'], ['5-hour line of %s, empty for the shared one' % IVY,
                                             'Weekly line of %s, empty for the shared one' % IVY,
                                             'Rank of %s, empty for none' % IVY])
        self.assertTrue(all(r['after'] for r in got['rows']))
        self.assertEqual(got['order'][-1], ['stay-then-refills', 'Stay, then refills first'])

    def test_typing_sends_the_change_and_empty_takes_it_away(self):
        demo._ready()
        demo.act('/api/auto', {'account': FERN, 'rank': 2})
        got = self.run_page(self.ROWS + r"""
      const field = (email, n) => all('#row-rules .rule').find((r) => r.querySelector('.rule-em').textContent === email)
        .querySelectorAll('input')[n];
      const put = async (el, v) => { el.value = v; el.dispatchEvent(new Event('change', {bubbles: true})); await wait(1200); };
      await put(field('%(ivy)s', 0), '85');
      await put(field('%(ivy)s', 1), '120');                  // refused here: nothing is sent
      await put(field('%(fern)s', 2), '');
      await put(field('%(work)s', 2), '1');
      const now = rows();
      const cmds = all('#row-rules .r-code .tok span').map((x) => x.textContent);
      done({posts: sent.filter((x) => x[0] === '/api/auto').map((x) => x[1]), now, cmds});
""" % {'ivy': IVY, 'fern': FERN, 'work': WORK})
        self.assertEqual(got['posts'], [{'account': ids()[IVY], 'own_lines': {'session_at': 85}},
                                        {'account': ids()[FERN], 'rank': None},
                                        {'account': ids()[WORK], 'rank': 1}])
        by = dict((r['email'], r) for r in got['now'])
        self.assertEqual((by[IVY]['values'], by[FERN]['values'], by[WORK]['values']),
                         (['85', '', ''], ['', '', ''], ['', '', '1']))
        self.assertEqual(got['cmds'], ['switchboard auto rank %s 1' % WORK,
                                       'switchboard auto line %s --five-hour 85' % IVY])

    def test_the_models_are_ticks(self):
        demo._ready()
        got = self.run_page(r"""
      await wait(600); location.hash = '#settings'; await wait(900);
      const ticks = () => all('#row-model input[type=checkbox]').map((x) => [x.value, x.checked]);
      const before = ticks();
      const fable = all('#row-model input[type=checkbox]').find((x) => x.value === 'Fable');
      fable.checked = !fable.checked; fable.dispatchEvent(new Event('change', {bubbles: true})); await wait(1200);
      done({before, after: ticks(), posts: sent.filter((x) => x[0] === '/api/auto').map((x) => x[1])});
""")
        self.assertEqual(got['before'], [['Fable', True]])
        self.assertEqual(got['posts'], [{'model': []}])
        self.assertEqual(got['after'], [['Fable', False]])

    def test_the_bar_mark_moves_to_the_accounts_own_line(self):
        demo._ready()
        demo.act('/api/auto', {'account': IVY, 'own_lines': {'session_at': 60}})
        got = self.run_page(r"""
      await wait(600); location.hash = '#accounts'; await wait(900);
      const marks = {};
      all('.ag.arow').forEach((r) => { marks[r.dataset.email] = [...r.querySelectorAll('.bar b')].map((b) => b.style.left); });
      const red = {};
      all('.ag.arow').forEach((r) => { red[r.dataset.email] = r.querySelectorAll('.bar.over').length; });
      done({marks, red});
""")
        self.assertEqual(got['marks'][IVY][:2], ['60%', '95%'])
        self.assertEqual(got['marks'][FERN][:2], ['90%', '95%'])
        self.assertEqual(got['red'][IVY], 0)                    # 20% used: under its own 60
        demo.act('/api/auto', {'account': IVY, 'own_lines': {'session_at': 50}})
        demo.STATE['accounts'][IVY]['usage']['session']['pct'] = 55
        got = self.run_page(r"""
      await wait(600); location.hash = '#accounts'; await wait(900);
      done(all('.ag.arow').filter((r) => r.dataset.email === '%s').map((r) => r.querySelectorAll('.bar.over').length));
""" % IVY)
        self.assertEqual(got, [1])


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class AtEveryWidth(FramedPage):
    def test_whole_emails_fields_in_the_frame_and_no_sideways_scroll(self):
        demo._ready()
        got = self.in_frames(r"""
      const out = [];
      for (const f of document.querySelectorAll('iframe')) {
        const d = f.contentDocument;
        f.contentWindow.location.hash = '#settings'; await wait(1500);
        out.push({width: f.clientWidth, overflow: d.documentElement.scrollWidth - d.documentElement.clientWidth,
                  cut: [...d.querySelectorAll('#row-rules .rule-em')]
                    .filter((el) => el.scrollWidth > el.clientWidth + 1).length,
                  inside: [...d.querySelectorAll('#row-rules input, #row-rules select')]
                    .map((s) => s.getBoundingClientRect().right <= f.clientWidth + 1),
                  aligned: [...d.querySelectorAll('#row-rules .rule')].map((r) => Math.round(
                    r.querySelectorAll('input')[2].getBoundingClientRect().right -
                    d.querySelector('#row-rules .rule-head .rk').getBoundingClientRect().right))});
      }
      done(out);""", widths=(390, 680, 1280), budget=30000)
        self.assertEqual([f['width'] for f in got], [390, 680, 1280])
        for frame in got:
            with self.subTest(width=frame['width']):
                self.assertEqual(frame['overflow'], 0)
                self.assertEqual(frame['cut'], 0)
                self.assertTrue(frame['inside'] and all(frame['inside']))
                self.assertEqual(set(frame['aligned']), {0})         # each rank field under the Rank name

    def test_each_percent_sign_sits_inside_its_field(self):
        """The % of a number field is drawn at the right edge of the span around the field, so that span must be as
        wide as the field and no wider: a name above it that is wider than the field (a model's week) must not
        stretch it, or the % lands outside the field's border."""
        demo._ready()
        got = self.in_frames(r"""
      const out = [];
      for (const f of document.querySelectorAll('iframe')) {
        const d = f.contentDocument;
        f.contentWindow.location.hash = '#settings'; await wait(1500);
        out.push({width: f.clientWidth,
                  names: [...d.querySelectorAll('#row-lines .lines label')].map((l) => l.firstChild.textContent),
                  past: [...d.querySelectorAll('.num')].filter((n) => n.getClientRects().length)
                    .map((n) => Math.round(n.getBoundingClientRect().right -
                                           n.querySelector('input').getBoundingClientRect().right))});
      }
      done(out);""", widths=(390, 680, 1280), budget=30000)
        self.assertEqual([f['width'] for f in got], [390, 680, 1280])
        for frame in got:
            with self.subTest(width=frame['width']):
                self.assertEqual(frame['names'], ['5-hour', 'Weekly', 'Fable’s week'])
                self.assertGreaterEqual(len(frame['past']), 3)       # the three shared lines, and each account's
                self.assertEqual(set(frame['past']), {0})


@unittest.skipUnless(os.path.exists(CHROME) and os.environ.get('SWITCHBOARD_SHOTS'), 'pictures only when asked for')
class Pictures(ChromePage):
    """Settings and the accounts list from the demo with own lines, ranks and a rule set, at 680 and 1280 px, light
    and dark, left in $SWITCHBOARD_SHOTS (a folder under the temp folder) for a person to look at."""

    def shot(self, path, out, width, height):
        profile = tempfile.mkdtemp(prefix='switchboard-chrome-')
        self.addCleanup(shutil.rmtree, profile, True)
        p = subprocess.Popen([CHROME, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
                              '--disable-extensions', '--use-mock-keychain', '--password-store=basic',
                              '--hide-scrollbars', '--user-data-dir=' + profile, '--window-size=%d,%d' % (width, height),
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
        demo.act('/api/auto', {'rule': 'last', 'account': ids()[MAIL]})
        demo.act('/api/auto', {'account': IVY, 'own_lines': {'session_at': 60, 'weekly_at': 90}})
        demo.act('/api/auto', {'account': FERN, 'rank': 1})
        demo.act('/api/auto', {'account': WORK, 'rank': 2})
        demo.act('/api/auto', {'strategy': 'stay-then-refills'})
        for where, height in (('settings', 1500), ('accounts', 1100)):
            for width in (680, 1280):
                for theme in ('light', 'dark'):
                    out = os.path.join(folder, '%s-%d-%s.png' % (where, width, theme))
                    self.shot('?theme=%s#%s' % (theme, where), out, width, height)
                    with self.subTest(picture=out):
                        self.assertGreater(os.path.getsize(out), 10000)


if __name__ == '__main__':
    unittest.main()
