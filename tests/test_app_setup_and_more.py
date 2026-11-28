import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Setup and the install lines, the demo with no engine, the account menu's actions and More's settings, and the
# first run in the page.
#
# The engine is tests/fakes_accounts_api.py, in accounts.api's place in this process (the engine's own suites test the
# engine). Every folder is a throwaway one; nothing here touches a real login.
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)

from switchboard import core, demo, engine, more, server
from switchboard.accounts import api
from fakes_accounts_api import install
import test_app  # its headless-Chrome helper; it has no tests of its own

A, B = 'ana@work.example', 'ana@home.example'


def write_exe(path, text):
    with open(path, 'w') as f:
        f.write(text)
    os.chmod(path, 0o755)


class EngineSandbox(unittest.TestCase):
    """A throwaway home and data folder, and the engine's stand-in in accounts.api's place."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-release-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        os.makedirs(os.path.join(self.home, '.claude'))
        os.makedirs(os.path.join(self.tmp, 'bin'))
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude')})
        env.start()
        self.addCleanup(env.stop)
        # The page's questions are this test's own: a remove another test asked about (server.ASKED keeps a question
        # 10 minutes) must not count as the yes to this test's first remove.
        asked = mock.patch.dict(server.ASKED, clear=True)
        asked.start()
        self.addCleanup(asked.stop)
        os.environ.pop('SWITCHBOARD_PORT', None)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.fake = install(self)
        host = engine.AutoHost()
        for target, name, value in ((engine, 'HOST', host), (server.artifacts, 'refresh', lambda: None),
                                    (server, 'tell', lambda *a, **k: None),
                                    (engine, 'foreign_switcher', lambda folder=None: None)):
            q = mock.patch.object(target, name, value)    # the history scan and notifications have their own tests
            q.start()
            self.addCleanup(q.stop)
        self.addCleanup(host.stop, 5)

    def changes(self):
        """The engine calls that change something, in order."""
        return [c for c in self.fake.calls if c[0] in ('switch', 'add_current', 'remove', 'set_alias', 'reorder',
                                                       'set_auto', 'run_as')]


# ---------- the first step, and how Switchboard is installed ----------

# Only the projects the docs credit (CREDITED, docs/credits.md), the one they cite (CITED) and the
# placeholder: any other GitHub owner in a file is a mistake.
CREDITED = ('astral-sh', 'colbymchenry', 'thedotmack', 'Graphify-Labs', 'multica-ai', 'DietrichGebert',
            'addyosmani', 'garrytan', 'affaan-m', 'Textualize', 'sethmlarson',
            'vercel',     # Geist, the page's typeface: its license (switchboard/web/Geist-OFL.txt) names its home
            'ronaldoussoren',     # PyObjC, which the menu bar item draws with
            'oven-sh',            # Bun, which the claude-mem switch downloads when the Mac has none
            'lucide-icons',       # the page's icons (switchboard/web/Lucide-LICENSE.txt)
            'shadcn-ui')          # the page's colours follow its zinc set
CITED = ('mozilla',)              # CODE_OF_CONDUCT.md is the Contributor Covenant 2.1 word for word, and its own
                                  # attribution names Mozilla's enforcement ladder
# The docs link the repository's own pages (issues, the private report form, the troubleshooting page), each
# with the same placeholder in the same address. Publishing replaces OWNER in install.sh's REPO line and in these files.
PLACEHOLDER_DOCS = ('README.md', 'SECURITY.md', 'CODE_OF_CONDUCT.md', 'docs/troubleshooting.md',
                    '.github/ISSUE_TEMPLATE/config.yml')


class SetUpAndInstall(EngineSandbox):
    def test_the_first_step_is_done_for_a_new_person(self):
        f = dict(server.facts(), connecting=[])
        self.assertTrue(f['engine']['installed'])                       # the engine is part of the program
        self.assertTrue(server.render(core.load(), f)['setup']['engine'])

    def test_no_package_manager_in_the_product_path(self):
        words = re.compile(r'uv tool|brew install|pipx|uv pip|install uv', re.I)
        sources = []
        for top in ('switchboard', 'router'):
            for d, dirs, names in os.walk(os.path.join(ROOT, top)):
                dirs[:] = sorted(x for x in dirs if x not in ('build', '__pycache__'))
                sources += [os.path.join(d, n) for n in sorted(names)
                            if n.endswith(('.py', '.swift', '.sh', '.plist', '.md', '.html'))]
        self.assertTrue(any(p.endswith('SwitchboardLinks.swift') for p in sources) and any(p.endswith('build.sh') for p in sources))
        for path in sources + [os.path.join(ROOT, 'install.sh'), os.path.join(ROOT, 'uninstall.sh')]:
            name = os.path.relpath(path, ROOT)
            with open(path, encoding='utf-8') as f:
                for n, line in enumerate(f, 1):
                    with self.subTest(file=name, line=n):
                        self.assertNotRegex(line, words)

    def test_the_github_address_is_one_marked_placeholder_in_one_place(self):
        import subprocess as sp
        files = sp.run(['git', '-C', ROOT, 'ls-files'], capture_output=True, text=True).stdout.split()
        found = []
        for name in files:
            try:
                with open(os.path.join(ROOT, name), encoding='utf-8') as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError):
                continue
            found += ['%s:%d' % (name, n) for n, line in enumerate(text.splitlines(), 1) if 'amyjainberkeley/switchboard' in line
                      and not name.startswith('tests/')]
            if not name.startswith('tests/'):
                for url in re.findall(r'github(?:usercontent)?\.com/([\w.-]+/[\w.-]+)', text):
                    if name == 'README.md' and url == 'user-attachments/assets':
                        continue        # the README's film: GitHub's own place for uploaded files, not an owner
                    self.assertIn(url.split('/')[0], CREDITED + CITED + ('$REPO', 'amyjainberkeley'), (name, url))
                    if url.split('/')[0] == 'amyjainberkeley':
                        self.assertEqual(url, 'amyjainberkeley/switchboard', name)        # one placeholder, one address
        with open(os.path.join(ROOT, 'install.sh'), encoding='utf-8') as f:
            repo_line = next(n for n, l in enumerate(f.read().splitlines(), 1) if l.startswith('REPO='))
        with open(os.path.join(ROOT, 'README.md'), encoding='utf-8') as f:
            lines = f.read().splitlines()
        readme = [n for n, l in enumerate(lines, 1) if 'amyjainberkeley/switchboard' in l and 'install.sh' in l]
        # install.sh's REPO line, and the README's two lines that show its install command (the same placeholder in
        # the same address): the install line, and the block to paste into Claude Code
        self.assertEqual(len(readme), 2)
        for n in readme:
            self.assertIn('curl -fsSL https://raw.githubusercontent.com/amyjainberkeley/switchboard/main/install.sh | sh', lines[n - 1])
        self.assertEqual([x for x in found if x.startswith('install.sh:')], ['install.sh:%d' % repo_line])
        # every other place is a link in the docs to the repository's own pages: the list publishing works through
        self.assertEqual(sorted(set(x.rsplit(':', 1)[0] for x in found) - {'install.sh'}), sorted(PLACEHOLDER_DOCS))

    # Homebrew: the README shows the cask's install line twice (the install line and the block to paste into Claude
    # Code), with the same placeholder owner the GitHub address has; publishing replaces it there too. The cask itself
    # is in the owner's tap; what it runs from here is packaging/homebrew (tests/install-check.sh case 19).
    def test_the_homebrew_line_is_the_first_install_line(self):
        with open(os.path.join(ROOT, 'README.md'), encoding='utf-8') as f:
            lines = f.read().splitlines()
        brew = [n for n, l in enumerate(lines) if 'brew install' in l]
        self.assertEqual(len(brew), 2)
        self.assertEqual(lines[brew[0]], 'brew install amyjainberkeley/apps/switchboard')
        self.assertTrue(lines[brew[1]].endswith(': brew install amyjainberkeley/apps/switchboard'), lines[brew[1]])
        curl = next(n for n, l in enumerate(lines) if l.startswith('curl -fsSL '))
        self.assertLess(brew[0], curl)
        for name in ('launcher', 'uninstall.sh'):
            path = os.path.join(ROOT, 'packaging', 'homebrew', name)
            with self.subTest(file=name):
                self.assertTrue(os.access(path, os.X_OK))
                with open(path, encoding='utf-8') as f:
                    self.assertTrue(f.readline().startswith('#!/bin/sh'))


# ---------- the demo needs no engine ----------

class DemoWithoutAnEngine(EngineSandbox):
    def test_the_demo_draws_and_answers_without_the_engine(self):
        core.DEMO = True
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        with mock.patch.object(api, 'accounts', side_effect=AssertionError('the demo asked the engine')):
            v = server.view()
            self.assertEqual(len(v['accounts']), 5)
            self.assertTrue(v['setup']['engine'])
            self.assertNotIn('repair', v)
            for path in ('/api/remove', '/api/purge', '/api/terminal'):           # the demo only says it is the demo
                self.assertEqual(server.act(path, {}), {'demo': True})
            server.act('/api/switch', {'email': 'maya@ivy.example'})
            self.assertEqual(server.view()['active'], 'maya@ivy.example')
        self.assertEqual(self.changes(), [])

    def test_a_demo_left_running_for_a_day_still_has_numbers_and_a_next_account(self):
        # A demo left running for four hours: every reading was over an hour old, so the page showed a
        # dash for every account and "All nearly out".
        core.DEMO = True
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)
        old = 'maya.reyes@mail.example'          # its reading is a day old on purpose: the row that shows a dash
        first = server.view()
        self.assertEqual([a['email'] for a in first['accounts'] if not a['trusted']], [old])
        later = time.time() + 26 * 3600          # past every made-up 5-hour reset and ivy's weekly one
        with mock.patch('time.time', return_value=later):
            v = server.view()
        self.assertEqual([a['email'] for a in v['accounts'] if not a['trusted']], [old])
        self.assertEqual(v['plan']['next'], first['plan']['next'])
        self.assertIsNotNone(v['plan']['next'])
        for a in v['accounts']:
            u = a['usage']
            # no 5-hour window (no 5-hour limit), or one not started (no reset): nothing to move on
            for w in [x for x in [u['session'], u['weekly']] + list(u['scoped']) if x and x['resets']]:
                self.assertGreater(core.parse_iso(w['resets']), later, a['name'])


# ---------- what one account's line and the More area do ----------

class MoreActions(EngineSandbox):
    def setUp(self):
        super().setUp()
        self.a = self.fake.hold(A, 'org-work')
        self.b = self.fake.hold(B)
        self.fake.sign_in(B)
        server.tick()                                   # A is first, B second; Claude Code is on B
        self.fake.calls[:] = []

    def refused(self, path, body):
        with self.assertRaises(core.SwitchError) as cm:
            server.act(path, body)
        return str(cm.exception)

    def test_remove_says_what_it_changes_first_and_only_then_takes_the_yes(self):
        # A yes the server never asked for is not taken: it says what removing changes instead, naming the email.
        d = server.act('/api/remove', {'email': A, 'confirm': True})
        self.assertEqual((d['state'], d['line']), ('confirm', 'Remove %s from Switchboard? Its saved login is deleted '
                                                               'here. Claude Code and the browser are not touched.' % A))
        self.assertEqual(self.changes(), [])
        server.act('/api/remove', {'email': A, 'confirm': True})
        self.assertEqual(self.changes(), [('remove', self.a)])
        self.assertIn('stays on it until you switch', server.act('/api/remove', {'email': B})['line'])  # the one in use
        self.fake.fail['remove'] = api.AccountsError('claimed', 'A terminal is using ana@home.example. Close it first.')
        self.assertEqual(self.refused('/api/remove', {'email': B, 'confirm': True}),
                         'A terminal is using ana@home.example. Close it first.')
        self.assertEqual(server.act('/api/remove', {'email': B, 'confirm': True})['state'], 'confirm')   # a yes is used once

    def test_a_terminal_as_the_account(self):
        opened = os.path.join(self.tmp, 'opened.txt')
        opener = os.path.join(self.tmp, 'bin', 'fake-open')
        write_exe(opener, '#!/bin/sh\necho "$@" > %s\n' % opened)
        command = os.path.join(self.data, 'bin', 'switchboard')
        os.makedirs(os.path.dirname(command), exist_ok=True)
        write_exe(command, '#!/bin/sh\nexit 0\n')
        with mock.patch.object(more, 'OPEN', [opener]):
            server.act('/api/terminal', {'email': B})
        deadline = time.time() + 5
        while not os.path.exists(opened) and time.time() < deadline:
            time.sleep(0.05)
        with open(opened) as f:
            path = f.read().strip()
        self.assertEqual(path, os.path.join(self.data, 'terminal', 'account-%s.command' % self.b))
        with open(path) as f:
            script = f.read()
        self.assertIn("exec '%s' run '%s'\n" % (command, self.b), script)   # Claude Code as that account, there only
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o700)

    def test_a_terminal_without_the_command_says_so(self):
        self.assertIn('Run the installer again', self.refused('/api/terminal', {'email': B}))

    def test_reorder(self):
        server.act('/api/move', {'email': B, 'to': 1})
        self.assertEqual(self.changes(), [('reorder', [self.b, self.a])])
        for body in ({'email': B, 'to': 0}, {'email': B, 'to': '1'}, {'email': B, 'to': 3}, {'email': B, 'to': True}):
            with self.subTest(body=body):
                self.refused('/api/move', body)
        self.assertEqual(len(self.changes()), 1)

    def test_remove_everything_needs_the_typed_words_and_stops_automatic_switching_first(self):
        self.assertEqual(self.refused('/api/purge', {'typed': 'yes'}), 'Type "remove everything" to confirm.')
        self.assertEqual(self.changes(), [])
        self.fake.auto['enabled'] = True
        with mock.patch.object(engine.HOST, 'ensure'):
            server.act('/api/purge', {'typed': 'Remove everything'})
        self.assertEqual(self.changes(), [('set_auto', {'enabled': False}), ('remove', self.a), ('remove', self.b)])
        self.assertIs(self.fake.auto['enabled'], False)
        self.assertEqual(engine.saved_identities(), set())

    def test_a_remove_the_engine_refuses_stops_there_and_says_which(self):
        self.fake.fail['remove'] = api.AccountsError('keychain_locked', "macOS's Keychain is locked.")
        with mock.patch.object(engine.HOST, 'ensure'):
            said = self.refused('/api/purge', {'typed': 'remove everything'})
        self.assertEqual(said, "%s was not removed: macOS's Keychain is locked." % A)

    def test_the_more_area_says_the_engines_state(self):
        d = server.more_view()
        self.assertEqual(d, {'status': ['Switchboard %s keeps 2 accounts.' % core.VERSION]})


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class MoreInThePage(test_app.ChromePage):
    def test_one_accounts_own_settings_and_its_remove_confirm(self):
        # An account's own actions are its row's menu (each with its command); a short name and removing
        # it each ask under the row
        v = self.run_page("""
      const c = (q) => { const el = document.querySelector(q); if (el) el.click(); return !!el; };
      await wait(300);
      c('[data-do="menu"][data-email="maya@northwind.example"]'); await wait(300);
      const got = {list: document.querySelectorAll('#acc .arow').length, text: $('#menu').textContent, open: !$('#menu').hidden,
                   items: all('#menu [role="menuitem"]').map((b) => [...b.children].map((s) => s.textContent))};
      c('#menu [data-do="alias"]'); await wait(400);
      const x = document.querySelector('#acc .arow-x[data-own="alias"]');
      got.alias = x.querySelector('input[data-k^="alias:"]').value;
      got.own = x.textContent;
      c('[data-do="menu"][data-email="maya@northwind.example"]'); await wait(300);
      c('#menu [data-do="remove"]'); await wait(500);
      got.ask = document.querySelector('#acc .arow-x[data-own="remove"]').textContent;
      done(got);
