import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# How many Keychain calls a put-back makes, and where each one is made. Each `security` call takes 0.3 to 1.1 s on a
# busy Mac, and while the login is gone every Claude Code process that saves writes an item without it. So the look
# that follows a sighting of the loss reads nothing of Claude Code's item outside the locks (it decides from the
# sighting, the files and the account's record); the saved login is the one the first look read while it waited, kept
# in the process while the account's record still carries its fingerprint, else it is read once under the engine's
# locks, before Claude Code's save lock is taken (never inside it); and under the save lock come the fresh live read,
# the decision again, the write and the read-back. From the second look to the landing: two calls, then the read-back.
# The account engine on its stand-ins (fakes_accounts) with a fake clock. Made-up *.example accounts.
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, autoloop, context, facts, live, putback, vault
from switchboard.accounts import signin_watch as sw

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='put-back-calls-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        putback._KEPT.clear()
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)                 # two sign-ins never share an end stamp to the millisecond
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)   # Claude Code is on B
        self.saved_b = self.rig.vault('uuid-b')
        self.told = []
        patcher = mock.patch.object(autoloop, 'notify_put_back', self.told.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.where = []                         # (verb, which item, the save lock held at that moment)
        self.rig.kc.on_call = self.note_call

    def tearDown(self):
        self.use.__exit__(None, None, None)
        putback.reset()
        putback._KEPT.clear()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    # the stand-in Keychain's calls

    def note_call(self, verb, service, account):
        which = 'live' if service == self.rig.service else 'saved' if service == vault.SERVICE_CURRENT else service
        held = os.path.isdir(facts.storage_lock(self.ctx.env, self.ctx.home))
        self.where.append((verb, which, held))

    def until_landed(self, start):
        """The calls from `start` up to and including the read-back of the first write of Claude Code's item."""
        calls = self.where[start:]
        for i, (verb, which, _) in enumerate(calls):
            if verb == 'write' and which == 'live':
                for j in range(i + 1, len(calls)):
                    if calls[j][:2] == ('read', 'live'):
                        return calls[:j + 1]
        self.fail('no write of the item read back: %r' % (calls,))

    # Claude Code's side

    @property
    def cred(self):
        return os.path.join(self.rig.home, '.claude', '.credentials.json')

    def write_file(self, text, age):
        with open(self.cred, 'w') as f:
            f.write(text)
        os.chmod(self.cred, 0o600)
        t = self.clock.wall() - age
        os.utime(self.cred, (t, t))

    def lose_login(self):
        """The item deleted, then saved again holding only connector state."""
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))

    def in_file_only(self, text=None, age=3600.0):
        """Claude Code's fallback: the item gone, the login only in its plaintext file."""
        self.write_file(self.b_text if text is None else text, age)
        self.rig.sec.drop(self.rig.service, self.rig.account)

    def watch(self, after=0.0, **hint):
        self.clock.advance(after)
        h = dict(sw.no_hint())
        h.update(hint)
        h['login_file_written'] = h['login_file'] in ('appeared', 'changed')
        return api.watch_look(h)

    def live_writes(self):
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)

    def has(self, text):
        live_text = self.rig.live_text()
        return bool(live_text) and json.loads(live_text).get('claudeAiOauth') == json.loads(text)['claudeAiOauth']


