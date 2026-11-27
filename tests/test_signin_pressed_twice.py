"""A second press of Sign in never throws a sign-in away, and a finished sign-in is kept within seconds.

What happened on a real Mac (Mon Nov 23 2026, 22:16): Sign in pressed on a row; the link opened in the account's
browser profile; the person signed in to claude.ai there and approved it; 17 seconds after the press the browser said
"Sign in successful". Back on the page the row still said "Sign in": a finished sign-in was handed to the engine only at
the next reading, a minute away (up to four on a busy Mac). So the person pressed Sign in again, 5 seconds after the
success. That second press stopped "the sign-in under way" before starting a new one: it deleted the finished
sign-in's Keychain item and folder, the login that had just been made. The new sign-in was never approved and timed
out 15 minutes later.

Each test here failed on the build that Mac ran, or on the first form of the fix (what its review found):

- a second press after the browser approved it keeps that sign-in and starts no other;
- a second press while it still waits opens the same link again (one listener, so whichever tab is finished counts),
  in the profile it is in whatever the press named, and a double click is one link;
- two presses at the same moment make one sign-in, never two listeners with one of them forgotten;
- only a sign-in that failed is replaced;
- Cancel a moment after it finished leaves it to be kept; Cancel while it waits, long after it finished (the way out
  if the engine never comes), or while it is kept for another try, still cancels;
- pressing again while it is kept for another try makes that try due;
- the page's loop is woken when a sign-in's process ends, and the watcher stops once the sign-in is over;
- the 15 minutes count from the last press;
- a Cancel and a new sign-in that come while the engine reads the old one leave the new one alone;
- the reading that keeps a sign-in leaves its row signed in (the rows are read after the engine has the login);
- through the page's own loop, with its next reading an hour away, a sign-in is kept within seconds of the browser's
  yes;
- the page's route opens the same new profile again, and says "Opened the sign-in" only when a page was opened;
- a restart: a sign-in the browser approved just before it, or during it, is handed to the engine at the next start;
  one that was handed over, cancelled, or is old, is cleared and never handed over again; one still waiting is
  stopped, as before;
- the page is told when a sign-in has ended, and is sent with the name of its own file, which the state names too, so
  an open tab can tell that an update replaced it.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import sys
import subprocess
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import browser, connect, core, server
from switchboard.accounts import api
from fakes_accounts_api import install
import test_keep  # its Sandbox: the page's own loop made safe to run
import test_server_browser_setup as sandbox  # its Sandbox: a throwaway home, data folder and Applications folder

A, B = 'ana@work.example', 'bo@home.example'
LINK = 'https://claude.ai/oauth/authorize?state=%d'
CLEAN = connect._clean                      # the real one: most tests here only note what would be cleared
OVER, ASKED = getattr(connect, 'OVER', 'over'), getattr(connect, 'ASKED', 'asked.json')
KEPT_FROM_CANCEL = getattr(connect, 'KEPT_FROM_CANCEL', 120)
TAKEN_UP_FOR = getattr(connect, 'TAKEN_UP_FOR', 1800)


class Login(object):
    """Stands in for `claude auth login`: it hands over its link at once and then waits, as the real one does, until
    the test says the browser approved it (approve) or that it failed (fail)."""

    def __init__(self, test, args, env):
        self.args, self.dir, self.rc = args, env['CLAUDE_CONFIG_DIR'], None
        test.made.append(self)
        self.pid = 900000 + len(test.made)
        self.link = LINK % len(test.made)
        gate = test.gate
        if gate is not None:                    # two starts at the same moment: both are let in together, if both come
            try:
                gate.wait()
            except threading.BrokenBarrierError:
                pass
        with open(env['SWITCHBOARD_URL_FILE'], 'w') as f:
            f.write(self.link)

    def poll(self):
        return self.rc

    def wait(self, timeout=None):               # (the first form of the fix waited here; nothing does now)
        while self.rc is None:
            time.sleep(0.01)
        return self.rc

    def approve(self, email):
        """The browser approved it: Claude Code left the new login in this sign-in's folder, and its process ended."""
        with open(os.path.join(self.dir, '.claude.json'), 'w') as f:
            json.dump({'oauthAccount': {'emailAddress': email}}, f)
        self.rc = 0

    def fail(self):
        self.rc = 1


