import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The look that follows a sighting goes straight to Claude Code's save lock (putback._straight); it must answer as a
# look that reads the item outside the locks first, in every state. From a fixed seed: a loss seen once (the item gone,
# an item without a login, the login only in the plain login file, or an item without a login beside a plain file
# holding one), then one change made-up at random (the login back, a new sign-in, the settings naming nobody or another
# account, the item gone, the plain file written, rewritten or removed, the saved login gone or marked, a sign-in under
# way on the page, a renewal of the saved login), a made-up wait and the sign-in watch's hint, then the second look.
# Each case runs twice on twin stand-ins, with the straight path and with it taken out: the answer, what the item and
# the plain file hold, the record and History must be the same. One difference is allowed, by design: a look on the
# straight path makes no read of the item, so when the watch says the plain file was written and it holds the item's
# very bytes, the straight path notes that write as proof where a look that read the item first would not (it takes
# such a file for one writer's, a switch or a put-back); those cases may differ in the record's proof and in nothing
# else. Made-up *.example accounts.
import json
import os
import random
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, autoloop, context, live, putback, vault
from switchboard.accounts import signin_watch as sw

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
LOSSES = ('item', 'absent', 'file', 'beside')
CHANGES = ('none', 'none', 'login_back', 'new_sign_in', 'nobody', 'other_account', 'item_gone', 'file_login',
           'file_no_login', 'file_gone', 'saved_gone', 'saved_refused', 'page_sign_in', 'saved_renewed')
WAITS = (0.1, 0.5, 1.0, 4.0, 4.5, 15.0, 29.0, 31.0)
HINTS = ('none', 'keychain', 'appeared', 'changed', 'settings')


def make_case(rng):
    return {'loss': rng.choice(LOSSES), 'change': rng.choice(CHANGES), 'first_proof': rng.random() < 0.4,
            'wait': rng.choice(WAITS), 'hint': rng.choice(HINTS), 'other_process': rng.random() < 0.3}


