"""The account engine around conflicted logins, request deadlines, stops and transcripts. Each class's docstring names
the failure its tests rule out."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import inspect
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from fakes_autoswitch import Home
from fakes_accounts import Rig
from switchboard.accounts import api, capture, live, net, roster, runas, sessions, tokens, vault

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')


def lapsed(text, ago_s=3600):
    """The same login with its access token lapsed (its refresh token, and so its fingerprint, unchanged)."""
    blob = json.loads(text)
    blob['claudeAiOauth']['expiresAt'] = int((time.time() - ago_s) * 1000)
    return json.dumps(blob, separators=(',', ':'))


def mark_conflict(rig, aid, names='uuid-zzz'):
    with vault.changing(rig.ctx) as doc:
        doc['accounts'][aid]['conflict'] = {'token_uuid': names, 'at': time.time()}


def answer_names(server, uuid):
    """Anthropic's token answer names `uuid` as the login's owner; returns the undo."""
    real = server._token

    def other(body, timeout_s):
        status, h, data = real(body, timeout_s)
        obj = json.loads(data)
        if isinstance(obj.get('account'), dict):
            obj['account']['uuid'] = uuid
        return status, h, json.dumps(obj).encode()
    server._token = other
    return lambda: setattr(server, '_token', real)


# ---------- the whole-request deadline with TLS on ----------

class Dripper(object):
    """A made-up server on 127.0.0.1 that reads one request, then answers one byte every 0.2 s for `seconds`."""

    def __init__(self, test, seconds, wrap=None):
        self.srv = socket.socket()
        self.srv.bind(('127.0.0.1', 0))
        self.srv.listen(1)
        test.addCleanup(self.srv.close)
        self.port = self.srv.getsockname()[1]
        self.answering_at = None
        self.seconds, self.wrap = seconds, wrap
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        try:
            c, _ = self.srv.accept()
        except OSError:
            return
        s = c
        try:
            s = self.wrap(c) if self.wrap else c
            s.recv(65536)
            self.answering_at = time.monotonic()
            s.sendall(b'HTTP/1.1 200 OK\r\n')
            end = time.monotonic() + self.seconds
            while time.monotonic() < end:
                time.sleep(0.2)
                s.sendall(b'X')
        except (OSError, ValueError):
            pass
        finally:
            s.close()
            c.close()


class MovesTheConnectionLikeSsl(object):
    """A stand-in TLS context whose wrap_socket hands the connection to a new socket and leaves the one it was given
    empty (its descriptor -1), as ssl's own wrap_socket does; no TLS, so no handshake time on a busy Mac."""
    verify_mode, check_hostname, post_handshake_auth = 0, False, None

    def wrap_socket(self, sock, server_hostname=None):
        timeout = sock.gettimeout()
        moved = socket.socket(sock.family, sock.type, sock.proto, fileno=sock.detach())
        moved.settimeout(timeout)
        return moved


