import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A put-back, a promote or a repair that lands while Claude Code signs out is taken back: the only place Switchboard
# removes a login from Claude Code's item outside a switch's roll back. Five bounds, each of which alone keeps the item
# untouched: (1) it is a write of ours (the record's note of it); (2) it landed less than TAKE_BACK_S ago; (3) Claude
# Code's settings are there and name nobody; (4) the item still holds the login we wrote (another part may have changed:
# a chat that saved its own part in between kept our login); (5) the settings file is the lived-in one, not one Claude
# Code made anew (it writes a small new one when it finds none or cannot parse the old one, which also names nobody).
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
from switchboard.accounts import api, autoloop, context, putback
from switchboard.accounts import signin_watch as sw

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
LIVED_IN = {'projects': dict(('/made-up/project-%03d' % n, {'history': ['x' * 80] * 3}) for n in range(80))}
ANEW = "Claude Code's settings file is much smaller than a minute ago; Switchboard left its write as it is"


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='take-back-')
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
        self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'), **LIVED_IN)
        self.assertGreater(os.path.getsize(self.rig.config_path()), 20000)        # a lived-in settings file
        patcher = mock.patch.object(autoloop, 'notify_put_back', lambda res: None)
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def watch(self, after=1.0, **hint):
        self.clock.advance(after)
        h = dict(sw.no_hint())
        h.update(hint)
        h['login_file_written'] = h['login_file'] in ('appeared', 'changed')
        return api.watch_look(h)

    def landed(self, created):
        """A put-back of ours: into no item at all (created), or over an item without a login."""
        self.rig.sec.drop(self.rig.service, self.rig.account)
        if not created:
            self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))
        self.watch(0.2, keychain=True)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        return self.rig.live_text()

    def sign_out_settings(self):
        """The sign-out's second step: the settings' account cleared, the rest of the file as it is."""
        self.rig.set_config(None)

    def made_anew(self):
        """Claude Code made its settings file anew: a small new file that names nobody."""
        with open(self.rig.config_path(), 'w') as f:
            json.dump({'numStartups': 1, 'installMethod': 'made-up'}, f)

    def oauth(self):
        text = self.rig.live_text()
        return json.loads(text).get('claudeAiOauth') if text else None

    def removals(self):
        return [c for c in self.rig.kc.calls if c[0] == 'delete' and c[1] == self.rig.service]


class AChatsSaveBetweenOurWriteAndTheSignOut(Base):
    def chat_saves(self):
        """A running chat saves its own part (a connector login added); its fresh read found our login and kept it."""
        blob = json.loads(self.rig.live_text())
        blob['mcpOAuth'] = dict(blob.get('mcpOAuth') or {}, **{'chat-server': {'accessToken': 'mcp-made-up-2'}})
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        return blob

    def our_login_is_taken_out_and_the_chats_part_stays(self, created):
        self.landed(created)
        self.clock.advance(1.0)
        saved = self.chat_saves()
        self.assertEqual(self.watch(1.0, keychain=True).action, 'leave')     # still named: nothing to do
        self.assertIsNotNone(putback.load_record(self.ctx)['written'])      # another part changed: the note stays
        self.sign_out_settings()
        self.watch(0.3, settings=True)
        now = json.loads(self.rig.live_text())
        self.assertNotIn('claudeAiOauth', now)
        self.assertEqual(now, dict((k, v) for k, v in saved.items() if k != 'claudeAiOauth'))
        self.assertEqual(self.removals(), [])                               # an ordinary write, never a delete
        self.assertIsNone(putback.load_record(self.ctx)['written'])

    def test_into_an_item_we_created_our_login_is_taken_out_and_the_chats_part_stays(self):
        self.our_login_is_taken_out_and_the_chats_part_stays(created=True)

    def test_over_an_item_without_a_login_our_login_is_taken_out_and_the_chats_part_stays(self):
        self.our_login_is_taken_out_and_the_chats_part_stays(created=False)

    def test_a_renewal_in_between_is_never_touched(self):
        self.landed(created=False)
        renewed = self.rig.claude_renews()
        self.sign_out_settings()
        for _ in range(5):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), renewed)
        self.assertIsNotNone(putback.load_record(self.ctx)['written'])   # its login is not ours: the note stays its
                                                                         # minute and takes nothing back

    def test_a_sign_in_in_between_is_never_touched(self):
        self.landed(created=True)
        new = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')
        self.rig.set_live(new)
        self.sign_out_settings()
        for _ in range(5):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), new)


