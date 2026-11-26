import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Two accounts with one email (one person in two organizations): every command that takes or prints an account works
# on each, the same way with the page running or not, and every JSON document tells the two apart. Made-up accounts
# only; the engine is a stand-in (tests/fakes_accounts_api.py), and so is the running page.
import io
import json
import os
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import __main__ as cli
from switchboard import autocheck, core, places
from switchboard.accounts import api, policy
from fakes_autoswitch import account
from test_autoswitch_policy import NOW, reading, snap
from test_term import Page, Sandbox

TWIN, OTHER = 'maya@twin.example', 'bo@other.example'
NORTH, OWN = 'maya@twin.example (Northwind)', 'maya@twin.example (Personal)'
H = 3600.0


class Twins(Sandbox):
    """Three accounts: the twin in Northwind (1, in use), the twin's own organization (2), and another (3)."""

    def setUp(self):
        super().setUp()
        self.north = self.fake.hold(TWIN, 'org-n', org_name='Northwind')
        self.own = self.fake.hold(TWIN, 'org-p', org_name="maya@twin.example's Organization")
        self.other = self.fake.hold(OTHER, 'org-o', org_name='Other Co')
        self.fake.sign_in(TWIN, 'org-n')
        now = time.time()
        for aid, five, week in ((self.north, 95, 30), (self.own, 10, 10), (self.other, 99, 10)):
            self.fake.reading(aid, five=(five, now + 3 * H), week=(week, now + 4 * 24 * H), at=now - 30)

    def run_both(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except core.SwitchError as e:
                code = 'refused: %s' % e
        return code, out.getvalue(), err.getvalue()

    def page(self, answers=None):
        page = Page(cli.local_view(), answers=answers or {})
        self.addCleanup(page.stop)
        os.environ['SWITCHBOARD_PORT'] = str(page.server_address[1])
        return page


class TheBestSwitch(Twins):
    def test_it_moves_to_the_twin_without_the_page(self):
        code, out, err = self.run_both('switch', '--best')
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.fake.live().account_id, self.own)
        self.assertTrue(out.startswith('Claude Code is on %s now: ' % OWN), out)

    def test_it_sends_the_pages_name_to_the_running_page(self):
        page = self.page({'/api/switch': {'ok': True}})
        code, out, _ = self.run_both('switch', '--best')
        self.assertEqual(code, 0, out)
        self.assertEqual([p[:2] for p in page.posts], [('/api/switch', {'email': OWN})])


class TheSwitchDocument(Twins):
    def test_a_move_between_the_two_is_a_move_with_both_numbers(self):
        code, out, _ = self.run_both('switch', '2', '--json')
        self.assertEqual(code, 0, out)
        doc = json.loads(out)
        self.assertEqual((doc['moved'], doc['from'], doc['to'], doc['in_use']), (True, TWIN, TWIN, TWIN))
        self.assertEqual((doc['from_number'], doc['to_number']), (1, 2))
        self.assertEqual(self.fake.live().account_id, self.own)

    def test_no_move_has_no_to_number(self):
        code, out, _ = self.run_both('switch', '1', '--json')
        doc = json.loads(out)
        self.assertEqual((doc['moved'], doc['from_number'], doc['to_number']), (False, 1, None))


class Places(Twins):
    def test_move_and_swap_without_the_page_print_the_two_apart(self):
        code, out, _ = self.run_both('move', '2', '1')
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines()[0], 'Moved %s from 2 to 1.' % OWN)
        self.assertEqual(out.splitlines()[1:], ['  1  %s' % OWN, '  2  %s' % NORTH, '  3  %s' % OTHER])
        code, out, _ = self.run_both('swap', '1', '2')
        self.assertEqual(out.splitlines()[0], 'Swapped %s (1 to 2) and %s (2 to 1).' % (OWN, NORTH))

    def test_move_and_swap_send_the_pages_name_to_the_running_page(self):
        page = self.page({'/api/move': {}})
        self.assertEqual(self.run_both('move', '2', '3')[0], 0)
        self.assertEqual(self.run_both('swap', '1', '2')[0], 0)
        self.assertEqual([p[:2] for p in page.posts], [('/api/move', {'email': OWN, 'to': 3}),
                                                       ('/api/move', {'email': NORTH, 'to': 2}),
                                                       ('/api/move', {'email': OWN, 'to': 1})])

    def test_the_list_names_the_two_apart(self):
        self.assertEqual(places.listing(self.fake.accounts()), ['  1  %s' % NORTH, '  2  %s' % OWN,
                                                                '  3  %s' % OTHER])


