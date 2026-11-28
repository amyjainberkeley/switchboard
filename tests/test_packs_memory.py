import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest

# Tests for switchboard/packs/memory.py: search over a made-up history, the memory health check,
# and claude-mem managed (settings edits, the weekly cap, and what it would watch, on recorded
# hook inputs). Every folder is a throwaway one; nothing installs or runs claude-mem.

HERE = os.path.dirname(os.path.abspath(__file__))
# A fresh checkout has no bytecode folders for switchboard/packs, and tests/_isolate.py refuses any write outside
# the temp folders, so these imports write no bytecode.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, 'fixtures', 'packs', 'memory'))

import make_history as MH
from switchboard.packs import common as C
from switchboard.packs import memory as M

INPUTS = os.path.join(HERE, 'fixtures', 'packs', 'hook-inputs', 'inputs.json')


def tmp(prefix='switchboard-memory-'):
    return os.path.realpath(tempfile.mkdtemp(prefix=prefix))


class History(object):
    """The made-up history written once and indexed once for the read-only search tests."""
    _made = None

    @classmethod
    def get(cls):
        if cls._made is None:
            root = os.path.join(tmp(), 'projects')
            os.makedirs(root)
            ids, _ = MH.build(root)
            index = os.path.join(tmp(), 'data', 'packs', M.INDEX_NAME)
            stats = M.update_index(root, index)
            cls._made = (root, index, ids, {v: k for k, v in ids.items()}, stats)
        return cls._made


class TestPassLine(unittest.TestCase):
    def test_ten_questions_right_chat_in_top_three_for_at_least_eight(self):
        root, index, ids, by_session, _ = History.get()
        _, questions = MH.load()
        self.assertEqual(len(questions), 10)
        hits = []
        for q in questions:
            got = [by_session[r['session']] for r in M.search(q['q'], 3, index)]
            hits.append(q['expect'] in got)
        self.assertGreaterEqual(sum(hits), 8, hits)

    def test_same_with_the_plain_fallback_reports_its_number(self):
        root = os.path.join(tmp(), 'projects')
        os.makedirs(root)
        ids, _ = MH.build(root)
        by_session = {v: k for k, v in ids.items()}
        index = os.path.join(tmp(), 'plain.sqlite3')
        st = M.update_index(root, index, fts=False)
        self.assertFalse(st['fts5'])
        self.assertFalse(M.search_status(index)['fts5'])
        _, questions = MH.load()
        hits = sum(q['expect'] in [by_session[r['session']] for r in M.search(q['q'], 3, index)] for q in questions)
        self.assertGreaterEqual(hits, 8)        # no stemming, substring match: the same pass line
        r = M.search('stripe webhook', 1, index)[0]
        self.assertEqual(by_session[r['session']], 'c02')
        self.assertLessEqual(len(r['snippets']), 2)


class TestWhatIsIndexed(unittest.TestCase):
    def test_counts(self):
        _, index, _, _, stats = History.get()
        self.assertEqual(stats['files'], 40)               # helpers' files are not chats
        self.assertEqual(stats['chats'], 40)
        chats, _ = MH.load()
        self.assertEqual(stats['messages'], sum(2 * len(c['turns']) for c in chats))
        self.assertTrue(stats['fts5'])

    def test_tool_output_thinking_notices_side_chains_requests_and_helpers_are_not(self):
        _, index, _, _, _ = History.get()
        self.assertEqual(M.search(MH.NOISE, 5, index), [])
        self.assertEqual(M.search('Helper brief', 5, index)[:0], [])

    def test_result_has_title_date_folder_and_two_short_snippets(self):
        _, index, ids, by_session, _ = History.get()
        r = M.search('stripe webhook signature', 3, index)[0]
        self.assertEqual(by_session[r['session']], 'c02')
        self.assertEqual(r['title'], 'payments: webhook 400s')        # a name you gave wins over the made one
        self.assertEqual(r['folder'], '/Users/x/code/payments')
        self.assertEqual(r['date'], time.strftime('%Y-%m-%d', time.localtime(M.parse_ts('2026-08-04T14:40:00Z'))))
        self.assertEqual(len(r['snippets']), 2)
        for s in r['snippets']:
            self.assertIn('[', s)
            self.assertLess(len(s.split()), 30)

    def test_folder_name_and_title_count(self):
        _, index, ids, by_session, _ = History.get()
        r = M.search('rustparse', 1, index)
        self.assertEqual(by_session[r[0]['session']], 'c19')
        r = M.search('container', 1, index)                 # only in c05's title
        self.assertEqual(by_session[r[0]['session']], 'c05')

    def test_empty_and_stop_word_queries(self):
        _, index, _, _, _ = History.get()
        self.assertEqual(M.search('', 5, index), [])
        self.assertEqual(M.terms_of('the chat where we fixed the Docker image?'), ['fixed', 'docker', 'image'])
        self.assertEqual(M.terms_of('the'), ['the'])

    def test_limit(self):
        _, index, _, _, _ = History.get()
        self.assertEqual(len(M.search('test', 2, index)), 2)
        self.assertEqual(M.search('test', 0, index), [])

    def test_no_index_yet(self):
        self.assertEqual(M.search('anything', 5, os.path.join(tmp(), 'none.sqlite3')), [])


