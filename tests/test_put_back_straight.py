import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A look that follows a sighting of the loss goes straight to the locks. Each `security` call takes 1 to 1.5 s on a
# loaded Mac, and while the login is gone every Claude Code process that saves writes an item without it. The look
# that saw the loss first notes it in the record: when, for which account, and how (the item gone or without a login;
# the login only in the plain login file; an item without a login beside a plain file holding one). A later look, while
# that sighting is at most SIGHTING_STALE_S old and the rule says to write now on the state it was of, reads nothing of
# Claude Code's item outside the locks: its live read is the one under Claude Code's save lock, and the whole decision
# is made again from it. When that read finds anything else, nothing is written and the look answers from it as any
# look does. The account engine on its stand-ins (fakes_accounts) with a fake clock. Made-up *.example accounts.
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, autoloop, ccfiles, context, facts, live, putback, vault
from switchboard.accounts import signin_watch as sw

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='put-back-straight-')
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

    def note_call(self, verb, service, account):
        which = 'live' if service == self.rig.service else 'saved' if service == vault.SERVICE_CURRENT else service
        held = os.path.isdir(facts.storage_lock(self.ctx.env, self.ctx.home))
        self.where.append((verb, which, held))

    def outside(self, start):
        """The reads of Claude Code's item made outside its save lock since `start`."""
        return [c for c in self.where[start:] if c[:2] == ('read', 'live') and not c[2]]

    def until_landed(self, start):
        """The calls from `start` up to and including the read-back of the first write of Claude Code's item."""
        calls = self.where[start:]
        for i, (verb, which, _) in enumerate(calls):
            if verb == 'write' and which == 'live':
                for j in range(i + 1, len(calls)):
                    if calls[j][:2] == ('read', 'live'):
                        return calls[:j + 1]
        self.fail('no write of the item read back: %r' % (calls,))

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
        self.write_file(self.b_text if text is None else text, age)
        self.rig.sec.drop(self.rig.service, self.rig.account)

    def beside_file(self):
        """An item without a login beside a plain file holding the account's login."""
        self.write_file(self.b_text, 3600.0)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 7}), separators=(',', ':')))

    def watch(self, after=0.0, **hint):
        self.clock.advance(after)
        h = dict(sw.no_hint())
        h.update(hint)
        h['login_file_written'] = h['login_file'] in ('appeared', 'changed')
        return api.watch_look(h)

    def first_look(self):
        res = self.watch(0.2, keychain=True)
        self.assertEqual(res.action, 'wait', res.reason)
        return self.clock.wall()

    def live_writes(self):
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)

    def has(self, text):
        live_text = self.rig.live_text()
        return bool(live_text) and json.loads(live_text).get('claudeAiOauth') == json.loads(text)['claudeAiOauth']

    def record(self):
        return putback.load_record(self.ctx)


class TheLookAfterASightingReadsNothingOutsideTheLocks(Base):
    def test_a_put_back(self):
        self.lose_login()
        self.first_look()
        self.assertEqual(self.record()['missing_how'], 'item')
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(self.outside(start), [])
        self.assertEqual(self.until_landed(start)[-3:], [('read', 'live', True), ('write', 'live', True),
                                                         ('read', 'live', True)])
        self.assertTrue(self.has(self.saved_b))
        self.assertIsNone(self.record()['missing_how'])

    def test_a_promote(self):
        self.in_file_only()
        self.first_look()
        self.assertEqual(self.record()['missing_how'], 'file')
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, putback.PROMOTE, res.reason)
        self.assertEqual(self.until_landed(start), [('read', 'live', True), ('write', 'live', True),
                                                    ('read', 'live', True)])
        self.assertTrue(self.has(self.b_text))
        self.assertTrue(os.path.exists(self.cred))              # left for a later look to take away

    def test_the_repair_from_the_plain_file(self):
        self.beside_file()
        self.first_look()
        self.assertEqual(self.record()['missing_how'], 'beside')
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(self.until_landed(start), [('read', 'live', True), ('write', 'live', True),
                                                    ('read', 'live', True)])
        self.assertTrue(self.has(self.b_text))

    def test_a_write_of_the_plain_file_seen_by_the_watch_is_proof_before_the_wait_is_judged(self):
        self.lose_login()
        self.assertEqual(self.watch(0.2, keychain=True).next_s, putback.PUT_BACK_AFTER_S)    # no proof yet
        self.write_file(json.dumps(MCP), 0.0)                      # Claude Code's fallback wrote the file
        start = len(self.where)
        res = self.watch(putback.FAULT_AFTER_S + 0.05, login_file='appeared')
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(self.outside(start), [])

    def test_a_sighting_thirty_seconds_old_is_still_trusted(self):
        self.lose_login()
        first = self.first_look()
        start = len(self.where)
        self.clock.advance(first + putback.SIGHTING_STALE_S - 0.1 - self.clock.wall())
        res = self.watch()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(self.outside(start), [])


