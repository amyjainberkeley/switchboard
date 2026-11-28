import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Continue here, pressed where a person looks (the menu bar's menu and the page). The one rule:
# from these faces Switchboard never starts the provider in a terminal and never sends a chat. A press makes the note
# and opens the provider's own desktop app on it through its documented link; the note waits in its message box and
# the person presses send. Without that app (or when it does not take the link) the press gives the one line to run in
# a terminal and does nothing else. It answers in one sentence where it was made; refusals are one line (never, stay,
# not signed in, not on this Mac). The page's POST for it is guarded exactly as /api/switch is, and a request names
# only the provider: the chat and its folder are the server's own.
#
# Made-up accounts and chats only; the adapters are the stand-ins of tests/fakes_provider_kit.py, and the page's own
# server runs on the test run's second port.
import dataclasses
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_provider_kit import CHAT_A, NOW as KIT_NOW, KitCase, chat, codex_found, codex_limits, program  # noqa: E402
from switchboard import core, demo, server  # noqa: E402
from switchboard.providers import adapter_class, cli, contract, press, words  # noqa: E402
from switchboard.providers.contract import Found, Ran  # noqa: E402
import pagekey_help  # noqa: E402  (the page key, as Switchboard's own programs send it)

NAME = 'Upload retries'
OPEN = 'Codex is open with your chat "Upload retries" in its message box. Press send to carry on.'
PORT = int(os.environ.get('SWITCHBOARD_PORT') or 7937) + 1     # the test run's second port, never the page's own


class Press(KitCase, unittest.TestCase):
    def rig_with_chat(self, name=NAME, found=None, **kw):
        r = self.rig(claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name=name, **kw)], codex_found_=found)
        return r, {'id': CHAT_A, 'folder': r.folder}

    def nothing_happened(self, r):
        self.assertEqual((r.calls.execs, r.calls.links), ([], []))
        self.assertEqual((r.handoff.made, r.tasks.hops(r.folder)), ([], []))

    def test_the_app_opens_on_the_note_and_the_person_presses_send(self):
        r, c = self.rig_with_chat()
        got = press.press('codex', c, number=6, kit=r.kit)
        self.assertEqual(got, {'said': [OPEN]})
        (link,) = r.calls.links
        self.assertTrue(link.startswith('codex://new?prompt='), link)
        self.assertIn('&path=' + r.folder.replace('/', '%2F'), link)
        self.assertEqual(r.calls.execs, [])                                 # never a start in a terminal
        (hop,) = r.tasks.hops(r.folder)
        self.assertEqual((hop.state, hop.from_chat, hop.to_provider), ('in_app', CHAT_A, 'codex'))
        self.assertIsNone(r.choices.consent(r.folder, 'codex'))             # the press is no yes kept for the folder
        self.assertEqual(r.asked, [])
        (said,) = [s[0] for s in r.said]
        self.assertTrue(said.startswith('Opened Codex on a note of a chat from Claude in site (note '), said)

    def test_never_at_a_terminal_whatever_the_person_set(self):
        r, c = self.rig_with_chat()
        r.reach = dataclasses.replace(r.reach, tty=lambda: True)
        r.choices.set_open_with('codex', contract.IN_TERMINAL)
        self.assertEqual(press.press('codex', c, number=6, kit=r.kit), {'said': [OPEN]})
        self.assertEqual(r.calls.execs, [])
        self.assertEqual(len(r.calls.links), 1)

    def test_a_chat_with_no_name(self):
        r, c = self.rig_with_chat(name=None)
        self.assertEqual(press.press('codex', c, kit=r.kit)['said'],
                         ['Codex is open with your Claude Code chat in its message box. Press send to carry on.'])

    def test_real_problems_come_first_one_line_each(self):
        r, c = self.rig_with_chat(working=True)
        r.codex.limits_ = codex_limits(pct=97.0)
        got = press.press('codex', c, kit=r.kit)['said']
        self.assertEqual(got[1:], [words.STILL_WORKING, OPEN])
        self.assertTrue(got[0].startswith('Codex is out until '), got[0])

    def test_without_its_app_the_line_to_run_and_nothing_else(self):
        cli_only = program(self.root, 'codex', '0.169.1')                 # on PATH only: no desktop app
        found = Found('codex', installed=True, program=cli_only, programs=(cli_only,), newest_seen='0.169.1',
                      tested=True)
        r, c = self.rig_with_chat(found=found)
        got = press.press('codex', c, number=6, kit=r.kit)
        self.assertEqual(got, {'said': ["Codex's app is not on this Mac. To carry on, run this in a terminal:"],
                               'run': 'cd %s && switchboard 6' % r.folder})
        self.nothing_happened(r)

    def test_a_link_the_app_does_not_take_gives_the_line_to_run(self):
        r, c = self.rig_with_chat()
        r.reach = dataclasses.replace(r.reach, open_url=lambda url, app=None: Ran(code=1))
        got = press.press('codex', c, number=6, kit=r.kit)
        self.assertEqual(got['said'], ["Codex's app did not open. To carry on, run this in a terminal:"])
        self.assertEqual(r.tasks.hops(r.folder)[0].state, 'failed')
        self.assertEqual(r.calls.execs, [])

    def test_refusals_in_one_line(self):
        cases = [('never', lambda r: r.choices.set_consent(r.folder, 'codex', 'never', KIT_NOW),
                  'You said chats in site never go to Codex. switchboard allow codex, run there, lifts that.'),
                 ('stay', lambda r: r.choices.set_stay(r.folder, 'claude', KIT_NOW),
                  'Chats in site stay on Claude. switchboard stay off, run there, lifts that.'),
                 ('not signed in', lambda r: setattr(r.codex, 'found_', codex_found(self.root, signed_in=False)),
                  words.SIGNED_OUT.format(name='Codex', how=adapter_class('codex').sign_in)),
                 ('not here', lambda r: setattr(r.codex, 'found_', Found('codex', installed=False)),
                  "Codex is not on this Mac. Install OpenAI's Codex (its app or its command line), then try again.")]
        for what, make, line in cases:
            with self.subTest(what=what):
                r, c = self.rig_with_chat()
                make(r)
                with self.assertRaises(cli.Refused) as e:
                    press.press('codex', c, number=6, kit=r.kit)
                self.assertEqual(str(e.exception), line)
                self.assertNotIn('\n', str(e.exception))
                self.nothing_happened(r)

    def test_not_added(self):
        r = self.rig(codex_added=False, claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name=NAME)])
        with self.assertRaises(cli.Refused) as e:
            press.press('codex', {'id': CHAT_A, 'folder': r.folder}, kit=r.kit)
        self.assertEqual(str(e.exception), words.NOT_ADDED.format(name='Codex', pid='codex'))
        self.nothing_happened(r)

    def test_no_chat_a_chat_gone_and_a_word_it_does_not_know(self):
        r, c = self.rig_with_chat()
        for given, line in ((None, words.PRESS_NO_CHAT), ({'id': 'x', 'folder': r.folder}, words.PRESS_NO_CHAT),
                            ({'id': CHAT_A, 'folder': 'site'}, words.PRESS_NO_CHAT),
                            ({'id': 'b0b0b0b0-1111-4222-8333-444455556666', 'folder': r.folder},
                             words.PRESS_CHAT_GONE)):
            with self.subTest(given=given):
                with self.assertRaises(cli.Refused) as e:
                    press.press('codex', given, kit=r.kit)
                self.assertEqual(str(e.exception), line)
        for pid in ('claude', 'nope'):
            with self.assertRaises(cli.Refused):
                press.press(pid, c, kit=r.kit)
        self.nothing_happened(r)

    def test_the_log_holds_fixed_words_only(self):
        r, c = self.rig_with_chat(name='fix `parse()` in /Users/someone/site')
        with mock.patch.object(core, 'log') as log:
            press.press('codex', c, kit=r.kit)
        (line,) = [a[0][0] for a in log.call_args_list]
        self.assertTrue(line.startswith('press: Codex opened in its app on a note of a chat from Claude (hop h-'),
                        line)
        self.assertNotIn('parse', line)
        self.assertNotIn(r.folder, line)
        with mock.patch.object(core, 'log') as log:
            with self.assertRaises(cli.Refused):
                press.press('codex', None, kit=r.kit)
        self.assertEqual([a[0][0] for a in log.call_args_list],
                         ['press: nothing was opened for Codex (it was refused)'])


