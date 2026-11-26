import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A night replayed on the real loop: Claude Code can't work on the account in use, and every read is turned away, so
# the loop moves it to another account on older numbers. Seconds later a storm of saves by open chats leaves Claude
# Code's Keychain item with no login. The login held for the account Claude Code is now on is put back within the
# put-back's first wait; no renewal of that login is asked of Anthropic meanwhile (the read of the account landed on
# included); the loop goes on deciding; exactly one note says so; nothing false is said. Then the same night with the
# put-back unable to act (it has put the login back four times in the last hour already, so it stops for now): the
# person is told within five awake minutes. The account engine runs for real on its stand-ins (fakes_accounts); the clock is a
# stand-in; made-up *.example accounts; no notification is shown.
import json
import os
import shutil
import tempfile
import time
import unittest

from fakes_accounts import Rig
from fakes_autoswitch import FakeClock, FakeNet, set_policy
from switchboard import server, watch
from switchboard.accounts import api, autoloop, context, live, putback, roster, sessions, tokens
from test_put_back_loop import MCP, Timed

ON = {'enabled': True, 'on': True, 'problem': None}


class TheNight(unittest.TestCase):
    def setUp(self):
        self._home = os.environ.get('SWITCHBOARD_HOME')
        sessions._reset_for_tests()
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        os.makedirs(self.rig.data, exist_ok=True)
        os.environ['SWITCHBOARD_HOME'] = self.rig.data          # the loop's folder and the engine's, as on a Mac
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)
        self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)     # Claude Code is on B
        self.block_b = self.rig.config()['oauthAccount']
        set_policy(enabled=True)
        self.net = FakeNet(self.clock)
        for aid in ('uuid-a', 'uuid-b'):                        # Anthropic turns every read away
            self.net.set(json.loads(self.rig.vault(aid))['claudeAiOauth']['accessToken'], 'timeout')
        self.told = []
        deps = autoloop.Deps(net=self.net, net_ready=lambda: True, load=lambda: 0.5, power=lambda: (True, 100),
                             procs=lambda: 1, disk_free=lambda: 10 ** 12, history_note=lambda *a, **k: None,
                             notify=self.told.append)
        self.loop = autoloop.Loop(deps=deps, clock=self.clock, rand=lambda: 0.5)
        self.spells = watch.Spells(mono=self.clock.mono, wall=self.clock.wall)
        self.page_notes = []

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        putback.reset()
        self.rig.close()
        sessions._reset_for_tests()
        if self._home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._home

    # ---------- what happens ----------

    def has_login(self):
        text = self.rig.live_text()
        return bool(text) and live.parse(text).kind == 'oauth'

    def forced_move(self):
        """Anthropic refused B's login (Claude Code emptied it) and no read gets through: the loop moves to A on older
        numbers, and reads A at once."""
        blob = json.loads(self.rig.live_text())
        blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        roster.forget_live()
        res = self.loop.run_pass()
        self.assertIsNotNone(res.switched, res.decision.reason)
        self.assertEqual(res.switched.to_id, 'uuid-a')
        self.assertIn('moved on older numbers', res.decision.reason)
        self.assertIn('moved on older numbers: the account it moved to', self.log())
        self.assertTrue(self.has_login())
        return self.clock.wall()

    def storm(self, settings_name=None):
        """Many chats save the item at once: it is gone, then saved back without the account login. A chat may also
        write the earlier account's name back into Claude Code's settings."""
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))
        if settings_name is not None:
            self.rig.set_config(settings_name)
        roster.forget_live()

    def page_look(self):
        """What the page would say at this moment: the four kinds of the watch and the lost sign-in, through its own
        spells (server.lost_signin reads the put-back's record)."""
        now = self.clock.wall()
        lost, back = server.lost_signin(now)
        active = (autoloop.load_state().get('live') or {}).get('email') if self.has_login() else None
        st = {'active': active, 'accounts': {}}
        s = autoloop.status(self.clock)
        s['running'] = True
        found = watch.troubles(ON, s, st, None, now, lost=lost)
        self.page_notes += [(round(now), n) for n in self.spells.look(found, active=active, back_at=back)]
        return found

    def spent(self):
        return [t for t in self.rig.server.used if 'uuid-a' in t]

    def log(self):
        try:
            with open(os.path.join(self.rig.data, autoloop.LOG)) as f:
                return f.read()
        except OSError:
            return ''

    def live_for(self, seconds):
        """The loop as it runs: passes and the sleeps between them, the page looking every few seconds."""
        end = self.clock.wall() + seconds
        timed = Timed(self.clock)
        timed.after.append(self.page_look)
        while self.clock.wall() < end:
            self.loop.sleep(self.loop.run_pass().delay)
        return timed

    # ---------- the night ----------

    def test_the_login_is_put_back_within_its_first_wait_and_one_note_says_so(self):
        t0 = self.forced_move()
        timed = Timed(self.clock)
        timed.at(t0 + 5, self.storm)
        back = []
        timed.after.append(lambda: back.append(self.clock.wall()) if not back and self.clock.wall() > t0 + 5
                           and self.has_login() else None)
        timed.after.append(self.page_look)
        self.loop.sleep(60.0)
        self.assertTrue(back, 'the login never came back')
        self.assertLessEqual(back[0] - (t0 + 5), putback.PUT_BACK_WAITS[0] + autoloop.WATCH_EVERY_S + 1)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'],
                         json.loads(self.rig.vault('uuid-a'))['claudeAiOauth'])
        passes = self.loop.pass_n
        self.live_for(10 * 60)                                  # the loop goes on deciding
        self.assertGreater(self.loop.pass_n, passes + 3)
        self.assertIsNone(watch.decided_late(dict(autoloop.status(self.clock), running=True), True))
        self.assertEqual(self.spent(), [])                      # no renewal of A's login, the landing read included
        self.assertEqual([r.action for r in self.told], ['put_back'])     # one note: the put-back's own
        self.assertEqual(self.page_notes, [])                   # and nothing from the page's watch
        said = ' | '.join(e[0] for e in self.rig.events)
        for false in ('not signed in', 'could not put', 'signed out', 'Signed out'):
            self.assertNotIn(false, said)
        self.assertEqual(len([e for e in self.rig.events if e[0].startswith('Claude Code lost its sign-in;')]), 1)
        self.assertNotIn('pass failed', self.log())
        self.assertEqual(self.page_look(), {})

    def test_with_the_put_back_unable_to_act_the_person_is_told_within_five_awake_minutes(self):
        t0 = self.forced_move()
        now = self.clock.wall()                                 # four put-backs in the last hour already: it stops
        putback._save_record(self.ctx, {'missing': None, 'done': [now - 600 + 60 * i for i in range(4)],
                                        'failed': []}, now)
        timed = Timed(self.clock)
        timed.at(t0 + 5, self.storm)
        timed.after.append(self.page_look)
        self.loop.sleep(60.0)
        self.assertFalse(self.has_login())                      # the put-back's limit holds it back
        self.live_for(8 * 60)
        self.assertEqual(self.told, [])                         # no put-back, so no put-back note
        keys = [(t, n.key) for t, n in self.page_notes]
        self.assertEqual([k for _, k in keys], ['signin-lost'], keys)
        self.assertLessEqual(keys[0][0] - (t0 + 5), 300 + 10)  # within five awake minutes of the loss (a look's gap)
        self.assertIn('ana@work.example', self.page_notes[0][1].body)       # the account its settings name, whole
        said = [e[0] for e in self.rig.events]
        self.assertIn(putback.LIMIT_WORDS, said)                # History says why, once
        self.assertEqual(said.count(putback.LIMIT_WORDS), 1)
        self.assertEqual(self.spent(), [])


if __name__ == '__main__':
    unittest.main()
