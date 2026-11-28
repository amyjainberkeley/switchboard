import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Claude Code saves its Keychain item only while it holds <secure-storage folder>/.storage-write.lock (read in its
# program text, 2.1.329 to 2.1.331). Two saves of one item at one moment make macOS refuse one, and Claude Code answers
# a refused save by deleting its item. So every write of that item Switchboard makes (a switch, its read-back and its
# roll back, a put-back) is made under that lock: taken last, let go first, never held while waiting for another lock
# or for the network. Stand-ins only (fakes_accounts), a fake clock for every wait, made-up *.example accounts.
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig
from switchboard.accounts import ccfiles, context, facts, live, putback, switcher, vault
from switchboard.accounts.api import AccountsError

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}


class Holder:
    """Another process holding Claude Code's save lock: the folder made, and touched twice a second (real time, as a
    live proper-lockfile holder keeps it fresh), until let go."""

    def __init__(self, path, age_s=0.0):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.mkdir(path)
        self.ino = os.lstat(path).st_ino
        self.stop = threading.Event()
        if age_s:
            t = time.time() - age_s
            os.utime(path, (t, t))          # a holder that died: never touched again
            return
        self.thread = threading.Thread(target=self._touch, daemon=True)
        self.thread.start()

    def _touch(self):
        while not self.stop.wait(0.5):
            try:
                os.utime(self.path)
            except OSError:
                return

    def let_go(self):
        self.stop.set()
        try:
            os.rmdir(self.path)
        except OSError:
            pass

    def holds(self):
        try:
            return os.lstat(self.path).st_ino == self.ino
        except OSError:
            return False


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='storage-lock-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.let_go_at = None           # fake seconds: the holder lets go once a wait has run this long
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        self.lock_path = facts.storage_lock(self.rig.env, self.rig.home)
        self.holder = None
        self.seen = []                  # at each write or delete of Claude Code's item: (lock folder there, ours)

        def on_call(verb, service, account):
            if verb in ('write', 'delete') and service == self.rig.service:
                there = os.path.isdir(self.lock_path)
                theirs = self.holder is not None and self.holder.holds()
                self.seen.append((verb, there, there and not theirs))
        self.rig.kc.on_call = on_call

    def tearDown(self):
        if self.holder is not None:
            self.holder.let_go()
        self.use.__exit__(None, None, None)
        putback.reset()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sleep(self, s):
        self.clock.sleep(s)
        if self.let_go_at is not None and self.holder is not None and self.clock.mono() >= self.let_go_at:
            self.holder.let_go()

    def hold(self, age_s=0.0, let_go_after=None):
        self.holder = Holder(self.lock_path, age_s=age_s)
        if let_go_after is not None:
            self.let_go_at = self.clock.mono() + let_go_after

    def live_writes(self):
        return self.rig.kc.count('write', self.rig.service)

    def assert_lock_gone(self):
        self.assertFalse(os.path.exists(self.lock_path), 'the save lock was left behind')
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.lock_path)) if '.storage-write' in n], [])

    def assert_every_write_under_our_lock(self):
        self.assertTrue(self.seen)
        for verb, there, ours in self.seen:
            self.assertTrue(there and ours, '%s of the item without holding the save lock: %r' % (verb, self.seen))


