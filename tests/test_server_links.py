import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The page's server wired to links, sessions and the automatic loop: /api/go and every link form, Set up, Add to this
# profile, Check, the consent check, link rows, tab access, the banner and the note on other link apps in the state, the
# owner map after every switch, the switch notification's lines, the engine never asked under the state lock, the auto
# loop's one line. Made-up accounts, a fake Chrome and fake programs only.
import fcntl
import json
import os
import sys
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import artifacts, browser, connect, core, engine, linkapp, server
from switchboard.accounts import api
from fakes_accounts_api import install
from test_server_robust import Sandbox

A, B = 'ana@work.example', 'ana@home.example'
LINK = 'https://claude.ai/code/artifact/0f8c2d4e-6a1b-4c3d-9e8f-7a6b5c4d3e2f'


def state_lock_held():
    """True while some open file holds the page's state lock (core.locked: flock on <data>/.lock)."""
    path = core.data_path('.lock')
    if not os.path.exists(path):
        return False
    with open(path, 'a') as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
    return False


def until(test, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if test():
            return True
        time.sleep(0.02)
    return False


class Page(Sandbox):
    def setUp(self):
        super().setUp()
        self.fake = install(self)
        for d, values in ((getattr(server, 'CHECK', None), {'running': False, 'started': None, 'at': None, 'tabs': None,
                                                           'results': None, 'error': None}),
                          (getattr(server, 'DECIDE', None), {'at': None, 'decision': None}), (server.FED, {}),
                          (server.GUARD, {'at': -1e9, 'label': None}),
                          (server.LINKS, {'banner': None, 'handlers': None})):
            if d is None:                   # (a copy from before the name existed: each test then fails on its own)
                continue
            p = mock.patch.dict(d, values)
            p.start()
            self.addCleanup(p.stop)
        self.patch(artifacts, 'refresh', lambda: None)
        self.patch(artifacts, '_MAP', {'last': None, 'lock': threading.Lock()})
        keep_reset = mock.patch.object(server.keep, 'look', lambda rows, now=None, run=None: None)
        keep_reset.start()
        self.addCleanup(keep_reset.stop)

    def accounts(self):
        a = self.fake.hold(A, 'org-w', org_name='Work')
        b = self.fake.hold(B)
        self.fake.sign_in(A, 'org-w')
        return a, b

    def chrome(self, profiles, extensions=(), host=False):
        """A made-up Chrome: its Local State and its app, with the Claude extension in `extensions`, and (host) Claude
        Code's native host manifest naming a program that runs."""
        sup = os.path.join(self.home, 'Library', 'Application Support', 'Google', 'Chrome')
        os.makedirs(sup, exist_ok=True)
        with open(os.path.join(sup, 'Local State'), 'w') as f:
            json.dump({'profile': {'info_cache': dict((k, {'name': v}) for k, v in profiles.items())}}, f)
        for p in extensions:
            os.makedirs(os.path.join(sup, p, 'Extensions', browser.CLAUDE_EXTENSION, '1.0'), exist_ok=True)
        if host:
            prog = os.path.join(self.tmp, 'native-host')
            with open(prog, 'w') as f:
                f.write('#!/bin/sh\nexit 0\n')
            os.chmod(prog, 0o755)
            os.makedirs(os.path.join(sup, 'NativeMessagingHosts'), exist_ok=True)
            with open(os.path.join(sup, 'NativeMessagingHosts', browser.NATIVE_HOST + '.json'), 'w') as f:
                json.dump({'path': prog, 'allowed_origins': ['chrome-extension://%s/' % browser.CLAUDE_EXTENSION]}, f)
        os.makedirs(os.path.join(self.home, 'Applications', 'Google Chrome.app'), exist_ok=True)
        browser.APPS.clear()
        self.addCleanup(browser.APPS.clear)
        self.opened = []
        self.patch(browser, '_spawn', lambda args: self.opened.append(args))


# ---------- 1. /api/go: the link app's timing, every link form, a Link Router answered at once ----------

class Go(Page):
    def setUp(self):
        super().setUp()
        self.serve()
        self.calls = []
        self.patch(artifacts, 'go_quietly', lambda url, **timing: self.calls.append((url, timing)))
        self.patch(artifacts, 'go', lambda url, quiet=False: self.fail('a clicked link waited for its check'))

    def test_the_link_apps_timing_reaches_the_quiet_open(self):
        code, got = self.post('/api/go', {'url': LINK, 'wait': False, 'sent_at': 1000.5, 'fallback_after': 1.5})
        self.assertEqual((code, got['ok']), (200, True))
        self.assertTrue(until(lambda: self.calls))
        self.assertEqual(self.calls, [(LINK, {'sent_at': 1000.5, 'fallback_after': 1.5})])

    def test_the_old_link_router_is_answered_at_once_even_without_wait_false(self):
        code, got = self.post('/api/go', {'url': LINK}, headers={'X-Claude-Switch': '1', 'X-Switchboard': ''})
        self.assertEqual((code, got['ok']), (200, True))
        self.assertTrue(until(lambda: self.calls))
        self.assertEqual(self.calls[0][0], LINK)
        self.assertEqual(self.post('/api/check', {}, headers={'X-Claude-Switch': '1', 'X-Switchboard': ''})[0], 403)

    def test_every_form_the_link_app_routes_is_taken_and_nothing_else(self):
        for form in ('<%s>' % LINK, LINK.replace('claude.ai', 'CLAUDE.AI'),
                     'https://www.google.com/url?q=%s&sa=D' % LINK.replace(':', '%3A').replace('/', '%2F')):
            with self.subTest(form=form):
                self.calls.clear()
                self.assertEqual(self.post('/api/go', {'url': form, 'wait': False})[0], 200)
                self.assertTrue(until(lambda: self.calls))
                self.assertEqual(self.calls[0][0], LINK)
        for bad in ('https://claude.ai.evil.example/artifact/0f8c2d4e6a1b4c3d9e8f', 'http://claude.ai/artifact/x' * 2,
                    'https://claude.ai/new'):
            with self.subTest(bad=bad):
                code, got = self.post('/api/go', {'url': bad, 'wait': False})
                self.assertEqual((code, got['error']), (400, 'Paste a claude.ai artifact link.'))


# ---------- 2. Set up, Add to this profile, Check on its own thread ----------

class Routes(Page):
    def setUp(self):
        super().setUp()
        self.serve()

    def test_set_up_and_add_to_this_profile_go_to_the_links_lane(self):
        seen = []
        self.patch(artifacts, 'setup_profile', lambda email: seen.append(('setup', email)) or
                   {'profile': 'Claude work', 'profile_name': 'Claude work', 'opened': True})
        self.patch(artifacts, 'add_extension', lambda profile: seen.append(('ext', profile)) or
                   {'opened': True, 'profile': profile})
        code, got = self.post('/api/setup-profile', {'email': A.upper()})
        self.assertEqual((code, got['profile_name']), (200, 'Claude work'))
        self.assertEqual(self.post('/api/extension', {'profile': 'P1'})[1]['profile'], 'P1')
        self.assertEqual(seen, [('setup', A), ('ext', 'P1')])
        self.assertEqual(self.post('/api/setup-profile', {'email': 'not an email'})[0], 400)

    def test_check_answers_at_once_asks_macos_because_it_was_pressed_and_the_answer_comes_in_the_state(self):
        go = threading.Event()
        self.addCleanup(go.set)
        asked = []
        self.patch(browser, 'can_see_tabs', lambda ask=False: asked.append(ask) or go.wait(20) or True)
        self.patch(artifacts, 'check_profiles', lambda: [{'email': A, 'result': 'ok', 'profile': 'P1'},
                                                         {'email': B, 'result': 'trouble', 'profile': 'P2'},
                                                         {'email': 'c@x.example', 'result': 'no_profile'}])
        t0 = time.monotonic()
        code, got = self.post('/api/check')
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual((code, got.get('started')), (200, True))
        self.assertEqual(self.post('/api/check')[1].get('running'), True)          # one at a time
        self.assertTrue(self.get()[1]['check']['running'])
        go.set()
        self.assertTrue(until(lambda: not server.CHECK['running']))
        c = self.get()[1]['check']
        self.assertEqual(asked, [True])
        self.assertFalse(c['running'])
        self.assertEqual(c['line'], '1 checked · 1 no profile yet · 1 claude.ai had trouble')
        self.assertIn('claude.ai is having trouble', c['results'][1]['words'])
        self.assertIn('Set up', c['results'][2]['words'])

    def test_a_check_macos_does_not_allow_opens_nothing_and_says_so(self):
        self.patch(browser, 'can_see_tabs', lambda ask=False: False)
        self.patch(artifacts, 'check_profiles', lambda: self.fail('pages were opened'))
        self.post('/api/check')
        self.assertTrue(until(lambda: not server.CHECK['running']))
        self.assertIn('Let Switchboard check links', self.get()[1]['check']['line'])

    def test_the_consent_check_is_browser_pys_own(self):
        check = server.consent_check()
        self.assertIsNotNone(check)
        calls = []
        self.patch(browser, 'consent', lambda fresh=False: calls.append(('look', fresh)) or browser.WOULD_ASK)
        self.patch(browser, 'ask_consent', lambda deadline=120: calls.append(('ask',)) or browser.ALLOWED)
        self.assertEqual(check(ask=False), browser.WOULD_ASK)
        self.assertEqual(check(ask=True), browser.ALLOWED)
        self.assertEqual(calls, [('look', True), ('look', True), ('ask',)])


# ---------- 3. what the page draws: link rows, tab access, the banner, other link handlers ----------

class Drawn(Page):
    def test_each_account_has_its_link_row_and_claude_in_chrome_only_where_it_is_used(self):
        self.accounts()
        self.chrome({'P1': 'Work', 'P2': 'Home'})
        server.tick()
        with core.locked() as st:
            st['accounts'][A].update(profile='P1', profile_src='checked', proof={'profile': 'P1', 'at': time.time()})
        got = server.view()
        rows = dict((a['email'], a['link']) for a in got['accounts'])
        self.assertTrue(rows[A]['line'].startswith('Opens in the Google Chrome profile "Work", proved '))
        self.assertEqual(rows[B]['action']['do'], 'setup')
        self.assertTrue(rows[B]['line'].startswith(artifacts.NO_PROFILE))
        self.assertIsNone(rows[A]['chrome'])                    # no extension anywhere: no Claude in Chrome lines
        self.assertFalse(got['in_chrome'])
        self.chrome({'P1': 'Work', 'P2': 'Home'}, extensions=('P2',))
        got = server.view()
        self.assertTrue(got['in_chrome'])
        self.assertEqual(got['accounts'][0]['link']['chrome']['state'], 'no_host')   # Claude Code's way in is missing
        self.chrome({'P1': 'Work', 'P2': 'Home'}, extensions=('P2',), host=True)
        got = server.view()
        rows = dict((a['email'], a['link']) for a in got['accounts'])
        self.assertEqual(rows[A]['chrome']['action']['do'], 'add_extension')
        self.assertEqual(rows[A]['chrome']['action']['profile'], 'P1')

    def test_tab_access_is_drawn_without_asking_macos(self):
        self.patch(browser, 'consent', lambda fresh=False: self.fail('macOS was asked while the state was built'))
        self.patch(browser, 'PAGE_PROCESS', {'on': True})
        with server.CONSENT_LOCK:
            server.CONSENT['state'] = 'denied'
        tabs = server.view()['browser']['tabs']
        self.assertEqual(tabs['state'], 'denied')
        self.assertIn('System Settings', tabs['text'])

    def links(self, installed=True, on=False, wanted=None, holder='/Applications/Google Chrome.app'):
        if wanted is not None:
            core.set_setting(server.LINKS_WANTED, wanted)
        value = {'installed': installed, 'on': on, 'browser': 'Google Chrome'}
        self.patch(linkapp, 'status', lambda fresh=False: value)
        self.patch(linkapp, 'CACHE', {'at': time.time(), 'value': value, 'path': holder})
        self.patch(linkapp, 'handlers_note', lambda found=None, **k: None)
        with server.LINKS_LOCK:
            server.LINKS.update(loop=True, at=None, running=False)
        self.assertTrue(server.refresh_link_status(force=True))
        self.assertTrue(until(lambda: not server.LINKS['running']))
        return server.view()['links']

    def test_the_banner_shows_until_links_open_as_their_owner_unless_turned_off_on_purpose(self):
        # Opening as the owner is always on, so a link app built and never made the default is offered
        # too, as well as one whose seat went since, both in the page's words for who has the seat (a browser here);
        # only an off said on purpose is quiet.
        chrome = 'Google Chrome is the default browser, so links you click in other apps open there, not as their owner.'
        banner = self.links(installed=True, on=False)['banner']                     # built, never set up
        self.assertEqual(banner['action'], 'make_default')
        self.assertEqual(banner['text'], chrome)
        self.assertEqual(self.shown, [])                                           # nothing was lost: no notification
        self.assertIsNone(self.links(installed=True, on=True)['banner'])
        self.assertIs(core.settings()[server.LINKS_WANTED], True)                  # seen on: the person had it on
        banner = self.links(installed=True, on=False)['banner']
        self.assertEqual(banner['action'], 'make_default')
        self.assertEqual(banner['text'], chrome)
        self.assertEqual(len(self.shown), 1)                                       # the one notification
        self.assertEqual(self.shown[0][-1], chrome)
        self.links(installed=True, on=False)
        self.assertEqual(len(self.shown), 1)                                       # once per time it happens
        self.patch(linkapp, 'turn', lambda on: None)
        self.serve()
        self.post('/api/links', {'on': False})                                     # turned off here, on purpose
        self.assertIs(core.settings()[server.LINKS_WANTED], False)
        self.assertIsNone(self.links(installed=True, on=False)['banner'])

    def test_other_link_handlers_are_named(self):
        self.patch(linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False, 'browser': None})
        self.patch(linkapp, 'handlers_note', lambda found=None, **k: 'Other apps here can also take clicked links: X.')
        with server.LINKS_LOCK:
            server.LINKS.update(loop=True, at=None, running=False)
        server.refresh_link_status(force=True)
        self.assertTrue(until(lambda: not server.LINKS['running']))
        self.assertEqual(server.view()['links']['handlers'], 'Other apps here can also take clicked links: X.')


