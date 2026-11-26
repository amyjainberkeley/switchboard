"""The Keychain writes a login on security's standard input (never in the arguments `ps` shows) whenever its
command line fits Claude Code's own limit; deadlines grow with load; a call left waiting on a Keychain dialog says
'dialog', not 'timeout', and its helper is never killed."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import subprocess
import tempfile
import time
import unittest

from fakes_accounts import Rig, login_text
from switchboard.accounts import api, capture, context, keychain, live, switcher, tokens
from switchboard.accounts.api import ABSENT, PRESENT, UNREADABLE, AccountsError

SVC, ACCT = 'Claude Code-credentials', 'tester'


class KeychainCase(unittest.TestCase):
    def setUp(self):
        self.rig = Rig(real_keychain=True)
        self.sec = self.rig.sec

    def make(self, deadline=120.0, **kw):
        # generous deadlines for answers that must come (a process start at background priority on a loaded Mac can
        # take seconds); a short one only where a test wants "no answer in time"
        return keychain.Keychain(tool=self.sec.tool, read_deadline=deadline, write_deadline=deadline, backoff_s=30.0,
                                 gate_path=os.path.join(self.rig.data, 'keychain.gate'), **kw)

    def settle(self, kc, limit=90.0):
        end = time.time() + limit
        while kc.state()['needs_approval_or_slow'] and time.time() < end:
            time.sleep(0.2)
        self.assertEqual(kc.state()['needs_approval_or_slow'], [])

    def tearDown(self):
        self.rig.close()


def value_of(size):
    """A login-shaped text of exactly `size` bytes."""
    head = login_text('sk-ant-oat01-a', 'sk-ant-ort01-r', 1, 2)[:-1] + ',"pad":"'
    return head + 'x' * (size - len(head) - 2) + '"}'


class OnStandardInput(KeychainCase):
    def test_a_login_that_fits_one_line_goes_on_standard_input_never_in_the_arguments(self):
        kc = self.make()
        value = value_of(900)
        self.assertTrue(kc.write(SVC, ACCT, value).ok)
        adds = self.sec.calls('add')
        self.assertEqual([(c['via'], c['argv_value']) for c in adds], [('stdin', False)])
        self.assertIn('-U', adds[0]['flags'])
        self.assertEqual(self.sec.get(SVC, ACCT), value)
        self.assertEqual(kc.read(SVC, ACCT, fresh=True).value, value)

    def test_the_limit_is_claude_codes_own_4032_byte_line(self):
        self.assertEqual(keychain.STDIN_LIMIT, 4032)
        fixed = len(keychain.stdin_line(SVC, ACCT, ''))
        at = keychain.stdin_line(SVC, ACCT, 'a' * (4032 - fixed))
        self.assertEqual(len(at), 4032)
        self.assertTrue(at.endswith('"\n'))
        self.assertIsNone(keychain.stdin_line(SVC, ACCT, 'a' * (4033 - fixed)))

    def test_a_login_too_long_for_one_line_goes_in_the_arguments_and_lands_whole(self):
        kc = self.make()
        value = value_of(3000)                                   # 6000 hex characters: past the line buffer
        self.assertTrue(kc.write(SVC, ACCT, value).ok)
        self.assertEqual([(c['via'], c['argv_value']) for c in self.sec.calls('add')], [('argv', True)])
        self.assertEqual(self.sec.get(SVC, ACCT), value)

    def test_why_that_line_must_never_go_on_standard_input(self):
        # the stand-in, like security, cuts a line past its 4096-byte buffer short and stores what it got
        value = value_of(3000)
        line = 'add-generic-password -U -a "%s" -s "%s" -X "%s"\n' % (ACCT, SVC, value.encode().hex())
        r = subprocess.run([self.sec.tool, '-i'], input=line.encode(), capture_output=True, timeout=120)
        self.assertEqual(r.returncode, 0)                       # no word about it
        self.assertNotEqual(self.sec.get(SVC, ACCT), value)      # a corrupted login

    def test_a_command_that_fails_on_standard_input_is_a_failure_even_when_security_exits_0(self):
        kc = self.make()
        self.sec.rule(verb='add', exit=36, stderr='security: User interaction is not allowed.\n', times=1)
        self.assertEqual(kc.write(SVC, ACCT, '{"a":1}'), keychain.Done(False, 'locked'))
        kc.forget()
        self.sec.rule(verb='add', exit=36, exit_zero=True, times=1)
        self.assertEqual(kc.write(SVC, ACCT, '{"a":2}'), keychain.Done(False, 'locked'))
        self.assertIsNone(self.sec.get(SVC, ACCT))
        kc.forget()
        self.assertTrue(kc.write(SVC, ACCT, '{"a":3}').ok)
        self.assertEqual(self.sec.get(SVC, ACCT), '{"a":3}')

    def test_a_name_with_a_quote_or_a_line_break_goes_in_the_arguments_under_its_exact_name(self):
        kc = self.make()
        for acct in ('we"ird', 'back\\slash', 'line\nbreak'):
            self.assertTrue(kc.write(SVC, acct, '{"n":1}').ok, acct)
            self.assertEqual(self.sec.get(SVC, acct), '{"n":1}')
        self.assertEqual(set(c['via'] for c in self.sec.calls('add')), {'argv'})


class DeadlinesGrowWithLoad(unittest.TestCase):
    def kc(self, per_core, **kw):
        return keychain.Keychain(tool='/nonexistent', load=lambda: per_core, **kw)

    def test_as_set_under_one_and_a_half_per_core_up_to_three_times_at_three(self):
        for per_core in (None, 0.2, 1.49):
            self.assertEqual(self.kc(per_core).deadlines(), (8.0, 15.0), per_core)
        self.assertEqual(self.kc(2.25).deadlines(), (16.0, 30.0))
        self.assertEqual(self.kc(3.0).deadlines(), (24.0, 45.0))
        self.assertEqual(self.kc(26.0).deadlines(), (24.0, 45.0))
        self.assertEqual(keychain.Keychain(tool='/nonexistent').deadlines(), (8.0, 15.0))   # no load given

    def test_never_past_30_s_for_a_read_or_45_s_for_a_write_and_never_shorter_than_set(self):
        self.assertEqual(self.kc(5.0, read_deadline=12.0, write_deadline=20.0).deadlines(), (30.0, 45.0))
        self.assertEqual(self.kc(5.0, read_deadline=60.0, write_deadline=60.0).deadlines(), (60.0, 60.0))

    def test_a_load_that_cant_be_read_leaves_them_as_set(self):
        def broken():
            raise OSError('no load')
        self.assertEqual(keychain.Keychain(tool='/nonexistent', load=broken).deadlines(), (8.0, 15.0))
        self.assertEqual(self.kc(float('nan')).deadlines(), (8.0, 15.0))

    def test_every_call_waits_the_deadline_of_the_moment(self):
        kc = self.kc(3.0)
        seen = {}

        def run(key, args, deadline, verb, data=None):
            seen[verb] = deadline
            return 0, b'{}\n', b''
        kc._run = run
        kc._searchable = (True, kc._mono())
        kc.read(SVC, ACCT, fresh=True)
        kc.exists(SVC, ACCT)
        kc.write(SVC, ACCT, '{}')
        kc.delete(SVC, ACCT)
        self.assertEqual(seen, {'read': 24.0, 'exists': 24.0, 'write': 45.0, 'delete': 45.0})

    def test_the_apps_keychain_reads_this_macs_load_and_watches_for_dialogs(self):
        kc = context.keychain_for(tempfile.mkdtemp(prefix='sb-kc-'))
        self.assertIs(kc._load, keychain.system_load_per_core)
        self.assertIs(kc._dialog, keychain.security_agent_up)


class ADialogIsNotATimeout(KeychainCase):
    def test_a_read_past_its_deadline_while_a_keychain_dialog_is_up_says_dialog_and_is_left_alone(self):
        self.sec.put(SVC, ACCT, '{"late":true}')
        self.sec.rule(verb='find', delay=6.0, times=1)
        kc = self.make(1.0, dialog=lambda: True)
        r = kc.read(SVC, ACCT, fresh=True)
        self.assertEqual((r.kind, r.why), (UNREADABLE, 'dialog'))
        self.assertEqual(kc.state()['waiting_on_dialog'], [SVC])
        again = kc.read(SVC, ACCT, fresh=True, interactive=True)       # no second helper while one waits
        self.assertEqual((again.kind, again.why), (UNREADABLE, 'dialog'))
        self.assertEqual(kc.write(SVC, ACCT, '{"other":1}').why, 'busy')
        self.settle(kc)                                                # it finished by itself: never killed
        self.assertEqual(len(self.sec.calls('find')), 1)
        self.assertEqual(kc.read(SVC, ACCT).value, '{"late":true}')
        self.assertEqual(kc.state()['waiting_on_dialog'], [])

    def test_without_a_dialog_a_late_answer_is_still_timeout(self):
        self.sec.put(SVC, ACCT, '{}')
        for dialog in (lambda: False, lambda: None, None):
            self.sec.rule(verb='find', delay=6.0, times=1)
            kc = self.make(1.0, dialog=dialog)
            self.assertEqual(kc.read(SVC, ACCT, fresh=True).why, 'timeout')
            self.settle(kc)

    def test_a_write_past_its_deadline_while_a_dialog_is_up_says_dialog(self):
        self.sec.rule(verb='add', delay=6.0, times=1)
        kc = self.make(1.0, dialog=lambda: True)
        self.assertEqual(kc.write(SVC, ACCT, '{"w":1}'), keychain.Done(False, 'dialog'))
        self.settle(kc)
        self.assertEqual(self.sec.get(SVC, ACCT), '{"w":1}')          # it may land once answered

    def test_securityagent_in_the_process_list_is_a_dialog(self):
        class Done:
            def __init__(self, code, out):
                self.returncode, self.stdout = code, out

        listing = b'/sbin/launchd\n/System/Library/Frameworks/Security.framework/Versions/A/MachServices/' \
                  b'SecurityAgent.bundle/Contents/MacOS/SecurityAgent\n/usr/sbin/securityd\n'
        self.assertIs(keychain.security_agent_up(run=lambda *a, **k: Done(0, listing)), True)
        self.assertIs(keychain.security_agent_up(run=lambda *a, **k: Done(0, b'/usr/sbin/securityd\n')), False)
        self.assertIsNone(keychain.security_agent_up(run=lambda *a, **k: Done(1, b'')))

        def boom(*a, **k):
            raise subprocess.TimeoutExpired('ps', 2)
        self.assertIsNone(keychain.security_agent_up(run=boom))


class TheEngineSaysSo(unittest.TestCase):
    """What the engine does with 'dialog' (an in-process Keychain stand-in that answers it)."""

    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.a_text, self.b_text = self.rig.two_accounts()          # Claude Code is on B

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def test_the_live_login_is_not_asked_for_twice_while_a_dialog_waits(self):
        self.rig.kc.rule('read', service=self.rig.service, why='dialog', times=1)
        before = self.rig.kc.count('read', self.rig.service)
        lr = live.read_live(self.ctx, fresh=True)
        self.assertEqual((lr.read.kind, lr.read.why), (UNREADABLE, 'dialog'))
        self.assertEqual(self.rig.kc.count('read', self.rig.service), before + 1)

    def test_a_switch_stopped_by_a_dialog_says_to_answer_it_and_changes_nothing(self):
        self.rig.kc.rule('read', service=self.rig.service, why='dialog', times=5)
        with self.assertRaises(AccountsError) as e:
            api.switch('uuid-a', source='cli')
        self.assertEqual(e.exception.kind, 'keychain_slow')
        self.assertIn('Keychain dialog', str(e.exception))
        self.assertEqual(self.rig.live_text(), self.b_text)

    def test_a_live_write_left_waiting_on_a_dialog_is_settled_later_never_called_unchanged(self):
        self.rig.kc.rule('write', service=self.rig.service, why='dialog', times=1)
        res = api.switch('uuid-a', source='cli')
        self.assertEqual((res.ok, res.rolled_back), (False, False))
        self.assertIn('Keychain dialog', res.note)
        r, j = switcher.read_journal(self.ctx)
        self.assertEqual((r.kind, j.get('step')), (PRESENT, 'write_unknown'))   # settled once it is answered
        self.assertTrue(api.resolve_journal())                       # the write never landed: Claude Code stays on B
        self.assertEqual(switcher.read_journal(self.ctx)[0].kind, ABSENT)
        self.assertEqual(self.rig.live_text(), self.b_text)

    def test_keeping_says_a_dialog_waits_and_tries_again_in_a_minute(self):
        capture.reset()
        self.rig.kc.rule('read', service=self.rig.service, why='dialog', times=1)
        note = capture.keep_pass(self.ctx, force=True)
        self.assertIn('Keychain dialog', note)
        self.assertIn('a minute', note)

    def test_removing_an_account_while_a_dialog_waits_says_so(self):
        self.rig.kc.rule('delete', service='Switchboard login', why='dialog', times=1)
        with self.assertRaises(AccountsError) as e:
            api.remove('uuid-a')
        self.assertEqual(e.exception.kind, 'keychain_slow')
        self.assertIn('Keychain dialog', str(e.exception))
        self.assertEqual([a.id for a in api.accounts()], ['uuid-a', 'uuid-b'])


if __name__ == '__main__':
    unittest.main()
