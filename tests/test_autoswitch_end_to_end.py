"""One pass of the automatic loop across the account engine and the loop, with
fakes only for what is outside (the Keychain, Anthropic, the usage endpoint, the clock, load, power, processes).

Two accounts; Claude Code is on B, which is at its 5-hour line. The poller reads both accounts with tokens from the
engine (api.access_token: B's from the live login, A's from the vault), the policy forces a switch to A, the engine's
switch lands (live item and label are A's, the journal is gone), B's outgoing login is kept, and upkeep then renews
B's idle saved login exactly once (never A's, which Claude Code now owns)."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import shutil
import tempfile
import time
import unittest

from fakes_autoswitch import FakeClock, FakeNet, usage_body
from fakes_accounts import Rig
from switchboard.accounts import api, autoloop, context, live, sessions, switcher, tokens, usage, vault
from switchboard.accounts.api import ABSENT


class AutoAcrossTheEngine(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='sb-e2e-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')     # settings, readings, the loop's files
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        sessions._reset_for_tests()
        with usage._HANGING_LOCK:
            del usage._HANGING[:]
            del usage._HANGING_TOKENS[:]
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.a_text, b_text = self.rig.two_accounts()                # Claude Code is on B; A is an idle saved login
        self.b_text = self.expiring_live(b_text, 3600)              # B's access token lapses within the hour
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.net = FakeNet(self.clock)
        self.net.set(self.access(self.a_text), (200, {}, usage_body(five=20.0, week=30.0,
                                                                   five_reset=time.time() + 3 * 3600)))
        self.net.set(self.access(self.b_text), (200, {}, usage_body(five=95.0, week=40.0,
                                                                   five_reset=time.time() + 3600)))
        autoloop.save_settings(enabled=True, session_at=90.0, weekly_at=95.0, model_at=95.0, model='',
                               strategy='refills-first')
        self.notes = []
        deps = autoloop.Deps(net=self.net, net_ready=lambda: True, load=lambda: 0.5, power=lambda: (True, 100),
                             procs=lambda: 1, disk_free=lambda: 10 ** 12,
                             history_note=lambda text, kind='info', **f: self.notes.append(text),
                             notify_move=lambda note: None)
        self.loop = autoloop.Loop(deps=deps, clock=self.clock, rand=lambda: 0.5)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()
        sessions._reset_for_tests()
        os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def expiring_live(self, text, in_s):
        """The live login and B's saved copy, as they are an hour before the access token lapses (same tokens)."""
        blob = json.loads(text)
        blob['claudeAiOauth']['expiresAt'] = int((time.time() + in_s) * 1000)
        new = json.dumps(blob, separators=(',', ':'))
        self.rig.set_live(new)
        self.rig.kc.put('Switchboard login', 'uuid-b', new)
        with vault.changing(self.ctx) as doc:
            vault.note_login(doc['accounts']['uuid-b'], live.parse(new), None, doc['accounts']['uuid-b']['saved_at'])
        return new

    def access(self, text):
        return json.loads(text)['claudeAiOauth']['accessToken']

    def refresh_fp(self, text):
        return live.fingerprint(json.loads(text)['claudeAiOauth']['refreshToken'])

    def test_a_forced_switch_lands_the_outgoing_login_is_kept_and_upkeep_renews_it_once(self):
        res = self.loop.run_pass()

        # the poller read both accounts through the engine's token join
        self.assertEqual(sorted(set(tok for tok, _ in self.net.calls)),
                         sorted([self.access(self.a_text), self.access(self.b_text)]))
        # the policy forced a switch at B's 5-hour line, and it landed
        self.assertEqual((res.decision.action, res.decision.kind), ('switch', 'forced'), res.decision.reason)
        self.assertIsNotNone(res.switched, res.decision.reason)
        self.assertEqual((res.switched.ok, res.switched.from_id, res.switched.to_id), (True, 'uuid-b', 'uuid-a'))
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth']['refreshToken'],
                         json.loads(self.a_text)['claudeAiOauth']['refreshToken'])
        self.assertEqual(self.rig.config()['oauthAccount']['accountUuid'], 'uuid-a')
        now = api.live()
        self.assertEqual((now.state, now.account_id), (api.PRESENT, 'uuid-a'))
        self.assertEqual(switcher.read_journal(self.ctx)[0].kind, ABSENT)          # the journal is clear
        self.assertIn('Switched Claude Code to a@example.com', [e[0] for e in self.rig.events])
        self.assertEqual(autoloop.load_state()['switches'][-1]['to'], 'uuid-a')
        # B's outgoing login was kept: upkeep renewed exactly that lineage, once, and never A's (Claude Code's now)
        posts = self.rig.server.token_posts()
        self.assertEqual([p[2] for p in posts], [self.refresh_fp(self.b_text)])
        self.assertNotEqual(self.rig.vault('uuid-b'), self.b_text)                  # its successor was saved
        self.assertTrue(self.rig.holds_valid('uuid-a') and self.rig.holds_valid('uuid-b'))

        self.clock.advance(autoloop.UPKEEP_EVERY + 60)                             # the next pass may run upkeep
        res2 = self.loop.run_pass()
        self.assertIsNone(res2.switched, res2.decision.reason)                    # no flapping back
        self.assertEqual(len(self.rig.server.token_posts()), 1)                    # renewed once, not again
        self.assertEqual(len(self.rig.server.used), len(set(self.rig.server.used)))   # no token spent twice
        self.assertTrue(self.rig.holds_valid('uuid-a') and self.rig.holds_valid('uuid-b'))


if __name__ == '__main__':
    unittest.main()