class AFolderTooWide(KitCase, unittest.TestCase):
    # A chat started in the home folder, in /, or in a folder that holds the home folder: no other provider is
    # started there (it could read everything under it). The press and Continue here refuse it in one line, before
    # anything is read; the row the faces draw for it carries that as its why, so no Continue here is offered.
    def rig_in(self, folder, tty=False):
        return self.rig(tty=tty, claude_chats=[lambda f: chat('claude', folder or f, cid=CHAT_A, name=NAME)],
                        codex_found_=codex_found(self.root, signed_in=True))

    def wide(self, r):
        home = os.path.realpath(r.reach.home)
        os.makedirs(home, exist_ok=True)
        return home

    def test_continue_here_refuses_and_reads_nothing(self):
        r = self.rig()
        home = self.wide(r)
        r = self.rig_in(home)
        with self.assertRaises(cli.Refused) as e:
            press.press('codex', {'id': CHAT_A, 'folder': home}, number=6, kit=r.kit)
        self.assertEqual(str(e.exception), 'That chat was started in %s, a folder too wide to start Codex in: it could '
                                           'read everything under it.' % home)
        self.assertEqual((r.handoff.made, r.calls.links, r.calls.execs), ([], [], []))
        self.assertFalse([c for c in r.claude.calls if str(c).startswith('read')])

    def test_the_key_press_refuses_and_reads_nothing(self):
        r = self.rig()
        home = self.wide(r)
        r = self.rig_in(home, tty=True)
        kit = dataclasses.replace(r.kit, cwd=lambda: home)
        self.assertEqual(cli.move('codex', kit=kit), 1)
        self.assertEqual(r.lines(), ['That chat was started in %s, a folder too wide to start Codex in: it could read '
                                     'everything under it.' % home])
        self.assertEqual((r.asked, r.handoff.made, r.calls.execs), ([], [], []))

    def test_the_row_says_why_and_offers_nothing(self):
        r = self.rig()
        home = self.wide(r)
        r = self.rig_in(home)
        found = press.chat_from([(CHAT_A, None, home)], r.kit)
        self.assertIsNotNone(found)
        (row,) = press.face_rows(found, r.kit)
        self.assertEqual((row['why'], row['can']), ('its folder is too wide to carry on from', False))
        r = self.rig_in(None)                                               # a project folder: as before
        (row,) = press.face_rows(press.chat_from([(CHAT_A, None, r.folder)], r.kit), r.kit)
        self.assertEqual((row['why'], row['can']), (None, True))
        (row,) = press.face_rows(None, r.kit)                               # no chat: no reason, nothing to press
        self.assertEqual((row['why'], row['can']), (None, False))


