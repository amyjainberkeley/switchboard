import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""Behind the page: the session hooks note each publish.

Each artifact a session publishes, creates or updates, and each Claude Doc it makes, is one line in
hooks/published.jsonl the moment Claude Code answers: link and id, the local file the answer named, the time and the
session. Without a word to the session, well under a second, no Keychain and no network, never for a session on its
own login, and nothing for any other answer or for junk. Lines from sessions at the same moment never mix; the record
moves aside by size and by age, and its older half goes a week after its last line.

Recorded hook inputs follow Claude Code 2.1.309's own shapes (tests/fakes_artifacts.py)."""
import io
import json
import os
import stat
import subprocess
import sys
import time
import unittest
from unittest import mock

from fakes_links import Home
from fakes_artifacts import ID1, ID2, ID3, ID4, LINK, SESSION, create, doc, event, publish, update, written

from switchboard import session_hooks

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IDS = ['%s%02dAbCdEfGhIjKlMnOp' % (c, i) for i, c in enumerate('QRSTUVWXYZ')]


class HookCase(Home):
    """A fresh home, and one way to hand the hook its input as Claude Code does."""

    def run_hook(self, ev, env=None, now=None):
        out = io.StringIO()
        raw = ev if isinstance(ev, bytes) else json.dumps(ev).encode()
        code = session_hooks.hook(stdin=io.BytesIO(raw), env=env or {}, now=now or time.time(), out=out)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), '')            # a publish is noted without a word to the session
        return code

    def path(self):
        return os.path.join(self.data, 'hooks', 'published.jsonl')

    def lines(self, path=None):
        try:
            with open(path or self.path()) as f:
                return [json.loads(x) for x in f if x.strip()]
        except FileNotFoundError:
            return []


