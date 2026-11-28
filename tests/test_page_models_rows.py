import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
import os

# The two Models rows in the page's Settings, under In Claude Code: Models by the job, then Sonnet for helpers. Each
# keeps the page's pattern (its switch, its command, its one line) and its switch posts only its own part. Drawn in
# headless Chrome on the demo's made-up Mac, at 680 and 1280 px, light and dark: nothing runs past the edge. When
# SWITCHBOARD_SHOTS names a folder, a picture of the rows at each width and theme goes there (the run's own
# throwaway folder; nothing else writes it).
import html as htmllib
import json
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import test_app
from switchboard import core, demo, server

HELPERS_LINE = ('Four helpers, each on the model that fits its job: lookups on Haiku, reading, review and checks on '
                'Sonnet.')
SONNET_LINE = 'Helpers and workflow steps that name no model run on Sonnet at high effort. The chat keeps its own.'

ROWS = """
      location.hash = '#settings'; await wait(1200);
      const row = (id) => $('#row-' + id);
      const words = (id) => ({title: row(id).querySelector('.r-title').textContent, desc: row(id).querySelector('.r-desc').textContent,
        on: row(id).querySelector('.r-ctl .sw').getAttribute('aria-checked'), label: row(id).querySelector('.r-ctl .sw').getAttribute('aria-label'),
        cmd: row(id).querySelector('.r-code').textContent, keys: row(id).querySelector('.r-title').title,
        extra: row(id).querySelector('.r-extra').textContent});
      const out = {order: all('#g-code .r-title').map((x) => x.textContent), helpers: words('helpers'), models: words('models'),
                   font: getComputedStyle(row('helpers').querySelector('.r-desc')).fontFamily};
      $('#row-helpers .sw').click(); await wait(900);
      Object.assign(out, {sent: sent.filter((x) => x[0] === '/api/models').map((x) => x[1]), after: words('helpers'),
                          sonnet_after: words('models')});
      done(out);
"""

