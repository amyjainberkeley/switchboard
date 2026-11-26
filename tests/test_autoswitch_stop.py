"""The automatic loop's stop and its log, and what uninstall.sh, install.sh and the docs say of the loop. Each class's
docstring names the failure its tests rule out."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from fakes_autoswitch import FakeClock, FakeEngine, Home, account, make_loop, set_policy
from fakes_accounts import Rig
from switchboard.accounts import autoloop, tokens


class AStopIsHeardBetweenStages(Home):
    """The loop's SIGTERM handler only set a flag that nothing read until the pass ended, so after a stop the loop
    still started an upkeep (a refresh) and a switch; the host kills it 30 s after the stop, which can cut off a refresh
    whose answer is on its way (the old token spent, the new one never saved)."""

    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.eng = FakeEngine([account('a1', 1), account('a2', 2)], 'a1', self.clock)
        self.upkept = []
        self.eng.upkeep_pass = lambda budget_s=60.0: self.upkept.append(budget_s) or []
        set_policy()
        self.addCleanup(tokens.STOPPING.clear)

    def test_a_stop_before_a_pass_starts_no_stage(self):
        loop = make_loop(self.eng, self.clock)
        loop.stop.set()
        loop.run_pass()
        self.assertEqual(self.eng.live_calls, 0)
        self.assertEqual(self.eng.switches, [])
        self.assertEqual(self.upkept, [])

    def test_a_stop_during_a_stage_lets_it_end_and_starts_nothing_after_it(self):
        loop = make_loop(self.eng, self.clock)
        live = self.eng.live

        def stopped_while_reading():
            loop.stop.set()                                 # SIGTERM arrives while the live login is read
            return live()
        loop.d.live = stopped_while_reading
        loop.run_pass()
        self.assertEqual(self.eng.live_calls, 1)            # the stage under way finished
        self.assertEqual(self.eng.switches, [])
        self.assertEqual(self.upkept, [])

    def test_the_stop_handler_also_stops_new_renewals_in_this_process(self):
        loop = make_loop(self.eng, self.clock)
        autoloop.on_stop(loop)
        self.assertTrue(loop.stop.is_set())
        self.assertTrue(tokens.STOPPING.is_set())


class NoRenewalStartsAfterAStop(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.addCleanup(self.rig.close)
        self.addCleanup(tokens.STOPPING.clear)
        self.rig.two_accounts()
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)

    def test_refresh_and_upkeep_spend_nothing_once_the_process_is_stopping(self):
        posts = len(self.rig.server.token_posts())
        tokens.STOPPING.set()
        res = tokens.refresh(self.rig.ctx, 'uuid-a', budget_s=60)
        self.assertEqual((res.outcome, res.why), ('skipped', 'stopping'))
        self.assertEqual(tokens.upkeep_pass(self.rig.ctx, budget_s=120), [])
        self.assertEqual(len(self.rig.server.token_posts()), posts)
        self.assertNotIn('uuid-a', self.rig.doc()['refreshing'])
        tokens.STOPPING.clear()
        self.assertEqual(tokens.refresh(self.rig.ctx, 'uuid-a', budget_s=60).outcome, 'refreshed')


class TheLoopsLogNamesNoEmail(Home):
    """auto.log says accounts are named by number there, never by email, but a failed switch and a journal that
    could not be checked wrote the engine's sentence as it was (it names the account by email)."""

    def test_a_failed_switch_is_logged_without_the_email(self):
        from fakes_autoswitch import FakeNet, FakeTokens, T0
        from test_autoswitch_loop import ok, D
        from switchboard import core
        clock = FakeClock()
        eng = FakeEngine([account('a1', 1), account('a2', 2)], 'a1', clock)
        eng.fail_switch = 'ana@x.example is in use by a `switchboard run` right now; switch once it ends.'
        net = FakeNet(clock)
        net.set('tok-a1', ok(95.0, 30.0))
        net.set('tok-a2', ok(10.0, 20.0, week_reset=T0 + 2 * D))
        set_policy()
        make_loop(eng, clock, net, FakeTokens()).run_pass()
        with open(core.data_path(autoloop.LOG)) as f:
            text = f.read()
        self.assertIn('switch failed', text)
        self.assertNotIn('ana@x.example', text)

    def test_a_journal_that_cannot_be_checked_is_logged_without_the_email(self):
        from switchboard import core
        from switchboard.accounts import api
        clock = FakeClock()
        eng = FakeEngine([account('a1', 1)], 'a1', clock)

        def refuse():
            raise api.AccountsError('other', 'An earlier switch to ana@x.example was cut off.')
        eng.resolve_journal = refuse
        make_loop(eng, clock).start()
        with open(core.data_path(autoloop.LOG)) as f:
            text = f.read()
        self.assertIn('journal not checked', text)
        self.assertNotIn('ana@x.example', text)


HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TheUninstallerStopsALoneLoopForReal(unittest.TestCase):
    """uninstall.sh asked a lone loop to stop, waited 30 s, never escalated, said "Stopped automatic switching."
    either way, and then removed the program and data under a loop that might still be running."""

    def block(self):
        import re
        with open(os.path.join(HERE, 'uninstall.sh')) as f:
            text = f.read()
        found = re.search(r'^is_loop\(\) \{.*?^\}\n.*?^stop_loop\(\) \{.*?^\}\n', text, re.S | re.M)
        self.assertIsNotNone(found, 'uninstall.sh has no stop_loop function')
        self.assertIn('stop_loop || exit 1', text)
        # the same steps, with a short wait for the test (the first wait in stop_loop is the one after SIGTERM)
        short, n = re.subn(r'(while \[ \$i -lt )\d+( \] && is_loop)', r'\g<1>2\2', found.group(0), count=1)
        self.assertEqual(n, 1)
        return short

    def run_block(self, ignore_term):
        import subprocess
        import tempfile
        data = tempfile.mkdtemp()
        code = ('import signal, time\n' + ('signal.signal(signal.SIGTERM, signal.SIG_IGN)\n' if ignore_term else '') +
                'time.sleep(60)\n')
        loop = subprocess.Popen(['/usr/bin/python3', '-I', '-c', code, 'switchboard.accounts.autoloop'])
        self.addCleanup(lambda: loop.poll() is None and loop.kill())
        with open(os.path.join(data, 'auto.pid'), 'w') as f:
            f.write(str(loop.pid))
        time.sleep(0.3)
        r = subprocess.run(['/bin/sh', '-c', 'set -eu\nDATA="$1"\n' + self.block() + 'stop_loop', 'sh', data],
                           capture_output=True, text=True, timeout=60)
        loop.wait(10)
        return r, loop

    def test_a_loop_that_ignores_sigterm_is_killed_before_anything_is_removed(self):
        r, loop = self.run_block(ignore_term=True)
        self.assertIsNotNone(loop.poll())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Stopped automatic switching.', r.stdout)

    def test_a_loop_that_ends_at_sigterm_is_said_stopped(self):
        r, loop = self.run_block(ignore_term=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Stopped automatic switching.', r.stdout)


class TheInstallersCommentsNameRealSteps(unittest.TestCase):
    """install.sh's header said "step 8 is too", but its numbered list ends at 7."""

    def test_every_step_named_is_in_the_list(self):
        import re
        with open(os.path.join(HERE, 'install.sh')) as f:
            head = f.read().split('set -eu', 1)[0]
        steps = [int(n) for n in re.findall(r'^#\s+(\d+)\. ', head, re.M)]
        self.assertEqual(steps, list(range(1, len(steps) + 1)))
        for n in re.findall(r'\bstep (\d+)', head):
            self.assertLessEqual(int(n), max(steps), 'step %s' % n)


class TheDocsSayWhatTheLoopDoes(unittest.TestCase):
    """The docs promised reads of every account every 5 to 15 minutes
    (only the loop reads them, and it runs only while auto-switch is on), said a watch holds a move for 30 minutes (it
    holds while its session is active and 6 hours more), listed none of the loop's files, and said Switchboard "waits"
    on a Keychain dialog."""

    def read(self, name):
        with open(os.path.join(HERE, name)) as f:
            return f.read()

    def section(self, name, heading):
        text = self.read(name)
        start = text.index(heading)
        end = text.find('\n## ', start + len(heading))
        return text[start:end if end > 0 else len(text)]

    def test_fresh_numbers_for_the_other_accounts_need_auto_switch_on(self):
        for name, heading in (('README.md', '## How fresh the numbers are'),
                              ('docs/how-it-works.md', '## How fresh the numbers are'),
                              ('docs/troubleshooting.md', '## The numbers look old')):
            part = self.section(name, heading)
            self.assertIn('auto-switch is on', part, name)
            self.assertIn('off', part, name)

    def test_a_watch_is_said_as_the_code_holds_it(self):
        auto = self.section('docs/how-it-works.md', '## Auto-switch')
        self.assertNotIn('no artifact watch started on the account in the last 30 minutes', auto)
        self.assertIn('watches an artifact', auto)
        self.assertIn('6 hours after', auto)
        self.assertIn('watch', self.section('docs/troubleshooting.md', "## Auto-switch didn't switch"))
        self.assertIn('watch', self.section('docs/troubleshooting.md', '## A session says an artifact or doc was not found'))

    def test_the_files_list_names_the_loops_files(self):
        files = self.section('docs/commands.md', '## Files')
        for name in ('auto.log', 'auto-heartbeat.json', 'auto-state.json', 'auto.lease', 'auto.pid', 'recovery/'):
            self.assertIn('`%s`' % name, files)

    def test_a_keychain_dialog_is_never_closed_and_nothing_is_said_to_wait_forever(self):
        part = self.section('docs/troubleshooting.md', '## The Keychain is locked, slow, or waiting for you')
        self.assertNotIn('leaves it open and waits', part)
        self.assertIn('never closes it', part)


if __name__ == '__main__':
    unittest.main()
