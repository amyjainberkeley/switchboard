import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Small command-line and page wording rules:
#
# - An unknown command with --help or -h is still unknown: "Unknown command: NAME." then the help, exit 2.
# - `switchboard auto` names the model weeks auto-switch counts (", Fable too"), as the page's Settings does: one rule,
#   term.model_words, the page's modelNames. The header of `switchboard` and the menu bar's Auto-switch line name
#   only the lines the account rows show, as the page's switch line: never a model's week.
# - `switchboard doctor` with the status line off names both ways to turn it on.
# - `switchboard page` (with the page running) and `switchboard demo`, when macOS does not let this terminal start
#   /usr/bin/open or it fails: the address to open, no traceback; the demo serves on.
# - `switchboard version` says what it does in the help, as every other command does.
# - The first run's heading wraps at every width. At phone widths it does through the 640 px rule; above 640 px it
#   must not stay on one line (h1, white-space: nowrap), or a long email runs past the edge of a narrow window, and with
#   it the page. Each width is a frame of its own (headless Chrome keeps its window at least 500 px wide).
#
# Made-up accounts only (*.example); the engine (a fake of accounts.api), the browser and `open` are stand-ins, every
# folder is a throwaway one, and the page's port is 7898, where nothing listens.
import html as htmllib
import inspect
import io
import json
import os
import re
import socket
import sys
import time
import unittest
import urllib.request
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import core, demo, menubar, server, term
import test_app  # its ChromePage: the real page in headless Chrome
import test_keep  # its Sandbox: a fake engine and automatic-switching host, the page down
import test_menubar  # its made-up state of the page, as the menu bar reads it
import test_cli_help_and_no_page as cli_help  # its Fresh, nothing_runs, refused and the help's lines
import test_cli_commands  # its Cli: a throwaway home and the fake accounts, for the doctor
import test_term  # its made-up state of the page, as the terminal reads it

PORT = '7898'                   # nothing listens on this port, so the page is not running


class Fresh(cli_help.Fresh):
    """The throwaway home, data folder and Claude Code folder of test_cli_help_and_no_page, with this file's port."""

    def setUp(self):
        super().setUp()
        os.environ['SWITCHBOARD_PORT'] = PORT                   # inside the base's patch.dict: put back after each test
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)

    def asked(self, *args):
        """(exit code, stdout, stderr, what ran) of `switchboard ARGS` with nothing able to run."""
        with cli_help.nothing_runs() as ran:
            code, out, err = self.cli(*args)
        return code, out, err, ran


# ---------- an unknown command with --help ----------

class UnknownCommandWithHelp(Fresh):
    def test_an_unknown_command_with_help_says_so_first(self):
        for args in (('bogus', '--help'), ('bogus', '-h'), ('bogus', 'now', '--help'), ('--colour', '-h')):
            with self.subTest(args=args):
                self.assertEqual(self.asked(*args), (2, 'Unknown command: %s.\n' % args[0] + cli_help.short_help(), '',
                                                     []))

    def test_it_is_what_the_command_says_without_help(self):
        self.assertEqual(self.asked('bogus', '--help'), self.asked('bogus'))

    def test_commands_it_knows_keep_their_help(self):
        short = cli_help.short_help()           # a word with no lines of its own in the help: the short help
        for args, said in ((('tidy', '--help'), short), (('help', '-h'), short),
                           (('--help', '--help'), short), (('-h', '-h'), short),
                           (('--version', '--help'), short), (('-v', '-h'), short),
                           (('auto', '--help'), cli_help.AUTO), (('2', '-h'), cli_help.TWO), (('use', 'maya', '--help'), cli_help.USE)):
            with self.subTest(args=args):
                self.assertEqual(self.asked(*args), (0, said, '', []))


# ---------- the model weeks auto-switch counts, named in the terminal and the menu bar ----------

