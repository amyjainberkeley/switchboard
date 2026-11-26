"""The page's side: sign-ins, links, the loop, the hook and its words. Each class's docstring names the failure its
tests rule out."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import connect, core, engine
from switchboard.accounts import api
from fakes_accounts_api import install
import test_server_browser_setup


class Ended(object):
    """A sign-in process that has finished well."""
    pid = 0

    def poll(self):
        return 0


def account(email, aid='acct-new'):
    return api.Account(id=aid, email=email, org_id=None, org_name=None, plan=None, alias=None, order=1, status='ok',
                       refresh_expires_at=None, added_at=0.0, fingerprint=None)


class AFinishedSignInIsKeptThroughARefusalWorthAnotherTry(test_server_browser_setup.Sandbox):
    """A finished sign-in whose keeping the engine refused for a reason that passes (no network, a slow Keychain)
    was cleared at once (its Keychain item and folder deleted), so the person had to sign in again."""

    def setUp(self):
        super().setUp()
        self.addCleanup(connect.RUNNING.clear)
        self.addCleanup(connect.NOTES.clear)
        self.fake = install(self)
        self.cleaned = []
        self.patch(connect, '_clean', lambda d: self.cleaned.append(d))

    def finished(self, email='new@lab.example'):
        d = tempfile.mkdtemp(dir=self.tmp)
        with open(os.path.join(d, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': {'emailAddress': email}} if email else {}, f)
        connect.RUNNING['new'] = {'proc': Ended(), 'dir': d, 'started': time.time(), 'email': '', 'profile': None}
        return d

    def texts(self, st):
        return [e['text'] for e in st['events']]

    def asked(self):
        return [c for c in self.fake.calls if c[0] == 'add_login']

    def test_a_network_refusal_keeps_its_login_and_tries_again_until_it_is_kept(self):
        d = self.finished()
        self.fake.fail['add_login'] = api.AccountsError('network', "Switchboard couldn't confirm with Anthropic whose "
                                                                   "the new sign-in is (dns). Nothing was changed; try "
                                                                   "again in a minute.")
        st = core.blank_state()
        connect.finish(st)
        self.assertEqual(self.cleaned, [])                          # its Keychain item and folder stay
        self.assertIn('new', connect.RUNNING)
        self.assertFalse([t for t in self.texts(st) if 'did not finish' in t], self.texts(st))
        self.assertTrue([t for t in self.texts(st) if 'tries again by itself' in t], self.texts(st))
        connect.finish(st)                                          # the next try is not due yet: nothing asked
        self.assertEqual(self.asked(), [('add_login', d)])
        del self.fake.fail['add_login']
        connect.RUNNING['new']['retry_at'] = 0                       # it is due now
        connect.finish(st)
        self.assertEqual(self.cleaned, [d])
        self.assertNotIn('new', connect.RUNNING)
        self.assertIn('Added new@lab.example', self.texts(st))

    def test_a_refusal_no_try_can_change_is_said_and_cleared_at_once(self):
        d = self.finished()
        self.fake.fail['add_login'] = api.AccountsError('disagree', "The new sign-in's login belongs to x@lab.example, "
                                                                    "but its settings name y@lab.example. Nothing was "
                                                                    "changed; press Add account to try again.")
        st = core.blank_state()
        connect.finish(st)
        self.assertEqual(self.cleaned, [d])
        self.assertIn('The sign-in did not finish. Press Add account to try again.', self.texts(st))

    def test_the_tries_end_after_ten_minutes_and_that_is_said(self):
        d = self.finished()
        self.fake.fail['add_login'] = api.AccountsError('keychain_slow', "macOS's Keychain is slow to answer just now. "
                                                                         "Try again in a minute.")
        st = core.blank_state()
        connect.finish(st)
        c = connect.RUNNING['new']
        c['ended'] -= connect.RETRY_FOR + 1
        c['retry_at'] = 0
        connect.finish(st)
        self.assertEqual(self.cleaned, [d])
        self.assertIn('The sign-in did not finish. Press Add account to try again.', self.texts(st))

    def test_the_page_says_the_sign_in_is_being_kept_not_that_it_waits_for_one(self):
        self.finished()
        self.fake.fail['add_login'] = api.AccountsError('network', 'No answer from Anthropic. Try again in a minute.')
        st = core.blank_state()
        connect.finish(st)
        rows = connect.view(st)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].get('keeping'))
        self.assertIn('No answer from Anthropic', rows[0].get('line') or '')

    def test_a_folder_whose_settings_name_no_account_is_still_handed_to_the_engine(self):
        """The engine names the account from Anthropic's answer; the page gave up first."""
        d = self.finished(email=None)
        self.patch(api, 'add_login', lambda config_dir: account('new@lab.example'))
        st = core.blank_state()
        connect.finish(st)
        self.assertIn('Added new@lab.example', self.texts(st))
        self.assertEqual(self.cleaned, [d])


