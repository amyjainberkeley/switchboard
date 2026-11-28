import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The other provider's row in the page's state, for every face that shows it (the page and the menu bar's menu):
# the rows derived from the board (providers.rows.from_board), plus the chat that would go (the newest chat Claude
# Code wrote on this Mac, by its name only, on one line) and whether the row can be continued to now (a chat would go,
# and its folder has no stay on another provider and no never for this one). The server derives the rows from its last
# board on a thread of its own and the state carries the last ones; the demo has one made-up row and one made-up chat
# name.
#
# Made-up accounts, chats and numbers only; the adapters are the stand-ins of tests/fakes_provider_kit.py.
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from fakes_provider_kit import CHAT_A, NOW as KIT_NOW, KitCase, chat, codex_found, codex_limits, program  # noqa: E402
from switchboard import comeup  # noqa: E402
from switchboard.providers.contract import Found  # noqa: E402
from switchboard import core, server  # noqa: E402
from switchboard.providers import press, view, words  # noqa: E402
from switchboard.providers.codex import CodexProvider  # noqa: E402

CHAT_B = 'b0b0b0b0-1111-4222-8333-444455556666'
CHAT_C = 'c0c0c0c0-1111-4222-8333-444455556666'
NAME = 'Upload retries'


class TheChatThatWouldGo(KitCase, unittest.TestCase):
    def test_the_newest_one_a_person_had(self):
        r = self.rig(claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name=NAME, ago=60.0),
                                   lambda f: chat('claude', f, cid=CHAT_B, name='Nightly run', ago=5.0,
                                                  entrypoint='sdk-cli')])
        found = press.chat_from([(CHAT_B, None, r.folder), (CHAT_A, None, r.folder)], r.kit)
        self.assertEqual((found[0].id, found[1]), (CHAT_A, r.folder))     # a run nobody watched is passed over

    def test_none_when_the_reader_finds_none(self):
        r = self.rig(claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name=NAME)])
        self.assertIsNone(press.chat_from([], r.kit))
        self.assertIsNone(press.chat_from([(CHAT_C, None, r.folder)], r.kit))        # not in its folder
        self.assertIsNone(press.chat_from([('not-an-id', None, r.folder), (CHAT_A, None, 'relative')], r.kit))

    def test_it_looks_where_the_chat_was_started(self):
        # chats.py keeps the folder a chat last named; the reader files a chat where it began (its first lines).
        r = self.rig(claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name=NAME)])
        deeper = os.path.join(r.folder, 'src')
        os.makedirs(deeper)
        path = os.path.join(self.root, CHAT_A + '.jsonl')
        with open(path, 'w') as f:
            f.write(json.dumps({'type': 'user', 'cwd': r.folder, 'timestamp': '2026-11-13T23:00:00Z'}) + '\n')
        self.assertEqual(press.chat_from([(CHAT_A, path, deeper)], r.kit)[1], r.folder)
        self.assertIsNone(press.chat_from([(CHAT_A, None, deeper)], r.kit))

    def test_only_its_name_on_one_line(self):
        self.assertEqual(press.chat_words(chat('claude', '/x', name='Upload\nretries\x1b[31m')),
                         {'name': 'Upload retries', 'mark': press.mark(CHAT_A)})
        self.assertRegex(press.mark(CHAT_A), r'^[0-9a-f]{8}$')              # a short mark, never the id
        self.assertNotIn(press.mark(CHAT_A), CHAT_A)
        self.assertEqual(press.chat_words(chat('claude', '/x', name='x' * 90))['name'], 'x' * 57 + '...')
        self.assertEqual(press.chat_words(chat('claude', '/x', name=None)),
                         {'name': None, 'mark': press.mark(CHAT_A)})
        self.assertIsNone(press.chat_words(None))


