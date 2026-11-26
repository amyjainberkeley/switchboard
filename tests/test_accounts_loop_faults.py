"""Where switchboard/accounts/ meets the automatic loop. Each class's docstring names the failure its tests rule out."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import time
import unittest

from fakes_autoswitch import FakeClock, FakeEngine, FakeNet, Home, account, make_loop, set_policy, usage_body
from fakes_accounts import Rig
from switchboard.accounts import context, facts, identity, tokens, vault


class TheProfileLookupHasAWholeDeadline(unittest.TestCase):
    """identity.py: the profile lookup is advisory, 5 s, outside every lock. The request it made resolved the
    name inside connect() with no deadline at all, so a broken network held add, keeping and a switch's proof."""

    def setUp(self):
        self.rig = Rig()
        identity._PROFILE_CACHE.clear()
        self.asked = []
        self.request = self.rig.server.request

        def counted(method, url, *a, **kw):
            self.asked.append(url)
            return self.request(method, url, *a, **kw)
        self.rig.server.request = counted

    def tearDown(self):
        self.rig.close()

    def test_a_lookup_that_hangs_comes_back_unresolved_at_its_deadline(self):
        def stalled(method, url, *a, **kw):
            time.sleep(4.0)                                       # a name lookup or a connection that never answers
            return 200, {}, b'{"account": {"uuid": "uuid-late"}}'
        self.rig.server.request = stalled
        t0 = time.monotonic()
        ans = identity.lookup_profile(self.rig.ctx, 'sk-ant-oat01-x', deadline_s=0.5)
        self.assertEqual((ans.status, ans.why), ('unresolved', 'timeout'))
        self.assertLess(time.monotonic() - t0, 3.0)

    def test_a_name_that_does_not_resolve_sends_nothing(self):
        self.rig.server.dns_ok = False
        ans = identity.lookup_profile(self.rig.ctx, 'sk-ant-oat01-y')
        self.assertEqual((ans.status, ans.why), ('unresolved', 'dns'))
        self.assertEqual(self.asked, [])

    def test_a_good_answer_still_comes_through(self):
        text = self.rig.server.add('uuid-a', 'a@x.example', 'org-a')
        ans = identity.lookup_profile(self.rig.ctx, json.loads(text)['claudeAiOauth']['accessToken'])
        self.assertEqual((ans.status, ans.uuid, ans.email), ('resolved', 'uuid-a', 'a@x.example'))
        self.assertEqual(self.asked, [facts.PROFILE_URL])


class ASwitchMadeElsewhereWhileTheLoopDecided(Home):
    """The loop drops its switch when someone switched since its snapshot (its own rule), but it looked only at its
    own record: a switch from the page or a terminal (a switch line in history.jsonl) went unseen, and the loop then
    moved Claude Code off the account the person had just chosen."""

    def test_a_switch_from_the_page_since_the_snapshot_drops_the_loops_own(self):
        set_policy()
        clock = FakeClock()
        engine = FakeEngine([account('a1', 1), account('a2', 2), account('a3', 3)], 'a1', clock)
        net = FakeNet(clock)
        for aid, five in (('a1', 95.0), ('a2', 10.0), ('a3', 30.0)):
            net.set('tok-' + aid, (200, {}, usage_body(five=five, week=20.0, five_reset=clock.wall() + 3600)))

        def page_switches_meanwhile():                     # asked between the loop's decision and its switch
            with open(self.data('history.jsonl'), 'a') as f:
                f.write(json.dumps({'t': clock.wall(), 'kind': 'switch',
                                    'text': 'Switched Claude Code to a3@acct.example'}) + '\n')
            engine.active = 'a3'
            return 10 ** 10
        loop = make_loop(engine, clock, net=net, disk_free=page_switches_meanwhile)
        res = loop.run_pass()
        self.assertEqual((res.decision.action, res.decision.target), ('switch', 'a2'))
        self.assertIsNone(res.switched)
        self.assertEqual(engine.switches, [])

    def test_an_older_switch_or_one_dated_far_ahead_does_not_hold_it(self):
        set_policy()
        clock = FakeClock()
        engine = FakeEngine([account('a1', 1), account('a2', 2)], 'a1', clock)
        net = FakeNet(clock)
        net.set('tok-a1', (200, {}, usage_body(five=95.0, week=20.0, five_reset=clock.wall() + 3600)))
        net.set('tok-a2', (200, {}, usage_body(five=10.0, week=20.0, five_reset=clock.wall() + 3600)))
        with open(self.data('history.jsonl'), 'a') as f:
            for t in (clock.wall() - 3600, clock.wall() + 86400):    # an hour ago; a clock that moved back a day
                f.write(json.dumps({'t': t, 'kind': 'switch', 'text': 'Switched Claude Code to a1@acct.example'}) +
                        '\n')
        res = make_loop(engine, clock, net=net).run_pass()
        self.assertIsNotNone(res.switched, res.decision.reason)
        self.assertEqual([s['to'] for s in engine.switches], ['a2'])