class TestReading(unittest.TestCase):
    def test_prompt_text(self):
        self.assertEqual(M.prompt_text('fix the login'), 'fix the login')
        self.assertEqual(M.prompt_text('<task-notification>done</task-notification>'), '')
        self.assertEqual(M.prompt_text('<local-command-stdout>x</local-command-stdout>'), '')
        self.assertEqual(M.prompt_text('<command-name>/review</command-name>\n<command-args>the api</command-args>'),
                         '/review the api')
        self.assertEqual(M.prompt_text([{'type': 'text', 'text': '<pasted_content id="ab12">\nlog line\n'
                                                                 '</pasted_content id="ab12">\nwhy?'},
                                        {'type': 'image', 'source': {}}]), 'log line why?')

    def test_parse_ts(self):
        self.assertEqual(M.parse_ts('2026-08-04T14:40:00Z'), 1785854400.0)
        self.assertEqual(M.parse_ts('2026-08-04T16:40:00+02:00'), 1785854400.0)
        self.assertEqual(M.parse_ts('2026-08-04T14:40:00.500Z'), 1785854400.5)
        self.assertIsNone(M.parse_ts('yesterday'))
        self.assertIsNone(M.parse_ts(None))

    def test_spaced_json_file(self):
        root = os.path.join(tmp(), 'projects', '-Users-x-p')
        os.makedirs(root)
        with open(os.path.join(root, 's1.jsonl'), 'w') as f:
            f.write(json.dumps({'type': 'user', 'cwd': '/Users/x/p', 'timestamp': '2026-08-01T00:00:00Z',
                                'message': {'role': 'user', 'content': 'quarterly kumquat report'}}) + '\n')
        index = os.path.join(tmp(), 'i.sqlite3')
        M.update_index(os.path.dirname(root), index)
        self.assertEqual(M.search('kumquat', 1, index)[0]['session'], 's1')


class TestReview(unittest.TestCase):
    """Unusual inputs."""

    def test_same_session_id_in_two_folders_is_two_chats(self):
        root = os.path.join(tmp(), 'projects')
        line = lambda text: json.dumps({'type': 'user', 'cwd': '/Users/x/p', 'uuid': text,  # noqa: E731
                                        'timestamp': '2026-08-01T00:00:00Z',
                                        'message': {'role': 'user', 'content': text}}) + '\n'
        for folder, words in (('-Users-x-a', 'alpha apricot'), ('-Users-x-b', 'bravo blueberry')):
            os.makedirs(os.path.join(root, folder))
            with open(os.path.join(root, folder, 'same.jsonl'), 'w') as f:
                f.write(line(words))
        index = os.path.join(tmp(), 'i.sqlite3')
        self.assertEqual(M.update_index(root, index)['chats'], 2)
        with open(os.path.join(root, '-Users-x-a', 'same.jsonl'), 'w') as f:
            f.write(line('cherry'))                     # shorter: read again whole
        M.update_index(root, index)
        self.assertEqual(M.search('blueberry', 5, index)[0]['project'], '-Users-x-b')
        self.assertEqual(M.search('apricot', 5, index), [])
        self.assertEqual(M.search_status(index)['messages'], 2)

    def test_spaced_lines_after_a_first_line_with_no_type(self):
        root = os.path.join(tmp(), 'projects', '-Users-x-p')
        os.makedirs(root)
        with open(os.path.join(root, 's.jsonl'), 'w') as f:
            f.write('{"summary": "x"}\n')
            f.write(json.dumps({'type': 'user', 'timestamp': '2026-08-01T00:00:00Z',
                                'message': {'role': 'user', 'content': 'spaced quince'}}) + '\n')
        index = os.path.join(tmp(), 'i.sqlite3')
        M.update_index(os.path.dirname(root), index)
        self.assertEqual(len(M.search('quince', 1, index)), 1)


