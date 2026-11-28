import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# When the sign-in in use stops working is read from the login Claude Code holds (api.Live carries its access expiry
# and its sign-in end), and from the account's record in Switchboard's account list only when the live login does not
# tell. The record can be a generation behind: the keep pass files each renewal Claude Code makes, but one it could not
# file yet (no answer from Anthropic for a near stamp) leaves the older generation there, whose access token expired
# hours before, and read from it the moment is missed. The real live read on the account engine's stand-ins
# (fakes_accounts), then the automatic loop on its stand-ins (fakes_autoswitch) with a fake clock. Made-up *.example
# accounts; no Keychain, network or notification is reached.
import dataclasses
import json
import os
import shutil
import tempfile
import unittest

from fakes_autoswitch import FakeClock, FakeEngine, FakeNet, FakeTokens, Home, T0, account, make_loop, set_policy, \
    usage_body
from fakes_accounts import Rig
from switchboard.accounts import api, autoloop, context, policy, roster, vault

H, D = 3600.0, 86400.0


def ms(t):
    return int(t * 1000)


class TheLiveRead(unittest.TestCase):
    """api.live() on the account engine's stand-ins: Claude Code is on B."""

    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='live-times-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.rig = Rig()
        self.use = context.use(self.rig.ctx)
        self.use.__enter__()
        roster.forget_live()
        self.text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b')
        self.oauth = json.loads(self.text)['claudeAiOauth']

    def tearDown(self):
        self.use.__exit__(None, None, None)
        roster.forget_live()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_an_account_login_carries_its_access_expiry_and_sign_in_end_from_one_keychain_read(self):
        roster.forget_live()
        reads = self.rig.kc.count('read', self.rig.service)
        got = api.live()
        self.assertEqual((got.state, got.account_id), (api.PRESENT, 'uuid-b'))
        self.assertEqual((got.expires_ms, got.rx_ms), (self.oauth['expiresAt'], self.oauth['refreshTokenExpiresAt']))
        self.assertEqual(self.rig.kc.count('read', self.rig.service) - reads, 1)

    def test_the_login_in_claude_codes_plain_file_carries_its_own_times(self):
        newer = self.rig.server.refresh_for(self.text)
        cred = os.path.join(self.rig.home, '.claude', '.credentials.json')
        with open(cred, 'w') as f:
            f.write(newer)
        os.chmod(cred, 0o600)
        self.rig.sec.drop(self.rig.service, self.rig.account)
        roster.forget_live()
        got = api.live()
        o = json.loads(newer)['claudeAiOauth']
        self.assertEqual((got.state, got.from_file), (api.PRESENT, True))
        self.assertEqual((got.expires_ms, got.rx_ms), (o['expiresAt'], o['refreshTokenExpiresAt']))

    def test_no_times_without_an_account_login(self):
        blob = json.loads(self.text)
        blob['claudeAiOauth'].update({'accessToken': '', 'refreshToken': ''})
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        roster.forget_live()
        got = api.live()
        self.assertEqual((got.state, got.expires_ms, got.rx_ms), ('wiped', None, None))
        self.rig.sec.drop(self.rig.service, self.rig.account)
        roster.forget_live()
        got = api.live()
        self.assertEqual((got.state, got.expires_ms, got.rx_ms), (api.ABSENT, None, None))


def keep_record(aid, expires_at, ends_at):
    """The account's record in Switchboard's account list, with the login's two times as the keep pass files them."""
    ctx = context.current()
    doc = vault.blank()
    for a, n in (('a1', 1), ('a2', 2)):
        doc['accounts'][a] = {'id': a, 'uuid': a, 'email': '%s@acct.example' % a}   # every real list has 'uuid'
        doc['order'].append(a)
    doc['accounts'][aid].update(expires_ms=ms(expires_at), rx_ms=ms(ends_at))
    with vault.account_lock(ctx, timeout=5.0):
        vault.save(ctx, doc)


def live_of(aid, expires_at=None, ends_at=None, **kw):
    return api.Live(state=api.PRESENT, account_id=aid, email='%s@acct.example' % aid, proof='fingerprint',
                    expires_ms=None if expires_at is None else ms(expires_at),
                    rx_ms=None if ends_at is None else ms(ends_at), **kw)