class TheCallsFromTheSecondLookToTheLanding(Base):
    def test_a_put_back_makes_three(self):
        self.lose_login()
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        calls = self.until_landed(start)
        self.assertEqual([c[:2] for c in calls], [('read', 'live'), ('write', 'live'), ('read', 'live')])
        self.assertLessEqual(len(calls), 3)
        self.assertTrue(self.has(self.saved_b))

    def test_the_saved_login_is_read_before_claude_codes_save_lock_and_the_live_item_under_it(self):
        self.lose_login()
        self.watch(0.2, keychain=True)
        putback.reset()                         # another process made the first look: nothing is kept here
        start = len(self.where)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')
        calls = self.until_landed(start)
        self.assertEqual([held for verb, which, held in calls if which == 'saved'], [False])
        self.assertEqual([held for verb, which, held in calls if which == 'live'], [True, True, True])

    def test_a_promote_makes_three_and_never_reads_the_saved_login(self):
        self.in_file_only()
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'promote', res.reason)
        calls = self.until_landed(start)
        self.assertEqual([c[:2] for c in calls], [('read', 'live'), ('write', 'live'), ('read', 'live')])
        self.assertTrue(self.has(self.b_text))
        self.assertTrue(os.path.exists(self.cred))              # left for a later look to take away

    def test_the_repair_from_the_file_makes_three_and_never_reads_the_saved_login(self):
        self.write_file(self.b_text, 3600.0)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 7}), separators=(',', ':')))
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        calls = self.until_landed(start)
        self.assertEqual([c[:2] for c in calls], [('read', 'live'), ('write', 'live'), ('read', 'live')])
        self.assertTrue(self.has(self.b_text))

    def test_an_older_generation_in_the_file_writes_the_saved_login_the_first_look_read(self):
        old = self.b_text
        self.clock.advance(600)
        newer = self.rig.server.refresh_for(old)
        self.rig.set_live(newer)
        self.assertIn('Saved the newer login', api.keep_pass())
        self.in_file_only(old)
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'promote', res.reason)
        calls = self.until_landed(start)
        self.assertEqual([c[:2] for c in calls], [('read', 'live'), ('write', 'live'), ('read', 'live')])
        self.assertTrue(self.has(newer))

    def test_a_login_there_is_still_one_read_per_look(self):
        start = len(self.where)
        self.assertEqual(self.watch(1.0, keychain=True).action, 'leave')
        self.assertEqual(self.where[start:], [('read', 'live', False)])


