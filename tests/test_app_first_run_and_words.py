import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Notifications, the History wording and the page at phone widths, and hygiene checks on the docs and the tree.
#
# Fakes only: every settings file is a throwaway one under a temp HOME and CLAUDE_CONFIG_DIR, and the managed settings
# folder is a temp one too. No notification is shown and nothing runs claude.
import html as htmllib
import io
import json
import os
import re
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import core, history, keep, notify_hooks as nh, server
import test_app  # its headless-Chrome helper
import test_notify_hooks  # its throwaway Claude Code folder and managed-settings folder

A = 'maya@northwind.example'


def held(email):
    """A row of the engine's list (engine.listing): an account it keeps."""
    return {'email': email, 'org_id': None, 'unmanaged': False, 'status': 'ok'}


# ---------- 1. notifications on by themselves, once, at the first run ----------

class NotifyByItself(test_notify_hooks.Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        keep.reset()
        self.addCleanup(keep.reset)

    def ours(self):
        return self.read().count(nh.MARK) if os.path.exists(self.path) else 0

    def lines(self):
        try:
            with open(history.path(), encoding='utf-8') as f:
                return [json.loads(x) for x in f if x.strip()]
        except OSError:
            return []

    def test_a_fresh_install_turns_them_on_once_and_the_history_says_so(self):
        self.assertTrue(keep.notify_by_itself(now=self.now))
        self.assertEqual(self.ours(), 3)                                     # UserPromptSubmit, Stop, Notification
        self.assertEqual(nh.status(), dict(nh.status(), **{'in': True, 'works': True, 'off_by': None}))
        s = core.settings()
        self.assertEqual(s.get('notify_by_itself'), self.now)
        self.assertNotIn('notify', s)                                         # Switchboard's own notes: on, as before
        self.assertNotIn('notify_set_by_you', s)
        self.assertIs(server.mac_facts()['notify'], True)                     # the page's switch shows on
        last = self.lines()[-1]
        self.assertEqual((last['kind'], last['text'], last['by'], last['reason']),
                         ('setting', 'Notifications: on', 'auto', 'first run'))
        before, n = self.read(), len(self.lines())
        self.assertFalse(keep.notify_by_itself())                            # once
        self.assertEqual((self.read(), len(self.lines())), (before, n))

    def test_once_even_when_the_hooks_were_taken_out_by_hand(self):
        self.assertTrue(keep.notify_by_itself())
        os.remove(self.path)
        self.assertFalse(keep.notify_by_itself())
        self.assertFalse(os.path.exists(self.path))

    def test_never_once_you_set_it(self):
        os.makedirs(self.data, exist_ok=True)
        for mine in ({'notify_set_by_you': True}, {'notify': False}, {'notify': True}):   # the last two: from before
            with self.subTest(mine):
                with open(os.path.join(self.data, 'settings.json'), 'w') as f:
                    json.dump(mine, f)
                self.assertFalse(keep.notify_by_itself())
                self.assertFalse(os.path.exists(self.path))
                self.assertNotIn('notify_by_itself', core.settings())

    def test_never_when_claude_code_runs_no_hooks_or_its_settings_cant_be_read(self):
        for text in ('{\n  "disableAllHooks": true\n}\n', '{"model": "opus",}\n', '[]\n'):
            with self.subTest(text):
                self.write(text)
                self.assertFalse(keep.notify_by_itself())
                self.assertEqual(self.read(), text)
        os.remove(self.path)
        for managed in ({'allowManagedHooksOnly': True}, {'disableAllHooks': True}):
            with self.subTest(managed):
                with open(os.path.join(self.managed, 'managed-settings.json'), 'w') as f:
                    json.dump(managed, f)
                self.assertFalse(keep.notify_by_itself())
                self.assertFalse(os.path.exists(self.path))
        self.assertEqual((core.settings().get('notify_by_itself'), self.lines()), (None, []))

    def test_not_without_claude_codes_own_folder(self):
        os.rmdir(self.claude)
        self.assertFalse(keep.notify_by_itself())
        self.assertFalse(os.path.exists(self.claude))                        # nothing is made for it

    def test_when_they_cant_go_in_nothing_changes_and_nothing_is_said(self):
        self.patch(nh, 'verify', lambda cmd, timeout=30: "The notification hooks didn't run here (no answer).")
        self.assertFalse(keep.notify_by_itself())
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual((core.settings().get('notify_by_itself'), self.lines()), (None, []))
        self.assertIs(server.mac_facts()['notify'], False)

    def test_not_in_the_demo(self):
        core.DEMO = True
        self.assertFalse(keep.notify_by_itself())
        self.assertFalse(os.path.exists(self.path))

    def test_a_note_that_cannot_be_written_never_stops_the_first_run(self):
        real = core.set_setting

        def set_setting(key, value):
            if key == 'notify_by_itself':
                raise OSError(28, 'No space left on device')
            return real(key, value)
        self.patch(core, 'set_setting', set_setting)
        self.assertTrue(keep.notify_by_itself())                             # in, and said by the switch
        self.assertEqual(self.ours(), 3)

    def test_the_switch_and_switchboard_notify_make_it_yours(self):
        self.patch(server, 'NOTIFY_RUN', self.run_ok)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(['notify', 'off']), 0)
        self.assertIs(core.settings().get('notify_set_by_you'), True)
        self.assertFalse(keep.notify_by_itself())                            # off stays off
        self.assertFalse(os.path.exists(self.path))
        core.set_setting('notify_set_by_you', None)
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            json.dump({}, f)
        server.act('/api/notify', {'on': True})                              # the page's switch
        self.assertIs(core.settings().get('notify_set_by_you'), True)
        self.assertNotIn('notify_by_itself', core.settings())



