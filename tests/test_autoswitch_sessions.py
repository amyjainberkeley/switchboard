import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Busy or idle, and the obligations an optional switch would strand. Transcripts are made in a throwaway
# Claude Code folder; ps is a fake.
import json
import os
import sys
import time
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core
from switchboard.accounts import sessions
from fakes_autoswitch import Home

ART = 'https://claude.ai/artifact/AbCdEfGhIjKlMnOpQr1234'
DOC = 'https://claude.ai/artifact/DocDocDocDocDocDoc5678'
WATCHED = 'https://claude.ai/artifact/WatchWatchWatch1234567'


def stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace('+00:00', 'Z')


def result(text, t, uid='x'):
    return {'type': 'user', 'timestamp': stamp(t),
            'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': uid, 'content': text}]}}


def assistant(stop, t, tool=False):
    content = [{'type': 'tool_use', 'id': 't', 'name': 'Bash', 'input': {}}] if tool else [{'type': 'text',
                                                                                           'text': 'done'}]
    return {'type': 'assistant', 'timestamp': stamp(t), 'message': {'stop_reason': stop, 'content': content}}


def tool_use(name, t, uid='x'):
    """The assistant line that calls a tool: its answer comes back as a tool_result with tool_use_id `uid`."""
    return {'type': 'assistant', 'timestamp': stamp(t),
            'message': {'content': [{'type': 'tool_use', 'id': uid, 'name': name, 'input': {}}]}}


def doc_result(t, url=DOC):
    text = json.dumps({'verdict': 'ok', 'created': {'id': 'd1'}, 'frame': {'slug': 'doc', 'url': url}},
                      separators=(',', ':'))
    return result(text, t)