class ARequestEndsAtItsDeadlineWithTlsOn(unittest.TestCase):
    """net.Http's whole-request timer was built on the plain socket, which ssl's wrap_socket empties (the
    connection moves to the TLS socket). With real TLS the timer shut nothing, so a server that drips a byte now and
    then held a refresh (and its consume lock) far past the deadline. A wrap_socket that returns the same socket would
    never show it."""

    def test_a_wrap_that_moves_the_connection_as_ssl_does_still_ends_at_the_deadline(self):
        server = Dripper(self, 12.0)
        t0 = time.monotonic()
        with mock.patch.object(net, 'tls_context', lambda: MovesTheConnectionLikeSsl()):
            with self.assertRaises(net.NetError) as e:
                net.Http().request('GET', 'https://127.0.0.1:%d/x' % server.port, timeout_s=1.5)
        self.assertLess(time.monotonic() - t0, 6.0)                 # (the drip goes on for 12 s)
        self.assertEqual((e.exception.kind, e.exception.sent), ('timeout', True))

    def test_a_real_tls_answer_that_drips_ends_at_the_deadline(self):
        tool = '/usr/bin/openssl' if os.path.exists('/usr/bin/openssl') else shutil.which('openssl')
        if not tool:
            self.skipTest('needs openssl for a throwaway certificate')
        d = tempfile.mkdtemp(prefix='sb-tls-')
        self.addCleanup(shutil.rmtree, d, True)
        cert, key = os.path.join(d, 'c.pem'), os.path.join(d, 'k.pem')
        subprocess.run([tool, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert, '-days',
                        '2', '-subj', '/CN=localhost'], check=True, capture_output=True, timeout=120)
        sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        sctx.load_cert_chain(cert, key)
        cctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        cctx.check_hostname, cctx.verify_mode = False, ssl.CERT_NONE        # (the made-up certificate is no proof)
        deadline = 8.0                                  # room for the handshake on a busy Mac at background priority
        server = Dripper(self, 40.0, wrap=lambda c: sctx.wrap_socket(c, server_side=True))
        t0 = time.monotonic()
        with mock.patch.object(net, 'tls_context', lambda: cctx):
            with self.assertRaises(net.NetError) as e:
                net.Http().request('GET', 'https://127.0.0.1:%d/x' % server.port, timeout_s=deadline)
        took = time.monotonic() - t0
        if server.answering_at is None or server.answering_at - t0 > deadline - 0.5:
            self.skipTest('the Mac was too busy for the handshake to end inside the deadline (%.1f s)' % took)
        self.assertLess(took, deadline + 6.0)                       # (the drip goes on for 40 s)
        self.assertEqual((e.exception.kind, e.exception.sent), ('timeout', True))

    def test_a_connection_handed_over_after_the_time_is_up_is_shut_at_once(self):
        shut = []
        with mock.patch.object(net, '_shut', shut.append):
            d = net._Deadline(time.monotonic() - 1.0)               # the time is up already: it fires in 50 ms
            d.start('the plain socket')
            end = time.monotonic() + 10
            while not d.fired and time.monotonic() < end:
                time.sleep(0.02)
            d.hold('the TLS socket')                                # wrap_socket returned just after it fired
            d.cancel()
            d.hold('a socket after the request ended')              # never shut once the request is over
        self.assertEqual(shut, ['the plain socket', 'the TLS socket'])


# ---------- an address that never answers leaves time for the next ----------

class Clock(object):
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class ASilentFirstAddressLeavesTimeForTheNext(unittest.TestCase):
    """Each address was tried with all the time left, so a first address that drops packets (often IPv6 on a
    broken network) used the whole request's time and the next was never tried: every request said "timeout"."""

    def test_the_second_address_is_tried_and_answers(self):
        clock, tried = Clock(), []

        class Sock(object):
            def __init__(self, family, kind, proto):
                self.timeout = None

            def settimeout(self, t):
                self.timeout = t

            def connect(self, addr):
                tried.append((addr, self.timeout))
                if addr[0] == 'silent':
                    clock.now += self.timeout                       # it waits its whole share, then gives up
                    raise socket.timeout('timed out')

            def close(self):
                pass
        addrs = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('silent', 443)),
                 (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('answers', 443))]
        fake = SimpleNamespace(socket=Sock, timeout=socket.timeout)
        with mock.patch.object(net, 'socket', fake), mock.patch.object(net, 'time', clock):
            s = net._connect('host.example', 443, addrs, clock.now + 10.0)
        self.assertEqual([a[0] for a, _ in tried], ['silent', 'answers'])
        self.assertAlmostEqual(tried[0][1], 5.0)                    # half the time for the first of two
        self.assertAlmostEqual(tried[1][1], 5.0)                    # all that is left for the last
        self.assertIsInstance(s, Sock)


# ---------- `switchboard run` never uses a login Anthropic says is another account's ----------