class TheUninstaller(test_notify_hooks.SwitchBase):
    def test_an_install_again_puts_them_back_at_its_first_run(self):
        keep.reset()
        self.assertTrue(keep.notify_by_itself())
        out = io.StringIO()
        with redirect_stdout(out):
            cli.tidy()
        self.assertIn('Notification hooks: taken out.', out.getvalue().splitlines())
        self.assertFalse(os.path.exists(self.path))                          # Switchboard made the file: it goes
        self.assertTrue(keep.notify_by_itself())                             # the next install's first run
        self.assertEqual(self.read().count(nh.MARK.encode()), 3)

    def test_once_you_set_them_an_install_again_leaves_them_to_you(self):
        server.act('/api/notify', {'on': False})
        with redirect_stdout(io.StringIO()):
            cli.tidy()
        self.assertFalse(keep.notify_by_itself())
        self.assertFalse(os.path.exists(self.path))


class PageFirstRun(test_notify_hooks.Base):
    """The page's first run ends when its tick finds an account held after a look that found none."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        keep.reset()
        self.addCleanup(keep.reset)
        for name in ('look', 'auto_by_itself'):                                # the tick's other steps: not these tests'
            self.patch(keep, name, lambda *a, **k: None)

    def test_from_none_to_one_account_turns_them_on_once(self):
        server.keep_tick({'rows': []})
        self.assertFalse(os.path.exists(self.path))
        server.keep_tick({'rows': [held(A)]})
        self.assertEqual(self.read().count(nh.MARK), 3)
        self.assertIs(server.mac_facts()['notify'], True)
        os.remove(self.path)                                                   # taken out by hand, then a first run again
        server.keep_tick({'rows': []})
        server.keep_tick({'rows': [held(A)]})
        self.assertFalse(os.path.exists(self.path))

    def test_a_page_that_starts_with_accounts_turns_nothing_on(self):
        for _ in range(3):
            server.keep_tick({'rows': [held(A)]})
        self.assertFalse(os.path.exists(self.path))

    def test_a_login_kept_on_this_tick_ends_it(self):
        self.patch(keep, 'look', lambda *a, **k: A)
        server.keep_tick({'rows': []})                        # the first look: none held, one kept now
        self.assertFalse(os.path.exists(self.path))                           # the page saw none before: not yet
        keep.reset()
        self.assertFalse(keep.first_run_tick([]))
        self.assertTrue(keep.first_run_tick([], kept=A))
        self.assertEqual(self.read().count(nh.MARK), 3)

    def test_not_while_the_engine_did_not_answer(self):
        server.keep_tick({'rows': []})
        server.keep_tick({'rows': None})
        self.assertFalse(os.path.exists(self.path))
        server.keep_tick({'rows': [held(A)]})
        self.assertTrue(os.path.exists(self.path))




# ---------- 2. History everywhere: the page's words in the terminal, the help and the README ----------

ROOT = os.path.join(HERE, '..')
OLD = 'where it ' + 'went'                          # spelled apart, so this file is not a place that says it


DOCS = ('README.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'SECURITY.md', os.path.join('packaging', 'README.md'),
        os.path.join('.github', 'PULL_REQUEST_TEMPLATE.md'), os.path.join('.github', 'ISSUE_TEMPLATE', 'bug.yml'),
        os.path.join('.github', 'ISSUE_TEMPLATE', 'config.yml'))


def doc_files():
    """Every doc a person reads (the README, docs/ and the files beside them), relative to ROOT. Not
    CODE_OF_CONDUCT.md: it is the Contributor Covenant's own text."""
    names = list(DOCS) + sorted(os.path.join('docs', n) for n in os.listdir(os.path.join(ROOT, 'docs')) if n.endswith('.md'))
    return [n for n in names if os.path.isfile(os.path.join(ROOT, n))]