class OtherwiseTheLookReadsOutsideTheLocksFirst(Base):
    """Each condition of the straight path alone: when it does not hold, the look's first Keychain call is its read of
    Claude Code's item outside the locks, as before."""

    def test_a_sighting_older_than_thirty_seconds(self):
        self.lose_login()
        first = self.first_look()
        start = len(self.where)
        self.clock.advance(first + putback.SIGHTING_STALE_S + 0.1 - self.clock.wall())
        res = self.watch()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(self.where[start], ('read', 'live', False))

    def test_a_sighting_that_does_not_say_how_it_was_seen(self):
        self.lose_login()
        self.first_look()
        path = self.ctx.path(putback.STATE)
        with open(path) as f:
            obj = json.load(f)
        obj.pop('missing_how', None)                                # as a record written by an older build
        ccfiles.atomic_write(path, (json.dumps(obj) + '\n').encode('utf-8'), mode=0o600)
        start = len(self.where)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')
        self.assertEqual(self.where[start], ('read', 'live', False))

    def test_a_look_before_the_wait_has_passed(self):
        self.lose_login()
        self.first_look()
        putback.reset()                                             # another process made the first look
        start = len(self.where)
        self.assertEqual(self.watch(1.0).action, 'wait')
        self.assertEqual(self.where[start:], [('read', 'live', False), ('read', 'saved', False)])

    def test_a_mark_on_the_record(self):
        self.lose_login()
        self.first_look()
        putback.reset()
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-b']['strike'] = {'fp': live.parse(self.saved_b).fingerprint,
                                                   'at': self.clock.wall()}
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual((res.action, res.key), ('leave', 'refused'))
        self.assertEqual(self.where[start:], [('read', 'live', False)])

    def test_settings_that_name_another_account_now(self):
        self.lose_login()
        self.first_look()
        putback.reset()
        self.rig.set_config(identity_block('uuid-a', 'ana@work.example', 'org-a'))
        start = len(self.where)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'wait')
        self.assertEqual(self.where[start], ('read', 'live', False))

    def test_an_account_whose_record_holds_no_fingerprint(self):
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-b'].pop('fp', None)               # the saved login would have to be read
        self.lose_login()
        self.first_look()
        start = len(self.where)
        self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(self.where[start], ('read', 'live', False))

    def test_the_limit(self):
        now = self.clock.wall()
        self.assertTrue(putback._change_record(self.ctx, lambda r: r.update(
            done=[now - 50 * n for n in range(1, putback.PUT_BACK_MAX + 1)], limit_at=now), now))
        self.lose_login()
        self.assertEqual(self.watch(0.2, keychain=True).action, 'limit')
        putback.reset()
        start = len(self.where)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'limit')
        self.assertEqual(self.where[start], ('read', 'live', False))
        self.assertEqual(self.live_writes(), 0)


