import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard doctor` only looks, and now says NO, with the one thing to do, for: a settings file or list of accounts
# restored from a copy in the last day, a damaged one, an entry of Switchboard's missing from Claude Code's settings,
# and the page busy for minutes; when automatic switching last decided is one line, the watch's. `switchboard
# doctor --fix` runs every repair the page's self-check makes and says what each did, one plain line each.
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import __main__ as cli
from switchboard import config, core, leftovers, selfcheck, trust
from test_cli_commands import Cli


class Doctor(Cli):
    def doctor(self, *args):
        self.two()
        with mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(cli.linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(cli.browser, 'present', lambda: False), \
                mock.patch.object(cli.browser, 'profiles', lambda: {}), \
                mock.patch.object(leftovers, 'doctor_line', lambda: None), \
                mock.patch.object(trust, 'doctor_line', lambda: (True, 'Secure connections', '')), \
                mock.patch.object(config, 'not_read_lines', lambda s=None: []):
            return self.run_cli('doctor', *args)

    def damage_settings(self):
        core.set_setting('auto', True)
        with open(core.data_path('settings.json'), 'w') as f:
            f.write('{"auto": tr')

    def test_plain_doctor_says_a_damaged_settings_file_and_writes_nothing(self):
        self.damage_settings()
        code, out, _ = self.doctor()
        self.assertIn(" NO  Switchboard's settings file: can't be read\n       Run: switchboard doctor --fix", out)
        self.assertEqual(code, 1)
        with open(core.data_path('settings.json')) as f:
            self.assertEqual(f.read(), '{"auto": tr')                   # doctor only looks
        self.assertEqual([n for n in os.listdir(core.data_dir()) if '.damaged-' in n], [])

    def test_doctor_fix_repairs_it_and_says_what_it_did_then_doctor_says_it_for_a_day(self):
        self.damage_settings()
        code, out, _ = self.doctor('--fix')
        self.assertIn("fixed  Switchboard's settings file: was damaged; Switchboard's settings file was damaged. It was "
                      "restored from a copy made", out)
        self.assertIn('   ok  A switch cut off half way: none', out)
        self.assertIs(core.settings().get('auto'), True)
        self.assertEqual(code, 0, out)
        code, out, _ = self.doctor()
        self.assertRegex(out, r" NO  Switchboard's settings file was damaged\. It was restored from a copy made .+ "
                              r"\(.+\)\n       Look over your accounts and settings\. The damaged file is kept as "
                              r"settings\.json\.damaged-\d+\.")

    def test_doctor_fix_with_an_entry_left_out_exits_1_and_says_the_one_thing_to_do(self):
        with mock.patch.object(selfcheck, 'CHECKS', (lambda fix, now: [selfcheck.Result(
                'entry:statusline', "Switchboard's status line in Claude Code", 'missing', selfcheck.LEFT,
                selfcheck.COST['statusline'], fix='Turn it on again in Settings, or run: switchboard statusline on')],)):
            code, out, _ = self.doctor('--fix')
        self.assertEqual(code, 1)
        self.assertIn("   NO  Switchboard's status line in Claude Code: missing; Claude Code's status line no longer "
                      "feeds Switchboard its limits", out)
        self.assertIn('\n       Turn it on again in Settings, or run: switchboard statusline on\n', out)
        self.assertIn('Something above needs a look.', out)

    def test_no_decision_lately_is_one_line_in_the_watchs_words(self):
        # One fact, one line: the watch's "Auto-switch last decided N minutes ago." (awake time, late past the pass
        # the loop planned) is the only one doctor says for it.
        core.set_setting('auto', True)
        late = {'running': True, 'pass_age_s': 23 * 60.0 + 5, 'last_pass_at': time.time() - 1385,
                'next_pass_at': time.time() - 1325}
        with mock.patch.object(cli.engine, 'auto_settings', lambda: {'enabled': True}), \
                mock.patch.object(cli.api, 'auto_status', lambda: late):
            code, out, _ = self.doctor()
        self.assertEqual(out.count('last decided'), 1, out)
        self.assertIn(' NO  Auto-switch last decided 23 minutes ago\n', out)
        self.assertNotIn('no decision for', out)
        in_time = dict(late, pass_age_s=90.0, last_pass_at=time.time() - 90, next_pass_at=time.time() - 30)
        with mock.patch.object(cli.engine, 'auto_settings', lambda: {'enabled': True}), \
                mock.patch.object(cli.api, 'auto_status', lambda: in_time):
            self.assertNotIn('last decided', self.doctor()[1])
        with mock.patch.object(cli.engine, 'auto_settings', lambda: {'enabled': False}), \
                mock.patch.object(cli.api, 'auto_status', lambda: late):
            self.assertNotIn('last decided', self.doctor()[1])               # off: nothing to decide
        self.assertFalse(hasattr(selfcheck, 'since_decided'))                 # the second count is gone

    def test_the_page_busy_for_minutes(self):
        busy = {'accounts': [], 'answer': {'age': 400.0, 'stale': True}}
        with mock.patch.object(cli, 'page_state', lambda timeout=5: busy):
            out = self.doctor()[1]
        self.assertIn(' NO  The page has been busy for 6 min: its last answer is that old\n       If it stays like this, '
                      'restart the page: launchctl kickstart -k gui/', out)
        stage = {'accounts': [], 'answer': {'age': 1.0, 'stale': False, 'loop': {'stage': 'reading the accounts',
                                                                                 'for': 250.0}}}
        with mock.patch.object(cli, 'page_state', lambda timeout=5: stage):
            self.assertIn(" NO  The page's own loop has been reading the accounts for 4 min", self.doctor()[1])
        quick = {'accounts': [], 'answer': {'age': 20.0, 'stale': True, 'loop': {'stage': 'x', 'for': 30.0}}}
        with mock.patch.object(cli, 'page_state', lambda timeout=5: quick):
            self.assertNotIn('busy for', self.doctor()[1])

    def test_doctor_takes_only_fix(self):
        code, _, _ = self.doctor('--fx')
        self.assertTrue(str(code).startswith('refused: Run: switchboard doctor, or switchboard doctor --fix'), code)


if __name__ == '__main__':
    unittest.main()
