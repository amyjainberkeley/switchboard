"""`uninstall.sh --all` finishes while Claude Code is on a one-year token Switchboard keeps.

Removing one account is refused while Claude Code is on a token kept for it (switch away first), so the next switch
still knows that login. Removing everything is different: nothing switches afterwards. So `tidy --all` removes each
account even then, with its tokens' records and their Keychain items, then every other kept token and key, and
Switchboard's other Keychain items. Claude Code's own login item is left exactly as it is, no recovery copy is made
and nothing is sent. The engine half runs on the account fakes (the suite's stand-in Keychain and Anthropic); the
command half on tests/fakes_accounts_api.py."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from fakes_accounts import Rig
from switchboard import engine
from switchboard.accounts import api, bearer, context, identity, switcher, vault

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-1101-aaaaaaaaaaaa'
OWN = 'sk-ant-oat01-madeUpOneYearToken-1102-bbbbbbbbbbbb'
KEY = 'sk-ant-api03-madeUpConsoleKey-1103-cccccccccccc'
OURS = ('Switchboard login', 'Switchboard login (previous)', 'Switchboard recovery') + tuple(bearer.SERVICES.values())


class OnAToken(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.rig.two_accounts()                                     # Claude Code is on B
        self.for_a, _ = bearer.add(self.ctx, TOKEN, for_id='uuid-a', alias='a-tok')
        self.own, _ = bearer.add(self.ctx, OWN, label='spare')
        bearer.add(self.ctx, KEY, label='ci')
        self.asked, self.stashed = [], []
        for name, seen in (('lookup_profile', self.asked), ('stash', self.stashed)):
            where = identity if name == 'lookup_profile' else vault
            real = getattr(where, name)

            def spy(*args, _real=real, _seen=seen, **kwargs):
                _seen.append(args[1])
                return _real(*args, **kwargs)
            patch = mock.patch.object(where, name, spy)
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        self.rig.close()

    def everything(self):
        """What tidy --all asks the engine: each account, then the rest. Only what it does is watched."""
        del self.stashed[:], self.asked[:]
        for a in api.accounts():
            engine.remove(a.id, everything=True)
        return engine.remove_all()

    def assert_all_gone_and_claude_code_untouched(self, live):
        self.assertEqual(api.accounts(), [])
        self.assertEqual(bearer.records(self.rig.doc()), {})
        self.assertEqual(sorted(k for k in self.rig.sec.items() if k[0] in OURS), [])
        self.assertEqual(self.rig.live_text(), live)
        self.assertEqual((vault.stashed(self.ctx), self.stashed, self.asked), ([], [], []))

    def test_on_the_token_kept_for_an_account(self):
        self.assertTrue(switcher.switch(self.ctx, self.for_a['id'], source='cli').ok)
        live, sent = self.rig.live_text(), len(self.rig.server.requests)
        said = self.everything()
        self.assertIn('One-year tokens and API keys kept as accounts: 2 removed.', said)
        self.assert_all_gone_and_claude_code_untouched(live)
        self.assertEqual(len(self.rig.server.requests), sent)

    def test_on_a_token_of_its_own(self):
        self.assertTrue(switcher.switch(self.ctx, self.own['id'], source='cli').ok)
        live, sent = self.rig.live_text(), len(self.rig.server.requests)
        self.everything()
        self.assert_all_gone_and_claude_code_untouched(live)
        self.assertEqual(len(self.rig.server.requests), sent)

    def test_removing_one_account_is_still_refused(self):
        self.assertTrue(switcher.switch(self.ctx, self.for_a['id'], source='cli').ok)
        with self.assertRaises(engine.Refused):
            engine.remove('uuid-a')
        self.assertIn('uuid-a', self.rig.doc()['accounts'])


class TidyAllAsksForEverything(unittest.TestCase):
    def setUp(self):
        from fakes_accounts_api import install
        self.fake = install(self)
        self.fake.hold('ana@work.example', 'org-work')
        q = mock.patch('switchboard.lanes.lanes', return_value=[])          # no gateway keys here
        q.start()
        self.addCleanup(q.stop)

    def test_each_account_is_removed_as_part_of_everything(self):
        from switchboard import __main__ as cli
        seen, real = [], self.fake.remove

        def remove(target, **kwargs):
            seen.append(kwargs)
            return real(target, **kwargs)
        api.remove = remove
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['tidy', '--all']), 0)
        self.assertEqual(seen, [{'everything': True}])
        with redirect_stdout(io.StringIO()):
            cli.main(['tidy'])
        self.assertEqual(seen, [{'everything': True}])                  # a plain tidy removes no account


if __name__ == '__main__':
    unittest.main()
