import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A one-year token or an API key kept as an account, on the page: a token kept for an account is said on that
# account's own row (one row, not two); one with a name of its own, and an API key, is a row of its own with what it
# is and what it can't do in words, the day it was kept, its rule, and no bar and no dash; the top line names the
# token Claude Code is on. Made-up rows on the demo's made-up accounts.
# (its page tests start headless Chrome: run the file under the Chrome lock)
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import demo
from test_app import CHROME, ChromePage

WORK = 'maya@northwind.example'


def made_up(active=False):
    now = time.time()
    return [{'id': 'token-0001', 'kind': 'token', 'for': WORK, 'for_id': 'demo-account-1', 'name': WORK, 'label': None,
             'alias': None,
             'added_at': now - 86400, 'last4': 'aaaa', 'rule': 'out', 'active': False},
            {'id': 'token-0002', 'kind': 'token', 'for': None, 'name': 'build-runner', 'label': 'build-runner',
             'alias': None, 'added_at': now, 'last4': 'bbbb', 'rule': 'last', 'active': active},
            {'id': 'key-0003', 'kind': 'key', 'for': None, 'name': 'console-ci', 'label': 'console-ci', 'alias': None,
             'added_at': now, 'last4': 'cccc', 'rule': 'out', 'active': False}]


READ = r"""
const rows = all('#acc .arow').map((r) => ({who: (r.querySelector('.em') || {}).textContent, sub: (r.querySelector('.sub') || {}).textContent,
  token: r.dataset.token || null, bars: r.querySelectorAll('.bar').length, dash: r.querySelectorAll('.dash').length,
  inuse: !!r.querySelector('.inuse'), wide: r.scrollWidth <= r.clientWidth + 1}));
done({rows: rows, top: $('#h-live').textContent, width: document.documentElement.scrollWidth <= window.innerWidth + 1});
"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheRows(ChromePage):
    def test_one_row_for_an_account_with_a_token_and_a_row_of_its_own_for_the_others(self):
        demo._ready()
        demo.FACTS['tokens'] = made_up()
        got = self.run_page(READ)
        rows = got['rows']
        self.assertEqual([r['who'] for r in rows].count(WORK), 1)
        work = next(r for r in rows if r['who'] == WORK)
        day = time.strftime('%b ', time.localtime(time.time() - 86400)) + str(time.localtime(time.time() - 86400).tm_mday)
        self.assertIn('Also a one-year token, added ' + day, work['sub'])
        own = [r for r in rows if r['token']]
        self.assertEqual([r['who'] for r in own], ['build-runner', 'console-ci'])
        today = time.strftime('%b ') + str(time.localtime().tm_mday)
        self.assertEqual(own[0]['sub'], 'One-year token · model requests only; its limits are not known · added %s · '
                                        'Used last' % today)
        self.assertEqual(own[1]['sub'], 'API key · billed per token by Anthropic · added %s · Kept out of '
                                        'auto-switch' % today)
        for r in own:
            self.assertEqual((r['bars'], r['dash'], r['inuse']), (0, 0, False))
        self.assertTrue(got['width'])

    def test_the_top_line_names_the_token_claude_code_is_on(self):
        demo._ready()
        demo.STATE['active'] = None
        demo.FACTS['tokens'] = made_up(active=True)
        got = self.run_page(READ)
        self.assertEqual(got['top'], 'Claude Code is on the one-year token for build-runner')
        self.assertTrue(next(r for r in got['rows'] if r['who'] == 'build-runner')['inuse'])

    def test_a_token_is_said_on_its_accounts_row_by_the_accounts_id(self):
        demo._ready()
        for for_id, rows_saying_it in (('demo-account-1', [WORK]), ('demo-account-99', [])):
            with self.subTest(for_id=for_id):
                tokens = made_up()
                tokens[0] = dict(tokens[0], **{'for': 'MAYA@Northwind.example (as said elsewhere)', 'for_id': for_id})
                demo.FACTS['tokens'] = tokens
                got = self.run_page(READ)
                self.assertEqual([r['who'] for r in got['rows'] if 'Also a one-year token' in (r['sub'] or '')],
                                 rows_saying_it)

    def test_with_none_kept_the_page_is_as_it_was(self):
        demo._ready()
        got = self.run_page(READ)
        self.assertEqual([r for r in got['rows'] if r['token']], [])
        self.assertNotIn('one-year token', ' '.join(r['sub'] or '' for r in got['rows']))


if __name__ == '__main__':
    unittest.main()
