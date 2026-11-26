"""Acting as another account without touching the live login (runas.py; includes pid reuse): a private config
folder and its own Keychain item, the account claimed while it runs, and what the run renewed saved back."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import sys
import threading
import time
import unittest

from fakes_accounts import FileKeychain, Rig
from switchboard.accounts import facts, live, runas, switcher, tokens, vault
from switchboard.accounts.api import AccountsError

FAKE_CLAUDE = r'''#!%(python)s
import fcntl, hashlib, json, os, sys, time, unicodedata
spec = json.load(open(%(spec)r))
rec = {'argv': sys.argv[1:], 'cwd': os.getcwd(), 'pid': os.getpid(),
       'env': dict((k, os.environ.get(k)) for k in ('CLAUDE_CONFIG_DIR', 'CLAUDE_SECURESTORAGE_CONFIG_DIR',
                                                    'ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN', 'KEEP_ME'))}
cfg = os.environ.get('CLAUDE_CONFIG_DIR')
def kc(change):
    fd = os.open(spec['keychain'] + '.lock', os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        try:
            data = json.load(open(spec['keychain']))
        except (OSError, ValueError):
            data = {}
        out = change(data)
        json.dump(data, open(spec['keychain'] + '.tmp', 'w'))
        os.replace(spec['keychain'] + '.tmp', spec['keychain'])
        return out
    finally:
        os.close(fd)
if cfg:
    svc = 'Claude Code-credentials-' + hashlib.sha256(unicodedata.normalize('NFC', cfg).encode()).hexdigest()[:8]
    key = svc + '\u0000' + 'tester'
    rec['service'] = svc
    rec['seed'] = kc(lambda d: d.get(key))
    with open(os.path.join(cfg, '.claude.json')) as f:
        rec['claude_json'] = json.load(f)
    if spec.get('rotate') and rec['seed']:
        blob = json.loads(rec['seed'])
        blob['claudeAiOauth']['refreshToken'] = 'sk-ant-ort01-renewed-in-run'
        blob['claudeAiOauth']['accessToken'] = 'sk-ant-oat01-renewed-in-run'
        blob['claudeAiOauth']['expiresAt'] += 3600 * 1000
        kc(lambda d: d.__setitem__(key, json.dumps(blob)))
end = time.time() + 60
while spec.get('wait') and not os.path.exists(spec['wait']) and time.time() < end:
    time.sleep(0.05)
with open(spec['record'], 'w') as f:
    json.dump(rec, f)
sys.exit(spec.get('exit', 0))
'''


class RunCase(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        kc_path = os.path.join(self.rig.root, 'keychain.json')
        self.rig.kc = self.rig.sec = FileKeychain(kc_path)
        self.rig.ctx.keychain = self.rig.kc
        self.ctx = self.rig.ctx
        self.a_text, self.b_text = self.rig.two_accounts()          # Claude Code is on B
        self.spec_path = os.path.join(self.rig.root, 'claude-spec.json')
        self.record = os.path.join(self.rig.root, 'claude-record.json')
        self.claude = os.path.join(self.rig.root, 'fake-claude')
        with open(self.claude, 'w') as f:
            f.write(FAKE_CLAUDE % {'python': sys.executable, 'spec': self.spec_path})
        os.chmod(self.claude, 0o700)
        self.ctx.claude = self.claude
        self.spec(keychain=kc_path, record=self.record)

    def tearDown(self):
        self.rig.close()

    def spec(self, **kw):
        try:
            with open(self.spec_path) as f:
                spec = json.load(f)
        except OSError:
            spec = {}
        spec.update(kw)
        with open(self.spec_path, 'w') as f:
            json.dump(spec, f)

    def seen(self):
        with open(self.record) as f:
            return json.load(f)

    def env(self, **extra):
        e = {'PATH': '/usr/bin:/bin', 'HOME': self.rig.home, 'USER': 'tester', 'KEEP_ME': 'yes',
             'ANTHROPIC_API_KEY': 'sk-ant-api03-should-go', 'CLAUDE_CODE_OAUTH_TOKEN': 'should-go'}
        e.update(extra)
        return e


class Runs(RunCase):
    def test_running_as_the_live_account_runs_plainly_on_the_default_login(self):
        code = runas.run_as(self.ctx, 'uuid-b', ['-p', 'hi'], env=self.env(CLAUDE_CONFIG_DIR='/elsewhere'), timeout=120)
        self.assertEqual(code, 0)
        seen = self.seen()
        self.assertIsNone(seen['env']['CLAUDE_CONFIG_DIR'])            # on the default login, never a second copy
        self.assertEqual(seen['argv'], ['-p', 'hi'])
        self.assertEqual(self.rig.doc()['claims'], {})

    def test_running_as_another_account_uses_a_private_folder_and_its_own_keychain_item(self):
        self.spec(exit=3)
        code = runas.run_as(self.ctx, 'uuid-a', ['-p', 'hello'], env=self.env(), timeout=120)
        self.assertEqual(code, 3)
        seen = self.seen()
        folder = seen['env']['CLAUDE_CONFIG_DIR']
        self.assertTrue(folder.startswith(os.path.realpath(os.path.join(self.rig.data, 'run'))) or
                        folder.startswith(os.path.join(self.rig.data, 'run')))
        self.assertEqual(seen['service'], facts.keychain_service({'CLAUDE_CONFIG_DIR': folder}))
        seed = json.loads(seen['seed'])
        self.assertEqual(seed['claudeAiOauth'], json.loads(self.a_text)['claudeAiOauth'])
        self.assertEqual(seen['claude_json']['oauthAccount']['accountUuid'], 'uuid-a')
        self.assertTrue(seen['claude_json']['hasCompletedOnboarding'])
        self.assertIsNone(seen['env']['ANTHROPIC_API_KEY'])            # what would bypass the login is removed
        self.assertIsNone(seen['env']['CLAUDE_CODE_OAUTH_TOKEN'])
        self.assertEqual(seen['env']['KEEP_ME'], 'yes')
        self.assertFalse(os.path.exists(folder))                       # tidied: folder, item, claim
        self.assertIsNone(self.rig.kc.get(seen['service'], 'tester'))
        self.assertEqual(self.rig.doc()['claims'], {})
        self.assertEqual(self.rig.live_text(), self.b_text)            # the live login never moved
        self.assertEqual((self.rig.config().get('oauthAccount') or {}).get('accountUuid'), 'uuid-b')

    def test_the_macs_own_mcp_logins_are_never_copied_into_a_run(self):
        blob = json.loads(self.rig.vault('uuid-a'))
        blob['mcpOAuth'] = {'srv': 'machine-wide'}
        self.rig.kc.put('Switchboard login', 'uuid-a', json.dumps(blob))
        runas.run_as(self.ctx, 'uuid-a', [], env=self.env(), timeout=120)
        self.assertNotIn('mcpOAuth', json.loads(self.seen()['seed']))

    def test_a_login_the_run_renewed_is_saved_back(self):
        self.spec(rotate=True)
        runas.run_as(self.ctx, 'uuid-a', [], env=self.env(), timeout=120)
        saved = live.parse(self.rig.vault('uuid-a'))
        self.assertEqual(saved.refresh_token, 'sk-ant-ort01-renewed-in-run')
        self.assertEqual(saved.refresh_expires_ms, live.parse(self.a_text).refresh_expires_ms)

    def test_a_run_claims_its_account_so_no_switch_or_refresh_touches_it_meanwhile(self):
        go = os.path.join(self.rig.root, 'go')
        self.spec(wait=go)
        self.rig.set_saved_expiry('uuid-a', time.time() - 60)
        out = {}
        t = threading.Thread(target=lambda: out.setdefault('code', runas.run_as(self.ctx, 'uuid-a', [],
                                                                               env=self.env(), timeout=120)))
        t.start()
        end = time.time() + 60
        while 'uuid-a' not in self.rig.doc()['claims'] and time.time() < end:
            time.sleep(0.05)
        try:
            with self.assertRaises(AccountsError) as e:
                switcher.switch(self.ctx, 'uuid-a')
            self.assertEqual(e.exception.kind, 'claimed')
            self.assertEqual(tokens.refresh(self.ctx, 'uuid-a', budget_s=60).why, 'claimed')
            with self.assertRaises(AccountsError) as e:
                runas.run_as(self.ctx, 'uuid-a', [], env=self.env(), timeout=10)
            self.assertEqual(e.exception.kind, 'claimed')
        finally:
            open(go, 'w').close()
            t.join(120)
        self.assertEqual(out.get('code'), 0)

    def test_an_unreadable_live_login_refuses_a_run(self):
        self.rig.kc.rule('read', service=self.rig.service, why='timeout', times=-1)
        with self.assertRaises(AccountsError) as e:
            runas.run_as(self.ctx, 'uuid-a', [], env=self.env(), timeout=10)
        self.assertEqual(e.exception.kind, 'keychain_slow')
        self.assertEqual(self.rig.doc()['claims'], {})

    def test_a_run_from_inside_a_run_is_refused(self):
        self.rig.env['CLAUDE_CONFIG_DIR'] = os.path.join(self.rig.data, 'run', 'run-inner')
        os.makedirs(self.rig.env['CLAUDE_CONFIG_DIR'])
        with self.assertRaises(AccountsError):
            runas.run_as(self.ctx, 'uuid-a', [], env=self.env(), timeout=10)


class StaleClaims(RunCase):
    def claim(self, start='gone-start', rotated=True):
        folder = os.path.join(self.rig.data, 'run', 'run-dead')
        os.makedirs(folder)
        service = facts.keychain_service({'CLAUDE_CONFIG_DIR': folder})
        seeded = live.parse(self.a_text)
        blob = json.loads(self.a_text)
        if rotated:
            blob['claudeAiOauth'].update({'refreshToken': 'sk-ant-ort01-from-dead-run',
                                          'accessToken': 'sk-ant-oat01-from-dead-run',
                                          'expiresAt': seeded.expires_ms + 7200 * 1000})
        self.rig.kc.put(service, 'tester', json.dumps(blob))
        with vault.changing(self.ctx) as doc:
            doc['claims']['uuid-a'] = {'pid': 4242, 'start': 'run-start', 'dir': folder, 'service': service,
                                       'account': 'tester', 'seeded_fp': seeded.fingerprint,
                                       'seeded_rx': seeded.refresh_expires_ms, 'at': time.time()}
        self.rig.procs.table[4242] = start
        return folder, service

    def test_a_claim_whose_processes_died_is_settled_and_its_renewed_login_saved(self):
        self.rig.procs.table.pop(4242, None)
        folder, service = self.claim()
        self.rig.procs.table.pop(4242, None)
        runas.reap_stale_claims(self.ctx)
        self.assertEqual(live.parse(self.rig.vault('uuid-a')).refresh_token, 'sk-ant-ort01-from-dead-run')
        self.assertIsNone(self.rig.kc.get(service, 'tester'))
        self.assertFalse(os.path.exists(folder))
        self.assertEqual(self.rig.doc()['claims'], {})

    def test_a_recycled_pid_does_not_keep_a_dead_claim_alive(self):
        folder, service = self.claim(start='a-different-program')       # pid alive, but a later process
        self.assertIsNone(vault.claimed(self.ctx, self.rig.doc(), 'uuid-a'))
        runas.reap_stale_claims(self.ctx)
        self.assertEqual(self.rig.doc()['claims'], {})

    def test_a_live_claim_is_left_alone_and_a_cannot_tell_start_counts_as_alive(self):
        self.claim(start='run-start')
        runas.reap_stale_claims(self.ctx)
        self.assertIn('uuid-a', self.rig.doc()['claims'])
        self.rig.procs.table[4242] = None                               # ps could not say: never "gone"
        self.assertIsNotNone(vault.claimed(self.ctx, self.rig.doc(), 'uuid-a'))

    def test_a_dead_runs_item_that_cannot_be_read_is_kept_until_it_can(self):
        self.rig.procs.table.pop(4242, None)
        folder, service = self.claim()
        self.rig.procs.table.pop(4242, None)
        self.rig.kc.rule('read', service=service, why='locked', times=1)
        runas.reap_stale_claims(self.ctx)
        self.assertIn('uuid-a', self.rig.doc()['claims'])               # never removed unread
        self.assertIsNotNone(self.rig.kc.get(service, 'tester'))
        runas.reap_stale_claims(self.ctx)
        self.assertEqual(live.parse(self.rig.vault('uuid-a')).refresh_token, 'sk-ant-ort01-from-dead-run')


if __name__ == '__main__':
    unittest.main()