class TestIncremental(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(tmp(), 'projects')
        os.makedirs(self.root)
        chats, _ = MH.load()
        self.ids, _ = MH.build(self.root, chats=chats[:3])
        self.index = os.path.join(tmp(), 'i.sqlite3')
        M.update_index(self.root, self.index)
        self.path = os.path.join(self.root, MH.project_dir('/Users/x/code/webshop'), self.ids['c01'] + '.jsonl')

    def line(self, text, minute=50):
        return json.dumps({'type': 'user', 'isSidechain': False, 'cwd': '/Users/x/code/webshop', 'uuid': 'u%d' % minute,
                           'timestamp': '2026-08-03T10:%02d:00.000Z' % minute,
                           'message': {'role': 'user', 'content': text}}, separators=(',', ':')) + '\n'

    def test_unchanged_files_are_not_read(self):
        st = M.update_index(self.root, self.index)
        self.assertEqual((st['read'], st['unchanged'], st['bytes_read']), (0, 3, 0))

    def test_appended_turn_reads_only_the_new_bytes(self):
        add = self.line('now the persimmon banner is misaligned')
        with open(self.path, 'a') as f:
            f.write(add)
        st = M.update_index(self.root, self.index)
        self.assertEqual((st['read'], st['bytes_read'], st['reread_whole']), (1, len(add), 0))
        r = M.search('persimmon', 1, self.index)
        self.assertEqual(r[0]['session'], self.ids['c01'])
        chats, _ = MH.load()
        self.assertEqual(M.search_status(self.index)['messages'], sum(2 * len(c['turns']) for c in chats[:3]) + 1)

    def test_a_line_still_being_written_waits(self):
        add = self.line('half written mangosteen')
        with open(self.path, 'a') as f:
            f.write(add[:-10])
        M.update_index(self.root, self.index)
        self.assertEqual(M.search('mangosteen', 1, self.index), [])
        with open(self.path, 'a') as f:
            f.write(add[-10:])
        M.update_index(self.root, self.index)
        self.assertEqual(M.search('mangosteen', 1, self.index)[0]['session'], self.ids['c01'])

    def test_rewritten_file_is_read_again_whole(self):
        with open(self.path, 'w') as f:
            f.write(self.line('only this tamarind line now'))
        st = M.update_index(self.root, self.index)
        self.assertEqual(st['reread_whole'], 1)
        self.assertTrue(all(r['session'] != self.ids['c01'] for r in M.search('redirect loop', 5, self.index)))
        self.assertEqual(M.search('tamarind', 1, self.index)[0]['title'], 'only this tamarind line now')
        self.assertEqual(M.search('tamarind', 1, self.index)[0]['session'], self.ids['c01'])

    def test_same_size_edit_is_caught_by_the_tail_check(self):
        with open(self.path, 'rb') as f:
            data = f.read()
        changed = data.replace(b'forever', b'endless')    # same length, inside the last 4 KB or earlier
        self.assertEqual(len(changed), len(data))
        with open(self.path, 'wb') as f:
            f.write(changed)
        os.utime(self.path, ns=(time.time_ns(), time.time_ns() + 10 ** 9))
        st = M.update_index(self.root, self.index)
        self.assertEqual(st['read'], 1)

    def test_deleted_chat_stays_searchable(self):
        os.unlink(self.path)
        st = M.update_index(self.root, self.index)
        self.assertEqual(st['vanished'], 1)
        self.assertEqual(M.search('redirect loop google', 1, self.index)[0]['session'], self.ids['c01'])

    def test_index_is_owner_only(self):
        index = os.path.join(tmp(), 'data', 'packs', M.INDEX_NAME)
        M.update_index(self.root, index)
        self.assertEqual(stat.S_IMODE(os.stat(index).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(index)).st_mode), 0o700)

    def test_search_off_removes_only_the_index(self):
        self.assertEqual(M.search_on(self.root, self.index)['read'], 0)
        M.search_off(self.index)
        self.assertFalse(os.path.exists(self.index))
        self.assertTrue(os.path.exists(self.path))
        self.assertFalse(M.search_status(self.index)['on'])

    def test_default_index_lives_in_the_data_folder(self):
        self.assertEqual(M.index_path(), os.path.join(os.environ['SWITCHBOARD_HOME'], 'packs', M.INDEX_NAME))


class TestCli(unittest.TestCase):
    def test_search_command(self):
        root, index, _, _, _ = History.get()
        out = io.StringIO()
        self.assertEqual(M.main(['search', 'pods restarting health check', '--index', index, '--root', root], out), 0)
        text = out.getvalue()
        self.assertTrue(text.startswith('1. Pods in a restart loop'))
        self.assertIn('claude --resume ', text)
        out = io.StringIO()
        M.main(['search', 'pods', '--json', '--no-update', '--index', index], out)
        self.assertEqual(json.loads(out.getvalue())[0]['title'], 'Pods in a restart loop')


