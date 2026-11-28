import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The one record of the Mac-wide parts of Claude Code's Keychain item (connector logins, plugin secrets, the design
# login: facts.SHARED_SIBLINGS but the overflow pointer), kept by Switchboard as it keeps a saved login, in the
# "Switchboard login (Mac-wide parts)" Keychain item (vault.keep_shared). A saved login keeps only its account's parts,
# and a switch takes the Mac-wide ones from the live item; when Claude Code's item is gone entirely there is no live item
# to take them from, so the put-back's write into no item at all adds them, and nothing else ever uses the record.
# (a) it is kept up to date from Claude Code's item read whole with an account login, and written only when the parts
# change; (b) it is never used while a live item or a plain login file is there, and never by a switch; (c) the
# uninstaller takes it out with the rest; (d) a record that can't be read, or can't be written, changes nothing else;
# (e) it goes when Claude Code's item, read whole with its login, holds no Mac-wide part any more.
# The account engine on its stand-ins (fakes_accounts), a fake clock, made-up *.example accounts and logins.
import json
import os
import shutil
import tempfile
import time
import unittest

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig
from switchboard.accounts import api, capture, context, facts, live, putback, roster, switcher, vault

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
MAC = {'mcpOAuth': {'srv': 'mcp-made-up-2'}, 'pluginSecrets': {'p': 'plugin-made-up'}, 'designOauth': {'grant': 'g'}}


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='mac-wide-parts-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)                 # two sign-ins never share an end stamp to the millisecond
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)   # Claude Code is on B
        self.saved_b = self.rig.vault('uuid-b')
        self.since = len(self.rig.kc.calls)     # the Keychain calls a test makes, after the two accounts are kept

    def tearDown(self):
        self.use.__exit__(None, None, None)
        putback.reset()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    # what is kept

    def record(self):
        text = self.rig.sec.get(vault.SERVICE_SHARED, self.rig.service)
        return None if text is None else json.loads(text)

    def note(self):
        with open(self.ctx.path(vault.SHARED_NOTE)) as f:
            return json.load(f)

    def calls(self, verb):
        return [c for c in self.rig.kc.calls[self.since:] if c[0] == verb and c[1] == vault.SERVICE_SHARED]

    def read_before_the_write(self):
        """Reads of the record before the first write of Claude Code's item (after it, the keep step may read the item
        whole and keep its parts, which is the record kept up to date, not used)."""
        calls = self.rig.kc.calls[self.since:]
        first = [i for i, c in enumerate(calls) if c[0] == 'write' and c[1] == self.rig.service]
        upto = calls[:first[0]] if first else calls
        return [c for c in upto if c[0] == 'read' and c[1] == vault.SERVICE_SHARED]

    # Claude Code's side

    def live_with(self, parts, text=None):
        """Claude Code's item holding its login and these Mac-wide parts (a connector login renewed, say)."""
        blob = live.account_bound(json.loads(text or self.rig.live_text()))
        blob.update(parts)
        self.rig.set_live(live.dump(blob))

    def lose_all(self):
        """The item gone entirely: no live object at all."""
        self.rig.sec.drop(self.rig.service, self.rig.account)

    def look(self, after=5.0):
        self.clock.advance(after)
        return putback.look(self.ctx)

    def put_back(self, after=putback.PUT_BACK_AFTER_S + 1):
        self.look(0)
        res = self.look(after)
        self.assertEqual(res.action, 'put_back', res.reason)
        return json.loads(self.rig.live_text())

    def cred(self):
        return os.path.join(self.rig.home, '.claude', '.credentials.json')


