import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A sign-in Anthropic refused: when a renewal is refused, Claude Code empties its login (the engine's 'wiped') and its
# settings still name the account. The put-back leaves it (it never writes over a login Claude Code emptied itself),
# and the words say only what is known: the sign-in was refused, not which copy of the login. So with "keep Claude
# Code signed in" on, the loop moves Claude Code to a signed-in account, also with automatic switching off, by the
# move's ordinary targets and brake: one move, its one History line (the switch's own), one notification. The real
# account engine on its stand-ins (fakes_accounts) and the automatic loop over it, with a fake clock. Made-up
# *.example accounts; no notification is shown, and no Keychain or network is reached.
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock, FakeNet, set_policy
from fakes_accounts import Rig
from switchboard.accounts import api, autoloop, context, live, putback, roster, sessions, tokens, vault

A, B = 'ana@work.example', 'ana@home.example'
WORDS = ("%s's sign-in was refused by Anthropic. Switchboard moved Claude Code to %s%s. "
         'Sign in to %s again when you like.')


class Case(unittest.TestCase):
    """The real account engine on its stand-ins, Claude Code on B, A signed in too, and the automatic loop."""

    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='stay-refused-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        sessions._reset_for_tests()
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        roster.forget_live()
        self.rig.keep('uuid-a', A, 'org-a')
        self.clock.advance(1.0)                 # two sign-ins never share an end stamp to the millisecond
        self.b_text = self.rig.keep('uuid-b', B, 'org-b')
        self.told, self.notes, self.moved = [], [], []
        patcher = mock.patch.object(autoloop, 'notify_put_back', self.told.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        deps = autoloop.Deps(net=FakeNet(self.clock), net_ready=lambda: True, load=lambda: 0.5,
                             power=lambda: (True, 100), procs=lambda: 1, disk_free=lambda: 10 ** 12,
                             history_note=lambda text, kind='info', **f: self.notes.append(text),
                             notify=self.told.append, notify_move=self.moved.append)
        self.loop = autoloop.Loop(deps=deps, clock=self.clock, rand=lambda: 0.5)
        set_policy(enabled=False)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        putback.reset()
        roster.forget_live()
        self.rig.close()
        sessions._reset_for_tests()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def refuse(self):
        """Anthropic refused the renewal: Claude Code empties its login; its settings still name B."""
        blob = json.loads(self.rig.live_text())
        blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        roster.forget_live()
        self.assertEqual(api.live().state, 'wiped')

    def passes(self, n):
        out = []
        for _ in range(n):
            out.append(self.loop.run_pass())
            self.clock.advance(61.0)
        return out

    def switch_lines(self):
        return [e for e in self.rig.events if e[2].get('h') == 'switch']

    def loop_log(self):
        try:
            with open(os.path.join(os.environ['SWITCHBOARD_HOME'], autoloop.LOG)) as f:
                return f.read()
        except OSError:
            return ''


class ARefusedSignIn(Case):
    def test_with_automatic_switching_off_one_move_one_history_line_one_notification(self):
        self.refuse()
        res = self.passes(4)
        self.assertEqual([r.switched.to_id for r in res if r.switched], ['uuid-a'], [r.decision.reason for r in res])
        self.assertEqual(live.parse(self.rig.live_text()).kind, 'oauth')            # Claude Code works on A now
        self.assertEqual(api.live().account_id, 'uuid-a')
        lines = self.switch_lines()
        self.assertEqual(len(lines), 1, lines)
        text, kind, facts = lines[0]
        self.assertEqual((text, kind, facts.get('by')), ('Switched Claude Code to %s' % A, 'switch', 'auto'))
        self.assertEqual(facts.get('reason'), WORDS % (B, A, ', although automatic switching is off', B))
        self.assertEqual(self.notes, [])                    # the switch's own line is the one line
        self.assertEqual(len(self.moved), 1)
        note = self.moved[0]
        self.assertEqual(note.title, 'Switched to %s' % A)
        self.assertEqual(note.body, 'Claude Code lost its sign-in. Switchboard moved Claude Code to %s, although '
                                    'automatic switching is off. Sign in to %s again when you like.' % (A, B))
        self.assertEqual(self.told, [])                     # not a put-back
        self.assertIn('moved Claude Code from account 2 to account 1 to keep it signed in (its sign-in was lost)',
                      self.loop_log())
        self.assertNotIn('@', self.loop_log())
        self.assertEqual(autoloop.load_state()[autoloop.STAY_LEFT], {'uuid-b': vault.load(self.ctx)['accounts']
                                                                     ['uuid-b']['fp']})

    def test_with_automatic_switching_on_the_same_move_and_words(self):
        set_policy(enabled=True)
        self.refuse()
        res = self.passes(3)
        self.assertEqual([r.switched.to_id for r in res if r.switched], ['uuid-a'])
        # with automatic switching on, a refused sign-in is the forced switch's: History has its words
        reason = self.switch_lines()[0][2].get('reason')
        self.assertIn("%s's login was refused" % B, reason)
        self.assertIn(A, reason)
        self.assertEqual(len(self.moved), 1)

    def test_the_setting_off_nothing_moves(self):
        autoloop.save_settings(stay_signed_in=False)
        self.refuse()
        res = self.passes(3)
        self.assertEqual([r.switched for r in res], [None, None, None])
        self.assertEqual(res[-1].decision.reason, 'Automatic switching is off.')
        self.assertEqual(live.parse(self.rig.live_text()).kind, 'wiped')            # nothing was written
        self.assertEqual((self.switch_lines(), self.moved), ([], []))

    def test_with_no_other_signed_in_account_it_stays_and_says_what_each_account_needs(self):
        with vault.changing(self.ctx) as doc:                                   # A's saved login was refused too
            a = doc['accounts']['uuid-a']
            a['strike'] = {'fp': a['fp'], 'at': self.clock.wall()}
        self.refuse()
        res = self.passes(3)
        self.assertEqual([r.switched for r in res], [None, None, None])
        self.assertEqual(res[-1].decision.action, 'stay')
        self.assertEqual(res[-1].decision.reason, "%s's sign-in was refused by Anthropic, and no other account is "
                                                  'signed in: %s needs a sign-in.' % (B, A))
        self.assertEqual(live.parse(self.rig.live_text()).kind, 'wiped')
        self.assertEqual((self.switch_lines(), self.moved), ([], []))

    def test_settings_that_name_no_saved_account_move_nothing(self):
        self.refuse()
        self.rig.set_config({'accountUuid': 'uuid-z', 'emailAddress': 'zed@else.example', 'organizationUuid': 'org-z'})
        roster.forget_live()
        res = self.passes(3)
        self.assertEqual([r.switched for r in res], [None, None, None])
        self.assertEqual(self.moved, [])


if __name__ == '__main__':
    unittest.main()
