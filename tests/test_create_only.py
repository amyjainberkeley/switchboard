import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# "Create only if still absent": `security add-generic-password` without -U exits 45 and changes nothing when the item
# is there, and of eight such writes at one moment on an absent item exactly one lands (measured on macOS 26.5). With
# -U, exit 45 means another process saved the same item at that moment and macOS refused this write. Neither is a
# broken Keychain, so neither makes the item rest. The put-back writes with "create only" whenever Claude Code's item
# was absent at its last fresh read, so it can never land over a login another process made a moment before.
# The real Keychain wrapper over the suite's stand-in for `security`, and the in-process stand-in; made-up *.example
# accounts.
import json
import os
import random
import shutil
import tempfile
import time
import unittest

from fakes_autoswitch import FakeClock
from fakes_accounts import FakeKeychain, Rig, login_text
from switchboard.accounts import context, facts, keychain, live, putback
from switchboard.accounts.api import ABSENT, PRESENT

SVC, ACCT = 'Claude Code-credentials', 'tester'
MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
REFUSED = ('security: SecKeychainItemModifyContent: The specified item already exists in the keychain.\n')


def value_of(size):
    """A login-shaped text of exactly `size` bytes."""
    head = login_text('sk-ant-oat01-a', 'sk-ant-ort01-r', 1, 2)[:-1] + ',"pad":"'
    return head + 'x' * (size - len(head) - 2) + '"}'


class OverTheStandIn(unittest.TestCase):
    """keychain.Keychain over the stand-in for /usr/bin/security (a child process per call)."""

    def setUp(self):
        self.rig = Rig(real_keychain=True)
        self.sec = self.rig.sec
        self.logs = []
        self.kc = keychain.Keychain(tool=self.sec.tool, read_deadline=120.0, write_deadline=120.0, backoff_s=30.0,
                                    gate_path=os.path.join(self.rig.data, 'keychain.gate'), log=self.logs.append)

    def tearDown(self):
        self.rig.close()

    def last_add(self):
        return self.sec.calls('add')[-1]

    def assert_not_resting(self):
        self.assertEqual(self.kc.state()['resting'], [])
        self.assertIsNone(self.kc.state()['resting_all'])
        finds = len(self.sec.calls('find'))
        self.kc.read(SVC, ACCT)                 # an ordinary read still asks security: the item is not resting
        self.assertEqual(len(self.sec.calls('find')), finds + 1)

    def test_create_only_makes_an_absent_item_with_no_dash_u_on_standard_input(self):
        value = value_of(900)
        self.assertEqual(self.kc.write(SVC, ACCT, value, create_only=True), keychain.Done(True))
        add = self.last_add()
        self.assertEqual(add['via'], 'stdin')
        self.assertNotIn('-U', add['flags'])
        self.assertFalse(add['argv_value'])
        self.assertEqual(self.sec.get(SVC, ACCT), value)

    def test_create_only_makes_an_absent_item_with_no_dash_u_in_the_arguments_when_the_line_is_too_long(self):
        value = value_of(3000)
        self.assertEqual(self.kc.write(SVC, ACCT, value, create_only=True), keychain.Done(True))
        add = self.last_add()
        self.assertEqual(add['via'], 'argv')
        self.assertNotIn('-U', add['flags'])
        self.assertEqual(self.sec.get(SVC, ACCT), value)

    def test_create_only_on_an_item_that_is_there_changes_nothing_and_says_exists_on_both_routes(self):
        first = value_of(800)
        self.sec.put(SVC, ACCT, first)
        for size in (900, 3000):
            done = self.kc.write(SVC, ACCT, value_of(size), create_only=True)
            self.assertEqual(done, keychain.Done(False, 'exists'))
            self.assertNotIn('-U', self.last_add()['flags'])
            self.assertEqual(self.sec.get(SVC, ACCT), first)
        self.assert_not_resting()
        self.assertEqual([x for x in self.logs if 'resting' in x], [])

    def test_an_ordinary_write_refused_with_45_is_collided_and_the_item_does_not_rest(self):
        self.sec.put(SVC, ACCT, value_of(800))
        self.sec.rule(verb='add', exit=45, stderr=REFUSED, times=1)
        done = self.kc.write(SVC, ACCT, value_of(900))
        self.assertEqual(done, keychain.Done(False, 'collided'))
        self.assertIn('-U', self.last_add()['flags'])
        self.assertEqual(self.sec.get(SVC, ACCT), value_of(800))
        self.assert_not_resting()
        self.assertEqual(len([x for x in self.logs if 'at the same moment' in x]), 1, self.logs)
        self.assertTrue(self.kc.write(SVC, ACCT, value_of(900)).ok)          # the next write goes at once

    def test_a_45_that_security_dash_i_reports_only_on_its_error_stream_is_collided_too(self):
        self.sec.rule(verb='add', exit=45, stderr=REFUSED, times=1, exit_zero=True)
        self.assertEqual(self.kc.write(SVC, ACCT, value_of(900)), keychain.Done(False, 'collided'))
        self.assert_not_resting()

    def test_a_45_in_the_arguments_route_is_collided(self):
        self.sec.rule(verb='add', exit=45, stderr=REFUSED, times=1)
        self.assertEqual(self.kc.write(SVC, ACCT, value_of(3000)), keychain.Done(False, 'collided'))
        self.assertEqual(self.last_add()['via'], 'argv')
        self.assert_not_resting()

    def test_any_other_refusal_still_rests_the_item_as_before(self):
        self.sec.rule(verb='add', exit=1, times=1)
        self.assertEqual(self.kc.write(SVC, ACCT, value_of(900)), keychain.Done(False, 'unknown'))
        self.assertEqual(self.kc.state()['resting'], [SVC])

    def test_write_live_passes_create_only_on_and_bumps_no_file_when_the_item_was_there(self):
        ctx = self.rig.ctx
        login = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')
        cred = facts.credentials_file(self.rig.env, self.rig.home)
        with open(cred, 'w') as f:
            f.write('{}')
        self.assertTrue(live.write_live(ctx, login, create_only=True).ok)
        self.assertNotIn('-U', self.last_add()['flags'])
        with open(cred) as f:
            self.assertEqual(f.read(), login)                               # kept in step, as an ordinary write
        other = self.rig.server.add('uuid-a', 'ana@work.example', 'org-a')
        self.assertEqual(live.write_live(ctx, other, create_only=True), keychain.Done(False, 'exists'))
        self.assertEqual(self.rig.live_text(), login)
        with open(cred) as f:
            self.assertEqual(f.read(), login)