class TheServer(KitCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        saved = dict(server.PROVIDER_ROWS)
        self.addCleanup(server.PROVIDER_ROWS.update, saved)
        no_gap = mock.patch.object(server, 'PRESS_GAP', 0.0)
        no_gap.start()
        self.addCleanup(no_gap.stop)

    def test_the_app_opens_or_the_line_to_run_and_nothing_is_kept_for_later(self):
        cli_only = program(self.root, 'codex', '0.169.1')                 # on PATH only: no desktop app
        found = Found('codex', installed=True, program=cli_only, programs=(cli_only,), newest_seen='0.169.1',
                      tested=True)
        for found_, opened in ((found, False), (None, True)):
            r = self.rig(claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name=NAME)], codex_found_=found_)
            with mock.patch.object(server, 'load', lambda: {'accounts': {}}), \
                    mock.patch.object(server, 'newest_chats', lambda folder=None, r=r: [(CHAT_A, None, r.folder)]):
                got = server.continue_in('codex', press.mark(CHAT_A), kit=r.kit)
            self.assertEqual('run' in got, not opened)
        self.assertFalse(os.path.exists(core.data_path('comeup.json')))   # no record of a press is kept

    def test_one_press_every_few_seconds(self):
        r = self.rig(claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name=NAME)])
        clock = [100.0]
        with mock.patch.object(server, 'PRESS_GAP', 3.0), mock.patch.object(server, 'PRESS', {'at': None}), \
                mock.patch.object(server, 'monotonic', lambda: clock[0]), \
                mock.patch.object(server, 'load', lambda: {'accounts': {}}), \
                mock.patch.object(server, 'newest_chats', lambda folder=None: [(CHAT_A, None, r.folder)]):
            self.assertEqual(server.continue_in('codex', press.mark(CHAT_A), kit=r.kit), {'said': [OPEN]})
            clock[0] = 102.5
            with self.assertRaises(core.SwitchError) as e:
                server.continue_in('codex', press.mark(CHAT_A), kit=r.kit)
            self.assertEqual(str(e.exception), 'Continue was pressed a moment ago. Try again in a few seconds.')
            self.assertEqual(len(r.calls.links), 1)
            clock[0] = 103.1
            self.assertEqual(server.continue_in('codex', press.mark(CHAT_A), kit=r.kit), {'said': [OPEN]})
        self.assertEqual(len(r.calls.links), 2)

    def test_the_chat_is_the_servers_own_and_a_refusal_is_one_line(self):
        r = self.rig(claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name=NAME)])
        with mock.patch.object(server, 'load', lambda: {'accounts': {'a@x.example': {'connected': True, 'slot': 2},
                                                                     'b@x.example': {'connected': True, 'slot': 5}}}), \
                mock.patch.object(server, 'newest_chats', lambda folder=None: [(CHAT_A, None, r.folder)]):
            self.assertEqual(server.continue_in('codex', press.mark(CHAT_A), kit=r.kit), {'said': [OPEN]})
            r.choices.set_stay(r.folder, 'claude', KIT_NOW)
            with self.assertRaises(core.SwitchError):
                server.continue_in('codex', press.mark(CHAT_A), kit=r.kit)
            r.choices.set_stay(r.folder, None, KIT_NOW)
        with mock.patch.object(server.threading, 'Thread'):
            self.assertTrue(server.rows_tick())                        # it looks again on the next pass
        server.PROVIDER_ROWS.update(at=1.0, running=False)
        r.choices.set_consent(r.folder, 'codex', 'never', KIT_NOW)
        with self.assertRaises(core.SwitchError) as e, \
                mock.patch.object(server, 'newest_chats', lambda folder=None: [(CHAT_A, None, r.folder)]):
            server.continue_in('codex', press.mark(CHAT_A), kit=r.kit)
        self.assertIn('never go to Codex', str(e.exception))
        for bad in (None, '', 'Codex', '../x', 'a' * 40, 3):
            with self.assertRaises(core.SwitchError):
                server.continue_in(bad, press.mark(CHAT_A), kit=r.kit)

    def test_a_press_carries_the_chat_the_face_showed(self):
        # The face sends the mark of the chat it showed; when the newest chat is another one by the press, or no mark
        # came, the press is refused in one line and nothing happens.
        newer = 'b0b0b0b0-1111-4222-8333-444455556666'
        r = self.rig(claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name=NAME),
                                   lambda f: chat('claude', f, cid=newer, name='Newer one', ago=5.0)])
        with mock.patch.object(server, 'load', lambda: {'accounts': {}}), \
                mock.patch.object(server, 'newest_chats', lambda folder=None: [(newer, None, r.folder),
                                                                                (CHAT_A, None, r.folder)]):
            for mark in (press.mark(CHAT_A), None, 'deadbeef'):
                with self.subTest(mark=mark):
                    with self.assertRaises(core.SwitchError) as e:
                        server.continue_in('codex', mark, kit=r.kit)
                    self.assertEqual(str(e.exception), 'A newer chat is there now. Look again.')
            self.assertEqual((r.calls.links, r.tasks.hops(r.folder)), ([], []))
            got = server.continue_in('codex', press.mark(newer), kit=r.kit)
        self.assertEqual(got['said'], ['Codex is open with your chat "Newer one" in its message box. Press send to '
                                       'carry on.'])

    def test_the_rows_number_for_the_line_to_run(self):
        st = {'accounts': {'a@x.example': {'connected': True, 'slot': 2},
                           'b@x.example': {'connected': True, 'slot': 5},
                           'c@x.example': {'connected': False, 'slot': 9}}}
        rows = [{'provider': 'codex'}, {'provider': 'later'}]
        self.assertEqual([server.provider_number(p, rows, st) for p in ('codex', 'later', 'other')], [6, 7, None])