class TheLockItself(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='storage-lock-unit-')
        self.home = os.path.join(self.tmp, 'home')
        os.makedirs(os.path.join(self.home, '.claude'))
        self.clock = FakeClock(wall=time.time(), mono=1000.0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_its_path_is_the_secure_storage_folder_like_its_neighbours(self):
        env = {'USER': 'tester'}
        self.assertEqual(facts.storage_lock(env, self.home), os.path.join(self.home, '.claude', '.storage-write.lock'))
        env = {'CLAUDE_CONFIG_DIR': '/made/up/config'}
        self.assertEqual(facts.storage_lock(env, self.home), '/made/up/config/.storage-write.lock')
        env = {'CLAUDE_CONFIG_DIR': '/made/up/config', 'CLAUDE_SECURESTORAGE_CONFIG_DIR': '/made/up/secure'}
        self.assertEqual(facts.storage_lock(env, self.home), '/made/up/secure/.storage-write.lock')
        env = {'CLAUDE_CONFIG_DIR': '/made/up/config', 'CLAUDE_SECURESTORAGE_CONFIG_DIR': ''}
        self.assertEqual(facts.storage_lock(env, self.home), os.path.join(self.home, '.claude', '.storage-write.lock'))
        self.assertEqual(os.path.dirname(facts.storage_lock(env, self.home)), os.path.dirname(
            facts.refresh_lock(env, self.home)))
        self.assertEqual(facts.STORAGE_LOCK_STALE_S, 15.0)
        self.assertEqual(facts.FACTS['locks.storage'][1], '2.1.329')

    def lock(self, budget_s=facts.STORAGE_LOCK_BUDGET_S):
        return ccfiles.storage_lock({}, self.home, budget_s=budget_s, mono=self.clock.mono, sleep=self.clock.sleep)

    def test_a_live_holder_past_the_budget_raises_lock_busy_and_its_lock_is_untouched(self):
        holder = Holder(facts.storage_lock({}, self.home))
        try:
            start = self.clock.mono()
            with self.assertRaises(ccfiles.LockBusy):
                with self.lock():
                    self.fail('taken while held')
            self.assertGreaterEqual(self.clock.mono() - start, facts.STORAGE_LOCK_BUDGET_S)
            self.assertTrue(holder.holds())
        finally:
            holder.let_go()

    def test_a_stale_holder_is_taken_over_and_the_lock_is_gone_after(self):
        holder = Holder(facts.storage_lock({}, self.home), age_s=facts.STORAGE_LOCK_STALE_S + 5)
        with self.lock() as got:
            self.assertTrue(got.still_held())
            self.assertFalse(holder.holds())
        self.assertFalse(os.path.exists(holder.path))

    def test_a_holder_younger_than_its_stale_time_is_never_taken_over(self):
        holder = Holder(facts.storage_lock({}, self.home), age_s=facts.STORAGE_LOCK_STALE_S - 5)
        try:
            with self.assertRaises(ccfiles.LockBusy):
                with self.lock(budget_s=2.0):
                    pass
            self.assertTrue(holder.holds())
        finally:
            holder.let_go()

    def test_the_lock_is_gone_after_an_exception_inside(self):
        path = facts.storage_lock({}, self.home)
        with self.assertRaises(RuntimeError):
            with self.lock():
                self.assertTrue(os.path.isdir(path))
                raise RuntimeError('made up')
        self.assertFalse(os.path.exists(path))


class WriteLive(Base):
    def setUp(self):
        super().setUp()
        self.text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b')

    def test_a_write_takes_the_lock_and_lets_it_go(self):
        self.assertTrue(live.write_live(self.ctx, self.text).ok)
        self.assert_every_write_under_our_lock()
        self.assert_lock_gone()

    def test_a_caller_that_holds_the_lock_passes_it_and_it_is_not_taken_twice(self):
        with live.storage_lock(self.ctx) as held:
            with mock.patch.object(ccfiles.DirLock, 'acquire', side_effect=AssertionError('taken twice')):
                self.assertTrue(live.write_live(self.ctx, self.text, held=held).ok)
            self.assertTrue(held.still_held())
        self.assert_lock_gone()

    def test_a_lock_held_by_claude_code_past_the_budget_writes_nothing(self):
        self.hold()
        before = self.live_writes()
        with self.assertRaises(ccfiles.LockBusy):
            live.write_live(self.ctx, self.text)
        self.assertEqual(self.live_writes(), before)
        self.assertTrue(self.holder.holds())

    def test_a_holder_that_lets_go_after_a_second_lets_the_write_through(self):
        self.hold(let_go_after=1.0)
        start = self.clock.mono()
        self.assertTrue(live.write_live(self.ctx, self.text).ok)
        self.assertGreaterEqual(self.clock.mono() - start, 1.0)
        self.assert_every_write_under_our_lock()
        self.assert_lock_gone()

    def test_a_write_that_raises_still_lets_the_lock_go(self):
        def boom(verb, service, account):
            if verb == 'write':
                raise RuntimeError('made up')
        self.rig.kc.on_call = boom
        with self.assertRaises(RuntimeError):
            live.write_live(self.ctx, self.text)
        self.assert_lock_gone()


class TheSwitch(Base):
    def setUp(self):
        super().setUp()
        self.a_text = self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)     # Claude Code is on B

    def label(self):
        return (self.rig.config().get('oauthAccount') or {}).get('accountUuid')

    def test_a_switch_writes_under_the_lock_and_lets_it_go(self):
        res = switcher.switch(self.ctx, 'uuid-a')
        self.assertTrue(res.ok, res.note)
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, live.parse(self.a_text).fingerprint)
        self.assert_every_write_under_our_lock()
        self.assert_lock_gone()

    def test_while_claude_code_holds_the_lock_the_switch_waits_writes_nothing_and_is_undone_with_plain_words(self):
        self.hold()
        before = self.live_writes()
        with self.assertRaises(AccountsError) as e:
            switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'claude_busy')
        self.assertIn('Claude Code was saving its login', str(e.exception))
        self.assertEqual(self.live_writes(), before)
        self.assertEqual(self.rig.live_text(), self.b_text)
        self.assertEqual(self.label(), 'uuid-b')
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
        self.assertTrue(self.holder.holds())
        self.assertEqual(self.rig.vault('uuid-a'), self.a_text)
        # B's saved login keeps its account's parts (vault.bound_text); the Mac-wide parts are in their own record
        self.assertEqual(self.rig.vault('uuid-b'), vault.bound_text(self.b_text))
        self.assertEqual(json.loads(self.rig.sec.get(vault.SERVICE_SHARED, self.rig.service)), MCP)

    def test_a_holder_that_lets_go_after_a_second_lets_the_switch_through(self):
        self.hold(let_go_after=1.0)
        res = switcher.switch(self.ctx, 'uuid-a')
        self.assertTrue(res.ok, res.note)
        self.assertEqual(self.label(), 'uuid-a')
        self.assert_every_write_under_our_lock()
        self.assert_lock_gone()

    def test_a_stale_holder_is_taken_over(self):
        self.hold(age_s=facts.STORAGE_LOCK_STALE_S + 5)
        res = switcher.switch(self.ctx, 'uuid-a')
        self.assertTrue(res.ok, res.note)
        self.assertTrue(any('Took over a stale lock' in x for x in self.rig.logs), self.rig.logs)
        self.assert_lock_gone()

    def test_a_refused_write_lets_the_lock_go(self):
        self.rig.kc.rule('write', self.rig.service, why='denied')
        with self.assertRaises(AccountsError):
            switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(self.rig.live_text(), self.b_text)
        self.assert_lock_gone()

    def test_a_write_refused_because_another_process_saved_at_that_moment_says_so(self):
        self.rig.kc.rule('write', self.rig.service, why='collided')
        with self.assertRaises(AccountsError) as e:
            switcher.switch(self.ctx, 'uuid-a')
        self.assertEqual(e.exception.kind, 'claude_busy')
        self.assertIn('Claude Code was saving its login', str(e.exception))
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
        self.assert_lock_gone()

    def test_an_exception_during_the_write_lets_the_lock_go(self):
        def boom(verb, service, account):
            if verb == 'write' and service == self.rig.service:
                raise OSError('made up')
        self.rig.kc.on_call = boom
        with self.assertRaises(AccountsError):
            switcher.switch(self.ctx, 'uuid-a')
        self.assert_lock_gone()

    def test_the_roll_back_writes_under_the_lock_too(self):
        state = {'written': False}
        record = self.rig.kc.on_call

        def hook(verb, service, account):
            record(verb, service, account)
            if service != self.rig.service:
                return
            if verb == 'write':
                state['written'] = True
            elif verb == 'read' and state['written'] and not state.get('meddled'):
                state['meddled'] = True                     # a save the read-back does not expect
                self.rig.set_live(self.b_text)
        self.rig.kc.on_call = hook
        res = switcher.switch(self.ctx, 'uuid-a')
        self.assertFalse(res.ok)
        self.assertTrue(res.rolled_back)
        self.assertEqual(self.rig.live_text(), self.b_text)
        self.assertEqual(len(self.seen), 2)                 # the switch's write and the roll back's
        self.assert_every_write_under_our_lock()
        self.assert_lock_gone()

    def test_a_roll_back_that_cannot_get_the_lock_leaves_the_journal_for_later(self):
        # the config lock is held, so the label can't be written and the switch is rolled back; the save lock is
        # then held too, so the roll back waits and gives up: nothing is written without it
        real_lock = live.storage_lock
        calls = {'n': 0}

        def second_one_busy(ctx, env=None, budget_s=facts.STORAGE_LOCK_BUDGET_S):
            calls['n'] += 1
            if calls['n'] == 2:
                self.hold()
            return real_lock(ctx, env, budget_s)
        with mock.patch.object(live, 'storage_lock', side_effect=second_one_busy), \
                mock.patch.object(ccfiles, 'config_lock', side_effect=ccfiles.LockBusy('made up')):
            res = switcher.switch(self.ctx, 'uuid-a')
        self.assertFalse(res.ok)
        self.assertFalse(res.rolled_back)
        self.assertEqual(len(self.seen), 1)                 # the switch's own write; the roll back wrote nothing
        self.assert_every_write_under_our_lock()
        self.assertTrue(os.path.exists(switcher.journal_path(self.ctx)))
        self.assertTrue(any('Could not undo the switch yet' in x for x in self.rig.logs), self.rig.logs)
        self.holder.let_go()
        self.assertIsNotNone(switcher.resolve_journal(self.ctx))        # settled once the locks are free
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))

    def test_the_lock_comes_last_and_goes_first_and_never_waits_on_another_lock_or_the_network(self):
        order = []
        real_acquire, real_release = ccfiles.DirLock.acquire, ccfiles.DirLock.release
        server = self.rig.server

        def acquire(lock, budget_s=None):
            got = real_acquire(lock, budget_s)
            order.append(('take', os.path.basename(lock.path), len(server.requests)))
            return got

        def release(lock):
            order.append(('let go', os.path.basename(lock.path), len(server.requests)))
            return real_release(lock)
        with mock.patch.object(ccfiles.DirLock, 'acquire', acquire), \
                mock.patch.object(ccfiles.DirLock, 'release', release):
            res = switcher.switch(self.ctx, 'uuid-a')
        self.assertTrue(res.ok, res.note)
        names = [(what, name) for what, name, _ in order]
        refresh, legacy = facts.CRED_LOCK_NAME, os.path.basename(facts.legacy_lock(self.rig.env, self.rig.home))
        config = os.path.basename(facts.config_lock(self.rig.env, self.rig.home))
        store = facts.STORAGE_LOCK_NAME
        self.assertEqual(names[:2], [('take', refresh), ('take', legacy)])
        held = set()
        for what, name, posts in order:
            if what == 'take':
                if name == store:
                    self.assertEqual(held, {refresh, legacy}, order)     # inside Claude Code's two credential locks
                    took_at = posts
                else:
                    self.assertNotIn(store, held, order)                 # never waits on a lock while holding it
                held.add(name)
            else:
                if name == store:
                    self.assertEqual(posts, took_at, 'a request to Anthropic while holding the save lock')
                held.discard(name)
        self.assertEqual(held, set())
        takes = [name for what, name in names if what == 'take']
        self.assertEqual(takes.count(store), 2)             # the write, then the read-back
        self.assertLess(takes.index(store), takes.index(config))
        self.assertEqual(names[-2:], [('let go', legacy), ('let go', refresh)])