def product_files():
    """Every file of the product a person reads or runs: the program, the page, the scripts, the docs."""
    out = [os.path.join(ROOT, n) for n in ('install.sh', 'uninstall.sh') + tuple(doc_files())]
    for folder, _, names in os.walk(os.path.join(ROOT, 'switchboard')):
        out += [os.path.join(folder, n) for n in names if n.endswith(('.py', '.html', '.json'))]
    return out


def read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


class HistoryEverywhere(unittest.TestCase):
    def test_no_product_file_calls_history_where_it_went_or_the_usage_page(self):
        said = []
        for path in product_files():
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read().lower()
            if OLD in text or 'usage page' in text.replace("anthropic's usage page", ''):
                said.append(os.path.relpath(path, ROOT))
        self.assertEqual(said, [])

    def test_switchboard_usage_reads_as_the_page_does(self):
        from switchboard import usage
        page = {'days': 7, 'moved': None, 'cuts': {
            'work': [{'label': 'Website', 'share': 73, 'note': 'Mostly Opus.'}, {'label': 'Notes', 'share': 27, 'note': 'All chat.'}],
            'who': [{'label': 'Workflows', 'share': 81, 'note': ''}], 'model': [], 'kind': []}}
        out = io.StringIO()
        usage.print_page(page, out)
        self.assertEqual(out.getvalue().splitlines()[:5], ['Work, last 7 days (shares of weighted tokens)',
                                                           '   73%  Website  Mostly Opus.', '   27%  Notes    All chat.',
                                                           '', 'Who'])

    def test_the_help_and_the_docs_say_history(self):
        help_text = cli.__doc__                     # its indent is the compiler's to keep (3.13 and on strip it)
        self.assertRegex(help_text, r'switchboard usage \[--days N\] \[--json\]\n +Work, last 7 days, as History on '
                                    r'the page shows it\n')
        doc = read(os.path.join('docs', 'how-it-works.md'))     # what is on the page, told there
        page = doc[doc.index('## The page'):doc.index('## The Mac app')]
        self.assertIn('  - History: what happened.', page)                    # the week's work is under the accounts
        self.assertIn('then the week by project, by what agents did and by model.', page)
        readme = read('README.md')
        self.assertIn('History keeps no prompt, chat text, login or key.', readme)      # and the README calls it History




# ---------- 3. the page at phone widths ----------

LONG = 'maya.reyes-longname@northwind-industries.example'          # made up, as long as real ones get
LONG2 = 'alexandra.konstantinopoulou@fernhillstudiocollective.example'