class TheRealRoute(KitCase, unittest.TestCase):
    """POST /api/continue on the page's own server, not the demo: a trusted POST reaches the press (on stand-ins), and
    whatever else the body carries (a chat, a folder, a path, a link) changes nothing: the chat and its folder are the
    server's."""

    def setUp(self):
        super().setUp()
        self.r = self.rig(claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name=NAME)])
        saved = dict(server.PROVIDER_ROWS)
        self.addCleanup(server.PROVIDER_ROWS.update, saved)
        server.PROVIDER_ROWS.update(rows=[], chat=None, at=1.0, running=False)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        for p in (mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(PORT)}),
                  mock.patch.object(server, 'PRESS_GAP', 0.0),
                  mock.patch.object(cli, 'default_kit', lambda reach=None: self.r.kit),
                  mock.patch.object(server, 'newest_chats', lambda folder=None: [(CHAT_A, None, self.r.folder)]),
                  mock.patch.object(server, 'load', lambda: {'accounts': {'a@x.example': {'connected': True,
                                                                                          'slot': 5}}})):
            p.start()
            self.addCleanup(p.stop)
        httpd = ThreadingHTTPServer(('127.0.0.1', PORT), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

    def post(self, body):
        req = urllib.request.Request('http://127.0.0.1:%d/api/continue' % PORT, data=json.dumps(body).encode(),
                                     headers=pagekey_help.with_key({'Content-Type': 'application/json',
                                                                    'X-Switchboard': '1'}))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=20) as got:
                return got.status, json.load(got)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.load(e)
            finally:
                e.close()

    def test_a_trusted_post_reaches_the_press_and_nothing_else_in_the_body_counts(self):
        plain = self.post({'provider': 'codex', 'mark': press.mark(CHAT_A)})
        self.assertEqual(plain, (200, {'said': [OPEN], 'ok': True}))
        other = os.path.join(self.root, 'elsewhere')
        os.makedirs(other)
        more = self.post({'provider': 'codex', 'mark': press.mark(CHAT_A),
                          'chat': {'id': 'b0b0b0b0-1111-4222-8333-444455556666', 'folder': other},
                          'folder': other, 'path': '/etc/hosts', 'link': 'codex://new?prompt=x&path=%2Fetc',
                          'url': 'codex://new?prompt=y', 'how': 'terminal'})
        self.assertEqual(more, plain)
        self.assertEqual(len(self.r.calls.links), 2)
        for link in self.r.calls.links:
            self.assertIn('&path=' + self.r.folder.replace('/', '%2F'), link)
        self.assertEqual(self.r.calls.execs, [])
        self.assertEqual([h.from_chat for h in self.r.tasks.hops(self.r.folder)], [CHAT_A, CHAT_A])
        self.assertEqual(self.r.tasks.hops(other), [])