class TheRecordIsKeptUpToDate(Base):
    def test_the_page_add_keeps_the_parts_of_the_item_it_read_and_their_size_is_noted(self):
        self.assertEqual(self.record(), MCP)
        self.assertNotIn('mcpOAuth', json.loads(self.saved_b))                       # the saved login: its account's
        n = self.note()[self.rig.service]
        self.assertEqual(n['size'], len(live.dump(MCP).encode('utf-8')))
        self.assertEqual(n['size'], 74)                                             # this made-up object
        self.assertEqual(sorted(n), ['at', 'sha', 'size'])                          # no secret in the note

    def test_a_renewed_connector_login_is_kept_at_the_next_look_and_only_once(self):
        self.live_with(MAC)
        self.assertEqual(self.look().action, 'leave')
        self.assertEqual(self.record(), MAC)
        writes = len(self.calls('write'))
        for _ in range(5):
            self.look()
        self.assertEqual(len(self.calls('write')), writes)                          # unchanged parts: nothing written

    def test_the_keep_pass_keeps_them_too(self):
        self.live_with(MAC)
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.record(), MAC)

    def test_another_process_that_kept_the_same_parts_is_not_written_again(self):
        vault._SHARED_SEEN.clear()                                                  # a process with nothing in memory
        writes = len(self.calls('write'))
        self.look()
        self.assertEqual(len(self.calls('write')), writes)

    def test_never_from_an_item_without_a_login_nor_from_the_plain_login_file(self):
        self.rig.set_live(live.dump(MAC))                                           # saved back without the login
        self.look()
        self.assertEqual(self.record(), MCP)
        self.lose_all()
        with open(self.cred(), 'w') as f:
            f.write(live.dump(dict(live.account_bound(json.loads(self.b_text)), **MAC)))
        lr = live.read_live(self.ctx, fresh=True)
        self.assertEqual((lr.read.provenance, lr.login.kind), ('file', 'oauth'))
        self.assertFalse(vault.keep_shared(self.ctx, lr))
        self.assertEqual(self.record(), MCP)

    def test_never_claude_codes_overflow_pointer(self):
        self.live_with(dict(MAC, keychainOverflow={'a': 'made-up'}))
        self.look()
        self.assertEqual(self.record(), MAC)
        self.assertEqual(facts.OVERFLOW_KEY, 'keychainOverflow')


class OnlyIntoNoItemAtAll(Base):
    def test_a_put_back_into_no_item_at_all_writes_the_saved_login_whole_with_them(self):
        self.lose_all()
        blob = self.put_back()
        self.assertEqual(blob, dict(json.loads(self.saved_b), **MCP))
        self.assertEqual(len(self.calls('read')), 1)                                # read once, for that write

    def test_an_item_without_a_login_keeps_its_own_parts_and_the_record_is_not_read(self):
        self.rig.set_live(json.dumps({'discoveryState': {'made-up': 1}}))
        blob = self.put_back()
        self.assertNotIn('mcpOAuth', blob)
        self.rig.set_live(live.dump(MAC))
        blob = self.put_back(putback.PUT_BACK_WAITS[1] + 1)
        self.assertEqual(blob['mcpOAuth'], MAC['mcpOAuth'])
        self.assertEqual(self.read_before_the_write(), [])

    def test_a_login_only_in_the_plain_login_file_goes_in_as_it_is(self):
        filed = live.dump(dict(live.account_bound(json.loads(self.b_text)), **MAC))
        with open(self.cred(), 'w') as f:
            f.write(filed)
        self.lose_all()
        self.look(0)
        res = self.look()
        self.assertEqual(res.action, putback.PROMOTE, res.reason)
        blob = json.loads(self.rig.live_text())
        self.assertEqual(blob['claudeAiOauth'], json.loads(filed)['claudeAiOauth'])
        self.assertEqual(blob['mcpOAuth'], MAC['mcpOAuth'])                         # the file's own, not the record's
        self.assertEqual(self.read_before_the_write(), [])
        self.assertEqual(self.record(), MAC)            # the item read whole after it holds these now: kept up to date

    def test_a_switch_never_uses_it(self):
        self.live_with({}, self.b_text)                                             # the item has no Mac-wide parts
        res = switcher.switch(self.ctx, 'uuid-a')
        self.assertTrue(res.ok, res.note)
        self.assertNotIn('mcpOAuth', json.loads(self.rig.live_text()))
        self.assertEqual(self.read_before_the_write(), [])
        self.assertEqual(self.record(), MCP)                                        # and it stays as it was


class TheUninstaller(Base):
    def test_it_takes_the_record_out_with_the_rest(self):
        said = api.remove_all()
        self.assertIsNone(self.record())
        self.assertFalse(os.path.exists(self.ctx.path(vault.SHARED_NOTE)))
        self.assertIn('Earlier logins and recovery copies: 1 removed.', said)        # counted as a recovery copy
        self.assertEqual([k for k in self.rig.sec.items() if k[0].startswith('Switchboard')], [])