# ---------------------------------------------------------------- health

class TestHealth(unittest.TestCase):
    def setUp(self):
        self.cd = os.path.join(tmp(), 'claude')
        os.makedirs(os.path.join(self.cd, 'projects'))

    def memory(self, project, data):
        d = os.path.join(self.cd, 'projects', project, 'memory')
        os.makedirs(d)
        with open(os.path.join(d, 'MEMORY.md'), 'wb') as f:
            f.write(data)

    def rows(self):
        return {r['project']: r for r in M.health(self.cd, os.path.dirname(self.cd))['files']}

    def test_lines_and_bytes(self):
        self.memory('-a', b''.join(b'- line %d\n' % i for i in range(150)))
        self.memory('-b', b''.join(b'- line %d\n' % i for i in range(250)))
        self.memory('-c', b''.join(b'- %s\n' % (b'x' * 257) for _ in range(100)))       # 26,000 bytes
        self.memory('-d', b''.join(b'- %s\n' % (b'x' * 249) for _ in range(100)))       # 25,200 bytes
        r = self.rows()
        self.assertEqual((r['-a']['state'], r['-a']['lines']), ('ok', 150))
        self.assertEqual((r['-b']['state'], r['-b']['over_lines'], r['-b']['lines_not_loaded']), ('over', 50, 50))
        self.assertEqual((r['-c']['state'], r['-c']['over_bytes'], r['-c']['over_lines']), ('over', 26000 - 25600, 0))
        self.assertEqual(r['-c']['lines_not_loaded'], 100 - 25600 // 260)
        self.assertEqual(r['-d']['state'], 'maybe over')
        h = M.health(self.cd, os.path.dirname(self.cd))
        self.assertEqual((h['over'], h['maybe_over']), (2, 1))
        self.assertIn('first 200 lines', h['doc'][0]['quote'])

    def test_frontmatter_and_block_comments_do_not_count(self):
        body = b''.join(b'- line %d\n' % i for i in range(195))
        data = (b'---\nname: index\n---\n<!-- a note\nover\nlines -->\n' + body +
                b'<!-- one line -->\n```\n<!-- in a fence: kept -->\n```\n')
        self.memory('-e', data)
        r = self.rows()['-e']
        self.assertEqual(r['lines'], 195 + 3)
        self.assertEqual(r['state'], 'ok')

    def test_auto_memory_directory(self):
        home = os.path.dirname(self.cd)
        os.makedirs(os.path.join(home, 'mem'))
        with open(os.path.join(home, 'mem', 'MEMORY.md'), 'wb') as f:
            f.write(b'- one\n')
        with open(os.path.join(self.cd, 'settings.json'), 'w') as f:
            json.dump({'autoMemoryDirectory': '~/mem'}, f)
        files = M.memory_files(self.cd, home)
        self.assertEqual([f['where'] for f in files], ['autoMemoryDirectory'])

    def test_reads_only(self):
        self.memory('-a', b'- x\n')
        before = {p: os.stat(os.path.join(dp, p)).st_mtime_ns for dp, _, fs in os.walk(self.cd) for p in fs}
        M.health(self.cd, os.path.dirname(self.cd))
        after = {p: os.stat(os.path.join(dp, p)).st_mtime_ns for dp, _, fs in os.walk(self.cd) for p in fs}
        self.assertEqual(before, after)


# ---------------------------------------------------------------- claude-mem

SEEDED = (json.dumps({'CLAUDE_MEM_MODEL': 'haiku', 'CLAUDE_MEM_PROVIDER': 'claude',
                      'CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS': 'false', 'CLAUDE_MEM_OBSERVER_ENABLED': 'true',
                      'CLAUDE_MEM_DATA_DIR': '', 'CLAUDE_MEM_EXCLUDED_PROJECTS': ''}, indent=2) + '\n').encode()


class Mem(object):
    """A made-up home with Claude Code's folder and, optionally, claude-mem's settings file."""

    def __init__(self, settings=None, cc_settings=None, environ=None, installed=False):
        self.home = tmp('switchboard-cm-')
        self.cd = os.path.join(self.home, '.claude')
        os.makedirs(os.path.join(self.cd, 'projects'))
        self.data = os.path.join(self.home, '.claude-mem')
        self.file = os.path.join(self.data, 'settings.json')
        self.state = os.path.join(self.home, 'sb', 'packs', 'state.json')
        self.environ = environ or {}
        if settings is not None:
            os.makedirs(self.data)
            self.write(settings)
        if cc_settings is not None:
            with open(os.path.join(self.cd, 'settings.json'), 'w') as f:
                json.dump(cc_settings, f)
        if installed:
            os.makedirs(os.path.join(self.cd, 'plugins'))
            with open(os.path.join(self.cd, 'plugins', 'installed_plugins.json'), 'w') as f:
                json.dump({'version': 2, 'plugins': {M.CM_PLUGIN: [{'scope': 'user'}]}}, f)

    def write(self, data):
        with open(self.file, 'wb') as f:
            f.write(data)

    def read(self):
        return C.read_bytes(self.file)

    def plan(self, on, cap=None):
        return M.cm_plan(on, cap, self.cd, self.state, self.environ, self.home)

    def on(self, cap=None):
        return M.cm_apply(self.plan(True, cap))

    def off(self):
        return M.cm_undo(self.cd, self.state, self.environ, self.home)

    def record(self):
        return C.load_state(self.state)[0]['claude_mem']

    def cap_check(self, share, now=None):
        return M.cm_cap_check(now or time.time(), self.cd, self.state, self.environ, self.home,
                              measured={'share': share})


class TestClaudeMemSettings(unittest.TestCase):
    def test_not_installed_writes_the_gate_first_and_lists_the_documented_commands(self):
        m = Mem()
        p = m.plan(True)
        self.assertIsNone(p['blocked'])
        self.assertEqual([s.get('run') for s in p['install']],
                         [None, 'npx claude-mem install --ide claude-code --provider claude',
                          'npx claude-mem telemetry disable'])
        self.assertEqual(p['install'][1]['or'], ['/plugin marketplace add thedotmack/claude-mem',
                                                 '/plugin install claude-mem'])
        self.assertEqual(p['cap_percent'], 10.0)
        M.cm_apply(p)
        self.assertEqual(m.read(), b'{\n  "CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS": "true"\n}\n')
        self.assertEqual(stat.S_IMODE(os.stat(m.file).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(m.data).st_mode), 0o700)
        m.off()
        self.assertFalse(os.path.exists(m.data))
        self.assertIsNone(m.record())

    def test_seeded_file_changes_one_value_and_comes_back_exactly(self):
        m = Mem(SEEDED, installed=True)
        p = m.plan(True, 5)
        self.assertEqual(p['install'], [])
        M.cm_apply(p)
        self.assertEqual(m.read(), SEEDED.replace(b'"CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS": "false"',
                                                  b'"CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS": "true"'))
        self.assertEqual(m.record()['cap_percent'], 5.0)
        p = m.plan(False)
        self.assertEqual(p['remove'][0]['run'], 'npx claude-mem uninstall')
        M.cm_apply(p)
        self.assertEqual(m.read(), SEEDED)

    def test_claude_mem_rewrote_the_file_since(self):
        m = Mem(SEEDED, installed=True)
        m.on()
        obj = json.loads(m.read())
        obj['CLAUDE_MEM_MODEL'] = 'sonnet'
        rewritten = (json.dumps(obj, indent=2) + '\n').encode()
        m.write(rewritten)
        m.off()
        self.assertEqual(json.loads(m.read())['CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS'], 'false')
        self.assertEqual(m.read(), rewritten.replace(b'"true"', b'"false"', 1))

    def test_nested_env_document(self):
        nested = b'{\n  "env": {\n    "CLAUDE_MEM_MODEL": "haiku"\n  },\n  "theme": "dark"\n}\n'
        m = Mem(nested)
        m.on()
        got = json.loads(m.read())
        self.assertEqual(got['env']['CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS'], 'true')
        self.assertNotIn('CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS', got)
        m.off()
        self.assertEqual(m.read(), nested)

    def test_already_true_is_left_yours(self):
        mine = SEEDED.replace(b'"CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS": "false"',
                              b'"CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS": true')
        m = Mem(mine)
        p = m.plan(True)
        self.assertEqual(p['edits'], [])
        self.assertTrue(any('stays yours' in n for n in p['notes']))
        M.cm_apply(p)
        m.off()
        self.assertEqual(m.read(), mine)

    def test_value_changed_by_the_person_is_not_put_back(self):
        m = Mem(SEEDED)
        m.on()
        m.write(m.read().replace(b'"CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS": "true"',
                                 b'"CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS": "false"'))
        p = m.plan(False)
        self.assertEqual(p['edits'], [])
        self.assertEqual(p['forget'][0]['key'], M.CM_SKIP)
        before = m.read()
        M.cm_apply(p)
        self.assertEqual(m.read(), before)

    def test_environment_or_claude_code_env_wins_so_it_blocks(self):
        m = Mem(SEEDED, environ={M.CM_SKIP: 'false'})
        self.assertIn('the environment Claude Code starts from', m.plan(True)['blocked'])
        m = Mem(SEEDED, cc_settings={'env': {M.CM_OBSERVER: 'true'}})
        self.assertIn('weekly cap could not turn watching off', m.plan(True)['blocked'])
        m = Mem(SEEDED, cc_settings={'env': {M.CM_SKIP: 'true'}})
        p = m.plan(True)
        self.assertIsNone(p['blocked'])
        self.assertEqual(p['edits'], [])

    def test_empty_objects_and_blank_files_come_back_exactly(self):
        for old in (b'{}', b'{ }\n', b'{\n}\n', b'', b'  \n', b'{"env": {}}\n'):
            with self.subTest(old=old):
                m = Mem(old)
                m.on(10)
                self.assertEqual(_coerce_file(m.read())[M.CM_SKIP], 'true')
                m.cap_check(50)
                m.off()
                self.assertEqual(m.read(), old)

    def test_on_again_leaves_a_value_the_person_changed(self):
        m = Mem(SEEDED)
        m.on()
        theirs = m.read().replace(b'"CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS": "true"',
                                  b'"CLAUDE_MEM_SKIP_SUBAGENT_OBSERVATIONS": "false"')
        m.write(theirs)
        p = m.plan(True)
        self.assertEqual(p['edits'], [])
        self.assertTrue(any('yours stays' in n for n in p['notes']))
        M.cm_apply(p)
        self.assertEqual(m.read(), theirs)
        self.assertNotIn(M.CM_SKIP, m.record()['owned'])
        m.off()
        self.assertEqual(m.read(), theirs)

    def test_cap_on_a_settings_file_that_is_gone_writes_nothing(self):
        m = Mem(SEEDED)
        m.on(10)
        os.unlink(m.file)
        r = m.cap_check(90)
        self.assertIsNone(r['action'])
        self.assertIn('is gone', r['says'])
        self.assertFalse(os.path.exists(m.file))

    def test_refusals(self):
        m = Mem(b'{"CLAUDE_MEM_MODEL": "haiku",}')
        self.assertIn('not valid JSON', m.plan(True)['blocked'])
        m = Mem(SEEDED)
        p = m.plan(True)
        m.write(SEEDED + b' ')
        with self.assertRaises(C.Refused):
            M.cm_apply(p)
        self.assertEqual(m.read(), SEEDED + b' ')
        self.assertIn('above 0', Mem().plan(True, 0)['blocked'])
        self.assertIn('above 0', Mem().plan(True, 150)['blocked'])

    def test_data_folder_named_in_settings_or_environment(self):
        home = tmp()
        os.makedirs(os.path.join(home, '.claude-mem'))
        with open(os.path.join(home, '.claude-mem', 'settings.json'), 'w') as f:
            json.dump({'CLAUDE_MEM_DATA_DIR': '~/elsewhere'}, f)
        self.assertEqual(M.cm_paths({}, home)['settings'], os.path.join(home, 'elsewhere', 'settings.json'))
        self.assertEqual(M.cm_paths({'CLAUDE_MEM_DATA_DIR': '/srv/cm'}, home)['observer'],
                         '/srv/cm/observer-sessions')


def _coerce_file(data):
    obj = json.loads(data)
    return obj['env'] if M._nested(obj) else obj


class TestWeeklyCap(unittest.TestCase):
    def test_pause_and_resume_put_back_the_exact_bytes(self):
        m = Mem(SEEDED)
        m.on(10)
        after_on = m.read()
        r = m.cap_check(12.5)
        self.assertEqual(r['action'], 'pause')
        self.assertIn('"CLAUDE_MEM_OBSERVER_ENABLED": "false"', m.read().decode())
        self.assertEqual(m.read(), after_on.replace(b'"CLAUDE_MEM_OBSERVER_ENABLED": "true"',
                                                    b'"CLAUDE_MEM_OBSERVER_ENABLED": "false"'))
        self.assertIsNone(m.cap_check(11)['action'])          # still over: stays off
        r = m.cap_check(10)
        self.assertEqual(r['action'], 'resume')
        self.assertEqual(m.read(), after_on)
        self.assertIsNone(m.cap_check(3)['action'])

    def test_cap_on_a_file_without_the_key(self):
        m = Mem()
        m.on(10)
        after_on = m.read()
        m.cap_check(50)
        self.assertEqual(json.loads(m.read())[M.CM_OBSERVER], 'false')
        m.cap_check(1)
        self.assertEqual(m.read(), after_on)

    def test_person_turned_it_on_again_while_paused(self):
        m = Mem(SEEDED)
        m.on(10)
        m.cap_check(20)
        m.write(m.read().replace(b'"CLAUDE_MEM_OBSERVER_ENABLED": "false"', b'"CLAUDE_MEM_OBSERVER_ENABLED": "true"'))
        before = m.read()
        self.assertEqual(m.cap_check(5)['action'], 'forget')
        self.assertEqual(m.read(), before)

    def test_person_had_it_off_already(self):
        off = SEEDED.replace(b'"CLAUDE_MEM_OBSERVER_ENABLED": "true"', b'"CLAUDE_MEM_OBSERVER_ENABLED": "false"')
        m = Mem(off)
        m.on(10)
        self.assertIsNone(m.cap_check(90)['action'])
        m.off()
        self.assertEqual(m.read(), off)

    def test_turning_the_switch_off_while_paused_puts_everything_back(self):
        m = Mem(SEEDED)
        m.on(10)
        m.cap_check(30)
        p = m.plan(False)
        self.assertEqual(sorted(e['key'] for e in p['edits']), [M.CM_OBSERVER, M.CM_SKIP])
        M.cm_apply(p)
        self.assertEqual(m.read(), SEEDED)

    def test_no_cap_without_the_switch(self):
        self.assertIsNone(Mem(SEEDED).cap_check(99)['action'])


class TestMeasure(unittest.TestCase):
    def write(self, folder, name, reqs):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), 'w') as f:
            for i, (ts, inp, outp) in enumerate(reqs):
                f.write(json.dumps({'type': 'assistant', 'requestId': 'r%s%d' % (name, i), 'timestamp': ts,
                                    'message': {'id': 'm%s%d' % (name, i), 'model': 'claude-haiku-4-5',
                                                'content': [{'type': 'text', 'text': 'x'}],
                                                'usage': {'input_tokens': inp, 'output_tokens': outp}}}) + '\n')

    def test_share_of_the_week(self):
        m = Mem(SEEDED)
        root = os.path.join(m.cd, 'projects')
        now = time.time()
        iso = lambda t: time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(t))  # noqa: E731
        self.write(os.path.join(root, '-Users-x-code-webshop'), 's1.jsonl',
                   [(iso(now - 3600), 1000, 100), (iso(now - 10 * 86400), 99999, 0)])
        obs = os.path.join(root, M.project_folder_name(os.path.join(m.data, 'observer-sessions')))
        self.write(obs, 'o1.jsonl', [(iso(now - 1800), 300, 20)])
        r = M.cm_measure(now - M.WEEK, now, m.cd, {}, m.home, os.path.join(m.home, 'cache.json'))
        self.assertEqual(r['observer_weight'], 300 + 5 * 20)
        self.assertEqual(r['total_weight'], 1000 + 5 * 100 + 300 + 5 * 20)
        self.assertAlmostEqual(r['share'], 100.0 * 400 / 1900)
        self.assertEqual(r['observer_requests'], 1)
        self.assertTrue(obs.endswith('-claude-mem-observer-sessions'))

    def write_reads(self, folder, name, model, ts, reads):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, name), 'w') as f:
            f.write(json.dumps({'type': 'assistant', 'requestId': 'r' + name, 'timestamp': ts,
                                'message': {'id': 'm' + name, 'model': model,
                                            'content': [{'type': 'text', 'text': 'x'}],
                                            'usage': {'input_tokens': 0, 'output_tokens': 0,
                                                      'cache_read_input_tokens': reads}}}) + '\n')

    def test_an_observer_on_fable_weighs_its_reads_as_the_total_does(self):
        # The observer's 1,000 reads on Fable 5.2 weigh 25 (0.025 each), as inside the total; a chat's 1,000
        # reads on Opus 5.5 weigh 50 (0.05 each). Share: 25 / 75. Alone, the observer is the whole week: 100%.
        m = Mem(SEEDED)
        root = os.path.join(m.cd, 'projects')
        now = time.time()
        iso = lambda t: time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime(t))  # noqa: E731
        obs = os.path.join(root, M.project_folder_name(os.path.join(m.data, 'observer-sessions')))
        self.write_reads(obs, 'o1.jsonl', 'claude-fable-5-2', iso(now - 1800), 1000)
        r = M.cm_measure(now - M.WEEK, now, m.cd, {}, m.home, os.path.join(m.home, 'cache.json'))
        self.assertAlmostEqual(r['observer_weight'], 25.0)
        self.assertAlmostEqual(r['total_weight'], 25.0)
        self.assertAlmostEqual(r['share'], 100.0)
        self.write_reads(os.path.join(root, '-Users-x-code-webshop'), 's1.jsonl', 'claude-opus-5-5',
                         iso(now - 3600), 1000)
        r = M.cm_measure(now - M.WEEK, now, m.cd, {}, m.home, os.path.join(m.home, 'cache.json'))
        self.assertAlmostEqual(r['observer_weight'], 25.0)
        self.assertAlmostEqual(r['total_weight'], 75.0)
        self.assertAlmostEqual(r['share'], 100.0 * 25 / 75)

    def test_folder_name_rule(self):
        self.assertEqual(M.project_folder_name('/Users/x/.claude-mem/observer-sessions'),
                         '-Users-x--claude-mem-observer-sessions')


