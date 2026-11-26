import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Every surface names an account by its whole email, never by a short name: the page (History, ⌘K, the Artifacts
# folders, an account's menu, Chrome setup) and the notifications. A short name (an alias) is only something to type,
# in ⌘K and in commands, and an older #artifacts/<short word> address still lands in its folder. A folder's name is
# never cut: it breaks after its @, at phone width too.
# Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_page_whole_emails.py'
# (its page tests start headless Chrome: run the file under the Chrome lock)
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import demo, notify
from test_app import CHROME, ChromePage
from test_page_links_and_logo import FramedPage

WORK, MAIL, FERN, HARBOR, IVY = ('maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                                 'm.reyes@harbor.example', 'maya@ivy.example')
EMAILS = [WORK, MAIL, FERN, HARBOR, IVY]
LONG = 'a.very.long.name.for.someone@a-long-company-domain.example'
ALIASES = ('work', 'personal', 'fernhill', 'harbor', 'ivy')       # the demo's, each only something to type


class NotificationsNameTheEmail(unittest.TestCase):
    def test_an_account_with_or_without_an_alias(self):
        st = {'accounts': {WORK: {'alias': 'work', 'slot': 1}, MAIL: {'alias': None, 'slot': 2}}}
        self.assertEqual(notify.account_label(st, WORK), WORK)              # not its alias
        self.assertEqual(notify.account_label(st, MAIL), MAIL)              # not "Account 2"
        self.assertEqual(notify.account_label(st, None), 'An account')

    def test_a_switch_and_why_it_happened(self):
        accounts = {WORK: {'alias': 'work', 'slot': 1, 'usage': {'weekly': {'pct': 96}}},
                    MAIL: {'alias': 'personal', 'slot': 2, 'usage': {}}}
        old, new = {'active': WORK, 'accounts': accounts}, {'active': MAIL, 'accounts': accounts}
        (note,) = [n for n in notify.from_states(old, new, line=95) if n.key.startswith('switch:')]
        self.assertEqual(note.title, 'Switched to %s' % MAIL)
        self.assertEqual(note.body, 'From %s. %s had used 96%% of its weekly limit.' % (WORK, WORK))

    def test_a_long_email_still_fits_a_switch_note(self):
        long = 'a.very.long.name.for.someone@a-long-company-domain.example'
        accounts = {long: {'slot': 1, 'usage': {'weekly': {'pct': 99}}}, MAIL: {'slot': 2, 'usage': {}}}
        (note,) = [n for n in notify.from_states({'active': long, 'accounts': accounts},
                                                 {'active': MAIL, 'accounts': accounts}, line=95)
                   if n.key.startswith('switch:')]
        self.assertIn(long, note.body)
        self.assertLessEqual(len(note.body), 240)                           # the server cuts a body past 240


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ThePageNamesTheEmail(ChromePage):
    def test_history_an_accounts_menu_chrome_setup_and_cmd_k(self):
        demo._ready()
        # names only here: without the demo's sign-in dates (personal's ended, fernhill's near), each account has one
        # ⌘K item, and personal is one to switch to
        for e in EMAILS:
            demo.STATE['accounts'][e].update(signin_ends_at=None, needs_login=False, status='ok')
        x = dict(demo.STATE['artifacts'][0], id='demo%012d' % 99, url='https://claude.ai/artifact/demo%012d0000' % 99,
                 title='Work notes')
        demo.STATE['artifacts'].append(x)               # its title begins with an alias: the account still comes first
        got = self.run_page("""
      const c = (q) => { const el = document.querySelector(q); if (el) el.click(); return !!el; };
      await wait(600);
      location.hash = '#activity'; await wait(900);
      const log = all('#history-log .ev[data-kind="switch"] .w').map((w) => w.textContent);
      location.hash = '#accounts'; await wait(600);
      c('[data-do="menu"][data-email="%(fern)s"]'); await wait(300);
      const menu = all('#menu [role="menuitem"]').map((b) => [...b.children].map((s) => s.textContent));
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); await wait(200);
      location.hash = '#settings'; await wait(900);
      const pairs = all('.pairs .pk').map((x) => x.textContent);
      location.hash = '#accounts'; await wait(600);
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'k', metaKey: true, bubbles: true})); await wait(200);
      const q = $('#pal-q'), type = async (v) => { q.value = v; q.dispatchEvent(new Event('input', {bubbles: true})); await wait(200); };
      await type('@');
      const names = all('#pal-list .pal-it').filter((x) => /^(Use |In use: |Sign in again: )/.test(x.querySelector('.lb').textContent))
        .map((x) => x.querySelector('.lb').textContent.replace(/^(Use |In use: |Sign in again: )/, ''));
      const top = async (v) => { await type(v); return [$('.pal-it[aria-selected="true"] .lb').textContent, all('#pal-list .pal-it .lb').map((x) => x.textContent)]; };
      const work = await top('work'), pers = await top('pers'), whole = await top('personal');
      q.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true})); await wait(1500);
      done({log, menu, pairs, names, work, pers, whole, switched: sent.filter((x) => x[0] === '/api/switch').map((x) => x[1].email)});
""" % {'fern': FERN})
        self.assertTrue(got['log'])
        for line in got['log']:                                             # "from → to", both whole emails
            with self.subTest(line=line):
                left, _, right = line.partition(' → ')
                self.assertIn(left, EMAILS)
                self.assertIn(right, EMAILS)
        menu = dict((m[0], m[1] if len(m) > 1 else '') for m in got['menu'])
        self.assertEqual(menu['Open a terminal as %s' % FERN], 'switchboard run %s' % FERN)
        self.assertEqual(menu['Use'], 'switchboard use %s' % FERN)
        self.assertEqual(menu['Remove…'], 'switchboard remove %s' % FERN)
        self.assertEqual(menu['Short name…'], 'switchboard alias %s WORD' % FERN)   # still there, to type with
        self.assertTrue(got['pairs'])
        self.assertTrue(set(got['pairs']) <= set(EMAILS), got['pairs'])
        self.assertEqual(sorted(got['names']), sorted(EMAILS))              # ⌘K names each account by its email
        # an alias still finds its account, first: typed whole ("work" is in no part of its email) or begun
        self.assertEqual(got['work'][0], 'In use: %s' % WORK)
        self.assertIn('Work notes', got['work'][1])                          # the other match, ranked under it
        self.assertEqual(got['pers'][0], 'Use %s' % MAIL)
        self.assertEqual(got['whole'][0], 'Use %s' % MAIL)
        self.assertEqual(got['switched'], [MAIL])                            # and Enter moves Claude Code there

    def test_the_artifacts_folders_and_an_older_address(self):
        got = self.run_page("""
      await wait(600);
      location.hash = '#artifacts'; await wait(1500);
      const folders = all('#files-list a.folder').map((a) => [a.querySelector('.fname').textContent, a.getAttribute('href')]);
      location.hash = '#artifacts/work'; await wait(900);
      const old = $('#h-art').textContent;
      location.hash = '#artifacts/fernhill'; await wait(900);
      const domain = $('#h-art').textContent;
      location.hash = '#artifacts/m.reyes'; await wait(900);
      const local = $('#h-art').textContent;
      location.hash = '#artifacts/' + encodeURIComponent('MAYA@Fernhill.example'); await wait(900);
      const upper = $('#h-art').textContent;
      location.hash = '#artifacts/maya-fernhill.example'; await wait(900);
      const dash = $('#h-art').textContent;
      location.hash = '#artifacts/no-such-folder'; await wait(900);
      const none = $('#h-art').textContent;
      location.hash = '#artifacts'; await wait(900);
      document.querySelector('#files-list a.folder').click(); await wait(900);
      done({folders, old, domain, local, upper, dash, none, where: location.hash, tiles: all('#files-list .tile .meta, #files-list .tmeta').map((x) => x.textContent)});
""", budget=20000)
        names = [f[0] for f in got['folders']]
        self.assertEqual(names[:-1], [e for e in EMAILS if e in names])     # slot order, each by its email
        self.assertEqual(names[-1], 'Not matched yet')
        for name, href in got['folders'][:-1]:
            self.assertEqual(href, '#artifacts/' + name.replace('@', '%40'))
        self.assertEqual(got['old'], 'Artifacts›%s' % WORK)                  # an alias address lands in its folder
        self.assertEqual(got['domain'], 'Artifacts›%s' % FERN)               # so does its domain's word
        self.assertEqual(got['local'], 'Artifacts›%s' % HARBOR)              # and the part before the @
        self.assertEqual(got['upper'], 'Artifacts›%s' % FERN)                # an email in any case
        self.assertEqual(got['dash'], 'Artifacts›%s' % FERN)                 # and the old - for its @
        self.assertEqual(got['none'], 'Artifacts')                           # an address that names nothing: the list
        self.assertEqual(got['where'], '#artifacts/' + names[0].replace('@', '%40'))


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class FolderNamesStayWhole(FramedPage):
    def test_a_folder_name_breaks_after_its_at_and_is_never_cut(self):
        demo._ready()
        demo.STATE['artifacts'].append(dict(demo.STATE['artifacts'][0], id='demo%012d' % 98, title='Old notes',
                                            url='https://claude.ai/artifact/demo%012d0000' % 98, owner=LONG))
        got = self.in_frames(r"""
      const out = [];
      for (const f of document.querySelectorAll('iframe')) {
        const d = f.contentDocument;
        f.contentWindow.location.hash = '#artifacts'; await wait(1500);
        const lines = (el) => { const rows = []; let top = null;            // each line's text, by where it sits
          for (const n of el.childNodes) { if (n.nodeType !== 3) continue;
            for (let i = 0; i < n.data.length; i++) { const r = d.createRange(); r.setStart(n, i); r.setEnd(n, i + 1);
              const b = r.getClientRects()[0]; if (!b) continue; const t = Math.round(b.top);
              if (top === null || Math.abs(t - top) > 4) { rows.push(''); top = t; } rows[rows.length - 1] += n.data[i]; } }
          return rows; };
        out.push({width: f.clientWidth, overflow: d.documentElement.scrollWidth - d.documentElement.clientWidth,
                  names: [...d.querySelectorAll('#files-list .fname')].map((el) => ({text: el.textContent, lines: lines(el),
                    hidden: el.scrollHeight > el.clientHeight + 1 || el.getBoundingClientRect().right > el.closest('li').getBoundingClientRect().right + 1}))});
      }
      done(out);""", widths=(390, 1440), budget=30000)
        self.assertEqual([f['width'] for f in got], [390, 1440])
        for frame in got:
            with self.subTest(width=frame['width']):
                self.assertEqual(frame['overflow'], 0)                       # a long name never widens the page
                names = dict((x['text'], x) for x in frame['names'])
                self.assertEqual(sorted(names), sorted(EMAILS + [LONG, 'Not matched yet']))
                for text, x in names.items():
                    self.assertEqual(''.join(x['lines']), text)               # whole, never cut
                    self.assertFalse(x['hidden'], text)
                for email in EMAILS:                                         # a short part before the @: it breaks there
                    lines = names[email]['lines']
                    self.assertTrue(len(lines) == 1 or (len(lines) == 2 and lines[0].endswith('@')), lines)
                self.assertGreater(len(names[LONG]['lines']), 2)              # a long one takes the lines it needs


if __name__ == '__main__':
    unittest.main()
