"""One voice for one-year tokens and API keys kept as accounts.

- `switchboard auto rank` and `auto line`, given a token's or a key's name, say what `move`, `swap`, `folder` and
  `export` say ("spare is a one-year token; switchboard auto rank is for sign-ins.") and keep nothing.
- History's line for the newer sign-in of the same account names the account the way the plain switch line does: its
  email and organization when another kept account has that email (the line for a switch onto a token:
  tests/test_tokens_with_the_shared_rules.py).

The engine is the real one on a made-up Mac (fakes_accounts.Rig). Made-up tokens and accounts only."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from fakes_accounts import Rig
from switchboard import __main__ as cli
from switchboard.accounts import api, bearer, context, live, roster, switcher
from switchboard.core import SwitchError

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0701-aaaaaaaaaaaa'
OWN = 'sk-ant-oat01-madeUpOneYearToken-0702-bbbbbbbbbbbb'
KEY = 'sk-ant-api03-madeUpConsoleKey-0703-cccccccccccccc'
TWIN = 'maya@twin.example'


class AutoRankAndLine(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.rig.two_accounts()
        self.use = context.use(self.rig.ctx)
        self.use.__enter__()
        roster.forget_live()
        bearer.add(self.rig.ctx, TOKEN, for_id='uuid-a', alias='a-tok')
        bearer.add(self.rig.ctx, OWN, label='spare')
        bearer.add(self.rig.ctx, KEY, label='ci')

    def tearDown(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SwitchError as e:
                code = 'refused: %s' % e
        return code, out.getvalue(), err.getvalue()

    def test_a_tokens_or_a_keys_name_gets_the_sentence_the_other_commands_say(self):
        before = api.auto_settings()
        for argv, said in ((('rank', 'spare', '1'),
                            'spare is a one-year token; switchboard auto rank is for sign-ins.'),
                           (('rank', 'ci', '2'), 'ci is an API key; switchboard auto rank is for sign-ins.'),
                           (('line', 'a-tok', '--five-hour', '80'),
                            'a-tok is the one-year token for a@example.com; switchboard auto line is for sign-ins.'),
                           (('line', 'ci', 'off'), 'ci is an API key; switchboard auto line is for sign-ins.')):
            code, out, _ = self.run_cli('auto', *argv)
            self.assertEqual(code, 'refused: ' + said, argv)
            self.assertEqual(out, '')
        code, _, _ = self.run_cli('auto', 'rank', 'nobody', '1')         # a name nothing has: as before
        self.assertEqual(code, 'refused: No saved account matches "nobody".')
        self.assertEqual(api.auto_settings(), before)


class TheNewerSignInOfTheSameAccount(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx

    def tearDown(self):
        self.rig.close()

    def test_its_history_line_names_the_account_with_its_organization(self):
        older = self.rig.sign_in('uuid-m', TWIN, 'org-n', name='Northwind')
        roster.add_current(self.ctx)
        self.rig.sign_in('uuid-m', TWIN, 'org-p', name='Fernhill')
        roster.add_current(self.ctx)
        self.rig.sign_in('uuid-m', TWIN, 'org-n', name='Northwind')       # a newer sign-in of the first, kept
        roster.add_current(self.ctx)
        self.rig.set_live(older)                                           # a session wrote the older one back
        res = switcher.switch(self.ctx, 'uuid-m', source='auto', replace_dead=switcher.live_mark(live.parse(older)))
        self.assertTrue(res.ok)
        said = [(text, facts) for text, kind, facts in self.rig.events if facts.get('h') == 'signin']
        self.assertEqual(said[-1][0], switcher.SAME_ACCOUNT % ('%s (Northwind)' % TWIN))
        self.assertEqual(said[-1][1]['account'], TWIN)                     # its facts keep the email


if __name__ == '__main__':
    unittest.main()