class ThePagesPost(unittest.TestCase):
    """POST /api/continue on the page's own server (demo mode), guarded as /api/switch is."""

    def setUp(self):
        demo._ready()
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(PORT)})
        env.start()
        self.addCleanup(env.stop)
        httpd = ThreadingHTTPServer(('127.0.0.1', PORT), server.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)

    def post(self, headers, path='/api/continue'):
        req = urllib.request.Request('http://127.0.0.1:%d%s' % (PORT, path),
                                     data=json.dumps({'provider': 'codex'}).encode(),
                                     headers=dict({'Content-Type': 'application/json'}, **headers))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=10) as got:
                return got.status, json.load(got)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.load(e)
            finally:
                e.close()

    def test_from_the_page_it_reaches_the_press_and_from_elsewhere_never(self):
        self.assertEqual(self.post({'X-Switchboard': '1'}),
                         (400, {'error': "The demo's OpenAI row is made up, so nothing opens."}))
        for headers in ({}, {'X-Switchboard': '1', 'Origin': 'https://example.com'},
                        {'X-Switchboard': '1', 'Host': 'example.com:%d' % PORT},
                        {'Origin': 'http://127.0.0.1:%d' % PORT}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(headers), (403, {'error': 'forbidden'}))
                self.assertEqual(self.post(headers, '/api/switch')[0], 403)        # the same answer as a switch


if __name__ == '__main__':
    unittest.main()