class ASignInsKeychainItemGoesThroughTheEngine(unittest.TestCase):
    """connect._clean named the item's account by the login name even where Claude Code uses
    "claude-code-user", and ran `security` through the PATH outside the engine's Keychain wrapper."""

    def test_clean_asks_the_engine_and_runs_no_keychain_tool_itself(self):
        d = tempfile.mkdtemp()
        asked, ran = [], []
        with mock.patch.object(engine, 'remove_sign_in', lambda folder: asked.append(folder) or True, create=True), \
                mock.patch.object(connect, 'run', lambda cmd, **kw: ran.append(cmd)):
            connect._clean(d)
        self.assertEqual(asked, [d])
        self.assertEqual(ran, [])
        self.assertFalse(os.path.exists(d))

    def test_the_engine_removes_only_a_sign_in_folders_own_item_by_claude_codes_own_rule(self):
        from fakes_accounts import Rig
        from switchboard.accounts import facts, roster
        rig = Rig(user='jörg')                                  # a login name Claude Code does not use as is
        self.addCleanup(rig.close)
        folder = os.path.join(rig.root, 'signin')
        os.makedirs(folder)
        env_t = {'CLAUDE_CONFIG_DIR': folder, 'USER': rig.env['USER']}
        service, acct = facts.keychain_service(env_t), facts.keychain_account(rig.env)
        self.assertEqual(acct, 'claude-code-user')
        rig.sec.put(service, acct, '{"claudeAiOauth": {}}')
        rig.set_live('{"claudeAiOauth": {"accessToken": "live"}}')
        self.assertTrue(roster.remove_sign_in(rig.ctx, folder))
        self.assertIsNone(rig.sec.get(service, acct))
        self.assertIsNotNone(rig.live_text())                       # the live login is never touched
        with self.assertRaises(api.AccountsError):
            roster.remove_sign_in(rig.ctx, os.path.join(rig.home, '.claude'))       # Claude Code's own folder


class AChromeNeedSwitchOutlivesTheHook(unittest.TestCase):
    """The PreToolUse hook asked for the need switch on a thread and returned after 20 s; the hook's
    process then exited (or Claude Code ended it), cutting off a switch still writing (a slow Keychain), half done until
    the engine's journal was next settled. The switch now runs in a process of its own (its own session)."""

    def child(self, body):
        d = tempfile.mkdtemp()
        path = os.path.join(d, 'child.py')
        with open(path, 'w') as f:
            f.write(body)
        return d, [sys.executable, '-I', '-B', path]

    def test_the_answer_comes_back_when_the_switch_is_quick(self):
        from switchboard import session_hooks
        _, cmd = self.child('import json, sys\nsys.stdout.write(json.dumps({"went": "b@lab.example"}) + "\\n")\n')
        went = session_hooks._ask_need_switch({'b@lab.example': {'id': 'acct-b'}}, ['b@lab.example'], str, budget=20,
                                              command=cmd)
        self.assertEqual(went[0], 'b@lab.example')

    def test_a_slow_switch_goes_on_by_itself_after_the_hook_stops_waiting(self):
        from switchboard import session_hooks
        d, cmd = self.child('import os, sys, time\nopen(sys.argv[1] + "/sid", "w").write(str(os.getsid(0)))\n'
                            'time.sleep(1.5)\nopen(sys.argv[1] + "/done", "w").write("1")\n')
        cmd.append(d)
        t0 = time.monotonic()
        went = session_hooks._ask_need_switch({}, ['b@lab.example'], str, budget=0.3, command=cmd)
        self.assertEqual(went, (None, None))
        self.assertLess(time.monotonic() - t0, 1.2)
        for _ in range(100):
            if os.path.exists(os.path.join(d, 'done')):
                break
            time.sleep(0.05)
        self.assertTrue(os.path.exists(os.path.join(d, 'done')))         # it finished although nobody waited
        with open(os.path.join(d, 'sid')) as f:
            self.assertNotEqual(int(f.read()), os.getsid(0))              # in a session of its own

    def test_the_real_package_answers_in_that_process(self):
        import subprocess
        from switchboard import session_hooks
        parent = os.path.dirname(os.path.dirname(os.path.abspath(session_hooks.__file__)))
        r = subprocess.run(session_hooks.need_command(parent, {}, [], str), capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, '{"went": null}'), r.stderr)

    def test_the_child_runs_the_in_process_part_and_answers_in_one_line(self):
        from switchboard import session_hooks
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, 'switchboard'))
        open(os.path.join(root, 'switchboard', '__init__.py'), 'w').close()
        with open(os.path.join(root, 'switchboard', 'session_hooks.py'), 'w') as f:
            f.write('def _need_in_process(accounts, followers, why_of):\n'
                    '    return (followers[0], None) if why_of(followers[0]) == "why " + followers[0] else (None, None)\n')
        cmd = session_hooks.need_command(root, {'b@lab.example': {'id': 'acct-b'}}, ['b@lab.example'],
                                         lambda e: 'why ' + e)
        went = session_hooks._ask_need_switch({}, [], str, budget=20, command=cmd)
        self.assertEqual(went[0], 'b@lab.example')