class WhatTheReadUnderTheSaveLockFinds(Base):
    """On the straight path the read under Claude Code's save lock is the look's own live read: when it finds anything
    but the loss the sighting was of, nothing is written and the look answers from it as any look does."""

    def test_a_login_back_by_then_is_left_and_the_sighting_cleared(self):
        self.lose_login()
        self.first_look()
        self.rig.set_live(self.b_text)                              # a chat saved it back
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'leave', res.reason)
        self.assertEqual(self.live_writes(), 0)
        self.assertIsNone(self.record()['missing'])
        self.assertEqual([c for c in self.where[start:] if c[1] == 'live'], [('read', 'live', True)])

    def test_settings_that_name_nobody_by_then_get_nothing_and_clear_the_sighting(self):
        self.lose_login()
        self.first_look()

        def on_call(verb, service, account):
            self.note_call(verb, service, account)
            if self.where[-1] == ('read', 'live', True):
                self.rig.set_config(None)
        self.rig.kc.on_call = on_call
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'leave', res.reason)
        self.assertEqual(self.live_writes(), 0)
        self.assertIsNone(self.record()['missing'])

    def test_a_login_moved_to_the_plain_file_meanwhile_is_promoted_by_the_same_look(self):
        self.lose_login()
        self.first_look()
        self.in_file_only()                                         # the item gone, the login only in the file
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, putback.PROMOTE, res.reason)
        self.assertTrue(self.has(self.b_text))

    def test_a_fallback_file_written_just_before_that_read_is_not_answered_with_the_saved_copy(self):
        self.lose_login()
        self.first_look()
        self.clock.advance(1.0)
        newer = self.rig.server.refresh_for(self.b_text)            # Claude Code renewed; its save fell back

        def on_call(verb, service, account):
            self.note_call(verb, service, account)
            if self.where[-1] == ('read', 'live', True) and not os.path.exists(self.cred):
                self.write_file(newer, 0.0)
        self.rig.kc.on_call = on_call
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)        # the same look repairs it from the file
        self.assertTrue(self.has(newer))

    def test_a_read_that_gets_no_answer_is_not_a_failed_write(self):
        self.lose_login()
        first = self.first_look()
        self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=1)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'leave', res.reason)
        rec = self.record()
        self.assertEqual(rec['failed'], [])
        self.assertEqual(rec['missing'], ('uuid-b', first))
        self.assertEqual(self.live_writes(), 0)
        self.assertEqual(self.watch(0.5).action, 'put_back')        # the next look writes

    def test_after_a_read_under_the_save_lock_that_got_no_answer_the_next_looks_read_outside_it_first(self):
        # the lock is Claude Code's own, and its saves wait while it is held: a look that held it for a read that got
        # no answer does not take it again for a read until STRAIGHT_OFF_S have passed
        self.lose_login()
        self.first_look()
        self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=-1)    # the Keychain stops answering
        start = len(self.where)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'leave')
        self.assertEqual(self.where[start:], [('read', 'live', True)])                 # one read, under the lock
        for _ in range(4):                                                             # looks half a second apart
            start = len(self.where)
            self.assertEqual(self.watch(0.5).action, 'leave')
            self.assertEqual(self.where[start:], [('read', 'live', False)])            # the lock is not taken again
        self.clock.advance(putback.STRAIGHT_OFF_S - 2.0)                               # the pause is over
        start = len(self.where)
        self.assertEqual(self.watch(0.1).action, 'leave')
        self.assertEqual(self.where[start:], [('read', 'live', True)])
        self.assertEqual(self.record()['failed'], [])
        self.assertEqual(self.live_writes(), 0)

    def test_the_pause_ends_with_the_episode_and_the_next_loss_goes_straight_again(self):
        self.lose_login()
        self.first_look()
        self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=1)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'leave')
        start = len(self.where)
        self.assertEqual(self.watch(0.5).action, 'put_back')        # within the pause: read outside first, then written
        self.assertEqual(self.where[start], ('read', 'live', False))
        self.clock.advance(putback.EPISODE_QUIET_S + 5.0)
        self.assertEqual(self.watch(keychain=True).action, 'leave')                    # the login is there
        self.lose_login()                                           # lost again: a new episode, no pause left
        self.first_look()
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_WAITS[1])                 # the second put-back of the hour waits longer
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(self.outside(start), [])

    def test_writes_that_are_all_refused_are_tried_once_over(self):
        self.lose_login()
        self.first_look()
        self.rig.kc.rule('write', service=self.rig.service, why='locked', times=-1)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'failed', res.reason)
        self.assertEqual(self.live_writes(), putback.TRIES)        # never a second round in the same look
        self.assertEqual(len(self.record()['failed']), 1)

    def test_a_mark_found_under_the_locks_is_not_said_while_the_login_is_back(self):
        self.lose_login()
        self.first_look()
        putback.reset()                                             # another process made the first look
        self.rig.sec.drop(vault.SERVICE_CURRENT, 'uuid-b')         # the record still describes it
        self.rig.set_live(self.b_text)                              # and the login is back
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'leave', res.reason)
        self.assertIsNone(res.key)
        self.assertIsNone(res.line)
        self.assertIsNone(self.record()['missing'])


if __name__ == '__main__':
    unittest.main()
