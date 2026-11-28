import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# No secret leaves the login it belongs to. Real-looking logins (an access token and a refresh token shaped as
# Anthropic's, an email and ids in Claude Code's identity block, a connector login with its own tokens) go through the
# real account engine on its stand-ins (fakes_accounts): a put-back made by the loop's pass, a promote of a login left
# in Claude Code's plain login file, the repair of an item without a login beside that file, and the keep step of the
# sign-in watch's look. Then everything those write or return is searched: put-back.json, auto.log, switchboard.log,
# History (history.jsonl and the page's list), the sign-in record, every PutBack returned (`seen` too) and every
# notification.
#
# Where each kind of string may be, and where it may not:
# - a token (access, refresh, a connector's): nowhere of those.
# - the organization id: nowhere of those.
# - the whole email: History, a notification, a switchboard.log line that names the account, a PutBack's account,
#   reason and line, and put-back.json's `last` (doctor's line names the account of the last put-back). Wherever the
#   email is, it is whole: its local part never stands alone or cut. Never in auto.log (accounts by number there), the
#   sign-in record, a PutBack's `seen`, or anywhere else in put-back.json.
# - the account id (Switchboard keys an account by its Anthropic account id): a PutBack's account_id, put-back.json's
#   `missing` and `written`, and switchboard.log. Never in History, a notification, auto.log, the sign-in record, or a
#   PutBack's seen, reason or line.
# Made-up *.example accounts; no Keychain, network or notification is reached.
import base64
import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock, FakeNet, set_policy
from fakes_accounts import FakeAnthropic, Rig, login_text
from switchboard import core
from switchboard.accounts import api, autoloop, context, putback, roster, sessions, tokens
from switchboard.accounts import signin_watch as sw

A_UUID, A_ORG, A_EMAIL = '6b1f3c9e-2d4a-4f8b-9e7c-1a5d3f8b2c40', '0e9d7c2b-4a6f-4e1d-8b3c-5f2a9d7e6c18', \
    'tessaly.brookhaven@work.example'
B_UUID, B_ORG, B_EMAIL = 'a3f1c9e2-7b4d-4e8a-9c1f-2a6b8d0e4f7a', '5d2e8f1a-3c7b-4a9e-b6d4-8e1f2c5a9b73', \
    'quillon.marbury@home.example'


