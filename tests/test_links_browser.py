import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The browser. Tab reading asks macOS's consent API and never guesses from time; a slow, closed or
troubled Chrome; the Claude extension and Claude Code's native host per profile. A fake Chrome stands
in for osascript, pgrep, open and the consent API; nothing reaches the real browser."""
import json
import os
import stat
import time
import unittest

from fakes_links import ID1, LINK, FakeChrome, Home

from switchboard import browser


class TheConsentAPIDecides(Home):
    """Asked of macOS, four outcomes plus a hang; never 'waiting for a yes' from a timeout."""

    def test_each_outcome_of_the_consent_api_is_its_own_state_and_words(self):
        chrome = FakeChrome(self, {'P1': 'Work'})
        for code, state, shown, words in ((0, 'allowed', 'ok', 'can read'), (-1743, 'denied', 'denied', 'System Settings'),
                                          (-1744, 'would_ask', 'would_ask', 'Let Switchboard check links'),
                                          (-600, 'not_running', 'not_running', "isn't open"),
                                          (-50, 'unknown', 'unknown', "couldn't tell")):
            with self.subTest(code=code):
                chrome.consent = code
                browser.CONSENT.update(state=None, at=-1e9)
                browser.TABS.update(state=None)
                self.assertEqual(browser.consent(), state)
                access = browser.tab_access()
                self.assertEqual(access['state'], shown)
                self.assertIn(words, access['text'])
                self.assertNotIn('waiting for a yes', access['text'])
        browser.TABS.update(state='ok')                          # a look that worked, then macOS said no
        chrome.consent = -1743
        browser.CONSENT.update(state=None, at=-1e9)
        self.assertEqual(browser.tab_access()['state'], 'denied')
        self.assertTrue(all(ask is False for _, ask in chrome.consent_calls), 'a plain check never asks')
        self.assertEqual({b for b, _ in chrome.consent_calls}, {'com.google.Chrome'})

    def test_a_consent_check_that_hangs_is_not_known_within_its_deadline(self):
        FakeChrome(self, {'P1': 'Work'}).consent = lambda bundle, ask: time.sleep(2) or 0
        self.patch(browser, 'CONSENT_DEADLINE', 0.2)
        began = time.monotonic()
        self.assertEqual(browser.consent(fresh=True), 'unknown')
        self.assertLess(time.monotonic() - began, 1.5)

    def test_no_consent_api_at_all_is_not_known_and_tabs_wait_for_a_press(self):
        chrome = FakeChrome(self, {'P1': 'Work'})

        def missing(bundle, ask):
            raise OSError('no CoreServices here')
        chrome.consent = missing
        self.assertEqual(browser.consent(fresh=True), 'unknown')
        self.assertIsNone(browser.tabs())               # not known: the read itself could make macOS ask
        self.assertEqual(chrome.osa_calls, 0)
        self.assertEqual(browser.tabs(asked=True), [])  # a press reads (osascript decides), nothing open
        self.assertEqual(chrome.osa_calls, 1)
        self.assertEqual(browser.tabs(), [])            # once a read has worked, the quiet reads go on
        self.assertEqual(chrome.osa_calls, 2)

    def test_denied_and_would_ask_never_run_osascript_and_would_ask_never_prompts(self):
        chrome = FakeChrome(self, {'P1': 'Work'})
        for code, state in ((-1743, 'denied'), (-1744, 'would_ask')):
            chrome.consent = code
            browser.CONSENT.update(state=None, at=-1e9)
            self.assertIsNone(browser.tabs())
            self.assertEqual(browser.TABS['state'], state)
        self.assertEqual(chrome.osa_calls, 0)

    def test_only_a_button_asks_macos(self):
        chrome = FakeChrome(self, {'P1': 'Work'})
        chrome.consent = lambda bundle, ask: 0 if ask else -1744
        self.assertIs(browser.can_see_tabs(), False)              # the page's own look: no question
        self.assertEqual([a for _, a in chrome.consent_calls], [False])
        self.assertIs(browser.can_see_tabs(ask=True), True)        # "Let Switchboard check links"
        self.assertIn(True, [a for _, a in chrome.consent_calls])

    def test_chrome_not_running_means_nobody_asked(self):
        FakeChrome(self, {'P1': 'Work'}, running=False)
        self.assertIsNone(browser.can_see_tabs())


class SlowIsNotBlind(Home):
    """An osascript that takes 9 s at load 200 is 'slow' and backs off 30 s doubling to 5 minutes, never
    the old 10 minutes of "macOS is waiting for a yes"."""

    def test_a_slow_browser_backs_off_and_comes_back(self):
        chrome = FakeChrome(self, {'P1': 'Work'})
        chrome.load, chrome.osa = 20.0, 'hang'                # load per core 20: the Mac is very busy
        self.assertEqual(browser.tab_timeout(), 20)
        waits = []
        for _ in range(6):
            self.assertIsNone(browser.tabs())
            self.assertEqual(browser.TABS['state'], 'slow')
            waits.append(browser.TABS['until'] - chrome.t)
            self.assertIsNone(browser.tabs())                 # inside the back-off nothing runs
            chrome.t = browser.TABS['until'] + 0.01
        self.assertEqual([round(w) for w in waits], [30, 60, 120, 240, 300, 300])
        self.assertIn('slow to answer', browser.tab_access()['text'])
        self.assertNotIn('waiting for a yes', browser.tab_access()['text'])
        chrome.osa = 'ok'
        self.assertEqual(browser.tabs(), [])
        self.assertEqual((browser.TABS['state'], browser.TABS['backoff']), ('ok', 0.0))

    def test_the_timeout_grows_with_load_and_is_never_a_verdict(self):
        chrome = FakeChrome(self, {'P1': 'Work'})
        for load, secs in ((0.5, 8), (2.0, 12), (21.3, 20)):
            chrome.load = load
            self.assertEqual(browser.tab_timeout(), secs)

    def test_osascript_saying_no_is_denied_and_remembered(self):
        chrome = FakeChrome(self, {'P1': 'Work'})
        chrome.osa = 'denied'
        self.assertIsNone(browser.tabs())
        self.assertEqual((browser.TABS['state'], browser.CONSENT['state']), ('denied', 'denied'))
        self.assertIsNone(browser.tabs())
        self.assertEqual(chrome.osa_calls, 1)                 # the next look trusts the remembered answer

    def test_a_busy_browser_is_asked_again(self):
        chrome = FakeChrome(self, {'P1': 'Work'})
        chrome.osa = 'busy'
        self.assertIsNone(browser.tabs(tries=3))
        self.assertEqual(chrome.osa_calls, 3)


class TheScriptNeverLaunchesChrome(Home):
    def test_the_tab_script_runs_only_while_the_browser_runs_and_stops_at_200_tabs(self):
        script = browser.TABS_OSA % ('Google Chrome', 'Google Chrome', browser.MAX_TABS)
        self.assertTrue(script.splitlines()[2].startswith('if application "Google Chrome" is running then'))
        self.assertIn('if n is greater than or equal to 200 then return out', script)
        self.assertTrue(script.rstrip().endswith('return out'))
        self.assertEqual(script.count('tell application'), 1)

    def test_closing_a_tab_never_launches_it_either(self):
        chrome = FakeChrome(self, {'P1': 'Work'})
        seen = []
        real = chrome.run
        self.patch(browser, '_run', lambda cmd, timeout: seen.append(cmd) or real(cmd, timeout))
        browser.close_tab('7', '101')
        self.assertIn('is running then tell application', seen[-1][2])
        browser.close_tab('7; do shell script "x"', '1')      # not a number: nothing is run
        self.assertEqual(len(seen), 1)


class WatchingATab(Home):
    """Chrome closed, slow, a security check, an outage; only the opened tab is looked at and closed."""

    def open_and_watch(self, chrome, profile='P1', timeout=15):
        before = {row[1] for row in browser.tabs() or []}
        browser.open_url(profile, LINK % ID1)
        return browser.watch_tab(ID1, before, timeout=timeout)

    def test_a_real_page_must_say_its_title_twice(self):
        chrome = FakeChrome(self, {'P1': 'Work'}, logins={'P1': 'x'}, owners={ID1: 'x'}, titles={ID1: 'Budget'})
        verdict, tab = self.open_and_watch(chrome)
        self.assertEqual((verdict, tab[3]), ('ok', 'Budget – Claude'))
        self.assertEqual(chrome.opened, [('P1', LINK % ID1, False)])

    def test_wrong_account_and_signed_out_are_unmistakable(self):
        chrome = FakeChrome(self, {'P1': 'Work', 'P2': 'Home'}, logins={'P1': 'y', 'P2': None}, owners={ID1: 'x'})
        self.assertEqual(self.open_and_watch(chrome, 'P1')[0], 'wrong')
        self.assertEqual(self.open_and_watch(chrome, 'P2')[0], 'signin')

    def test_a_security_check_is_waited_out(self):
        chrome = FakeChrome(self, {'P1': 'Work'}, logins={'P1': 'x'}, owners={ID1: 'x'}, titles={ID1: 'Budget'})
        chrome.hold = 6.0
        verdict, tab = self.open_and_watch(chrome)
        self.assertEqual((verdict, tab[3]), ('ok', 'Budget – Claude'))

    def test_claude_ai_s_own_outage_is_trouble_never_the_wrong_account(self):
        chrome = FakeChrome(self, {'P1': 'Work'}, logins={'P1': 'x'}, owners={ID1: 'x'})
        chrome.outage = True
        verdict, tab = self.open_and_watch(chrome)
        self.assertEqual(verdict, 'trouble')
        self.assertTrue(browser.trouble('Something went wrong'))
        self.assertFalse(browser.trouble('Something went wrong with my budget plan for the next ten quarters and more'))
        self.assertFalse(browser.trouble('Budget – Claude'))

    def test_a_browser_that_never_shows_the_tab_settles_nothing(self):
        chrome = FakeChrome(self, {'P1': 'Work'}, logins={'P1': 'x'}, owners={ID1: 'x'})
        chrome.delay = 100.0
        began = chrome.t
        self.assertEqual(self.open_and_watch(chrome, timeout=15), (None, None))
        self.assertAlmostEqual(chrome.t - began, 15, delta=1)

    def test_a_tab_that_was_there_before_is_never_taken_for_the_new_one(self):
        chrome = FakeChrome(self, {'P1': 'Work'}, logins={'P1': 'x'}, owners={ID1: 'x'}, titles={ID1: 'Old'})
        browser.open_url('P1', LINK % ID1)
        chrome.t += 5
        old = browser.tabs()[0]
        chrome.logins['P1'] = 'y'                          # the profile is now someone else
        before = {row[1] for row in browser.tabs()}
        browser.open_url('P1', LINK % ID1)
        verdict, tab = browser.watch_tab(ID1, before)
        self.assertEqual(verdict, 'wrong')
        self.assertNotEqual(tab[1], old[1])


class OpeningInAProfile(Home):
    def test_only_a_plain_profile_name_is_passed_on(self):
        chrome = FakeChrome(self, {'P1': 'Work'})
        browser.open_url('Profile 4', 'https://claude.ai/login')
        browser.open_url('../Default', 'https://claude.ai/login', background=True)      # a path, not a profile name
        browser.open_url('Work\n--incognito', 'https://claude.ai/login')
        self.assertEqual(chrome.opened, [('Profile 4', 'https://claude.ai/login', False),
                                         (None, 'https://claude.ai/login', True), (None, 'https://claude.ai/login', False)])


class TheExtensionAndTheNativeHost(Home):
    """Which profiles have the Claude extension, and whether Claude Code's way into Chrome is there."""

    def test_extension_folders_in_one_two_and_five_profiles(self):
        five = dict(('P%d' % i, 'Profile %d' % i) for i in range(1, 6))
        for with_it in (('P1',), ('P1', 'P3'), tuple(five)):
            with self.subTest(n=len(with_it)):
                self.reset_caches()
                FakeChrome(self, five, extensions=with_it)
                got = browser.extensions()
                self.assertEqual(sorted(p for p, v in got.items() if v), sorted(with_it))
                self.assertEqual(sorted(got), sorted(five))
                versions = browser.extension_versions()
                self.assertEqual(versions[with_it[0]], '1.2.3_0')
                self.assertTrue(all(versions[p] is None for p in five if p not in with_it))
                import shutil
                shutil.rmtree(os.path.join(browser.support(), 'Google'))

    def write_host(self, program, origins=None):
        d = os.path.join(browser.data_dir(), 'NativeMessagingHosts')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, browser.NATIVE_HOST + '.json'), 'w') as f:
            json.dump({'name': browser.NATIVE_HOST, 'path': program, 'type': 'stdio',
                       'allowed_origins': origins or ['chrome-extension://%s/' % browser.CLAUDE_EXTENSION]}, f)

    def test_a_native_host_that_points_at_a_missing_program_is_not_ok(self):
        FakeChrome(self, {'P1': 'Work'})
        self.assertEqual(browser.native_host()['ok'], False)               # no manifest at all
        prog = os.path.join(self.tmp, 'chrome-native-host')
        self.write_host(prog)
        host = browser.native_host()
        self.assertEqual((host['ok'], host['names_extension'], host['program']), (False, True, prog))
        with open(prog, 'w') as f:
            f.write('#!/bin/sh\n')
        os.chmod(prog, stat.S_IRWXU)
        self.assertTrue(browser.native_host()['ok'])
        self.write_host(prog, origins=['chrome-extension://someoneelse/'])
        self.assertFalse(browser.native_host()['names_extension'])


