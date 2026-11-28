import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The board of every assistant in the page's state (DESIGN section 20, the server's part).
#
# - facts() carries the last board the loop built (server.PROVIDERS), and render() hands it on as `providers`, read
#   with f.get, so facts from before the board still draw (providers None).
# - The loop stage builds the board on a thread of its own: the first time, every 300 s after, at once when one of
#   the providers' own files changes (their settings, readings and programs), and at most every two minutes when
#   only the account engine's accounts or readings changed. Never twice at once (a build counts until its note
#   ends), never in the demo. A build that fails keeps the last board.
# - The look for the chat a press would carry lists no chat when the board has no row to draw.
# - The three providers' files are in SNAPSHOT_FILES, so the state is built again when one changes.
# - One notification, once per out spell (comeup.note): when the first provider in the order is the one whose seats
#   are the account list, every usable account at the person's lines and another provider's row holding, with where
#   to press; when another provider is first, it is out until a known time and another added one has room. Never with
#   notifications off, never in the demo, never when there is nowhere with room, and none for a provider that is not
#   the first in the order.
import json
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

from fakes_provider_kit import CHAT_A, KitCase, chat  # noqa: E402
from switchboard import core, server  # noqa: E402
from switchboard.providers import choices, contract, press, view, words  # noqa: E402
from test_comeup import WEEK_BACK, at, state as accounts_state, w  # noqa: E402

H, D = 3600.0, 86400.0
NOW = 1795600000.0