# ---------- 4. the owner map after every switch; the switch's own lines ----------

class OwnerMapAndNotes(Page):
    def map(self):
        return artifacts.read_owner_map()

    def test_the_map_follows_a_switch_from_the_page_and_one_the_engine_made(self):
        a, b = self.accounts()
        server.tick()
        self.assertEqual(self.map().get('active'), A)
        server.switch_to(B)
        self.assertEqual(self.map()['active'], B)                 # at once, not a tick later
        self.fake.sign_in(A, 'org-w')                             # the automatic loop moved it back
        server.DISK['low'] = True                                 # the artifact scan waits: the map still follows
        self.addCleanup(server.DISK.update, low=False)
        server.tick()
        self.assertEqual(self.map()['active'], A)
        self.assertIs(self.map()['auto'], False)
        self.fake.auto['enabled'] = True
        server.owner_map_now()
        self.assertIs(self.map()['auto'], True)

    def test_a_new_profile_reaches_the_map_at_once(self):
        self.accounts()
        self.chrome({'P1': 'Work'})
        server.tick()
        server.act('/api/profile', {'email': B, 'profile': 'P1'})
        self.assertEqual(self.map()['accounts'][B]['profile'], 'P1')

    def test_a_switch_seen_by_the_tick_says_what_its_account_lacks_by_its_email_and_only_once(self):
        self.accounts()
        self.chrome({'P1': 'Work'})
        server.tick()
        core.set_setting('notify', True)
        self.fake.sign_in(B)                                      # moved elsewhere (the automatic loop)
        server.tick()
        bodies = [c[-1] for c in self.shown if 'Switched to' in c[-2]]
        self.assertEqual(len(bodies), 1)
        self.assertIn('No Google Chrome profile is signed in as %s, so its pages won\'t open' % B, bodies[0])
        self.assertIn(artifacts.DESKTOP_NOTE, bodies[0])
        self.shown.clear()
        self.fake.sign_in(A, 'org-w')
        server.tick()
        body = [c[-1] for c in self.shown if 'Switched to' in c[-2]][0]
        self.assertNotIn(artifacts.DESKTOP_NOTE, body)            # said once

    def test_a_switch_pressed_here_says_it_under_the_switch_and_the_menu_bar_takes_nothing(self):
        self.accounts()
        server.tick()
        self.assertEqual(server.switch_to(B, source='menu'), {})
        self.assertEqual(server.switch_to(A)['note'], artifacts.DESKTOP_NOTE)
        self.assertIsNone(server.switch_to(B)['note'])


