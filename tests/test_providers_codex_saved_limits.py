import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Codex's saved numbers (codex_chats.saved_limits): the newest token_count line of the newest session files, no
# process and no network. Every file and number here is made up (shapes of Codex 0.169.1's token_count lines).
import os
import shutil
import tempfile
import time
import unittest

import fakes_provider_chats as F
from fakes_providers import make_reach
from switchboard.providers import chat_facts, codex_chats
from switchboard.providers.contract import Limits, Window


class SavedLimits(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='codex-limits-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = os.path.join(self.root, 'home')
        self.chome = F.codex_home(self.home)
        self.reach, _ = make_reach(self.root, env={'HOME': self.home})
        self.now = time.time()
        self.k = 0

    def session(self, lines_, age=60.0, began=None):
        """A session file whose lines are built by lines_(chat); its time is now - age; it is filed under the day
        folder of now - began (default: now - age)."""
        self.k += 1
        tid = 'cccccccc-0000-4000-8000-%012d' % self.k
        c = F.CodexChat(thread=tid, cwd='/Users/maya/site', t0=self.now - (began if began is not None else age))
        c.user_item('x')
        lines_(c)
        path = c.write(self.chome)
        F.touch(path, self.now - age)
        return path

    def test_one_weekly_window_with_the_plan(self):
        at = float(int(self.now) - 300)
        self.session(lambda c: c.token_count(primary={'used_percent': 42.5, 'window_minutes': 10080,
                                                      'resets_at': int(self.now + 86400)}, at=at))
        got = codex_chats.saved_limits(self.reach, self.now)
        self.assertEqual(got, Limits(provider='codex', seat='codex', source='file', plan='pro', reached=None,
                                     read_at=at, windows=(Window(label='weekly', used_pct=42.5,
                                                                           resets_at=float(int(self.now + 86400)),
                                                                           length_min=10080),)))

    def test_a_five_hour_window_and_a_weekly_one(self):
        self.session(lambda c: c.token_count(
            primary={'used_percent': 10.0, 'window_minutes': 300, 'resets_at': int(self.now + 3600)},
            secondary={'used_percent': 70.0, 'window_minutes': 10080, 'resets_at': int(self.now + 86400)}))
        got = codex_chats.saved_limits(self.reach, self.now)
        self.assertEqual([(w.label, w.used_pct, w.length_min) for w in got.windows],
                         [('5-hour', 10.0, 300), ('weekly', 70.0, 10080)])
        self.assertIsNone(got.windows[0].line)      # the adapter sets the line from its facts

    def test_other_lengths_are_named_from_their_length(self):
        self.assertEqual([chat_facts.window_label(m) for m in (300, 10080, 1440, 4320, 120, 90, 0, None, True)],
                         ['5-hour', 'weekly', '1-day', '3-day', '2-hour', '2-hour', None, None, None])

    def test_a_window_that_has_refilled_since_it_was_read_is_not_known(self):
        self.session(lambda c: c.token_count(primary={'used_percent': 99.0, 'window_minutes': 10080,
                                                      'resets_at': int(self.now - 10)}))
        w = codex_chats.saved_limits(self.reach, self.now).windows[0]
        self.assertIsNone(w.used_pct)
        self.assertEqual(w.resets_at, float(int(self.now - 10)))

    def test_reached_says_the_apps_own_word(self):
        self.session(lambda c: c.token_count(primary={'used_percent': 100.0, 'window_minutes': 10080,
                                                      'resets_at': int(self.now + 60)},
                                             reached='rate_limit_reached', plan='plus'))
        got = codex_chats.saved_limits(self.reach, self.now)
        self.assertEqual((got.reached, got.plan), ('rate_limit_reached', 'plus'))

    def test_a_line_for_another_bucket_is_passed_over(self):
        def build(c):
            c.token_count(primary={'used_percent': 5.0, 'window_minutes': 10080, 'resets_at': int(self.now + 60)})
            c.token_count(primary={'used_percent': 88.0, 'window_minutes': 10080, 'resets_at': int(self.now + 60)},
                          limit_id='codex_other_bucket')
        self.session(build)
        self.assertEqual(codex_chats.saved_limits(self.reach, self.now).windows[0].used_pct, 5.0)

    def test_a_line_that_names_no_bucket_is_the_default_one(self):
        self.session(lambda c: c.token_count(primary={'used_percent': 7.0, 'window_minutes': 300,
                                                      'resets_at': int(self.now + 60)}, limit_id=None))
        self.assertEqual(codex_chats.saved_limits(self.reach, self.now).windows[0].used_pct, 7.0)

    def test_the_newest_line_of_the_newest_file_wins(self):
        def older(c):
            c.token_count(primary={'used_percent': 1.0, 'window_minutes': 10080, 'resets_at': int(self.now + 60)})

        def newer(c):
            c.token_count(primary={'used_percent': 2.0, 'window_minutes': 10080, 'resets_at': int(self.now + 60)})
            c.token_count(primary={'used_percent': 3.0, 'window_minutes': 10080, 'resets_at': int(self.now + 60)})
        self.session(older, age=600)
        self.session(newer, age=60)
        self.assertEqual(codex_chats.saved_limits(self.reach, self.now).windows[0].used_pct, 3.0)

    def test_up_to_five_files_are_looked_in(self):
        self.session(lambda c: c.token_count(primary={'used_percent': 55.0, 'window_minutes': 10080,
                                                      'resets_at': int(self.now + 60)}), age=500)
        for age in (100, 200, 300, 400):
            self.session(lambda c: c.assistant('no numbers here'), age=age)
        self.assertEqual(codex_chats.saved_limits(self.reach, self.now).windows[0].used_pct, 55.0)
        self.session(lambda c: c.assistant('no numbers here either'), age=50)
        self.assertIsNone(codex_chats.saved_limits(self.reach, self.now))

    def test_the_newest_reading_wins_wherever_its_file_is_filed(self):
        """ADDENDA-3 C20: a thread stays in the folder of the day it began, so the file written last can be under a
        folder weeks old; its reading is newer than one in today's folder."""
        fresh = float(int(self.now) - 30)
        self.session(lambda c: c.token_count(primary={'used_percent': 41.0, 'window_minutes': 10080,
                                                      'resets_at': int(self.now + 86400)},
                                             at=float(int(self.now) - 600)), age=600, began=0)
        self.session(lambda c: c.token_count(primary={'used_percent': 42.0, 'window_minutes': 10080,
                                                      'resets_at': int(self.now + 86400)}, at=fresh),
                     age=60, began=30 * 86400)
        got = codex_chats.saved_limits(self.reach, self.now)
        self.assertEqual((got.windows[0].used_pct, got.read_at), (42.0, fresh))

    def test_the_newest_reading_wins_when_the_file_written_last_holds_an_older_one(self):
        # The file written last ended on lines that were not a token_count (an aborted turn), so its newest reading
        # is older than the one in the file written just before it.
        def last_written(c):
            c.token_count(primary={'used_percent': 5.0, 'window_minutes': 10080, 'resets_at': int(self.now + 60)},
                          at=float(int(self.now) - 900))
            c.assistant('then a turn that stopped')

        def written_before(c):
            c.token_count(primary={'used_percent': 6.0, 'window_minutes': 10080, 'resets_at': int(self.now + 60)},
                          at=float(int(self.now) - 120))
        self.session(written_before, age=100)
        self.session(last_written, age=10)
        got = codex_chats.saved_limits(self.reach, self.now)
        self.assertEqual((got.windows[0].used_pct, got.read_at), (6.0, float(int(self.now) - 120)))

    def test_a_helpers_file_counts_its_numbers_are_the_accounts(self):
        self.k += 1
        c = F.CodexChat(thread='cccccccc-0000-4000-8000-%012d' % self.k, cwd='/Users/maya/site',
                        source={'subagent': {'other': 'x'}}, thread_source='subagent', t0=self.now - 20 * 86400)
        c.token_count(primary={'used_percent': 12.0, 'window_minutes': 10080, 'resets_at': int(self.now + 60)})
        F.touch(c.write(self.chome), self.now - 60)
        self.assertEqual(codex_chats.saved_limits(self.reach, self.now).windows[0].used_pct, 12.0)

    def test_only_files_written_in_the_last_seven_days(self):
        self.session(lambda c: c.token_count(primary={'used_percent': 9.0, 'window_minutes': 10080,
                                                      'resets_at': int(self.now + 60)}),
                     age=(chat_facts.SAVED_LIMITS_DAYS + 1) * 86400, began=0)
        self.assertIsNone(codex_chats.saved_limits(self.reach, self.now))

    def test_nothing_saved_is_none(self):
        self.assertIsNone(codex_chats.saved_limits(self.reach, self.now))
        self.session(lambda c: c.token_count(primary=None))
        got = codex_chats.saved_limits(self.reach, self.now)
        self.assertEqual(got.windows, ())


if __name__ == '__main__':
    unittest.main()