class RulesLinesRanksAndFolders(Twins):
    def test_auto_line_and_rank_name_the_twin_and_history_says_it(self):
        code, out, _ = self.run_both('auto', 'line', '2', '--week', '90')
        self.assertEqual((code, out), (0, '%s now has its own line: 90%% weekly.\n' % OWN))
        code, out, _ = self.run_both('auto', 'rank', '1', '2')
        self.assertEqual(out, '%s is rank 2 now: auto-switch lands there before any account with a later rank.\n'
                         % NORTH)
        said = [e['text'] for e in core.load()['events']]
        self.assertIn('%s now has its own line: 90%% weekly.' % OWN, said)
        self.assertIn('%s is rank 2 now: auto-switch lands there before any account with a later rank.' % NORTH,
                      said)

    def test_auto_says_each_twins_rule_apart(self):
        self.assertEqual(self.run_both('auto', 'last', '1')[0], 0)
        self.assertEqual(self.run_both('auto', 'out', '2')[0], 0)
        code, out, _ = self.run_both('auto')
        self.assertIn('%s is used last.' % NORTH, out)
        self.assertIn('%s is kept out of auto-switch.' % OWN, out)

    def test_folder_name_says_which_twin_and_folders_json_tells_them_apart(self):
        here = os.path.join(self.tmp, 'work')
        os.makedirs(here)
        old = os.getcwd()
        os.chdir(here)
        self.addCleanup(os.chdir, old)
        code, out, _ = self.run_both('folder', '2')
        self.assertEqual(code, 0, out)
        self.assertIn('runs as %s,' % OWN, out)
        code, out, _ = self.run_both('folder')
        self.assertIn('This folder runs as %s' % OWN, out)
        code, out, _ = self.run_both('folders', '--json')
        row = json.loads(out)['folders'][0]
        self.assertEqual((row['account'], row['organization']), (TWIN, 'Personal'))


class TheCheckDocument(unittest.TestCase):
    ACCTS = (account('a', 1, email=TWIN), account('b', 2, email=TWIN), account('c', 3))

    def setUp(self):
        import dataclasses
        self.accts = (dataclasses.replace(self.ACCTS[0], org_name='Northwind'),
                      dataclasses.replace(self.ACCTS[1], org_name="maya@twin.example's Organization"), self.ACCTS[2])

    def test_each_account_has_its_number_and_organization_and_the_words_tell_them_apart(self):
        s = snap({'a': reading('a', five=95.0, week=30.0), 'b': reading('b', five=10.0, week=20.0),
                  'c': reading('c', five=10.0, week=20.0, week_reset=NOW + 6 * 24 * H)}, accounts=self.accts,
                 settings=policy.Settings(enabled=True, rules=(('c', 'out'),)))
        code, doc = autocheck.judge(autocheck.parse(['why']), s, s.settings)
        self.assertEqual((doc['from'], doc['to'], doc['from_number'], doc['to_number']), (TWIN, TWIN, 1, 2))
        self.assertEqual((doc['in_use']['number'], doc['in_use']['organization']), (1, 'Northwind'))
        self.assertEqual([(c['email'], c['number'], c['organization']) for c in doc['candidates']],
                         [(TWIN, 2, 'Personal'), ('c@acct.example', 3, None)])
        self.assertTrue(doc['said'].startswith('Would switch Claude Code to %s: %s 5-hour' % (OWN, NORTH)), doc['said'])
        self.assertIn('%s is the one it picks' % OWN, ' '.join(doc['reasons']))
        self.assertTrue(doc['reasons'][0].startswith('In use: %s,' % NORTH), doc['reasons'][0])

    def test_with_every_email_different_the_words_are_as_before(self):
        s = snap({'a': reading('a', five=95.0, week=30.0), 'b': reading('b', five=10.0, week=20.0)})
        code, doc = autocheck.judge(autocheck.parse(['why']), s, s.settings)
        self.assertEqual((doc['from'], doc['to']), ('a@acct.example', 'b@acct.example'))
        self.assertNotIn('(', doc['said'].split(':')[0])


class TheEnginesName(unittest.TestCase):
    def test_a_policy_sentence_names_the_twin(self):
        accts = (account('a', 1, email=TWIN), account('b', 2, email=TWIN))
        import dataclasses
        accts = (dataclasses.replace(accts[0], org_name='Northwind'), dataclasses.replace(accts[1], org_name='Ivy'))
        s = snap({'a': reading('a', five=95.0, week=30.0), 'b': reading('b', five=10.0, week=20.0)}, accounts=accts)
        d = policy.decide(s)
        self.assertEqual((d.action, d.target), ('switch', 'b'))
        self.assertIn('%s (Ivy)' % TWIN, d.reason)
        self.assertIn('%s (Northwind)' % TWIN, d.reason)
        self.assertIsInstance(d, api.Decision)


if __name__ == '__main__':
    unittest.main()
