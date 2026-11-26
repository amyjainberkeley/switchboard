import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The look for a newer version by itself: off until update_check is "daily", then at most once a day from the page's
# loop, its request carrying the agent name alone, and one quiet line in `switchboard status` and in the page's state
# when a newer version is out. The web is a stand-in that writes down every request.
import io
import os
import stat
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import core, newer, server
import test_keep

WEB = 'https://example.invalid/sb/main/install.sh'
T0 = 1790000000.0
LINE = 'Switchboard 9.0.0 is out (this is %s). Install it with: switchboard update' % core.VERSION


class Web(object):
    def __init__(self, body=b"VERSION = '9.0.0'\n"):
        self.body = body
        self.calls = []

    def request(self, method, url, headers=None, body=None, timeout_s=10.0):
        self.calls.append((method, url, dict(headers or {}), body))
        return 200, {}, self.body


class Daily(test_keep.Sandbox):
    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.data, 'app'), exist_ok=True)
        with open(os.path.join(self.data, 'app', 'source'), 'w') as f:
            f.write(WEB + '\n')
        self.web = Web()
        p = mock.patch.object(newer, 'HTTP', self.web)
        p.start()
        self.addCleanup(p.stop)
        self.brew = []
        self.find_brew = lambda: self.brew.append('asked') or None

    def tick(self, now):
        t = newer.tick(now=now, find_brew=self.find_brew)
        if t is not None:
            t.join(30)
        return t

    def test_off_by_default_and_while_off_nothing_is_asked(self):
        self.assertEqual(newer.check_setting(), 'off')
        for i in range(5):
            self.assertIsNone(self.tick(T0 + i * newer.DAY))
        self.assertEqual((self.web.calls, self.brew), ([], []))
        self.assertFalse(os.path.exists(os.path.join(self.data, newer.KEPT)))
        newer.set_check('off')
        self.assertIsNone(self.tick(T0))
        self.assertEqual(self.web.calls, [])

    def test_on_it_looks_at_most_once_a_day(self):
        self.assertEqual(newer.set_check('daily'), 'Switchboard looks for a newer version once a day.')
        self.assertIsNotNone(self.tick(T0))
        for later in (1, 60, 3600, newer.DAY - 1):
            self.assertIsNone(self.tick(T0 + later), later)
        self.assertEqual(len(self.web.calls), 1)
        self.assertIsNotNone(self.tick(T0 + newer.DAY))
        self.assertEqual(len(self.web.calls), 2)

    def test_its_request_carries_the_agent_name_and_nothing_else(self):
        newer.set_check('daily')
        self.tick(T0)
        self.assertEqual(self.web.calls, [('GET', 'https://example.invalid/sb/main/switchboard/core.py',
                                           {'User-Agent': 'Switchboard/%s' % core.VERSION}, None)])

    def test_what_it_found_is_kept_owner_only(self):
        newer.set_check('daily')
        self.tick(T0)
        path = os.path.join(self.data, newer.KEPT)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(newer.kept(), {'schema': 1, 'looked_at': T0, 'newest_version': '9.0.0', 'problem': None})

    def test_a_failed_look_counts_and_the_next_is_a_day_later(self):
        newer.set_check('daily')
        self.web.body = b'<html>'
        self.tick(T0)
        self.assertEqual(newer.kept()['problem'], 'Could not look for a newer version: example.invalid sent no '
                                                  'version Switchboard could read.')
        self.assertIsNone(self.tick(T0 + 600))
        self.assertEqual(len(self.web.calls), 1)
        self.assertIsNone(newer.line())

    def test_a_clock_set_back_does_not_hold_the_next_look_back(self):
        newer.set_check('daily')
        self.tick(T0)
        self.assertIsNotNone(self.tick(T0 - 7200))

    def test_a_value_it_does_not_take(self):
        with self.assertRaises(core.SwitchError) as e:
            newer.set_check('weekly')
        self.assertEqual(str(e.exception), 'update_check is off or daily, not weekly.')
        core.set_setting(newer.CHECK_KEY, 'hourly')                 # written by hand: read as off
        self.assertEqual(newer.check_setting(), 'off')
        self.assertIsNone(self.tick(T0))

    def test_the_line_only_while_on_and_only_when_newer(self):
        self.assertIsNone(newer.line())
        core.write_json(os.path.join(self.data, newer.KEPT), {'looked_at': T0, 'newest_version': '9.0.0'})
        self.assertIsNone(newer.line())                             # off: never said, whatever is kept
        newer.set_check('daily')
        self.assertEqual(newer.line(), LINE)
        for same_or_older in (core.VERSION, '0.0.1', 'v9', None):
            core.write_json(os.path.join(self.data, newer.KEPT), {'looked_at': T0, 'newest_version': same_or_older})
            self.assertIsNone(newer.line(), same_or_older)

    def test_status_says_it_in_one_line_under_the_list(self):
        newer.set_check('daily')
        self.tick(T0)
        code, out = self.run_cli('status')
        self.assertEqual(code, 0)
        self.assertTrue(out.endswith('\n\n' + LINE + '\n'), out)
        self.assertEqual(out.count('is out'), 1)
        newer.set_check('off')
        self.assertNotIn('is out', self.run_cli('status')[1])

    def test_the_page_state_carries_the_line(self):
        self.assertIsNone(server.view()['newer'])
        newer.set_check('daily')
        self.tick(T0)
        self.assertEqual(server.view()['newer'], LINE)

    def test_the_page_loop_calls_it(self):
        seen = []
        with mock.patch.object(newer, 'tick', lambda *a, **k: seen.append(1)):
            out = io.StringIO()
            with redirect_stdout(out):
                server.loop(ticks=1)
        self.assertEqual(seen, [1])


if __name__ == '__main__':
    unittest.main()
