import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""Behind the page: a new page opens as its owner at once.

The page takes the session hooks' record (hooks/published.jsonl, tests/test_artifacts_publish_hook.py) on each pass of its
loop and before an open or a lookup, with the owner from the switch history (near a switch only a guess, as the
history scan says it; a switch History recorded and the page has not seen yet counts), and writes owners.json again.
`switchboard open` and `switchboard owner` read the record directly when the page is down. Each line is taken once,
across the record moving aside, and junk in it is passed over."""
import json
import os
import shutil
import time
import unittest
from unittest import mock

from fakes_links import FakeChrome, Home
from fakes_artifacts import A, B, ID1, ID2, ID3, LINK, publish

from switchboard import artifacts, core, engine, history, keep, linkapp, server, session_hooks

IDS = ['%s%02dAbCdEfGhIjKlMnOp' % (c, i) for i, c in enumerate('QRSTUVWXYZ')]


class OwnerCase(Home):
    """Two accounts with their own profiles, the record, and the page's state."""

    def setUp(self):
        super().setUp()
        self.chrome = FakeChrome(self, {'P1': 'Work', 'P2': 'Home'}, logins={'P1': A, 'P2': B})
        self.now = time.time()
        self.addCleanup(artifacts._PUB.update, seen=None)
        artifacts._PUB['seen'] = None

    def accounts(self, timeline):
        self.state(**{A: {'profile': 'P1', 'profile_src': 'you'}, B: {'profile': 'P2', 'profile_src': 'you'}})
        with core.locked() as st:
            st['timeline'], st['active'], st['scan_v'] = timeline, timeline[-1][0], artifacts.SCAN_V

    def note(self, ev, t):
        session_hooks.note_published(session_hooks.published_line(ev, t), t)

    def item(self, aid):
        return artifacts.find(core.load(), aid) or {}

    def files(self):
        f = artifacts.folders(core.load(), {'P1': 'Work', 'P2': 'Home'})
        return dict((x['id'], dict(x, folder=fo['id'])) for fo in f['folders'] for x in fo['files'])


