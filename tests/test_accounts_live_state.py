"""Where the account engine and the automatic loop meet: the words each side reads from the other.

The loop and the page read api.live().state as present / absent / unreadable (api.PRESENT, ...), plus the engine's own
'wiped' (Claude Code emptied its login after Anthropic refused it) and 'other' (no account login). A login the engine
can name must read as present on the other side, or the loop never switches and the page shows no account in use."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import shutil
import tempfile
import time
import unittest

from fakes_accounts import Rig
from switchboard.accounts import api, autoloop, context, policy, roster, tokens, usage


def put_readings(now, **numbers):
    """The readings store with fresh endpoint numbers: account id -> (5-hour %, weekly %)."""
    store = usage.blank()
    for aid, (five, week) in numbers.items():
        store['accounts'][aid] = {
            'w': {'5-hour': {'pct': five, 'resets_at': now + 3600, 'at': now, 'src': 'endpoint'},
                  'weekly': {'pct': week, 'resets_at': now + 3 * 86400, 'at': now, 'src': 'endpoint'}},
            'ep': {'ok_at': now}}
    usage.write_atomic(usage.store_path(), store)


class SeamCase(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='sb-seam-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')     # settings, readings, the loop's state
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.a_text, self.b_text = self.rig.two_accounts()          # Claude Code is on B

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()
        os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def wipe_live(self):
        """What Claude Code does after Anthropic refused its refresh: both tokens emptied, the label kept."""
        blob = json.loads(self.rig.live_text())
        blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        roster.forget_live()

    def deps(self):
        return autoloop.Deps(load=lambda: 0.5, power=lambda: (True, 100), procs=lambda: 1,
                             disk_free=lambda: 10 ** 12, net_ready=lambda: True,
                             history_note=lambda *a, **k: None, notify_move=lambda note: None)


class TheLiveState(SeamCase):
    def test_a_login_the_engine_can_name_is_present_as_the_loop_and_the_page_read_it(self):
        got = api.live()
        self.assertEqual((got.state, got.account_id), (api.PRESENT, 'uuid-b'))
        active, ok, why, disagree, email, refused = autoloop.interpret_live(got, None, None)
        self.assertEqual((active, ok, why, disagree, email, refused),
                         ('uuid-b', True, None, False, 'b@example.com', False))

    def test_absent_and_unreadable_keep_their_shared_names(self):
        self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=2)
        roster.forget_live()
        self.assertEqual(api.live().state, api.UNREADABLE)
        self.rig.kc.rules.clear()
        self.rig.kc.drop(self.rig.service, self.rig.account)
        roster.forget_live()
        self.assertEqual(api.live().state, api.ABSENT)


class AnEmptiedLogin(SeamCase):
    """Claude Code emptied its login (Anthropic refused it): Claude Code can't work, which is a dead login, so a
    forced switch to an account with room; never "can't be read" forever (a dead end)."""

    def test_the_loop_reads_it_as_the_labelled_account_whose_login_was_refused(self):
        self.wipe_live()
        got = api.live()
        self.assertEqual((got.state, got.account_id), ('wiped', 'uuid-b'))
        active, ok, why, disagree, email, refused = autoloop.interpret_live(got, None, None)
        self.assertEqual((active, ok, refused), ('uuid-b', True, True))

    def test_auto_switching_forces_a_move_to_an_account_with_room(self):
        autoloop.save_settings(enabled=True)
        put_readings(time.time(), **{'uuid-a': (20.0, 30.0), 'uuid-b': (40.0, 50.0)})
        self.wipe_live()
        snap = autoloop.gather(self.deps())
        d = policy.decide(snap)
        self.assertEqual((d.action, d.target, d.kind), ('switch', 'uuid-a', 'forced'), d.reason)
        self.assertIn('refused', d.reason)
        self.assertIn('send your message again', policy.switch_note(d, snap))

    def test_with_no_account_to_go_to_it_says_so_instead_of_blaming_the_keychain(self):
        autoloop.save_settings(enabled=True)
        put_readings(time.time(), **{'uuid-a': (99.0, 99.0), 'uuid-b': (40.0, 50.0)})
        self.wipe_live()
        d = policy.decide(autoloop.gather(self.deps()))
        self.assertEqual(d.action, 'stay')
        self.assertNotIn("can't be read", d.reason)


class PlainWords(unittest.TestCase):
    def live(self, state, why=None, account_id=None):
        return api.Live(state=state, account_id=account_id, email=None, proof='none', why=why)

    def test_no_account_login_is_said_as_that(self):
        active, ok, why, _, _, refused = autoloop.interpret_live(self.live('other', 'Claude Code is not using an '
                                                                           'account login', 'uuid-x'), None, 'uuid-x')
        self.assertEqual((active, ok, refused), (None, False, False))
        self.assertIn('not using an account login', why)

    def test_a_login_read_fine_whose_owner_is_not_known_is_never_called_unreadable(self):
        active, ok, why, _, _, refused = autoloop.interpret_live(
            self.live(api.PRESENT, 'Not known whose login this is'), None, 'uuid-x')
        self.assertEqual((active, ok, refused), (None, False, False))
        self.assertNotIn("can't be read", why)
        self.assertIn('Not known whose login', why)

    def test_each_keychain_reason_has_its_own_words(self):
        words = dict((w, autoloop.interpret_live(self.live(api.UNREADABLE, w), None, 'uuid-x')[2])
                     for w in ('timeout', 'dialog', 'locked', 'denied'))
        self.assertIn('slow', words['timeout'])
        self.assertIn('Keychain dialog', words['dialog'])
        self.assertIn('locked', words['locked'])
        self.assertIn('refused', words['denied'])
        self.assertEqual(len(set(words.values())), 4)
        self.assertFalse(any('(%s)' % w in text for w, text in words.items()))     # never the bare code


if __name__ == '__main__':
    unittest.main()