class ARunNeverUsesAConflictedLogin(unittest.TestCase):
    """Only the switch read status 'conflict'. `switchboard run A` seeded a run folder with A's saved login (the
    other person's) under A's name, and saved what that run renewed back into A's slot."""

    def setUp(self):
        self.rig = Rig()
        self.addCleanup(self.rig.close)
        self.ctx = self.rig.ctx
        self.ctx.claude = '/usr/bin/true'
        self.rig.two_accounts()                                     # Claude Code is on B; A is an idle saved login
        self.ran = []
        patch = mock.patch.object(runas, '_run', lambda *a, **k: self.ran.append(a) or 0)
        patch.start()
        self.addCleanup(patch.stop)

    def run_items(self):
        return [k for k in self.rig.kc.items() if k[0].startswith('Claude Code-credentials-')]

    def test_a_run_as_a_conflicted_account_is_refused_and_seeds_nothing(self):
        mark_conflict(self.rig, 'uuid-a')
        before = self.run_items()
        with self.assertRaises(api.AccountsError) as e:
            runas.run_as(self.ctx, 'uuid-a', ['-p', 'hi'])
        self.assertEqual(e.exception.kind, 'dead_login')
        self.assertIn('belongs to another account', str(e.exception))
        self.assertEqual(self.ran, [])
        self.assertEqual(self.run_items(), before)
        self.assertEqual(self.rig.doc()['claims'], {})

    def test_the_claim_itself_refuses_one_marked_meanwhile(self):
        mark_conflict(self.rig, 'uuid-a')
        with self.assertRaises(api.AccountsError) as e:
            runas._claim_and_seed(self.ctx, 'uuid-a', 'a@example.com')
        self.assertIn('belongs to another account', str(e.exception))
        self.assertEqual(self.rig.doc()['claims'], {})

    def test_a_mark_set_while_its_login_is_read_still_refuses_the_run(self):
        marked, before = [], self.run_items()

        def on_call(verb, service, account):            # capture's check marks it meanwhile (it takes the account lock)
            if verb == 'read' and service == vault.SERVICE_CURRENT and account == 'uuid-a' and not marked:
                marked.append(True)
                mark_conflict(self.rig, 'uuid-a')
        self.rig.kc.on_call = on_call
        try:
            with self.assertRaises(api.AccountsError) as e:
                runas._claim_and_seed(self.ctx, 'uuid-a', 'a@example.com')
        finally:
            self.rig.kc.on_call = None
        self.assertEqual(marked, [True])
        self.assertIn('belongs to another account', str(e.exception))
        self.assertEqual(self.rig.doc()['claims'], {})
        self.assertEqual(self.run_items(), before)

    def test_a_run_whose_own_renewal_finds_the_conflict_says_so(self):
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        undo = answer_names(self.rig.server, 'uuid-zzz')
        try:
            with self.assertRaises(api.AccountsError) as e:
                runas.run_as(self.ctx, 'uuid-a', ['-p', 'hi'])
        finally:
            undo()
        self.assertIn('belongs to another account', str(e.exception))
        self.assertNotIn("Couldn't finish renewing", str(e.exception))
        self.assertEqual(self.ran, [])


# ---------- a set-aside login goes to the account Anthropic named ----------

