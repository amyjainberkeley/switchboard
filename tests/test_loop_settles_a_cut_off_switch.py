import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A switch that was cut off (a kill, a crash) leaves its journal, and while the journal is there the put-back leaves
# every loss of Claude Code's sign-in ("a switch is part-way"), at every look, for as long as the file stays, and the
# loop's start settles it only once. The loop's pass settles a journal last written more than
# JOURNAL_STALE_S ago through api.resolve_journal, under the switch lock (a switch under way holds it), once a pass, and
# says what was done once, in the engine's words, in its log and in History. A younger journal is never touched. The
# real account engine on its stand-ins (fakes_accounts), a switch cut off at a step by the switch's own test hook,
# and the automatic loop over it, with a fake clock. Made-up *.example accounts; no Keychain or network is reached.
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock, FakeNet, set_policy
from fakes_accounts import Rig
from switchboard.accounts import autoloop, context, live, putback, roster, sessions, switcher, tokens, vault

A, B = 'ana@work.example', 'ana@home.example'
MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}


class Cut(BaseException):
    """A switch cut off at a step, as by a kill: nothing after it runs, and its journal stays."""


class Case(unittest.TestCase):
    """The real account engine on its stand-ins, Claude Code on B, A signed in too, automatic switching off."""

    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='cut-off-switch-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        sessions._reset_for_tests()
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        putback._KEPT.clear()
        roster.forget_live()
        self.a_text = self.rig.keep('uuid-a', A, 'org-a')
        self.clock.advance(1.0)                 # two sign-ins never share an end stamp to the millisecond
        self.b_text = self.rig.keep('uuid-b', B, 'org-b', siblings=MCP)
        self.told, self.notes, self.moved = [], [], []
        patcher = mock.patch.object(autoloop, 'notify_put_back', self.told.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        deps = autoloop.Deps(net=FakeNet(self.clock), net_ready=lambda: True, load=lambda: 0.5,
                             power=lambda: (True, 100), procs=lambda: 1, disk_free=lambda: 10 ** 12,
                             history_note=lambda text, kind='info', **f: self.notes.append((text, kind, f)),
                             notify=self.told.append, notify_move=self.moved.append)
        self.loop = autoloop.Loop(deps=deps, clock=self.clock, rand=lambda: 0.5)
        set_policy(enabled=False)
        self.journal = switcher.journal_path(self.ctx)

    def tearDown(self):
        switcher._HOOK = None
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        putback.reset()
        putback._KEPT.clear()
        roster.forget_live()
        self.rig.close()
        sessions._reset_for_tests()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    # the stand-ins' moves

    def cut_off(self, step):
        """The page's switch to A, cut off right after `step`; its journal stays."""
        def hook(name):
            if name == step:
                raise Cut(step)
        switcher._HOOK = hook
        try:
            with self.assertRaises(Cut):
                switcher.switch(self.ctx, 'uuid-a', kind='manual', source='page')
        finally:
            switcher._HOOK = None
        roster.forget_live()
        self.assertTrue(os.path.exists(self.journal))

    def age(self, seconds):
        """The journal was last written `seconds` ago by the fake clock."""
        t = self.clock.wall() - seconds
        os.utime(self.journal, (t, t))

    def lose_login(self):
        """What many processes saving at once can leave: the item deleted, then saved again without the login."""
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))
        roster.forget_live()

    def label(self):
        return (self.rig.config().get('oauthAccount') or {}).get('accountUuid')

    def login_of(self):
        """Whose login Claude Code's item holds: 'uuid-a', 'uuid-b', or None (no account login)."""
        text = self.rig.live_text()
        got = live.parse(text) if text else None
        if got is None or got.kind != 'oauth':
            return None
        for aid, saved in (('uuid-a', self.a_text), ('uuid-b', self.b_text)):
            if got.refresh_token == live.parse(saved).refresh_token:
                return aid
        return 'another'

    # what was said

    def journal_lines(self):
        with open(os.path.join(os.environ['SWITCHBOARD_HOME'], autoloop.LOG)) as f:
            return [line for line in f.read().splitlines() if 'journal' in line]

    def repairs(self):
        return [n for n in self.notes if n[2].get('h') == 'repair']

    def run_until_put_back(self, most=4):
        for _ in range(most):
            self.loop.run_pass()
            if self.login_of() is not None:
                return True
            self.clock.advance(putback.PUT_BACK_AFTER_S + 1)
        return False


class AYoungJournal(Case):
    def test_a_journal_ten_seconds_old_is_never_touched_and_the_put_back_waits(self):
        self.cut_off('credential_written')
        self.lose_login()
        self.age(10.0)
        with open(self.journal, 'rb') as f:
            before = f.read()
        stamp = os.stat(self.journal).st_mtime_ns
        for _ in range(3):
            self.loop.run_pass()
            self.assertEqual(self.loop.look.reason, 'a switch is part-way')
            self.clock.advance(putback.PUT_BACK_AFTER_S + 1)
        with open(self.journal, 'rb') as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(os.stat(self.journal).st_mtime_ns, stamp)
        self.assertIsNone(self.login_of())                                  # left while the switch may run
        self.assertEqual(self.repairs(), [])
        self.assertEqual([e for e in self.rig.events if e[2].get('h') == 'repair'], [])