class EveryCheckStillHoldsAtItsNewPlace(Base):
    """What the reads that were taken out used to catch is caught at the reads that stay."""

    def deciding_look(self, on_live=None, on_saved=None):
        """The look that decides, with `on_live(n)` / `on_saved(n)` run just before its n-th read of that item."""
        counts = {'live': 0, 'saved': 0}

        def on_call(verb, service, account):
            self.note_call(verb, service, account)
            which = self.where[-1][1]
            if verb == 'read' and which in counts:
                counts[which] += 1
                act = on_live if which == 'live' else on_saved
                if act is not None:
                    act(counts[which])
        self.rig.kc.on_call = on_call
        return self.watch(putback.PUT_BACK_AFTER_S)

    def test_a_login_that_appears_while_the_saved_one_is_read_under_the_locks_is_left_alone(self):
        self.lose_login()
        self.watch(0.2, keychain=True)
        putback.reset()                         # another process made the first look: the saved login is read here
        new = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')
        res = self.deciding_look(on_saved=lambda n: self.rig.set_live(new) if n == 1 else None)
        self.assertNotEqual(res.action, 'put_back')
        self.assertEqual(self.live_writes(), 0)
        self.assertEqual(self.rig.live_text(), new)

    def test_a_login_that_appears_just_before_the_read_under_the_save_lock_is_left_alone(self):
        self.lose_login()
        self.watch(0.2, keychain=True)
        new = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')
        res = self.deciding_look(on_live=lambda n: self.rig.set_live(new) if n == 1 else None)
        self.assertNotEqual(res.action, 'put_back')
        self.assertEqual(self.live_writes(), 0)
        self.assertEqual(self.rig.live_text(), new)

    def test_settings_that_name_nobody_by_the_read_under_the_save_lock_get_nothing(self):
        self.lose_login()
        self.watch(0.2, keychain=True)
        res = self.deciding_look(on_live=lambda n: self.rig.set_config(None) if n == 1 else None)
        self.assertNotEqual(res.action, 'put_back')
        self.assertEqual(self.live_writes(), 0)

    def test_a_sign_in_that_starts_on_the_page_by_the_read_under_the_save_lock_is_not_raced(self):
        self.lose_login()
        self.watch(0.2, keychain=True)

        def page_sign_in(n):
            if n == 1:
                folder = os.path.join(self.rig.data, 'logins', 'made-up-sign-in')
                os.makedirs(folder)
                os.utime(folder, (self.clock.wall(), self.clock.wall()))
        res = self.deciding_look(on_live=page_sign_in)
        self.assertNotEqual(res.action, 'put_back')
        self.assertEqual(self.live_writes(), 0)

    def test_a_saved_login_gone_by_the_deciding_look_is_left_and_said_once(self):
        self.lose_login()
        self.watch(0.2, keychain=True)
        putback.reset()                         # another process made the first look: the saved login is read here
        self.rig.sec.drop(vault.SERVICE_CURRENT, 'uuid-b')         # the record still describes it
        res = [self.watch(putback.PUT_BACK_AFTER_S) for _ in range(4)]
        self.assertEqual([r.action for r in res], ['leave'] * 4)
        said = [r.line for r in res if r.line]
        self.assertEqual(len(said), 1, said)
        self.assertIn('ana@home.example', said[0])
        self.assertIn('is not in the Keychain', said[0])
        self.assertEqual(res[0].key, 'missing')
        self.assertEqual(self.live_writes(), 0)

    def test_a_saved_login_that_cannot_be_read_under_the_locks_is_left(self):
        self.lose_login()
        self.watch(0.2, keychain=True)
        putback.reset()                         # another process made the first look: the saved login is read here
        self.rig.kc.rule('read', service=vault.SERVICE_CURRENT, account='uuid-b', why='locked', times=-1)
        res = [self.watch(putback.PUT_BACK_AFTER_S) for _ in range(3)]
        self.assertEqual([r.action for r in res], ['leave'] * 3)
        self.assertEqual(len([r for r in res if r.line]), 1)
        self.assertEqual(self.live_writes(), 0)

    def test_a_saved_login_that_is_not_the_one_its_record_describes_is_never_written(self):
        self.lose_login()
        self.watch(0.2, keychain=True)
        putback.reset()                         # another process made the first look: the saved login is read here
        blob = json.loads(self.saved_b)         # not newer than its record (a newer full one is written)
        blob['claudeAiOauth'] = dict(blob['claudeAiOauth'], refreshToken='sk-ant-ort01-made-up-other',
                                     expiresAt=blob['claudeAiOauth']['expiresAt'] - 60000)
        other = json.dumps(blob, separators=(',', ':'))
        self.rig.sec.put(vault.SERVICE_CURRENT, 'uuid-b', other)      # the Keychain copy, without its record
        for _ in range(3):
            self.assertNotEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')
        self.assertEqual(self.live_writes(), 0)

    def test_a_mark_on_the_record_still_stops_the_deciding_look_with_no_saved_read(self):
        self.lose_login()
        self.watch(0.2, keychain=True)
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-b']['strike'] = {'fp': live.parse(self.saved_b).fingerprint,
                                                   'at': self.clock.wall()}
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual((res.action, res.key), ('leave', 'refused'))
        self.assertEqual([c for c in self.where[start:] if c[1] == 'saved'], [])
        self.assertEqual(self.live_writes(), 0)

    def test_a_fallback_file_written_before_the_read_under_the_save_lock_is_not_answered_with_the_saved_copy(self):
        self.lose_login()
        self.watch(0.2, keychain=True)
        self.clock.advance(600)
        newer = self.rig.server.refresh_for(self.b_text)            # Claude Code renewed; its save fell back to the file
        self.deciding_look(on_live=lambda n: self.write_file(newer, 0.0) if n == 2 else None)
        self.assertFalse(self.has(self.saved_b), 'the older saved copy went in beside a newer login in the file')
        self.rig.kc.on_call = self.note_call
        for _ in range(4):
            if self.has(newer):
                break
            self.watch(putback.FAULT_AFTER_S)
        self.assertTrue(self.has(newer))                            # a later look repairs it from the file

    def test_a_file_that_changes_after_the_deciding_read_is_never_promoted_from_its_old_bytes(self):
        self.in_file_only()
        self.watch(0.2, keychain=True)
        other = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')

        def rewrite(n):
            if n == 1:
                self.write_file(other, 0.0)
        self.deciding_look(on_live=rewrite)
        text = self.rig.live_text()
        self.assertTrue(text is None or not self.has(self.b_text), text)