def board(claude_state=contract.ROOM, codex_state=contract.ROOM, claude_back=None, codex_back=None, order=None,
          codex_can_carry=True, read=NOW - 600):
    claude = {'id': 'claude', 'name': 'Claude', 'account_list': True, 'state': claude_state, 'back_at': claude_back,
              'read_at': NOW - 60, 'count': 3, 'with_room': 0 if claude_state == contract.OUT else 2,
              'can_carry': True, 'seats': [], 'fit_why': None}
    codex = {'id': 'codex', 'name': 'Codex', 'account_list': False, 'state': codex_state, 'back_at': codex_back,
             'read_at': read, 'plan': 'ChatGPT Pro', 'can_carry': codex_can_carry, 'fit_why': None,
             'seats': [{'label': 'ChatGPT Pro', 'plan': 'pro', 'read_at': read, 'state': codex_state,
                        'back_at': codex_back, 'windows': [{'label': 'weekly', 'used_pct': 40.0,
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
        p = mock.patch.dict(server.NOTE_SPELL, {'since': None}, clear=True)
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

    def test_the_accounts_files_rebuild_it_at_most_every_so_often(self):
        # The account engine rewrites its readings about once a minute while Claude Code is in use; a provider's own
        # files are what the other rows are made of, so they still rebuild it at once.
        usage = core.data_path('usage.json')
        os.makedirs(os.path.dirname(usage), exist_ok=True)

        def reading(n):
            with open(usage, 'w') as f:
                f.write('{"n": %d}' % n)
        gap = server.PROVIDERS_ACCOUNTS_EVERY
        self.assertTrue(60.0 < gap < server.PROVIDERS_EVERY)
        with mock.patch.object(view, 'view', lambda *a, **k: board()):
            self.assertTrue(server.providers_tick(now=1000.0, start=self.start))
            reading(1)
            self.assertFalse(server.providers_tick(now=1060.0, start=self.start))           # a reading a minute on
            self.assertTrue(server.providers_tick(now=1000.0 + gap, start=self.start))
            reading(2)
            choices.add('codex', time.time())                                               # a provider's own file
            self.assertTrue(server.providers_tick(now=1001.0 + gap, start=self.start))
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

    def test_no_second_build_while_the_note_of_the_first_runs(self):
        # The note may build the whole state (seconds under load). A file that changes meanwhile waits for the next
        # pass, so two builds and two notes never run side by side; the board is already kept when the note runs.
        made, during = board(), []

        def note(b, now=None, state=None):
            if not during:
                during.append('noting')
                choices.add('codex', time.time())
                during[0] = (server.providers_tick(now=1001.0, start=self.start), server.PROVIDERS['view'] is made)
            return False
        with mock.patch.object(view, 'view', lambda *a, **k: made), mock.patch.object(server, 'providers_note', note):
            self.assertTrue(server.providers_tick(now=1000.0, start=self.start))
            self.assertEqual(during, [(False, True)])
            self.assertFalse(server.PROVIDERS['running'])
            self.assertTrue(server.providers_tick(now=1002.0, start=self.start))     # the change, at the next pass

    def test_a_note_that_fails_never_stops_the_next_build(self):
        def broken(b, now=None, state=None):
            raise RuntimeError('made-up')
        with mock.patch.object(view, 'view', lambda *a, **k: board()), mock.patch.object(server, 'providers_note',
                                                                                         broken):
            with self.assertRaises(RuntimeError):
                server.providers_look()
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


def only_claude():
    """A board with no other provider added: no row can be there."""
    b = board()
    return dict(b, order=['claude'], providers=b['providers'][:1], carry=False)


def codex_signed_out():
    """A board where Codex is added but cannot be continued to: no row either."""
    b = board()
    b['providers'][1]['fit_why'] = words.STATE_SIGNED_OUT
    return b


class TheChatLook(KitCase, Base):
    """rows_look, the look for the chat a press would carry: a person with no row to draw pays nothing for it."""

    def setUp(self):
        super().setUp()
        p = mock.patch.dict(server.PROVIDER_ROWS, {'rows': [], 'chat': None, 'at': None, 'running': False},
                            clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.r = self.rig(claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name='Upload retries')])
        self.listed = []

    def newest(self, folder=None):
        self.listed.append(folder)
        return [(CHAT_A, None, self.r.folder)]

    def test_no_chat_is_listed_when_no_row_can_be_there(self):
        for name, b in (('only Claude', only_claude()), ('Codex not signed in', codex_signed_out())):
            with self.subTest(board=name):
                del self.listed[:]
                server.PROVIDERS['view'] = b
                server.PROVIDER_ROWS.update(rows=[{'provider': 'codex'}], running=True,
                                            chat={'id': CHAT_A, 'folder': self.r.folder, 'mark': press.mark(CHAT_A)})
                with mock.patch.object(server, 'newest_chats', self.newest):
                    server.rows_look(kit=self.r.kit)
                self.assertEqual(self.listed, [])
                self.assertEqual((server.PROVIDER_ROWS['rows'], server.PROVIDER_ROWS['chat'],
                                  server.PROVIDER_ROWS['running']), ([], None, False))

    def test_a_row_that_can_be_there_still_gets_its_chat(self):
        server.PROVIDERS['view'] = board()
        with mock.patch.object(server, 'newest_chats', self.newest):
            server.rows_look(kit=self.r.kit)
        self.assertEqual(len(self.listed), 1)
        self.assertEqual(server.PROVIDER_ROWS['chat']['id'], CHAT_A)
        self.assertEqual([x['provider'] for x in server.PROVIDER_ROWS['rows']], ['codex'])

    def test_a_press_with_no_row_is_the_no_chat_refusal(self):
        # A page left open from before, or any program on this Mac, can still send a press: with no row, no chat is
        # looked for and the press is refused as when there is no chat at all.
        server.PROVIDERS['view'] = only_claude()
        server.PROVIDER_ROWS['chat'] = {'id': CHAT_A, 'folder': self.r.folder, 'mark': press.mark(CHAT_A)}
        with mock.patch.object(server, 'PRESS_GAP', 0.0), mock.patch.object(server, 'newest_chats', self.newest), \
                mock.patch.object(server, 'load', lambda: {'accounts': {}}):
            with self.assertRaises(core.SwitchError) as e:
                server.continue_in('codex', press.mark(CHAT_A), kit=self.r.kit)
        self.assertEqual(str(e.exception), words.PRESS_NO_CHAT)
        self.assertEqual((self.listed, self.r.calls.links), ([], []))

    def test_no_look_while_the_first_board_is_being_built(self):
        # The rows follow the first board (providers_look makes them due at once), so the gate above can decide.
        server.PROVIDERS.update(view=None, running=True)
        with mock.patch.object(server.threading, 'Thread') as thread:
            self.assertFalse(server.rows_tick(now=100.0))
            server.PROVIDERS.update(view=only_claude(), running=False)
            self.assertTrue(server.rows_tick(now=101.0))
        self.assertEqual(thread.call_count, 1)

    def test_the_index_is_read_again_only_when_it_changed(self):
        data = os.path.join(self.tmp, 'chats-here')
        os.makedirs(data)
        path = os.path.join(data, 'chats.json')

        def write(cwd):
            with open(path + '.new', 'w') as f:
                json.dump({'v': 1, 'chats': {CHAT_A: {'file': '/made-up/a.jsonl', 'cwd': cwd}}}, f)
            os.replace(path + '.new', path)
        write('/made-up/site')
        seen = {'chats': [{'id': CHAT_A, 'last': 10.0}]}
        reads, real = [], server.chatfile.load_index
        site = [(CHAT_A, '/made-up/a.jsonl', '/made-up/site')]

        def counted(p, loads=None):
            reads.append(p)
            return real(p, loads)
        with mock.patch.object(server.chatfile, 'load_index', counted), \
                mock.patch.object(server.chats, 'load', side_effect=AssertionError('chats-view.json is not needed')):
            for _ in range(2):
                self.assertEqual(server.newest_chats(seen, folder=data), site)
            self.assertEqual(len(reads), 1)                                 # the same file: not parsed again
            write('/made-up/other')
            self.assertEqual(server.newest_chats(seen, folder=data), [(CHAT_A, '/made-up/a.jsonl', '/made-up/other')])
            self.assertEqual(len(reads), 2)
            later = {'chats': [{'id': CHAT_A, 'last': 10.0}, {'id': 'not-in-the-index', 'last': 20.0}]}
            self.assertEqual(server.newest_chats(later, folder=data), [(CHAT_A, '/made-up/a.jsonl', '/made-up/other')])
            self.assertEqual(len(reads), 3)                                 # other chats are the newest: read again


def at_lines():
    """The page's state with both Claude accounts at the person's lines (test_comeup's)."""
    return accounts_state()


def with_room():
    """The same, with one account back under its lines."""
    s = accounts_state()
    s['accounts'][1]['usage']['weekly'] = w(20, WEEK_BACK)
    return s


SAID = ('Both Claude accounts are at your lines. Codex has room. In a terminal in your project folder, type '
        'switchboard and press 3.')


class TheNotification(Base):
    def note(self, b, now=NOW, state=None):
        return server.providers_note(b, now=now, state=state)

    def test_claude_out_codex_has_room(self):
        self.assertTrue(self.note(board(claude_state=contract.OUT, claude_back=NOW + 2 * H), state=at_lines()))
        self.assertEqual(self.bodies(), [SAID])

    def test_once_per_out_spell(self):
        b = board(claude_state=contract.OUT, claude_back=NOW + 2 * H)
        self.note(b, state=at_lines())
        self.note(b, now=NOW + 60, state=at_lines())
        self.note(board(claude_state=contract.OUT, claude_back=NOW + 2 * H + 30), now=NOW + 120,
                  state=at_lines())                                                                # the same spell
        self.assertEqual(len(self.shown), 1)
        later = at_lines()
        later['accounts'][0]['usage']['session'] = w(93, at(15, 18))         # its 5-hour window two days on
        self.note(board(claude_state=contract.OUT, claude_back=NOW + 30 * H, read=NOW + 26 * H - 600),
                  now=NOW + 26 * H, state=later)                                                     # a new one
        self.assertEqual(len(self.shown), 2)

    def test_none_when_there_is_nowhere_with_room(self):
        self.assertFalse(self.note(board(claude_state=contract.OUT, claude_back=NOW + H, codex_state=contract.OUT,
                                         codex_back=NOW + D), state=at_lines()))
        self.assertFalse(self.note(board(claude_state=contract.OUT, claude_back=NOW + H, codex_can_carry=False),
                                   state=at_lines()))
        self.assertFalse(self.note(board(codex_state=contract.OUT, codex_back=None, order=['codex', 'claude'])))
        self.assertEqual(self.shown, [])                                      # until when: not known

    def test_none_for_a_provider_that_is_not_first_in_the_order(self):
        self.assertFalse(self.note(board(codex_state=contract.OUT, codex_back=NOW + D), state=with_room()))
        self.assertEqual(self.shown, [])

    def test_the_first_in_the_order_is_the_one_in_use(self):
        self.assertTrue(self.note(board(codex_state=contract.OUT, codex_back=NOW + D, order=['codex', 'claude'])))
        self.assertTrue(self.bodies()[0].startswith('Codex is out until '))
        self.assertIn('Claude has room.', self.bodies()[0])

    def test_never_with_notifications_off_or_in_the_demo(self):
        b = board(claude_state=contract.OUT, claude_back=NOW + 2 * H)
        with mock.patch.object(server, 'helper_settings', lambda: {'awake': True, 'notify': False}):
            self.assertFalse(self.note(b, state=at_lines()))
        core.DEMO = True
        self.assertFalse(self.note(b, state=at_lines()))
        self.assertEqual(self.shown, [])

    def test_a_spell_with_no_known_end_never_silences_a_later_one(self):
        # With no reset time known, a spell is said once from when this page first saw it. Between two such spells a
        # board with no row (Codex signed out), or with another provider first, must not keep the first one's start,
        # or the new spell has the old instance and notify's memory never says it.
        def no_end():
            s = at_lines()
            for a in s['accounts']:
                a['usage'] = {'session': w(93), 'weekly': w(96), 'scoped': []}
            return s
        signed_out = board(claude_state=contract.OUT)
        signed_out['providers'][1]['fit_why'] = 'not signed in'
        between = (('no row', lambda t: server.providers_note(signed_out, now=t, state=no_end())),
                   ('another provider first', lambda t: server.providers_note(
                       board(codex_state=contract.OUT, codex_back=None, order=['codex', 'claude']), now=t)))
        for name, gap in between:
            with self.subTest(between=name):
                server.NOTE_SPELL['since'] = None
                del self.shown[:]
                seen = []
                real = server.notify.send
                with mock.patch.object(server.notify, 'send', lambda n, **k: seen.append(n.instance) or real(n, **k)):
                    later = NOW + (0 if name == 'no row' else 9) * D
                    self.assertTrue(self.note(board(claude_state=contract.OUT, read=later - 600), now=later,
                                              state=no_end()))
                    gap(later + H)
                    again = later + 3 * H
                    self.assertTrue(self.note(board(claude_state=contract.OUT, read=again - 600), now=again,
                                              state=no_end()))
                self.assertEqual(seen, [later, again])
                self.assertEqual(len(self.shown), 2)

    def test_a_build_of_the_board_is_followed_by_its_note(self):
        b = board(claude_state=contract.OUT, claude_back=NOW + 2 * H, read=time.time() - 600)
        with mock.patch.object(view, 'view', lambda *a, **k: b), mock.patch.object(server, 'view', at_lines):
            server.providers_look()
        self.assertEqual(len(self.shown), 1)


if __name__ == '__main__':
    unittest.main()