import test_keep


class KeepingNamesTheAccountReallyKept(test_keep.Sandbox):
    """The page's look kept "who" by Claude Code's config label, but the engine keeps the account the login really
    belongs to. With the label naming B and the login A's (already kept), the history and a notification said "Kept B"
    (never true), every few seconds."""

    def setUp(self):
        super().setUp()
        core.set_setting('auto_set_by_you', True)

    def use(self, add_current):
        p = mock.patch.object(api, 'add_current', add_current)
        p.start()
        self.addCleanup(p.stop)

    def as_account(self, email):
        aid = test_keep.FakeAccounts.hold(self.fake, email, test_keep.ORG.get(email, ''))
        return [a for a in self.fake.accounts() if a.id == aid][0]

    def test_a_label_naming_another_account_than_the_login_never_says_kept(self):
        self.fake.hold(test_keep.A)
        real_a = [a for a in self.fake.accounts() if a.email == test_keep.A][0]
        self.signed_in(test_keep.B)                            # the label says B; the login is A's
        self.use(lambda: self.fake.calls.append(['add']) or real_a)
        t = 1000.0
        for dt in (0, 6, 12, 18):
            self.look(t + dt)
        self.assertEqual(self.kept_events(), [])
        self.assertEqual(self.notes(), [])
        self.assertEqual(self.adds(), 1)                       # and it is not asked again every few seconds

    def test_a_new_account_kept_under_another_label_is_named_as_itself(self):
        self.fake.hold(test_keep.A)
        self.signed_in(test_keep.B)                            # the label says B; the login is C's, kept now
        self.use(lambda: self.fake.calls.append(['add']) or self.as_account('zoe@lab.example'))
        t = 1000.0
        self.look(t)
        self.look(t + 6)
        self.assertEqual(self.kept_events(), ['Kept zoe@lab.example \u00b7 2 accounts'])


class AClickedLinkNeverWaitsLongForTheBrowser(unittest.TestCase):
    """A clicked link (answered at once, so the link app does not open it itself) waited with no limit for the
    browser lock while a daily re-proof or a Check held it (15 to 40 s or more), so nothing opened. The page's Open
    and `switchboard open` did the same on a request thread, enough of them filling the page's 16."""

    URL = 'https://claude.ai/artifact/AbCdEfGhIjKlMnOpQr1234'

    def setUp(self):
        from switchboard import artifacts
        self.artifacts = artifacts
        self.ran = []
        for name, value in (('GO_WAIT', 0.2), ('GO_PAGE_WAIT', 0.3),
                            ('_go', lambda url, aid, quiet: self.ran.append(url) or {'email': None, 'checked': True}),
                            ('_map_soon', lambda: None)):
            p = mock.patch.object(artifacts, name, value, create=True)
            p.start()
            self.addCleanup(p.stop)
        self.assertTrue(artifacts.GO_LOCK.acquire(timeout=5))      # a re-proof or a Check holds the browser
        self.addCleanup(artifacts.GO_LOCK.release)

    def within(self, seconds, fn, *a, **kw):
        """fn's answer (or the exception it raised), or a failure when it has not come back in `seconds`."""
        import threading
        box = {}

        def run():
            try:
                box['value'] = fn(*a, **kw)
            except Exception as e:  # noqa: BLE001 - handed back to the test
                box['error'] = e
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(seconds)
        self.assertFalse(t.is_alive(), 'still waiting for the browser after %g s' % seconds)
        return box

    def test_a_clicked_link_opens_from_the_owner_map_when_the_browser_is_busy(self):
        opened = []
        with mock.patch.object(self.artifacts, 'open_from_map',
                               lambda url, m=None: opened.append(url) or {'by': 'map', 'checked': False}):
            got = self.within(3, self.artifacts.go_quietly, self.URL)
        self.assertEqual(opened, [self.URL])
        self.assertEqual(got.get('value', {}).get('by'), 'map')

    def test_the_pages_open_says_busy_instead_of_holding_a_request_thread(self):
        from switchboard import server
        from switchboard.core import SwitchError
        got = self.within(3, server._act, '/api/go', {'url': self.URL})
        self.assertIsInstance(got.get('error'), SwitchError)
        self.assertIn('Try again in a moment', str(got['error']))
        self.assertEqual(self.ran, [])