class ASettingsChangeAlone(Base):
    """Claude Code rewrites its settings file for many reasons. When the sign-in watch saw only that file change, the
    settings name the same account and organization as at the last look, no loss is on record and no write of ours is
    recent enough to take back, the look answers without a Keychain read: every save of the item replaces the
    Keychain file, and the watch would have said so."""

    def reads(self, start):
        return [c for c in self.where[start:] if c[0] == 'read']

    def rewrite_settings(self, block=True, **other):
        self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b') if block is True else block, **other)

    def test_the_same_account_named_again_costs_no_keychain_read(self):
        self.watch(1.0, keychain=True)
        for n in range(3):
            self.rewrite_settings(numStartups=n)
            start = len(self.where)
            res = self.watch(1.0, settings=True)
            self.assertEqual(res.action, 'leave', res.reason)
            self.assertEqual(self.reads(start), [])

    def test_another_account_named_is_looked_at(self):
        self.watch(1.0, keychain=True)
        self.rewrite_settings(identity_block('uuid-a', 'ana@work.example', 'org-a'))
        start = len(self.where)
        self.watch(1.0, settings=True)
        self.assertIn(('read', 'live', False), self.reads(start))

    def test_another_organization_of_the_same_account_is_looked_at(self):
        self.watch(1.0, keychain=True)
        self.rewrite_settings(identity_block('uuid-b', 'ana@home.example', 'org-z'))
        start = len(self.where)
        self.watch(1.0, settings=True)
        self.assertNotEqual(self.reads(start), [])

    def test_a_loss_on_record_is_looked_at_and_put_back(self):
        self.lose_login()
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        self.rewrite_settings(numStartups=9)
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_AFTER_S, settings=True)
        self.assertNotEqual(self.reads(start), [])
        self.assertEqual(res.action, 'put_back', res.reason)

    def test_soon_after_a_write_of_ours_it_is_looked_at(self):
        self.lose_login()
        self.watch(0.2, keychain=True)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')
        self.watch(1.0, keychain=True)
        self.rewrite_settings(numStartups=3)
        start = len(self.where)
        self.watch(1.0, settings=True)
        self.assertNotEqual(self.reads(start), [])
        self.clock.advance(putback.TAKE_BACK_S)
        self.rewrite_settings(numStartups=4)
        start = len(self.where)
        self.watch(1.0, settings=True)
        self.assertEqual(self.reads(start), [])

    def test_a_process_that_starts_afresh_goes_by_the_record(self):
        self.watch(1.0, keychain=True)
        putback.reset()                           # what a new process knows: nothing but the record
        self.rewrite_settings(numStartups=1)
        start = len(self.where)
        self.assertEqual(self.watch(1.0, settings=True).action, 'leave')
        self.assertEqual(self.reads(start), [])

    def test_a_sign_out_soon_after_a_put_back_of_ours_still_reaches_the_take_back(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)          # no item at all: ours is created
        self.watch(0.2, keychain=True)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')
        self.rig.set_config(None)                                       # the settings name nobody
        self.watch(0.5, settings=True)
        self.assertIsNone(self.rig.live_text())

    def test_a_take_back_that_did_not_work_is_tried_again_at_the_next_change_of_the_settings_alone(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.watch(0.2, keychain=True)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')
        self.rig.kc.rule('delete', service=self.rig.service, why='locked', times=1)
        self.rig.set_config(None)
        self.assertEqual(self.watch(0.5, settings=True).action, 'wait')  # the take-back did not land
        self.assertIsNotNone(self.rig.live_text())
        self.rig.set_config(None, numStartups=2)                       # the settings rewritten, still nobody
        self.watch(0.5, settings=True)
        self.assertIsNone(self.rig.live_text())


class TheLooksAfterAWrite(Base):
    """After a write of ours the sign-in watch sees every save of the item by itself (each one replaces the Keychain
    file); the looks it is asked for come 1, 2, 4, 8 and 15 s after the write, in case a change was missed, then
    none."""

    def follow(self, res, start, until=None):
        """Look again when each answer asks; [(seconds since start, answer)] until an answer asks for no look (or
        `until` s have passed)."""
        out = []
        while res.next_s is not None and (until is None or self.clock.wall() - start + res.next_s <= until + 1e-6):
            res = self.watch(res.next_s)
            out.append((round(self.clock.wall() - start, 6), res))
        return out

    def put_back(self, proof):
        self.lose_login()
        if proof:
            self.assertEqual(self.watch(0.0, login_file='appeared').action, 'wait')
            res = self.watch(putback.FAULT_AFTER_S)
        else:
            self.watch(0.2, keychain=True)
            res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        return res, self.clock.wall()

    def test_one_two_four_eight_fifteen_seconds_then_none(self):
        res, start = self.put_back(proof=False)
        self.assertEqual(res.next_s, 1.0)
        looks = self.follow(res, start)
        self.assertEqual([t for t, _ in looks], [1.0, 2.0, 4.0, 8.0, 15.0])
        self.assertEqual(set(r.action for _, r in looks), {'leave'})

    def test_the_same_after_a_promote(self):
        self.write_file(self.b_text, 3600.0)
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.watch(0.2, keychain=True)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'promote', res.reason)
        start = self.clock.wall()
        self.assertEqual([t for t, _ in self.follow(res, start)], [1.0, 2.0, 4.0, 8.0, 15.0])

    def test_a_look_that_comes_late_asks_for_the_next_point_and_none_after_the_last(self):
        res, start = self.put_back(proof=False)
        self.assertAlmostEqual(self.watch(2.5).next_s, 1.5, places=3)   # 2.5 s after the write: the next is at 4 s
        self.assertAlmostEqual(self.watch(1.4).next_s, 4.1, places=3)   # 3.9 s: 4 s is too close, so 8 s
        self.assertIsNone(self.watch(12.0).next_s)                 # 15.9 s: past the last

    def loss_at(self, point):
        """A put-back inside an episode that saw proof, then the timed looks; the login is lost again just before
        the look at `point` s. Returns (that look's answer, the looks after it until the next put-back)."""
        res, start = self.put_back(proof=True)
        looks = self.follow(res, start, until=point - 0.5)
        self.assertEqual([t for t, _ in looks], [p for p in (1.0, 2.0, 4.0, 8.0, 15.0) if p < point])
        before = looks[-1][1] if looks else res
        self.clock.advance(start + point - self.clock.wall() - before.next_s)
        self.lose_login()
        seen = self.watch(before.next_s)
        self.assertEqual(round(self.clock.wall() - start, 6), point)
        after = []
        res = seen
        while res.action != 'put_back' and len(after) < 100:
            res = self.watch(res.next_s if res.next_s is not None else 1.0)
            after.append(res)
        self.assertEqual(res.action, 'put_back', [r.action for r in after])
        return seen, self.clock.wall() - (start + point)

    def test_a_loss_at_each_timed_look_within_ten_seconds_is_put_back_on_the_quick_path(self):
        for point in (1.0, 2.0, 4.0, 8.0):
            with self.subTest(point=point):
                self.setUp_again()
                seen, took = self.loss_at(point)
                self.assertEqual((seen.action, seen.next_s), ('wait', putback.FAULT_AFTER_S))
                self.assertAlmostEqual(took, putback.FAULT_AFTER_S, places=3)

    def test_a_loss_at_the_last_timed_look_takes_the_slow_path(self):
        seen, took = self.loss_at(15.0)                            # past FAULT_SEEN_S: no proof any more
        self.assertEqual((seen.action, seen.next_s), ('wait', putback.PUT_BACK_AFTER_S))
        self.assertGreaterEqual(took, putback.PUT_BACK_WAITS[1])  # the second put-back of the hour waits 15 s

    def setUp_again(self):
        self.tearDown()
        self.setUp()


if __name__ == '__main__':
    unittest.main()