class ModelWeekWords(unittest.TestCase):
    def test_the_words_follow_the_pages_rule(self):
        """index.html, modelWords: the model weeks counted, by name (the engine counts one model's week, or
        none); a week only seen on an account is not said, and "all" or "none" is not a model."""
        for auto, words in (({'models': ['Fable'], 'models_seen': ['Fable', 'Opus']}, ', Fable too'),
                            ({'models': ['Fable', 'Opus'], 'models_seen': []}, ', Fable and Opus too'),
                            ({'models': ['Fable', 'Opus', 'Sonnet'], 'models_seen': []},
                             ', Fable, Opus and Sonnet too'),
                            ({'models': ['all'], 'models_seen': ['Fable']}, ''),
                            ({'models': ['none'], 'models_seen': ['Fable']}, ''),
                            ({'models': [], 'models_seen': ['Fable']}, ''),
                            ({}, '')):
            with self.subTest(auto=auto):
                self.assertEqual(term.model_words(auto), words)

    def test_the_header_of_switchboard_does_not_name_them(self):
        # (it said ", Fable too": a model's week has no line in the rows now, so the header leaves it out too)
        view = dict(test_term.VIEW, auto=dict(test_term.VIEW['auto'], models=['Fable'], models_seen=['Fable']))
        self.assertEqual(term.view(term.read(view, tz=test_term.TZ))[0],
                         'Claude Code is on ana@mail.example · auto-switch on at 90% 5-hour · 95% weekly')

    def test_with_none_counted_the_header_says_nothing_more(self):
        view = dict(test_term.VIEW, auto=dict(test_term.VIEW['auto'], models=[], models_seen=['Fable']))
        self.assertEqual(term.view(term.read(view, tz=test_term.TZ))[0],
                         'Claude Code is on ana@mail.example · auto-switch on at 90% 5-hour · 95% weekly')

    def test_the_menu_bars_auto_switch_line_does_not_name_them(self):
        # (it said ", Fable too", as the header did; the 5-hour line is there because an account has a 5-hour limit)
        s = test_menubar.view()
        s['auto'].update(models=['Fable'], models_seen=['Fable'])
        self.assertIn(('auto', True, 'at 90% 5-hour · 95% weekly', True), menubar.menu(s))

    def test_with_none_counted_the_menu_bar_says_nothing_more(self):
        s = test_menubar.view()
        s['auto'].update(models=[], models_seen=['Fable'])
        self.assertIn(('auto', True, 'at 90% 5-hour · 95% weekly', True), menubar.menu(s))


class ModelWeeksOnTheDemo(Fresh):
    def setUp(self):
        super().setUp()
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        core.DEMO = True

    def test_its_header_does_not_name_them(self):
        """`switchboard status` on the demo, which counts Fable's week: the header names the lines the rows show."""
        with mock.patch.object(cli, 'width', lambda: None):
            code, out, err = self.cli('status')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(out.splitlines()[0], 'Claude Code is on maya@northwind.example · auto-switch on at 90% 5-hour '
                                              '· 95% weekly')