class AStaleJournal(Case):
    def test_with_the_targets_login_it_is_finished_said_once_and_a_later_loss_is_put_back(self):
        self.cut_off('credential_written')                                  # A's login written, the label not yet
        self.age(200.0)
        self.loop.run_pass()
        self.assertFalse(os.path.exists(self.journal))
        self.assertEqual((self.login_of(), self.label()), ('uuid-a', 'uuid-a'))
        said = 'Repaired the cut-off switch: Claude Code is on %s' % A
        self.assertEqual(self.repairs(), [(said, 'info', {'h': 'repair', 'by': 'auto'})])
        self.assertEqual(self.journal_lines(), [self.journal_lines()[0]])
        self.assertTrue(self.journal_lines()[0].endswith('journal: Repaired the cut-off switch: Claude Code is on '
                                                         '<account>'), self.journal_lines())
        self.assertEqual([e for e in self.rig.events if e[2].get('h') == 'repair'], [])    # said by the loop, once
        self.clock.advance(61.0)
        self.lose_login()
        self.assertTrue(self.run_until_put_back())
        self.assertEqual(self.login_of(), 'uuid-a')
        self.assertEqual(len(self.repairs()), 1)

    def test_with_the_outgoing_login_it_is_rolled_back_said_once_and_a_later_loss_is_put_back(self):
        self.cut_off('config_written')                                      # A's login and label written
        self.rig.set_live(self.b_text)                                      # a roll back cut off between its writes:
        roster.forget_live()                                                # B's login back, the label still A's
        self.assertEqual(self.label(), 'uuid-a')
        self.age(200.0)
        self.loop.run_pass()
        self.assertFalse(os.path.exists(self.journal))
        self.assertEqual((self.login_of(), self.label()), ('uuid-b', 'uuid-b'))
        self.assertEqual(self.repairs(), [('Repaired the switch that was cut off', 'info',
                                           {'h': 'repair', 'by': 'auto'})])
        self.clock.advance(61.0)
        self.lose_login()
        self.assertTrue(self.run_until_put_back())
        self.assertEqual(self.login_of(), 'uuid-b')

    def test_left_while_the_login_is_lost_it_is_put_away_and_the_put_back_acts_at_the_next_pass(self):
        self.cut_off('credential_written')
        self.lose_login()
        self.age(200.0)
        first = self.loop.run_pass()
        self.assertFalse(os.path.exists(self.journal))
        self.assertEqual(len(self.repairs()), 1)
        self.assertEqual(self.repairs()[0][0], 'Put away the record of a switch that was cut off: Claude Code holds '
                                               'no login now, so nothing was changed (the login that was in use is '
                                               'in a safety copy).')
        self.assertNotEqual(self.loop.look.reason, 'a switch is part-way')
        self.assertIsNone(first.switched)
        self.clock.advance(putback.PUT_BACK_AFTER_S + 1)
        self.loop.run_pass()
        self.assertEqual(self.login_of(), 'uuid-b')                         # the settings still name B
        self.assertEqual([r.action for r in self.told], ['put_back'])

    def test_one_that_cannot_be_settled_yet_is_said_once_then_settled(self):
        self.cut_off('credential_written')
        self.age(200.0)
        self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=-1)    # macOS does not answer
        for _ in range(3):
            self.loop.run_pass()
            self.clock.advance(61.0)
        self.assertTrue(os.path.exists(self.journal))
        waiting = 'A switch was cut off; Switchboard will settle it as soon as the Keychain answers.'
        self.assertEqual([n[0] for n in self.repairs()], [waiting])
        self.assertEqual(len(self.journal_lines()), 1, self.journal_lines())
        del self.rig.kc.rules[:]
        roster.forget_live()
        self.loop.run_pass()
        self.assertFalse(os.path.exists(self.journal))
        self.assertEqual([n[0] for n in self.repairs()],
                         [waiting, 'Repaired the cut-off switch: Claude Code is on %s' % A])

    def test_while_a_switch_holds_the_switch_lock_the_journal_is_not_touched(self):
        self.cut_off('credential_written')
        self.age(200.0)
        held, release = threading.Event(), threading.Event()

        def a_switch_under_way():
            with vault.switch_lock(self.ctx, timeout=5.0):
                held.set()
                release.wait(60)
        t = threading.Thread(target=a_switch_under_way)
        t.start()
        try:
            self.assertTrue(held.wait(30))
            self.loop.run_pass()
            self.assertTrue(os.path.exists(self.journal))
            self.assertEqual(self.repairs(), [])
        finally:
            release.set()
            t.join(30)
        self.clock.advance(61.0)
        self.loop.run_pass()
        self.assertFalse(os.path.exists(self.journal))
        self.assertEqual(len(self.repairs()), 1)


class OnOneFolder(object):
    """The loop's own data folder and the engine's are one folder, as on a Mac, so both settlers of a cut-off switch
    look at the one journal: the pass's first step (_settle_journal) and the upkeep's (_settle_cut_off). A switch that
    ended on a Keychain write macOS did not confirm in time leaves a young journal while that write may still land:
    neither may touch it, and an old one is still said once."""

    def setUp(self):
        super().setUp()
        os.makedirs(self.rig.data, exist_ok=True)
        os.environ['SWITCHBOARD_HOME'] = self.rig.data      # tearDown puts the earlier one back
        set_policy(enabled=False)
        self.loop = autoloop.Loop(deps=self.loop.d, clock=self.clock, rand=lambda: 0.5)


class AYoungJournalOnOneFolder(OnOneFolder, AYoungJournal):
    pass


class AStaleJournalOnOneFolder(OnOneFolder, AStaleJournal):
    pass


class TheNumber(unittest.TestCase):
    def test_a_journal_is_stale_after_two_minutes(self):
        self.assertEqual(autoloop.JOURNAL_STALE_S, 120.0)


if __name__ == '__main__':
    unittest.main()