class ARecordThatCannotBeUsed(Base):
    def check_written_alone(self):
        self.lose_all()
        blob = self.put_back()
        self.assertEqual(blob, json.loads(self.saved_b))                            # as before the record was kept
        self.assertEqual(len(putback.load_record(self.ctx)['done']), 1)
        self.assertEqual(len([e for e in self.rig.events if e[2].get('h') == 'repair']), 1)

    def test_one_that_cannot_be_read_changes_nothing(self):
        self.rig.kc.rule('read', service=vault.SERVICE_SHARED, why='locked', times=-1)
        self.check_written_alone()

    def test_one_that_is_not_an_object_of_parts_changes_nothing(self):
        self.rig.sec.put(vault.SERVICE_SHARED, self.rig.service, 'not a record')
        self.check_written_alone()

    def test_one_that_cannot_be_written_is_said_once_and_nothing_else_changes(self):
        self.rig.kc.rule('write', service=vault.SERVICE_SHARED, why='denied', times=-1)
        for parts in (MAC, dict(MAC, mcpOAuth={'srv': 'mcp-made-up-3'})):
            self.live_with(parts)
            self.assertEqual(self.look().action, 'leave')
        self.assertEqual(self.record(), MCP)
        said = [t for t in self.rig.logs if 'Mac-wide parts' in t]
        self.assertEqual(len(said), 1, said)
        for secret in ('mcp-made-up', 'plugin-made-up', 'sk-ant-'):
            self.assertNotIn(secret, said[0])
        roster.forget_live()
        self.assertEqual(api.live().account_id, 'uuid-b')                           # the sign-in is as it was


class TheRecordGoesWithTheParts(Base):
    """Every connector removed: Claude Code's item read whole with its login and no Mac-wide part. The record of that
    item goes (the Keychain item and its note), once, so a later put-back into no item at all writes no removed login."""

    def remove_every_connector(self):
        self.live_with({}, self.b_text)                                             # the login alone

    def test_the_record_goes_when_the_parts_go_and_only_once(self):
        self.remove_every_connector()
        self.assertEqual(self.look().action, 'leave')
        self.assertIsNone(self.record())
        self.assertFalse(os.path.exists(self.ctx.path(vault.SHARED_NOTE)))         # the note too
        self.assertEqual(len(self.calls('delete')), 1)
        for _ in range(3):
            self.look()
        self.assertEqual(len(self.calls('delete')), 1)                              # nothing noted: nothing asked
        self.assertEqual(self.calls('write'), [])
        self.live_with(MAC)                                                         # a connector added again
        self.look()
        self.assertEqual(self.record(), MAC)

    def test_the_keep_pass_takes_it_out_too(self):
        self.remove_every_connector()
        capture.keep_pass(self.ctx, force=True)
        self.assertIsNone(self.record())

    def test_it_stays_on_a_degraded_read_a_cached_one_a_plain_file_or_one_without_a_login(self):
        alone = live.dump(live.account_bound(json.loads(self.b_text)))
        for read in (api.Read(api.PRESENT, value=alone, provenance='keychain', degraded=True),
                     api.Read(api.PRESENT, value=alone, provenance='cache'),
                     api.Read(api.PRESENT, value=alone, provenance='file')):
            lr = live.LiveRead(read, live.parse(alone), self.rig.service)
            self.assertFalse(vault.keep_shared(self.ctx, lr), read)
        self.rig.set_live(json.dumps({'discoveryState': {'made-up': 1}}))           # no login and no part
        self.assertFalse(vault.keep_shared(self.ctx, live.read_live(self.ctx, fresh=True)))
        self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=-1)     # macOS does not answer
        with open(self.cred(), 'w') as f:
            f.write(alone)
        lr = live.read_live(self.ctx, fresh=True)
        self.assertEqual((lr.read.provenance, lr.read.degraded, lr.login.full_pair), ('file', True, True))
        self.assertFalse(vault.keep_shared(self.ctx, lr))
        self.assertEqual(self.record(), MCP)
        self.assertIn(self.rig.service, self.note())
        self.assertEqual(self.calls('delete'), [])

    def test_one_that_cannot_be_taken_out_is_said_once_changes_nothing_and_a_later_look_tries_again(self):
        self.rig.kc.rule('delete', service=vault.SERVICE_SHARED, why='denied', times=2)
        self.remove_every_connector()
        self.look()
        self.look()
        self.assertEqual(self.record(), MCP)
        self.assertIn(self.rig.service, self.note())
        said = [t for t in self.rig.logs if 'Mac-wide parts' in t]
        self.assertEqual(len(said), 1, said)
        for secret in ('mcp-made-up', 'sk-ant-'):
            self.assertNotIn(secret, said[0])
        self.look()
        self.assertIsNone(self.record())
        self.assertFalse(os.path.exists(self.ctx.path(vault.SHARED_NOTE)))

    def test_a_put_back_into_no_item_at_all_after_that_writes_no_connector_logins(self):
        self.remove_every_connector()
        self.look()
        self.assertIsNone(self.record())
        self.lose_all()
        blob = self.put_back()
        self.assertEqual(blob, json.loads(self.saved_b))                            # the saved login alone
        for name in facts.SHARED_SIBLINGS:
            self.assertNotIn(name, blob)
        self.assertEqual(self.calls('read'), [])                                    # nothing noted: not even asked


if __name__ == '__main__':
    unittest.main()