class ModelWeeksInTheAutoCommand(test_keep.Sandbox):
    """`switchboard auto` with the page down: the engine's settings, and the model weeks the page last saw."""

    def setUp(self):
        super().setUp()
        self.job()                                              # the page runs under the background job
        self.fake.auto.update(enabled=True, strategy='refills-first', weekly_at=95.0, model=None)

    def seen(self, *names):
        """The page's last look found these model weeks on an account (server.apply_rows keeps them in its state)."""
        with core.locked() as st:
            core.ensure_account(st, 'maya@ivy.example').update(connected=True, slot=1, models_seen=list(names))

    def test_the_model_week_it_switches_on_is_named(self):
        self.fake.auto['model'] = 'Fable'
        self.assertEqual(self.run_cli('auto'), (0, 'Auto-switch is on: at 90% 5-hour · 95% weekly, Fable too, using '
                                                   'what refills first.\n'))

    def test_none_counted_says_nothing_more_even_with_one_seen(self):
        self.assertEqual(self.run_cli('auto'), (0, 'Auto-switch is on: at 90% 5-hour · 95% weekly, using what refills '
                                                   'first.\n'))
        self.seen('Fable')
        self.assertEqual(self.run_cli('auto'), (0, 'Auto-switch is on: at 90% 5-hour · 95% weekly, using what refills '
                                                   'first.\n'))


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class SwitchLineSameWordsAsThePage(test_app.ChromePage):
    """The page's switch line in headless Chrome and the terminal's header, on the same demo state: the same lines,
    whatever model weeks auto-switch counts (none is named: a model's week has no line in the rows), and no 5-hour
    line when no account has a 5-hour limit. (It compared the model names after the weekly line.)"""

    def page_words(self):
        return self.run_page("done([...document.querySelectorAll('#auto .w')].map((w) => w.textContent.trim())"
                             ".filter((w) => w && w !== 'at'));")

    def terminal_words(self):
        text = ''.join(t for t, _ in term.header(term.read(server.view())['head'])[0])
        return re.findall(r'% (5-hour|weekly)', text)

    def test_the_page_and_the_terminal_say_the_same(self):
        for models, no_five in ((['Fable'], False), (['Fable', 'Opus'], False), ([], False), (['Fable'], True)):
            with self.subTest(models=models, no_five=no_five):
                demo.STATE.clear()
                demo.FACTS.clear()
                demo.view()
                demo.FACTS['auto'].update(model=models[0] if models else None, models=models)
                if no_five:
                    for a in demo.STATE['accounts'].values():
                        a['usage']['session'] = None
                want = ['weekly'] if no_five else ['5-hour', 'weekly']
                self.assertEqual((self.terminal_words(), self.page_words()), (want, want))


# ---------- the doctor's status line row ----------

