import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A command talks to whatever page answers on its port. Given a data folder of its own but no port, a small script once
# changed the settings of a page serving another folder. So the running page says which data folder it serves (a short
# mark that gives nothing away, in the state answer the commands already read, busy or not), and every command that
# changes something through the page compares it with its own first and, when they differ, refuses in one sentence and
# sends nothing. A page that does not say (one from before this check) is refused too: that is the safe side. A command
# that only reads goes on as before.
import json
import os
import sys
import shutil
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import __main__ as cli
from switchboard import core, engine, server
from test_cli_commands import Cli
from test_cli_with_page import FakePage, state

OTHER = ('The page on port %d serves another data folder than this command uses. Nothing was changed. Run it with the '
         "page's data folder, or give it its own port with SWITCHBOARD_PORT.")
OLDER = ('The page on port %d does not say which data folder it serves (it is from an older Switchboard). Nothing was '
         'changed. %s')


class TheMark(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-mark-')
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_one_folder_one_mark_and_it_gives_nothing_away(self):
        a, b = os.path.join(self.tmp, 'a'), os.path.join(self.tmp, 'b')
        os.makedirs(a)
        os.makedirs(b)
        os.symlink(a, os.path.join(self.tmp, 'link-to-a'))
        self.assertEqual(core.folder_mark(a), core.folder_mark(a))
        self.assertEqual(core.folder_mark(os.path.join(self.tmp, 'link-to-a')), core.folder_mark(a))   # its real path
        self.assertNotEqual(core.folder_mark(a), core.folder_mark(b))
        self.assertNotEqual(core.folder_mark(os.path.join(self.tmp, 'not-made-yet')), core.folder_mark(a))
        mark = core.folder_mark(a)
        self.assertRegex(mark, r'^[0-9a-f]{16}$')
        self.assertNotIn('switchboard-mark', mark)
        with mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': a}):
            self.assertEqual(core.folder_mark(), mark)                      # the folder this process uses

    def test_the_page_says_it_in_its_state_answer_and_when_busy(self):
        self.assertEqual(server.heal_view()['folder'], core.folder_mark())
        # busy: no answer made yet (its first build still running)
        done = threading.Event()
        self.addCleanup(done.set)
        snap = server.Snapshot(build=lambda: done.wait(10) or {})
        snap.FIRST_WAIT = 0.05
        code, body = snap.answer()
        self.assertEqual(code, 503)
        said = json.loads(body.decode())
        self.assertEqual((said['busy'], said['folder']), (True, core.folder_mark()))
        # busy: more requests at once than it answers (the refusal sent before the request is read)
        sent = []

        class Request(object):
            def settimeout(self, t):
                pass

            def sendall(self, data):
                sent.append(data)

            def setblocking(self, b):
                pass

            def recv(self, n):
                return b''

            def shutdown(self, how):
                pass

            def close(self):
                pass
        server.PageServer.say_busy(server.PageServer.__new__(server.PageServer), Request(), wait=0)
        said = json.loads(sent[0].split(b'\r\n\r\n', 1)[1].decode())
        self.assertEqual((said['busy'], said['folder']), (True, core.folder_mark()))


class ACommandThatChanges(Cli):
    def test_the_same_folder_goes_through(self):
        page = FakePage(self, {('POST', '/api/auto'): (200, {})})
        cli.ask_page('/api/auto', {'enabled': False}, timeout=5)
        self.assertEqual(page.posted, [('/api/auto', {'enabled': False})])

    def test_another_folder_is_refused_in_one_sentence_and_nothing_is_sent(self):
        page = FakePage(self, {('GET', '/api/state'): (200, state(folder='0123456789abcdef')),
                               ('POST', '/api/auto'): (200, {})})
        with self.assertRaises(engine.Refused) as e:
            cli.ask_page('/api/auto', {'enabled': False}, timeout=5)
        self.assertEqual(str(e.exception), OTHER % core.port())
        self.assertEqual(page.posted, [])

    def test_a_page_from_before_the_check_is_refused(self):
        page = FakePage(self, {('GET', '/api/state'): (200, state(folder=None)), ('POST', '/api/auto'): (200, {})})
        with self.assertRaises(engine.Refused) as e:
            cli.ask_page('/api/auto', {'enabled': False}, timeout=5)
        self.assertEqual(str(e.exception), OLDER % (core.port(), cli.restart_page_words()))
        self.assertEqual(page.posted, [])

    def test_the_demo_is_refused(self):
        page = FakePage(self, {('GET', '/api/state'): (200, state(demo=True)), ('POST', '/api/auto'): (200, {})})
        with self.assertRaises(engine.Refused) as e:
            cli.ask_page('/api/auto', {'enabled': False}, timeout=5)
        self.assertEqual(str(e.exception), 'The page on port %d is the demo, with made-up accounts. Nothing was '
                                           'changed. Stop the demo (Ctrl-C where it runs), then run this again.'
                         % core.port())
        self.assertEqual(page.posted, [])

    def test_a_busy_page_of_this_folder_is_asked_as_before(self):
        busy = {'error': 'Switchboard is busy. It answers again in a moment.', 'busy': True, 'retry': 2,
                'version': core.VERSION}
        page = FakePage(self, {('GET', '/api/state'): (503, busy), ('POST', '/api/auto'): (200, {})})
        cli.ask_page('/api/auto', {'enabled': True}, timeout=5)
        self.assertEqual(page.posted, [('/api/auto', {'enabled': True})])
        page.answers[('GET', '/api/state')] = (503, dict(busy, folder='0123456789abcdef'))
        with self.assertRaises(engine.Refused):
            cli.ask_page('/api/auto', {'enabled': False}, timeout=5)
        self.assertEqual(len(page.posted), 1)

    def test_no_page_is_no_page_as_before(self):
        with self.assertRaises(OSError):                                    # callers then make the change here
            cli.ask_page('/api/auto', {'enabled': False}, timeout=2)

    def test_auto_off_from_a_script_with_its_own_folder_leaves_the_other_page_alone(self):
        self.two()
        page = FakePage(self, {('GET', '/api/state'): (200, state(folder='0123456789abcdef')),
                               ('POST', '/api/auto'): (200, {})})
        before = core.settings().get('auto')
        code, out, err = self.run_cli('auto', 'off')
        self.assertEqual(code, 'refused: ' + OTHER % core.port())
        self.assertEqual(page.posted, [])
        self.assertEqual(core.settings().get('auto'), before)              # nor was its own folder changed

    def test_config_set_is_refused_the_same_way(self):
        page = FakePage(self, {('GET', '/api/state'): (200, state(folder='0123456789abcdef')),
                               ('POST', '/api/auto'): (200, {})})
        code, out, err = self.run_cli('config', 'set', 'auto_policy.strategy', 'most-room')
        self.assertNotEqual(code, 0)
        self.assertIn(OTHER % core.port(), str(code) + out + err)
        self.assertEqual(page.posted, [])

    def test_a_command_that_only_reads_goes_on(self):
        self.two()
        FakePage(self, {('GET', '/api/state'): (200, state(folder='0123456789abcdef'))})
        self.assertEqual(cli.page_status(), 'running')
        self.assertIsNotNone(cli.page_state())


if __name__ == '__main__':
    unittest.main()