class Twin(object):
    """One made-up Mac for one arm of a case."""

    def __init__(self, wall):
        self.tmp = tempfile.mkdtemp(prefix='straight-many-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=wall, mono=50000.0)
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        putback._KEPT.clear()
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)
        self.cred = os.path.join(self.rig.home, '.claude', '.credentials.json')

    def close(self):
        self.use.__exit__(None, None, None)
        putback.reset()
        putback._KEPT.clear()
        self.rig.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_file(self, text, age=0.0):
        tmp = self.cred + '.cc'
        with open(tmp, 'w') as f:
            f.write(text)
        os.chmod(tmp, 0o600)
        t = self.clock.wall() - age
        os.utime(tmp, (t, t))
        os.replace(tmp, self.cred)

    def no_login_item(self):
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))

    def watch(self, after, hint):
        self.clock.advance(after)
        h = dict(sw.no_hint())
        if hint == 'keychain':
            h['keychain'] = True
        elif hint in ('appeared', 'changed'):
            h['login_file'] = hint
            h['login_file_written'] = True
        elif hint == 'settings':
            h['settings'] = True
        return api.watch_look(h)

    def run(self, case):
        loss = case['loss']
        if loss == 'file':
            self.write_file(self.b_text, age=0.0 if case['first_proof'] else 3600.0)
            self.rig.sec.drop(self.rig.service, self.rig.account)
        elif loss == 'beside':
            self.write_file(self.b_text, age=0.0 if case['first_proof'] else 3600.0)
            self.no_login_item()
        else:
            self.rig.sec.drop(self.rig.service, self.rig.account)
            if loss == 'item':
                self.no_login_item()
            if case['first_proof']:
                self.write_file(json.dumps(MCP))                    # a fallback write seen by the watch, then gone
                os.unlink(self.cred)
        first = self.watch(0.2, 'appeared' if case['first_proof'] else 'keychain')
        if case['other_process']:
            putback.reset()                                         # the next look is another process's
        change = case['change']
        if change == 'login_back':
            self.rig.set_live(self.b_text)
        elif change == 'new_sign_in':
            self.rig.set_live(self.rig.server.add('uuid-b', 'ana@home.example', 'org-b'))
        elif change == 'nobody':
            self.rig.set_config(None)
        elif change == 'other_account':
            self.rig.set_config(identity_block('uuid-a', 'ana@work.example', 'org-a'))
        elif change == 'item_gone':
            self.rig.sec.drop(self.rig.service, self.rig.account)
        elif change == 'file_login':
            self.write_file(self.b_text)
        elif change == 'file_no_login':
            self.write_file(json.dumps(dict(MCP, discoveryState={'theirs': 2})))
        elif change == 'file_gone':
            if os.path.exists(self.cred):
                os.unlink(self.cred)
        elif change == 'saved_gone':
            self.rig.sec.drop(vault.SERVICE_CURRENT, 'uuid-b')
        elif change == 'saved_refused':
            with vault.changing(self.ctx) as doc:
                a = doc['accounts']['uuid-b']
                a['strike'] = {'fp': a.get('fp'), 'at': self.clock.wall()}
        elif change == 'page_sign_in':
            folder = os.path.join(self.rig.data, 'logins', 'made-up-sign-in')
            os.makedirs(folder)
            os.utime(folder, (self.clock.wall(), self.clock.wall()))
        elif change == 'saved_renewed':
            renewed = self.rig.server.refresh_for(self.rig.vault('uuid-b'))
            with vault.changing(self.ctx) as doc:
                vault.save_login(self.ctx, 'uuid-b', renewed)
                vault.note_login(doc['accounts']['uuid-b'], live.parse(renewed), None, self.clock.wall())
        filed = None
        if os.path.exists(self.cred):
            with open(self.cred) as f:
                filed = f.read()
        same_bytes = case['hint'] in ('appeared', 'changed') and filed is not None and filed == self.rig.live_text()
        second = self.watch(case['wait'], case['hint'])
        rec = putback.load_record(self.ctx)
        item = self.rig.live_text()
        filed = None
        if os.path.exists(self.cred):
            with open(self.cred) as f:
                filed = f.read()
        return {'first': first.action,
                'answer': (second.action, second.account, second.key, second.line, second.next_s, second.account_id),
                'item': item, 'file': filed,
                'record': (rec['missing'], rec['missing_how'], len(rec['done']), len(rec['failed']),
                           rec['fault_at'] is not None, (rec['written'] or {}).get('sha'), rec['limit_at']),
                'history': [e[0] for e in self.rig.events], 'same_bytes': same_bytes}


class TheStraightPathAnswersAsALookThatReadsFirst(unittest.TestCase):
    CASES = 2000
    SEED = 4211

    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        patcher = mock.patch.object(autoloop, 'notify_put_back', lambda res: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home

    def arm(self, case, wall, straight):
        twin = Twin(wall)
        try:
            if straight:
                return twin.run(case)
            with mock.patch.object(putback, '_straight', lambda *a, **k: None):
                return twin.run(case)
        finally:
            twin.close()

    def test_every_case_gets_the_same_answer_and_leaves_the_same_state(self):
        rng = random.Random(self.SEED)
        wall = float(int(time.time()))
        took, writes, allowed = 0, 0, 0
        for n in range(self.CASES):
            case = make_case(rng)
            calls = []
            real = putback._straight

            def counted(*a, **k):
                got = real(*a, **k)
                calls.append(got)
                return got
            with mock.patch.object(putback, '_straight', counted):
                a = self.arm(case, wall, True)
            b = self.arm(case, wall, False)
            if a['same_bytes'] and a != b:                          # the one difference allowed: the proof noted
                a = dict(a, record=a['record'][:4] + a['record'][5:])
                b = dict(b, record=b['record'][:4] + b['record'][5:])
                allowed += 1
            self.assertEqual(a, b, 'case %d %r' % (n, case))
            took += any(isinstance(c, api.PutBack) for c in calls)
            writes += a['answer'][0] in ('put_back', putback.PROMOTE)
        print('\n%d cases: the straight path answered %d itself, %d looks wrote, %d differed in the proof noted only'
              % (self.CASES, took, writes, allowed))
        self.assertGreater(took, self.CASES // 10)                  # the check reached the straight path


if __name__ == '__main__':
    unittest.main()