class DoctorStatusLineOff(test_cli_commands.Cli):
    ROW = ' --  Status line in Claude Code\n       Optional. Turn it on from the page, or run: switchboard statusline on\n'

    def doctor(self):
        self.two()
        with mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(cli.linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(cli.browser, 'present', lambda: False), \
                mock.patch.object(cli.connect, 'claude_bin', lambda: None):
            return self.run_cli('doctor')[1]

    def settings(self, text):
        with open(os.path.join(os.environ['CLAUDE_CONFIG_DIR'], 'settings.json'), 'w') as f:
            f.write(text)

    def test_off_names_the_page_and_the_command(self):
        self.assertIn(self.ROW, self.doctor())

    def test_off_beside_a_line_of_your_own_names_the_route_that_works(self):
        # plain `switchboard statusline on` leaves your own line and exits 1, so the row names --replace
        self.settings('{"statusLine": {"type": "command", "command": "echo mine"}}\n')
        self.assertIn(" --  Status line in Claude Code: your own\n       Optional. To use Switchboard's instead, turn it on "
                      "from the page, or run: switchboard statusline on --replace\n", self.doctor())

    def test_a_settings_file_that_cant_be_read_keeps_its_own_words(self):
        self.settings('{not json')
        self.assertIn(" NO  Status line in Claude Code\n       Claude Code's settings file can't be read, or the Python "
                      "the line runs is gone. Check the file, then run `switchboard statusline on` again.\n",
                      self.doctor())


# ---------- `open` refused or failing, for `switchboard page` and `switchboard demo` ----------

class PageOpenRefused(Fresh):
    URL = 'http://localhost:%s' % PORT

    def page(self, opener):
        with mock.patch.object(cli, 'page_running', lambda: True), mock.patch.object(cli.subprocess, 'call', opener), \
                mock.patch.object(cli.subprocess, 'Popen', opener):
            return self.cli('page')

    def test_open_refused_says_the_address_to_open(self):
        self.assertEqual(self.page(cli_help.refused), (1, 'Open %s in your browser.\n' % self.URL, ''))

    def test_open_failing_says_it_too(self):
        self.assertEqual(self.page(lambda argv, **kw: 1), (1, 'Open %s in your browser.\n' % self.URL, ''))

    def test_open_that_works_opens_the_page_and_says_nothing(self):
        seen = []
        self.assertEqual(self.page(lambda argv, **kw: seen.append(argv) or 0), (0, '', ''))
        self.assertEqual(seen, [['/usr/bin/open', self.URL]])


class Stop(Exception):
    """The demo reached its wait: it serves on."""


class DemoOpenRefused(Fresh):
    def setUp(self):
        super().setUp()
        free = socket.socket()
        free.bind(('127.0.0.1', 0))
        self.port = free.getsockname()[1]
        free.close()
        os.environ['SWITCHBOARD_PORT'] = str(self.port)
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        real = server.ThreadingHTTPServer

        def making(*a, **kw):                                   # each server the demo makes is stopped after the test
            s = real(*a, **kw)
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
            return s
        p = mock.patch.object(server, 'ThreadingHTTPServer', making)
        p.start()
        self.addCleanup(p.stop)

    def demo(self, opener):
        """`switchboard demo` until it waits to be stopped (Ctrl-C): what it printed."""
        def wait(_):
            raise Stop()
        clock = type('Clock', (), {'time': staticmethod(time.time), 'sleep': staticmethod(wait)})
        out = io.StringIO()
        with mock.patch.object(cli.subprocess, 'Popen', opener), mock.patch.object(cli.subprocess, 'call', opener), \
                mock.patch.object(server, 'time', clock), redirect_stdout(out):
            with self.assertRaises(Stop):
                cli.main(['demo'])
        return out.getvalue()

    def test_open_refused_says_the_address_and_the_demo_serves_on(self):
        url = 'http://localhost:%d' % self.port
        self.assertEqual(self.demo(cli_help.refused), 'Open %s in your browser.\nSwitchboard demo (made-up accounts) at %s'
                                                 '  -  Ctrl-C to stop\n' % (url, url))
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with direct.open('http://127.0.0.1:%d/api/state' % self.port, timeout=30) as r:
            self.assertIs(json.load(r).get('demo'), True)

    def test_it_says_its_address_and_opens_it_in_a_tab(self):
        seen = []
        self.assertEqual(self.demo(lambda argv, **kw: seen.append(argv)),
                         'Switchboard demo (made-up accounts) at http://localhost:%d  -  Ctrl-C to stop\n' % self.port)
        self.assertEqual(seen, [['/usr/bin/open', 'http://localhost:%d' % self.port]])


# ---------- `switchboard version` in the help ----------

class VersionHelp(Fresh):
    def test_version_help_says_what_it_does(self):
        self.assertEqual(self.asked('version', '--help'),
                         (0, 'switchboard version         which version this is\n', '', []))

    def test_every_command_in_the_help_says_what_it_does(self):
        """Its words on its own line, or on the lines under it (`switchboard log [-n N] [-f] [--json]`)."""
        lines = inspect.cleandoc(cli.__doc__).splitlines()
        for i, line in enumerate(lines):
            if line.startswith('switchboard'):
                with self.subTest(line=line):
                    own = re.split(r'\s{2,}', line, maxsplit=1)[1:]
                    under = lines[i + 1].startswith(' ') if i + 1 < len(lines) else False
                    self.assertTrue(own or under, 'no words for: %s' % line)

    def test_version_prints_its_version_and_build(self):
        self.assertEqual(self.cli('version'), (0, 'switchboard %s\n' % core.version_text(), ''))   # with its build


# ---------- the first run's heading wraps at every width ----------

LONG = 'maya.reyes-longname@northwind-industries.example'          # made up, as long as real ones get

# The page in one frame per width, light and dark: whatever in <main> ends past the frame's right edge, and how wide the
# page is, in #result (the empty <main> is what test_app's dom() waits for).
FIRST_RUN_FRAMES = r"""<!doctype html><meta charset="utf-8"><body style="margin:0"><main hidden></main>%(frames)s<pre id="result"></pre><script>
(async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms)), out = {found: [], widths: [], looks: 0};
  const frames = [...document.querySelectorAll('iframe')], doc = (f) => f.contentDocument;
  try {
    for (let i = 0; i < 300 && !frames.every((f) => doc(f) && doc(f).querySelector('main[data-drawn] .fr-title')); i++) await wait(50);
    await Promise.all(frames.map((f) => doc(f).fonts.ready));
    await wait(300);
    out.widths = frames.map((f) => f.contentWindow.innerWidth);
    for (const f of frames) {
      const d = doc(f), w = f.contentWindow.innerWidth, say = (theme, what) => out.found.push(w + ' px, ' + theme + ': ' + what);
      for (const theme of ['light', 'dark']) {
        d.documentElement.dataset.theme = theme;
        out.looks++;
        if (d.documentElement.scrollWidth > w) say(theme, 'the page is ' + d.documentElement.scrollWidth + ' px wide');
        const h1 = d.querySelector('.fr-title');
        if (h1.scrollWidth > h1.clientWidth + 1) say(theme, 'the heading runs past its own box');
        for (const el of d.querySelectorAll('main *')) {
          if (el.getClientRects().length && el.getBoundingClientRect().right > w + 0.5) say(theme, (el.className || el.tagName) + ' ends at ' + Math.round(el.getBoundingClientRect().right));
        }
      }
      delete d.documentElement.dataset.theme;
    }
  } catch (e) { out.error = String(e && e.stack || e); }
  document.getElementById('result').textContent = JSON.stringify(out);
})();
</script>"""


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class FirstRunHeadingWraps(test_app.ChromePage):
    """The first run: Claude Code is on an account not added yet. One look, every width a frame of its own."""
    PHONES = (320, 390, 430)
    NARROW = (641, 720)                 # a narrow window, just over the 640 px rule
    maxDiff = None

    def look(self, email, widths):
        demo._ready()
        for a in demo.STATE['accounts'].values():
            a['connected'] = False
        demo.STATE['accounts'][email] = dict(demo.STATE['accounts']['maya@northwind.example'], email=email, slot=None,
                                             connected=True, unmanaged=True)
        if email not in demo.STATE['order']:
            demo.STATE['order'].append(email)
        demo.STATE['active'] = email
        demo.FACTS.update(setup_hidden=False)

        def page(handler, name):
            if 'frames' in handler.path:
                body = (FIRST_RUN_FRAMES % {'frames': ''.join(
                    '<iframe src="/" style="display:block;width:%dpx;height:700px;border:0"></iframe>' % w
                    for w in widths)}).encode()
            else:
                with open(os.path.join(core.WEB, name), 'rb') as f:
                    body = f.read()
            handler.send_response(200)                  # framed here only: the real page refuses to be framed
            handler.send_header('Content-Type', 'text/html; charset=utf-8')
            handler.send_header('Content-Length', str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
        with mock.patch.object(server.Handler, '_page', page):
            dom = self.dom('?frames=1', budget=20000)
        m = re.search(r'<pre id="result">(.*?)</pre>', dom, re.S)
        if not m or not m.group(1).strip():
            self.fail('the frames did not finish')
        got = json.loads(htmllib.unescape(m.group(1)))
        self.assertNotIn('error', got)
        self.assertEqual(got['widths'], list(widths))           # each frame is laid out at its own width
        self.assertEqual(got['looks'], 2 * len(widths))
        return got['found']

    def test_a_long_email_wraps_in_a_narrow_window(self):
        """At the phone widths nothing runs past the edge (the 640 px rule); at 641 and 720 px it did."""
        found = self.look(LONG, self.PHONES + self.NARROW)
        self.assertEqual([x for x in found if int(x.split(' ')[0]) in self.PHONES], [])
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
