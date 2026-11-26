import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard config`: every setting from a terminal. Alone, each setting grouped as the page's Settings view groups
# them, with its value, its default and what it does; `get KEY` the value alone; `set KEY VALUE` sets it through the
# page's own request (the running page when it answers, else the same handler here); `unset KEY` puts its default
# back; `path` names the files; `--json` on the list and on get. An unknown KEY exits 2 and names the nearest real key.
# The settings kept in Claude Code's own files keep their own commands: config names them and never sets them. A key
# in the settings file that nothing reads is said under the list, never deleted. No secret is a setting.
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import config, core
from test_cli_commands import Cli, B
from test_cli_with_page import FakePage

ROOT = os.path.join(HERE, '..')
LIST_KEYS = {'schema', 'file', 'settings', 'not_read'}
ROW_KEYS = {'key', 'group', 'value', 'default', 'changed', 'type', 'choices', 'low', 'high', 'words', 'set_with'}
GET_KEYS = {'schema', 'key', 'value', 'default', 'refused'}
# A value for each setting `config set` sets, as typed, and as `config get` says it back.
VALUES = {'auto': ('on', 'on'), 'auto_policy.session_at': ('80', '80'), 'auto_policy.weekly_at': ('85.5', '85.5'),
          'auto_policy.model_at': ('70', '70'), 'auto_policy.model': ('Fable', 'Fable'),
          'auto_policy.strategy': ('most-room', 'most-room'), 'awake': ('off', 'off')}


def page_keys():
    """Every key the page's Settings view shows (the first word of each row's keys()), as the page writes them:
    `auto_policy.{session_at, weekly_at, model_at}` is three keys."""
    with open(os.path.join(ROOT, 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
        html = f.read()
    groups = html[html.index('const GROUPS = ['):html.index('const ROWS = ')]
    groups = groups.replace("' + (modelNames() ? ', model_at' : '') + '", ', model_at')
    found = set()
    for k in re.findall(r"keys: \(\) => \[\['([^']+)'", groups):
        m = re.match(r'^([a-z_]+)\.\{(.+)\}$', k)
        found |= set('%s.%s' % (m.group(1), x.strip()) for x in m.group(2).split(',')) if m else {k}
    return found


class Table(unittest.TestCase):
    def test_every_key_the_page_shows_is_in_the_table_and_none_without_a_reason(self):
        shown = page_keys()
        self.assertIn('auto_policy.model_at', shown)
        version = set(k for k in shown if re.match(r'^\d+(\.\d+)+$', k))       # claude-mem's version, not a key
        self.assertEqual(len(version), 1, shown)
        table = set(r.page for r in config.ROWS if r.page)
        self.assertEqual(shown - version, table)
        for r in config.ROWS:
            with self.subTest(key=r.key):
                self.assertTrue(r.page or r.page_why, 'no page key and no reason: %s' % r.key)
                self.assertRegex(r.key, r'^[a-z_]+(\.[a-z_]+)?$')
                self.assertIn(r.group, dict(config.GROUPS))
                self.assertTrue(r.words.endswith('.') or r.words.endswith(')'), r.words)
                self.assertEqual(sum(1 for x in (r.post, r.command, r.where, r.setter) if x), 1, r.key)

    def test_the_four_in_claude_codes_settings_keep_their_commands(self):
        for key in ('statusline', 'notify', 'hooks', 'models'):
            self.assertEqual(config.BY_KEY[key].command, 'switchboard %s on|off' % key)
            self.assertIsNone(config.BY_KEY[key].post)

    def test_no_secret_is_a_setting(self):
        for r in config.ROWS:
            self.assertNotRegex(r.key, r'key$|token|secret|password|login')
        self.assertNotIn('key', config.BY_KEY)
        self.assertIn('key', config.OWN_KEYS)

    def test_every_key_the_code_reads_from_the_settings_file_is_known(self):
        """A key read or written anywhere is a row's or Switchboard's own: never said to be one nothing reads."""
        known = set(r.file_key for r in config.ROWS if r.file_key) | set(config.OWN_KEYS)
        consts = {'POLICY_KEY': 'auto_policy', 'LINKS_WANTED': 'links_wanted', 'FORWARDED_KEY': 'links_forwarded',
                  'CLAUDE_PATH': 'claude_path'}
        found = set()
        for folder, _, names in os.walk(os.path.join(ROOT, 'switchboard')):
            for n in names:
                if n.endswith('.py'):
                    with open(os.path.join(folder, n), encoding='utf-8') as f:
                        text = f.read()
                    found |= set(re.findall(r"(?:set_setting|update_setting)\('([a-z_]+)'", text))
                    found |= set(re.findall(r"(?<![A-Za-z_])settings\(\)\.get\('([a-z_]+)'", text))
                    if n == 'keep.py':                  # it reads the file as `s` inside a lock
                        found |= set(re.findall(r"\bs\.get\('([a-z_]+)'\)", text))
                    # a key named by a constant of the module itself: `CHECK_KEY = 'update_check'`
                    own = dict(consts, **dict(re.findall(r"^([A-Z_]+) = '([a-z_]+)'", text, re.M)))
                    for name, key in own.items():
                        reads = r'(?:set_setting|update_setting|(?<![A-Za-z_])settings\(\)\.get)\(%s\b' % name
                        if re.search(reads, text):
                            found.add(key)
        self.assertEqual(sorted(found - known), [])

    def test_every_key_of_auto_policy_the_policy_reads_is_known(self):
        """The keys inside `auto_policy` policy.settings_from reads: never said to be read by nothing."""
        with open(os.path.join(ROOT, 'switchboard', 'accounts', 'policy.py'), encoding='utf-8') as f:
            text = f.read()
        body = text[text.index('def settings_from('):]
        body = body[:body.index('\ndef ')]
        found = set(re.findall(r"\bd\.get\('([a-z_]+)'\)", body)) | set(re.findall(r"\bline\('([a-z_]+)'\)", body))
        self.assertIn('own_lines', found)
        self.assertEqual(sorted(found - set(config.POLICY_KEYS) - {'enabled'}), [])     # enabled is the `auto` row


class Config(Cli):
    def doc(self, *args):
        code, out, err = self.run_cli('config', *args)
        self.assertEqual((code, err), (0, ''), out)
        return json.loads(out)


class Listing(Config):
    def test_alone_every_setting_grouped_with_value_default_and_words(self):
        self.two()
        code, out, err = self.run_cli('config')
        self.assertEqual((code, err), (0, ''))
        titles = [t for _, t in config.GROUPS]
        heads = [line for line in out.splitlines() if line and not line.startswith(' ')]
        self.assertEqual(heads[:len(titles)], titles)
        for r in config.ROWS:
            with self.subTest(key=r.key):
                line = next(x for x in out.splitlines() if x.startswith('  %s ' % r.key))
                self.assertIn('default %s' % config.shown(r, r.default), line)
                self.assertIn(r.words, out)
        self.assertIn('Set with: switchboard statusline on|off.', out)
        self.assertRegex(out, r'\n  auto_policy\.weekly_at +95 +default 95\n')
        self.assertTrue(out.rstrip().endswith('Kept in %s.' % config.settings_file()) or 'Kept in ~' in out, out)

    def test_json_pins_every_key(self):
        self.two()
        doc = self.doc('--json')
        self.assertEqual(set(doc), LIST_KEYS)
        self.assertEqual(list(doc)[0], 'schema')
        self.assertEqual(doc['schema'], 1)
        self.assertEqual([x['key'] for x in doc['settings']], [r.key for r in config.ROWS])
        for x in doc['settings']:
            self.assertEqual(set(x), ROW_KEYS)
        week = next(x for x in doc['settings'] if x['key'] == 'auto_policy.weekly_at')
        self.assertEqual((week['value'], week['default'], week['changed'], week['low'], week['high']),
                         (95, 95, False, 50, 99.9))
        self.assertEqual(week['set_with'], 'switchboard config set auto_policy.weekly_at VALUE')
        self.assertEqual(doc['not_read'], [])

    def test_get_prints_the_value_alone(self):
        self.two()
        self.assertEqual(self.run_cli('config', 'get', 'auto_policy.session_at'), (0, '90\n', ''))
        self.assertEqual(self.run_cli('config', 'get', 'awake'), (0, 'on\n', ''))
        self.assertEqual(self.run_cli('config', 'get', 'auto_policy.model'), (0, 'none\n', ''))
        self.assertEqual(self.run_cli('config', 'get', 'auto_policy.strategy'), (0, 'refills-first\n', ''))

    def test_get_json(self):
        self.two()
        doc = self.doc('get', 'auto_policy.weekly_at', '--json')
        self.assertEqual(set(doc), GET_KEYS)
        self.assertEqual(doc, {'schema': 1, 'key': 'auto_policy.weekly_at', 'value': 95, 'default': 95,
                               'refused': None})

    def test_path_names_the_files(self):
        code, out, _ = self.run_cli('config', 'path')
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), [os.path.join(os.environ['SWITCHBOARD_HOME'], 'settings.json'),
                                            os.path.join(core.claude_dir(), 'settings.json')])

    def test_the_rules_are_listed_by_whole_email_and_name_their_command(self):
        a, b = self.two()
        self.fake.auto['rules'] = {b: 'out'}
        self.assertEqual(self.run_cli('config', 'get', 'auto_policy.rules'), (0, '%s out\n' % B, ''))
        code, out, _ = self.run_cli('config', 'set', 'auto_policy.rules', 'none')
        self.assertEqual(code, 'refused: auto_policy.rules is set with switchboard auto last|out|in NAME.')


class SetAndUnset(Config):
    def test_set_then_get_round_trips_every_key_config_sets(self):
        self.two()
        settable = [r.key for r in config.ROWS if r.post and not r.page_only]
        self.assertEqual(sorted(settable), sorted(VALUES))
        for key in settable:
            typed, said = VALUES[key]
            with self.subTest(key=key):
                code, out, err = self.run_cli('config', 'set', key, typed)
                self.assertEqual((code, err), (0, ''), out)
                self.assertEqual(out, '%s is %s now (it was %s).\n' % (key, said, config.shown(
                    config.BY_KEY[key], config.BY_KEY[key].default)))
                self.assertEqual(self.run_cli('config', 'get', key), (0, said + '\n', ''))
                again = self.run_cli('config', 'set', key, typed)
                self.assertEqual(again, (0, '%s is %s already.\n' % (key, said), ''))
                code, out, _ = self.run_cli('config', 'unset', key)
                self.assertEqual(code, 0)
                self.assertEqual(self.run_cli('config', 'get', key)[1],
                                 config.shown(config.BY_KEY[key], config.BY_KEY[key].default) + '\n')

    def test_a_value_is_checked_by_the_pages_own_check(self):
        self.two()
        week = 'auto_policy.weekly_at takes a number from 50 to 99.9.'
        for key, typed, said in (('auto_policy.weekly_at', '120', week), ('auto_policy.weekly_at', 'lots', week),
                                 ('auto_policy.weekly_at', 'nan', week),
                                 ('auto_policy.strategy', 'fastest',
                                  'auto_policy.strategy takes refills-first, most-room or stay-then-refills.'),
                                 ('auto', 'maybe', 'auto takes on or off.')):
            with self.subTest(key=key, typed=typed):
                self.assertEqual(self.run_cli('config', 'set', key, typed)[0], 'refused: %s' % said)
        self.assertEqual(self.run_cli('config', 'get', 'auto_policy.weekly_at')[1], '95\n')

    def test_auto_on_from_config_is_yours_as_from_the_page(self):
        self.two()
        self.run_cli('config', 'set', 'auto', 'on')
        self.assertIs(core.settings().get('auto_set_by_you'), True)
        self.assertEqual(self.run_cli('auto')[0], 0)

    def test_through_the_running_page_when_it_answers(self):
        self.two()
        page = FakePage(self, {('POST', '/api/auto'): (200, {}), ('POST', '/api/gateway'): (200, {})})
        self.assertEqual(self.run_cli('config', 'set', 'auto_policy.weekly_at', '88')[0], 0)
        self.assertEqual(self.run_cli('config', 'set', 'gateway_on', 'on')[0], 0)
        self.assertEqual(page.posted, [('/api/auto', {'weekly_at': 88}), ('/api/gateway', {'on': True})])
        self.assertFalse([c for c in self.fake.calls if c[0] == 'set_auto'])       # the page did it

    def test_the_page_says_no_in_its_words(self):
        self.two()
        FakePage(self, {('POST', '/api/gateway'): (400, {'error': 'Add a key first.'})})
        self.assertEqual(self.run_cli('config', 'set', 'gateway_on', 'on')[0], 'refused: Add a key first.')

    def test_a_setting_that_runs_in_the_page_needs_the_page(self):
        self.two()
        self.assertEqual(self.run_cli('config', 'set', 'gateway_on', 'on')[0],
                         'refused: gateway_on runs in the page, which is not running. Start it with: switchboard serve')

    def test_settings_with_their_own_command_or_place_are_not_set_here(self):
        for key, said in (('statusline', 'statusline is set with switchboard statusline on|off.'),
                          ('port', 'port is set with switchboard update --port N.'),
                          ('house_rules', 'house_rules is set on the page (Settings, In Claude Code), not here.'),
                          ('gateway_port', 'gateway_port is set by hand in the settings file, not here.')):
            with self.subTest(key=key):
                self.assertEqual(self.run_cli('config', 'set', key, 'on')[0], 'refused: ' + said)


class Unknown(Config):
    def test_an_unknown_key_exits_2_and_names_the_nearest(self):
        code, out, _ = self.run_cli('config', 'get', 'auto_policy.weekly')
        self.assertEqual((code, out), (2, 'Unknown setting: auto_policy.weekly. Did you mean auto_policy.weekly_at?\n'))
        code, out, _ = self.run_cli('config', 'set', 'awak', 'on')
        self.assertEqual((code, out), (2, 'Unknown setting: awak. Did you mean awake?\n'))
        code, out, _ = self.run_cli('config', 'get', 'zzzz')
        self.assertEqual((code, out), (2, 'Unknown setting: zzzz. `switchboard config` lists them.\n'))

    def test_unknown_key_with_json_is_a_document_too(self):
        code, out, err = self.run_cli('config', 'get', 'awak', '--json')
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out), {'schema': 1, 'key': 'awak', 'value': None, 'default': None,
                                           'refused': 'Unknown setting: awak. Did you mean awake?'})
        self.assertIn('awake', err)

    def test_words_it_does_not_take(self):
        for args in (('set', 'awake'), ('get',), ('frob',), ('--frob',), ('path', 'x')):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli('config', *args), (2, config.USAGE + '\n', ''))


class NothingReadsIt(Config):
    def write(self, s):
        core.write_json(config.settings_file(), s)

    def test_a_key_nothing_reads_is_said_under_the_list_with_the_nearest_and_kept(self):
        self.two()
        self.write({'awak': False, 'auto_policy': {'weekly_At': 80, 'weekly_at': 90}, 'awake': True, 'key': 'x' * 20,
                    'old_thing': 1})
        code, out, _ = self.run_cli('config')
        self.assertEqual(code, 0)
        where = config._short(config.settings_file())
        self.assertEqual(out.splitlines()[-3:], [
            'awak is in %s, and nothing reads it. Did you mean awake?' % where,
            'old_thing is in %s, and nothing reads it.' % where,
            'auto_policy.weekly_At is in %s, and nothing reads it. Did you mean auto_policy.weekly_at?' % where])
        self.assertIn('awak', core.settings())                      # never deleted
        self.assertNotIn('x' * 20, out)                             # the links' secret is never printed
        doc = self.doc('--json')
        self.assertEqual(doc['not_read'], [{'key': 'awak', 'nearest': 'awake'}, {'key': 'old_thing', 'nearest': None},
                                           {'key': 'auto_policy.weekly_At', 'nearest': 'auto_policy.weekly_at'}])
        self.assertNotIn('x' * 20, json.dumps(doc))

    def test_a_file_with_only_known_keys_says_nothing(self):
        self.write({'awake': False, 'auto': True, 'let_go': [], 'auto_policy': {'weekly_at': 90}})
        self.assertEqual(config.not_read(), [])


class Help(Config):
    def test_help_names_it(self):
        code, out, _ = self.run_cli('config', '--help')
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('switchboard config'), out)


if __name__ == '__main__':
    unittest.main()