class SignIns(sandbox.Sandbox):
    """Sign-ins started the real way (connect.start), with a stand-in for Claude Code's own sign-in command, for the
    engine (it keeps what a folder holds), for the Keychain clean-up and for the browser."""

    def setUp(self):
        super().setUp()
        self.addCleanup(connect.RUNNING.clear)
        self.addCleanup(connect.NOTES.clear)
        self.made, self.opened, self.cleaned, self.killed, self.woken, self.gate = [], [], [], [], [], None
        self.fake = install(self)
        self.wake_of_the_server = getattr(connect, 'ON_END', None)
        self.patch(connect, 'ON_END', lambda: self.woken.append(time.time()))
        self.patch(connect, 'WATCH_EVERY', 0.01)
        self.patch(connect, 'claude_bin', lambda: '/made-up/claude')
        self.patch(connect, '_clean', lambda d: self.cleaned.append(d) or True)       # it would ask the Keychain
        self.patch(connect.os, 'killpg', lambda pid, sig: self.killed.append(pid))
        self.patch(browser, 'open_url', lambda p, u, background=False: self.opened.append((p, u)))
        self.patch(connect.subprocess, 'Popen', lambda args, env=None, **kw: Login(self, args, env))
        self.browser_data('chrome', {'P1': 'Work', 'P2': 'Home'})
        self.app('chrome')
        self.addCleanup(self.end_watchers)      # last in, so first out: while this test's stand-ins are still in place

    def end_watchers(self):
        """Every sign-in of this test ends, and the threads that watch them with it: none wakes a later test."""
        for x in self.made:
            if x.rc is None:
                x.fail()
        for _ in range(500):
            if not self.watchers():
                break
            time.sleep(0.01)

    def state(self, *here):
        """A page state where these accounts are here; the last one's saved login has stopped working."""
        st = core.blank_state()
        for i, email in enumerate(here):
            core.ensure_account(st, email).update(connected=True, slot=i + 1, needs_login=email == here[-1])
        return st

    def texts(self, st):
        return [e['text'] for e in st['events']]

    def long_ago(self, key, seconds=60):
        """The link of this sign-in was opened `seconds` ago (a second press now is a press, not a double click)."""
        c = connect.RUNNING[key]
        c['pressed'] = c.get('pressed', time.time()) - seconds

    def woken_soon(self, more_than=0):
        for _ in range(500):
            if len(self.woken) > more_than:
                return True
            time.sleep(0.01)
        return False

    def watchers(self):
        return [t for t in threading.enumerate() if t.name == 'sign-in-ended']