import test_app_engine


class TurningAutoOffNeverWaitsForTheLoop(test_app_engine.Base):
    """Turning automatic switching off (the page, the menu bar, `switchboard auto off`) stopped the loop on the
    request thread: SIGTERM, up to 30 s for it to end, then SIGKILL and 5 s more, under the choice lock."""

    setUp = test_app_engine.Host.setUp
    popen = test_app_engine.Host.popen
    killpg = test_app_engine.Host.killpg

    def test_off_answers_at_once_and_the_loop_is_ended_in_the_background(self):
        import signal
        self.host.ensure()
        p = self.procs[-1]
        p.ends_on_term = False                          # in the middle of a switch: it does not end at SIGTERM
        waited = []
        real_wait = p.wait

        def slow_wait(timeout=None):
            waited.append(timeout)
            time.sleep(0.3)                             # stands in for the loop finishing its stage
            return real_wait(timeout)
        p.wait = slow_wait
        self.fake.auto['enabled'] = False
        t0 = time.monotonic()
        self.host.ensure()
        self.assertLess(time.monotonic() - t0, 0.2)     # the caller did not wait for the loop
        self.assertEqual(self.killed[0], (p.pid, signal.SIGTERM))
        self.assertFalse(self.host.running())
        self.fake.auto['enabled'] = True
        self.host.ensure()
        self.assertEqual(len(self.procs), 1)            # nothing new starts while the old loop is ending
        self.host.stop()                                # the page's own exit waits for it
        self.assertIn((p.pid, signal.SIGKILL), self.killed)
        self.assertEqual(self.host.stopping, 0)
        self.assertFalse(os.path.exists(self.host.pid_file()))


class WordsThatSayWhatIsTrue(test_server_browser_setup.Sandbox):
    """The page's Open said a link "may have been deleted" (never say that). macOS's consent lines
    named Google Chrome even when the profiles are Brave's or Edge's."""

    def test_no_profile_could_open_it_never_says_it_may_be_deleted(self):
        from switchboard import artifacts, browser
        from switchboard.core import SwitchError
        self.patch(artifacts, 'load', lambda: {'accounts': {}, 'artifacts': [], 'order': []})
        self.patch(artifacts, 'refresh', lambda: None)
        self.patch(artifacts, 'candidates', lambda st, item: [('P1', 'a@lab.example')])
        self.patch(browser, 'profiles', lambda: {'P1': 'Work'})
        self.patch(browser, 'running', lambda: True)
        self.patch(browser, 'tabs', lambda *a, **k: [])
        self.patch(browser, 'open_url', lambda *a, **k: None)
        self.patch(browser, 'watch_tab', lambda aid, before, timeout=15: ('wrong', ('w', 't', 'u', 'Page not found')))
        self.patch(browser, 'close_tab', lambda *a: None)
        self.patch(artifacts, 'learn_wrong', lambda *a: None)
        with self.assertRaises(SwitchError) as e:
            artifacts._go('https://claude.ai/artifact/AbCdEfGhIjKlMnOpQr1234', 'AbCdEfGhIjKlMnOpQr1234', False)
        self.assertNotIn('deleted', str(e.exception))
        self.assertIn('None of your browser profiles could open that link', str(e.exception))

    def test_the_consent_lines_name_the_browser_that_holds_the_profiles(self):
        from switchboard import browser, server
        self.patch(browser, 'app', lambda: 'Brave Browser')
        for state in ('denied', 'not_running'):
            with mock.patch.dict(server.CONSENT, {'state': state, 'asking': False}):
                line = server.consent_view()['line']
            self.assertIn('Brave Browser', line, state)
            self.assertNotIn('Google Chrome', line, state)