class Activity(Home):
    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.root = os.path.join(os.environ['CLAUDE_CONFIG_DIR'], 'projects')
        self.procs = []
        self.scanner = sessions.Scanner(root=self.root, procs=lambda: self.procs.append(1) or 2)

    def write(self, rel, lines, age):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a') as f:
            for d in lines:
                f.write(json.dumps(d) + '\n')
        t = self.now - age
        os.utime(path, (t, t))
        return path

    def look(self, timeline=(), light=False, now=None):
        return self.scanner.look(timeline, now=now or self.now, light=light)

    def test_a_chat_written_30_seconds_ago_is_busy(self):
        self.write('proj/s1.jsonl', [assistant('end_turn', self.now - 30)], 30)
        a = self.look()
        self.assertTrue(a['busy'])
        self.assertEqual(a['idle_for_s'], 0.0)

    def test_an_open_turn_is_busy_until_it_counts_as_stuck(self):
        self.write('proj/s1.jsonl', [assistant('tool_use', self.now - 480, tool=True)], 480)
        self.assertTrue(self.look()['busy'])
        self.write('proj/s2.jsonl', [assistant('tool_use', self.now - 1200, tool=True)], 1200)
        os.unlink(os.path.join(self.root, 'proj/s1.jsonl'))
        a = sessions.Scanner(root=self.root, procs=lambda: 0).look((), now=self.now)
        self.assertFalse(a['busy'])
        self.assertAlmostEqual(a['idle_for_s'], 1200, delta=2)

    def test_a_workflow_agent_writing_is_busy(self):
        self.write('proj/s1.jsonl', [assistant('end_turn', self.now - 900)], 900)
        self.write('proj/s1/subagents/workflows/wf_1/agent-1.jsonl', [assistant('end_turn', self.now - 30)], 30)
        self.assertTrue(self.look()['busy'])

    def test_status_line_numbers_that_moved_are_busy(self):
        self.write('proj/s1.jsonl', [assistant('end_turn', self.now - 900)], 900)
        live = core.data_path('live')
        os.makedirs(live)
        p = os.path.join(live, 's1.json')
        with open(p, 'w') as f:
            f.write('{}')
        os.utime(p, (self.now - 120, self.now - 120))
        self.assertTrue(self.look()['busy'])

    def test_quiet_for_ten_minutes_is_idle(self):
        self.write('proj/s1.jsonl', [assistant('end_turn', self.now - 600)], 600)
        a = self.look()
        self.assertFalse(a['busy'])
        self.assertAlmostEqual(a['idle_for_s'], 600, delta=2)
        self.assertIsNone(sessions.Scanner(root=os.path.join(self.tmp, 'none'), procs=lambda: 0)
                          .look((), now=self.now)['idle_for_s'])

    def test_a_doc_made_ten_minutes_ago_holds_and_one_made_forty_minutes_ago_does_not(self):
        self.write('proj/s1.jsonl', [doc_result(self.now - 600)], 590)
        obs = self.look([(self.now - 7200, 'acct-a')])['obligations']
        self.assertEqual([(o['kind'], o['url'], o['account_id']) for o in obs], [('doc', DOC, 'acct-a')])
        self.assertAlmostEqual(obs[0]['until'], self.now - 600 + sessions.OBLIGATION_KEEP, delta=1)
        sessions._reset_for_tests()
        self.write('proj/s2.jsonl', [doc_result(self.now - 2400, url=DOC.replace('Doc', 'Old'))], 2390)
        obs = sessions.Scanner(root=self.root, procs=lambda: 0).look((), now=self.now)['obligations']
        self.assertEqual([o['url'] for o in obs], [DOC])

    def test_a_publish_is_an_artifact_obligation(self):
        self.write('proj/s1.jsonl', [result('Published page.html at %s (private)' % ART, self.now - 300)], 300)
        self.write('proj/s2.jsonl', [result('The history mentions Published x at %s' % ART.replace('Ab', 'Zz'),
                                            self.now - 300)], 300)
        obs = self.look()['obligations']
        self.assertEqual([(o['kind'], o['url']) for o in obs], [('artifact', ART)])

    def test_a_watch_holds_until_it_ends_or_its_session_is_long_quiet(self):
        self.write('proj/s1.jsonl', [result('Published a.html at %s\nThis session now watches it for comments.'
                                            % WATCHED, self.now - 5 * 3600)], 5 * 3600)
        kinds = sorted(o['kind'] for o in self.look()['obligations'])
        self.assertEqual(kinds, ['watch'])                           # the publish itself is older than 30 minutes
        self.write('proj/s1.jsonl', [tool_use('ArtifactComments', self.now - 61),
                                     result('This session is no longer watching %s.' % WATCHED, self.now - 60)], 60)
        self.assertEqual(self.look()['obligations'], [])

    def test_a_watch_with_its_session_quiet_for_seven_hours_has_expired(self):
        self.write('proj/s1.jsonl', [tool_use('ArtifactComments', self.now - 7 * 3600 - 1),
                                     result('Now watching %s' % WATCHED, self.now - 7 * 3600)], 7 * 3600)
        self.assertEqual(self.look()['obligations'], [])
        self.write('proj/s2.jsonl', [tool_use('ArtifactComments', self.now - 61),
                                     result('Now watching %s' % WATCHED, self.now - 60)], 60)
        self.assertEqual([o['kind'] for o in self.look()['obligations']], ['watch'])     # (a fresh one holds)

    def test_only_the_artifact_tools_own_answer_starts_a_watch(self):
        """Any tool's output that quoted an artifact link beside "watching the" or "subscribed to" (a
        Bash or Read result, a fetched chat log) started a watch, holding optional switches for 6 hours or more."""
        self.write('proj/s1.jsonl', [tool_use('Bash', self.now - 121, uid='b1'),
                                     result('keep watching the page %s for changes' % WATCHED, self.now - 120),
                                     tool_use('Read', self.now - 101, uid='r1'),
                                     result('Subscribed to nothing. see %s' % WATCHED, self.now - 100)], 100)
        self.assertEqual(self.look()['obligations'], [])
        self.write('proj/s1.jsonl', [tool_use('Artifact', self.now - 61, uid='a1'),
                                     result('Now watching %s for comments.' % WATCHED, self.now - 60, uid='a1')], 60)
        obs = self.look()['obligations']
        self.assertEqual([(o['kind'], o['url']) for o in obs], [('watch', WATCHED)])

    def test_each_obligation_belongs_to_the_account_live_when_it_was_made(self):
        timeline = [(self.now - 3600, 'old'), (self.now - 600, 'new')]
        self.write('proj/s1.jsonl', [doc_result(self.now - 900, url=DOC),
                                     doc_result(self.now - 620, url=DOC.replace('5678', '0000')),
                                     doc_result(self.now - 300, url=DOC.replace('5678', '1111'))], 300)
        got = dict((o['url'][-4:], o['account_id']) for o in self.look(timeline)['obligations'])
        self.assertEqual(got, {'5678': 'old', '0000': 'new', '1111': 'new'})      # 20 s before: the newer one

    def test_new_lines_are_read_on_from_where_the_last_look_stopped(self):
        self.write('proj/s1.jsonl', [assistant('end_turn', self.now - 900)], 900)
        self.assertEqual(self.look()['obligations'], [])
        self.write('proj/s1.jsonl', [doc_result(self.now - 60)], 60)
        self.assertEqual([o['kind'] for o in self.look()['obligations']], ['doc'])

    def test_a_huge_line_is_skipped_and_the_next_still_read(self):
        path = self.write('proj/s1.jsonl', [assistant('end_turn', self.now - 900)], 900)
        self.look()
        with open(path, 'a') as f:
            f.write('{"type":"user","pad":"' + 'x' * (sessions.LONG_LINE + 1000) + '"}\n')
            f.write(json.dumps(doc_result(self.now - 30)) + '\n')
        os.utime(path, (self.now - 30, self.now - 30))
        self.assertEqual([o['kind'] for o in self.look()['obligations']], ['doc'])

    def test_a_line_longer_than_a_whole_look_is_skipped_across_looks(self):
        path = self.write('proj/s1.jsonl', [assistant('end_turn', self.now - 900)], 900)
        self.look()
        old = (sessions.MAX_BYTES, sessions.LONG_LINE)
        sessions.MAX_BYTES, sessions.LONG_LINE = 100 * 1024, 16 * 1024
        self.addCleanup(lambda: setattr(sessions, 'MAX_BYTES', old[0]) or setattr(sessions, 'LONG_LINE', old[1]))
        with open(path, 'a') as f:
            f.write('{"type":"user","pad":"' + 'y' * (350 * 1024) + '"}\n')
            f.write(json.dumps(doc_result(self.now - 30)) + '\n')
        os.utime(path, (self.now - 30, self.now - 30))
        found = []
        for _ in range(6):
            found = [o['kind'] for o in self.look()['obligations']]
            if found:
                break
        self.assertEqual(found, ['doc'])

    def test_light_looks_read_no_transcript_and_run_no_ps(self):
        self.write('proj/s1.jsonl', [doc_result(self.now - 60)], 60)
        a = self.look(light=True)
        self.assertEqual(a['obligations'], [])
        self.assertEqual(self.procs, [])
        self.assertTrue(a['busy'])

    def test_ps_runs_at_most_once_a_minute(self):
        self.write('proj/s1.jsonl', [assistant('end_turn', self.now - 900)], 900)
        self.look()
        self.look(now=self.now + 30)
        self.assertEqual(len(self.procs), 1)
        self.assertEqual(self.look(now=self.now + 61)['processes'], 2)
        self.assertEqual(len(self.procs), 2)

    def test_nothing_about_files_or_projects_comes_out(self):
        self.write('secret-project/s-9f1c.jsonl', [doc_result(self.now - 60)], 60)
        text = json.dumps(self.look())
        for word in ('secret-project', 's-9f1c', self.tmp, '.jsonl'):
            self.assertNotIn(word, text)

    def test_claude_codes_own_limit_notice_is_seen_and_nothing_else_is(self):
        notice = {'type': 'assistant', 'timestamp': stamp(self.now - 120), 'isApiErrorMessage': True,
                  'message': {'model': '<synthetic>', 'content': [{'type': 'text',
                                                                   'text': "You've hit your limit · resets 4pm"}]}}
        chat = {'type': 'assistant', 'timestamp': stamp(self.now - 60),
                'message': {'model': 'claude-opus', 'content': [{'type': 'text', 'text': 'the usage limit is 90%'}]}}
        self.write('proj/s1.jsonl', [notice, chat], 60)
        self.assertAlmostEqual(self.look()['limited_at'], self.now - 120, delta=1)
        self.assertEqual(self.look()['obligations'], [])
        sessions._reset_for_tests()
        old = dict(notice, timestamp=stamp(self.now - 1200))
        self.write('proj/s2.jsonl', [old], 1200)
        os.unlink(os.path.join(self.root, 'proj/s1.jsonl'))
        self.assertIsNone(sessions.Scanner(root=self.root, procs=lambda: 0).look((), now=self.now)['limited_at'])

    def test_a_per_minute_rate_limit_is_not_the_accounts_limit(self):
        """A bare "rate limit" in an API error (a per-minute 429) counted as the account's usage
        limit, so a healthy account was marked refused and switched away from."""
        def notice(text, t):
            return {'type': 'assistant', 'timestamp': stamp(t), 'isApiErrorMessage': True,
                    'message': {'model': '<synthetic>', 'content': [{'type': 'text', 'text': text}]}}
        self.write('proj/s1.jsonl', [notice('API Error: 429 {"type":"error","error":{"type":"rate_limit_error",'
                                            '"message":"This request would exceed your per-minute rate limit"}}',
                                            self.now - 60)], 60)
        self.assertIsNone(self.look()['limited_at'])
        self.write('proj/s1.jsonl', [notice('Claude AI usage limit reached|1794955200', self.now - 30)], 30)
        self.assertAlmostEqual(self.look()['limited_at'], self.now - 30, delta=1)

    def test_owner_at_without_a_timeline_is_not_known(self):
        self.assertIsNone(sessions.owner_at([], self.now))
        self.assertIsNone(sessions.owner_at([(self.now + 100, 'later')], self.now))


if __name__ == '__main__':
    unittest.main()