class ASettingsFileMadeAnew(Base):
    def test_nothing_is_taken_back_at_any_look_for_sixty_seconds_and_it_is_said_once(self):
        ours = self.landed(created=True)
        self.clock.advance(1.0)
        self.made_anew()
        looks = [self.watch(0.5, settings=True)] + [self.watch(2.0) for _ in range(35)]
        self.assertGreater(self.clock.wall() - putback.load_record(self.ctx).get('wrote_at', 0), putback.TAKE_BACK_S)
        self.assertEqual(self.rig.live_text(), ours)
        self.assertEqual(self.removals(), [])
        self.assertEqual(len([t for t in self.rig.logs if t == ANEW]), 1, self.rig.logs[-5:])
        self.assertEqual(set(r.action for r in looks), {'leave'})
        self.rig.set_config(None, **LIVED_IN)                  # a lived-in file naming nobody: the note has aged out
        for _ in range(3):
            self.watch(1.0, settings=True)
        self.assertEqual(self.rig.live_text(), ours)

    def test_over_an_item_without_a_login_too(self):
        ours = self.landed(created=False)
        self.made_anew()
        for _ in range(10):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), ours)

    def test_made_anew_in_the_same_hold_as_the_write_leaves_it(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.watch(0.2, keychain=True)
        state = {'wrote': False}

        def at(verb, service, account):
            if service != self.rig.service:
                return
            if verb == 'write':
                state['wrote'] = True
            elif verb == 'read' and state['wrote']:
                state['wrote'] = False
                self.made_anew()
        self.rig.kc.on_call = at
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.rig.kc.on_call = None
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(self.oauth(), json.loads(self.b_text)['claudeAiOauth'])
        self.assertEqual(self.removals(), [])

    def test_a_sign_out_of_a_lived_in_file_is_still_taken_back(self):
        self.landed(created=True)
        self.clock.advance(2.0)
        self.sign_out_settings()                                   # a few hundred bytes fewer: the lived-in file
        res = self.watch(0.3, settings=True)
        self.assertIn('took its write back', res.line or '')
        self.assertIsNone(self.rig.live_text())


class EachBoundAloneStopsIt(Base):
    def test_1_a_login_claude_code_wrote_is_never_removed(self):
        self.landed(created=True)
        mine = json.loads(self.rig.server.add('uuid-b', 'ana@home.example', 'org-b'))
        self.rig.set_live(json.dumps(mine, separators=(',', ':')))     # not a write of ours
        self.sign_out_settings()
        for _ in range(5):
            self.watch(1.0)
        self.assertEqual(self.oauth(), mine['claudeAiOauth'])

    def test_2_only_within_sixty_seconds(self):
        ours = self.landed(created=True)
        self.clock.advance(putback.TAKE_BACK_S)
        self.sign_out_settings()
        for _ in range(5):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), ours)

    def test_2_a_take_back_that_could_not_land_in_time_is_not_tried_after(self):
        ours = self.landed(created=True)
        self.clock.advance(2.0)
        self.rig.kc.rule('delete', service=self.rig.service, why='locked', times=-1)
        self.sign_out_settings()
        self.assertEqual(self.watch(0.3, settings=True).action, 'wait')        # refused: tried again later
        self.clock.advance(putback.TAKE_BACK_S)
        del self.rig.kc.rules[:]
        for _ in range(3):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), ours)

    def test_3_settings_that_name_someone_cannot_be_read_or_are_missing(self):
        ours = self.landed(created=True)
        for _ in range(3):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), ours)
        with open(self.rig.config_path(), 'w') as f:
            f.write('{not json' + 'x' * 30000)
        for _ in range(3):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), ours)
        os.unlink(self.rig.config_path())
        for _ in range(3):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), ours)

    def test_4_the_item_must_still_hold_the_login_we_wrote(self):
        self.landed(created=False)
        blob = json.loads(self.rig.live_text())
        blob['claudeAiOauth'] = dict(blob['claudeAiOauth'], expiresAt=blob['claudeAiOauth']['expiresAt'] + 1)
        changed = json.dumps(blob, separators=(',', ':'))
        self.rig.set_live(changed)                                    # one field of the login changed
        self.sign_out_settings()
        for _ in range(5):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), changed)

    def test_5_a_settings_file_made_anew(self):
        ours = self.landed(created=True)
        self.made_anew()
        for _ in range(5):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), ours)


if __name__ == '__main__':
    unittest.main()