# One look at a width: both rows drawn, nothing past the right edge, light and dark; then the page's own "In Claude
# Code" filter, so the rows are near the top of the picture (headless Chrome draws nothing in a picture after a scroll).
LOOK = """
      location.hash = '#settings'; await wait(1200);
      const out = {width: innerWidth, found: []};
      for (const theme of ['light', 'dark']) {
        document.documentElement.dataset.theme = theme;
        await wait(100);
        if (document.documentElement.scrollWidth > innerWidth) out.found.push(theme + ': ' + document.documentElement.scrollWidth);
        for (const id of ['helpers', 'models']) {
          const r = $('#row-' + id).getBoundingClientRect();
          if (r.right > innerWidth + 0.5 || r.width < 200) out.found.push(theme + ' ' + id + ': ' + r.left + '..' + r.right);
          for (const el of $('#row-' + id).querySelectorAll('.r-title, .r-desc, .r-ctl, .r-code')) {
            const b = el.getBoundingClientRect();
            if (b.right > innerWidth + 0.5) out.found.push(theme + ' ' + id + ' ' + el.className + ' runs past the edge');
          }
        }
      }
      document.documentElement.dataset.theme = %(theme)s;
      $('[data-do="chip"][data-g="code"]').click(); await wait(300);    // the In Claude Code group alone, for the picture
      done(out);
"""


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class ModelsRows(test_app.ChromePage):

    def test_two_rows_each_with_its_switch_line_and_command(self):
        got = self.run_page(ROWS)
        self.assertEqual(got['order'], ['Status line', 'Models by the job', 'Sonnet for helpers', 'House rules',
                                        'Code map', 'claude-mem'])
        h, m = got['helpers'], got['models']
        self.assertEqual((h['title'], h['desc'], h['on'], h['label']), ('Models by the job', HELPERS_LINE, 'false',
                                                                        'Models by the job'))
        self.assertEqual((m['title'], m['desc'], m['on'], m['label']), ('Sonnet for helpers', SONNET_LINE, 'false',
                                                                        'Sonnet for helpers'))
        self.assertEqual((h['cmd'], m['cmd']), ('switchboard models off', 'switchboard models sonnet off'))
        self.assertIn('~/.claude/agents/', h['keys'])
        self.assertIn('~/.claude/rules/', h['keys'])
        self.assertIn('env.CLAUDE_CODE_SUBAGENT_EFFORT', m['keys'])
        self.assertNotIn('mono', got['font'].lower())                   # the line is plain text, not code
        self.assertEqual(got['sent'], [{'enabled': True, 'part': 'helpers'}])   # its own part, nothing of the other
        self.assertEqual((got['after']['on'], got['after']['cmd']), ('true', 'switchboard models on'))
        self.assertEqual(got['sonnet_after']['on'], 'false')
        self.assertEqual((demo.FACTS['helpers'], demo.FACTS['models']), (True, False))

    def test_a_helper_of_yours_is_named_under_the_row(self):
        demo._ready()
        real = demo.models

        def mine():
            v = real()
            v['helpers'] = {'enabled': True, 'blocked': None,
                            'left_out': [{'name': 'reviewer', 'why': 'you have your own'}]}
            return v
        with mock.patch.object(demo, 'models', mine):
            got = self.run_page(ROWS)
        self.assertEqual(got['helpers']['extra'], 'Left out: reviewer (you have your own).')
        self.assertEqual(got['helpers']['on'], 'true')

    def test_an_effort_level_of_yours_is_said_under_both_rows(self):
        demo._ready()
        real = demo.models

        def set_level():
            return dict(real(), effort_level={'value': 'low', 'where': 'your user settings'})
        with mock.patch.object(demo, 'models', set_level):
            got = self.run_page(ROWS)
        line = 'CLAUDE_CODE_EFFORT_LEVEL=low in your user settings comes first: every helper runs at that effort.'
        self.assertEqual((got['helpers']['extra'], got['models']['extra']), (line, line))
        shots = os.environ.get('SWITCHBOARD_SHOTS')
        if shots:                       # a picture of both on, with a helper of yours and an effort level of yours

            def both_on():
                return dict(real(), enabled=True, effort_level={'value': 'low', 'where': 'your user settings'},
                            helpers={'enabled': True, 'blocked': None,
                                     'left_out': [{'name': 'reviewer', 'why': 'you have your own'}]})
            with mock.patch.object(demo, 'models', both_on):
                self.assertEqual(self.look(1280, 'light', shots, 'models-rows-on')['found'], [])

    def test_at_680_and_1280_px_light_and_dark_nothing_runs_past_the_edge(self):
        shots = os.environ.get('SWITCHBOARD_SHOTS')
        for width in (680, 1280):
            for theme in ('light', 'dark'):
                if theme == 'dark' and not shots:
                    continue                     # the look checks both themes; the second run is only for its picture
                with self.subTest(width=width, theme=theme):
                    got = self.look(width, theme, shots)
                    self.assertEqual(got['found'], [])
                    self.assertEqual(got['width'], width)

    def look(self, width, theme, shots, name='models-rows'):
        """The LOOK script at this window width; with `shots`, a second Chrome run saves its picture there."""
        js = (self.PRELUDE % (LOOK % {'theme': json.dumps(theme)})).encode()

        def page(handler, name):
            with open(os.path.join(core.WEB, name), 'rb') as f:
                data = f.read()
            if name == 'index.html':
                data = data.replace(b'</body>', b'<script>' + js + b'</script></body>')
            handler._send(200, data, 'text/html; charset=utf-8')
        with mock.patch.object(server.Handler, '_page', page):
            dom = self.chrome(['--window-size=%d,900' % width, '--dump-dom'], self.url + '?theme=%s#settings' % theme)
        if shots:
            # The picture is of the page as Chrome built it above (its scripts taken out, so nothing redraws): a
            # --screenshot of the live page with virtual time comes out before anything is drawn.
            still = tempfile.mkdtemp(prefix='switchboard-still-')
            self.addCleanup(shutil.rmtree, still, True)
            page = re.sub(r'<script\b.*?</script>', '', dom, flags=re.S).replace('font-display: block',
                                                                                'font-display: swap')  # drawn at once
            for folder in (still, shots):                       # the still beside its picture, to look at again
                with open(os.path.join(folder, 'page.html' if folder == still else
                                       '%s-%d-%s.html' % (name, width, theme)), 'w', encoding='utf-8') as f:
                    f.write(page)
            self.chrome(['--window-size=%d,900' % width, '--hide-scrollbars',
                         '--screenshot=' + os.path.join(shots, '%s-%d-%s.png' % (name, width, theme))],
                        'file://' + os.path.join(still, 'page.html'), budget=False)
        m = re.search(r'<pre id="result">(.*?)</pre>', dom, re.S)
        if not m:
            self.fail('the page script did not finish at %d px' % width)
        got = json.loads(htmllib.unescape(m.group(1)))
        if got.get('error'):
            self.fail(got['error'])
        return got

    def chrome(self, flags, url, budget=True):
        """Headless Chrome on `url`, as test_app.ChromePage.dom runs it, with these flags: what it printed. A still page
        needs no virtual time (and a picture taken with it can come out before anything is drawn)."""
        profile = tempfile.mkdtemp(prefix='switchboard-chrome-')
        self.addCleanup(shutil.rmtree, profile, True)
        out = os.path.join(profile, 'out.html')
        with open(out, 'wb') as f:
            p = subprocess.Popen([test_app.CHROME, '--headless=new', '--disable-gpu', '--no-first-run',
                                  '--no-default-browser-check', '--disable-extensions', '--use-mock-keychain',
                                  '--password-store=basic', '--user-data-dir=' + profile]
                                 + (['--virtual-time-budget=8000'] if budget else []) + flags + [url], stdout=f, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            try:
                p.wait(timeout=90)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
                self.fail('headless Chrome did not finish the page in 90 s')
        with open(out, encoding='utf-8', errors='replace') as f:
            return f.read()


if __name__ == '__main__':
    unittest.main()
