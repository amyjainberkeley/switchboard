"""Where doctor, the uninstaller and the page's Add meet the Keychain.

1. doctor's Keychain line (engine.health_items) for the reason 'dialog' (a Keychain call ran past its deadline while
   macOS's Keychain dialog was up) is a NO in plain words, never the bare code "macOS's Keychain: dialog" as something
   only worth knowing: the person has to answer the dialog before anything can be switched.
2. uninstall --all's refusal (uninstall.py) for the same reason says so in plain words, never "it answered dialog".
3. The page's Add (connect.start) gives Claude Code's sign-in its own folder and none of the page's variables that
   point at another login. A CLAUDE_SECURESTORAGE_CONFIG_DIR would point that throwaway sign-in at another login item
   (the person's own live login, when it names ~/.claude), so the sign-in would replace the live login before
   add_login runs, and add_login would then find no login in the folder. The variables that give Claude Code a login
   of its own (facts.AUTH_OVERRIDES) stay out too.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import shutil
import time
import unittest
from unittest import mock

from fakes_accounts import Rig
from switchboard import connect, engine
from switchboard.accounts import api, context, facts, keychain, tokens
from switchboard.accounts.api import AccountsError

SVC, ACCT = 'Switchboard login', 'uuid-a'


def keychain_line(state):
    """doctor's one line for the Keychain, for keychain.Keychain.state()'s answer."""
    lines = [(good, text) for good, text in engine.health_items({'keychain': state})
             if text.startswith("macOS's Keychain")]
    assert len(lines) == 1, lines
    return lines[0]


class DoctorSaysADialogPlainly(unittest.TestCase):
    def resting(self, why):
        """The real Keychain wrapper's own state after a call ran into `why` (what its read, write and delete record)."""
        kc = keychain.Keychain(tool='/nonexistent')
        kc._fail((SVC, ACCT), why)
        return kc.state()

    def test_a_dialog_waiting_for_an_answer_is_a_no_in_plain_words(self):
        self.assertEqual(keychain_line(self.resting('dialog')),
                         (False, "macOS's Keychain: waiting for you to answer its dialog"))

    def test_an_item_still_waiting_on_the_dialog_says_so_too(self):
        st = keychain.Keychain(tool='/nonexistent').state()       # the rest is over, the helper still waits
        st.update(needs_approval_or_slow=[SVC], waiting_on_dialog=[SVC])
        self.assertEqual(keychain_line(st), (False, "macOS's Keychain: waiting for you to answer its dialog"))
        st.update(waiting_on_dialog=[])                             # only slow: worth knowing, not a NO
        self.assertEqual(keychain_line(st),
                         (None, "macOS's Keychain: waiting on 1 item (a dialog to answer, or a slow answer)"))

    def test_every_reason_the_keychain_rests_on_has_words_never_its_code(self):
        for why in keychain._GLOBAL:
            with self.subTest(why=why):
                good, text = keychain_line(self.resting(why))
                self.assertNotEqual(text, "macOS's Keychain: %s" % why)
                self.assertNotIn('_', text)


class UninstallSaysADialogPlainly(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.use = context.use(self.rig.ctx)
        self.use.__enter__()
        self.rig.two_accounts()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def test_a_delete_held_by_a_keychain_dialog_says_to_answer_it_and_the_account_stays(self):
        self.rig.kc.rule('delete', service=SVC, account=ACCT, why='dialog')
        with self.assertRaises(AccountsError) as cm:
            api.remove_all()
        self.assertEqual((cm.exception.kind, str(cm.exception)),
                         ('keychain_slow', "Couldn't take a@example.com's saved login out of the Keychain: macOS is "
                                           "waiting for you to answer a Keychain dialog."))
        self.assertEqual([a.email for a in api.accounts()], ['a@example.com'])     # so the uninstaller can run again

    def test_no_reason_a_delete_can_fail_with_is_said_as_its_code(self):
        for why in keychain._GLOBAL + ('denied',):
            with self.subTest(why=why):
                self.rig.kc.rules[:] = []
                self.rig.kc.rule('delete', service=SVC, account=ACCT, why=why)
                with self.assertRaises(AccountsError) as cm:
                    api.remove_all()
                self.assertNotIn('answered', str(cm.exception))
                self.assertNotIn(why if '_' in why else '\0', str(cm.exception))


class TheSignInGetsOnlyItsOwnFolder(unittest.TestCase):
    def setUp(self):
        self.envs = []
        self.addCleanup(connect.RUNNING.clear)
        for name, value in (('claude_bin', lambda: '/made-up/claude'), ('open_sign_in', lambda p, u: 'default'),
                            ('_clean', lambda d: None)):        # _clean would run the Keychain's tool
            p = mock.patch.object(connect, name, value)
            p.start()
            self.addCleanup(p.stop)

        def popen(args, env=None, **kw):
            self.envs.append(dict(env))
            # the sign-in's folder goes with the test: a later tidy's sweep would ask the Keychain to delete its item
            self.addCleanup(shutil.rmtree, env['CLAUDE_CONFIG_DIR'], True)
            with open(env['SWITCHBOARD_URL_FILE'], 'w') as f:
                f.write('https://claude.ai/oauth/authorize?code=made-up')
            return mock.Mock(pid=None, poll=lambda: None)
        p = mock.patch.object(connect.subprocess, 'Popen', popen)
        p.start()
        self.addCleanup(p.stop)

    def test_nothing_in_the_pages_environment_points_it_at_another_login(self):
        outside = dict((name, 'made-up') for name in facts.AUTH_OVERRIDES)
        outside['CLAUDE_SECURESTORAGE_CONFIG_DIR'] = os.path.expanduser('~/.claude')
        with mock.patch.dict(os.environ, outside):
            connect.start('a@example.com', None)
        env = self.envs[0]
        self.assertEqual(sorted(set(outside) & set(env)), [])
        self.assertTrue(env['CLAUDE_CONFIG_DIR'].startswith(os.path.join(connect.data_path('logins'), '')))
        self.assertEqual(facts.keychain_service(env), facts.keychain_service({'CLAUDE_CONFIG_DIR': env['CLAUDE_CONFIG_DIR']}))
        self.assertNotEqual(facts.keychain_service(env), facts.keychain_service({}))       # its own item, never the live one


if __name__ == '__main__':
    unittest.main()