class ThePageTakesThemAtOnce(OwnerCase):
    def test_a_new_page_gets_its_owner_its_file_and_owners_json_within_one_pass(self):
        self.accounts([[A, self.now - 7200]])
        self.note(publish(ID1), self.now - 20)
        self.assertEqual(artifacts.published_tick(), 1)
        it = self.item(ID1)
        self.assertEqual((it['owner'], it['src'], it['file'], it['title'], it['name']),
                         (A, 'published', '/Users/maya/site/launch/index.html', 'Launch board', 'launch'))
        self.assertEqual(artifacts.read_owner_map()['artifacts'][ID1], {'o': A, 'p': 'P1', 's': 'published'})
        f = self.files()[ID1]
        self.assertEqual((f['folder'], f['sure'], f['preview'], f['profile']), (A, True, False, 'P1'))
        with mock.patch.object(artifacts, 'locked', side_effect=AssertionError('nothing new: nothing written')):
            self.assertEqual(artifacts.published_tick(), 0)
        with open(artifacts.published_files()[1], 'ab') as f:
            f.write(b'{"v": 1, "t": ')                      # a line still being written: read when it is whole
        with mock.patch.object(artifacts, 'locked', side_effect=AssertionError('nothing whole: nothing written')):
            self.assertEqual(artifacts.published_tick(), 0)

    def test_a_publish_right_at_a_switch_is_not_sure_and_the_first_open_checks_it(self):
        switch = self.now - 60
        self.accounts([[A, self.now - 7200], [B, switch]])
        self.note(publish(ID1), switch + 10)
        self.note(publish(ID2, path='/tmp/b.html'), switch - 15)
        self.note(publish(ID3, path='/tmp/c.html'), self.now - 6600)
        self.assertEqual(artifacts.take_published_now(), 3)
        self.assertEqual((self.item(ID1)['owner'], self.item(ID1)['src']), (B, 'guess'))
        self.assertEqual((self.item(ID2)['owner'], self.item(ID2)['src']), (A, 'guess'))
        self.assertEqual((self.item(ID3)['owner'], self.item(ID3)['src']), (A, 'published'))
        f = self.files()
        self.assertEqual((f[ID1]['folder'], f[ID1]['sure']), (B, False))      # in its folder, never "not known yet"
        self.assertEqual(artifacts.read_owner_map()['artifacts'][ID1], {'o': B, 'p': 'P2', 's': 'guess'})
        said = artifacts.owner_of(LINK % ID1, scan=False)['said']
        self.assertTrue(said.startswith('Probably %s (it was published near a switch' % B), said)
        tries = artifacts.candidates(core.load(), self.item(ID1), {'P1': 'Work', 'P2': 'Home'})
        self.assertEqual(tries[0], ('P2', B))               # the first open tries its profile, and checks the tab

    def test_a_switch_the_page_has_not_seen_yet_counts_from_history(self):
        self.accounts([[A, self.now - 7200]])               # the page looks once a minute: it has not seen it yet
        history.record('switch', 'Switched Claude Code to %s' % B, t=self.now - 600, to=B, account=B, by='auto')
        self.note(publish(ID1), self.now - 20)
        self.note(publish(ID2, path='/tmp/b.html'), self.now - 590)
        artifacts.take_published_now()
        self.assertEqual((self.item(ID1)['owner'], self.item(ID1)['src']), (B, 'published'))
        self.assertEqual((self.item(ID2)['owner'], self.item(ID2)['src']), (B, 'guess'))     # 10 s after it
        tl = artifacts.switch_timeline(core.load())
        self.assertEqual(tl, [[A, self.now - 7200], [B, self.now - 600]])
        with core.locked() as st:                           # once the page sees it (late), nothing moves back
            core.set_active(st, B)
        self.assertEqual(artifacts.switch_timeline(core.load())[-1][0], B)
        self.assertEqual(len(artifacts.switch_timeline(core.load())), 2)

    def test_with_the_page_down_open_and_owner_read_the_record(self):
        self.accounts([[A, self.now - 7200], [B, self.now - 3600]])
        artifacts.write_owner_map()                         # the map as the page wrote it last: no new page in it
        self.assertNotIn(ID1, artifacts.read_owner_map()['artifacts'])
        self.note(publish(ID1), self.now - 20)
        self.patch(linkapp, 'ask_page', mock.Mock(side_effect=linkapp.PageDown('nothing on its port')))
        got = artifacts.open_link(LINK % ID1)
        self.assertEqual((got['by'], got['email'], got['profile'], got['from']), ('map', B, 'P2', 'record'))
        self.assertEqual(self.chrome.opened[-1], ('P2', LINK % ID1, False))
        self.assertIn('(%s)' % B, got['said'])
        self.assertIsNone(self.item(ID1).get('owner'))      # an open from the record writes nothing
        with mock.patch.object(artifacts, 'refresh', side_effect=AssertionError('no history scan needed')):
            r = artifacts.owner_of(LINK % ID1)
        self.assertEqual((r['email'], r['src'], r['profile']), (B, 'published', 'P2'))
        self.assertIn(ID1, artifacts.read_owner_map()['artifacts'])     # and owners.json has it now

    def test_an_owner_without_a_profile_opens_nothing_from_the_record(self):
        self.accounts([[A, self.now - 7200]])
        with core.locked() as st:
            st['accounts'][A].update(profile=None, profile_src=None)
        artifacts.write_owner_map()
        self.note(publish(ID1), self.now - 20)
        self.patch(linkapp, 'ask_page', mock.Mock(side_effect=linkapp.PageDown('down')))
        got = artifacts.open_link(LINK % ID1)
        self.assertTrue(got['no_profile'])
        self.assertEqual(self.chrome.opened, [])            # never "Page not found" in another account's profile

    def test_each_line_is_taken_once_across_a_move_and_after_losing_track(self):
        self.accounts([[A, self.now - 7200]])
        for i in range(3):
            self.note(publish(IDS[i], path='/tmp/%d.html' % i), self.now - 900 + 60 * i)
        self.assertEqual(artifacts.take_published_now(), 3)
        self.assertEqual(artifacts.take_published_now(), 0)
        self.note(publish(IDS[3], path='/tmp/3.html'), self.now - 500)
        older, current = artifacts.published_files()
        os.replace(current, older)                          # the hook moved it aside
        self.note(publish(IDS[4], path='/tmp/4.html'), self.now - 400)
        self.assertEqual(artifacts.take_published_now(), 2)     # the rest of the old file, then the new one
        for p in (older, current):                          # both replaced by copies: track is lost
            shutil.copyfile(p, p + '.x')
            os.replace(p + '.x', p)
        self.note(publish(IDS[5], path='/tmp/5.html'), self.now - 300)
        artifacts.take_published_now()
        ids = [x['id'] for x in core.load()['artifacts']]
        self.assertEqual(sorted(ids), sorted(IDS[:6]))      # each once, the new one taken
        self.assertEqual(self.item(IDS[5])['file'], '/tmp/5.html')

    def test_junk_in_the_record_is_passed_over(self):
        self.accounts([[A, self.now - 7200]])
        t = self.now - 30
        rows = ['not json', json.dumps({'v': 1, 't': t, 'id': '../../../etc/passwd'}),
                json.dumps({'v': 2, 't': t, 'id': IDS[0]}), json.dumps({'v': 1, 't': self.now + 86400, 'id': IDS[1]}),
                json.dumps({'v': 1, 't': True, 'id': IDS[2]}), 'x' * 9000,
                json.dumps({'v': 1, 't': t, 'id': ID1, 'file': 'relative.html', 'how': 'erased'}),
                json.dumps({'v': 1, 't': t, 'id': ID2, 'url': 'https://evil.example/artifact/' + ID2}),
                json.dumps({'v': 1, 't': t, 'id': ID3, 'file': '/tmp/a\x07.html', 'title': 'A\x1b[2Jtitle\nhere'})]
        os.makedirs(os.path.dirname(artifacts.published_files()[1]), exist_ok=True)
        with open(artifacts.published_files()[1], 'w') as f:
            f.write('\n'.join(rows) + '\n')
        self.assertEqual(artifacts.take_published_now(), 3)
        st = core.load()
        self.assertEqual(sorted(x['id'] for x in st['artifacts']), sorted([ID1, ID2, ID3]))
        self.assertIsNone(self.item(ID1).get('file'))
        self.assertEqual(self.item(ID2)['url'], LINK % ID2)
        self.assertEqual((self.item(ID3).get('file'), self.item(ID3)['title']), (None, 'A [2Jtitle here'))

    def test_an_open_or_a_lookup_takes_the_record_before_the_history_scan(self):
        self.accounts([[A, self.now - 7200]])
        self.note(publish(ID1), self.now - 20)
        artifacts.refresh()                                 # what an open does for a page it doesn't know
        self.assertEqual(self.item(ID1)['owner'], A)

    def test_the_history_scan_keeps_the_file_a_publish_named(self):
        self.accounts([[A, self.now - 7200]])
        proj = os.path.join(core.projects(), 'p')
        os.makedirs(proj)
        when = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.now - 30))
        with open(os.path.join(proj, 's.jsonl'), 'w') as f:
            for text, cwd in (('Published /Users/maya/site/a.html at %s (version 1)' % (LINK % ID1), '/x'),
                              ('Published b/index.html at %s' % (LINK % ID2), '/Users/maya/work')):
                f.write(json.dumps({'type': 'assistant', 'message': {'content': [
                    {'type': 'tool_use', 'id': 't', 'name': 'Artifact'}]}}) + '\n')     # the call each answer belongs to
                f.write(json.dumps({'type': 'user', 'timestamp': when, 'cwd': cwd, 'message': {'content': [
                    {'type': 'tool_result', 'tool_use_id': 't', 'content': text}]}}) + '\n')
        artifacts.refresh()
        self.assertEqual(self.item(ID1)['file'], '/Users/maya/site/a.html')
        self.assertEqual((self.item(ID2)['file'], self.item(ID2)['name']), ('/Users/maya/work/b/index.html', 'b'))


class TheLoopTakesThemEachPass(Home):
    def test_each_pass_calls_it_and_a_failure_never_stops_the_loop(self):
        calls = []
        self.patch(artifacts, 'published_tick', lambda: calls.append(1) or (_ for _ in ()).throw(OSError('disk')))
        self.patch(server, 'tick', lambda: {'rows': None})
        for name, value in (('keep_tick', lambda looked: None), ('mac_tick', lambda now=None: None),
                            ('disk_tick', lambda run=None: False), ('load_per_core', lambda: 0.0)):
            self.patch(server, name, value)
        self.patch(keep, 'login_changed', lambda: False)
        self.patch(keep, 'due', lambda now=None: False)
        self.patch(keep, 'wait_for', lambda default, now=None: 0.0)
        self.patch(engine.HOST, 'ensure', lambda now=None: None)
        self.patch(server.claudecode, 'CAP', {'running': True, 'at': 0})
        self.patch(server.claudecode, 'HEALTH', {'running': True, 'at': 0})
        self.patch(server.WAKE, 'wait', lambda timeout=None: False)
        server.loop(ticks=3)
        self.assertEqual(len(calls), 3)


if __name__ == '__main__':
    unittest.main()
