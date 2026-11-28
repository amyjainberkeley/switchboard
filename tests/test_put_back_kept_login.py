import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The saved login is read during the wait, not during the write. The look that first sees the loss and decides to wait
# reads the account's saved login (so its marks are told at once); its text is kept in this process, with the account
# and its fingerprint, for at most SIGHTING_STALE_S. The look that writes uses it only when, under the account lock,
# the account's record still carries that fingerprint; else it reads the saved login again. The keep step and every
# renewal change the record's fingerprint, so an older generation is never written from it. Nothing of it is written
# anywhere, logged or returned. The account engine on its stand-ins (fakes_accounts) with a fake clock. Made-up
# *.example accounts.
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
        self.tmp = tempfile.mkdtemp(prefix='put-back-kept-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        putback._KEPT.clear()
        self.a_text = self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)                 # two sign-ins never share an end stamp to the millisecond
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)   # Claude Code is on B
        self.saved_b = self.rig.vault('uuid-b')
        patcher = mock.patch.object(autoloop, 'notify_put_back', lambda res: None)
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

    def until_landed(self, start):
        calls = self.where[start:]
        for i, (verb, which, _) in enumerate(calls):
            if verb == 'write' and which == 'live':
                for j in range(i + 1, len(calls)):
                    if calls[j][:2] == ('read', 'live'):
                        return calls[:j + 1]
        self.fail('no write of the item read back: %r' % (calls,))

    def lose_login(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))

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

    def saved_reads(self, start):
        return [c for c in self.where[start:] if c[:2] == ('read', 'saved')]

    def live_writes(self):
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)

    def holds(self, text):
        live_text = self.rig.live_text()
        return bool(live_text) and json.loads(live_text).get('claudeAiOauth') == json.loads(text)['claudeAiOauth']

    def renew_saved_b(self):
        """Switchboard renews B's saved login: a newer generation in its slot and in its record."""
        newer = self.rig.server.refresh_for(self.saved_b)
        with vault.changing(self.ctx) as doc:
            self.assertTrue(vault.save_login(self.ctx, 'uuid-b', newer).ok)
            vault.note_login(doc['accounts']['uuid-b'], live.parse(newer), None, self.clock.wall())
        return newer


class TwoKeychainCallsFromTheSecondLookToTheLanding(Base):
    def test_a_put_back_reads_the_item_and_writes_it_and_nothing_else(self):
        self.lose_login()
        self.first_look()
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        calls = self.until_landed(start)
        self.assertEqual(calls, [('read', 'live', True), ('write', 'live', True), ('read', 'live', True)])
        self.assertTrue(self.holds(self.saved_b))

    def test_what_the_first_look_read_is_written_though_the_keychain_cannot_answer_by_then(self):
        """The look that writes does not read the saved login again: the text the first look read is the one the
        account's record still describes (the record and the saved login change together, under the account lock)."""
        self.lose_login()
        self.first_look()
        self.rig.kc.rule('read', service=vault.SERVICE_CURRENT, account='uuid-b', why='locked', times=-1)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertTrue(self.holds(self.saved_b))

    def test_the_text_is_never_written_down_logged_or_returned(self):
        self.lose_login()
        self.first_look()
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        token = json.loads(self.saved_b)['claudeAiOauth']['refreshToken']
        with open(self.ctx.path(putback.STATE)) as f:
            self.assertNotIn(token, f.read())
        self.assertNotIn(token, repr(res))
        self.assertNotIn(token, '\n'.join(self.rig.logs))
        self.assertNotIn(token, repr(self.rig.events))


class TheKeptLoginIsNotUsed(Base):
    """Each way the kept text must not be used: the look that writes then reads the saved login from the Keychain."""

    def test_when_the_record_carries_another_fingerprint_after_a_renewal_the_new_login_is_written(self):
        self.lose_login()
        self.first_look()
        newer = self.renew_saved_b()
        start = len(self.where)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(len(self.saved_reads(start)), 1)
        self.assertTrue(self.holds(newer))
        self.assertFalse(self.holds(self.saved_b))

    def test_for_another_account_even_when_its_record_carries_that_fingerprint(self):
        self.rig.set_config(identity_block('uuid-a', 'ana@work.example', 'org-a'))
        self.lose_login()
        self.first_look()                                           # A's saved login is kept
        a_fp = live.parse(self.rig.vault('uuid-a')).fingerprint
        self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-b']['fp'] = a_fp                  # a record that says B's saved login is A's
        now = self.clock.wall()                                     # B's loss seen by another process 5 s ago
        self.assertTrue(putback._change_record(self.ctx, lambda r: r.update(missing=('uuid-b', now - 5.0),
                                                                            missing_how='item'), now))
        start = len(self.where)
        self.watch(0.1)
        self.assertGreaterEqual(len(self.saved_reads(start)), 1)
        self.assertFalse(self.holds(self.a_text))

    def test_older_than_its_time(self):
        self.lose_login()
        first = self.first_look()
        start = len(self.where)
        self.clock.advance(first + putback.SIGHTING_STALE_S + 1.0 - self.clock.wall())
        self.assertEqual(self.watch().action, 'put_back')
        self.assertEqual(len(self.saved_reads(start)), 1)

    def test_after_a_reset(self):
        self.lose_login()
        self.first_look()
        putback.reset()
        start = len(self.where)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')
        self.assertEqual(len(self.saved_reads(start)), 1)

    def test_after_a_look_that_found_the_login_there(self):
        self.lose_login()
        self.first_look()
        self.assertIn(self.ctx.data_dir, putback._KEPT_SAVED)
        self.rig.set_live(self.b_text)
        self.assertEqual(self.watch(1.0, keychain=True).action, 'leave')
        self.assertNotIn(self.ctx.data_dir, putback._KEPT_SAVED)

    def test_after_a_look_that_found_the_saved_login_gone(self):
        self.lose_login()
        self.first_look()
        self.rig.sec.drop(vault.SERVICE_CURRENT, 'uuid-b')         # the record still describes it
        self.assertEqual(self.watch(1.0).key, 'missing')            # a look that writes nothing reads it
        res = [self.watch(putback.PUT_BACK_AFTER_S) for _ in range(3)]
        self.assertEqual([r.action for r in res], ['leave'] * 3)
        self.assertEqual(self.live_writes(), 0)


if __name__ == '__main__':
    unittest.main()
