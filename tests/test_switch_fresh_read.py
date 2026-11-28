import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A switch composes what it writes from its read of Claude Code's item, then keeps the outgoing login, saves it for
# its account and waits for Claude Code's save lock (seconds). A Claude Code save in that time (a renewal of the
# outgoing login, a connector's login) would be written over. So, holding the save lock, the switch reads the item
# again and writes only when it still holds exactly the text it composed from; otherwise nothing is written and the
# next pass decides again. Stand-ins only (fakes_accounts), a fake clock, made-up *.example accounts.
import json
import os
import random
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig
from switchboard.accounts import context, facts, live, putback, switcher
from switchboard.accounts.api import AccountsError

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='switch-fresh-read-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        self.lock_path = facts.storage_lock(self.rig.env, self.rig.home)
        self.a_text = self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)     # Claude Code is on B
        self.calls = []                 # each call on Claude Code's item: (verb, our save lock held)

        def on_call(verb, service, account):
            if service == self.rig.service:
                self.calls.append((verb, os.path.isdir(self.lock_path)))
        self.rig.kc.on_call = on_call

    def tearDown(self):
        self.use.__exit__(None, None, None)
        putback.reset()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def label(self):
        return (self.rig.config().get('oauthAccount') or {}).get('accountUuid')

    def live_writes(self):
        return self.rig.kc.count('write', self.rig.service)

    def before_the_lock(self, save):
        """Claude Code saves its item (save()) once, right before the switch takes the save lock for its write: after
        the switch's reads, the journal, the safety copy and the keep of the outgoing login."""
        real = live.storage_lock
        calls = {'n': 0}

        def wrapper(ctx, env=None, budget_s=facts.STORAGE_LOCK_BUDGET_S):
            calls['n'] += 1
            if calls['n'] == 1:
                save()
            return real(ctx, env, budget_s)
        return mock.patch.object(live, 'storage_lock', side_effect=wrapper)

    def assert_not_made(self, e, before_writes, item):
        self.assertEqual(e.exception.kind, 'claude_busy')
        self.assertIn('Claude Code was saving its login, so nothing was changed', str(e.exception))
        self.assertEqual(self.live_writes(), before_writes)              # nothing written to Claude Code
        self.assertEqual(self.rig.live_text(), item)                      # its save is there, byte for byte
        self.assertEqual(self.label(), 'uuid-b')
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
        self.assertFalse(os.path.exists(self.lock_path), 'the save lock was left behind')


class ASaveBetweenTheFirstReadAndTheLock(Base):
    def test_a_renewal_of_the_outgoing_login_is_never_written_over_and_reaches_its_slot_at_the_next_switch(self):
        got = {}

        def renew():
            self.clock.advance(60.0)                # a later access expiry: the newer generation by Claude Code's rule
            got['renewed'] = self.rig.claude_renews()
        before = self.live_writes()
        with self.before_the_lock(renew):
            with self.assertRaises(AccountsError) as e:
                switcher.switch(self.ctx, 'uuid-a')
        self.assert_not_made(e, before, got['renewed'])
        self.assertTrue(self.rig.holds_valid('uuid-b'))                  # the renewed login is not lost
        res = switcher.switch(self.ctx, 'uuid-a')                         # the next switch goes through
        self.assertTrue(res.ok, res.note)
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, live.parse(self.a_text).fingerprint)
        self.assertEqual(live.parse(self.rig.vault('uuid-b')).fingerprint, live.parse(got['renewed']).fingerprint)
        self.assertTrue(self.rig.holds_valid('uuid-a'))
        self.assertTrue(self.rig.holds_valid('uuid-b'))

    def test_a_connector_login_saved_meanwhile_is_never_written_over_and_travels_with_the_next_switch(self):
        connector = {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1},
                     'mail-server': {'accessToken': 'mcp-made-up-2', 'expiresAt': 2}}
        got = {}

        def connect():
            blob = json.loads(self.rig.live_text())
            blob['mcpOAuth'] = connector
            got['item'] = json.dumps(blob, separators=(',', ':'))
            self.rig.set_live(got['item'])
        before = self.live_writes()
        with self.before_the_lock(connect):
            with self.assertRaises(AccountsError) as e:
                switcher.switch(self.ctx, 'uuid-a')
        self.assert_not_made(e, before, got['item'])
        res = switcher.switch(self.ctx, 'uuid-a')
        self.assertTrue(res.ok, res.note)
        blob = json.loads(self.rig.live_text())
        self.assertEqual(blob['mcpOAuth'], connector)                     # the connector login stays with the Mac
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, live.parse(self.a_text).fingerprint)

    def test_the_item_gone_meanwhile_is_not_written_either(self):
        before = self.live_writes()
        with self.before_the_lock(lambda: self.rig.sec.drop(self.rig.service, self.rig.account)):
            with self.assertRaises(AccountsError) as e:
                switcher.switch(self.ctx, 'uuid-a')
        self.assert_not_made(e, before, None)

    def test_the_read_under_the_lock_is_made_and_an_unchanged_item_is_switched(self):
        res = switcher.switch(self.ctx, 'uuid-a')
        self.assertTrue(res.ok, res.note)
        first_write = self.calls.index(('write', True))
        self.assertEqual(self.calls[first_write - 1], ('read', True))   # read again, holding the save lock
        self.assertEqual(self.label(), 'uuid-a')

    def test_a_read_under_the_lock_that_gets_no_answer_writes_nothing(self):
        def no_answer():
            self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=1)
        before = self.live_writes()
        with self.before_the_lock(no_answer):
            with self.assertRaises(AccountsError) as e:
                switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'keychain_slow')
        self.assertEqual(self.live_writes(), before)
        self.assertEqual(self.rig.live_text(), self.b_text)
        self.assertEqual(self.label(), 'uuid-b')
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
        self.assertFalse(os.path.exists(self.lock_path))