class ASetAsideLoginGoesToItsOwner(unittest.TestCase):
    """A refresh answer that named another account set the new login aside under the account it was taken
    from, and nothing ever offered it to anyone. When the named account kept the same login (one login in two slots,
    one of them kept on a label alone), its saved copy was now spent: it needed a sign-in for a login Switchboard
    held. And once the mark was cleared, the set-aside login (another account's) stayed in the recovery item."""

    def setUp(self):
        self.rig = Rig()
        self.addCleanup(self.rig.close)
        self.ctx = self.rig.ctx
        self.rig.keep('uuid-a', 'a@example.com', 'org-a')
        self.rig.keep('uuid-c', 'c@example.com', 'org-c')
        self.rig.keep('uuid-b', 'b@example.com', 'org-b')           # Claude Code is on B; A and C are idle
        c_text = self.rig.vault('uuid-c')
        self.spent = live.parse(c_text).fingerprint
        in_a = lapsed(c_text)                                       # A's slot holds C's login (kept on a label)
        self.rig.sec.put(vault.SERVICE_CURRENT, 'uuid-a', in_a)
        with vault.changing(self.ctx) as doc:
            vault.note_login(doc['accounts']['uuid-a'], live.parse(in_a), None, time.time())

    def conflict_entries(self):
        return [e for e in vault.stashed(self.ctx) if e.get('reason') == tokens.CONFLICT]

    def test_the_named_account_holding_the_spent_login_gets_the_new_one_at_once(self):
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual((res.outcome, res.why), ('stashed', tokens.CONFLICT))
        self.assertIn(self.rig.server.valid_refresh('uuid-c'), self.rig.vault('uuid-c'))     # C keeps working
        doc = self.rig.doc()
        self.assertIn('conflict', doc['accounts']['uuid-a'])
        self.assertEqual(doc['accounts']['uuid-c']['fp'], res.new_fp)
        self.assertEqual(doc['accounts']['uuid-c']['proof']['kind'], 'token_account')
        self.assertEqual(self.conflict_entries(), [])                                         # nothing left aside
        self.assertEqual(dict((a.id, a.status) for a in roster.accounts(self.ctx))['uuid-c'], 'ok')

    def test_when_it_could_not_be_saved_there_at_once_that_accounts_next_look_takes_it(self):
        self.rig.kc.rule('write', service=vault.SERVICE_CURRENT, account='uuid-c', why='locked')
        res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual((res.outcome, res.why), ('stashed', tokens.CONFLICT))
        self.assertNotIn(self.rig.server.valid_refresh('uuid-c'), self.rig.vault('uuid-c'))
        self.assertEqual([e.get('names') for e in self.conflict_entries()], ['uuid-c'])
        tokens.refresh(self.ctx, 'uuid-c', budget_s=60)                                       # its next look
        self.assertIn(self.rig.server.valid_refresh('uuid-c'), self.rig.vault('uuid-c'))
        self.assertEqual(self.conflict_entries(), [])
        self.assertNotIn(self.rig.server.valid_refresh('uuid-c'), self.rig.vault('uuid-a'))   # never A's slot

    def test_one_nobody_needs_goes_once_the_mark_is_cleared(self):
        self.rig.sec.put(vault.SERVICE_CURRENT, 'uuid-a', lapsed(self.rig.server.add('uuid-a', 'a@example.com',
                                                                                     'org-a')))
        with vault.changing(self.ctx) as doc:
            vault.note_login(doc['accounts']['uuid-a'], live.parse(self.rig.vault('uuid-a')), None, time.time())
        undo = answer_names(self.rig.server, 'uuid-zzz')            # an account not kept here
        try:
            res = tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        finally:
            undo()
        self.assertEqual((res.outcome, res.why), ('stashed', tokens.CONFLICT))
        self.assertEqual(len(self.conflict_entries()), 1)
        tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(len(self.conflict_entries()), 1)                                     # kept while marked
        self.rig.sign_in('uuid-a', 'a@example.com', 'org-a')        # a fresh /login as A, proven by its profile
        roster.add_current(self.ctx)
        self.assertNotIn('conflict', self.rig.doc()['accounts']['uuid-a'])
        tokens.refresh(self.ctx, 'uuid-a', budget_s=60)
        self.assertEqual(self.conflict_entries(), [])
        self.assertIsNone(self.rig.kc.get(vault.SERVICE_RECOVERY, 'successor:uuid-a'))


# ---------- every stop outwaits a refresh already sent ----------