class TheRealPlumbing(Home):
    """The default _run and _spawn, with stand-in programs (never osascript or open themselves)."""

    def script(self, name, body):
        path = os.path.join(self.tmp, name)
        with open(path, 'w') as f:
            f.write('#!/bin/sh\n' + body)
        os.chmod(path, 0o755)
        return path

    def test_open_gets_exactly_these_words(self):
        log = os.path.join(self.tmp, 'open.log')
        fake = self.script('fake-open', 'for a in "$@"; do printf "%%s\\n" "$a" >> "%s"; done\n' % log)
        self.patch(browser, 'OPEN', fake)
        browser.open_url('Claude gmail', 'https://claude.ai/login', background=True)
        for _ in range(200):
            if os.path.exists(log):
                with open(log) as f:
                    if 'claude.ai/login' in f.read():
                        break
            time.sleep(0.05)
        with open(log) as f:
            self.assertEqual(f.read().splitlines(), ['-gna', 'Google Chrome', '--args', '--profile-directory=Claude gmail',
                                                     'https://claude.ai/login'])

    def test_tabs_through_a_stand_in_osascript(self):
        fake = self.script('fake-osascript', 'printf "1\\t11\\thttps://claude.ai/artifact/%s\\tBudget – Claude\\n"\n' % ID1)
        self.patch(browser, 'OSASCRIPT', fake)
        self.patch(browser, 'CONSENT_CALL', lambda bundle, ask: 0)
        self.assertEqual(browser.tabs(), [('1', '11', LINK % ID1, 'Budget – Claude')])

    def test_a_stand_in_that_hangs_is_slow_after_its_timeout(self):
        fake = self.script('fake-osascript', 'sleep 5\n')
        self.patch(browser, 'OSASCRIPT', fake)
        self.patch(browser, 'CONSENT_CALL', lambda bundle, ask: 0)
        self.patch(browser, 'tab_timeout', lambda: 0.5)
        began = time.monotonic()
        self.assertIsNone(browser.tabs())
        self.assertLess(time.monotonic() - began, 4)
        self.assertEqual(browser.TABS['state'], 'slow')

    def test_running_asks_pgrep_by_the_exact_name(self):
        fake = self.script('fake-pgrep', '[ "$1" = -x ] && [ "$2" = "Google Chrome" ]\n')
        self.patch(browser, 'PGREP', fake)
        self.assertTrue(browser.running())
        self.patch(browser, 'PGREP', os.path.join(self.tmp, 'missing'))
        self.assertFalse(browser.running())


class TheConsentBinding(unittest.TestCase):
    """The ctypes call itself, once, for an app that does not exist: macOS answers "not running" (-600) at once and
    can never show a question (prompting is off and there is no such app). Skipped where CoreServices is missing."""

    def test_the_binding_answers_for_an_app_that_is_not_there(self):
        if not os.path.exists('/System/Library/Frameworks/CoreServices.framework'):
            self.skipTest('no CoreServices')
        code = browser._consent_code('io.github.amyjainberkeley.switchboard.b4-test-no-such-app', False, 5)
        self.assertIn(code, (-600, None))                  # None only when a very busy Mac took over 5 s
        if code is not None:
            self.assertEqual(browser.CODES[code], 'not_running')


if __name__ == '__main__':
    unittest.main()