class TheHookNotesEachPublish(HookCase):
    def test_a_publish_an_update_a_create_and_a_doc_are_one_line_each(self):
        now = time.time()
        for ev in (publish(), update(), create(), doc()):
            self.run_hook(ev, now=now)
        got = self.lines()
        self.assertEqual([(x['id'], x['how']) for x in got],
                         [(ID1, 'published'), (ID2, 'updated'), (ID3, 'created'), (ID4, 'doc')])
        first = got[0]
        self.assertEqual(sorted(first), ['file', 'how', 'id', 'sid', 't', 'title', 'url', 'v'])
        self.assertEqual((first['v'], first['url'], first['file'], first['title'], first['sid']),
                         (1, LINK % ID1, '/Users/maya/site/launch/index.html', 'Launch board', SESSION))
        self.assertAlmostEqual(first['t'], now, places=2)
        self.assertEqual(got[1]['file'], '/Users/maya/site/plan.html')
        self.assertEqual((got[2]['file'], got[2]['title']), (None, 'Q4 review'))       # made from a type: no file yet
        self.assertEqual((got[3]['file'], got[3]['title']), (None, 'Hiring plan'))
        self.assertEqual(stat.S_IMODE(os.stat(self.path()).st_mode), 0o600)              # only you can read it
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path())).st_mode), 0o700)

    def test_an_answer_written_out_as_the_chat_keeps_it_is_read_too(self):
        self.run_hook(written('Published /tmp/notes/index.html at %s (version 3)\n\nLive subscription: arming in the '
                              'background' % (LINK % ID1)))
        self.run_hook(written('Created a new Artifact at %s (version 1) from the Artifact type https://claude.ai/'
                              'artifact/TypeSlidesAAAAAAAAAAA.' % (LINK % ID3),
                              {'type_url': 'https://claude.ai/artifact/TypeSlidesAAAAAAAAAAA'}))
        self.run_hook(written('Updated the Artifact at %s (version 2) with /tmp/a.html (and any `files` listed); own '
                              'files not sent this time were kept.' % (LINK % ID2), {'url': LINK % ID2}))
        self.run_hook(event('Artifact', {'file_path': 'notes.html'}, 'Published notes.html at %s' % (LINK % ID4),
                            cwd='/Users/maya/work'))
        got = self.lines()
        self.assertEqual([(x['id'], x['how'], x['file']) for x in got],
                         [(ID1, 'published', '/tmp/notes/index.html'), (ID3, 'created', None), (ID2, 'updated', None),
                          (ID4, 'published', '/Users/maya/work/notes.html')])       # a relative file, from its folder

    def test_any_other_answer_writes_nothing(self):
        refused = dict(publish(), hook_event_name='PostToolUseFailure', error='Refused: the file is too large')
        for ev in (event('Artifact', {'action': 'read', 'url': LINK % ID1},
                         {'url': LINK % ID1, 'content': 'Published x.html at %s' % (LINK % ID2)}),
                   event('Artifact', {'action': 'list'}, '3 published artifacts, newest first'),
                   event('Artifact', {'url': LINK % ID1, 'file_path': '/tmp/logo.png', 'asset': True},
                         {'url': LINK % ID1, 'path': '/tmp/logo.png'}),
                   event('Artifact', {'file_path': '/tmp/a.html'}, {'isError': True, 'url': LINK % ID1}),
                   event('ArtifactComments', {'url': LINK % ID1}, {'url': LINK % ID1}),
                   event('mcp__claude_ai_Claude_Docs__read', {'container': {'id': ID4}},
                         [{'type': 'text', 'text': '{"verdict":"ok","created":{"frame":{"slug":"x","url":"%s"}}}'
                           % (LINK % ID4)}]),
                   event('mcp__claude_ai_Claude_Docs__batch', {'container': {'kind': 'project', 'id': ID4}},
                         [{'type': 'text', 'text': '{"verdict":"ok","applied":3}'}]),
                   event('Bash', {'command': 'ls'}, {'stdout': 'Published x.html at %s' % (LINK % ID1)}),
                   refused):
            with self.subTest(tool=ev['tool_name'], inp=str(ev['tool_input'])[:40]):
                self.run_hook(ev)
        self.assertEqual(self.lines(), [])

    def test_junk_writes_nothing_or_leaves_the_junk_out(self):
        for ev in (event('Artifact', {}, {'url': 'https://claude.ai.example/artifact/' + ID1}),
                   event('Artifact', {}, {'url': 'http://claude.ai/artifact/' + ID1}),
                   event('Artifact', {}, {'url': 'https://claude.ai@evil.example/artifact/' + ID1}),
                   event('Artifact', {}, {'url': 'https://claude.ai/artifact/short'}),
                   event('Artifact', {}, {'url': 'https://claude.ai/artifact/../../etc/passwd'}),
                   event('Artifact', {}, {'url': LINK % ('x' * 400)}),
                   event('Artifact', {}, {'url': 5, 'path': ['x'], 'artifact_id': '../x'}),
                   event('Artifact', 'not a dict', 'Published  at nothing'),
                   event('Artifact', {}, None),
                   event('Artifact', {}, {'deep': [[[['Published /a at https://claude.ai.example/artifact/' + ID1]]]]}),
                   event(['Artifact'], {}, {'url': LINK % ID1})):
            with self.subTest(resp=str(ev['tool_response'])[:50]):
                self.run_hook(ev)
        for raw in (b'{"hook_event_name":"PostToolUse","tool_name":"Artifact","tool_response":', b'\xff\xfe\x00',
                    json.dumps(publish()).encode()[:-10]):
            self.run_hook(raw)
        self.assertEqual(self.lines(), [])
        self.run_hook(event('Artifact', {}, {'url': LINK % ID1, 'path': '/tmp/a\x00b.html',
                                             'title': 'Ti\x1b[31mtle ' + 'x' * 500}, sid='../../etc'))
        self.run_hook(event('Artifact', {}, {'url': LINK % ID2, 'path': 'rel/x.html'}, cwd='relative/cwd'))
        self.run_hook(event('Artifact', {}, {'url': LINK % ID3, 'path': '/' + 'p' * 2000}))
        got = self.lines()
        self.assertEqual([(x['id'], x['file']) for x in got], [(ID1, None), (ID2, None), (ID3, None)])
        self.assertNotIn('\x1b', got[0]['title'])
        self.assertLessEqual(len(got[0]['title']), session_hooks.TITLE_MAX)
        self.assertIsNone(got[0]['sid'])

    def test_a_session_on_its_own_login_and_hooks_turned_off_note_nothing(self):
        self.run_hook(publish(), env={'CLAUDE_CONFIG_DIR': os.path.join(self.tmp, 'own-login')})
        self.assertEqual(self.lines(), [])
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            json.dump({'session_hooks': False}, f)
        self.run_hook(publish())
        self.assertEqual(self.lines(), [])

    def test_well_under_a_second_and_no_map_no_keychain_no_network(self):
        with mock.patch.object(session_hooks, 'owners', side_effect=AssertionError('the map is not read')), \
                mock.patch('socket.socket', side_effect=AssertionError('no network in a hook')), \
                mock.patch('subprocess.Popen', side_effect=AssertionError('nothing is run (no Keychain)')):
            began = time.process_time()
            self.run_hook(publish())
            took = time.process_time() - began
        self.assertLess(took, 0.25)                     # its own work, in CPU time: the Mac's load can't move this
        cmd = session_hooks.command()                   # exactly as Claude Code runs it: a shell, this Python
        began = time.monotonic()
        r = subprocess.run(['/bin/sh', '-c', cmd], input=json.dumps(update()).encode(), capture_output=True,
                           timeout=60)
        took = time.monotonic() - began
        self.assertEqual((r.returncode, r.stdout), (0, b''))
        self.assertLess(took, 30.0)                     # a Python started too, under load: only a hang fails this
        self.assertEqual([x['id'] for x in self.lines()], [ID1, ID2])

    def test_lines_from_many_sessions_at_once_never_mix(self):
        code = ('import sys, time; sys.path.insert(0, %r); from switchboard import session_hooks as h\n'
                'for i in range(40):\n'
                '    h.note_published({"v": 1, "t": time.time(), "id": "%%s%%02dAbCdEfGhIjKlMn" %% (sys.argv[1], i), '
                '"url": None, "how": "published", "file": "/tmp/" + "f" * 150 + ".html", "title": "t" * 60, '
                '"sid": sys.argv[1]}, time.time())\n' % ROOT)
        procs = [subprocess.Popen([sys.executable, '-I', '-S', '-c', code, 'Session%d' % n]) for n in range(8)]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        got = self.lines()
        self.assertEqual(len(got), 320)
        self.assertEqual(len(set(x['id'] for x in got)), 320)

    def test_the_record_moves_aside_by_size_and_by_age_and_its_older_half_goes_after_a_week(self):
        self.patch(session_hooks, 'PUBLISHED_MAX', 2000)
        now = time.time()
        for i in range(12):
            session_hooks.note_published(session_hooks.published_line(publish(IDS[i % 10]), now), now)
        older = self.path() + '.1'
        self.assertTrue(os.path.exists(older))
        self.assertLess(os.path.getsize(self.path()), 2000)
        self.assertEqual(len(self.lines(older)) + len(self.lines()), 12)       # nothing lost in the move
        current = self.lines()
        session_hooks.prune(os.path.dirname(self.path()), now + 3600)
        self.assertEqual(len(self.lines(older)) + len(self.lines()), 12)       # an hour on: nothing goes
        session_hooks.prune(os.path.dirname(self.path()), now + 8 * 86400)
        self.assertEqual(self.lines(older), current)        # the older half went a week after its last line, and the
        self.assertFalse(os.path.exists(self.path()))       # record, its first line a week old, moved into its place


if __name__ == '__main__':
    unittest.main()