class EveryStopOutwaitsASentRefresh(unittest.TestCase):
    """The loop waits at exit for a refresh already sent (up to 35 s), but the page killed a stuck loop after
    10 s, the page's stop and the uninstaller after 30 s: a refresh sent just before the stop could be killed before
    its answer was saved (the old login spent, the new one lost: the account needed a sign-in)."""

    def test_the_loops_exit_wait_covers_an_answer_and_its_save(self):
        self.assertGreaterEqual(tokens.EXIT_WAIT_S, tokens.RESPONSE_TIMEOUT_S + tokens.PERSIST_RESERVE_S)
        self.assertEqual(inspect.signature(tokens._wait_at_exit).parameters['limit_s'].default, tokens.EXIT_WAIT_S)

    def test_no_stop_of_the_page_kills_before_that(self):
        from switchboard import engine
        host = engine.AutoHost
        waits = {'STOP_WAIT': host.STOP_WAIT, 'TERM_WAIT': host.TERM_WAIT,
                 'stop()': inspect.signature(host.stop).parameters['wait'].default,
                 'stop_soon()': inspect.signature(host.stop_soon).parameters['wait'].default}
        for name, wait in waits.items():
            self.assertGreater(wait, tokens.EXIT_WAIT_S, name)

    def test_the_pages_own_exit_waits_as_long_and_launchd_gives_it_the_time(self):
        from switchboard import engine, server
        seen = []
        with mock.patch.object(engine.HOST, 'stop', lambda wait=engine.AutoHost.STOP_WAIT: seen.append(wait)), \
                mock.patch.object(server.lanes, 'stop', lambda: None):
            with self.assertRaises(SystemExit):
                server.on_sigterm()
        self.assertEqual(len(seen), 1)
        self.assertGreater(seen[0], tokens.EXIT_WAIT_S)
        with open(os.path.join(ROOT, 'install.sh')) as f:
            m = re.search(r'<key>ExitTimeOut</key><integer>(\d+)</integer>', f.read())
        self.assertGreater(int(m.group(1)), engine.AutoHost.STOP_WAIT + 5)     # its stop: the wait, then KILL and 5 s

    def test_the_uninstaller_waits_as_long_for_a_lone_loop_and_a_page_started_by_hand(self):
        with open(os.path.join(ROOT, 'uninstall.sh')) as f:
            text = f.read()
        block = text[text.index('stop_loop() {'):text.index('stop_loop || exit 1')]
        term = re.search(r'kill -TERM "\$pid".*?while \[ \$i -lt (\d+) \] && is_loop "\$pid"; do i=\$\(\(i \+ 1\)\); '
                         r'sleep 1; done', block, re.S)
        self.assertGreater(int(term.group(1)), tokens.EXIT_WAIT_S)
        page = re.search(r'while \[ \$i -lt (\d+) \] && \[ -n "\$\(page_pids \|\| true\)" \]; do i=\$\(\(i \+ 1\)\); '
                         r'sleep 0\.25; done', text)
        from switchboard import engine
        self.assertGreater(int(page.group(1)) * 0.25, engine.AutoHost.STOP_WAIT + 5)


# ---------- no usage is read with a conflicted account's saved login ----------

class NoUsageIsReadWithAConflictedLogin(unittest.TestCase):
    """access_token handed out a conflicted account's saved access token (the other account's), so numbers
    read with it would be shown under the wrong account."""

    def test_the_token_for_usage_is_refused(self):
        rig = Rig()
        self.addCleanup(rig.close)
        rig.two_accounts()
        self.assertEqual(roster.access_token(rig.ctx, 'uuid-a').kind, api.PRESENT)
        mark_conflict(rig, 'uuid-a')
        got = roster.access_token(rig.ctx, 'uuid-a')
        self.assertEqual((got.kind, got.why), (api.UNREADABLE, 'conflict'))
        self.assertEqual(roster.access_token(rig.ctx, 'uuid-b').kind, api.PRESENT)    # the others as before


# ---------- a profile answer that proves the login clears the mark ----------

class AProfileProofClearsTheMark(unittest.TestCase):
    """An account kept on its label, marked 'conflict' by a token answer, whose login Anthropic's profile then
    said IS its own, kept the mark: its proof turned 'profile' (so the check never ran again) and it stayed refused
    for switching and renewal until a new sign-in."""

    def test_the_agreeing_profile_answer_clears_it(self):
        rig = Rig()
        self.addCleanup(rig.close)
        rig.two_accounts()
        rig.sign_in('uuid-c', 'c@example.com', 'org-c')
        rig.server.script.append(('profile', net.NetError('dns', False)))
        capture.reset()
        capture.keep_pass(rig.ctx, force=True)                      # kept on its label alone
        self.assertEqual(rig.doc()['accounts']['uuid-c']['proof']['kind'], 'label')
        mark_conflict(rig, 'uuid-c')                                # a token answer named someone else meanwhile
        capture.reset()
        capture.keep_pass(rig.ctx, force=True)                      # Anthropic's profile: it is C's own
        doc = rig.doc()
        self.assertEqual(doc['accounts']['uuid-c']['proof']['kind'], 'profile')
        self.assertNotIn('conflict', doc['accounts']['uuid-c'])
        self.assertEqual(dict((a.id, a.status) for a in roster.accounts(rig.ctx))['uuid-c'], 'ok')


# ---------- what a transcript says ----------