class TheRow(KitCase, unittest.TestCase):
    def rig_with_chat(self, signed_in=True, **kw):
        kw.setdefault('codex_found_', codex_found(self.root, signed_in=signed_in))
        r = self.rig(claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name=NAME)], **kw)
        return r, press.chat_from([(CHAT_A, None, r.folder)], r.kit)

    def test_the_row_carries_the_chat_and_can(self):
        r, found = self.rig_with_chat()
        got = press.face_rows(found, r.kit)
        self.assertEqual([(x['provider'], x['chat'], x['can']) for x in got],
                         [('codex', {'name': NAME, 'mark': press.mark(CHAT_A)}, True)])
        self.assertEqual(sorted(got[0]), ['app', 'back_at', 'can', 'chat', 'email', 'fresh', 'in_use', 'name', 'next',
                                          'plan', 'provider', 'read_at', 'state', 'vendor', 'why', 'windows'])
        self.assertTrue(got[0]['app'])
        cli_only = program(self.root, 'codex', '0.169.1')                     # on PATH only: no desktop app
        r, found = self.rig_with_chat(codex_found_=Found('codex', installed=True, program=cli_only,
                                                         programs=(cli_only,), signed_in=True, newest_seen='0.169.1',
                                                         tested=True))
        self.assertFalse(press.face_rows(found, r.kit)[0]['app'])

    def test_no_chat_no_continuing(self):
        r = self.rig(codex_found_=codex_found(self.root, signed_in=True))
        got = press.face_rows(None, r.kit)
        self.assertEqual([(x['chat'], x['can']) for x in got], [(None, False)])

    def test_the_chats_folder_decides(self):
        r, found = self.rig_with_chat()
        r.choices.set_consent(r.folder, 'codex', 'never', KIT_NOW)
        self.assertFalse(press.face_rows(found, r.kit)[0]['can'])
        r.choices.set_consent(r.folder, 'codex', 'yes', KIT_NOW)
        self.assertTrue(press.face_rows(found, r.kit)[0]['can'])
        r.choices.set_stay(r.folder, 'claude', KIT_NOW)
        self.assertFalse(press.face_rows(found, r.kit)[0]['can'])

    def test_nothing_started_and_never_at_a_terminal(self):
        # The rows come from the board, which is built from files only; nothing else is asked.
        r, found = self.rig_with_chat(tty=True)
        press.face_rows(found, r.kit)
        self.assertEqual((r.calls.runs, r.calls.execs, r.calls.links, r.calls.pipes), ([], [], [], []))
        self.assertFalse(press._kit_in(r.kit, r.folder).reach.tty())

    def test_only_a_codex_that_can_be_continued_to_now(self):
        # Saved numbers that read room whatever the program is: a Codex that cannot be continued to, as far as files
        # can tell, has no row (so nothing to press, and it is never next); one line under the list says why, and for
        # a sign-in not seen here the one step to take (Codex's own sign-in words).
        here = program(self.root, 'codex', '0.169.1', where='ChatGPT.app', source='app')
        cases = [('not installed', Found('codex', installed=False), 'not on this Mac'),
                 ('signed out', codex_found(self.root, signed_in=False), 'not signed in'),
                 ('sign-in not seen', codex_found(self.root, signed_in=None),
                  words.FIT_LINE_SIGN_IN_NOT_SEEN.format(state='sign-in not seen here', how=CodexProvider.sign_in)),
                 ('no copy fit to run', Found('codex', installed=True, program=None, programs=(here,),
                                              signed_in=True), 'no copy here can be run'),
                 ('older than its chats', codex_found(self.root, version='0.155.1', seen='0.169.1', signed_in=True),
                  'older than the one that wrote your chats')]
        for what, found_, why in cases:
            with self.subTest(what=what):
                r, found = self.rig_with_chat(codex_found_=found_)
                self.assertEqual(press.face_rows(found, r.kit), [])
                said = view.under_list(view.view(kit=r.kit), KIT_NOW)
                self.assertEqual([x for x in said if x[0] != 'carry'], [('provider', 'Codex', why)])
                self.assertFalse(next_of(at_lines(None)))
        r, found = self.rig_with_chat()
        (row,) = press.face_rows(found, r.kit)
        self.assertEqual((row['can'], row['why']), (True, None))
        self.assertTrue(next_of(at_lines(row)))

    def test_has_room_only_on_numbers_read_within_the_hour(self):
        for ago, fresh in ((720.0, True), (3600.0, True), (3601.0, False), (5 * 86400.0, False)):
            with self.subTest(ago=ago):
                r, found = self.rig_with_chat()
                r.codex.limits_ = codex_limits(read_ago=ago)
                (row,) = press.face_rows(found, r.kit)
                self.assertEqual((row['state'], row['fresh']), ('room', fresh))
                self.assertEqual(next_of(at_lines(row)), fresh)
        r, found = self.rig_with_chat()
        r.codex.limits_ = codex_limits(pct=97.0)
        self.assertFalse(press.face_rows(found, r.kit)[0]['fresh'])           # out: no room, however fresh

    def test_none_added_none_shown_and_a_failure_shows_none(self):
        self.assertEqual(press.face_rows(None, self.rig(codex_added=False).kit), [])
        self.assertEqual(press.face_rows(('not', 'a chat'), self.rig().kit), [])


def at_lines(row):
    """A state with both Claude accounts over their lines (test_comeup's), set up as a running page's, and this row
    (None: no row)."""
    from test_comeup import state
    return dict(state(provider_rows=[row] if row is not None else []), setup={'engine': True, 'accounts': True})


def next_of(state):
    """Is a provider row marked next (comeup.with_next, the one Next of every face)?"""
    return any(p.get('next') for p in comeup.with_next(state)['provider_rows'])