# ---------- 5. the engine is never asked under the state lock; the files that move the snapshot ----------

class NeverUnderTheLock(Page):
    def test_the_tick_asks_the_engine_and_hands_over_a_sign_in_outside_the_state_lock(self):
        self.accounts()
        held = []
        for name in ('accounts', 'live', 'readings', 'auto_settings', 'auto_status', 'add_login', 'keep_pass'):
            real = getattr(api, name)

            def watched(*a, _real=real, _name=name, **k):
                if state_lock_held():
                    held.append(_name)
                return _real(*a, **k)
            self.patch(api, name, watched)
        d = os.path.join(self.tmp, 'signin')
        os.makedirs(d)
        with open(os.path.join(d, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': {'emailAddress': 'new@lab.example'}}, f)

        class Done(object):
            pid = 0

            def poll(self):
                return 0
        self.patch(connect, '_clean', lambda d: None)
        connect.RUNNING['new'] = {'proc': Done(), 'dir': d, 'started': time.time(), 'email': '', 'profile': None}
        self.addCleanup(connect.RUNNING.clear)
        server.tick()
        self.assertIn(('add_login', d), self.fake.calls)
        self.assertEqual(held, [])
        self.assertIn('Added new@lab.example', [e['text'] for e in core.load()['events']])

    def test_turning_auto_switch_on_reads_it_back_outside_the_state_lock(self):
        held = []
        real = engine.auto_settings
        self.patch(engine, 'auto_settings', lambda: held.append(state_lock_held()) or real())
        self.patch(engine, 'foreign_switcher', lambda folder=None: None)
        self.patch(engine.HOST, 'ensure', lambda: None)
        server.act('/api/auto', {'enabled': True})
        self.assertTrue(held)
        self.assertNotIn(True, held)

    def test_the_engines_files_move_the_snapshot(self):
        before = server.files_signal()
        for name in ('accounts.json', 'usage.json', 'owners.json', 'switch-journal.json', 'auto-heartbeat.json'):
            with self.subTest(name=name):
                core.write_json(core.data_path(name), {'v': 1})
                now = server.files_signal()
                self.assertNotEqual(now, before)
                before = now

    def test_the_account_id_is_kept_with_each_account(self):
        a, b = self.accounts()
        server.tick()
        st = core.load()
        self.assertEqual((st['accounts'][A]['account_id'], st['accounts'][B]['account_id']), (a, b))


# ---------- 6. automatic switching in one line: what it waits for ----------

class AutoLine(Page):
    def line(self, **host):
        self.fake.auto['enabled'] = True
        base = {'wanted': True, 'running': True, 'on': True, 'starting': False, 'last_pass': time.time(),
                'problem': None, 'stage': 'sleeping', 'waiting_for': 'the next pass', 'restarted': None,
                'offline_since': None, 'settling': False, 'stuck_for': 0.0}
        base.update(host)
        self.patch(engine.HOST, 'status', lambda wanted=None: dict(base))
        return server.view()['auto']['line']

    def test_offline_waking_and_a_late_stage_are_said(self):
        t = time.mktime((2026, 11, 17, 17, 19, 0, 0, 0, -1))
        self.assertEqual(self.line(offline_since=t), 'No internet since 5:19 PM. Automatic switching waits for it.')
        self.assertEqual(self.line(settling=True), 'The Mac just woke. Automatic switching waits for the network.')
        self.assertEqual(self.line(stuck_for=75.0, waiting_for='the Keychain', stage='live'),
                         'Automatic switching is waiting for the Keychain (75 s so far).')
        self.assertIsNone(self.line(stuck_for=10.0, waiting_for='the Keychain'))
        self.assertIsNone(self.line(restarted='Restarted automatic switching at 10:42 (it was stuck).'))

    def test_the_policys_hold_reason_is_one_plain_line_and_decide_runs_only_in_the_loop(self):
        self.fake.decision = api.Decision('stay', target='x', kind='optional', reason='Next: x; waits: busy.',
                                          hold='Claude Code is working')
        self.assertIsNone(self.line())                            # the loop has not asked yet
        self.assertNotIn(('decide',), self.fake.calls)            # building the state never asks the policy
        server.decide_tick()
        self.assertEqual(self.line(), 'Waiting: Claude Code is working.')
        self.fake.decision = api.Decision('stay', kind='forced', reason='Every account is out. Back at 4:10 PM (x, '
                                                                         '5-hour)', hold='every account is out')
        server.decide_tick()
        self.assertEqual(self.line(), 'Every account is out. Back at 4:10 PM (x, 5-hour).')
        self.fake.auto['enabled'] = False
        self.assertIsNone(server.decide_tick())
        self.assertIsNone(server.DECIDE['decision'])

    def test_the_menu_bar_says_the_same_line(self):
        from switchboard import menubar
        auto = {'enabled': True, 'on': True, 'line': 'Waiting: Claude Code is working.', 'session_at': 90,
                'weekly_at': 95}
        self.assertEqual(menubar.auto_note(auto), 'Waiting: Claude Code is working.')
        self.assertEqual(menubar.auto_note(dict(auto, restarted='Restarted automatic switching at 10:42.')),
                         'Restarted automatic switching at 10:42.')
        self.assertEqual(menubar.auto_note(dict(auto, on=False, problem='Automatic switching stopped (exit 1).')),
                         'Automatic switching stopped (exit 1).')
        self.assertIsNone(menubar.auto_note(dict(auto, enabled=False)))


if __name__ == '__main__':
    unittest.main()