class ASecondPressNeverThrowsASignInAway(SignIns):
    def test_after_the_browser_approved_it_that_sign_in_is_kept_and_no_other_is_started(self):
        key = connect.start(B, 'P1')
        self.made[0].approve(B)                                 # "Sign in successful" in the browser
        self.woken_soon()
        woken = len(self.woken)
        again = connect.start(B, 'P1')                          # the row still said "Sign in": pressed again
        self.assertEqual(self.cleaned, [])                      # its Keychain item and folder stay
        self.assertEqual(self.killed, [])
        self.assertEqual(len(self.made), 1)                     # no second sign-in
        self.assertEqual(self.opened, [('P1', LINK % 1)])       # and no second page
        self.assertEqual((again, again.did), (key, 'kept'))
        self.assertGreaterEqual(len(self.woken), woken + 1)     # the engine is asked now
        st = self.state(A, B)
        connect.finish(st)
        self.assertIn(('add_login', self.made[0].dir), self.fake.calls)
        self.assertIn('Added %s' % B, self.texts(st))
        self.assertFalse(st['accounts'][B]['needs_login'])
        self.assertEqual(self.cleaned, [self.made[0].dir])      # cleared only once the engine has the login

    def test_while_it_still_waits_the_same_link_opens_again(self):
        key = connect.start(B, 'P1')
        self.long_ago(B)
        again = connect.start(B, 'P1')
        self.assertEqual(len(self.made), 1)                     # one listener
        self.assertEqual((self.cleaned, self.killed), ([], []))
        self.assertEqual(self.opened, [('P1', LINK % 1), ('P1', LINK % 1)])
        self.assertEqual((again, again.did), (key, 'again'))
        self.made[0].approve(B)                                 # whichever tab was finished: the one listener has it
        st = self.state(A, B)
        connect.finish(st)
        self.assertIn('Added %s' % B, self.texts(st))

    def test_while_it_waits_a_press_that_names_another_profile_opens_the_one_it_is_in(self):
        connect.start(B, 'P1')
        self.long_ago(B)
        again = connect.start(B, 'P2')                          # the first tab, in P1, may be the one approved
        self.assertEqual(self.opened, [('P1', LINK % 1), ('P1', LINK % 1)])
        self.assertEqual((again.did, again.profile), ('again', 'P1'))
        self.made[0].approve(B)
        st = self.state(A, B)
        connect.finish(st)
        self.assertEqual(st['accounts'][B]['profile'], 'P1')    # the profile it was approved in, not the one named

    def test_a_double_click_is_one_link(self):
        self.patch(connect, 'AGAIN_AFTER', 60)                  # (however slow this Mac is between the two)
        connect.start(B, 'P1')
        again = connect.start(B, 'P1')
        self.assertEqual(len(self.made), 1)
        self.assertEqual(self.opened, [('P1', LINK % 1)])
        self.assertEqual((self.cleaned, self.killed), ([], []))
        self.assertEqual(again.did, 'same')

    def test_two_presses_at_the_same_moment_make_one_sign_in(self):
        self.patch(connect, 'AGAIN_AFTER', 60)
        self.gate = threading.Barrier(2, timeout=1.5)
        keys = []
        both = [threading.Thread(target=lambda: keys.append(connect.start(B, 'P1'))) for _ in range(2)]
        for t in both:
            t.start()
        for t in both:
            t.join(30)
        self.assertEqual(len(keys), 2)
        self.assertEqual(len(self.made), 1)                     # never two listeners, one of them forgotten
        self.assertEqual(self.opened, [('P1', LINK % 1)])
        self.assertIs(connect.RUNNING[B]['proc'], self.made[0])

    def test_only_a_sign_in_that_failed_is_replaced(self):
        connect.start(B, 'P1')
        self.made[0].fail()
        again = connect.start(B, 'P1')
        self.assertEqual(len(self.made), 2)
        self.assertEqual(self.cleaned, [self.made[0].dir])
        self.assertIs(connect.RUNNING[B]['proc'], self.made[1])
        self.assertEqual(self.opened, [('P1', LINK % 1), ('P1', LINK % 2)])
        self.assertEqual(again.did, 'opened')

    def test_the_profile_it_is_in_is_known_while_it_is_under_way(self):
        self.assertIsNone(connect.profile_of(B))
        connect.start(B, 'P1')
        self.assertEqual(connect.profile_of(B), 'P1')
        self.made[0].approve(B)
        self.assertEqual(connect.profile_of(B), 'P1')
        connect.finish(self.state(A, B))
        self.assertIsNone(connect.profile_of(B))

    def test_the_page_is_told_when_one_has_ended(self):
        connect.start(B, 'P1')
        st = self.state(A, B)
        self.assertEqual([(c['email'], c.get('ended'), c['keeping']) for c in connect.view(st)], [(B, False, False)])
        self.made[0].approve(B)
        self.assertEqual([(c['email'], c.get('ended'), c['keeping']) for c in connect.view(st)], [(B, True, False)])