class TheServer(KitCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        saved = dict(server.PROVIDER_ROWS)
        self.addCleanup(server.PROVIDER_ROWS.update, saved)
        board = dict(server.PROVIDERS)
        self.addCleanup(server.PROVIDERS.update, board)

    def test_newest_chats_from_the_last_look(self):
        data = os.path.join(self.root, 'data')
        os.makedirs(data)
        entries = {CHAT_A: {'file': '/made-up/a.jsonl', 'cwd': '/made-up/site', 'last': 10.0},
                   CHAT_B: {'file': '/made-up/b.jsonl', 'cwd': '/made-up/other', 'last': 20.0},
                   CHAT_C: {'file': '/made-up/c.jsonl', 'last': 30.0}}
        with open(os.path.join(data, 'chats.json'), 'w') as f:
            json.dump({'v': 1, 'chats': entries}, f)
        view = {'chats': [{'id': CHAT_A, 'last': 10.0}, {'id': CHAT_B, 'last': 20.0}, {'id': CHAT_C, 'last': 30.0}]}
        self.assertEqual(server.newest_chats(view, folder=data),
                         [(CHAT_B, '/made-up/b.jsonl', '/made-up/other'),
                          (CHAT_A, '/made-up/a.jsonl', '/made-up/site')])
        self.assertEqual(server.newest_chats({'chats': []}, folder=data), [])

    def test_the_state_carries_the_last_look(self):
        r = self.rig(claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name=NAME)],
                     codex_found_=codex_found(self.root, signed_in=True))
        server.PROVIDERS['view'] = view.view(kit=r.kit)
        with mock.patch.object(server, 'newest_chats', lambda folder=None: [(CHAT_A, None, r.folder)]):
            server.rows_look(kit=r.kit)
        self.assertEqual(server.PROVIDER_ROWS['chat'], {'id': CHAT_A, 'folder': r.folder, 'name': NAME,
                                                    'mark': press.mark(CHAT_A)})
        got = server.provider_rows()
        self.assertEqual([(x['provider'], x['chat'], x['can']) for x in got],
                         [('codex', {'name': NAME, 'mark': press.mark(CHAT_A)}, True)])
        got[0]['can'] = False                                       # a copy: the state never changes the cache
        self.assertTrue(server.provider_rows()[0]['can'])
        with mock.patch.object(server, 'provider_rows', lambda: [{'provider': 'codex', 'chat': {'name': NAME}}]):
            f = server.facts()
        self.assertEqual(f['provider_rows'], [{'provider': 'codex', 'chat': {'name': NAME}}])

    def test_a_look_that_fails_keeps_the_last_rows(self):
        server.PROVIDER_ROWS.update(rows=[{'provider': 'codex'}], chat=None, at=None, running=True)
        with mock.patch.object(server, 'newest_chats', lambda folder=None: 1 / 0):
            server.rows_look()
        self.assertEqual((server.PROVIDER_ROWS['rows'], server.PROVIDER_ROWS['running']),
                         ([{'provider': 'codex'}], False))

    def test_the_rows_come_from_the_last_board_and_nothing_else(self):
        r = self.rig(claude_chats=[lambda f: chat('claude', f, cid=CHAT_A, name=NAME)],
                     codex_found_=codex_found(self.root, signed_in=True))
        newest = mock.patch.object(server, 'newest_chats', lambda folder=None: [(CHAT_A, None, r.folder)])
        server.PROVIDERS['view'] = None
        with newest:
            server.rows_look(kit=r.kit)
        self.assertEqual(server.provider_rows(), [])                # no board yet: no rows
        server.PROVIDERS['view'] = view.view(kit=r.kit)
        r.codex.calls.clear()
        r.codex.limits_ = codex_limits(pct=97.0)                    # the world changed; the board has not yet
        with newest:
            server.rows_look(kit=r.kit)
        (row,) = server.provider_rows()
        self.assertEqual((row['state'], row['chat']['name']), ('room', NAME))
        self.assertNotIn('detect', r.codex.calls)

    def test_a_new_board_makes_the_rows_due_at_once(self):
        server.PROVIDER_ROWS.update(at=100.0, running=False)
        board = {'v': 1, 'now': KIT_NOW, 'order': ['claude'], 'providers': [], 'add': [], 'carry': False}
        with mock.patch.object(view, 'view', lambda *a, **k: board), \
                mock.patch.object(server, 'providers_note', lambda b, now=None: False):
            server.providers_look()
        with mock.patch.object(server.threading, 'Thread') as thread:
            self.assertTrue(server.rows_tick(now=110.0))
        self.assertEqual(thread.call_count, 1)

    def test_a_look_is_due_every_30_seconds_and_never_in_the_demo(self):
        server.PROVIDER_ROWS.update(at=100.0, running=False)
        with mock.patch.object(server.threading, 'Thread') as thread:
            self.assertFalse(server.rows_tick(now=110.0))
            self.assertTrue(server.rows_tick(now=131.0))
            self.assertFalse(server.rows_tick(now=200.0))        # one at a time
        self.assertEqual(thread.call_count, 1)
        server.PROVIDER_ROWS.update(at=None, running=False)
        with mock.patch.object(core, 'DEMO', True):
            self.assertFalse(server.rows_tick(now=500.0))


class TheDemo(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True

    def test_one_made_up_row_and_one_made_up_chat_name(self):
        from switchboard import demo
        got = demo.view()['provider_rows']
        self.assertEqual([(p['provider'], p['vendor'], p['plan'], p['chat'], p['can']) for p in got],
                         [('codex', 'OpenAI', 'Plus', {'name': 'Billing export fix',
                                                       'mark': press.mark(demo.DEMO_CHATS[0][0])}, True)])


if __name__ == '__main__':
    unittest.main()
