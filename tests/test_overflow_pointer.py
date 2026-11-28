import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Claude Code 2.1.331, behind a server flag, can move long sections of its Keychain item into the items
# "<service>-overflow-a" / "-b" and leave a pointer, "keychainOverflow", in the item. The pointer belongs to this Mac
# (it says where this Mac's overflow items are), so every write of the live item keeps the live pointer as it is now,
# present or absent, and never brings one back from a saved copy. When the pointer carries "login", the account login
# itself sits encrypted in an overflow item: Claude Code is signed in, in a form Switchboard cannot read ('sealed'). It
# is never a lost sign-in, and a switch away from it would lose it, so the switch refuses. Stand-ins only; made-up
# *.example accounts.
import json
import os
import shutil
import tempfile
import time
import unittest

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig
from switchboard.accounts import context, facts, live, putback, switcher
from switchboard.accounts.api import AccountsError

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
HERE = {'slot': 'a', 'key': 'bWFkZS11cC1rZXktaGVyZQ=='}             # this Mac's pointer now
OLD = {'slot': 'b', 'key': 'bWFkZS11cC1rZXktb2xkLWNvcHk='}          # a pointer kept in a saved copy
SEALED = dict(MCP, organizationUuid='org-made-up',
              keychainOverflow={'slot': 'a', 'key': 'bWFkZS11cC1rZXktc2VhbGVk', 'login': '0123456789abcdef'})
OAUTH = {'accessToken': 'sk-ant-oat01-made-up', 'refreshToken': 'sk-ant-ort01-made-up', 'expiresAt': 1}


def dump(blob):
    return json.dumps(blob, separators=(',', ':'))


class ThePointerIsTheMacs(unittest.TestCase):
    def test_it_is_a_machine_wide_sibling_and_its_fact_names_the_version(self):
        self.assertEqual(facts.OVERFLOW_KEY, 'keychainOverflow')
        self.assertIn(facts.OVERFLOW_KEY, facts.SHARED_SIBLINGS)
        self.assertEqual(facts.FACTS['keychain.overflow'][1], '2.1.331')

    def test_a_switch_keeps_the_live_pointer_over_the_saved_copys(self):
        out = live.compose({'claudeAiOauth': OAUTH, 'keychainOverflow': OLD}, dict(MCP, keychainOverflow=HERE))
        self.assertEqual(out['keychainOverflow'], HERE)
        self.assertEqual(out['claudeAiOauth'], OAUTH)

    def test_a_pointer_the_live_item_does_not_have_is_never_written_from_a_saved_copy(self):
        out = live.compose({'claudeAiOauth': OAUTH, 'keychainOverflow': OLD}, dict(MCP))
        self.assertNotIn('keychainOverflow', out)

    def test_with_no_live_item_at_all_no_pointer_is_written_from_a_saved_copy(self):
        out = live.compose({'claudeAiOauth': OAUTH, 'keychainOverflow': OLD, 'mcpOAuth': {'x': 1}}, None)
        self.assertNotIn('keychainOverflow', out)
        self.assertEqual(out['claudeAiOauth'], OAUTH)

    def test_a_put_back_over_an_item_without_a_login_keeps_its_pointer_and_over_nothing_writes_none(self):
        saved = {'claudeAiOauth': OAUTH, 'keychainOverflow': OLD}
        self.assertEqual(live.compose(saved, dict(MCP, keychainOverflow=HERE))['keychainOverflow'], HERE)
        self.assertNotIn('keychainOverflow', live.compose(saved, {}))

    def test_a_run_as_copy_carries_no_pointer(self):
        self.assertNotIn('keychainOverflow', live.account_bound({'claudeAiOauth': OAUTH, 'keychainOverflow': OLD}))


class TheSealedKind(unittest.TestCase):
    def test_a_pointer_with_login_and_no_login_in_the_item_is_sealed(self):
        self.assertEqual(live.parse(dump(SEALED)).kind, 'sealed')

    def test_a_pointer_without_login_is_still_no_login(self):
        self.assertEqual(live.parse(dump(dict(MCP, keychainOverflow=HERE))).kind, 'no_login')

    def test_a_login_in_the_item_is_read_as_it_is_whatever_the_pointer_says(self):
        got = live.parse(dump(dict(SEALED, claudeAiOauth=OAUTH)))
        self.assertEqual(got.kind, 'oauth')
        self.assertTrue(got.full_pair)

    def test_a_sealed_login_is_never_missing(self):
        self.assertFalse(putback.is_missing(putback.Seen(live='present', login='sealed')))
        v = putback.decide(putback.Seen(live='present', login='sealed', named='uuid-b', saved='ok'), None, [],
                           time.time())
        self.assertEqual(v.action, 'leave')
        self.assertIn('cannot read', v.reason)


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='overflow-pointer-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        putback.reset()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def live_blob(self):
        return json.loads(self.rig.live_text())

    def live_writes(self):
        return self.rig.kc.count('write', self.rig.service)

    def look(self, after=5.0):
        self.clock.advance(after)
        return putback.look(self.ctx)

    def kept_with_pointer(self, aid, pointer):
        """A saved copy holding a pointer, as an older build kept it (a saved login keeps only its account's parts now,
        vault.bound_text, so keeping one never stores a pointer): put in the vault as that build left it."""
        self.assertNotIn('keychainOverflow', json.loads(self.rig.vault(aid)))
        text = dump(dict(json.loads(self.rig.vault(aid)), keychainOverflow=pointer))
        self.rig.sec.put('Switchboard login', aid, text)
        return text