class TheRecoveryStashReadsBack(unittest.TestCase):
    """The recovery stash is where a refreshed login goes when its own slot can't take it: the one copy of it. It
    trusted the Keychain's "done" without reading back, so a write the tool failed without saying so (`security -i`
    reports a failure only in words) lost that login."""

    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.text = self.rig.server.add('uuid-a', 'a@example.com', 'org-a')

    def tearDown(self):
        self.rig.close()

    def test_a_copy_the_keychain_said_it_kept_but_did_not_goes_to_a_file(self):
        self.rig.kc.rule('write', service=vault.SERVICE_RECOVERY, why='silent', times=1)
        ok, where = vault.stash(self.ctx, 'successor:uuid-a', self.text, 'uuid-a', 'save failed', supersedes='x')
        self.assertEqual((ok, where), (True, 'file'))
        got = vault.read_stash(self.ctx, 'successor:uuid-a')
        self.assertEqual((got.kind, got.value), ('present', self.text))
        self.assertTrue(self.rig.holds_valid('uuid-a'))

    def test_a_copy_that_reads_back_stays_in_the_keychain(self):
        ok, where = vault.stash(self.ctx, 'successor:uuid-a', self.text, 'uuid-a', 'save failed', supersedes='x')
        self.assertEqual((ok, where), (True, 'keychain'))
        self.assertEqual(vault.read_stash(self.ctx, 'successor:uuid-a').value, self.text)


class UpkeepLeavesTheActiveLineageAlone(unittest.TestCase):
    """Claude Code owns the active account's lineage, so upkeep skips it, but it set no wait: every upkeep pass (each
    10 minutes) read that saved login and the live login again and rewrote accounts.json, so its five rolling backups
    held less than an hour."""

    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.rig.two_accounts()                                      # Claude Code is on B
        self.rig.set_saved_expiry('uuid-b', time.time() + 1800)      # B's saved copy is due; Claude Code holds it
        tokens.WAKE.reset()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def test_the_next_passes_neither_read_it_nor_rewrite_the_account_list(self):
        tokens.upkeep_pass(self.ctx, 60)
        self.assertEqual(self.rig.doc()['upkeep']['uuid-b']['last_outcome'], 'skipped')
        reads = self.rig.kc.count('read', 'Switchboard login')
        written = os.stat(vault.store_path(self.ctx)).st_mtime_ns
        for _ in range(3):
            tokens.upkeep_pass(self.ctx, 60)
        self.assertEqual(self.rig.kc.count('read', 'Switchboard login'), reads)
        self.assertEqual(os.stat(vault.store_path(self.ctx)).st_mtime_ns, written)
        self.assertEqual(self.rig.server.token_posts(), [])
        wait = self.rig.doc()['upkeep']['uuid-b']['next_at'] - time.time()
        self.assertTrue(0 < wait <= tokens.SKIP_WAIT_S, wait)

    def test_an_idle_account_due_is_still_renewed_at_once(self):
        self.rig.set_saved_expiry('uuid-a', time.time() + 1800)
        notes = tokens.upkeep_pass(self.ctx, 60)
        self.assertIn('Renewed the saved login of a@example.com', notes)
        self.assertEqual(len(self.rig.server.token_posts()), 1)


if __name__ == '__main__':
    unittest.main()
