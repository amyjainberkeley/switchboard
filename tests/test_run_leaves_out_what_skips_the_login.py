"""`switchboard run NAME` runs Claude Code as that account. Claude Code itself puts several things above an account's
login: a cloud provider it is told to use, a bearer token, an API key, a long-lived token, a named profile (its docs,
"Authentication precedence"). A run's environment carries none of them, on a private folder and on the default login
alike, and a run that shares the person's settings is refused when those settings turn one on."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import tempfile
import unittest

from switchboard import gateway
from switchboard.accounts import facts, runas, runfolder
from switchboard.accounts.api import AccountsError

SHELL = {'PATH': '/usr/bin:/bin', 'KEEP_ME': 'yes', 'CLAUDE_CONFIG_DIR': '/elsewhere',
         'CLAUDE_SECURESTORAGE_CONFIG_DIR': '/elsewhere', 'ANTHROPIC_API_KEY': 'made-up',
         'ANTHROPIC_AUTH_TOKEN': 'made-up', 'CLAUDE_CODE_OAUTH_TOKEN': 'made-up', 'CLAUDE_CODE_USE_BEDROCK': '1',
         'CLAUDE_CODE_USE_VERTEX': '1', 'CLAUDE_CODE_USE_FOUNDRY': '1', 'CLAUDE_CODE_USE_MANTLE': '1',
         'CLAUDE_CODE_USE_ANTHROPIC_AWS': '1', 'CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD': '1',
         'ANTHROPIC_PROFILE': 'work', 'ANTHROPIC_FEDERATION_RULE_ID': 'made-up'}


class TheRunsEnvironment(unittest.TestCase):
    def test_a_private_folder_run_carries_nothing_that_skips_the_login(self):
        env = runas._own_env(SHELL, '/made/up/run-folder')
        self.assertEqual(env, {'PATH': '/usr/bin:/bin', 'KEEP_ME': 'yes', 'CLAUDE_CONFIG_DIR': '/made/up/run-folder'})

    def test_a_run_on_the_default_login_carries_none_of_it_either(self):
        self.assertEqual(runas._own_env(SHELL), {'PATH': '/usr/bin:/bin', 'KEEP_ME': 'yes'})

    def test_every_provider_claude_code_names_is_left_out_everywhere(self):
        for name in facts.PROVIDERS:
            self.assertIn(name, facts.NOT_THE_LOGIN)
            self.assertIn(name, gateway.REMOVED_FROM_ENV)       # a session started for the keys' gateway, the same
        for name in facts.AUTH_OVERRIDES:
            self.assertIn(name, facts.NOT_THE_LOGIN)


class SharedSettings(unittest.TestCase):
    def items(self, doc):
        path = os.path.join(tempfile.mkdtemp(prefix='switchboard-settings-'), 'settings.json')
        with open(path, 'w') as f:
            json.dump(doc, f)
        return [('settings.json', 'link', path)]

    def refused(self, doc, name):
        with self.assertRaises(AccountsError) as caught:
            runfolder._no_key(self.items(doc))
        self.assertEqual(str(caught.exception), "Your Claude Code settings set %s, so a run would use that, not the "
                                                "account's own login. %s" % (name, runfolder.BARE))

    def test_a_provider_turned_on_in_the_settings_stops_a_shared_run(self):
        for name in facts.PROVIDERS:
            for on in ('1', 'true', 'YES', 'on', True, 1):
                self.refused({'env': {name: on}}, name)

    def test_a_provider_turned_off_there_does_not(self):
        for off in ('0', 'false', 'No', 'OFF', '', ' ', False, 0, None):
            runfolder._no_key(self.items({'env': dict((name, off) for name in facts.PROVIDERS)}))

    def test_a_profile_a_key_a_token_and_a_helper_each_stop_it(self):
        self.refused({'env': {'ANTHROPIC_PROFILE': 'work'}}, 'ANTHROPIC_PROFILE')
        self.refused({'env': {'ANTHROPIC_FEDERATION_RULE_ID': 'made-up'}}, 'ANTHROPIC_FEDERATION_RULE_ID')
        self.refused({'env': {'ANTHROPIC_API_KEY': 'made-up'}}, 'ANTHROPIC_API_KEY')
        self.refused({'env': {'ANTHROPIC_AUTH_TOKEN': 'made-up'}}, 'ANTHROPIC_AUTH_TOKEN')
        self.refused({'apiKeyHelper': '/made/up/helper'}, 'apiKeyHelper')
        runfolder._no_key(self.items({'env': {'KEEP_ME': 'yes'}, 'model': 'sonnet'}))


if __name__ == '__main__':
    unittest.main()