class SigninTimes(Home):
    """autoloop.signin_times(account id, the pass's live login): the live login's first, the record's second."""

    def setUp(self):
        super().setUp()
        # a1's record is one generation behind: its access token expired 7 hours ago; the same sign-in's end, which
        # Claude Code stores a little lower at each renewal
        keep_record('a1', T0 - 7 * H, T0 + 50 * 60 + 5)

    def test_the_live_login_of_that_account_first(self):
        got = autoloop.signin_times('a1', live_of('a1', T0 + H, T0 + 50 * 60))
        self.assertEqual(got, (T0 + H, T0 + 50 * 60))
        self.assertEqual(policy.ends_at(*got), T0 + H - 300)
        self.assertIsNone(policy.ends_at(*autoloop.signin_times('a1')))      # the record alone misses the moment

    def test_the_record_when_the_live_login_does_not_tell(self):
        record = (T0 - 7 * H, T0 + 50 * 60 + 5)
        for lv in (None, live_of('a2', T0 + H, T0 + 50 * 60), live_of('a1'),
                   live_of('a1', T0 + H, T0 + 50 * 60, degraded=True),
                   dataclasses.replace(live_of('a1', T0 + H, T0 + 50 * 60), state='wiped'),
                   api.Live(state=api.UNREADABLE, account_id=None, email=None, proof='none', why='timeout')):
            with self.subTest(live=lv):
                self.assertEqual(autoloop.signin_times('a1', lv), record)

    def test_a_live_login_without_a_sign_in_end_has_none(self):
        self.assertEqual(autoloop.signin_times('a1', live_of('a1', T0 + H)), (T0 + H, None))
        self.assertEqual(autoloop.signin_times('zz', None), (None, None))


class Engine(FakeEngine):
    """The stand-in engine: a1's live login carries `times` (access expiry, sign-in end; ms); a switch brings the
    target's login, which carries none."""

    times = (None, None)

    def live(self):
        got = super().live()
        if got.state == api.PRESENT and self.active == 'a1':
            return dataclasses.replace(got, expires_ms=self.times[0], rx_ms=self.times[1])
        return got


def ok(five, week):
    return (200, {}, usage_body(five=five, week=week, five_reset=T0 + 4 * H, week_reset=T0 + 4 * D))


class TheLoop(Home):
    """a1's live login is on its last access token: it expires an hour from now, and the sign-in ends 50 minutes from
    now, before the renewal 5 minutes ahead of that expiry. Automatic switching is off."""

    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.engine = Engine([account('a1', 1), account('a2', 2)], 'a1', self.clock)
        self.net = FakeNet(self.clock)
        self.net.set('tok-a1', ok(10.0, 20.0))
        self.net.set('tok-a2', ok(10.0, 20.0))
        self.told = []
        set_policy(enabled=False)
        self.expires, self.end = T0 + H, T0 + 50 * 60
        self.stop = self.expires - 300

    def loop(self):
        loop = make_loop(self.engine, self.clock, self.net, FakeTokens())
        loop.d.notify_move = self.told.append              # the real signin_times: the live login, then the record
        return loop

    def run_until(self, until):
        loop, held = self.loop(), []
        while self.clock.wall() <= until:
            loop.run_pass()
            held.append((self.clock.wall(), self.engine.active))
            self.clock.advance(60.0)
        return held

    def test_with_the_record_a_generation_behind_one_move_between_ten_and_five_minutes_before_it_stops(self):
        keep_record('a1', self.expires - 8 * H, self.end + 5)
        self.engine.times = (ms(self.expires), ms(self.end))
        held = self.run_until(self.stop + 600)
        self.assertEqual([(s['to'], s['kind'], s['source']) for s in self.engine.switches], [('a2', 'forced', 'auto')])
        at = self.engine.switches[0]['at']
        self.assertTrue(self.stop - 600 <= at <= self.stop - 300, at - self.stop)
        self.assertTrue(all(who == 'a2' for t, who in held if t >= self.stop))
        self.assertTrue(self.engine.switches[0]['reason'].startswith(
            "a1@acct.example's sign-in ends in 10 minutes. Switchboard moved Claude Code to a2@acct.example"))
        self.assertEqual(len(self.told), 1)

    def test_with_neither_time_known_nothing_moves(self):
        held = self.run_until(self.stop + 600)
        self.assertEqual(self.engine.switches, [])
        self.assertTrue(all(who == 'a1' for _, who in held))
        self.assertEqual(self.told, [])

    def test_the_record_alone_a_generation_behind_misses_it(self):
        keep_record('a1', self.expires - 8 * H, self.end + 5)
        self.run_until(self.stop + 600)
        self.assertEqual(self.engine.switches, [])


if __name__ == '__main__':
    unittest.main()