class Cancel(SignIns):
    def test_a_moment_after_it_finished_leaves_it_to_be_kept(self):
        key = connect.start(B, 'P1')
        self.made[0].approve(B)
        self.assertIsNone(connect.stop(key))                    # "Cancel" on the line that still said waiting
        self.assertEqual((self.cleaned, self.killed), ([], []))
        self.assertIn(B, connect.RUNNING)
        st = self.state(A, B)
        connect.finish(st)
        self.assertIn('Added %s' % B, self.texts(st))

    def test_long_after_it_finished_cancels_it(self):
        key = connect.start(B, 'P1')
        self.made[0].approve(B)
        self.woken_soon()
        connect.RUNNING[B]['ended_at'] = time.time() - KEPT_FROM_CANCEL - 5     # and the engine never came
        self.assertIsNotNone(connect.stop(key))                 # the way out
        self.assertEqual(self.cleaned, [self.made[0].dir])
        self.assertNotIn(B, connect.RUNNING)

    def test_while_it_waits_stops_it(self):
        key = connect.start(B, 'P1')
        self.assertIsNotNone(connect.stop(key))
        self.assertEqual(self.killed, [self.made[0].pid])
        self.assertEqual(self.cleaned, [self.made[0].dir])
        self.assertNotIn(B, connect.RUNNING)

    def test_of_one_kept_for_another_try_still_cancels_and_a_press_makes_that_try_due(self):
        key = connect.start(B, 'P1')
        self.made[0].approve(B)
        self.fake.fail['add_login'] = api.AccountsError('network', 'No answer from Anthropic. Try again in a minute.')
        st = self.state(A, B)
        connect.finish(st)                                      # kept for another try in 30 seconds
        self.assertTrue(connect.RUNNING[B].get('retry_at'))
        del self.fake.fail['add_login']
        again = connect.start(B, 'P1')                          # pressed again: that try is now, no new sign-in
        self.assertEqual((again.did, len(self.made), self.cleaned), ('kept', 1, []))
        self.assertLessEqual(connect.RUNNING[B]['retry_at'], time.time())      # due now
        self.assertTrue(connect.view(st)[0]['keeping'])         # and the page still says it is being kept
        self.assertIsNotNone(connect.stop(key))                 # its Cancel is the way out of the wait
        self.assertEqual(self.cleaned, [self.made[0].dir])


class KeptWithinSeconds(SignIns):
    def test_the_loop_is_woken_when_a_sign_in_ends(self):
        connect.start(B, 'P1')
        time.sleep(0.1)
        self.assertEqual(self.woken, [])                        # nothing while it waits for the browser
        self.made[0].approve(B)
        self.assertTrue(self.woken_soon())

    def test_the_watcher_stops_once_the_sign_in_is_over(self):
        key = connect.start(B, 'P1')
        self.assertEqual(len(self.watchers()), 1)
        connect.stop(key)                                       # cancelled: nothing more to watch
        for _ in range(300):
            if not self.watchers():
                break
            time.sleep(0.01)
        self.assertEqual(self.watchers(), [])
        self.assertEqual(self.woken, [])

    def test_the_server_gives_its_own_wake(self):
        self.assertEqual(self.wake_of_the_server, server.WAKE.set)

    def test_the_fifteen_minutes_count_from_the_last_press(self):
        connect.start(B, 'P1')
        c = connect.RUNNING[B]
        limit = getattr(connect, 'TIMES_OUT', 900)
        c['started'] -= limit + 100                             # started long ago,
        c['pressed'] = time.time() - 10                         # its link opened again ten seconds ago
        self.assertEqual(connect.collect(), [])
        self.assertIn(B, connect.RUNNING)
        c['pressed'] -= limit + 100
        done = connect.collect()
        self.assertEqual([d.get('timed_out') for d in done], [True])
        self.assertEqual(self.killed, [self.made[0].pid])

    def test_a_cancel_and_a_new_sign_in_while_the_engine_reads_leave_the_new_one_alone(self):
        connect.start(B, 'P1')
        self.made[0].approve(B)
        newer = {'proc': Login.__new__(Login), 'dir': os.path.join(self.tmp, 'newer'), 'started': time.time(),
                 'email': B, 'profile': 'P1'}
        newer['proc'].rc = None

        def reads(c):                                           # the engine is reading the old one's login when...
            connect.RUNNING[B] = newer                          # ...a Cancel took it and Sign in started another
            raise core.SwitchError('That sign-in left no login behind. Try again.')
        self.patch(connect, '_engine_part', reads)
        connect.collect()
        self.assertIs(connect.RUNNING.get(B), newer)            # not dropped unstopped, with its listener forgotten
        self.assertEqual(self.cleaned, [])                      # and the old one's folder is not cleared twice