WATCHED = 'https://claude.ai/artifact/WatchWatchWatch1234567'


def stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace('+00:00', 'Z')


def tool_use(name, t, uid):
    return {'type': 'assistant', 'timestamp': stamp(t),
            'message': {'content': [{'type': 'tool_use', 'id': uid, 'name': name, 'input': {}}]}}


def result(text, t, uid):
    return {'type': 'user', 'timestamp': stamp(t),
            'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': uid, 'content': text}]}}


def notice(text, t):
    return {'type': 'assistant', 'timestamp': stamp(t), 'isApiErrorMessage': True,
            'message': {'model': '<synthetic>', 'content': [{'type': 'text', 'text': text}]}}


class Transcripts(Home):
    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.root = os.path.join(os.environ['CLAUDE_CONFIG_DIR'], 'projects')
        self.scanner = sessions.Scanner(root=self.root, procs=lambda: 2)

    def write(self, rel, lines, age):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a') as f:
            for d in lines:
                f.write(d if isinstance(d, str) else json.dumps(d) + '\n')
        t = self.now - age
        os.utime(path, (t, t))
        return path

    def look(self):
        return self.scanner.look([(self.now - 7200, 'uuid-a')], now=self.now)


class AWatchWhoseCallIsBeforeTheFirstRead(Transcripts):
    """A transcript seen for the first time is read from its last FIRST_TAIL bytes, with no tool call
    known; an Artifact tool's answer whose call lay just before that start was no watch, so a session watching an
    artifact could be switched off it."""

    def filler(self, size):
        line = json.dumps({'type': 'user', 'timestamp': stamp(self.now - 200),
                           'message': {'role': 'user', 'content': 'x' * 1000}}) + '\n'
        return [line] * (size // len(line) + 1)

    def test_the_call_before_the_cut_is_found_and_the_watch_counts(self):
        self.write('proj/s1.jsonl', [tool_use('ArtifactComments', self.now - 300, 'call-1')], 300)
        self.write('proj/s1.jsonl', self.filler(sessions.FIRST_TAIL), 200)          # the cut falls in here
        self.write('proj/s1.jsonl', [result('Now watching %s for comments.' % WATCHED, self.now - 60, 'call-1')], 60)
        obs = self.look()['obligations']
        self.assertEqual([(o['kind'], o['url'], o['account_id']) for o in obs], [('watch', WATCHED, 'uuid-a')])

    def test_another_tools_answer_with_the_same_words_is_still_no_watch(self):
        self.write('proj/s1.jsonl', [tool_use('Bash', self.now - 300, 'call-2')], 300)
        self.write('proj/s1.jsonl', self.filler(sessions.FIRST_TAIL), 200)
        self.write('proj/s1.jsonl', [result('Now watching %s for comments.' % WATCHED, self.now - 60, 'call-2')], 60)
        self.assertEqual(self.look()['obligations'], [])


class NotTheAccountsLimit(Transcripts):
    """Claude Code's notice for the server's own short throttle says "(not your usage limit)", and its words
    "usage limit" counted as the account's limit: a healthy account was marked refused and switched away from. (Found
    beside it: a notice that says "out of extra usage" never reached LIMIT_RE, which names it, because the line was
    first required to hold "limit".)"""

    def test_the_servers_throttle_is_not_the_accounts_limit(self):
        text = 'API Error: Server is temporarily limiting requests (not your usage limit) · Rate limited'
        self.assertEqual(sessions.events_in_line(json.dumps(notice(text, self.now - 60)).encode(), 1.0), [])
        self.write('proj/s1.jsonl', [notice(text, self.now - 60)], 60)
        self.assertIsNone(self.look()['limited_at'])

    def test_the_real_limit_notices_still_count(self):
        for text in ("You've hit your limit · resets 4pm", 'Claude AI usage limit reached|1794955200',
                     '5-hour limit reached ∙ resets 3pm', "You're out of extra usage · resets Nov 20"):
            with self.subTest(text=text):
                got = sessions.events_in_line(json.dumps(notice(text, self.now - 30)).encode(), 1.0)
                self.assertEqual([k for _, k, _ in got], ['limit'])


if __name__ == '__main__':
    unittest.main()
