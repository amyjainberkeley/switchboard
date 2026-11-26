import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The automatic loop with the real keep pass, when Claude Code's emptied login has lost its end date: the kept sign-in
# is put back once; when Claude Code empties it again, nothing is written, and the same pass makes the forced switch
# to an account with room. Stand-ins only (fakes_accounts, fakes_autoswitch); made-up *.example accounts.
import json
import unittest

from fakes_autoswitch import set_policy
from switchboard.accounts import roster
from test_put_back_loop import RigLoop


def emptied_without_date(text):
    blob = json.loads(text)
    blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
    blob['claudeAiOauth'].pop('refreshTokenExpiresAt', None)
    return json.dumps(blob, separators=(',', ':'))


class AnEmptiedLoginWithNoEndDate(RigLoop):
    def writes(self):
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)

    def test_the_second_emptying_brings_the_forced_switch_in_the_same_pass(self):
        set_policy(enabled=True)
        self.loop.run_pass()
        held = self.rig.vault('uuid-b')
        self.clock.advance(600)
        renewed = self.rig.server.refresh_for(self.rig.live_text())     # Claude Code renews B by itself
        self.rig.set_live(emptied_without_date(renewed))                  # then Anthropic refuses it: emptied
        roster.forget_live()
        self.clock.advance(30)
        before = self.writes()
        first = self.loop.run_pass()
        self.assertEqual(self.writes() - before, 1)                       # the kept sign-in, once
        self.assertEqual(self.rig.live_text().count('"refreshToken":""'), 0)
        self.assertNotEqual(first.decision.action, 'switch', first.decision.reason)
        self.rig.set_live(emptied_without_date(held))                     # Claude Code is refused again
        roster.forget_live()
        self.clock.advance(30)
        res = self.loop.run_pass()
        self.assertEqual((res.decision.action, res.decision.kind, res.decision.target), ('switch', 'forced', 'uuid-a'),
                         res.decision.reason)
        self.assertIsNotNone(res.switched)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth']['refreshToken'],
                         json.loads(self.rig.vault('uuid-a'))['claudeAiOauth']['refreshToken'])
        self.assertEqual(self.writes() - before, 2)                       # the once, and the forced switch to A
        self.assertEqual(len([e for e in self.rig.events if e[0].startswith('ana@home.example needs sign-in')]), 1)


if __name__ == '__main__':
    unittest.main()
