import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A check that could not look is a check that failed, never "nothing to do": when Claude Code's login can't be read,
# the Mac is offline, or it just woke and the network is not back, `switchboard auto check` exits 1 with outcome
# `failed` and one sentence that says why, and `switchboard auto why` says the same and exits 1. A scheduled job that
# watches for 1 hears that nothing was checked. Made-up accounts only; the engine, the network and the clock are
# stand-ins (tests/fakes_autoswitch.py).
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import autocheck
from switchboard.accounts import api, autoloop
from test_auto_check import CheckCase, ok
from test_autoswitch_policy import reading, snap


def judged(s):
    return autocheck.judge(autocheck.parse(['why', '--json']), s, s.settings)


class TheDryRun(unittest.TestCase):
    def readings(self):
        return {'a': reading('a', five=20.0, week=30.0), 'b': reading('b', five=10.0, week=20.0)}

    def test_each_way_of_not_looking_exits_1_with_its_sentence(self):
        for kw, said in ((dict(live_ok=False, live_why='Claude Code is not signed in'),
                          'Nothing was checked: Claude Code is not signed in.'),
                         (dict(live_ok=False, live_why=None),
                          "Nothing was checked: Claude Code's login can't be read right now."),
                         (dict(offline=True), 'Nothing was checked: there is no connection to Anthropic.'),
                         (dict(settling=True), 'Nothing was checked: the Mac just woke, and the network is not back '
                                               'yet.')):
            with self.subTest(kw=kw):
                code, doc = judged(snap(self.readings(), **kw))
                self.assertEqual((code, doc['outcome'], doc['exit_code']), (autoloop.EXIT_ERROR, 'failed', 1))
                self.assertEqual(doc['problem'], said)
                self.assertIn(said, autocheck.lines_of(doc))

    def test_a_check_that_looked_and_stays_still_exits_4(self):
        code, doc = judged(snap(self.readings()))
        self.assertEqual((code, doc['outcome'], doc['problem']), (autoloop.EXIT_NOTHING, 'stays', None))

    def test_claude_code_on_no_saved_account_is_not_a_failure(self):
        code, doc = judged(snap(self.readings(), active='nobody'))
        self.assertEqual((code, doc['outcome']), (autoloop.EXIT_NOTHING, 'stays'))


class TheCheck(CheckCase):
    def test_a_login_that_cannot_be_read_exits_1(self):
        self.engine.live_state = api.UNREADABLE
        self.answers(ok(20.0, 40.0), ok(30.0, 40.0), ok(30.0, 40.0))
        code, out, _ = self.check('--json')
        doc = json.loads(out)
        self.assertEqual((code, doc['outcome'], doc['exit_code']), (autoloop.EXIT_ERROR, 'failed', 1))
        self.assertTrue(doc['problem'].startswith('Nothing was checked: '), doc['problem'])
        self.assertEqual(self.engine.switches, [])

    def test_why_says_the_same_and_exits_1(self):
        self.engine.live_state = api.UNREADABLE
        code, out, _ = self.check('--dry-run')
        self.assertEqual(code, autoloop.EXIT_ERROR)
        self.assertIn('Nothing was checked: ', out)


if __name__ == '__main__':
    unittest.main()
