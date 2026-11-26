import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""A chat's own file says which account it was on.

Claude Code writes a session-owner line (the login a chat started under) and a credential_org line each time the
login it is on changes. chatfile reads exactly those: kind, organization id, time, the chat's id and folder, and
nothing else from the file. The lines below follow the shapes Claude Code 2.1.296 to 2.1.325 wrote; every id is
made up."""
import ast
import calendar
import json
import os
import shutil
import tempfile
import time
import unittest

from switchboard import chatfile, statusline

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ORG_A = 'aaaaaaaa-0000-4000-8000-00000000000a'
ORG_B = 'bbbbbbbb-0000-4000-8000-00000000000b'
ORG_C = 'cccccccc-0000-4000-8000-00000000000c'
ACCT_A = 'a1a1a1a1-0000-4000-8000-00000000000a'
CHAT = '5e55105e-0000-4000-8000-000000000001'
FOLDER = '/Users/example/project'


def stamp(t):
    """Claude Code's own time words for second `t` (with thousandths)."""
    whole = int(t)
    ms = int(round((t - whole) * 1000))
    y, mo, d, h, mi, s = time.gmtime(whole)[:6]
    return '%04d-%02d-%02dT%02d:%02d:%02d.%03dZ' % (y, mo, d, h, mi, s, ms)


def owner_line(org=ORG_A, acct=ACCT_A, chat=CHAT):
    return json.dumps({'type': 'session-owner', 'accountUuid': acct, 'organizationUuid': org, 'sessionId': chat})


def org_line(org, t, chat=CHAT, cwd=FOLDER):
    return json.dumps({'parentUuid': '00000000-0000-4000-8000-0000000000aa', 'isSidechain': False,
                       'attachment': {'type': 'credential_org', 'organizationUuid': org}, 'type': 'attachment',
                       'uuid': '00000000-0000-4000-8000-0000000000bb', 'timestamp': stamp(t), 'userType': 'external',
                       'entrypoint': 'cli', 'cwd': cwd, 'sessionId': chat, 'version': '2.1.325',
                       'gitBranch': 'main'})


def said(text, t, role='user'):
    return json.dumps({'type': role, 'timestamp': stamp(t), 'sessionId': CHAT,
                       'message': {'role': role, 'content': text}})


T0 = 1794000000.0        # a made-up day in November 2026


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='sb-chatfile-')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, CHAT + '.jsonl')

    def put(self, *lines, **kw):
        with open(self.path, 'ab' if kw.get('more') else 'wb') as fh:
            for line in lines:
                fh.write(line.encode('utf-8') + (b'' if kw.get('torn') and line is lines[-1] else b'\n'))


class Times(unittest.TestCase):
    def test_claude_codes_times_read_to_the_second_they_name(self):
        for y, mo, d, h, mi, s in ((1970, 1, 1, 0, 0, 0), (2024, 2, 29, 23, 59, 59), (2024, 3, 1, 0, 0, 0),
                                   (2026, 11, 18, 2, 47, 45), (2028, 12, 31, 12, 0, 1), (2100, 3, 1, 0, 0, 0),
                                   (2000, 2, 29, 6, 7, 8), (2027, 1, 1, 0, 0, 0)):
            words = '%04d-%02d-%02dT%02d:%02d:%02dZ' % (y, mo, d, h, mi, s)
            self.assertEqual(chatfile.parse_ts(words), calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0)), words)

    def test_thousandths_are_kept(self):
        self.assertAlmostEqual(chatfile.parse_ts('2026-11-18T02:47:45.569Z'),
                               calendar.timegm((2026, 11, 18, 2, 47, 45, 0, 0, 0)) + 0.569, places=3)

    def test_anything_else_is_no_time(self):
        for bad in (None, 5, '', 'yesterday', '2026-11-18', '2026-13-01T00:00:00Z', '2026-11-18T25:00:00Z',
                    '2026-11-18T02:47:45+05:30', '2026-11-18T02:47:45.5X', '2026/11/18T02:47:45Z'):
            self.assertIsNone(chatfile.parse_ts(bad), bad)


