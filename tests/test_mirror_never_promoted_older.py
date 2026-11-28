import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The standing copy is never what puts an older login into Claude Code's item. While the item is missing, the copy is
# Claude Code's sign-in, and the promote writes it back into the item; but a renewal can land in the item and the item
# go before any look brought the copy up to it. The keep step saves a renewal into the saved copy within a look, so
# the copy goes back into the item only when it is the saved copy (the same refresh token) or newer than it; when it
# is older the newer saved copy goes in instead, and when its age against the saved copy can't be told, nothing does.
# The write only creates the item: one that came back meanwhile (a newer login of Claude Code's) is never written over.
# The account engine on its stand-ins (fakes_accounts) with a fake clock; made-up *.example accounts.
import json
import unittest

from hold_fakes import Base
from switchboard.accounts import capture, putback


class Mirror(Base):
    MIRROR = True

    def setUp(self):
        super().setUp()
        self.look(keychain=True)
        self.assertTrue(self.ours())
        self.hold('looked')

    def oauth(self, text):
        return json.loads(text)['claudeAiOauth'] if text else None

    def missing_until_a_write(self, limit=40.0):
        """The sign-in watch's looks while the item is missing, at the pace they ask for. Returns the answers."""
        answers, t = [], 0.0
        res = self.look(after=0.15, keychain=True)
        while t < limit:
            answers.append(res)
            if res.action in ('promote', 'put_back'):
                break
            step = res.next_s or 1.0
            t += step
            res = self.look(after=step)
        return answers


class AnOlderCopy(Mirror):
    def test_the_newer_saved_copy_goes_in_not_the_copy(self):
        older = self.file_text()
        self.clock.advance(600.0)
        renewed = self.rig.claude_renews()                    # the item moves on; no look has followed it yet
        self.assertIsNotNone(capture.keep_pass(self.ctx, force=True))     # the saved copy has the renewal
        self.cc_deletes_item()
        answers = self.missing_until_a_write()
        self.assertEqual(answers[-1].action, 'promote', [a.reason for a in answers])
        self.assertEqual(self.oauth(self.rig.live_text()), self.oauth(renewed))
        self.assertNotEqual(self.oauth(self.rig.live_text()), self.oauth(older))


class ACopyThatCantBeToldApart(Mirror):
    def test_is_not_written_into_the_item(self):
        # a copy whose refresh token is not the saved one's, with the same sign-in end and the same access end
        blob = json.loads(self.file_text())
        blob['claudeAiOauth']['refreshToken'] = 'sk-ant-ort01-made-up-unknown'
        odd = json.dumps(blob, separators=(',', ':'))
        with putback._HOLD_FILES:
            note = putback._hold_note(self.ctx)
            self.assertIsNotNone(putback._hold_swap(self.ctx, self.cred, putback._stamp(self.cred), note, odd))
        self.assertTrue(self.ours())
        self.cc_deletes_item()
        answers = self.missing_until_a_write(limit=30.0)
        self.assertNotIn('promote', [a.action for a in answers])
        self.assertIsNone(self.rig.live_text())
        self.assertEqual(self.file_text(), odd)               # left as it is: it is Claude Code's sign-in now


class TheCopySameAsTheSavedCopy(Mirror):
    def test_goes_back_into_the_item(self):
        text = self.file_text()
        self.cc_deletes_item()
        answers = self.missing_until_a_write()
        self.assertEqual(answers[-1].action, 'promote')
        self.assertEqual(self.rig.live_text(), text)


class AnItemThatCameBack(Mirror):
    def test_a_newer_login_of_claude_codes_is_never_written_over(self):
        self.cc_deletes_item()
        newer = self.rig.server.refresh_for(self.file_text())
        state = {'done': False}

        def on_call(verb, service, account):
            # right before the promote's write, a Claude Code process saves a newer login into the item
            if verb == 'write' and service == self.rig.service and not state['done']:
                state['done'] = True
                self.rig.kc.store[(service, account)] = newer
        self.rig.kc.on_call = on_call
        try:
            answers = self.missing_until_a_write()
        finally:
            self.rig.kc.on_call = None
        self.assertTrue(state['done'], [a.action for a in answers])
        self.assertEqual(self.rig.live_text(), newer)
        self.look(after=1.0, keychain=True)
        self.assertEqual(self.file_text(), newer)             # the copy follows the newer login


if __name__ == '__main__':
    unittest.main()