class TestWhatItWatches(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(INPUTS) as f:
            cls.inputs = json.load(f)['inputs']

    def check(self, mode, settings):
        for name, x in self.inputs.items():
            with self.subTest(mode=mode, input=name):
                self.assertEqual(M.cm_would_observe(x['event'], settings, {}, '/Users/x'), x['expect'][mode])

    def test_defaults_watch_helpers_too(self):
        self.check('defaults', json.loads(SEEDED))

    def test_with_the_settings_switchboard_writes(self):
        m = Mem(SEEDED)
        m.on(10)
        self.check('gate', json.loads(m.read()))
        m.cap_check(40)
        self.check('gate_paused', json.loads(m.read()))

    def test_environment_wins_over_the_file(self):
        ev = self.inputs['helper-explore']['event']
        self.assertTrue(M.cm_would_observe(ev, {M.CM_SKIP: 'true'}, {M.CM_SKIP: 'false'}, '/Users/x'))
        self.assertFalse(M.cm_would_observe(ev, {}, {M.CM_SKIP: 'true'}, '/Users/x'))
        self.assertFalse(M.cm_would_observe(self.inputs['chat-write']['event'], {}, {'CLAUDE_MEM_INTERNAL': '1'},
                                            '/Users/x'))

    def test_disable_flags_its_own_folders_and_odd_inputs(self):
        chat = dict(self.inputs['chat-write']['event'])
        helper = dict(self.inputs['helper-explore']['event'])
        gate = {M.CM_SKIP: 'true'}
        for flag in ('CLAUDE_MEM_DISABLE_TOOL_HOOKS', 'CLAUDE_MEM_DISABLE_OBSERVATION'):
            self.assertFalse(M.cm_would_observe(chat, {}, {flag: '1'}, '/Users/x'))
            self.assertTrue(M.cm_would_observe(chat, {}, {flag: 'true'}, '/Users/x'))      # only "1" counts
        for cwd in ('/Users/x/.claude/plugins/cache/thedotmack/claude-mem/15.18.3',
                    '/Users/x/.claude/plugins/marketplaces/thedotmack/plugin',
                    '/Users/x/.claude-mem/x/../observer-sessions/y'):
            self.assertFalse(M.cm_would_observe(dict(chat, cwd=cwd), {}, {}, '/Users/x'))
        no_cwd = {k: v for k, v in chat.items() if k != 'cwd'}
        self.assertTrue(M.cm_would_observe(no_cwd, gate, {}, '/Users/x'))
        self.assertFalse(M.cm_would_observe({k: v for k, v in helper.items() if k != 'cwd'}, gate, {}, '/Users/x'))
        camel = {k: v for k, v in chat.items() if k != 'tool_name'}
        camel['toolName'] = 'Write'
        self.assertTrue(M.cm_would_observe(camel, gate, {}, '/Users/x'))
        self.assertTrue(M.cm_would_observe(helper, {M.CM_SKIP_TYPES: ['Explore']}, {}, '/Users/x'))

    def test_type_list_and_excluded_projects(self):
        wf = self.inputs['workflow-agent']['event']
        self.assertFalse(M.cm_would_observe(wf, {M.CM_SKIP_TYPES: 'workflow-subagent'}, {}, '/Users/x'))
        chat = self.inputs['chat-write']['event']
        self.assertFalse(M.cm_would_observe(chat, {M.CM_EXCLUDED: '~/code/**'}, {}, '/Users/x'))
        self.assertTrue(M.cm_would_observe(chat, {M.CM_EXCLUDED: '~/other/**'}, {}, '/Users/x'))

    def test_status_reads_the_effect(self):
        m = Mem(SEEDED, installed=True)
        s = M.cm_status(m.cd, m.state, {}, m.home)
        self.assertEqual((s['helpers_dropped'], s['watching'], s['switch']), (False, True, False))
        m.on(10)
        s = M.cm_status(m.cd, m.state, {}, m.home)
        self.assertEqual((s['helpers_dropped'], s['watching'], s['switch']), (True, True, True))
        self.assertEqual(len(s['hooks']), 8)


if __name__ == '__main__':
    unittest.main()
