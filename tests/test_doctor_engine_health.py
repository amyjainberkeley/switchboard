"""doctor's lines for the account engine's own health check, in the shape the engine really gives.

engine.health_items must not say "Claude code: not known" as a NO (needs a look) whenever Claude Code's version could
not be read, or is newer than the version the engine's facts were checked against, nor "not known" for the Keychain,
the accounts' file and the logins being renewed. Here the health check is the engine's own (api.health on the
account fakes), and each line is read as a person reads it: NO only for what needs a look, and words that say what is
there.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import time
import unittest

from fakes_accounts import Rig, login_text
from switchboard import engine
from switchboard.accounts import context, facts, tokens, vault


class DoctorLines(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.rig.two_accounts()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def items(self):
        return engine.health_items(engine.health())

    def claude_code(self, version):
        os.makedirs(os.path.join(self.rig.home, '.local', 'share', 'claude', 'versions', version))

    def test_a_healthy_engine_has_no_no_and_says_what_is_there(self):
        self.claude_code(facts.CHECKED)
        items = self.items()
        self.assertEqual([t for good, t in items if good is False], [])
        self.assertIn((True, "Claude Code's conventions: checked against %s, the version here" % facts.CHECKED), items)
        store = [(g, t) for g, t in items if t.startswith("The accounts' file")]
        self.assertEqual(len(store), 1)
        self.assertRegex(store[0][1], r"^The accounts' file: 2 accounts, \d+ backups?$")   # one kept at each save
        self.assertIs(store[0][0], True)
        self.assertIn((True, 'A cut-off switch: none'), items)
        self.assertIn((True, 'Logins being renewed: none'), items)
        self.assertIn((True, 'Recovery copies: none'), items)
        self.assertIn((True, 'Accounts a terminal is using: none'), items)
        self.assertIn((True, 'Warnings: none'), items)
        for good, text in items:
            with self.subTest(line=text):
                self.assertNotIn('not known', text)
                self.assertNotIn("{'", text)                        # never a dict as Python prints it

    def test_claude_codes_version_not_known_or_newer_is_worth_knowing_not_a_no(self):
        items = self.items()                                        # no Claude Code in this home
        self.assertIn((None, "Claude Code's conventions: checked against %s; the version on this Mac is not known"
                       % facts.CHECKED), items)
        self.assertEqual(engine.health_lines(engine.health()), [])
        self.assertIn((True, 'Warnings: none'), items)              # said once, on its own line
        self.claude_code('9.9.9')
        self.assertIn((None, "Claude Code's conventions: checked against %s; this Mac has 9.9.9, newer"
                       % facts.CHECKED), self.items())

    def test_what_needs_a_look_is_a_no(self):
        with open(vault.store_path(self.ctx), 'w') as f:
            f.write('{not json')
        # doctor only looks: it never restores the list from its copies (any other reader does), so it says NO
        with vault.no_restore():
            items = self.items()
        self.assertIn((False, "The accounts' file: can't be read (its last good copies are beside it)"), items)

    def test_a_recovery_copy_and_a_keychain_that_waits(self):
        now = int(time.time() * 1000)
        vault.stash(self.ctx, 'safety:0123456789abcdef', login_text('a', 'r', now + 3600000), None, 'before switching')
        h = engine.health()
        h['keychain'] = dict(h['keychain'], needs_approval_or_slow=['Switchboard login'])
        items = engine.health_items(h)
        self.assertIn((None, 'Recovery copies: 1 login kept nowhere else yet'), items)
        self.assertIn((None, "macOS's Keychain: waiting on 1 item (a dialog to answer, or a slow answer)"), items)
        h['keychain'] = dict(h['keychain'], resting_all='locked')
        self.assertIn("macOS's Keychain: locked (unlock the Mac)", engine.health_lines(h))
        # not in this shell's keychain list (over SSH, or a home that is not the account's): worth knowing, not a NO
        h['keychain'] = dict(h['keychain'], resting_all='no_keychain')
        self.assertIn((None, "macOS's Keychain: not reachable from here (a terminal on the Mac itself reaches it)"),
                      engine.health_items(h))
        h['keychain'] = dict(h['keychain'], resting_all='timeout')
        self.assertEqual(engine.health_lines(h), [])                # a slow answer once is not a fault


if __name__ == '__main__':
    unittest.main()