class ThePutBack(Base):
    def setUp(self):
        super().setUp()
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)
        self.saved_b = self.rig.vault('uuid-b')

    def lose_login(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(MCP, separators=(',', ':')))

    def look(self, after=5.0):
        self.clock.advance(after)
        return putback.look(self.ctx)

    def test_a_put_back_writes_under_the_lock_and_lets_it_go(self):
        self.lose_login()
        self.look(0)
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assert_every_write_under_our_lock()
        self.assert_lock_gone()

    def test_while_claude_code_holds_the_lock_nothing_is_written_and_the_next_look_puts_it_back(self):
        self.lose_login()
        self.look(0)
        self.hold()
        res = self.look()
        self.assertEqual(res.action, 'wait', res.reason)                # a held lock is not a failed write
        self.assertIn('Claude Code was saving its login', res.reason)
        self.assertEqual(self.live_writes(), 0)
        self.assertTrue(self.holder.holds())
        self.assertEqual(putback.load_record(self.ctx)['done'], [])
        self.holder.let_go()
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(live.parse(self.rig.live_text()).refresh_token, live.parse(self.saved_b).refresh_token)
        self.assert_every_write_under_our_lock()
        self.assert_lock_gone()

    def test_a_lock_held_at_three_looks_in_a_row_is_said_once_with_its_holders_age(self):
        self.lose_login()
        self.look(0)
        self.hold()
        said = lambda: [x for x in self.rig.logs if 'save lock' in x and 'looks in a row' in x]   # noqa: E731
        for n in range(1, 7):
            self.assertEqual(self.look(5.0).action, 'wait')             # not a failed write: the next look tries
            self.assertEqual(len(said()), 0 if n < putback.BUSY_LOOKS else 1, (n, said()))
        self.assertRegex(said()[0], r'its holder made it \d+ s ago')
        self.holder.let_go()
        self.assertEqual(self.look(putback.PUT_BACK_WAITS[-1] + 1).action, 'put_back')
        self.lose_login()                                   # a new loss, and the lock held again: said again
        self.hold()
        for _ in range(putback.BUSY_LOOKS + 1):             # the first look of a loss only notes it
            self.look(putback.PUT_BACK_WAITS[1] + 1)
        self.assertEqual(len(said()), 2, said())
        self.assertEqual(self.live_writes(), 1)

    def test_a_holder_that_lets_go_after_a_second_lets_the_put_back_through(self):
        self.lose_login()
        self.look(0)
        self.hold(let_go_after=1.0)
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assert_every_write_under_our_lock()
        self.assert_lock_gone()

    def test_a_stale_holder_is_taken_over(self):
        self.lose_login()
        self.look(0)
        self.hold(age_s=facts.STORAGE_LOCK_STALE_S + 5)
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assert_lock_gone()

    def test_every_refused_try_lets_the_lock_go_between_tries(self):
        self.lose_login()
        self.look(0)
        between = []
        real_sleep = self.sleep

        def sleep(s):
            between.append(os.path.exists(self.lock_path))
            real_sleep(s)
        self.ctx.sleep = sleep
        self.rig.kc.rule('write', self.rig.service, why='unknown', times=-1)
        res = self.look()
        self.assertEqual(res.action, 'failed', res.reason)
        self.assertTrue(between)
        self.assertNotIn(True, between)
        self.assert_lock_gone()

    def test_a_save_that_ends_while_the_put_back_waits_for_the_lock_is_never_written_over(self):
        self.lose_login()
        self.look(0)
        self.hold(let_go_after=1.0)
        real_sleep = self.sleep

        def sleep(s):
            if self.holder.holds() and self.clock.mono() + s >= self.let_go_at:
                self.rig.set_live(self.b_text)              # the holder's save: a login is back
            real_sleep(s)
        self.ctx.sleep = sleep
        res = self.look()
        self.assertEqual(res.action, 'leave', res.reason)
        self.assertEqual(self.rig.live_text(), self.b_text)
        self.assertEqual(self.live_writes(), 0)
        self.assert_lock_gone()


if __name__ == '__main__':
    unittest.main()