class QuietLooksStayQuiet(test_server_browser_setup.Sandbox):
    """A Check whose tab read failed opened its page in front of the person's work and left it open. Pressing
    "Let Switchboard check links" while a quiet look ran did nothing. In the page's process, a consent check that
    could not answer still let the tab read run, which can make macOS ask without a press."""

    def test_a_check_that_cannot_read_tabs_opens_nothing(self):
        from switchboard import artifacts, browser
        opened = []
        self.patch(artifacts, 'load', lambda: {'accounts': {}, 'artifacts': [], 'order': []})
        self.patch(artifacts, 'refresh', lambda: None)
        self.patch(artifacts, 'candidates', lambda st, item: [('P1', 'a@lab.example')])
        self.patch(browser, 'profiles', lambda: {'P1': 'Work'})
        self.patch(browser, 'running', lambda: True)
        self.patch(browser, 'tabs', lambda *a, **k: None)           # the tabs can't be read now
        self.patch(browser, 'open_url', lambda *a, **k: opened.append(a))
        got = artifacts._go('https://claude.ai/artifact/AbCdEfGhIjKlMnOpQr1234', 'AbCdEfGhIjKlMnOpQr1234', True)
        self.assertEqual(opened, [])
        self.assertFalse(got['checked'])

    def test_a_press_during_a_quiet_look_still_asks_macos(self):
        import threading
        from switchboard import browser, server
        asked = []
        self.patch(browser, 'consent', lambda fresh=False: browser.WOULD_ASK)
        self.patch(browser, 'ask_consent', lambda deadline=120: asked.append(1) or browser.ALLOWED)
        self.patch(server, 'SNAPSHOT', mock.Mock())
        with mock.patch.dict(server.CONSENT, {'running': True, 'asking': False, 'state': 'would_ask'}):
            def quiet_look_ends():
                time.sleep(0.3)
                with server.CONSENT_LOCK:
                    server.CONSENT['running'] = False
            threading.Thread(target=quiet_look_ends, daemon=True).start()
            server.consent_ask()
            for _ in range(60):
                if asked:
                    break
                time.sleep(0.05)
        self.assertEqual(asked, [1])

    def test_an_unknown_answer_never_runs_the_tab_read_in_the_pages_process(self):
        from switchboard import browser
        ran = []
        self.patch(browser, 'consent', lambda fresh=False: browser.UNKNOWN)
        self.patch(browser, '_run', lambda cmd, timeout=None: ran.append(cmd) or mock.Mock(returncode=0, stdout=''))
        with mock.patch.dict(browser.PAGE_PROCESS, {'on': True}), \
                mock.patch.dict(browser.TABS, {'state': 'unknown', 'until': 0.0}):
            self.assertIsNone(browser.tabs())
            self.assertEqual(ran, [])
            self.assertEqual(browser.tabs(asked=True), [])          # a press may ask
            self.assertEqual(len(ran), 1)


class AnEmptiedLoginIsShownAndSaid(test_server_browser_setup.Sandbox):
    """When Claude Code emptied its login after Anthropic refused it (the engine's 'wiped'), the
    page showed no account in use and said nothing."""

    def test_the_account_whose_login_was_emptied_is_in_use_and_needs_a_sign_in(self):
        fake = install(self)
        a = fake.hold('ana@lab.example', '')
        fake.hold('ben@lab.example', '')
        wiped = api.Live(state='wiped', account_id=a, email='ana@lab.example', proof='label')
        self.patch(api, 'live', lambda: wiped)
        rows = engine.listing()['rows']
        row = [r for r in rows if r['email'] == 'ana@lab.example'][0]
        self.assertTrue(row['active'])
        self.assertTrue(engine.needs_sign_in(row))
        self.assertIn('refused', engine.note_of(row) or '')
        self.assertFalse([r for r in rows if r['email'] == 'ben@lab.example'][0]['active'])


if __name__ == '__main__':
    unittest.main()