def made_up_token(tag):
    """About a hundred characters of base64url, as Anthropic's tokens look after their prefix."""
    raw = hashlib.sha512(tag.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')[:93] + 'AA'


class RealLooking(FakeAnthropic):
    """Anthropic's stand-in, issuing tokens shaped as the real ones (prefix, about a hundred characters)."""

    def _issue(self, uuid, n):
        line = self.accounts[uuid]['lineages'][n]
        line['gen'] += 1
        tag = '%s-%d-%d' % (uuid, n, line['gen'])
        line['refresh'] = 'sk-ant-ort01-' + made_up_token('refresh ' + tag)
        access = 'sk-ant-oat01-' + made_up_token('access ' + tag)
        exp = self.wall() + self.LIFETIME
        self.access[access] = (uuid, exp, line.get('org'))
        self.issued.extend([access, line['refresh']])
        return login_text(access, line['refresh'], int(exp * 1000), line['rx_ms'])


CONNECTOR = {'mcpOAuth': {'docs-server|7f3a91c2': {
    'serverName': 'docs-server', 'serverUrl': 'https://docs.example/mcp', 'clientId': 'docs-client-made-up',
    'accessToken': 'mcp_at_' + made_up_token('connector access'), 'refreshToken': 'mcp_rt_' + made_up_token(
        'connector refresh'), 'expiresAt': 1794837600000}}}
CONNECTOR_TOKENS = [CONNECTOR['mcpOAuth']['docs-server|7f3a91c2'][k] for k in ('accessToken', 'refreshToken')]


class ThroughTheRealEngine(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='secrets-stay-out-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        sessions._reset_for_tests()
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        server = RealLooking(wall=self.clock.wall)
        server.issued = []
        self.rig.server = self.rig.ctx.http = server
        self.ctx = self.rig.ctx
        # the history, the page's list and switchboard.log as the installed program writes them
        self.ctx.events, self.ctx.log = context._core_event, context._core_log
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        putback._KEPT.clear()
        roster.forget_live()
        self.rig.keep(A_UUID, A_EMAIL, A_ORG)
        self.clock.advance(1.0)                 # two sign-ins never share an end stamp to the millisecond
        self.b_text = self.rig.keep(B_UUID, B_EMAIL, B_ORG, siblings=CONNECTOR)     # Claude Code is on B
        self.answers = []                       # every PutBack returned
        self.notes = []                         # every notification, as it would be shown
        patcher = mock.patch.object(autoloop, 'notify_put_back', self.notify)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.record = sw.Record(path=core.data_path('signin-record.jsonl'), clock=self.clock)
        deps = autoloop.Deps(net=FakeNet(self.clock), net_ready=lambda: True, load=lambda: 0.5,
                             power=lambda: (True, 100), procs=lambda: 1, disk_free=lambda: 10 ** 12,
                             notify=self.notify, notify_move=lambda note: self.notes.append((note.title, note.body)),
                             put_back=self.loop_put_back)
        self.loop = autoloop.Loop(deps=deps, clock=self.clock, rand=lambda: 0.5)
        set_policy(enabled=False)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        putback.reset()
        putback._KEPT.clear()
        roster.forget_live()
        self.rig.close()
        sessions._reset_for_tests()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    # what Claude Code does, and the engine's two callers

    @property
    def cred(self):
        return os.path.join(self.rig.home, '.claude', '.credentials.json')

    def notify(self, res):
        note = autoloop.put_back_note(res)
        self.notes.append((note.title, note.body))

    def loop_put_back(self):
        res = api.put_back_pass()
        self.answers.append(res)
        return res

    def watch(self, after=0.0, **hint):
        """The sign-in watch's look, and its record line, as the watch writes them."""
        self.clock.advance(after)
        h = dict(sw.no_hint())
        h.update(hint)
        res = api.watch_look(dict(h))
        self.answers.append(res)
        answer = sw._answer(res)
        fields = dict(hint=h, action=sw.word(answer[0]))
        if answer[3] is not None:
            fields['seen'] = answer[3]
        self.record.add('look', **fields)
        return res

    def lose_login(self):
        """The item deleted, then saved again by a chat with only its connector state."""
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(CONNECTOR, discoveryState={'made-up': 1}), separators=(',', ':')))
        roster.forget_live()

    def write_file(self, text, age):
        with open(self.cred, 'w') as f:
            f.write(text)
        os.chmod(self.cred, 0o600)
        t = self.clock.wall() - age
        os.utime(self.cred, (t, t))

    def has_login(self):
        text = self.rig.live_text()
        return bool(text) and json.loads(text).get('claudeAiOauth', {}).get('refreshToken', '').startswith('sk-ant-')

    def quiet(self):
        """Long enough that the next write is a new episode, well inside the put-back limits."""
        self.clock.advance(putback.EPISODE_MAX_S + putback.TAKE_BACK_S + 60)

    # the places

    def text_of(self, name, folder=None):
        try:
            with open(os.path.join(folder or os.environ['SWITCHBOARD_HOME'], name), encoding='utf-8') as f:
                return f.read()
        except OSError:
            return ''

    def places(self):
        """{place: text} of everything searched, the allowed parts split from the rest."""
        record = json.loads(self.text_of(putback.STATE, self.ctx.data_dir) or '{}')
        last = record.pop('last', None)
        missing, written = record.pop('missing', None), record.pop('written', None)
        out = {'put-back.json': json.dumps(record), 'put-back.json last': json.dumps(last),
               'put-back.json missing and written': json.dumps([missing, written]),
               'auto.log': self.text_of(autoloop.LOG), 'switchboard.log': self.text_of('switchboard.log'),
               'history.jsonl': self.text_of('history.jsonl'),
               "the page's list": json.dumps(core.load().get('events')),
               'the sign-in record': self.text_of('signin-record.jsonl'),
               'notifications': json.dumps(self.notes)}
        for i, res in enumerate(self.answers):
            out['PutBack %d seen' % i] = json.dumps(res.seen)
            out['PutBack %d words' % i] = json.dumps([res.action, res.reason, res.line, res.key, res.at, res.next_s])
            out['PutBack %d account' % i] = json.dumps(res.account)
            out['PutBack %d account_id' % i] = json.dumps(res.account_id)
        return out

    def run_every_path(self):
        # 1. a put-back made by the loop's own pass (the first look waits, the next puts it back)
        self.lose_login()
        self.loop.run_pass()
        self.clock.advance(putback.PUT_BACK_AFTER_S + 1)
        self.loop.run_pass()
        self.assertEqual([r.action for r in self.answers][-2:], ['wait', 'put_back'], [r.reason for r in self.answers])
        self.assertTrue(self.has_login())
        # 2. a promote: Claude Code's fallback wrote its whole object to its plain file and deleted the item
        self.quiet()
        self.write_file(self.rig.live_text(), 0.0)
        self.rig.sec.drop(self.rig.service, self.rig.account)
        roster.forget_live()
        self.watch(login_file='appeared', login_file_written=True)
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, putback.PROMOTE, self.answers[-1].reason)
        self.assertTrue(os.path.exists(self.cred))              # left for a later look to take away
        # 3. the repair of an item without a login beside the plain file that holds it
        self.quiet()
        self.write_file(self.rig.live_text(), 3600.0)
        self.rig.set_live(json.dumps(dict(CONNECTOR, discoveryState={'made-up': 7}), separators=(',', ':')))
        roster.forget_live()
        res = self.watch(0.2, keychain=True)
        for _ in range(200):                    # the wait grows with the put-backs made within the hour
            if res.action != 'wait':
                break
            res = self.watch(5.0, keychain=True)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(putback.load_record(self.ctx)['written']['how'], 'over')
        # 4. the keep step: Claude Code renewed its login, and the look brings the saved copy up to it
        self.quiet()
        self.rig.claude_renews()
        roster.forget_live()
        self.assertEqual(self.watch(keychain=True).action, 'kept', self.answers[-1].reason)
        self.assertGreaterEqual(len(self.notes), 2)                 # the two put-backs were told

    def secrets(self):
        return list(self.rig.server.issued) + CONNECTOR_TOKENS

    def test_no_token_and_no_id_where_it_does_not_belong_and_the_email_only_whole_where_it_may_be(self):
        self.run_every_path()
        places = self.places()
        self.assertGreaterEqual(len(self.secrets()), 8)
        for place, text in places.items():
            with self.subTest(place=place):
                for secret in self.secrets():
                    self.assertNotIn(secret, text)
                    self.assertNotIn(secret[13:40] if secret.startswith('sk-ant-') else secret[7:40], text)
                for org in (A_ORG, B_ORG):
                    self.assertNotIn(org, text)
        email_may = ('history.jsonl', "the page's list", 'notifications', 'switchboard.log', 'put-back.json last')
        id_may = ('put-back.json missing and written', 'switchboard.log')
        for place, text in places.items():
            with self.subTest(place=place):
                for email in (A_EMAIL, B_EMAIL):
                    local = email.split('@')[0]
                    if place in email_may or place.endswith(' account') or place.endswith(' words') and \
                            'seen' not in place:
                        self.assertEqual(text.count(local), text.count(email), (place, text))   # whole, always
                    else:
                        self.assertNotIn(local, text)
                for aid in (A_UUID, B_UUID):
                    if place in id_may or place.endswith(' account_id'):
                        continue
                    self.assertNotIn(aid, text)
        # and the places that may hold them do: the test sees what it looks for
        self.assertIn(B_EMAIL, places['history.jsonl'])
        self.assertIn(B_EMAIL, places['notifications'])
        self.assertIn(B_EMAIL, places['switchboard.log'])
        self.assertIn('put back', places['auto.log'])
        self.assertIn('"action":"put_back"', places['the sign-in record'])
        self.assertIn(B_UUID, places['put-back.json missing and written'] + ''.join(
            v for k, v in places.items() if k.endswith(' account_id')))

    def test_seen_holds_plain_facts_only(self):
        self.run_every_path()
        for res in self.answers:
            for k, v in (res.seen or {}).items():
                self.assertRegex(k, r'^[a-z_]{1,32}$')
                self.assertTrue(v is None or isinstance(v, (bool, int, float)) or
                                (isinstance(v, str) and (v in sw.WORDS or len(v) == 16)), (res.action, k, v))


if __name__ == '__main__':
    unittest.main()