# The page in one frame per width: headless Chrome keeps its own window at least 500 px wide, and a frame is laid out
# at its own width (media queries included). Each look checks every frame, light and dark; the result goes in #result
# (the empty <main> is what test_app's dom() waits for).
FRAMES = r"""<!doctype html><meta charset="utf-8"><body style="margin:0"><main hidden></main>%(frames)s<pre id="result"></pre><script>
(async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms)), out = {found: [], widths: [], looks: 0};
  const frames = [...document.querySelectorAll('iframe')], doc = (f) => f.contentDocument;
  try {
    for (let i = 0; i < 300 && !frames.every((f) => doc(f) && doc(f).querySelector('main[data-drawn]')); i++) await wait(50);
    await Promise.all(frames.map((f) => doc(f).fonts.ready));
    await wait(1500);                                           // the artifacts, History and Work arrive
    const look = (what) => {
      for (const f of frames) {
        for (const theme of ['light', 'dark']) {
          const root = doc(f).documentElement;
          root.dataset.theme = theme;
          out.looks++;
          if (root.scrollWidth > f.contentWindow.innerWidth) out.found.push(f.contentWindow.innerWidth + ' px, ' + theme + ', ' + what + ': ' + root.scrollWidth);
          for (const b of doc(f).querySelectorAll('.fr-add')) {          // a button's words stay inside it
            if (b.scrollHeight > b.clientHeight + 1) out.found.push(f.contentWindow.innerWidth + ' px, ' + theme + ', ' + what + ': the Add button spills');
          }
        }
        delete doc(f).documentElement.dataset.theme;
      }
    };
    const click = async (q, ms) => { for (const f of frames) { const b = doc(f).querySelector(q); if (b) b.click(); } await wait(ms); };
    out.widths = frames.map((f) => f.contentWindow.innerWidth);
    look('the page');
    if (%(more)s) {                                            // each view, and an account's own menu
      const go = async (hash, ms) => { for (const f of frames) f.contentWindow.location.hash = hash; await wait(ms); };
      await go('#settings', 1500);
      look('Settings');
      await go('#accounts', 700);
      await click('[data-do="menu"]', 600);
      out.reached = frames.map((f) => !!doc(f).querySelector('#menu:not([hidden]) [data-do="terminal"]'));
      look("an account's own menu");
      await click('#menu [data-do="alias"]', 600);
      await go('#artifacts/work', 1200);
      look('a folder of artifacts');
    }
  } catch (e) { out.error = String(e && e.stack || e); }
  document.getElementById('result').textContent = JSON.stringify(out);
})();
</script>"""


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class PhoneWidths(test_app.ChromePage):
    """Nothing runs past the right edge at 320, 375, 390 or 430 px, light or dark (demo page, made-up accounts with the
    longest values the page meets: an email in use that long, 92.5% lines, a sign-in waiting, a problem line)."""
    WIDTHS = (320, 375, 390, 430)
    maxDiff = None

    def setUp(self):
        super().setUp()
        from switchboard import demo
        demo._ready()
        self.state, self.facts = demo.STATE, demo.FACTS

    def frames(self, more):
        def page(handler, name):
            if 'frames' in handler.path:
                body = (FRAMES % {'more': 'true' if more else 'false', 'frames': ''.join(
                    '<iframe src="/?w=%d" style="display:block;width:%dpx;height:900px;border:0"></iframe>' % (w, w)
                    for w in self.WIDTHS)}).encode()
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
        self.assertEqual(got['widths'], list(self.WIDTHS))       # each frame is laid out at its own width
        return got

    def test_the_board_and_more_settings_with_long_values(self):
        a = dict(self.state['accounts']['maya@northwind.example'], email=LONG, slot=6, alias='',
                 org='Northwind Industries International')
        self.state['accounts'][LONG] = a
        self.state['order'].append(LONG)
        self.state['active'] = LONG
        self.state['error'] = 'The account engine did not answer: %s timed out' % LONG2
        self.facts['auto'].update(session_at=92.5, weekly_at=97.5)
        self.facts['connecting'] = [{'key': 'k1', 'email': LONG2, 'name': LONG2, 'profile_name': 'New profile',
                                     'started': 0, 'default_browser': True}]
        got = self.frames(more=True)
        self.assertEqual(got['found'], [])
        self.assertEqual((got['looks'], got['reached']), (4 * 2 * 4, [True] * 4))   # every look, every frame

    def test_the_first_run_with_a_long_email(self):
        for x in self.state['accounts'].values():
            x['connected'] = False
        self.state['accounts'][LONG2] = dict(self.state['accounts']['maya@northwind.example'], email=LONG2, slot=None,
                                             connected=True, unmanaged=True)
        self.state['order'].append(LONG2)
        self.state['active'] = LONG2
        self.facts.update(setup_hidden=False)
        got = self.frames(more=False)
        self.assertEqual(got['found'], [])


# ---------- 4. "run helpers on Sonnet", told in the docs ----------
# (docs/how-it-works.md, "Sonnet for helpers")

class TheSonnetSwitch(unittest.TestCase):
    def test_the_switch_is_told(self):
        doc = read(os.path.join('docs', 'how-it-works.md'))
        part = doc[doc.index('### Sonnet for helpers'):doc.index('### House rules')]
        self.assertIn('so helpers and workflow steps that name no model run on Sonnet', part)


# ---------- 5. few words: the House rules, Code map and claude-mem rows under More settings ----------

