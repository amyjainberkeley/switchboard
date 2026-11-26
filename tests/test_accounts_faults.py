"""The account engine's set-aside logins, helper process groups, request deadlines, lock release, removal order,
forgotten readings and Keychain load. Each class's docstring names the failure its tests rule out."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import time
import unittest

from fakes_accounts import Rig
from switchboard.accounts import api, capture, policy, roster, switcher, tokens, vault


class Clock:
    def __init__(self):
        self.wall_jump = 0.0
        self.mono_jump = 0.0

    def wall(self):
        return time.time() + self.wall_jump

    def mono(self):
        return time.monotonic() + self.mono_jump


class AnIdentityConflictKeepsTheOtherAccountsLoginOut(unittest.TestCase):
    """A refresh whose answer names another account set its successor aside and marked the account, but the next
    refresh (upkeep, the poller, a switch) adopted that set-aside login back into the slot, and nothing read the mark,
    so a switch to A could put B's login live under A's name."""

    def setUp(self):
        self.clock = Clock()
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono)
        self.ctx = self.rig.ctx
        self.a_text, self.b_text = self.rig.two_accounts()      # Claude Code is on B; A is an idle saved login
        self.server = self.rig.server

    def tearDown(self):
        self.rig.close()

    def expire_a(self):
        return self.rig.set_saved_expiry('uuid-a', time.time() - 60)

    def answer_names_another_account(self):
        real = self.server._token

        def other(body, timeout_s):
            status, h, data = real(body, timeout_s)
            obj = json.loads(data)
            if isinstance(obj.get('account'), dict):
                obj['account']['uuid'] = 'uuid-zzz'
            return status, h, json.dumps(obj).encode()
        self.server._token = other
        return lambda: setattr(self.server, '_token', real)

    def conflict(self):
        spent = self.expire_a()
        undo = self.answer_names_another_account()
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        undo()
        self.assertEqual((res.outcome, res.why), ('stashed', 'identity_conflict'))
        return spent

    def test_the_set_aside_login_is_never_adopted_back_into_the_slot(self):
        spent = self.conflict()
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertNotEqual(res.outcome, 'adopted')
        self.assertEqual((res.outcome, res.why), ('skipped', 'conflict'))
        self.assertEqual(self.rig.vault('uuid-a'), spent)              # the slot keeps what it had
        self.assertTrue(self.rig.holds_valid('uuid-a'))                 # the set-aside login is still kept

    def test_upkeep_leaves_it_alone(self):
        self.conflict()
        self.assertFalse(tokens.due(self.rig.doc(), 'uuid-a', time.time()))
        posts = len(self.server.token_posts())
        self.assertEqual(tokens.upkeep_pass(self.ctx, budget_s=120), [])
        self.assertEqual(len(self.server.token_posts()), posts)

    def test_upkeep_says_the_login_was_set_aside_not_that_it_will_be_saved(self):
        self.expire_a()
        undo = self.answer_names_another_account()
        try:
            notes = tokens.upkeep_pass(self.ctx, budget_s=120)
        finally:
            undo()
        self.assertEqual(len(notes), 1, notes)
        self.assertIn('belongs to another account', notes[0])
        self.assertNotIn('will save it', notes[0])

    def test_its_status_says_conflict_and_the_policy_never_picks_it(self):
        self.conflict()
        got = dict((a.id, a.status) for a in roster.accounts(self.ctx))
        self.assertEqual(got['uuid-a'], 'conflict')
        self.assertNotIn('conflict', policy.OK_STATUSES)
        self.assertNotIn(got['uuid-b'], ('conflict', 'needs_login'))

    def test_a_switch_to_it_is_refused_and_names_the_real_cause(self):
        self.conflict()
        live_before = self.rig.live_text()
        with self.assertRaises(api.AccountsError) as e:
            switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli')
        self.assertEqual(e.exception.kind, 'dead_login')
        self.assertIn('belongs to another account', str(e.exception))
        self.assertEqual(self.rig.live_text(), live_before)             # Claude Code stays on B

    def test_a_switch_whose_own_renewal_finds_the_conflict_says_so(self):
        self.expire_a()
        undo = self.answer_names_another_account()
        live_before = self.rig.live_text()
        try:
            with self.assertRaises(api.AccountsError) as e:
                switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli')
        finally:
            undo()
        self.assertIn('belongs to another account', str(e.exception))
        self.assertNotIn("Couldn't finish renewing", str(e.exception))
        self.assertEqual(self.rig.live_text(), live_before)

    def test_a_login_anthropic_proves_is_its_own_clears_it(self):
        self.conflict()
        self.rig.sign_in('uuid-a', 'a@example.com', 'org-a')           # a fresh /login as A, proven by its profile
        roster.add_current(self.ctx)
        self.assertNotIn('conflict', self.rig.doc()['accounts']['uuid-a'])
        got = dict((a.id, a.status) for a in roster.accounts(self.ctx))
        self.assertEqual(got['uuid-a'], 'ok')

    def test_a_login_kept_on_its_label_that_anthropic_says_is_another_is_never_switched_to(self):
        self.rig.sign_in('uuid-c', 'c@example.com', 'org-c')
        self.server.script.append(('profile', __import__('switchboard.accounts.net', fromlist=['x'])
                                   .NetError('dns', False)))
        capture.reset()
        capture.keep_pass(self.ctx, force=True)                         # kept on its label alone (by design)
        self.assertEqual(self.rig.doc()['accounts']['uuid-c']['proof']['kind'], 'label')
        capture.reset()
        self.server.script.append(('profile', (200, {'account': {'uuid': 'uuid-z', 'email': 'z@x.example'},
                                                     'organization': {'uuid': 'org-z'}})))
        capture.keep_pass(self.ctx, force=True)                         # Anthropic: that login is someone else's
        got = dict((a.id, a.status) for a in roster.accounts(self.ctx))
        self.assertEqual(got['uuid-c'], 'conflict')
        switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli')     # Claude Code moves away from C
        with self.assertRaises(api.AccountsError) as e:
            switcher.switch(self.ctx, 'uuid-c', kind='manual', source='cli')
        self.assertIn('belongs to another account', str(e.exception))


class TheKeychainHelperOutlivesAKillOfItsCaller(unittest.TestCase):
    """A `security` helper past its deadline is never killed (it may be drawing macOS's Keychain dialog), but
    it ran in its caller's process group, and the page ends a stuck loop by killing that whole group (SIGKILL), helper
    and dialog included. Each helper now starts in a session of its own."""

    def test_each_helper_runs_in_a_session_and_group_of_its_own(self):
        import os
        import stat
        import tempfile
        from switchboard.accounts import keychain
        d = tempfile.mkdtemp()
        out = os.path.join(d, 'ids')
        tool = os.path.join(d, 'stand-in-tool')         # (a tool named security is refused here)
        with open(tool, 'w') as f:
            f.write('#!/bin/sh\n/usr/bin/python3 -I -c "import os; print(os.getsid(0), os.getpgrp())" > "%s"\n'
                    'echo "security: SecKeychainSearchCopyNext: The specified item could not be found." >&2\n'
                    'exit 44\n' % out)
        os.chmod(tool, stat.S_IRWXU)
        kc = keychain.Keychain(tool=tool, read_deadline=30.0, write_deadline=30.0)
        kc.read('Switchboard test', 'nobody', fresh=True)
        with open(out) as f:
            sid, pgrp = (int(x) for x in f.read().split())
        self.assertNotEqual(sid, os.getsid(0))
        self.assertNotEqual(pgrp, os.getpgrp())


class ARequestEndsAtItsDeadline(unittest.TestCase):
    """net.Http.request resolved the name again inside connect() with no deadline of its own, and its timeout bound
    each socket call, not the request. A refresh whose name lookup stalled outlived the caller's budget, so upkeep said
    the login "was renewed but not yet saved" though nothing had gone out; a server that drips its answer held the
    refresh worker (and the consume lock) far past its deadline."""

    def test_a_name_lookup_that_stalls_ends_at_the_deadline_as_not_sent(self):
        import socket
        from unittest import mock
        from switchboard.accounts import net
        real = socket.getaddrinfo

        def stalled(host, *a, **kw):
            time.sleep(3.0)
            return real('127.0.0.1', 9, *a[1:], **kw)
        t0 = time.monotonic()
        with mock.patch.object(socket, 'getaddrinfo', stalled):
            with self.assertRaises(net.NetError) as e:
                net.Http().request('POST', 'https://token.example.invalid/x', body=b'{}', timeout_s=0.5)
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertEqual((e.exception.kind, e.exception.sent), ('dns', False))

    def test_an_answer_that_drips_in_ends_at_the_deadline(self):
        import socket
        import threading
        from unittest import mock
        from switchboard.accounts import net
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        self.addCleanup(srv.close)

        def drip():
            c, _ = srv.accept()
            try:
                c.recv(65536)
                c.sendall(b'HTTP/1.1 200 OK\r\n')
                for _ in range(20):                      # one byte every 0.2 s: each read is quick, the answer never
                    time.sleep(0.2)                      # ends (stopped after 4 s so the old code ends too)
                    c.sendall(b'X')
            except OSError:
                pass
            finally:
                c.close()
        threading.Thread(target=drip, daemon=True).start()

        class Plain(object):                             # no TLS on the made-up server
            verify_mode, check_hostname, post_handshake_auth = 0, False, None

            def wrap_socket(self, sock, server_hostname=None):
                return sock
        t0 = time.monotonic()
        with mock.patch.object(net, 'tls_context', lambda: Plain()):
            with self.assertRaises(net.NetError) as e:
                net.Http().request('GET', 'https://127.0.0.1:%d/x' % port, timeout_s=1.0)
        self.assertLess(time.monotonic() - t0, 2.5)
        self.assertEqual((e.exception.kind, e.exception.sent), ('timeout', True))

    def test_an_answer_still_waited_for_is_not_called_renewed(self):
        rig = Rig()
        self.addCleanup(rig.close)
        rig.two_accounts()
        rig.set_saved_expiry('uuid-a', time.time() - 60)
        from unittest import mock
        with mock.patch.object(tokens, 'refresh', lambda ctx, aid, budget_s=60.0, reason='upkeep':
                               tokens.Result('unanswered', aid, 'no answer yet', spent_maybe=True)):
            notes = tokens.upkeep_pass(rig.ctx, budget_s=120)
        self.assertEqual(len(notes), 1)
        self.assertNotIn('was renewed', notes[0])
        self.assertIn('waiting for', notes[0])


class SmallEngineFaults(unittest.TestCase):
    def test_a_lock_toucher_that_wakes_as_the_lock_is_let_go_ends_quietly(self):
        """release() set _held to None while a toucher thread past its wait read _held[:2] (a TypeError, a
        traceback on stderr)."""
        from switchboard.accounts import ccfiles

        class Waits(object):
            def __init__(self):
                self.n = 0

            def wait(self, s):
                self.n += 1
                return self.n > 1                       # the first wait times out just as release() runs

            def is_set(self):
                return self.n > 1

            def set(self):
                self.n = 2
        import tempfile
        lock = ccfiles.DirLock(tempfile.mkdtemp(), 10.0)  # the lock folder is still there as the toucher looks
        lock._stop = Waits()
        lock._held = None                               # release() has just let it go
        lock._toucher()                                 # returns, never raises

    def test_a_remove_whose_list_cannot_be_written_removes_nothing(self):
        """remove deleted the saved login, then wrote the list; a full disk left the account listed with no login
        (and the error went out raw)."""
        from unittest import mock
        rig = Rig()
        self.addCleanup(rig.close)
        a_text, _ = rig.two_accounts()
        with mock.patch.object(vault, 'save', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(api.AccountsError) as e:
                roster.remove(rig.ctx, 'uuid-a')
        self.assertIn('not removed', str(e.exception))
        self.assertEqual(rig.vault('uuid-a'), a_text)              # its saved login is still there
        self.assertIn('uuid-a', rig.doc()['accounts'])

    def test_remove_reads_no_login_and_a_refused_delete_removes_nothing(self):
        """Without a read: the uninstaller's --all removes each account and must never read a login of
        Switchboard's, so remove writes the list first, then deletes; a refused delete puts
        the account back as it was."""
        rig = Rig()
        self.addCleanup(rig.close)
        a_text, b_text = rig.two_accounts()
        before = rig.doc()
        rig.kc.rule('delete', service=vault.SERVICE_CURRENT, account='uuid-a', why='locked')
        listed_at_delete = []

        def on_call(verb, service, account):
            if verb == 'delete' and service == vault.SERVICE_CURRENT and account == 'uuid-a':
                listed_at_delete.append('uuid-a' in rig.doc()['accounts'])
        rig.kc.on_call = on_call
        with self.assertRaises(api.AccountsError) as e:
            roster.remove(rig.ctx, 'uuid-a')
        rig.kc.on_call = None
        self.assertEqual(listed_at_delete, [False])        # the list was written first
        self.assertIn('nothing was removed', str(e.exception))
        after = rig.doc()
        self.assertEqual(after['order'], before['order'])
        self.assertEqual(after['accounts']['uuid-a'], before['accounts']['uuid-a'])
        self.assertNotIn('uuid-a', after['removed'])
        self.assertEqual(rig.vault('uuid-a'), a_text)
        reads = [c for c in rig.kc.calls if c[0] == 'read' and c[1].startswith('Switchboard')]
        roster.remove(rig.ctx, 'uuid-b')                              # a remove that goes through
        self.assertEqual([c for c in rig.kc.calls if c[0] == 'read' and c[1].startswith('Switchboard')], reads)
        self.assertIsNone(rig.vault('uuid-b'))

    def test_a_removed_accounts_readings_go_with_it(self):
        """usage.forget was never called, so a removed account's readings and samples stayed."""
        import os
        from switchboard.accounts import usage
        rig = Rig()
        self.addCleanup(rig.close)
        rig.two_accounts()
        path = os.path.join(rig.data, usage.STORE)
        store = usage.blank()
        for aid in ('uuid-a', 'uuid-b'):
            store['accounts'][aid] = {'read_at': time.time()}
            store['samples'][aid] = [[time.time(), 10.0]]
        usage.write_atomic(path, store)
        roster.remove(rig.ctx, 'uuid-a')
        after = usage.load_store(path)
        self.assertNotIn('uuid-a', after['accounts'])
        self.assertNotIn('uuid-a', after['samples'])
        self.assertIn('uuid-b', after['accounts'])

    def test_the_loops_live_stage_outlasts_the_longest_keychain_read(self):
        """The 'live' stage's 20 s was shorter than a Keychain read under load (24 s)."""
        from switchboard.accounts import autoloop, keychain
        longest = min(keychain.READ_CAP, keychain.READ_DEADLINE * keychain.LOAD_FACTOR_MAX)
        self.assertGreater(autoloop.STAGE_DEADLINE['live'], longest)


class ALoginWrittenInTheCommandLineIsSaid(unittest.TestCase):
    """A login too long for `security`'s standard input goes in its arguments (as Claude Code does), where other
    users of the Mac can see it while it runs. Only each process's log said so; doctor runs in a process of its own and
    could not. Now each one is counted beside the Keychain gate, and doctor names the count."""

    def test_each_one_is_counted_where_doctor_reads_it(self):
        import os
        import stat
        import tempfile
        from switchboard import engine
        from switchboard.accounts import keychain
        d = tempfile.mkdtemp()
        tool = os.path.join(d, 'stand-in-tool')
        with open(tool, 'w') as f:
            f.write('#!/bin/sh\nexit 0\n')
        os.chmod(tool, stat.S_IRWXU)
        kc = keychain.Keychain(tool=tool, gate_path=os.path.join(d, 'keychain.gate'), stdin_limit=10)
        kc.write('Switchboard test', 'acct', 'x' * 100)
        kc.write('Switchboard test', 'acct', 'y' * 100)
        self.assertEqual(keychain.argv_note(d)['count'], 2)
        rig = Rig()
        self.addCleanup(rig.close)
        os.makedirs(rig.data, exist_ok=True)
        with open(os.path.join(rig.data, keychain.ARGV_NOTE), 'w') as f:
            f.write('{"count": 2, "last_at": 1800000000.0}')
        h = roster.health(rig.ctx)
        self.assertEqual(h['keychain_argv']['count'], 2)
        lines = [text for good, text in engine.health_items(h)]
        self.assertTrue([t for t in lines if 'command line' in t and '2' in t], lines)


if __name__ == '__main__':
    unittest.main()