""")
        self.assertEqual(v['list'], 5)                                  # nothing new on the accounts themselves
        self.assertTrue(v['open'])
        items = dict((m[0], m[1] if len(m) > 1 else '') for m in v['items'])     # each item: its words, its command
        self.assertEqual(items['Short name…'], 'switchboard alias maya@northwind.example WORD')
        self.assertEqual(items['Open a terminal as maya@northwind.example'], 'switchboard run maya@northwind.example')
        self.assertEqual(items['Remove…'], 'switchboard remove maya@northwind.example')
        self.assertEqual(v['alias'], 'work')                           # its short name, to type; the page edits it
        self.assertIn('switchboard use work', v['own'])
        self.assertIn('Remove maya@northwind.example from Switchboard? Its saved login is deleted here.', v['ask'])

    def test_more_settings_opens_in_place_and_each_item_opens_under_itself(self):
        # Settings is a view of its own (#settings), its groups by what they are for; a chip shows one group
        # at a time, All shows them again
        v = self.run_page("""
      const c = (q) => { const el = document.querySelector(q); if (el) el.click(); return !!el; };
      const groups = () => all('#set-list .group').map((g) => g.id.slice(2));
      const hidden = $('#sec-settings').hidden;
      location.hash = '#settings'; await wait(1200);
      const shown = !$('#sec-settings').hidden, all0 = groups(), chips = all('#chips .chip').map((b) => [b.textContent, b.getAttribute('aria-pressed')]);
      const rows = all('#g-switching .r-title').map((s) => s.textContent);
      const subs = [$('#gh-accounts').textContent, $('#g-accounts .g-note').textContent].concat(all('#g-accounts .r-title').map((s) => s.textContent));
      c('#chips [data-g="accounts"]'); await wait(300);
      const open = groups();
      c('#chips [data-g="keys"]'); await wait(300);
      const open2 = groups();
      c('#chips [data-g=""]'); await wait(300);
      location.hash = '#activity'; await wait(900);
      const log = $('#history-log').textContent;
      done({hidden, shown, all0, chips, rows, subs, open, open2, log});