@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class PackRowsInFewWords(test_app.ChromePage):
    """What the three rows say on the demo page: their names, and only the facts their controls cannot show."""

    def test_the_rows_say_only_what_their_controls_cannot(self):
        # Each setting has its title, its key and one line, and nothing more
        got = self.run_page("""
      location.hash = '#settings'; await wait(1300);
      const row = (pack) => $('[data-do="pack"][data-pack="' + pack + '"]').closest('.row');
      const words = (r) => [r.querySelector('.r-title').textContent, r.querySelector('.r-desc').textContent];
      const before = {house: words(row('house')), codemap: words(row('codemap')), mem: words(row('claudemem')),
                      houseCtl: row('house').querySelector('.r-ctl').textContent};
      $('[data-do="houseView"]').click(); await wait(400);
      const view = row('house').querySelector('.r-extra');
      done(Object.assign(before, {houseHint: view.querySelector('.hint').textContent, hasText: !!view.querySelector('pre').textContent,
                                  hints: all('#row-claudemem .hint').map((x) => x.textContent)}));
""")
        self.assertEqual(got['house'], ['House rules', 'A short block of working rules at the top of CLAUDE.md.'])
        self.assertEqual(got['houseCtl'], 'View')
        self.assertEqual(got['codemap'], ['Code map', 'Claude Code asks graphify before it searches files.'])
        self.assertEqual(got['mem'], ['claude-mem', 'Notes from your chats, for Claude Code to look up. It pauses past 10% of your use.'])
        self.assertEqual(got['houseHint'], 'At the top of ~/.claude/CLAUDE.md.')     # the lines themselves are shown
        self.assertTrue(got['hasText'])
        self.assertIn('Used 4.2% of this week.', got['hints'])                      # the measured share: kept

    def test_the_other_words_those_rows_can_carry(self):
        with open(os.path.join(ROOT, 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            page = f.read()
        rows = page[page.index("{id: 'code', title: 'In Claude Code'"):page.index("{id: 'links', title: 'Links'")]
        for gone in ('Off takes out exactly these lines', ' lines at the top of ', 'turning it off is left to you',
                     'with a weekly cap', 'Installs codegraph, then', 'is installed but turned off'):
            self.assertNotIn(gone, rows)
        for kept in ('Edited by you, so Switchboard leaves it in.', "codegraph’s own section already does this.",
                     'Turned off in Claude Code.'):
            self.assertIn(kept, rows)

    def test_a_stopped_install_says_only_that(self):
        from switchboard.packs import install
        with mock.patch.object(install, 'record', lambda pack: {'state': 'installing', 'version': '15.18.3'}), \
                mock.patch.object(install, 'job_view', lambda pack: None), \
                mock.patch.object(install, 'busy', lambda pack: False):
            self.assertEqual(install.view('claude-mem', False)['line'], 'Stopped part-way.')   # Repair is beside it


# ---------- 6. nothing of the owner's in the tree, but the licence's author line ----------

EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}')
MADE_UP = re.compile(r'@(?:[A-Za-z0-9-]+\.)*(?:example|test|invalid|localhost|local)$|'      # RFC 2606 / 6761 names
                     r'@(?:[A-Za-z0-9-]+\.)*example\.(?:com|net|org)$', re.I)


def own_files():
    """(path from the top, text) of each tracked text file of Switchboard's own (git ls-files; a walk outside a
    checkout)."""
    import subprocess
    try:
        names = subprocess.run(['git', '-C', ROOT, 'ls-files', '-z'], capture_output=True, check=True,
                               timeout=30).stdout.decode('utf-8').split('\0')
    except (OSError, subprocess.SubprocessError):
        names = []
    if not [n for n in names if n]:
        names = []
        for folder, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', '.venv', '.pytest_cache')]
            names += [os.path.relpath(os.path.join(folder, n), ROOT) for n in files if n != '.git']
    out = []
    for name in (n for n in names if n):
        try:
            with open(os.path.join(ROOT, name), 'rb') as f:
                data = f.read()
        except OSError:
            continue
        if b'\0' not in data:                                   # text only (the screenshot and the icon have none)
            out.append((name, data.decode('utf-8', 'replace')))
    return out


class NothingOfTheOwners(unittest.TestCase):
    def test_the_owner_is_named_in_the_licence_and_nowhere_else(self):
        with open(os.path.join(ROOT, 'LICENSE'), encoding='utf-8') as f:
            author = re.search(r'Copyright \(c\) \d{4} (.+)', f.read()).group(1)
        words = [w.lower() for w in re.findall(r'[A-Za-z]{3,}', author)]
        self.assertTrue(words)
        found = [(name, w) for name, text in own_files() if name != 'LICENSE'
                 for w in words if re.search(r'\b%s\b' % re.escape(w), text.lower())]
        self.assertEqual(found, [])

    def test_every_address_is_a_made_up_one(self):
        found = sorted(set((name, m) for name, text in own_files() for m in EMAIL.findall(text)
                           if not MADE_UP.search(m)))
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
