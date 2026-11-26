import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The board of every assistant in the page's state (DESIGN section 20, the server's part).
#
# - facts() carries the last board the loop built (server.PROVIDERS), and render() hands it on as `providers`, read
#   with f.get, so facts from before the board still draw (providers None).
# - The loop stage builds the board on a thread of its own: the first time, every 300 s after, and at once when one
#   of its files changes (the providers' settings, readings and programs, the account engine's accounts and
#   readings). Never twice at once, never in the demo. A build that fails keeps the last board.
# - The three providers' files are in SNAPSHOT_FILES, so the state is built again when one changes.
# - One notification, once per out spell: the first provider in the order is out until a known time and another
#   added one has room. Its words say what is so and what the command does. Never with notifications off, never in
#   the demo, never when there is nowhere with room, and none for a provider that is not the first in the order.
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard import core, notify, server  # noqa: E402
from switchboard.providers import choices, contract, view, words  # noqa: E402

H, D = 3600.0, 86400.0
NOW = 1795600000.0


def board(claude_state=contract.ROOM, codex_state=contract.ROOM, claude_back=None, codex_back=None, order=None,
          codex_can_carry=True):
    claude = {'id': 'claude', 'name': 'Claude', 'account_list': True, 'state': claude_state, 'back_at': claude_back,
              'read_at': NOW - 60, 'count': 3, 'with_room': 0 if claude_state == contract.OUT else 2,
              'can_carry': True, 'seats': []}
    codex = {'id': 'codex', 'name': 'Codex', 'account_list': False, 'state': codex_state, 'back_at': codex_back,
             'read_at': NOW - 600, 'plan': 'ChatGPT Pro', 'can_carry': codex_can_carry,
             'seats': [{'label': 'ChatGPT Pro', 'windows': [{'label': 'weekly', 'used_pct': 40.0,
                                                             'resets_at': NOW + 2 * D, 'line': 95.0}]}]}
    shown = [claude, codex] if order != ['codex', 'claude'] else [codex, claude]
    return {'v': 1, 'now': NOW, 'order': [p['id'] for p in shown], 'providers': shown, 'add': [], 'carry': True}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-board-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        home = os.path.join(self.tmp, 'home')
        os.makedirs(os.path.join(home, '.claude', 'projects'))
        env = mock.patch.dict(os.environ, {'HOME': home, 'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data'),
                                           'CLAUDE_CONFIG_DIR': os.path.join(home, '.claude')})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        self.shown = []
        p = mock.patch.object(server, 'NOTIFY_RUN', lambda cmd: self.shown.append(cmd) or 0)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.dict(server.PROVIDERS, {'view': None, 'at': None, 'sig': None, 'running': False}, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def bodies(self):
        return [c[-1] for c in self.shown]         # osascript ... -- TITLE BODY


class InTheState(Base):
    def facts(self, **more):
        f = {'profiles': {}, 'browser': {'name': 'Google Chrome', 'present': False, 'tabs_blocked': False},
             'engine': {'installed': True, 'claude': True}, 'links': {'installed': False, 'on': False},
             'auto': {'session_at': 90, 'weekly_at': 95}, 'setup_hidden': True, 'now': time.time(), 'demo': False}
        f.update(more)
        return f

    def test_render_hands_the_board_on_and_draws_without_it(self):
        st = core.blank_state()
        self.assertIsNone(server.render(st, self.facts())['providers'])
        b = board()
        self.assertEqual(server.render(st, self.facts(providers=b))['providers'], b)

    def test_facts_carries_the_last_board(self):
        server.PROVIDERS['view'] = board()
        self.assertEqual(server.facts()['providers'], board())
        server.PROVIDERS['view'] = None
        self.assertIsNone(server.facts()['providers'])

    def test_the_providers_files_are_snapshot_files(self):
        watched = [w() for w in server.SNAPSHOT_FILES]
        for rel in (contract.SETTINGS_FILE, contract.READINGS_FILE, contract.PROGRAMS_FILE):
            self.assertIn(core.data_path(rel), watched)


class TheStage(Base):
    def setUp(self):
        super().setUp()
        self.looks = []

    def start(self, target, kwargs):
        """Run the look here, at once, as its thread would."""
        self.looks.append(kwargs)
        target(**kwargs)

    def test_first_time_then_every_300_s_or_when_a_file_changes(self):
        made = board()
        with mock.patch.object(view, 'view', lambda *a, **k: made):
            self.assertTrue(server.providers_tick(now=1000.0, start=self.start))
            self.assertEqual(server.PROVIDERS['view'], made)
            self.assertFalse(server.providers_tick(now=1100.0, start=self.start))     # not due, nothing changed
            choices.add('codex', time.time())                                            # a file changed
            self.assertTrue(server.providers_tick(now=1110.0, start=self.start))
            self.assertFalse(server.providers_tick(now=1200.0, start=self.start))
            self.assertTrue(server.providers_tick(now=1110.0 + server.PROVIDERS_EVERY + 1, start=self.start))
        self.assertEqual(len(self.looks), 3)

    def test_never_twice_at_once_and_never_in_the_demo(self):
        server.PROVIDERS['running'] = True
        self.assertFalse(server.providers_tick(now=1000.0, start=self.start))
        server.PROVIDERS['running'] = False
        core.DEMO = True
        self.assertFalse(server.providers_tick(now=1000.0, start=self.start))
        self.assertEqual(self.looks, [])

    def test_a_build_that_fails_keeps_the_last_board(self):
        server.PROVIDERS['view'] = board()

        def broken(*a, **k):
            raise RuntimeError('made-up')
        with mock.patch.object(view, 'view', broken):
            server.providers_look()
        self.assertEqual(server.PROVIDERS['view'], board())
        self.assertFalse(server.PROVIDERS['running'])

    def test_a_look_never_forces_a_build_of_the_state(self):
        # A look on its own thread may end in the middle of anything the page asks for (test_server_robust's two
        # hundred clients saw a second build a second after the first); the state takes the board at its next build.
        gen = server.SNAPSHOT.gen
        with mock.patch.object(view, 'view', lambda *a, **k: board()):
            server.providers_look()
        self.assertEqual(server.SNAPSHOT.gen, gen)
        self.assertEqual(server.PROVIDERS['view'], board())

    def test_the_loop_has_the_stage(self):
        import inspect
        src = inspect.getsource(server.loop)
        self.assertIn('providers_tick()', src)


class TheNotification(Base):
    def note(self, b, now=NOW):
        return server.providers_note(b, now=now)

    def test_claude_out_codex_has_room(self):
        self.assertTrue(self.note(board(claude_state=contract.OUT, claude_back=NOW + 2 * H)))
        self.assertEqual(self.bodies(), ['Claude is out until %s. Codex has room. To carry this chat on in Codex: '
                                         'switchboard continue.' % words.when(NOW + 2 * H, NOW)])

    def test_once_per_out_spell(self):
        b = board(claude_state=contract.OUT, claude_back=NOW + 2 * H)
        self.note(b)
        self.note(b, now=NOW + 60)
        self.note(board(claude_state=contract.OUT, claude_back=NOW + 2 * H + 30), now=NOW + 120)   # the same spell
        self.assertEqual(len(self.shown), 1)
        self.note(board(claude_state=contract.OUT, claude_back=NOW + 30 * H), now=NOW + 26 * H)     # a new one
        self.assertEqual(len(self.shown), 2)

    def test_none_when_there_is_nowhere_with_room(self):
        self.assertFalse(self.note(board(claude_state=contract.OUT, claude_back=NOW + H, codex_state=contract.OUT,
                                         codex_back=NOW + D)))
        self.assertFalse(self.note(board(claude_state=contract.OUT, claude_back=NOW + H, codex_can_carry=False)))
        self.assertFalse(self.note(board(claude_state=contract.OUT, claude_back=None)))       # until when: not known
        self.assertEqual(self.shown, [])

    def test_none_for_a_provider_that_is_not_first_in_the_order(self):
        self.assertFalse(self.note(board(codex_state=contract.OUT, codex_back=NOW + D)))
        self.assertEqual(self.shown, [])

    def test_the_first_in_the_order_is_the_one_in_use(self):
        self.assertTrue(self.note(board(codex_state=contract.OUT, codex_back=NOW + D, order=['codex', 'claude'])))
        self.assertTrue(self.bodies()[0].startswith('Codex is out until '))
        self.assertIn('Claude has room.', self.bodies()[0])

    def test_never_with_notifications_off_or_in_the_demo(self):
        b = board(claude_state=contract.OUT, claude_back=NOW + 2 * H)
        with mock.patch.object(server, 'helper_settings', lambda: {'awake': True, 'notify': False}):
            self.assertFalse(self.note(b))
        core.DEMO = True
        self.assertFalse(self.note(b))
        self.assertEqual(self.shown, [])

    def test_a_build_of_the_board_is_followed_by_its_note(self):
        b = board(claude_state=contract.OUT, claude_back=NOW + 2 * H)
        with mock.patch.object(view, 'view', lambda *a, **k: b):
            server.providers_look()
        self.assertEqual(len(self.shown), 1)


if __name__ == '__main__':
    unittest.main()
