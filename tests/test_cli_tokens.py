"""`switchboard add --token`, and a one-year token or an API key kept as an account in the terminal, the page's API
and `status --json`: by prompt and by standard input, refused shapes, the words of its row, its rule, a switch onto it
and back, removal, and nothing secret anywhere. The engine is the real one on a made-up Mac (fakes_accounts.Rig:
the suite's stand-in Keychain and Anthropic); the page is not running."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from fakes_accounts import Rig
from switchboard import __main__ as cli
from switchboard import bearers, core, server, term
from switchboard.accounts import bearer, context, live, roster
from switchboard.core import SwitchError

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0101-aaaaaaaaaaaa'
KEY = 'sk-ant-api03-madeUpConsoleKey-0202-bbbbbbbbbbbbbb'


class Case(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.rig.two_accounts()                 # Claude Code is on b@example.com
        self.use = context.use(self.rig.ctx)
        self.use.__enter__()
        roster.forget_live()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def run_cli(self, *argv, stdin='', tty=False, secret=None, answers=()):
        out, err = io.StringIO(), io.StringIO()
        feed = io.StringIO(stdin)
        feed.isatty = lambda: tty
        said = iter(answers)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(sys, 'stdin', feed), \
                mock.patch('getpass.getpass', side_effect=lambda prompt='': secret), \
                mock.patch('builtins.input', side_effect=lambda prompt='': next(said)):
            try:
                code = cli.main(list(argv))
            except SwitchError as e:
                code = 'refused: %s' % e
        roster.forget_live()
        return code, out.getvalue(), err.getvalue()

    def kept(self):
        return bearer.records(self.rig.doc())

    def assert_secret_nowhere(self, *texts):
        for t in texts:
            for s in (TOKEN, KEY):
                self.assertNotIn(s, t)
        for top in (self.rig.root, core.data_dir()):
            for folder, _, names in os.walk(top):
                for name in names:
                    if name == '.credentials.json':
                        continue
                    with open(os.path.join(folder, name), 'rb') as f:
                        data = f.read()
                    for s in (TOKEN, KEY):
                        self.assertNotIn(s.encode(), data, name)


class Adding(Case):
    def test_from_standard_input_for_an_account(self):
        code, out, err = self.run_cli('add', '--token', '-', '--for', 'a@example.com', '--as', 'a-tok',
                                      stdin=TOKEN + '\n')
        self.assertEqual(code, 0, err)
        self.assertEqual(out, 'Kept a one-year token for a@example.com (ends in aaaa) in your Keychain; auto-switch '
                              'keeps it out.\n')
        rec = list(self.kept().values())[0]
        self.assertEqual((rec['for'], rec['alias']), ('uuid-a', 'a-tok'))
        code, out, _ = self.run_cli('add', '--token', '-', '--for', 'a@example.com', stdin=TOKEN + '\n')
        self.assertEqual((code, out), (0, 'That one-year token for a@example.com is kept already.\n'))
        self.assert_secret_nowhere(out, err)

    def test_by_prompt_a_key_asks_for_its_name_and_a_token_which_account(self):
        code, out, err = self.run_cli('add', '--token', tty=True, secret=KEY, answers=['ci'])
        self.assertEqual(code, 0, err)
        self.assertTrue(out.endswith('Kept an API key for ci (ends in bbbb) in your Keychain; auto-switch keeps it '
                                     'out.\n'), out)
        code, out, err = self.run_cli('add', '--token', tty=True, secret=TOKEN, answers=['1'])
        self.assertEqual(code, 0, err)
        self.assertIn('1  a@example.com\n2  b@example.com\n', out)
        self.assertIn('Kept a one-year token for a@example.com', out)
        kinds = sorted((r['kind'], r['for'], r['label']) for r in self.kept().values())
        self.assertEqual(kinds, [('key', None, 'ci'), ('token', 'uuid-a', None)])
        self.assert_secret_nowhere(out, err)

    def test_a_refused_shape_keeps_nothing_and_never_repeats_it(self):
        for text in ('sk-ant-ort01-madeUpRefresh-0303-cccccccccc', 'not a token', ''):
            code, out, err = self.run_cli('add', '--token', '-', '--label', 'x', stdin=text + '\n')
            self.assertEqual(code, 'refused: ' + bearer.REFUSED)
            self.assertNotIn('madeUp', out + err)
        self.assertEqual(self.kept(), {})

    def test_a_wrong_command_line_is_exit_2(self):
        for argv, said in ((('--token', '-', '--for', 'a@example.com', '--label', 'x'), 'not both'),
                           (('--token', '--colour'), 'Unknown word: --colour.'),
                           (('--token', '--for'), 'Unknown word: --for.'),
                           (('--token', '-'), bearer.NAME_IT)):
            code, out, err = self.run_cli('add', *argv, stdin=TOKEN + '\n')
            self.assertEqual(code, 2, argv)
            self.assertIn(said, err)
        self.assertEqual(self.kept(), {})

    def test_the_help_has_its_lines_after_add_provider(self):
        doc = cli.__doc__
        self.assertLess(doc.index('switchboard add PROVIDER'), doc.index('switchboard add --token'))
        self.assertLess(doc.index('switchboard add --token'), doc.index('switchboard signin'))
        self.assertTrue(all(len(line) <= 120 for line in doc.splitlines()))


class Shown(Case):
    def setUp(self):
        super().setUp()
        self.run_cli('add', '--token', '-', '--for', 'a@example.com', '--as', 'a-tok', stdin=TOKEN)
        self.run_cli('add', '--token', '-', '--label', 'ci', stdin=KEY)
        self.run_cli('add', '--token', '-', '--label', 'spare', stdin=TOKEN.replace('0101', '0909'))

    def test_the_rows_say_what_each_is_with_no_bar_and_no_dash(self):
        code, out, err = self.run_cli('status')
        self.assertEqual(code, 0, err)
        day = term.day(self.rig.ctx.wall())
        self.assertIn('also a one-year token, added %s' % day, out)
        self.assertEqual(out.count('a@example.com'), 1)                  # one row, not two
        self.assertIn('   spare  one-year token · model requests only; its limits are not known · added %s · kept '
                      'out of auto-switch' % day, out)
        self.assertIn('   ci  API key · billed per token by Anthropic · added %s · kept out of auto-switch' % day, out)
        for line in out.splitlines():
            if line.strip().startswith(('spare', 'ci')):
                self.assertNotIn(term.DASH, line)
                self.assertNotIn(term.USED, line)
        self.assert_secret_nowhere(out, err)

    def test_status_json_gives_every_account_a_kind(self):
        code, out, err = self.run_cli('status', '--json')
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertEqual(doc['schema'], 1)
        got = dict(((a['email'] or a['label']), a) for a in doc['accounts'])
        self.assertEqual([got[k]['kind'] for k in ('a@example.com', 'b@example.com', 'spare', 'ci')],
                         ['sign_in', 'sign_in', 'one_year', 'api_key'])
        self.assertTrue(got['a@example.com']['one_year_added_at'].endswith('Z'))
        self.assertIsNone(got['b@example.com']['one_year_added_at'])
        self.assertEqual(got['spare']['limits'], {'five_hour': {'used_percent': None, 'refills_at': None},
                                                  'week': {'used_percent': None, 'refills_at': None, 'pace': None},
                                                  'models': [], 'spend': None})
        self.assertEqual(got['ci']['limits'], {'five_hour': None, 'week': None, 'models': [], 'spend': None})
        self.assertEqual((got['ci']['email'], got['ci']['number'], got['ci']['in_use']), (None, None, False))
        self.assertTrue(got['ci']['added_at'].endswith('Z'))
        self.assertEqual(len(doc['accounts']), 4)
        self.assertNotIn('token', out)                  # the guard of test_cli_status_json holds with them too
        self.assert_secret_nowhere(out, err)

    def test_rules_a_token_last_or_out_and_a_key_never_in_or_last_by_any_path(self):
        code, out, _ = self.run_cli('auto', 'last', 'spare')
        self.assertEqual(out, bearers.RULE_SAID['last'] % 'spare' + '\n')
        self.assertEqual(bearer.rule_of(self.kept()[bearer.find_id(self.rig.doc(), 'spare')]), 'last')
        code, _, _ = self.run_cli('auto', 'in', 'spare')
        self.assertTrue(str(code).startswith('refused: '))
        for rule in ('in', 'last'):
            code, _, _ = self.run_cli('auto', rule, 'ci')
            self.assertEqual(code, 'refused: ' + bearer.refusal('key', rule))
            with self.assertRaises(SwitchError) as e:
                server.act('/api/auto', {'rule': rule, 'account': 'ci'})
            self.assertEqual(str(e.exception), bearer.refusal('key', rule))
        self.assertEqual(bearer.rule_of(self.kept()[bearer.find_id(self.rig.doc(), 'ci')]), 'out')
        server.act('/api/auto', {'rule': 'out', 'account': 'spare'})
        self.assertEqual(bearer.rule_of(self.kept()[bearer.find_id(self.rig.doc(), 'spare')]), 'out')

    def test_switch_onto_the_token_by_its_name_and_back(self):
        code, out, err = self.run_cli('switch', 'a-tok')
        self.assertEqual(code, 0, err)
        self.assertIn('Claude Code is on the one-year token for a@example.com.', out)
        self.assertIn(bearer.AFTER_SWITCH['token'], out)
        self.assertEqual(live.parse(self.rig.live_text()).access_token, TOKEN)
        code, out, err = self.run_cli('status')
        self.assertIn('Claude Code is on the one-year token for a@example.com', out)
        doc = json.loads(self.run_cli('status', '--json')[1])
        self.assertIsNone(doc['in_use'])
        self.assertFalse(any(a['in_use'] for a in doc['accounts']))      # a token kept for an account: no own row
        code, out, err = self.run_cli('switch', 'b@example.com')
        self.assertEqual(code, 0, err)
        self.assertEqual(live.parse(self.rig.live_text()).access_token is None, False)
        self.assertTrue(live.parse(self.rig.live_text()).has_refresh)
        self.assert_secret_nowhere(out, err)

    def test_switch_onto_an_api_key_says_how_to_use_it_and_exits_1(self):
        before = self.rig.live_text()
        code, out, err = self.run_cli('switch', 'ci')
        self.assertEqual(code, 1)
        self.assertEqual(err, bearer.KEY_SWITCH % 'ci' + '\n')
        self.assertEqual(self.rig.live_text(), before)

    def test_remove_by_name_and_removing_the_account_forgets_its_token(self):
        code, out, err = self.run_cli('remove', 'ci', '--yes')
        self.assertEqual((code, out), (0, 'Removed the API key for ci.\n'))
        code, out, err = self.run_cli('remove', 'a@example.com', '--yes')
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(r['label'] for r in self.kept().values()), ['spare'])


class WithTheOtherCommands(Case):
    """The commands that name an account, with a token kept for a@example.com (a-tok), a token of its own (spare) and
    an API key (ci): each takes a sign-in as before, and says plainly what it does with a token's or a key's name."""

    def setUp(self):
        super().setUp()
        self.run_cli('add', '--token', '-', '--for', 'a@example.com', '--as', 'a-tok', stdin=TOKEN)
        self.run_cli('add', '--token', '-', '--label', 'ci', stdin=KEY)
        self.run_cli('add', '--token', '-', '--label', 'spare', stdin=TOKEN.replace('0101', '0909'))

    def test_status_json_reads_every_account_the_same_way(self):
        code, _, err = self.run_cli('auto', 'line', 'a@example.com', '--five-hour', '80')
        self.assertEqual(code, 0, err)
        code, out, err = self.run_cli('status', '--json')
        self.assertEqual(code, 0, err)
        doc = json.loads(out)
        self.assertIsNone(doc['this_folder'])
        self.assertEqual(doc['in_use'], 'b@example.com')
        got = dict(((a['email'] or a['label']), a) for a in doc['accounts'])
        keys, limit_keys = set(got['b@example.com']), set(got['b@example.com']['limits'])
        for name in ('spare', 'ci'):
            self.assertEqual(set(got[name]), keys, name)
            self.assertEqual(set(got[name]['limits']), limit_keys, name)
            self.assertIs(got[name]['from_another_mac'], False)
            self.assertIsNone(got[name]['limits']['spend'])
        self.assertEqual(set(got['spare']['limits']['week']), set(got['b@example.com']['limits']['week']))
        self.assertIsNone(got['spare']['limits']['week']['pace'])
        self.assertIsNone(got['ci']['limits']['week'])
        self.assertEqual(set(got['a@example.com']) - keys, {'five_hour_line'})     # its own line still appears
        self.assertEqual(got['a@example.com']['five_hour_line'], 80)
        self.assertTrue(got['a@example.com']['one_year_added_at'].endswith('Z'))
        self.assertNotIn('token', out)
        self.assert_secret_nowhere(out, err)

    def test_add_reaches_the_token_code_only_with_token(self):
        from switchboard import places
        reached = []
        with mock.patch.object(bearers, 'add_command', lambda args: reached.append(('token', list(args))) or 0), \
                mock.patch.object(places, 'add_named', lambda args, _: reached.append(('sign-in', list(args))) or 0):
            for argv in (('add', '--token', '-', '--label', 'night', '--as', 'nt'),
                         ('add', '--as', 'work', '--at', '2'),
                         ('add', '--token', '--as', 'nt'),
                         ('add', '--as', 'nt', '--token')):
                self.assertEqual(self.run_cli(*argv)[0], 0, argv)
        self.assertEqual(reached, [('token', ['--token', '-', '--label', 'night', '--as', 'nt']),
                                   ('sign-in', ['--as', 'work', '--at', '2']),
                                   ('token', ['--token', '--as', 'nt']),
                                   ('token', ['--as', 'nt', '--token'])])
        code, out, err = self.run_cli('add', '--token', '-', '--label', 'night', '--as', 'nt',
                                      stdin=TOKEN.replace('0101', '0808'))
        self.assertEqual(code, 0, err)
        rec = self.kept()[bearer.find_id(self.rig.doc(), 'nt')]
        self.assertEqual((rec['label'], rec['alias']), ('night', 'nt'))

    def test_auto_rank_and_line_refuse_a_token_or_a_key_and_keep_nothing(self):
        from switchboard.accounts import api
        before, rules = api.auto_settings(), dict((k, bearer.rule_of(r)) for k, r in self.kept().items())
        for argv in (('rank', 'spare', '1'), ('rank', 'ci', '2'), ('rank', 'a-tok', '1'),
                     ('line', 'spare', '--five-hour', '80'), ('line', 'ci', '--week', '90'), ('line', 'a-tok', 'off')):
            code, out, _ = self.run_cli('auto', *argv)
            entry = next(x for x in bearers.rows() if argv[1] in (x['alias'], x['label']))
            self.assertEqual(code, 'refused: ' + bearer.sign_ins_only(entry, argv[1], 'auto ' + argv[0]), argv)
            self.assertEqual(out, '')
        for body in ({'account': 'spare', 'rank': 1}, {'account': 'ci', 'own_lines': {'session_at': 80}}):
            with self.assertRaises(SwitchError) as e:
                server.act('/api/auto', body)
            self.assertEqual(str(e.exception), 'No saved account matches "%s".' % body['account'])
        self.assertEqual(api.auto_settings(), before)
        self.assertEqual(dict((k, bearer.rule_of(r)) for k, r in self.kept().items()), rules)

    def test_commands_for_sign_ins_say_so_for_a_token_or_a_key(self):
        from switchboard.accounts import folders
        order = list(self.rig.doc()['order'])
        for argv, said in ((('move', 'spare', '1'), 'spare is a one-year token; switchboard move is for sign-ins.'),
                           (('swap', 'ci', 'a@example.com'), 'ci is an API key; switchboard swap is for sign-ins.'),
                           (('swap', 'a@example.com', 'a-tok'),
                            'a-tok is the one-year token for a@example.com; switchboard swap is for sign-ins.'),
                           (('folder', 'spare'), 'spare is a one-year token; switchboard folder is for sign-ins.'),
                           (('export', '-', '--plain', '--account', 'ci'),
                            'ci is an API key; switchboard export is for sign-ins.')):
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 'refused: ' + said, argv)
            self.assertEqual(out, '', argv)
        self.assertEqual(self.rig.doc()['order'], order)
        self.assertEqual(list(folders.entries()), [])
        code, out, err = self.run_cli('move', 'nobody', '1')                   # a name nothing has: as before
        self.assertEqual(code, 'refused: No saved account matches "nobody".')
        code, out, err = self.run_cli('move', 'a@example.com', '2')            # a sign-in moves as before
        self.assertEqual(code, 0, err)
        self.assertEqual(self.rig.doc()['order'], order[::-1])

    def test_leftovers_takes_ids_and_lists_with_tokens_kept_and_in_use(self):
        code, out, err = self.run_cli('leftovers', 'clear', 'spare', '--yes')
        self.assertEqual((code, out), (1, 'No leftover login has the id spare; switchboard leftovers lists them.\n'))
        self.assertEqual(self.run_cli('switch', 'spare')[0], 0)
        for argv in (('leftovers',), ('leftovers', '--json')):
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 0, err)
            self.assert_secret_nowhere(out, err)

    def test_export_carries_the_sign_ins_and_no_token_or_key(self):
        from switchboard import keys, lanes
        from test_keys import FakeKeychain
        lanes.KEYS['store'] = keys.Keys(runner=FakeKeychain(), path=os.path.join(self.rig.root, 'lanes.json'))
        self.addCleanup(lanes.KEYS.update, store=None)
        for more in ((), ('--with-keys',)):
            code, out, err = self.run_cli('export', '-', '--plain', *more)
            self.assertEqual(code, 0, err)
            payload = json.loads(out)
            self.assertEqual(sorted(a['email'] for a in payload['accounts']), ['a@example.com', 'b@example.com'])
            self.assertEqual(payload.get('keys') or [], [])
            self.assertNotIn('bearers', payload)
            self.assert_secret_nowhere(out, err)
            self.assertNotIn(TOKEN.replace('0101', '0909'), out)


if __name__ == '__main__':
    unittest.main()
