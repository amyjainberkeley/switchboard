import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Right after a switch away from an account, settings that name it may be a stale write by a Claude Code session that
# was open during the switch, so a loss under that name is left for SWITCHED_AWAY_S. The rule ends when that account is
# signed in again: a new sign-in, not a renewal. Switchboard's own renewal of the idle saved login, or a `switchboard
# run` as that account, also saves it anew, so the time a login was saved tells nothing. A switch's record keeps the
# sign-in end stamp of the login it left ('left_rx'); the rule ends when the account's record carries an end stamp that
# is not that sign-in (facts.same_sign_in: a renewal keeps the stamp within SAME_SIGN_IN_MS, a new sign-in moves it).
# A record without 'left_rx' (an older build's switch) never ends the rule by the record. Stand-ins only
# (fakes_accounts), a fake clock, made-up *.example accounts.
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, autoloop, context, live, putback, roster, switcher, vault
from switchboard.accounts import signin_watch as sw

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
B_BLOCK = identity_block('uuid-b', 'ana@home.example', 'org-b')


class Cut(BaseException):
    """The process stops right here (as a kill would), leaving the switch's journal behind."""


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='switched-away-')
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
        self.clock.advance(1.0)
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)   # Claude Code is on B
        self.b_rx = live.parse(self.b_text).refresh_expires_ms
        patcher = mock.patch.object(autoloop, 'notify_put_back', lambda res: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        switcher._HOOK = None
        self.use.__exit__(None, None, None)
        putback.reset()
        putback._KEPT.clear()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def switch_to_a(self):
        self.clock.advance(5.0)
        self.assertTrue(switcher.switch(self.ctx, 'uuid-a', source='auto', kind='forced').ok)
        roster.forget_live()
        self.clock.advance(5.0)

    def last(self):
        return vault.load(self.ctx)['switches'][-1]

    def watch(self, after=1.0, **hint):
        self.clock.advance(after)
        h = dict(sw.no_hint())
        h.update(hint)
        h['login_file_written'] = h['login_file'] in ('appeared', 'changed')
        return api.watch_look(h)

    def lose_login(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))

    def stale_name_then_a_loss(self):
        """A session open during the switch writes B's name back, and the login is lost."""
        self.rig.set_config(B_BLOCK)
        self.lose_login()
        return [self.watch(putback.PUT_BACK_AFTER_S) for _ in range(4)]


class TheSwitchsRecordKeepsTheSignInItLeft(Base):
    def test_a_switch_keeps_the_end_stamp_of_the_login_it_left(self):
        self.switch_to_a()
        rec = self.last()
        self.assertEqual((rec['from'], rec['to']), ('uuid-b', 'uuid-a'))
        self.assertEqual(rec['left_rx'], self.b_rx)

    def test_a_switch_finished_from_its_journal_keeps_it_too(self):
        def hook(name):
            if name == 'config_lock':
                raise Cut(name)
        switcher._HOOK = hook
        with self.assertRaises(Cut):
            switcher.switch(self.ctx, 'uuid-a')
        switcher._HOOK = None
        self.assertEqual(switcher.resolve_journal(self.ctx),
                         'Repaired the cut-off switch: Claude Code is on ana@work.example')
        rec = self.last()
        self.assertEqual((rec['to'], rec['source']), ('uuid-a', 'repair'))
        self.assertEqual(rec['left_rx'], self.b_rx)


class TheRuleEndsOnANewSignInOnly(Base):
    def test_a_renewal_of_the_idle_saved_login_after_the_switch_does_not_end_it(self):
        self.switch_to_a()
        renewed = self.rig.server.refresh_for(self.rig.vault('uuid-b'))   # Switchboard renews B's idle saved login
        with vault.changing(self.ctx) as doc:
            self.assertTrue(vault.save_login(self.ctx, 'uuid-b', renewed).ok)
            vault.note_login(doc['accounts']['uuid-b'], live.parse(renewed), None, self.clock.wall())
        res = self.stale_name_then_a_loss()
        self.assertEqual(set(r.action for r in res), {'leave'}, [r.reason for r in res])
        self.assertEqual(res[0].key, 'switched')

    def test_a_new_sign_in_after_the_switch_ends_it(self):
        self.switch_to_a()
        self.clock.advance(180.0)                                   # a sign-in two minutes later is a new one
        new = self.rig.sign_in('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)   # /login of B in Claude Code
        self.assertEqual(self.watch(2.0, keychain=True).action, 'kept')
        self.lose_login()
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, live.parse(new).fingerprint)

    def test_an_older_switch_record_without_the_stamp_never_ends_it_by_the_record(self):
        self.clock.advance(5.0)
        rec = {'at': self.clock.wall(), 'from': 'uuid-b', 'to': 'uuid-a', 'kind': 'manual', 'source': 'cli'}
        with vault.changing(self.ctx) as doc:
            doc['switches'] = (doc.get('switches') or []) + [rec]   # as an older build writes it
        self.clock.advance(180.0)
        self.rig.sign_in('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)
        self.assertEqual(self.watch(2.0, keychain=True).action, 'kept')
        res = self.stale_name_then_a_loss()
        self.assertEqual(set(r.action for r in res), {'leave'}, [r.reason for r in res])
        self.assertEqual(res[0].key, 'switched')

    def test_the_rule_with_no_io(self):
        a = {'rx_ms': self.b_rx}
        self.assertFalse(putback._signed_in_since(a, {'left_rx': self.b_rx}))
        self.assertFalse(putback._signed_in_since(a, {'left_rx': self.b_rx + 90000}))     # a renewal's stamp
        self.assertTrue(putback._signed_in_since(a, {'left_rx': self.b_rx - 600000}))     # another sign-in
        self.assertFalse(putback._signed_in_since(a, {}))                                 # an older record
        self.assertFalse(putback._signed_in_since({}, {'left_rx': self.b_rx}))            # no stamp on record


if __name__ == '__main__':
    unittest.main()
