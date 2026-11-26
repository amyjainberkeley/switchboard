import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page draws what the server sends, in headless Chrome (a throwaway profile, no window): each account's link and
# its proof date, Set up (on its row and in Settings › Links), Add to this profile, the links line with its one
# button, the note, the auto loop's one line, and the Check's answer. The buttons post to their own routes. The
# demo's made-up state, with these parts added by the page's own fetch. (Chrome setup is the Links group of Settings;
# the links line is on the Artifacts view and in that group.)
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from test_app import CHROME, ChromePage
from test_page_robust import STALL

PAGE = os.path.join(HERE, '..', 'switchboard', 'web', 'index.html')

PARTS = r"""
      const s0 = await base('/api/state').then((r) => r.json());
      const proof = 'Opens in the Google Chrome profile "Work", proved Nov 17.';
      // without the demo's sign-in dates (its second account's ended): each row's one short action here is its link's
      const accts = s0.accounts.map((a, i) => Object.assign({}, a, {needs_login: false, signin_ends_at: null, pages: 1, link: i === 0
        ? {link: 'proved', at: 1, line: proof, action: null,
           chrome: {state: 'missing', line: 'Claude in Chrome: the extension is not in "Work".',
                    action: {do: 'add_extension', label: 'Add to this profile', profile: 'P1', url: 'x'}}}
        : i === 1 ? {link: 'none', line: 'No Chrome profile is signed in as this account.', action: {do: 'setup', label: 'Set up', profile_name: 'Claude gmail'}, chrome: null}
        : undefined}));
      extra = {accounts: accts,
               links: Object.assign({}, s0.links, {installed: true, on: false, browser: 'Google Chrome',
                                                     banner: {text: 'Links no longer open as their owner: Google Chrome is the default browser now. Make Switchboard Links the default again.', action: 'make_default'},
                                                     holder: {name: 'Google Chrome', kind: 'browser'}, forwards: false,
                                                     handlers: 'Other apps here can also take clicked links: Link Router (~/Applications/Link Router.app).'}),
               auto: Object.assign({}, s0.auto, {enabled: true, on: true, line: 'Waiting: Claude Code is working.', stage_words: 'waiting for the next pass'}),
               check: {running: false, at: 1, line: '1 checked · 1 no profile yet', results: []}};
      await look(600);
"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheNewParts(ChromePage):
    def test_links_proofs_set_up_add_to_this_profile_the_banner_and_the_auto_line(self):
        got = self.run_page(STALL + PARTS + r"""
      const top = {notes: notes(), autoTitle: ($('#auto .auto-sw') || {}).title || ''};
      const cardSetup = all('#acc .arow [data-do="setupProfile"]').map((b) => b.dataset.email);
      const cardFoot = all('#acc .arow [data-do="setupProfile"]').map((x) => x.title);   // one button, the reason on hover
      location.hash = '#artifacts'; await wait(500);
      const line = {text: ($('#art-needs') || {}).textContent || '', banner: !!$('#art-needs [data-k="links:default"]')};
      location.hash = '#settings'; await wait(700);
      const steps = all('#sec-settings .pairs .pv').map((x) => (x.title || '') + ' | ' + x.textContent)
        .concat(all('#sec-settings [data-do="addExtension"]').map((x) => (x.title || '') + ' | Add to this profile'));
      const add = $('#sec-settings [data-do="addExtension"]');
      const groupSetup = all('#sec-settings [data-do="setupProfile"]').length;
      const checkLine = ($('[data-k="check-line"]') || {}).textContent || '';
      line.settings = ($('#g-links .needs') || {}).textContent || '';
      if (add) add.click();
      await wait(300);
      const card = $('#acc .arow [data-do="setupProfile"]');
      if (card) card.click();
      await wait(300);
      $('[data-k="links:default"]').click();
      await wait(300);
      const handlers = ($('[data-k="handlers"]') || {}).textContent || '';     // under Chrome setup, still open
      done({top, line, cardSetup, cardFoot, steps, groupSetup, checkLine, handlers,
            posted: sent.filter((x) => ['/api/extension', '/api/setup-profile', '/api/links'].includes(x[0]))});
""", budget=6000)
        self.assertNotIn('error', got, got.get('error'))
        # the links line, in the words of what is true (a browser holds the seat), and its one button
        for text in (got['line']['text'], got['line']['settings']):
            self.assertIn('Google Chrome is the default browser, so links you click in other apps open there, not as their owner.', text)
            self.assertIn('Make Switchboard Links the default', text)
        self.assertTrue(got['line']['banner'])
        self.assertIn('Waiting: Claude Code is working.', got['top']['notes'])
        self.assertEqual(got['top']['autoTitle'], 'Now: waiting for the next pass')
        self.assertEqual(len(got['cardSetup']), 1)                               # the account with no profile
        self.assertTrue(any(x.startswith('No Chrome profile is signed in as this account') for x in got['cardFoot']))
        self.assertTrue(any(x.startswith('Opens in the Google Chrome profile "Work", proved Nov 17.') for x in got['steps']), got['steps'])
        self.assertTrue(any('the extension is not in "Work"' in x and 'Add to this profile' in x for x in got['steps']))
        self.assertTrue(any('No Chrome profile is signed in as this account.' in x and 'Set up' in x for x in got['steps']))
        self.assertEqual(got['groupSetup'], 1)
        self.assertEqual(got['checkLine'], '1 checked · 1 no profile yet')
        self.assertIn('Link Router', got['handlers'])
        posted = [p for p, _ in got['posted']]
        self.assertEqual(posted, ['/api/extension', '/api/setup-profile', '/api/links'])
        self.assertEqual(got['posted'][0][1], {'profile': 'P1'})
        self.assertEqual(got['posted'][1][1], {'email': got['cardSetup'][0]})
        self.assertEqual(got['posted'][2][1], {'on': True})


class TheWords(unittest.TestCase):
    def test_the_page_posts_to_the_new_routes_and_waits_for_check_in_the_state(self):
        with open(PAGE, encoding='utf-8') as f:
            html = f.read()
        self.assertIn("post('/api/setup-profile', {email})", html)
        self.assertIn("post('/api/extension', {profile})", html)
        self.assertIn("post('/api/check')", html)
        self.assertNotIn("(d.results || []).filter((r) => r.result", html)   # the answer comes in the state, not the reply


if __name__ == '__main__':
    unittest.main()