class Base(unittest.TestCase):
    real_keychain = False

    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='create-only-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep,
                       real_keychain=self.real_keychain)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)     # Claude Code is on B
        self.saved_b = self.rig.vault('uuid-b')

    def tearDown(self):
        self.use.__exit__(None, None, None)
        putback.reset()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def look(self, after=5.0):
        self.clock.advance(after)
        return putback.look(self.ctx)

    def live_writes(self):
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)


class ThePutBack(Base):
    def test_with_no_item_at_all_the_put_back_only_creates_it(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.look(0)
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(self.rig.kc.created, [(self.rig.service, self.rig.account)])
        self.assertEqual(live.parse(self.rig.live_text()).refresh_token, live.parse(self.saved_b).refresh_token)

    def test_an_item_without_a_login_is_written_the_ordinary_way(self):
        self.rig.set_live(json.dumps(MCP, separators=(',', ':')))
        self.look(0)
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(self.rig.kc.created, [])
        self.assertEqual(json.loads(self.rig.live_text())['mcpOAuth'], MCP['mcpOAuth'])

    def made_first(self, text):
        """Another process makes the item between the put-back's last read and its write."""
        def on_call(verb, service, account):
            if verb == 'write' and service == self.rig.service and not self.rig.live_text():
                self.rig.set_live(text)
        self.rig.kc.on_call = on_call

    def test_a_login_made_first_by_another_process_is_never_written_over(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.look(0)
        theirs = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')
        self.made_first(theirs)
        res = self.look()
        self.assertEqual(res.action, 'leave', res.reason)
        self.assertEqual(self.rig.live_text(), theirs)
        self.assertEqual(self.rig.kc.created, [(self.rig.service, self.rig.account)])
        self.assertEqual(putback.load_record(self.ctx)['done'], [])
        self.assertEqual([e for e in self.rig.events if e[2].get('h') == 'repair'], [])

    def test_an_item_without_a_login_made_first_is_read_again_and_written_the_ordinary_way_at_once(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.look(0)
        self.made_first(json.dumps(MCP, separators=(',', ':')))
        slept = []
        self.ctx.sleep = lambda s: (slept.append(s), self.clock.sleep(s))
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(self.live_writes(), 2)                 # the refused "create", then the ordinary write
        self.assertEqual(slept, [])                             # no gap: nothing was wrong
        blob = json.loads(self.rig.live_text())
        self.assertEqual(blob['claudeAiOauth'], json.loads(self.saved_b)['claudeAiOauth'])
        self.assertEqual(blob['mcpOAuth'], MCP['mcpOAuth'])
        self.assertEqual(len(putback.load_record(self.ctx)['done']), 1)


class ThePutBackOverTheStandIn(Base):
    real_keychain = True

    def test_with_no_item_the_command_has_no_dash_u_and_the_login_lands(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.look(0)
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        adds = [c for c in self.rig.sec.calls('add') if c['service'] == self.rig.service]
        self.assertNotIn('-U', adds[-1]['flags'])
        self.assertEqual(live.parse(self.rig.live_text()).refresh_token, live.parse(self.saved_b).refresh_token)

    def test_a_login_made_first_is_left_as_it_is(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.look(0)
        theirs = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')
        # the stand-in has no hook between read and write: the item is there, and the look's three reads (the look,
        # the look again under the locks, the last read before the write) still answer "no such item", as when
        # another process made it a moment after them
        self.rig.sec.put(self.rig.service, self.rig.account, theirs)
        self.rig.sec.rule(verb='find', service=self.rig.service, exit=44, times=3,
                          stderr='security: The specified item could not be found in the keychain.\n')
        res = self.look()
        self.assertEqual(self.rig.live_text(), theirs, res.reason)
        adds = [c for c in self.rig.sec.calls('add') if c['service'] == self.rig.service]
        self.assertTrue(adds)
        self.assertTrue(all('-U' not in c['flags'] for c in adds), adds)


class ManyMadeUpCases(Base):
    """From a fixed seed: Claude Code's item absent, holding no login, or holding a login; its plaintext file there or
    not; at the put-back's write another process makes the absent item with a login, saves one without a login,
    deletes it, or does nothing; a
    write refused now and then. After every case: (a) an item holding an account login was never changed by the
    put-back; (b) every put-back write after a read that found no item was "create only", and every one after a read
    that found the item was an ordinary write; (c) when it says it put the login back, the item holds the saved login
    of the account the settings name; (d) no write came without Claude Code's save lock."""

    CASES = 1500

    def test_the_rules_hold_over_many_made_up_cases(self):
        rng = random.Random(3636)
        kc = self.rig.kc
        svc, acct = self.rig.service, self.rig.account
        lock_path = facts.storage_lock(self.rig.env, self.rig.home)
        cred = facts.credentials_file(self.rig.env, self.rig.home)
        state = {'last_read': None, 'meddle': None}
        problems = []
        real_read, real_write = FakeKeychain.read, FakeKeychain.write

        def read(service, account, fresh=False, interactive=False):
            got = real_read(kc, service, account, fresh=fresh, interactive=interactive)
            if service == svc:
                state['last_read'] = got.kind
            return got

        def write(service, account, value, interactive=False, create_only=False):
            if service != svc:
                return real_write(kc, service, account, value, interactive=interactive, create_only=create_only)
            if not os.path.isdir(lock_path):
                problems.append('(d) a write without the save lock')
            if state['last_read'] == ABSENT and not create_only:
                problems.append('(b) an ordinary write after a read that found no item')
            if state['last_read'] == PRESENT and create_only:
                problems.append('(b) a create-only write after a read that found the item')
            m, state['meddle'] = state['meddle'], None
            if m == 'login' and kc.get(svc, acct) is not None:
                # over an item that is there, only a writer that ignores Claude Code's save lock could land at this
                # instant, and `security -U` has no compare-and-swap to stop it: not something a rule here can hold
                m = None
            if m == 'login':
                kc.put(svc, acct, self.rig.server.add('uuid-b', 'ana@home.example', 'org-b'))
            elif m == 'no_login':
                kc.put(svc, acct, json.dumps(MCP))
            elif m == 'delete':
                kc.drop(svc, acct)
            before = kc.get(svc, acct)
            done = real_write(kc, service, account, value, interactive=interactive, create_only=create_only)
            after = kc.get(svc, acct)
            if before is not None and live.parse(before).kind == 'oauth' and after != before:
                problems.append('(a) wrote over a login')
            return done

        kc.read, kc.write = read, write
        outcomes = {}
        for n in range(self.CASES):
            try:
                os.unlink(self.ctx.path(putback.STATE))
            except FileNotFoundError:
                pass
            putback.reset()
            kc.rules = []
            self.clock.advance(10.0)            # the record is gone: no wait or limit carries over
            start = rng.choice(('absent', 'absent', 'no_login', 'login'))
            if start == 'absent':
                kc.drop(svc, acct)
            elif start == 'no_login':
                kc.put(svc, acct, json.dumps(MCP))
            else:
                kc.put(svc, acct, self.b_text)
            if rng.random() < 0.3:
                with open(cred, 'w') as f:
                    f.write(json.dumps(MCP) if rng.random() < 0.7 else self.b_text)
            elif os.path.exists(cred):
                os.unlink(cred)
            if rng.random() < 0.2:
                kc.rule('write', svc, why=rng.choice(('collided', 'unknown', 'timeout')), times=rng.randint(1, 6))
            state['meddle'] = rng.choice((None, None, 'login', 'no_login', 'delete'))
            self.look(0)
            res = self.look(putback.PUT_BACK_AFTER_S + 1)
            outcomes[res.action] = outcomes.get(res.action, 0) + 1
            if res.action == 'put_back':
                now = live.parse(kc.get(svc, acct) or '')
                if now.refresh_token != live.parse(self.saved_b).refresh_token:
                    problems.append('(c) put back something else')
            if problems:
                self.fail('case %d (%s): %s' % (n, start, problems))
            self.assertFalse(os.path.exists(lock_path))
        self.assertGreater(outcomes.get('put_back', 0), self.CASES // 5, outcomes)
        self.assertGreater(outcomes.get('leave', 0), self.CASES // 10, outcomes)


if __name__ == '__main__':
    unittest.main()
