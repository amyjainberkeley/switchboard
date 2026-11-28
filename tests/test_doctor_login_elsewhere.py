"""Said, never acted on: (a) a variable set in this shell that the page's background job does not have
(CLAUDE_CONFIG_DIR, SWITCHBOARD_HOME, SWITCHBOARD_PORT), so a command here and the page can work on different
logins; (b) Claude Code set to use an API key or a cloud provider instead of a login, so a switch changes nothing
for it. doctor says each in one line; `switchboard switch` adds (b) after its result."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import io
import json
import os
import plistlib
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from switchboard import __main__ as cli
from switchboard import core
from switchboard.accounts import facts

CLEAN = dict((k, v) for k, v in os.environ.items()
             if k not in facts.AUTH_OVERRIDES + cli.CLOUD + ('ANTHROPIC_PROFILE',))
KEY, OTHER, THIRD = 'made-up-key-' + 'k' * 30, 'another-key-' + 'z' * 30, 'a-third-key-' + 'y' * 30


class Lines(object):
    def __init__(self):
        self.said = []

    def __call__(self, good, text, fix=''):
        self.said.append((good, text))


class ShellOnly(unittest.TestCase):
    def setUp(self):
        self.plist = os.path.join(tempfile.mkdtemp(prefix='switchboard-plist-'), 'job.plist')

    def job(self, env):
        with open(self.plist, 'wb') as f:
            plistlib.dump({'Label': cli.AGENT, 'EnvironmentVariables': env}, f)

    def test_each_variable_the_job_lacks_is_one_line_naming_it(self):
        self.job({'PATH': '/usr/bin:/bin'})
        lines = Lines()
        with mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': '/x/.claude-work', 'SWITCHBOARD_PORT': '7801'}):
            cli._doctor_shell(lines, self.plist)
        said = [t for g, t in lines.said]
        self.assertIn("CLAUDE_CONFIG_DIR is set in this shell but not in the page's background job, so the two can "
                      "work on different logins", said)
        self.assertIn("SWITCHBOARD_PORT is set in this shell but not in the page's background job, so this command "
                      "can be talking to another page", said)
        self.assertTrue(all(g is None for g, t in lines.said))           # said, never a failure

    def test_another_value_in_the_job_is_said_as_that(self):
        self.job({'SWITCHBOARD_PORT': '7799'})
        lines = Lines()
        with mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': '7801'}):
            cli._doctor_shell(lines, self.plist)
        self.assertEqual([t for g, t in lines.said if t.startswith('SWITCHBOARD_PORT')],
                         ["SWITCHBOARD_PORT is set in this shell but has another value in the page's background "
                          "job, so this command can be talking to another page"])

    def test_nothing_when_the_job_has_the_same_or_there_is_no_job(self):
        same = dict((k, '/made/up/%s' % k) for k in cli.SHELL_ONLY)
        self.job(same)
        lines = Lines()
        with mock.patch.dict(os.environ, same):
            cli._doctor_shell(lines, self.plist)
            cli._doctor_shell(lines, self.plist + '.gone')
        self.assertEqual(lines.said, [])


class AuthOverride(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, CLEAN, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.settings = os.path.join(core.claude_dir(), 'settings.json')
        os.makedirs(core.claude_dir(), exist_ok=True)
        self.addCleanup(lambda: os.path.exists(self.settings) and os.remove(self.settings))

    def write(self, doc):
        with open(self.settings, 'w') as f:
            json.dump(doc, f)

    def test_where_it_is_set(self):
        self.assertIsNone(cli.auth_override())
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'made-up'}):
            self.assertEqual(cli.auth_override(), ('ANTHROPIC_API_KEY', 'this shell'))
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_USE_BEDROCK': '1'}):
            self.assertEqual(cli.auth_override(), ('CLAUDE_CODE_USE_BEDROCK', 'this shell'))
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_USE_VERTEX': '0'}):
            self.assertIsNone(cli.auth_override())
        for name, value in (('CLAUDE_CODE_USE_FOUNDRY', 'true'), ('CLAUDE_CODE_USE_MANTLE', '1'),
                            ('CLAUDE_CODE_USE_ANTHROPIC_AWS', 'yes'), ('CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD', 'ON'),
                            ('ANTHROPIC_PROFILE', 'work')):
            with mock.patch.dict(os.environ, {name: value}):
                self.assertEqual(cli.auth_override(), (name, 'this shell'))
        for off in ('false', 'No', 'OFF', ' '):                    # Claude Code's own words for "off", in any casing
            with mock.patch.dict(os.environ, {'CLAUDE_CODE_USE_BEDROCK': off}):
                self.assertIsNone(cli.auth_override(), off)
        self.write({'env': {'ANTHROPIC_AUTH_TOKEN': 'made-up'}})
        self.assertEqual(cli.auth_override(), ('ANTHROPIC_AUTH_TOKEN', "Claude Code's settings"))
        self.write({'apiKeyHelper': '/made/up/helper'})
        self.assertEqual(cli.auth_override(), ('apiKeyHelper', "Claude Code's settings"))
        self.assertEqual(cli.auth_line('apiKeyHelper', "Claude Code's settings"),
                         "Claude Code uses apiKeyHelper (set in Claude Code's settings) instead of an account's login, "
                         "so a switch changes nothing for it.")

    def test_switch_says_it_after_its_result(self):
        out = io.StringIO()
        with redirect_stdout(out), \
                mock.patch.object(cli, 'use',
                                  lambda name, why=False: print('Claude Code is now on b@x.example') or 0), \
                mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'made-up'}):
            self.assertEqual(cli.switch_command(['b@x.example']), 0)
        self.assertEqual(out.getvalue(), 'Claude Code is now on b@x.example\n' +
                         cli.auth_line('ANTHROPIC_API_KEY', 'this shell', None) + '\n')      # (no answer on record)
        out = io.StringIO()
        with redirect_stdout(out), \
                mock.patch.object(cli, 'use', lambda name, why=False: print('Claude Code is now on b@x.example') or 0):
            cli.switch_command(['b@x.example'])
        self.assertEqual(out.getvalue(), 'Claude Code is now on b@x.example\n')

    def answered(self, key, where):
        """Claude Code's own record of the person's answer to "use this API key?" (the key's last 20 characters)."""
        self.answers({'approved': [], 'rejected': [], where: [key[-20:]]})

    def answers(self, lists):
        path = core.claude_json()
        with open(path, 'w') as f:
            json.dump({'customApiKeyResponses': lists}, f)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))

    def test_what_the_settings_say_is_what_applies(self):
        """Claude Code writes each env entry of its settings over what the shell gave it."""
        self.write({'env': {'CLAUDE_CODE_USE_BEDROCK': '0'}})
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_USE_BEDROCK': '1'}):
            self.assertIsNone(cli.auth_override())
        self.write({'env': {'ANTHROPIC_API_KEY': ''}})                 # a key the settings blank out is no key
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': KEY}):
            self.assertIsNone(cli.auth_override())
        self.write({'env': {'CLAUDE_CODE_USE_BEDROCK': True}})         # (JSON's own true)
        self.assertEqual(cli.auth_override(), ('CLAUDE_CODE_USE_BEDROCK', "Claude Code's settings"))
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_USE_BEDROCK': '0'}):
            self.assertEqual(cli.auth_override(), ('CLAUDE_CODE_USE_BEDROCK', "Claude Code's settings"))
        self.write({'env': {'CLAUDE_CODE_USE_BEDROCK': '1'}})
        self.assertEqual(cli.auth_override(), ('CLAUDE_CODE_USE_BEDROCK', "Claude Code's settings"))
        with mock.patch.dict(os.environ, {'CLAUDE_CODE_USE_BEDROCK': '1'}):
            self.assertEqual(cli.auth_override(), ('CLAUDE_CODE_USE_BEDROCK', "this shell and Claude Code's settings"))

    def test_the_one_named_is_the_one_claude_code_picks(self):
        """Its own order: a cloud provider, the bearer token, the API key, the key helper, the long-lived token, a
        named profile."""
        self.answered(KEY, 'approved')
        self.write({'apiKeyHelper': '/made/up/helper'})
        left = {'CLAUDE_CODE_USE_VERTEX': '1', 'ANTHROPIC_AUTH_TOKEN': 'made-up', 'ANTHROPIC_API_KEY': KEY,
                'CLAUDE_CODE_OAUTH_TOKEN': 'made-up', 'ANTHROPIC_PROFILE': 'work'}
        for name in ('CLAUDE_CODE_USE_VERTEX', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'apiKeyHelper',
                     'CLAUDE_CODE_OAUTH_TOKEN', 'ANTHROPIC_PROFILE'):
            with mock.patch.dict(os.environ, left):
                self.assertEqual(cli.auth_override()[0], name)
                self.assertEqual(cli.auth_said(after_switch=True), cli.auth_line(*cli.auth_override()))
            if name == 'apiKeyHelper':
                self.write({})
            else:
                left.pop(name)
        self.assertIsNone(cli.auth_said())

    def test_beside_a_key_not_known_to_be_in_use_both_are_named_and_a_switch_still_says_it(self):
        """A key that is declined, not answered yet, or on both answer lists, with something set below it: chats use
        one of the two and runs started with -p use the key, so whichever it is, a switch changes nothing. The line
        says that much and no more, and both doctor and the switch say it."""
        for lists in (None, {'rejected': [KEY[-20:]]}, {'approved': [KEY[-20:]], 'rejected': [KEY[-20:]]}):
            if lists:
                self.answers(lists)
            for name, env, doc, where in (
                    ('apiKeyHelper', {}, {'apiKeyHelper': '/made/up/helper'}, "Claude Code's settings"),
                    ('CLAUDE_CODE_OAUTH_TOKEN', {'CLAUDE_CODE_OAUTH_TOKEN': 'made-up'}, {}, 'this shell'),
                    ('ANTHROPIC_PROFILE', {'ANTHROPIC_PROFILE': 'work'}, {}, 'this shell')):
                self.write(doc)
                with mock.patch.dict(os.environ, dict(env, ANTHROPIC_API_KEY=KEY)):
                    said = cli.auth_said()
                    self.assertEqual(said, "Claude Code has ANTHROPIC_API_KEY (set in this shell) and %s (set in %s): "
                                           "it uses one of them instead of an account's login, so a switch changes "
                                           "nothing." % (name, where), lists)
                    self.assertEqual(cli.auth_said(after_switch=True), said)
            self.write({})
            for name in ('CLAUDE_CODE_USE_BEDROCK', 'ANTHROPIC_AUTH_TOKEN'):       # and what ranks above the key
                with mock.patch.dict(os.environ, {name: '1', 'ANTHROPIC_API_KEY': KEY}):
                    self.assertEqual(cli.auth_said(after_switch=True), cli.auth_line(name, 'this shell'))

    def test_a_key_on_both_answer_lists_is_not_called_declined(self):
        self.answers({'approved': [KEY[-20:]], 'rejected': [KEY[-20:]]})
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': KEY}):
            self.assertEqual(cli.auth_said(after_switch=True), cli.auth_line('ANTHROPIC_API_KEY', 'this shell', None))

    def test_of_two_keys_the_settings_one_is_the_one_whose_answer_counts(self):
        """The settings' value is what Claude Code runs with, so its answer is the one that says what chats use."""
        both = "this shell and Claude Code's settings"
        self.answers({'approved': [THIRD[-20:]], 'rejected': [OTHER[-20:]]})
        self.write({'env': {'ANTHROPIC_API_KEY': THIRD}, 'apiKeyHelper': '/made/up/helper'})
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': OTHER}):            # declined here, approved there
            self.assertEqual(cli.auth_said(after_switch=True), cli.auth_line('ANTHROPIC_API_KEY', both))
        self.write({'env': {'ANTHROPIC_API_KEY': OTHER}})
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': THIRD}):            # approved here, declined there
            self.assertIsNone(cli.auth_said(after_switch=True))
            self.assertEqual(cli.auth_said(), cli.auth_line('ANTHROPIC_API_KEY', both, 'declined'))
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': OTHER}):            # one key in both places, declined
            self.assertIsNone(cli.auth_said(after_switch=True))
            self.assertEqual(cli.auth_said(), cli.auth_line('ANTHROPIC_API_KEY', both, 'declined'))

    def test_an_api_key_the_person_declined_is_not_said_to_be_in_use(self):
        key = 'made-up-key-' + 'k' * 30
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': key}):
            self.assertEqual(cli.auth_said(), cli.auth_line('ANTHROPIC_API_KEY', 'this shell', None))   # not answered
            self.assertEqual(cli.auth_said(), "Claude Code has ANTHROPIC_API_KEY (set in this shell): runs started "
                                              "with -p use it, and chats use it only if you approved it when Claude "
                                              "Code asked. Where it is used, a switch changes nothing.")
            self.answered(key, 'rejected')
            said = cli.auth_said()
            self.assertEqual(said, "Claude Code has ANTHROPIC_API_KEY (set in this shell), which you declined for "
                                   "chats, so chats do not use it. Runs started with -p do, so a switch changes "
                                   "nothing for those.")
            self.assertIsNone(cli.auth_said(after_switch=True))        # the switch did move the chats: nothing to add
            out = io.StringIO()
            with redirect_stdout(out), mock.patch.object(cli, 'use', lambda name, why=False: print('moved') or 0):
                cli.switch_command(['b@x.example'])
            self.assertEqual(out.getvalue(), 'moved\n')
            self.answered(key, 'approved')
            self.assertEqual(cli.auth_said(after_switch=True),
                             "Claude Code uses ANTHROPIC_API_KEY (set in this shell) instead of an account's login, "
                             "so a switch changes nothing for it.")

    def test_another_key_s_answer_does_not_count(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'made-up-key-' + 'k' * 30}):
            self.answered('another-key-' + 'z' * 30, 'rejected')
            self.assertIn('only if you approved it', cli.auth_said())

    def test_each_shell_only_variable_says_what_can_differ(self):
        plist = os.path.join(tempfile.mkdtemp(prefix='switchboard-plist-'), 'job.plist')
        with open(plist, 'wb') as f:
            plistlib.dump({'Label': cli.AGENT, 'EnvironmentVariables': {'PATH': '/usr/bin:/bin'}}, f)
        lines = Lines()
        with mock.patch.dict(os.environ, dict((k, '/made/up/%s' % k) for k in cli.SHELL_ONLY)):
            cli._doctor_shell(lines, plist)
        head = " is set in this shell but not in the page's background job, so "
        self.assertEqual([t for g, t in lines.said],
                         ['CLAUDE_CONFIG_DIR' + head + 'the two can work on different logins',
                          'SWITCHBOARD_HOME' + head + 'the two can keep different accounts and settings',
                          'SWITCHBOARD_PORT' + head + 'this command can be talking to another page'])

    def test_doctor_has_the_line(self):
        with open(cli.__file__, encoding='utf-8') as f:
            src = f.read()
        self.assertIn('    said = auth_said()\n    if said:\n        line(None, said)\n', src)
        self.assertIn('    _doctor_shell(line, plist)\n', src)


if __name__ == '__main__':
    unittest.main()
