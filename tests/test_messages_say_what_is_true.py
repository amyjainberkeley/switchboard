import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# What a person reads says what is true: a rank or a line given to an account kept out of auto-switch, or used last,
# says what it does for that account; `add --at` names the places a new account can have, the one past the end too;
# a value that is not one names the flag or the setting it was for, in one sentence everywhere. Made-up accounts
# only; the engine is a stand-in (tests/fakes_accounts_api.py).
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import autocheck, autorules, core
from test_autoswitch_lines_and_ranks_cli import Three as Accounts, B, C
from test_term import FakeTty

KEPT_OUT = ' It is kept out of auto-switch, so this does nothing until switchboard auto in %s.'


class Three(Accounts):
    def run_cli(self, *args, tty=None):
        """(exit code, standard output); a refusal is 'refused: <its sentence>'."""
        try:
            return Accounts.run_cli(self, *args, tty=tty)
        except core.SwitchError as e:
            return 'refused: %s' % e, ''


class RankAndLineOnAnAccountWithARule(Three):
    def test_a_rank_for_an_account_kept_out_or_used_last(self):
        self.assertEqual(self.run_cli('auto', 'out', B)[0], 0)
        self.assertEqual(self.run_cli('auto', 'rank', B, '1'), (0, '%s is rank 1 now.%s\n' % (B, KEPT_OUT % B)))
        self.assertEqual(self.run_cli('auto', 'last', C)[0], 0)
        self.assertEqual(self.run_cli('auto', 'rank', C, '1'),
                         (0, '%s is rank 1 now; it is used last, so it still comes after every account that is '
                             'not.\n' % C))

    def test_a_line_for_an_account_kept_out(self):
        self.assertEqual(self.run_cli('auto', 'out', B)[0], 0)
        self.assertEqual(self.run_cli('auto', 'line', B, '--week', '90'),
                         (0, '%s now has its own line: 90%% weekly.%s\n' % (B, KEPT_OUT % B)))

    def test_with_no_rule_the_words_are_as_before(self):
        self.assertEqual(autorules.rank_said(B, 2), '%s is rank 2 now: auto-switch lands there before any account '
                                                    'with a later rank.' % B)
        self.assertEqual(autorules.line_said(B, {'weekly_at': 90}), '%s now has its own line: 90%% weekly.' % B)


class APlaceForANewAccount(Three):
    def test_add_at_past_the_end_names_the_places(self):
        code, out = self.run_cli('add', '--at', '5', tty=FakeTty([]))      # at a terminal: a new account
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith('There is no place 5: a new account can go at 1 to 4.\n'), out)
        code, out = self.run_cli('add', '--at', '4')        # no terminal: the account in use, kept already
        self.assertTrue(out.startswith('There is no place 4: Switchboard has 3 accounts.\n'), out)


class AWrongValueNamesItsFlagOrSetting(Three):
    def test_one_sentence_everywhere(self):
        self.assertEqual(self.run_cli('config', 'set', 'auto_policy.weekly_at', '120')[0],
                         'refused: auto_policy.weekly_at takes a number from 50 to 99.9.')
        self.assertEqual(self.run_cli('auto', 'line', B, '--week', '120')[0],
                         'refused: --week takes a number from 50 to 99.9.')
        code, out = self.run_cli('auto', 'why', '--week', '120')
        self.assertEqual((code, out.splitlines()[0]), (2, '--week takes a number from 50 to 99.9.'))
        self.assertEqual(autocheck.line_words('--week'), '--week takes a number from 50 to 99.9.')
        self.assertEqual(self.run_cli('config', 'set', 'auto', 'maybe')[0], 'refused: auto takes on or off.')


if __name__ == '__main__':
    unittest.main()