class ManySwitchesWithSavesInBetween(Base):
    """2,000 switch attempts from a fixed seed, each with or without one Claude Code save right before the lock: a
    renewal, a connector login, a save of the very same bytes, or none. The switch writes exactly when the item still
    holds what it read; whatever Claude Code saved is never lost."""

    CASES = 2000

    def test_the_rule_holds_over_many_cases(self):
        rnd = random.Random(3601)
        accounts = {'uuid-a': 'ana@work.example', 'uuid-b': 'ana@home.example'}
        on = 'uuid-b'
        made = refused = 0
        for n in range(self.CASES):
            target = 'uuid-a' if on == 'uuid-b' else 'uuid-b'
            kind = rnd.choice(('none', 'none', 'renew', 'connector', 'same'))
            seen = {}

            def save():
                seen['before'] = self.rig.live_text()
                if kind == 'renew':
                    self.rig.claude_renews()
                elif kind == 'connector':
                    blob = json.loads(self.rig.live_text())
                    blob['mcpOAuth'] = {'server-%d' % n: {'accessToken': 'mcp-made-up-%d' % n, 'expiresAt': n}}
                    self.rig.set_live(json.dumps(blob, separators=(',', ':')))
                elif kind == 'same':
                    self.rig.set_live(self.rig.live_text())
                seen['after'] = self.rig.live_text()
            self.clock.advance(rnd.choice((1.0, 30.0, 600.0)))
            writes = self.live_writes()
            try:
                with self.before_the_lock(save):
                    res = switcher.switch(self.ctx, target)
                err = None
            except AccountsError as e:
                res, err = None, e
            changed = seen['after'] != seen['before']
            where = 'case %d (%s, to %s)' % (n, kind, accounts[target])
            if changed:
                self.assertIsNotNone(err, where)
                self.assertEqual(err.kind, 'claude_busy', where)
                self.assertEqual(self.live_writes(), writes, where)
                self.assertEqual(self.rig.live_text(), seen['after'], where)
                self.assertEqual(self.label(), on, where)
                refused += 1
            else:
                self.assertIsNone(err, '%s: %s' % (where, err))
                self.assertTrue(res.ok, '%s: %s' % (where, res.note))
                live_blob = json.loads(self.rig.live_text())
                before_blob = json.loads(seen['before'])
                self.assertEqual(live_blob.get('mcpOAuth'), before_blob.get('mcpOAuth'), where)
                self.assertEqual(self.label(), target, where)
                on = target
                made += 1
            self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)), where)
            for uuid in accounts:
                self.assertTrue(self.rig.holds_valid(uuid), '%s: %s lost' % (where, uuid))
        self.assertGreater(made, self.CASES // 3)
        self.assertGreater(refused, self.CASES // 5)


if __name__ == '__main__':
    unittest.main()