class TheSwitch(Base):
    def test_the_live_pointer_stays_and_the_targets_old_one_is_not_written(self):
        a = self.rig.keep('uuid-a', 'ana@work.example', 'org-a', siblings={'keychainOverflow': OLD})
        self.assertEqual(json.loads(self.kept_with_pointer('uuid-a', OLD))['keychainOverflow'], OLD)
        self.clock.advance(1.0)
        self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=dict(MCP, keychainOverflow=HERE))
        res = switcher.switch(self.ctx, 'uuid-a')
        self.assertTrue(res.ok, res.note)
        blob = self.live_blob()
        self.assertEqual(blob['claudeAiOauth'], json.loads(a)['claudeAiOauth'])
        self.assertEqual(blob['keychainOverflow'], HERE)

    def test_with_no_live_pointer_none_is_written(self):
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a', siblings={'keychainOverflow': OLD})
        self.clock.advance(1.0)
        self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)
        res = switcher.switch(self.ctx, 'uuid-a')
        self.assertTrue(res.ok, res.note)
        self.assertNotIn('keychainOverflow', self.live_blob())

    def setup_sealed(self):
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)
        self.rig.keep('uuid-b', 'ana@home.example', 'org-b')
        self.saved = {aid: self.rig.vault(aid) for aid in ('uuid-a', 'uuid-b')}

    def assert_nothing_changed(self, text):
        self.assertEqual(self.rig.live_text(), text)
        self.assertEqual((self.rig.config().get('oauthAccount') or {}).get('accountUuid'), 'uuid-b')
        self.assertEqual({aid: self.rig.vault(aid) for aid in ('uuid-a', 'uuid-b')}, self.saved)
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))

    def test_a_switch_away_from_a_sealed_login_refuses_with_plain_words_and_changes_nothing(self):
        self.setup_sealed()
        text = dump(SEALED)
        self.rig.set_live(text)
        before = self.live_writes()
        with self.assertRaises(AccountsError) as e:
            switcher.switch(self.ctx, 'uuid-a')
        self.assertIn("form Switchboard can't read", str(e.exception))
        self.assertIn('Nothing was changed', str(e.exception))
        self.assertEqual(self.live_writes(), before)
        self.assert_nothing_changed(text)

    def test_a_login_sealed_between_the_first_look_and_the_locked_look_is_refused_too(self):
        self.setup_sealed()
        text = dump(SEALED)
        lock = facts.refresh_lock(self.rig.env, self.rig.home)

        def on_call(verb, service, account):
            if verb == 'read' and service == self.rig.service and os.path.isdir(lock):
                self.rig.set_live(text)                 # Claude Code saved the split form meanwhile
        self.rig.kc.on_call = on_call
        before = self.live_writes()
        with self.assertRaises(AccountsError) as e:
            switcher.switch(self.ctx, 'uuid-a')
        self.rig.kc.on_call = None
        self.assertIn("form Switchboard can't read", str(e.exception))
        self.assertEqual(self.live_writes(), before)
        self.assert_nothing_changed(text)


class ThePutBack(Base):
    def setUp(self):
        super().setUp()
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)
        self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings={'keychainOverflow': OLD})
        self.saved_b = self.kept_with_pointer('uuid-b', OLD)
        self.assertEqual(json.loads(self.saved_b)['keychainOverflow'], OLD)

    def test_a_put_back_over_no_item_writes_no_pointer_from_the_saved_copy(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.look(0)
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        blob = self.live_blob()
        self.assertEqual(blob['claudeAiOauth'], json.loads(self.saved_b)['claudeAiOauth'])
        self.assertNotIn('keychainOverflow', blob)

    def test_a_put_back_over_an_item_with_no_login_and_no_pointer_writes_none(self):
        self.rig.set_live(dump(MCP))
        self.look(0)
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertNotIn('keychainOverflow', self.live_blob())
        self.assertEqual(self.live_blob()['mcpOAuth'], MCP['mcpOAuth'])

    def test_a_sealed_login_is_never_put_back_over_and_it_is_said_once_in_the_log(self):
        text = dump(SEALED)
        self.rig.set_live(text)
        lines = []
        for _ in range(30):
            res = self.look(putback.PUT_BACK_AFTER_S + 1)
            self.assertEqual(res.action, 'leave', res.reason)
            if res.line:
                lines.append(res.line)
        self.assertEqual(self.live_writes(), 0)
        self.assertEqual(self.rig.live_text(), text)
        self.assertEqual(lines, [putback.SEALED_LINE])
        self.assertEqual(len([x for x in self.rig.logs if 'cannot read' in x]), 1, self.rig.logs)
        self.assertEqual([e for e in self.rig.events if e[2].get('h') == 'repair'], [])
        self.assertEqual(self.rig.vault('uuid-b'), self.saved_b)


if __name__ == '__main__':
    unittest.main()