""")
        # Opening links as their owner is always on, so it has no switch, and no group of its own but Links
        self.assertTrue(v['hidden'])
        self.assertTrue(v['shown'])
        self.assertEqual(v['all0'], ['switching', 'working', 'code', 'links', 'keys', 'accounts'])
        self.assertEqual(v['chips'], [[n, 'true' if n == 'All' else 'false'] for n in
                                      ('All', 'Switching', 'While Claude works', 'In Claude Code', 'Links', 'Keys', 'Accounts')])
        self.assertEqual(v['rows'], ['Auto-switch', 'Switch at', 'Count a model', 'Order', 'Each account'])
        self.assertEqual(v['subs'], ['Accounts', 'Switchboard %s keeps 5 accounts.' % core.VERSION, 'Remove everything'])
        self.assertEqual(v['open'], ['accounts'])                        # one group at a time
        self.assertEqual(v['open2'], ['keys'])
        self.assertIn('m.reyes@harbor.example \u2192 maya@northwind.example', v['log'])   # the switch history, by email

    LINKS_ROWS = """
      location.hash = '#settings'; await wait(1200);
      const g = $('#g-links');
      const got = {main: all('#g-working .r-title, #g-code .r-title').map((r) => r.textContent).slice(0, 5),
                   replaces: $('#row-statusline .r-ctl').textContent,
                   more: all('#g-code .r-title').map((r) => r.textContent),
                   switches: all('#sec-settings .sw').map((b) => b.getAttribute('aria-label')),
                   items: all('#set-list .group').map((x) => x.id.slice(2))};
      const pick = g.querySelector('select[data-change="home"]');
      Object.assign(got, {subs: all('#sec-settings .g-head h2').map((x) => x.textContent),
                          rows: [...g.querySelectorAll('.r-title')].map((x) => x.textContent),
                          home: pick ? Object.assign({}, pick.dataset) : null,
                          bm: (g.querySelector('a.bm') || {}).textContent || null,
                          needs: (g.querySelector('[data-k="links-needs"]') || {}).textContent || null,
                          code: (g.querySelector('[data-k="links-needs"] code') || {}).textContent || null});
      if (pick) { pick.value = pick.options[1].value; pick.dispatchEvent(new Event('change', {bubbles: true})); await wait(900); }
      Object.assign(got, {sent: sent.filter((x) => x[0] === '/api/links').map((x) => x[1]), said: $('#toasts').textContent});
      done(got);