class WithARealProcess(sandbox.Sandbox):
    """The same with a real process in place of Claude Code's sign-in command (a shell script that hands over a link,
    waits until the test lets it go and ends well): the watcher, the wake and the second press on the real thing."""

    def setUp(self):
        super().setUp()
        self.addCleanup(connect.RUNNING.clear)
        self.addCleanup(connect.NOTES.clear)
        self.opened, self.cleaned, self.woken = [], [], []
        install(self)
        self.go = os.path.join(self.tmp, 'go')
        script = os.path.join(self.tmp, 'stand-in-for-the-sign-in')     # (never a file called claude: _isolate)
        with open(script, 'w') as f:
            f.write('#!/bin/sh\necho "%s" > "$SWITCHBOARD_URL_FILE"\n'
                    'n=0; while [ ! -e "%s" ] && [ $n -lt 1200 ]; do sleep 0.05; n=$((n+1)); done\nexit 0\n'
                    % (LINK % 7, self.go))
        os.chmod(script, 0o700)
        self.patch(connect, 'claude_bin', lambda: script)
        self.patch(connect, 'ON_END', lambda: self.woken.append(time.time()))
        self.patch(connect, '_clean', lambda d: self.cleaned.append(d) or True)
        self.patch(browser, 'open_url', lambda p, u, background=False: self.opened.append((p, u)))
        self.browser_data('chrome', {'P1': 'Work'})
        self.app('chrome')
        self.addCleanup(self.end)               # last in, so first out

    def end(self):
        """Whatever happened, the script ends, and the thread that watches it with it."""
        open(self.go, 'w').close()
        for _ in range(500):
            if not [t for t in threading.enumerate() if t.name == 'sign-in-ended']:
                break
            time.sleep(0.01)

    def test_it_ends_the_loop_is_woken_and_a_second_press_keeps_it(self):
        key = connect.start(B, 'P1')
        self.assertEqual(self.opened, [('P1', LINK % 7)])
        self.assertEqual(connect.under_way(connect.RUNNING[B]), 'waiting')
        self.assertEqual(self.woken, [])
        open(self.go, 'w').close()                              # the browser approved it: the process ends well
        for _ in range(3000):
            if self.woken:
                break
            time.sleep(0.01)
        self.assertEqual(len(self.woken), 1)                    # woken once, when the process ended
        self.assertEqual(connect.under_way(connect.RUNNING[B]), 'finished')
        again = connect.start(B, 'P1')
        self.assertEqual((again, again.did), (key, 'kept'))
        self.assertEqual((self.cleaned, self.opened), ([], [('P1', LINK % 7)]))
        self.assertEqual(len(self.woken), 2)


class TheRowAfterTheReadingThatKeptIt(SignIns):
    """The reading (server.tick) that hands a finished sign-in to the engine reads the accounts after it, so the row
    is signed in at once. Read before it, the rows still said "needs a sign-in" and put that back on the row."""

    def test_its_row_no_longer_asks_for_a_sign_in(self):
        self.fake.hold(A)
        self.fake.sign_in(A)
        b = self.fake.hold(B, status='needs_login')
        keeps = api.add_login                                   # the fake's: it notes the call and names the account

        def add_login(config_dir):                              # as the engine does: the account has a login again
            got = keeps(config_dir)
            self.fake.by_id[got.id]['status'] = 'ok'
            return got
        self.patch(api, 'add_login', add_login)
        server.tick()
        self.assertTrue(core.load()['accounts'][B]['needs_login'])          # before: its saved login had stopped
        connect.start(B, 'P1')
        self.made[0].approve(B)
        server.tick()
        st = core.load()
        self.assertEqual(self.fake.by_id[b]['status'], 'ok')
        self.assertIn('Added %s' % B, self.texts(st))
        self.assertFalse(st['accounts'][B]['needs_login'])
        self.assertEqual(st['accounts'][B]['status'], 'ok')