class Lines(unittest.TestCase):
    def test_the_owner_line(self):
        self.assertEqual(chatfile.parse_line(owner_line()), ('owner', ORG_A, ACCT_A, CHAT))

    def test_the_account_line(self):
        self.assertEqual(chatfile.parse_line(org_line(ORG_B, T0)), ('org', ORG_B, T0, CHAT, FOLDER))

    def test_a_message_that_only_quotes_the_words_says_nothing(self):
        quoted = said('the file has {"type":"session-owner"} and "credential_org" lines', T0)
        self.assertIsNone(chatfile.parse_line(quoted))
        result = json.dumps({'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'content': org_line(ORG_C, T0)}]}})
        self.assertIsNone(chatfile.parse_line(result))

    def test_a_long_line_is_not_even_parsed(self):
        long_line = org_line(ORG_B, T0)[:-1] + ', "pad": "%s"}' % ('x' * chatfile.LINE_MAX)

        def never(_):
            raise AssertionError('parsed')
        self.assertIsNone(chatfile.parse_line(long_line, loads=never))

    def test_torn_and_foreign_lines_say_nothing(self):
        for bad in (org_line(ORG_B, T0)[:40], '"credential_org"', '["session-owner"]',
                    json.dumps({'type': 'attachment', 'attachment': {'type': 'credential_org'}}),
                    json.dumps({'type': 'attachment', 'attachment': {'type': 'credential_org',
                                                                     'organizationUuid': 7}}),
                    json.dumps({'type': 'session-owner', 'organizationUuid': ''})):
            self.assertIsNone(chatfile.parse_line(bad), bad)

    def test_an_account_line_with_no_time_still_names_the_account(self):
        d = json.loads(org_line(ORG_B, T0))
        del d['timestamp']
        self.assertEqual(chatfile.parse_line(json.dumps(d)), ('org', ORG_B, None, CHAT, FOLDER))


class Reading(Files):
    def test_a_whole_chat(self):
        self.put(said('hello', T0), owner_line(), org_line(ORG_A, T0 + 1), said('working', T0 + 2, 'assistant'),
                 owner_line(), org_line(ORG_A, T0 + 50), org_line(ORG_B, T0 + 4000), owner_line(),
                 org_line(ORG_B, T0 + 4100), org_line(ORG_C, T0 + 9000, cwd='/Users/example/other'))
        r = chatfile.read(self.path)
        self.assertEqual((r.owner, r.owner_account, r.session), (ORG_A, ACCT_A, CHAT))
        self.assertEqual(r.steps, [(T0 + 1, ORG_A), (T0 + 4000, ORG_B), (T0 + 9000, ORG_C)])   # changes only
        self.assertEqual(r.cwd, '/Users/example/other')                                          # the last said
        self.assertEqual(r.end, os.path.getsize(self.path))
        self.assertEqual(r.size, r.end)

    def test_reading_on_from_where_the_last_look_stopped(self):
        self.put(owner_line(), org_line(ORG_A, T0))
        r = chatfile.read(self.path)
        self.put(said('more', T0 + 5), org_line(ORG_B, T0 + 600), more=True)
        r2 = chatfile.read(self.path, start=r.end, into=r)
        self.assertIs(r2, r)
        fresh = chatfile.read(self.path)
        self.assertEqual((r.owner, r.steps, r.end), (fresh.owner, fresh.steps, fresh.end))
        self.assertEqual(r.steps, [(T0, ORG_A), (T0 + 600, ORG_B)])

    def test_a_line_still_being_written_waits_for_the_next_look(self):
        self.put(owner_line(), org_line(ORG_A, T0))
        whole = os.path.getsize(self.path)
        half = org_line(ORG_B, T0 + 600)
        self.put(half[:60], more=True, torn=True)
        r = chatfile.read(self.path)
        self.assertEqual(r.steps, [(T0, ORG_A)])
        self.assertEqual(r.end, whole)
        with open(self.path, 'ab') as fh:
            fh.write(half[60:].encode() + b'\n')
        chatfile.read(self.path, start=r.end, into=r)
        self.assertEqual(r.steps, [(T0, ORG_A), (T0 + 600, ORG_B)])

    def test_nothing_new_changes_nothing(self):
        self.put(owner_line(), org_line(ORG_A, T0))
        r = chatfile.read(self.path)
        before = (r.owner, list(r.steps), r.end)
        chatfile.read(self.path, start=r.end, into=r)
        self.assertEqual((r.owner, r.steps, r.end), before)

    def test_a_file_cut_shorter_is_read_from_its_start(self):
        self.put(owner_line(), org_line(ORG_A, T0), org_line(ORG_B, T0 + 600))
        r = chatfile.read(self.path)
        self.put(owner_line(ORG_C, None), org_line(ORG_C, T0 + 900))      # replaced by a shorter file
        r2 = chatfile.read(self.path, start=r.end, into=r)
        self.assertEqual((r2.owner, r2.steps), (ORG_C, [(T0 + 900, ORG_C)]))

    def test_a_missing_file_says_nothing_and_raises_nothing(self):
        r = chatfile.read(os.path.join(self.dir, 'gone.jsonl'))
        self.assertEqual((r.owner, r.steps, r.end, r.size), (None, [], 0, 0))
        self.assertEqual(chatfile.tail(os.path.join(self.dir, 'gone.jsonl')).steps, [])

    def test_a_bounded_look_stops_and_the_next_carries_on(self):
        self.put(owner_line(), org_line(ORG_A, T0), *[said('line %d' % i, T0 + i) for i in range(200)])
        self.put(org_line(ORG_B, T0 + 600), more=True)
        r = chatfile.read(self.path, max_bytes=2000)
        self.assertLess(r.end, os.path.getsize(self.path))
        self.assertEqual(r.steps, [(T0, ORG_A)])
        while r.end < os.path.getsize(self.path):
            chatfile.read(self.path, start=r.end, into=r, max_bytes=2000)
        self.assertEqual(r.steps, [(T0, ORG_A), (T0 + 600, ORG_B)])

    def test_the_tail_starts_inside_a_line_and_skips_the_rest_of_it(self):
        self.put(owner_line(), org_line(ORG_A, T0), said('x' * 5000, T0 + 1), org_line(ORG_B, T0 + 600),
                 owner_line(), said('y' * 300, T0 + 601))
        size = os.path.getsize(self.path)
        r = chatfile.tail(self.path, nbytes=1500)                     # begins inside the long message
        self.assertEqual(r.steps, [(T0 + 600, ORG_B)])
        self.assertEqual(r.owner, ORG_A)                              # Claude Code repeats the owner line
        self.assertEqual(r.end, size)
        cut = len(owner_line().encode()) + 1 + 20                      # begins inside the first account line
        r = chatfile.read(self.path, start=cut)
        self.assertEqual(r.steps, [(T0 + 600, ORG_B)])                # that torn first line is not half-read

    def test_the_hooks_small_reader_reads_the_same(self):
        self.put(owner_line(), org_line(ORG_A, T0), org_line(ORG_B, T0 + 600))
        a = chatfile.read(self.path)
        b = chatfile.read(self.path, loads=statusline._loads)
        self.assertEqual((a.owner, a.owner_account, a.steps, a.session, a.cwd),
                         (b.owner, b.owner_account, b.steps, b.session, b.cwd))

    def test_bytes_that_are_not_text_do_not_stop_the_read(self):
        with open(self.path, 'wb') as fh:
            fh.write(b'\xff\xfe junk "credential_org" \xff\n')
            fh.write(owner_line().encode() + b'\n' + org_line(ORG_B, T0).encode() + b'\n')
        r = chatfile.read(self.path)
        self.assertEqual((r.owner, r.steps), (ORG_A, [(T0, ORG_B)]))


class Steps(unittest.TestCase):
    def test_where_a_chat_was_at_a_time(self):
        steps = [(T0, ORG_A), (T0 + 600, ORG_B), (T0 + 9000, ORG_C)]
        self.assertEqual(chatfile.on_at(steps, T0 - 1, owner=ORG_A), ORG_A)     # before its first line: its owner
        self.assertIsNone(chatfile.on_at(steps, T0 - 1))
        self.assertEqual(chatfile.on_at(steps, T0), ORG_A)
        self.assertEqual(chatfile.on_at(steps, T0 + 599), ORG_A)
        self.assertEqual(chatfile.on_at(steps, T0 + 600), ORG_B)
        self.assertEqual(chatfile.on_at(steps, T0 + 99999), ORG_C)
        self.assertEqual(chatfile.on_at([], T0, owner=ORG_B), ORG_B)

    def test_where_a_chat_is_now(self):
        self.assertEqual(chatfile.on_now([(T0, ORG_A), (T0 + 600, ORG_B)], owner=ORG_A), ORG_B)
        self.assertEqual(chatfile.on_now([], owner=ORG_A), ORG_A)
        self.assertIsNone(chatfile.on_now([]))

    def test_a_flutter_folds_into_where_the_chat_came_to_rest(self):
        steps = [(T0, ORG_A), (T0 + 5000, ORG_B), (T0 + 5010, ORG_A), (T0 + 5020, ORG_C), (T0 + 9000, ORG_A)]
        self.assertEqual(chatfile.settle(steps), [(T0, ORG_A), (T0 + 5000, ORG_C), (T0 + 9000, ORG_A)])

    def test_a_flutter_that_ends_where_it_began_is_no_move(self):
        steps = [(T0, ORG_A), (T0 + 5000, ORG_B), (T0 + 5026, ORG_A), (T0 + 9000, ORG_C)]
        self.assertEqual(chatfile.settle(steps), [(T0, ORG_A), (T0 + 9000, ORG_C)])

    def test_real_moves_stay(self):
        steps = [(T0, ORG_A), (T0 + 600, ORG_B), (T0 + 1200, ORG_A)]
        self.assertEqual(chatfile.settle(steps), steps)
        self.assertEqual(chatfile.settle([]), [])
        self.assertEqual(chatfile.settle([(None, ORG_A), (None, ORG_B)]), [(None, ORG_A), (None, ORG_B)])

    def test_how_many_times_a_chat_moved(self):
        self.assertEqual(chatfile.moves([], owner=ORG_A), 0)
        self.assertEqual(chatfile.moves([(T0, ORG_A)], owner=ORG_A), 0)
        self.assertEqual(chatfile.moves([(T0, ORG_A), (T0 + 600, ORG_B), (T0 + 1200, ORG_A)], owner=ORG_A), 2)
        self.assertEqual(chatfile.moves([(T0, ORG_B)], owner=ORG_A), 1)          # resumed under another login
        self.assertEqual(chatfile.moves([(T0, ORG_A), (T0 + 5000, ORG_B), (T0 + 5026, ORG_A)], owner=ORG_A), 0)


class SavedLook(Files):
    def entry(self, **kw):
        e = {'file': self.path, 'end': 0, 'owner': ORG_A, 'steps': [[T0, ORG_A], [T0 + 600, ORG_B]],
             'cwd': FOLDER, 'last': T0 + 700}
        e.update(kw)
        return e

    def save(self, doc):
        p = chatfile.index_path(self.dir)
        with open(p, 'w') as fh:
            json.dump(doc, fh)
        return p

    def test_the_saved_look_is_read_or_is_not_known(self):
        p = self.save({'v': 1, 'at': T0, 'orgs': {ORG_A: 'a@example.com'}, 'chats': {CHAT: self.entry()},
                       'connectors': {ORG_A: {'Google Drive': T0}}})
        self.assertEqual(os.path.basename(p), 'chats.json')
        self.assertEqual(chatfile.load_index(p)['chats'][CHAT]['owner'], ORG_A)
        self.assertEqual(chatfile.load_index(p, loads=statusline._loads)['connectors'][ORG_A]['Google Drive'], T0)
        self.assertIsNone(chatfile.load_index(os.path.join(self.dir, 'none.json')))
        for bad in ({'v': 2, 'chats': {}}, {'v': 1}, {'v': 1, 'chats': []}, ['v', 1]):
            self.assertIsNone(chatfile.load_index(self.save(bad)), bad)
        with open(p, 'w') as fh:
            fh.write('{"v": 1, "chats": {')
        self.assertIsNone(chatfile.load_index(p))

    def test_a_chat_is_carried_on_from_its_saved_entry(self):
        self.put(owner_line(), org_line(ORG_A, T0), org_line(ORG_B, T0 + 600))
        end = os.path.getsize(self.path)
        self.put(said('later', T0 + 800), org_line(ORG_C, T0 + 900), more=True)
        r = chatfile.resume_from(self.entry(end=end))
        self.assertEqual((r.owner, r.steps, r.end, r.cwd), (ORG_A, [(T0, ORG_A), (T0 + 600, ORG_B)], end, FOLDER))
        chatfile.read(self.path, start=r.end, into=r)
        fresh = chatfile.read(self.path)
        self.assertEqual((r.owner, r.steps, r.end), (fresh.owner, fresh.steps, fresh.end))

    def test_a_damaged_entry_carries_nothing_wrong(self):
        for bad in (None, 7, {}, {'owner': 5, 'steps': 'x', 'end': -3, 'cwd': 9},
                    {'steps': [[T0], ['t', ORG_A], [True, ORG_B], [T0, 5]], 'end': True}):
            r = chatfile.resume_from(bad)
            self.assertIsNone(r.owner)
            self.assertEqual(r.end, 0)
            self.assertIsNone(r.cwd)
            for t, org in r.steps:
                self.assertIn(org, (ORG_A, ORG_B))
                self.assertIsNone(t)


class Connectors(unittest.TestCase):
    def test_a_connectors_name_as_claude_code_says_it(self):
        self.assertEqual(chatfile.connector('mcp__claude_ai_Google_Drive__search'), 'Google Drive')
        self.assertEqual(chatfile.connector('mcp__claude_ai_Linear__list_issues'), 'Linear')
        self.assertEqual(chatfile.connector('mcp__claude_ai_Claude_Docs__batch'), 'Claude Docs')

    def test_other_tools_are_no_connector(self):
        for name in ('Artifact', 'mcp__claude-in-chrome__navigate', 'mcp__linear__list', 'mcp__claude_ai___x',
                     'mcp__claude_ai_', '', None, 7):
            self.assertIsNone(chatfile.connector(name), name)


class Light(unittest.TestCase):
    def test_it_imports_only_os_so_a_hook_can_use_it(self):
        with open(os.path.join(ROOT, 'switchboard', 'chatfile.py')) as fh:
            tree = ast.parse(fh.read())
        top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertEqual([a.name for n in top for a in n.names], ['os'])


if __name__ == '__main__':
    unittest.main()