"""
    FOUR = ['Keep Mac awake', 'Notifications', 'Claude in Chrome', 'Status line', 'Models by the job']

    def test_links_have_no_switch_and_their_rows_are_under_chrome_setup(self):
        # Opening links as their owner is always on: no switch for it anywhere on the page. Where other links open
        # and the bookmark are under Chrome setup, and work there.
        v = self.run_page(self.LINKS_ROWS)
        self.assertEqual(v['main'], self.FOUR)
        self.assertIn('Replaces yours', v['replaces'])                    # the demo's own status line is there
        self.assertEqual(v['more'], ['Status line', 'Models by the job', 'Sonnet for helpers', 'House rules',
                                     'Code map', 'claude-mem'])
        self.assertFalse(any('Open links as their owner' in r for r in v['more']), v['more'])
        self.assertNotIn('Open links as their owner', v['switches'])
        self.assertEqual(v['items'], ['switching', 'working', 'code', 'links', 'keys', 'accounts'])
        self.assertIn('Links', v['subs'])
        self.assertEqual(v['rows'], ['Chrome profiles', 'Other links', 'Bookmarklet'])
        self.assertEqual(v['home']['zone'], 'set')
        self.assertEqual(v['bm'], 'Open as owner')
        self.assertIsNone(v['needs'])                                    # the demo's link app is there
        self.assertEqual(len(v['sent']), 1)                              # where other links open: one POST
        self.assertEqual(list(v['sent'][0]), ['home'])
        self.assertIn('Demo: nothing changed on this Mac.', v['said'])   # said in a toast

    def test_without_the_link_app_chrome_setup_says_how_to_add_it(self):
        demo._ready()
        demo.FACTS['links'].update(installed=False, on=False)
        v = self.run_page(self.LINKS_ROWS)
        self.assertEqual(v['main'], self.FOUR)
        self.assertNotIn('Open links as their owner', v['switches'])
        # the command works however it was installed (a one-line install has no install.sh to run), and the line says
        # what it needs
        self.assertEqual(v['needs'], 'Needs the link app: switchboard update --link-app (it needs Apple’s command line tools)')
        self.assertEqual(v['code'], 'switchboard update --link-app')
        self.assertIsNone(v['home'])                                     # nothing to pick without the link app
        self.assertEqual(v['bm'], 'Open as owner')                       # the bookmark works without it
        self.assertEqual(v['sent'], [])


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class FirstRunInThePage(test_app.ChromePage):
    def main(self, path=''):
        dom = self.dom(path)
        return dom[dom.find('<main'):dom.find('</main>')]

    def test_the_first_run_is_the_login_and_one_button(self):
        demo._ready()
        for e, a in demo.STATE['accounts'].items():
            a['connected'] = e == 'maya@northwind.example'
        demo.STATE['accounts']['maya@northwind.example']['unmanaged'] = True
        demo.FACTS.update(setup_hidden=False)
        main = self.main()
        self.assertIn('<h1 class="fr-title" id="h-first">Claude Code is on <span class="on">maya@northwind.example</span></h1>', main)
        self.assertRegex(main, r'<button[^>]*data-do="addCurrent"[^>]*>Add maya@northwind.example</button>')
        for gone in ('id="acc"', 'id="settings"', 'id="mq"', 'id="history"', 'Auto-switch'):
            self.assertNotIn(gone, main)

    def test_another_switcher_is_said_above_it(self):
        demo._ready()
        demo.FACTS['guard'] = "Another program is also switching Claude Code's login (com.example.x). Turn it off first."
        main = self.main()
        self.assertRegex(main, r'<div class="nl repair" role="status"><svg[^>]*>.*?</svg><span class="tx say">Another program is '
                               r"also switching Claude Code's login \(com\.example\.x\)\. Turn it off first\.</span></div>")
        self.assertLess(main.find('class="nl repair"'), main.find('id="acc"'))


if __name__ == '__main__':
    unittest.main()