class ThroughThePagesOwnLoop(test_keep.Sandbox):
    """server.loop itself, its next reading an hour away and 30 seconds between two passes: a sign-in the browser
    approves is kept within seconds, because its end wakes the loop (the server's own wake, nothing stood in for it),
    and its row is signed in by that same reading. Before, it waited for the next reading."""

    def setUp(self):
        super().setUp()
        self.addCleanup(connect.RUNNING.clear)
        self.addCleanup(connect.NOTES.clear)
        self.made, self.gate, self.opened = [], None, []
        real = subprocess.Popen

        def popen(args, env=None, **kw):
            if list(args[1:3]) == ['auth', 'login']:
                return Login(self, args, env)
            if args[0] == '/usr/bin/open':                      # the sign-in page, in the default browser
                self.opened.append(args[1])
                return None
            return real(args, env=env, **kw)
        for target, name, value in ((connect, 'WATCH_EVERY', 0.01), (connect, '_clean', lambda d: True),
                                    (connect.subprocess, 'Popen', popen), (browser, 'present', lambda: False),
                                    (server, 'mac_tick', lambda now=None: None), (server, 'MAC_TICK', 30),
                                    (server, 'TICK', 3600)):
            p = mock.patch.object(target, name, value, create=True)
            p.start()
            self.addCleanup(p.stop)
        self.stop = threading.Event()
        self.loop = threading.Thread(target=server.loop, kwargs={'stop': self.stop}, daemon=True)
        self.addCleanup(self.end)               # last in, so first out: the loop ends before its stand-ins go

    def end(self):
        for x in self.made:
            if x.rc is None:
                x.fail()
        self.stop.set()
        server.WAKE.set()
        if self.loop.is_alive():
            self.loop.join(60)

    def until(self, said, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if said():
                return True
            time.sleep(0.05)
        return False

    def test_a_sign_in_is_kept_within_seconds_of_the_browsers_yes(self):
        fake = self.fake
        fake.hold(test_keep.A)
        self.signed_in(test_keep.A)
        b = fake.hold(B, status='needs_login')
        handed = []

        def add_login(config_dir):          # as the engine does: it reads the folder, and the account has a login again
            handed.append((core.read_json(os.path.join(config_dir, '.claude.json')).get('oauthAccount') or {})
                          .get('emailAddress'))
            fake.by_id[b]['status'] = 'ok'
            return [a for a in fake.accounts() if a.id == b][0]
        p = mock.patch.object(api, 'add_login', add_login)
        p.start()
        self.addCleanup(p.stop)
        self.loop.start()
        self.assertTrue(self.until(lambda: (core.load().get('accounts') or {}).get(B, {}).get('needs_login'), 120))
        connect.start(B, None)                                  # Sign in pressed on its row
        time.sleep(1.0)                                         # the loop sleeps: its next reading is an hour away
        self.assertTrue(core.load()['accounts'][B]['needs_login'])
        self.made[0].approve(B)                                 # the browser's yes
        kept = self.until(lambda: not core.load()['accounts'][B]['needs_login'], 20)
        self.assertTrue(kept, 'not kept within 20 seconds of the yes: %r' % [e['text'] for e in core.load()['events']])
        self.assertIn('Added %s' % B, [e['text'] for e in core.load()['events']])
        self.assertEqual(handed, [B])                           # the engine was handed that sign-in's folder, once
        self.assertEqual(connect.RUNNING, {})


class ThePagesRoute(SignIns):
    def setUp(self):
        super().setUp()
        self.names = []

        def new_name(label):
            self.names.append(label)
            return 'Claude %s%s' % (label, '' if len(self.names) == 1 else ' %d' % len(self.names))
        self.patch(browser, 'new_profile_name', new_name)

    def opened_said(self):
        return [t for t in self.texts(core.load()) if t.startswith('Opened the sign-in')]

    def test_pressed_again_for_a_new_profile_opens_the_one_its_sign_in_is_in(self):
        self.assertEqual(server.act('/api/connect', {'email': B, 'profile': '+new'})['key'], B)
        first = self.opened[0][0]
        self.long_ago(B)
        self.assertEqual(server.act('/api/connect', {'email': B, 'profile': '+new'})['key'], B)
        self.assertEqual([p for p, _ in self.opened], [first, first])       # not one more new profile
        self.assertEqual(len(self.made), 1)
        self.assertEqual(len(self.opened_said()), 2)

    def test_opened_is_said_only_when_a_page_was_opened(self):
        self.patch(connect, 'AGAIN_AFTER', 60)
        server.act('/api/connect', {'email': B, 'profile': 'P1'})
        server.act('/api/connect', {'email': B, 'profile': 'P1'})           # a double click
        self.assertEqual(len(self.opened_said()), 1)
        self.made[0].approve(B)
        server.act('/api/connect', {'email': B, 'profile': 'P1'})           # after the browser approved it
        self.assertEqual(len(self.opened_said()), 1)
        self.assertEqual(self.cleaned, [])

    def test_opened_names_the_profile_the_page_opened_in(self):
        server.act('/api/connect', {'email': B, 'profile': 'P1'})
        self.long_ago(B)
        server.act('/api/connect', {'email': B, 'profile': 'P2'})           # opened in P1 again: its sign-in is there
        said = self.opened_said()
        self.assertEqual(len(said), 2)
        self.assertEqual(said[0], said[1])
        self.assertTrue(said[0].endswith('(Work)'), said)


class ARestart(SignIns):
    """The page's job stops and starts again (an update, a crash) with a sign-in under way. The new start knows only
    what the folders say (connect.sweep). Here the Keychain clean-up is the real one, over a stand-in for the engine's
    removal of the item."""

    def setUp(self):
        super().setUp()
        self.removes, self.removed, self.going = True, [], ''
        self.patch(connect, '_clean', CLEAN)
        self.patch(connect.engine, 'remove_sign_in', lambda d: self.removed.append(d) or self.removes)
        # what `ps` says of a folder's process number: nothing (it has ended), unless a test says it is still going
        self.patch(connect, 'run', lambda args, **kw: type('Ran', (), {'stdout': self.going})())

    def restart(self):
        """The job stopped (nothing in memory any more) and starts: the folders are looked through."""
        connect.RUNNING.clear()
        connect.sweep()

    def test_one_the_browser_approved_just_before_is_handed_to_the_engine(self):
        connect.start(B, 'P1')
        d = self.made[0].dir
        self.made[0].approve(B)
        self.woken_soon()
        woken = len(self.woken)
        self.restart()
        self.assertTrue(os.path.isdir(d))                       # not cleared: its login is in there
        self.assertEqual([(c['email'], c['profile']) for c in connect.RUNNING.values()], [(B, 'P1')])
        self.assertGreater(len(self.woken), woken)              # the loop is woken to hand it over
        st = self.state(A, B)
        connect.finish(st)
        self.assertIn(('add_login', d), self.fake.calls)
        self.assertIn('Added %s' % B, self.texts(st))
        self.assertFalse(st['accounts'][B]['needs_login'])
        self.assertEqual(st['accounts'][B]['profile'], 'P1')
        self.assertFalse(os.path.isdir(d))
        self.assertEqual(connect.RUNNING, {})

    def test_one_approved_while_the_job_was_down(self):
        connect.start(B, 'P1')
        connect.RUNNING.clear()                                 # the job stops; the sign-in's own process goes on
        self.made[0].approve(B)                                 # ...and the browser approves it
        connect.sweep()                                         # the job starts
        st = self.state(A, B)
        connect.finish(st)
        self.assertIn('Added %s' % B, self.texts(st))

    def test_a_folder_from_the_build_before_names_nobody_and_the_engine_does(self):
        connect.start(B, 'P1')
        d = self.made[0].dir
        self.made[0].approve(B)
        if os.path.exists(os.path.join(d, ASKED)):
            os.remove(os.path.join(d, ASKED))                   # the build before wrote no such file
        self.restart()
        self.assertEqual([(k.startswith('left:'), c['email'], c['profile']) for k, c in connect.RUNNING.items()],
                         [(True, '', None)])
        st = self.state(A, B)
        connect.finish(st)
        self.assertIn('Added %s' % B, self.texts(st))

    def test_one_with_no_login_in_it_is_cleared_without_a_word(self):
        connect.start(B, 'P1')
        d = self.made[0].dir
        self.made[0].fail()                                     # it ended badly: nothing in its folder
        self.restart()
        st = self.state(A, B)
        connect.finish(st)
        self.assertEqual(self.texts(st), [])
        self.assertFalse(os.path.isdir(d))
        self.assertEqual(connect.RUNNING, {})

    def test_one_already_handed_over_is_never_handed_over_again(self):
        self.removes = False                                    # its Keychain item could not be removed just then
        connect.start(B, 'P1')
        d = self.made[0].dir
        self.made[0].approve(B)
        st = self.state(A, B)
        connect.finish(st)
        self.assertIn('Added %s' % B, self.texts(st))
        self.assertTrue(os.path.isdir(d))                       # so its folder stays for the next start
        self.assertTrue(os.path.exists(os.path.join(d, OVER)))
        handed = self.fake.calls.count(('add_login', d))
        self.removes = True
        self.restart()
        self.assertEqual(connect.RUNNING, {})                   # cleared, not taken up
        self.assertFalse(os.path.isdir(d))
        connect.finish(st)
        self.assertEqual(self.fake.calls.count(('add_login', d)), handed)

    def test_one_that_was_cancelled_is_cleared(self):
        self.removes = False
        key = connect.start(B, 'P1')
        d = self.made[0].dir
        connect.stop(key)
        self.made[0].approve(B)                                 # (whatever its process did after the Cancel)
        self.assertTrue(os.path.isdir(d))
        self.removes = True
        self.restart()
        self.assertEqual(connect.RUNNING, {})
        self.assertFalse(os.path.isdir(d))

    def test_an_old_one_is_cleared(self):
        connect.start(B, 'P1')
        d = self.made[0].dir
        self.made[0].approve(B)
        old = time.time() - TAKEN_UP_FOR - 60
        for path in [os.path.join(d, n) for n in os.listdir(d)] + [d]:
            os.utime(path, (old, old))
        self.restart()
        self.assertEqual(connect.RUNNING, {})
        self.assertFalse(os.path.isdir(d))

    def test_one_still_waiting_is_stopped_as_before(self):
        connect.start(B, 'P1')
        d = self.made[0].dir
        self.going = '/x/claude auth login --claudeai'
        self.restart()
        self.assertEqual(self.killed, [self.made[0].pid])
        self.assertEqual(connect.RUNNING, {})
        self.assertFalse(os.path.isdir(d))


class TheStateNamesThePagesFile(sandbox.Sandbox):
    PAGE = b'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<title>x</title>\n</head>\n</html>\n'

    def setUp(self):
        super().setUp()
        self.web = os.path.join(self.tmp, 'web')
        os.makedirs(self.web)
        self.patch(server, 'WEB', self.web)
        self.patch(server, 'PAGE', {'key': None, 'id': None})

    def write(self, data, name='index.html'):
        with open(os.path.join(self.web, name), 'wb') as f:
            f.write(data)

    def test_a_short_name_that_changes_with_the_file(self):
        self.assertIsNone(server.page_id())                     # no file: no name
        self.write(self.PAGE)
        one = server.page_id()
        self.assertRegex(one, r'^[0-9a-f]{12}$')
        self.assertEqual(server.page_id(), one)
        self.write(self.PAGE.replace(b'x', b'after an update'))
        self.assertNotEqual(server.page_id(), one)

    def test_the_page_is_sent_with_the_name_of_its_own_file(self):
        self.write(self.PAGE)
        self.write(b'<meta charset="utf-8"><p>another page</p>', 'go.html')
        mark = b'\n<meta name="switchboard-page" content="' + server.page_id().encode() + b'">'
        sent = server.page_bytes('index.html')
        self.assertEqual(sent.count(mark), 1)
        self.assertEqual(sent.replace(mark, b''), self.PAGE)    # and nothing else is changed
        self.assertTrue(sent.index(b'<meta charset="utf-8">') < sent.index(mark) < sent.index(b'<title>'))
        self.assertEqual(server.page_bytes('go.html'), b'<meta charset="utf-8"><p>another page</p>')


class TheRealPage(unittest.TestCase):
    def test_it_has_the_place_for_its_name_and_reads_it(self):
        with open(os.path.join(core.WEB, 'index.html'), 'rb') as f:
            page = f.read()
        self.assertEqual(page.count(getattr(server, 'PAGE_MARK', b'<meta charset="utf-8">')), 1)
        self.assertIn(b'meta[name="switchboard-page"]', page)

    def test_the_state_names_it(self):
        install(self)
        self.assertEqual(server.view().get('page'), server.page_id())
        self.assertTrue(server.page_id())


if __name__ == '__main__':
    unittest.main()
