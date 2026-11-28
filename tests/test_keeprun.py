import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Keep running (keeprun.py and GET /api/keep): each cause is told from records shaped like Claude Code's own (shapes
# copied from 2.1.329 to 2.1.332, the text made up), one stop written many times counts once, time lost runs from the
# stop to the next reply, the cache reads a file again only when it changed and a pass stops at its time budget, a
# workflow run's dead agents are found with why and whether a later run did their work again, a chat waiting on you and
# a chat cut off are told apart, every step shows only when its numbers say so, and /api/keep answers on an empty Mac.
# Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_keeprun.py'
import itertools
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)

from switchboard import core, keeprun, server  # noqa: E402
import pagekey_help  # noqa: E402  (the page key, as Switchboard's own programs send it)

NOW = 1795776600.0         # a fixed wall time: Fri Nov 27 2026, morning (UTC)


def ts(t):
    """Claude Code's own timestamp shape: 2026-11-27T09:55:15.707Z."""
    return time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(t)) + '.%03dZ' % int((t % 1) * 1000)


def reply(t, model='claude-fable-5-2', tools=(), stop='end_turn', mid=None, out=100, side=False):
    content = [{'type': 'text', 'text': 'made-up reply'}] + [{'type': 'tool_use', 'id': 'tu_%s_%d' % (n, i),
                                                              'name': n, 'input': {}} for i, n in enumerate(tools)]
    return {'parentUuid': 'p', 'isSidechain': side, 'type': 'assistant', 'uuid': 'u%f' % t, 'timestamp': ts(t),
            'requestId': 'req_%f' % t, 'requestedModel': model,
            'message': {'id': mid or 'msg_%f' % t, 'model': model, 'role': 'assistant', 'type': 'message',
                        'stop_reason': 'tool_use' if tools else stop, 'content': content,
                        'usage': {'input_tokens': 10, 'output_tokens': out, 'cache_creation_input_tokens': 0,
                                  'cache_read_input_tokens': 0}}}


IDS = itertools.count()


def stop(t, kind, mid=None):
    """A stop record as Claude Code writes it: model "<synthetic>", isApiErrorMessage, and the fields of each kind.
    Each has a message id of its own, as Claude Code gives it, unless `mid` names one (a copy of the same record)."""
    d = {'parentUuid': 'p', 'isSidechain': False, 'type': 'assistant', 'uuid': 's%f' % t, 'timestamp': ts(t),
         'message': {'id': mid or 'stop-%d' % next(IDS), 'model': '<synthetic>', 'role': 'assistant',
                     'stop_reason': 'stop_sequence', 'type': 'message', 'content': [],
                     'usage': {'input_tokens': 0, 'output_tokens': 0}},
         'isApiErrorMessage': True, 'userType': 'external', 'entrypoint': 'claude-vscode'}
    text = {
        'limit_model': "You've hit your Fable limit · resets Nov 30 at 1pm (America/Los_Angeles). Switch to another "
                       'model, or manage usage credits at https://claude.ai/settings/usage, to continue.',
        'limit_5hour': "You've hit your 5-hour limit · resets 12:20pm (America/Los_Angeles)",
        'limit_weekly': "You've hit your weekly limit · resets Dec 3 at 9am (America/Los_Angeles)",
        'limit_credits': 'Your usage credits are used up. Add credits or raise your spend limit to continue.',
        'signed_out': 'Not logged in · Please run /login',
        'oauth': 'Failed to authenticate: OAuth session expired and could not be refreshed',
        'blocked_403': 'Failed to authenticate. API Error: 403 Attention Required! | Cloudflare',
        'offline': "API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)",
        'slept': 'API Error: Your computer went to sleep mid-response. The response above may be cut short.',
        'overloaded': 'API Error: 529 Overloaded. This is a server-side issue, usually temporary.',
        'too_long': 'Prompt is too long',
        'flagged': "API Error: Opus 5.5's safeguards flagged this session",
        'empty': 'No response requested.',
        'error_other': 'API Error: 500 Internal server error',
    }[kind]
    d['message']['content'] = [{'type': 'text', 'text': text}]
    if kind.startswith('limit_') and kind != 'limit_credits':
        d.update(apiError='usage_limit_reached', error='rate_limit', apiErrorStatus=429,
                 apiErrorType='rate_limit_error',
                 apiErrorParams={'rate_limit_info': {'status': 'rejected', 'resetsAt': NOW + 86400,
                                                     'rateLimitType': {'limit_model': 'seven_day_overage_included',
                                                                       'limit_5hour': 'five_hour',
                                                                       'limit_weekly': 'seven_day'}[kind]}})
    elif kind == 'limit_credits':
        d.update(error='rate_limit', apiErrorStatus=429)
    elif kind in ('signed_out', 'oauth'):
        d.update(error='authentication_failed')
    elif kind == 'blocked_403':
        d.update(error='authentication_failed', apiErrorStatus=403)
    elif kind == 'offline':
        d.update(apiError='connection_failed', error='server_error')
    elif kind == 'slept':
        d.update(apiError='computer_slept', error='server_error', streamCutCause='stream_suspended')
    elif kind == 'overloaded':
        d.update(error='server_error', apiErrorStatus=529, apiErrorType='overloaded_error')
    elif kind in ('too_long',):
        d.update(error='invalid_request')
    elif kind == 'flagged':
        d.update(error='invalid_request')
        d['message']['stop_reason'] = 'refusal'
    elif kind == 'empty':
        d['isApiErrorMessage'] = False
    else:
        d.update(error='server_error', apiErrorStatus=500)
    return d


def prompt(t, text='made-up prompt'):
    return {'parentUuid': 'p', 'isSidechain': False, 'type': 'user', 'uuid': 'q%f' % t, 'timestamp': ts(t),
            'message': {'role': 'user', 'content': text}}


def result(t, shutdown=False):
    d = {'parentUuid': 'p', 'isSidechain': False, 'type': 'user', 'uuid': 'r%f' % t, 'timestamp': ts(t),
         'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'tu', 'content': 'x' * 50}]},
         'toolUseResult': 'made-up output'}
    if shutdown:
        d.update(toolDenialKind='user-rejected', toolDenialBeforeRun=True, interruptedByShutdown=True)
    return d


def title(text):
    return {'type': 'custom-title', 'customTitle': text, 'sessionId': 's'}


class Fixture(unittest.TestCase):
    """A made-up Claude Code folder, Mac and Switchboard data folder; no real command runs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='keeprun-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, 'projects')
        self.proj = os.path.join(self.root, '-Users-someone-work')
        os.makedirs(self.proj)
        self.ext = os.path.join(self.tmp, 'extensions')
        os.makedirs(self.ext)
        self.settings = os.path.join(self.tmp, 'settings.json')
        self.logs = os.path.join(self.tmp, 'vscode-logs')
        self.cmds = {'pmset_log': '', 'batt': "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t80%; charged;",
                     'swap': 'vm.swapusage: total = 2048.00M  used = 100.00M  free = 1948.00M  (encrypted)',
                     'ps': ''}
        self.ran = []

        def run(cmd, timeout):
            self.ran.append(cmd)
            if cmd[:3] == ['/usr/bin/pmset', '-g', 'log']:
                return self.cmds['pmset_log']
            if cmd[:3] == ['/usr/bin/pmset', '-g', 'batt']:
                return self.cmds['batt']
            if cmd[0] == '/usr/sbin/sysctl':
                return self.cmds['swap']
            if cmd[0] == '/bin/ps':
                return self.cmds['ps']
            return None
        env = mock.patch.dict(keeprun.ENV, {'projects': self.root, 'extensions': self.ext,
                                            'claude_settings': self.settings, 'vscode_logs': self.logs, 'path': [],
                                            'run': run}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.data = core.data_dir()
        os.makedirs(self.data, mode=0o700, exist_ok=True)
        for name in (keeprun.CACHE_FILE, keeprun.MAC_FILE, 'keeprun-panels.json', 'history.jsonl', 'readings.jsonl'):
            self.addCleanup(lambda p=os.path.join(self.data, name): os.path.exists(p) and os.remove(p))
        self.addCleanup(shutil.rmtree, os.path.join(self.data, 'running'), True)

    def chat(self, session, lines, age=3600, sub=None):
        """A chat file (or a helper's under it, when `sub` names one) written `age` seconds before NOW."""
        if sub:
            folder = os.path.join(self.proj, session, 'subagents')
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, 'agent-%s.jsonl' % sub)
        else:
            path = os.path.join(self.proj, session + '.jsonl')
        with open(path, 'w') as f:
            for line in lines:
                f.write(json.dumps(line) + '\n')
        os.utime(path, (NOW - age, NOW - age))
        return path

    def build(self, **kw):
        kw.setdefault('lines', [])
        kw.setdefault('auto_on', True)
        kw.setdefault('awake_on', True)
        return keeprun.build(now=NOW, **kw)


class Causes(unittest.TestCase):
    def test_each_cause_is_told_from_its_record(self):
        for kind, want in (('limit_model', 'limit_model'), ('limit_5hour', 'limit_5hour'),
                           ('limit_weekly', 'limit_weekly'), ('limit_credits', 'limit_credits'),
                           ('signed_out', 'signed_out'), ('oauth', 'signed_out'), ('blocked_403', 'blocked_403'),
                           ('offline', 'offline'), ('slept', 'offline'), ('overloaded', 'overloaded'),
                           ('too_long', 'too_long'), ('flagged', 'flagged'), ('empty', 'empty'),
                           ('error_other', 'error_other')):
            d = stop(NOW, kind)
            text = d['message']['content'][0]['text']
            self.assertEqual(keeprun.cause_of(d, text), want, kind)

    def test_a_fable_limit_is_the_model_limit_even_though_it_names_credits(self):
        d = stop(NOW, 'limit_model')
        self.assertIn('credits', d['message']['content'][0]['text'])
        self.assertEqual(keeprun.cause_of(d, d['message']['content'][0]['text']), 'limit_model')

    def test_families(self):
        self.assertEqual(keeprun.family('claude-fable-5-2'), 'fable')
        self.assertEqual(keeprun.family('claude-opus-5-5[1m]'), 'opus')
        self.assertIsNone(keeprun.family('gpt-x'))
        self.assertIsNone(keeprun.family(None))


class Stops(Fixture):
    def test_merged_stops_time_lost_and_what_came_next(self):
        t0 = NOW - 7200
        self.chat('a' * 8 + '-0001', [title('Release notes'), prompt(t0 - 60), reply(t0 - 30),
                                      stop(t0, 'limit_model'), prompt(t0 + 120), stop(t0 + 125, 'limit_model'),
                                      prompt(t0 + 600), reply(t0 + 1200, model='claude-opus-5-5')])
        self.chat('b' * 8 + '-0002', [title('Data import'), reply(t0 - 10, model='claude-opus-5-5'),
                                      stop(t0, 'slept'), reply(t0 + 30, model='claude-opus-5-5')])
        self.chat('c' * 8 + '-0003', [title('Search index'), reply(t0 - 10), stop(t0 + 5, 'signed_out')])
        r = self.build()
        by = dict((c['key'], c) for c in r['causes'])
        fable = by['limit_model']
        self.assertEqual(fable['count'], 2)                         # a line she typed between: two stops
        self.assertEqual(fable['name'], 'Fable used up on the account in use')
        self.assertIsNone(fable['went_on_min'])                     # it went on after a typed line: not in it
        self.assertEqual((fable['typed'], fable['continued'], fable['never']), (2, 0, 0))
        self.assertEqual(fable['examples'][0]['title'], 'Release notes')
        self.assertEqual((by['offline']['continued'], by['offline']['went_on_min']), (1, 1))
        self.assertEqual((by['signed_out']['never'], by['signed_out']['went_on_min']), (1, None))
        self.assertEqual(r['week']['d7']['stops'], 4)
        self.assertEqual(r['week']['d7']['continued'], 1)
        self.assertEqual(r['week']['sentence'], 'Last 7 days: 4 early stops in chats. Of the stops, 1 went on by '
                                                'itself, 2 after you typed, 1 never went on.')

    def test_no_time_lost_anywhere_only_exact_counts(self):
        # Two chats stop in the same minute and one goes on only days later: no "lost" hours are added up anywhere.
        t0 = NOW - 6 * 86400
        self.chat('a' * 8 + '-0101', [title('One'), reply(t0 - 10), stop(t0, 'offline'), prompt(t0 + 3 * 86400),
                                      reply(t0 + 3 * 86400 + 5)])
        self.chat('a' * 8 + '-0102', [title('Two'), reply(t0 - 10), stop(t0 + 20, 'offline'), reply(t0 + 80)])
        self.chat('a' * 8 + '-0103', [title('Three'), reply(t0 - 10), stop(t0 + 30, 'signed_out')])
        self.chat('a' * 8 + '-0103', [reply(t0 - 5, model='claude-sonnet-5-5'), stop(t0 + 40, 'signed_out')],
                  sub='h1')
        r = self.build()
        text = json.dumps(r)
        for gone in ('lost', 'hours_lost', 'lost_min'):
            self.assertNotIn(gone, text)
        self.assertEqual(r['week']['sentence'], 'Last 7 days: 4 early stops in chats and their helpers. Of the stops, '
                                                '1 went on by itself, 1 after you typed, 2 never went on.')
        off = [c for c in r['causes'] if c['key'] == 'offline'][0]
        self.assertEqual(off['went_on_min'], 1)         # the middle value of 1 minute and 3 days: the lower one
        self.assertEqual([c['key'] for c in r['causes']], ['signed_out', 'offline'])    # by count, then the order

    def test_went_on_by_itself_never_counts_a_prompt_she_typed(self):
        t0 = NOW - 7200
        # she typed a prompt that quotes the interrupt marker: it is a typed line, so the stop did not go on by itself
        self.chat('a' * 8 + '-0201', [reply(t0 - 10), stop(t0, 'offline'),
                                      prompt(t0 + 60, '[Request interrupted by user] made-up words she pasted'),
                                      reply(t0 + 90)])
        self.chat('a' * 8 + '-0202', [reply(t0 - 10), stop(t0, 'offline'),
                                      prompt(t0 + 60, 'about the line [Request interrupted by user]'),
                                      reply(t0 + 90)])
        self.chat('a' * 8 + '-0203', [reply(t0 - 10), stop(t0, 'offline'), reply(t0 + 30)])    # the model's, first
        w = self.build()['week']['d7']
        self.assertEqual((w['itself'], w['typed'], w['never']), (1, 2, 0))

    def test_a_limit_sentence_quoted_in_a_message_is_not_a_stop(self):
        t0 = NOW - 7200
        quoted = "You've hit your Fable limit · resets Nov 30 at 1pm (America/Los_Angeles)."
        said = reply(t0 + 30)
        said['message']['content'] = [{'type': 'text', 'text': 'The notice read: ' + quoted}]
        self.chat('a' * 8 + '-0301', [prompt(t0, 'why did it say ' + quoted), said,
                                      prompt(t0 + 60, quoted), reply(t0 + 90)])
        r = self.build()
        self.assertEqual(r['week']['d14']['stops'], 0)
        self.assertEqual(r['causes'], [])

    def test_a_stop_copied_into_a_fork_counts_once(self):
        t0 = NOW - 7200
        lines = [title('Weekly report'), reply(t0 - 10), stop(t0, 'offline', mid='same-stop')]
        self.chat('d' * 8 + '-0004', lines)
        self.chat('d' * 8 + '-0004', [reply(t0 - 10), stop(t0, 'offline', mid='same-stop')], sub='fork1')
        r = self.build()
        self.assertEqual([c['count'] for c in r['causes'] if c['key'] == 'offline'], [1])

    def test_a_stop_older_than_fourteen_days_is_left_out(self):
        old = NOW - 15 * 86400
        self.chat('e' * 8 + '-0005', [reply(old - 10), stop(old, 'offline')], age=15 * 86400)
        self.assertEqual(self.build()['week']['d14']['stops'], 0)

    def test_helper_stops_are_counted_as_helpers(self):
        t0 = NOW - 7200
        self.chat('f' * 8 + '-0006', [title('Lab notes'), reply(t0 - 100)])
        self.chat('f' * 8 + '-0006', [reply(t0 - 10, model='claude-sonnet-5-5'), stop(t0, 'signed_out')], sub='h1')
        w = self.build()['week']['d7']
        self.assertEqual((w['chats'], w['helpers']), (0, 1))

    def test_no_message_text_is_kept(self):
        t0 = NOW - 7200
        secret = 'made-up private words that must not show'
        self.chat('g' * 8 + '-0007', [prompt(t0 - 60, secret), reply(t0 - 30), stop(t0, 'offline')])
        self.build()
        with open(os.path.join(self.data, keeprun.CACHE_FILE)) as f:
            self.assertNotIn(secret, f.read())
        self.assertNotIn(secret, json.dumps(self.build()))


class Cache(Fixture):
    def test_a_file_is_read_again_only_when_it_changed(self):
        t0 = NOW - 7200
        p = self.chat('h' * 8 + '-0008', [reply(t0 - 10), stop(t0, 'offline')])
        first = keeprun.scan(NOW)
        self.assertEqual(first['read'], 1)
        self.assertEqual(keeprun.scan(NOW)['read'], 0)
        with open(p, 'a') as f:
            f.write(json.dumps(reply(t0 + 60)) + '\n')
        os.utime(p, (NOW - 100, NOW - 100))
        again = keeprun.scan(NOW)
        self.assertEqual(again['read'], 1)
        self.assertIsNotNone(again['files'][0]['data']['stops'][0][6])

    def test_a_pass_stops_at_its_budget_and_says_so(self):
        for i in range(3):
            self.chat('%08d-00%02d' % (i, i), [reply(NOW - 7300), stop(NOW - 7200, 'offline')])
        r = self.build(budget=-1)
        self.assertTrue(any('still being read' in p for p in r['problems']))
        self.assertIsNone(r['week']['d7'])                      # no totals while part of the history is unread
        r = self.build()                                        # the next pass reads the rest
        self.assertEqual(r['week']['d7']['stops'], 3)
        self.assertFalse(r['problems'])

    def test_no_totals_until_every_chat_file_was_read_once(self):
        for i in range(3):
            self.chat('%08d-01%02d' % (i, i), [reply(NOW - 7300), stop(NOW - 7200, 'offline')])
        r = self.build(budget=-1)
        self.assertEqual(r['week']['sentence'], 'Reading your chats: 0 of 3 files so far.')
        self.assertEqual(r['week']['reading'], {'done': 0, 'of': 3})
        self.assertEqual((r['week']['d7'], r['week']['d14'], r['causes'], r['runs']), (None, None, [], []))
        self.assertNotIn('Nothing', r['week']['sentence'])
        r = self.build()
        self.assertIsNone(r['week']['reading'])
        self.assertTrue(r['week']['sentence'].startswith('Last 7 days: 3 early stops in chats.'))

    def test_a_changed_file_the_pass_has_no_time_for_keeps_its_earlier_reading(self):
        t0 = NOW - 7200
        p = self.chat('h' * 8 + '-0108', [title('Grows'), reply(t0 - 10), stop(t0, 'offline')])
        self.assertEqual(self.build()['week']['d7']['stops'], 1)
        with open(p, 'a') as f:
            f.write(json.dumps(prompt(t0 + 60)) + '\n')
        os.utime(p, (NOW - 100, NOW - 100))
        r = self.build(budget=-1)
        self.assertIsNone(r['week']['reading'])                 # read once already: totals stay
        self.assertEqual(r['week']['d7']['stops'], 1)           # its earlier reading is kept
        self.assertTrue(any('still being read' in x for x in r['problems']))

    def test_a_grown_chat_is_read_on_from_where_the_last_read_ended(self):
        t0 = NOW - 7200
        p = self.chat('h' * 8 + '-0109', [title('Grows'), reply(t0 - 10), stop(t0, 'offline')])
        keeprun.scan(NOW)
        size = os.path.getsize(p)
        with open(p, 'a') as f:
            f.write(json.dumps(reply(t0 + 30)) + '\n')
            f.write('{"type":"user","timestamp":"half a line still being wri')
        os.utime(p, (NOW - 100, NOW - 100))
        starts, real = [], keeprun.usage._marked_lines

        def spy(f, marks):
            starts.append(f.f.tell() if hasattr(f, 'f') else f.tell())
            return real(f, marks)
        with mock.patch.object(keeprun.usage, '_marked_lines', spy):
            again = keeprun.scan(NOW)
        self.assertEqual(starts, [size])                        # not from byte 0
        data = again['files'][0]['data']
        self.assertEqual(data['stops'][0][6], keeprun.usage.parse_ts(ts(t0 + 30)))   # its next reply was found
        self.assertEqual(data['title'], 'Grows')
        with open(p, 'w') as f:                                 # a file written anew is read from the start
            f.write(json.dumps(reply(t0 + 5)) + '\n')
        os.utime(p, (NOW - 50, NOW - 50))
        starts[:] = []
        with mock.patch.object(keeprun.usage, '_marked_lines', spy):
            data = keeprun.scan(NOW)['files'][0]['data']
        self.assertEqual((starts, data['stops'], data['title']), ([0], [], None))

    def test_the_error_line_of_a_stop_is_not_kept(self):
        self.chat('h' * 8 + '-0110', [reply(NOW - 7300), stop(NOW - 7200, 'signed_out')])
        self.build()
        with open(os.path.join(self.data, keeprun.CACHE_FILE)) as f:
            text = f.read()
        self.assertNotIn('logged in', text)
        self.assertNotIn('/login', text)

    def test_a_broken_cache_is_read_from_scratch(self):
        self.chat('i' * 8 + '-0009', [reply(NOW - 7300), stop(NOW - 7200, 'offline')])
        with open(os.path.join(self.data, keeprun.CACHE_FILE), 'w') as f:
            f.write('{not json')
        self.assertEqual(self.build()['week']['d7']['stops'], 1)


class Runs(Fixture):
    def run_folder(self, session, wf, agents, status='completed', name='build-and-test', start=None):
        folder = os.path.join(self.proj, session, 'subagents', 'workflows', wf)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, 'journal.jsonl'), 'w') as f:
            f.write(json.dumps({'type': 'launched'}) + '\n')
            for aid, label, ev, lines, model in agents:
                f.write(json.dumps({'type': 'started', 'key': label, 'agentId': aid, 'label': label,
                                    'phase': 'build'}) + '\n')
                if ev:
                    f.write(json.dumps({'type': ev, 'key': label, 'agentId': aid}) + '\n')
                p = os.path.join(folder, 'agent-%s.jsonl' % aid)
                with open(p, 'w') as g:
                    for line in lines:
                        g.write(json.dumps(line) + '\n')
                os.utime(p, (NOW - 3600, NOW - 3600))
                with open(p[:-len('.jsonl')] + '.meta.json', 'w') as g:
                    json.dump({'agentType': 'workflow-subagent', 'description': label, 'workflowPhase': 'build',
                               'model': model}, g)
        rec = os.path.join(self.proj, session, 'workflows')
        os.makedirs(rec, exist_ok=True)
        with open(os.path.join(rec, wf + '.json'), 'w') as f:
            json.dump({'workflowName': name, 'status': status, 'timestamp': ts(start or NOW - 9000)}, f)

    def test_dead_agents_why_completed_and_run_again(self):
        s = 'j' * 8 + '-0010'
        self.chat(s, [title('Page redesign'), reply(NOW - 9500)])
        t0 = NOW - 8000
        self.run_folder(s, 'wf_one', [
            ('a1', 'builder', 'result', [reply(t0, model='claude-opus-5-5', out=500)], 'opus'),
            ('a2', 'tests', 'failed', [reply(t0, model='claude-opus-5-5', out=700), stop(t0 + 10, 'signed_out')],
             'opus'),
            ('a3', 'review findings', None, [reply(t0, model='claude-opus-5-5', out=300),
                                             stop(t0 + 20, 'offline')], 'opus'),
            ('a4', 'notes', None, [reply(t0, model='claude-sonnet-5-5', out=50),
                                   prompt(t0 + 20, '[Request interrupted by user]')], 'sonnet')])
        self.run_folder(s, 'wf_two', [('b1', 'tests', 'result', [reply(t0 + 3600, model='claude-sonnet-5-5')],
                                       'sonnet')], start=t0 + 3500, name='other-workflow')
        r = self.build()
        run = [x for x in r['runs'] if x['name'] == 'build-and-test' and x['dead']][0]
        self.assertEqual((run['agents'], run['dead'], run['returned']), (4, 2, 1))
        self.assertEqual(run['why'], {'signed_out': 1, 'offline': 1})     # the one a person stopped did not die
        self.assertTrue(run['said_completed'])
        # no later run of build-and-test in this chat (the later one is another workflow): both are not run again
        self.assertEqual((run['later_run'], run['never_rerun']), (False, 2))
        self.assertEqual(run['chat'], 'Page redesign')
        step = [x for x in r['steps'] if x['key'] == 'runs'][0]
        self.assertIn('build-and-test in Page redesign', step['items'][0]['title'])
        self.assertIn('the run still said completed', step['items'][0]['note'])
        facts = [x for x in r['steps'] if x['key'] == 'workflows'][0]['why']
        self.assertEqual(facts, '40% of workflow agents died (2 of 5: 1 signed out, 1 offline).')
        for gone in ('tokens', 'a second time', 'Opus', 'Sonnet', 'review', 'began'):
            self.assertNotIn(gone, facts)                       # not measured exactly: not shown
        self.assertNotIn('save', facts.lower())
        agents = [c for c in r['causes'] if c['key'] == 'agents'][0]
        self.assertEqual((agents['count'], agents['step']), (2, 'runs'))

    def test_long_workflow_names_and_labels_are_cut_like_titles(self):
        s = 'j' * 8 + '-0040'
        self.chat(s, [title('Page redesign'), reply(NOW - 9500)])
        t0 = NOW - 8000
        self.run_folder(s, 'wf_one', [('a1', 'L' * 100, 'failed', [reply(t0), stop(t0 + 10, 'offline')], 'opus')],
                        name='N' * 100)
        run = self.build()['runs'][0]
        self.assertEqual((len(run['name']), run['name'][-1]), (60, '…'))
        self.assertEqual((len(run['labels'][0]), run['labels'][0][-1]), (60, '…'))

    def test_each_agents_meta_file_is_read_once(self):
        s = 'j' * 8 + '-0041'
        self.chat(s, [title('Page redesign'), reply(NOW - 9500)])
        t0 = NOW - 8000
        self.run_folder(s, 'wf_one', [('a1', 'builder', 'failed', [reply(t0), stop(t0 + 10, 'offline')], 'opus')])
        with mock.patch.object(keeprun, '_meta', wraps=keeprun._meta) as meta:
            self.build()
            self.build()
        self.assertEqual(meta.call_count, 1)

    def test_a_later_run_of_the_same_workflow_in_the_same_chat_is_yes_or_no(self):
        s, other = 'j' * 8 + '-0030', 'j' * 8 + '-0031'
        self.chat(s, [title('Page redesign'), reply(NOW - 9500)])
        self.chat(other, [title('Elsewhere'), reply(NOW - 9500)])
        t0 = NOW - 8000
        self.run_folder(s, 'wf_one', [('a1', 'builder', 'failed', [reply(t0), stop(t0 + 10, 'offline')], 'opus')],
                        start=t0)
        self.run_folder(other, 'wf_two', [('b1', 'builder', 'result', [reply(t0 + 600)], 'opus')], start=t0 + 500)
        r = self.build()
        run = [x for x in r['runs'] if x['session'] == s][0]
        self.assertEqual((run['later_run'], run['never_rerun']), (False, 1))   # the same name in ANOTHER chat: no
        self.run_folder(s, 'wf_three', [('c1', 'tests', 'result', [reply(t0 + 900)], 'opus')], start=t0 + 800)
        r = self.build()
        run = [x for x in r['runs'] if x['session'] == s][0]
        self.assertEqual((run['later_run'], run['never_rerun']), (True, 0))
        self.assertNotIn('runs', [x['key'] for x in r['steps']])
        self.assertNotIn('tokens', json.dumps(r))

    def test_an_agent_a_person_stopped_or_that_went_on_after_a_stop_is_not_dead(self):
        s = 'j' * 8 + '-0021'
        self.chat(s, [title('Page redesign'), reply(NOW - 9500)])
        t0 = NOW - 8000
        self.run_folder(s, 'wf_one', [
            ('a1', 'builder', 'result', [reply(t0, model='claude-opus-5-5')], 'opus'),
            ('a2', 'notes', 'failed', [reply(t0, model='claude-sonnet-5-5'),
                                       prompt(t0 + 20, '[Request interrupted by user]')], 'sonnet'),
            ('a3', 'tests', None, [reply(t0, model='claude-opus-5-5'), stop(t0 + 10, 'offline'),
                                   reply(t0 + 60, model='claude-opus-5-5', tools=('Bash',))], 'opus')])
        r = self.build()
        self.assertEqual(r['runs'], [])                         # no run lost an agent
        self.assertNotIn('agents', [c['key'] for c in r['causes']])
        self.assertEqual(r['week']['d14']['agents_dead'], 0)
        self.assertNotIn('runs', [x['key'] for x in r['steps']])

    def test_a_dead_workflow_agent_is_counted_once_never_as_a_stop_in_chats(self):
        s = 'j' * 8 + '-0020'
        self.chat(s, [title('Page redesign'), reply(NOW - 9500)])
        t0 = NOW - 8000
        self.run_folder(s, 'wf_one', [
            ('a1', 'builder', 'failed', [reply(t0, model='claude-opus-5-5'), stop(t0 + 10, 'signed_out')], 'opus')])
        r = self.build()
        self.assertEqual(r['week']['d7']['agents_dead'], 1)
        self.assertEqual((r['week']['d7']['stops'], r['week']['d14']['stops']), (0, 0))
        self.assertEqual([c['key'] for c in r['causes']], ['agents'])
        self.assertNotIn('early stop', r['week']['sentence'])


class OpenChats(Fixture):
    def test_waiting_cut_off_and_quiet(self):
        sid = lambda c: c * 8 + '-1111-2222-3333-444455556666'  # noqa: E731
        self.chat(sid('a'), [title('Asks a question'), prompt(NOW - 1500),
                             reply(NOW - 1400, tools=('AskUserQuestion',))], age=1400)
        self.chat(sid('b'), [title('Asks to run'), prompt(NOW - 1500), reply(NOW - 1400, tools=('Bash',))],
                  age=1400)
        self.chat(sid('c'), [title('Window closed'), prompt(NOW - 1500), reply(NOW - 1400, tools=('Bash',)),
                             result(NOW - 1390, shutdown=True)], age=1390)
        self.chat(sid('d'), [title('Program gone'), prompt(NOW - 1500), reply(NOW - 1400, tools=('Read',)),
                             result(NOW - 1395)], age=1395)
        self.chat(sid('e'), [title('Done long ago'), prompt(NOW - 3 * 86400), reply(NOW - 3 * 86400 + 60)],
                  age=3 * 86400)
        self.chat(sid('f'), [title('Still working'), prompt(NOW - 60), reply(NOW - 30, tools=('Bash',))], age=30)
        self.chat(sid('g'), [title('Helper busy'), prompt(NOW - 1500), reply(NOW - 1400, tools=('Bash',))],
                  age=1400)
        self.chat(sid('g'), [reply(NOW - 20, model='claude-sonnet-5-5')], age=20, sub='busy')
        running = [sid(c) for c in 'abefg']
        self.cmds['ps'] = '\n'.join('/x/anthropic.claude-code-2.1.332/resources/native-binary/claude --output-format '
                                    'stream-json --resume=%s --debug' % s for s in running) + '\n/usr/bin/other --x'
        self.cmds['swap'] = 'vm.swapusage: total = 2048.00M  used = 1900.00M  free = 148.00M  (encrypted)'
        r = self.build()
        # a tool call with no result is not called waiting (it may be running); a chat not found in ps is not known
        # to have ended while Claude Code programs run
        self.assertEqual([(w['title'], w['what']) for w in r['waiting']], [('Asks a question', 'a question')])
        self.assertEqual(sorted(c['title'] for c in r['cut_off']), ['Window closed'], r['problems'])
        self.assertEqual([q['title'] for q in r['mac']['quiet']], ['Done long ago'])
        self.assertEqual(r['mac']['programs'], 5)
        keys = [x['key'] for x in r['steps']]
        self.assertIn('waiting', keys)
        self.assertIn('cut', keys)
        cut = [x for x in r['steps'] if x['key'] == 'cut'][0]
        self.assertTrue(cut['title'].startswith('A chat was working at'))
        self.assertEqual(cut['why'], 'Open each and type continue.')
        self.assertNotIn('memory', keys)                        # 1.9 GB of swap: under the fixed figure
        self.cmds['swap'] = 'vm.swapusage: total = 10240.00M  used = 9216.00M  free = 1024.00M  (encrypted)'
        mem = [x for x in self.build()['steps'] if x['key'] == 'memory'][0]
        self.assertEqual(mem['why'], '9 GB of swap in use (10 GB allocated). 1 open chat has been quiet for 2 days or '
                                     'more.')
        self.assertEqual([i['title'] for i in mem['items']], ['Done long ago'])

    def test_waiting_minutes_run_from_the_question_not_the_file_time(self):
        sid = 'abcdef01-1111-2222-3333-444455556666'
        self.chat(sid, [title('Asks a question'), prompt(NOW - 7300), reply(NOW - 7200, tools=('AskUserQuestion',))],
                  age=1200)                                     # a reload touched the file 20 minutes ago
        self.cmds['ps'] = '/x/native-binary/claude --resume=%s' % sid
        w = self.build()['waiting']
        self.assertEqual([x['minutes'] for x in w], [120])

    def test_a_chat_cut_off_days_ago_is_not_a_step_even_if_its_file_was_touched(self):
        self.chat('w' * 8 + '-0000-0000-0000-000000000000',
                  [title('Old'), prompt(NOW - 7 * 86400), reply(NOW - 7 * 86400 + 5, tools=('Read',)),
                   result(NOW - 7 * 86400 + 9)], age=3600)        # a window reload wrote to it an hour ago
        self.assertEqual(self.build()['cut_off'], [])

    def test_three_chats_that_ended_together_were_a_reload(self):
        for i, c in enumerate('xyz'):
            self.chat(c * 8 + '-0000-0000-0000-000000000000',
                      [title('Chat %s' % c), prompt(NOW - 1500), reply(NOW - 1400, tools=('Bash',)),
                       result(NOW - 1390 + i * 5, shutdown=True)], age=1390 - i * 5)
        steps = [x for x in self.build()['steps'] if x['key'] == 'cut']
        self.assertEqual(len(steps), 1)
        self.assertTrue(steps[0]['title'].startswith('3 chats were working at '), steps[0]['title'])
        self.assertNotIn('VS Code', steps[0]['title'])


class TheMac(Fixture):
    PMSET = '\n'.join((
        '2026-11-20 22:21:56 -0800 Sleep \tEntering Sleep state due to \'Clamshell Sleep\':TCPKeepAlive=active '
        'Using Batt (Charge:100%) 278 secs',
        '2026-11-20 22:21:58 -0800 Wake Requests \t[process=mDNSResponder request=Maintenance deltaSecs=7198]',
        '2026-11-20 22:26:34 -0800 DarkWake \tDarkWake from Deep Idle [CDNP] : due to rtc/SleepService Using '
        'BATT (Charge:100%) 45 secs',
        '2026-11-21 00:40:05 -0800 DarkWake \tDarkWake from Deep Idle [CDNP] : due to rtc/SleepService Using '
        'BATT (Charge:99%) 2 secs',
        '2026-11-21 13:10:27 -0800 Wake \tWake from Deep Idle [CDNVA] : due to lid UserActivity Assertion '
        'Using BATT (Charge:98%)',
        '2026-11-21 14:00:00 -0800 Sleep \tEntering Sleep state due to \'Idle Sleep\': Using AC (Charge:98%)',
        '2026-11-21 14:30:00 -0800 Wake \tDarkWake to FullWake from Deep Idle [CDNVA] : due to '
        'UserActivity Assertion Using AC (Charge:98%)'))

    def test_sleep_spans_lid_battery_and_wakes(self):
        spans, wakes = keeprun.parse_pmset_log(self.PMSET)
        self.assertEqual(len(spans), 2)
        start, end, why, batt, charge, dark = spans[0]
        self.assertEqual((why, batt, charge, dark), ('Clamshell Sleep', True, 100, 2))
        self.assertAlmostEqual((end - start) / 3600.0, 14.81, places=1)
        self.assertFalse(spans[1][3])                           # the second began on power
        self.assertEqual(len(wakes), 4)                         # "Wake Requests" is not a wake

    def test_the_lid_step_and_the_offline_row_near_a_wake(self):
        self.cmds['pmset_log'] = self.PMSET
        # the dark wake at 00:40:05 on Nov 21 (local, -0800) and a network stop in the same second
        wake = 1795250405.0
        self.chat('k' * 8 + '-0011', [title('Overnight'), reply(wake - 3000), stop(wake, 'offline')],
                  age=86400 * 6)
        with mock.patch.object(keeprun, 'DAYS', 14):
            r = keeprun.build(now=wake + 5 * 86400, lines=[], auto_on=True, awake_on=False)
        lid = [x for x in r['steps'] if x['key'] == 'lid'][0]
        self.assertEqual(lid['why'], 'The Mac slept 1 time on battery with the lid shut (15 hours asleep in all).')
        self.assertEqual(lid['action']['post'], '/api/awake')   # Keep the Mac awake is off: the press turns it on
        names = dict((c['key'], c['name']) for c in r['causes'])
        self.assertEqual(names['offline'], 'Offline')
        self.assertEqual([c['note'] for c in r['causes'] if c['key'] == 'offline'],
                         ['1 of 1 within 3 minutes after a wake'])
        steps = dict((c['key'], c['step']) for c in r['causes'])
        self.assertEqual(steps['offline'], 'lid')               # a stop on waking ends in the lid's step
        self.assertEqual(names['lid'], 'Lid shut on battery')

    def test_pmset_log_runs_at_most_every_ten_minutes(self):
        self.cmds['pmset_log'] = self.PMSET
        keeprun.mac_facts(NOW)
        keeprun.mac_facts(NOW + 60)
        keeprun.mac_facts(NOW + 700)
        self.assertEqual(sum(1 for c in self.ran if c[:3] == ['/usr/bin/pmset', '-g', 'log']), 2)

    def test_a_pmset_log_that_times_out_keeps_the_last_spans(self):
        self.cmds['pmset_log'] = self.PMSET
        keeprun.mac_facts(NOW)
        self.cmds['pmset_log'] = None                           # under load the next run times out
        self.assertEqual(len(keeprun.mac_facts(NOW + 700)['spans']), 2)

    def test_a_pmset_log_that_fails_is_remembered_like_a_good_one(self):
        self.cmds['pmset_log'] = None
        keeprun.mac_facts(NOW)
        out = keeprun.mac_facts(NOW + 60)
        self.assertEqual(sum(1 for c in self.ran if c[:3] == ['/usr/bin/pmset', '-g', 'log']), 1)
        self.assertEqual(out['problems'], ['The Mac’s sleep record could not be read.'])
        r = self.build()
        self.assertEqual([x for x in r['problems'] if 'sleep' in x], ['The Mac’s sleep record could not be read.'])
        self.cmds['pmset_log'] = self.PMSET
        self.assertEqual(keeprun.mac_facts(NOW + 700)['problems'], [])

    def test_a_command_that_cannot_run_leaves_its_part_out(self):
        self.cmds.update(pmset_log=None, batt=None, swap=None, ps=None)
        r = self.build()
        self.assertEqual((r['mac']['on_battery'], r['mac']['swap'], r['mac']['programs']), (None, None, None))


class ClaudeCode(Fixture):
    def install(self, version, npm=True):
        pkg = os.path.join(self.tmp, 'npm', 'lib', 'node_modules', '@anthropic-ai', 'claude-code')
        os.makedirs(os.path.join(pkg, 'bin'), exist_ok=True)
        with open(os.path.join(pkg, 'package.json'), 'w') as f:
            json.dump({'name': '@anthropic-ai/claude-code', 'version': version}, f)
        exe = os.path.join(pkg, 'bin', 'claude.exe')
        open(exe, 'w').close()
        bindir = os.path.join(self.tmp, 'npm', 'bin')
        os.makedirs(bindir, exist_ok=True)
        os.symlink(exe, os.path.join(bindir, 'claude'))
        keeprun.ENV['path'] = [bindir]

    def test_an_old_terminal_claude_gets_the_line_to_copy(self):
        self.install('2.1.309')
        os.makedirs(os.path.join(self.ext, 'anthropic.claude-code-2.1.332-darwin-arm64'))
        r = self.build()
        self.assertEqual((r['claude']['terminal'], r['claude']['newest']), ('2.1.309', '2.1.332'))
        step = [x for x in r['steps'] if x['key'] == 'terminal'][0]
        # which program it looked at and the two versions; no line to copy (what a terminal finds is not known
        # from here) and nothing about Fable
        self.assertIsNone(step['action'])
        self.assertEqual(step['title'], 'The claude program at %s is older than the VS Code extension.'
                         % os.path.join(self.tmp, 'npm', 'bin', 'claude'))
        self.assertEqual(step['why'], 'It is Claude Code 2.1.309. The extension is 2.1.332.')
        self.assertNotIn('Fable', step['title'] + step['why'])

    def test_a_terminal_claude_new_enough_for_fable_has_no_step(self):
        self.install('2.1.330')
        os.makedirs(os.path.join(self.ext, 'anthropic.claude-code-2.1.332-darwin-arm64'))
        self.assertNotIn('terminal', [x['key'] for x in self.build()['steps']])

    def test_an_api_key_is_said_never_shown(self):
        key = 'sk-ant-made-up-0000000000000000000000'
        with open(self.settings, 'w') as f:
            json.dump({'env': {'ANTHROPIC_API_KEY': key}}, f)
        r = self.build()
        self.assertTrue(r['claude']['api_key'])
        step = [x for x in r['steps'] if x['key'] == 'api_key'][0]
        self.assertEqual(step['title'], 'Claude Code’s settings hold an API key (ANTHROPIC_API_KEY in env).')
        self.assertNotIn(key, json.dumps(r))
        self.assertNotIn('sk-ant', json.dumps(r))

    def test_a_key_helper_or_an_auth_token_is_named_as_what_it_is(self):
        with open(self.settings, 'w') as f:
            json.dump({'apiKeyHelper': '/made/up/helper.sh'}, f)
        step = [x for x in self.build()['steps'] if x['key'] == 'api_key'][0]
        self.assertEqual(step['title'], 'Claude Code’s settings hold a key helper (apiKeyHelper).')
        self.assertNotIn('env', step['why'])
        self.assertNotIn('helper.sh', json.dumps(self.build()))
        with open(self.settings, 'w') as f:
            json.dump({'env': {'ANTHROPIC_AUTH_TOKEN': 'made-up-token'}}, f)
        step = [x for x in self.build()['steps'] if x['key'] == 'api_key'][0]
        self.assertEqual(step['title'], 'Claude Code’s settings hold an auth token (ANTHROPIC_AUTH_TOKEN in env).')
        self.assertNotIn('billed', step['why'])
        self.assertNotIn('made-up-token', json.dumps(self.build()))


class Steps(Fixture):
    def test_turn_on_shows_only_when_there_is_a_switch_and_it_is_off(self):
        self.chat('l' * 8 + '-0012', [reply(NOW - 7300), stop(NOW - 7200, 'limit_model')])
        fake = types.ModuleType('switchboard.running')
        fake.status = lambda: {'on': False, 'since': None, 'plugin': None, 'problems': []}
        with mock.patch.dict(sys.modules, {'switchboard.running': fake}):
            r = self.build()
            self.assertEqual(r['steps'][0]['key'], 'on')
            self.assertEqual(r['steps'][0]['action']['post'], '/api/running')
            row = [c for c in r['causes'] if c['key'] == 'limit_model'][0]
            self.assertEqual((row['handled'], row['step']), (None, 'on'))
            fake.status = lambda: {'on': True, 'since': NOW, 'plugin': '0.1.0', 'problems': []}
            r = self.build()
            self.assertNotIn('on', [x['key'] for x in r['steps']])
            row = [c for c in r['causes'] if c['key'] == 'limit_model'][0]
            self.assertIsNone(row['handled'])                   # on, but it continued none of these
        r = self.build()                                        # no module in this build: no switch, no press
        self.assertNotIn('on', [x['key'] for x in r['steps']])
        self.assertFalse(r['now']['switch'])

    def test_automatic_switching_off(self):
        self.chat('m' * 8 + '-0013', [reply(NOW - 7300), stop(NOW - 7200, 'limit_5hour')])
        lines = [{'t': NOW - 90000, 'kind': 'auto', 'on': False, 'text': 'Automatic switching off'}]
        r = self.build(lines=lines, auto_on=False)
        step = [x for x in r['steps'] if x['key'] == 'auto'][0]
        self.assertEqual(step['action']['body'], {'enabled': True})
        row = [c for c in r['causes'] if c['key'] == 'auto_off'][0]
        self.assertEqual((row['handled'], row['step']), (None, 'auto'))
        self.assertNotIn('auto', [x['key'] for x in self.build(lines=lines, auto_on=True)['steps']])
        blink = [{'t': NOW - 900, 'kind': 'auto', 'on': False, 'text': 'off'},
                 {'t': NOW - 900, 'kind': 'auto', 'on': True, 'text': 'on'}]
        self.assertNotIn('auto_off', [c['key'] for c in self.build(lines=blink)['causes']])

    def test_automatic_switching_step_only_for_stops_while_it_was_off(self):
        self.chat('m' * 8 + '-0113', [reply(NOW - 7300), stop(NOW - 7200, 'limit_5hour')])
        lines = [{'t': NOW - 3600, 'kind': 'auto', 'on': False, 'text': 'Automatic switching off'}]
        r = self.build(lines=lines, auto_on=False)                  # the stop came before it was turned off
        self.assertNotIn('auto', [x['key'] for x in r['steps']])
        lines = [{'t': NOW - 9000, 'kind': 'auto', 'on': False, 'text': 'off'}]
        step = [x for x in self.build(lines=lines, auto_on=False)['steps'] if x['key'] == 'auto'][0]
        self.assertEqual(step['why'], '1 early stop at a 5-hour or weekly limit in the last 14 days came while it was '
                                      'off.')

    def test_the_build_without_lane_mod_says_nothing_of_keep_running_and_room_changes_the_rows(self):
        import switchboard                     # room not importable here, whether or not room.py is in the tree
        gone, attrs = mock.patch.dict(sys.modules, {'switchboard.room': None}), mock.patch.dict(switchboard.__dict__)
        for p in (gone, attrs):
            p.start()
            self.addCleanup(p.stop)
        switchboard.__dict__.pop('room', None)
        self.chat('l' * 8 + '-0112', [reply(NOW - 7300), stop(NOW - 7200, 'limit_model')])
        lines = [{'t': NOW - 20000, 'kind': 'limit', 'text': 'Every account is out. Back at 12:19 PM.'},
                 {'t': NOW - 6680, 'kind': 'switch', 'from': 'a@example.com', 'to': 'b@example.com', 'by': 'auto'}]
        r = self.build(lines=lines)
        self.assertFalse(r['now']['switch'])
        text = json.dumps([r['causes'], r['steps']])
        self.assertNotIn('Keep running', text)
        self.assertNotIn('refus', text.lower())
        row = [c for c in r['causes'] if c['key'] == 'limit_model'][0]
        self.assertEqual((row['handled'], row['step'], row['doing']),
                         (None, None, 'A request stopped by the limit ends the turn.'))
        fake = types.ModuleType('switchboard.room')
        with mock.patch.dict(sys.modules, {'switchboard.room': fake}):
            r = self.build(lines=lines)
        by = dict((c['key'], c) for c in r['causes'])
        self.assertEqual((by['limit_model']['handled'], by['limit_model']['step'], by['limit_model']['doing']),
                         (None, None, 'Automatic switching is on.'))
        self.assertEqual((by['every_out']['handled'], by['every_out']['doing']),
                         (None, 'Switchboard says every account is out only when no account has room on its '
                                 '5-hour and weekly windows.'))

    def test_the_on_step_says_what_keep_running_does_and_nothing_wider(self):
        self.chat('l' * 8 + '-0114', [reply(NOW - 7300), stop(NOW - 7200, 'limit_model')])
        fake = types.ModuleType('switchboard.running')
        fake.status = lambda: {'on': False, 'since': None, 'plugin': None, 'problems': []}
        with mock.patch.dict(sys.modules, {'switchboard.running': fake}):
            r = self.build()
        step = [x for x in r['steps'] if x['key'] == 'on'][0]
        self.assertTrue(step['why'].endswith('When a model’s usage limit is reached on the account in use, Keep '
                                             'running sends the request to the next model with room, and it '
                                             'continues a chat that stopped.'), step['why'])
        self.assertNotIn('refus', json.dumps(r).lower())

    def test_every_account_out_and_panels_under_their_switch(self):
        lines = [{'t': NOW - 20000, 'kind': 'limit', 'text': 'Every account is out. Back at 12:19 PM.'},
                 {'t': NOW - 6680, 'kind': 'switch', 'from': 'a@example.com', 'to': 'b@example.com', 'by': 'auto'}]
        os.makedirs(os.path.join(self.logs, '20261126T1', 'window1', 'exthost', 'Anthropic.claude-code'))
        log = os.path.join(self.logs, '20261126T1', 'window1', 'exthost', 'Anthropic.claude-code', 'Claude VSCode.log')
        at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(NOW - 6620))
        with open(log, 'w') as f:
            for i in range(3):
                f.write('%s.38%d [warning] Claude Code did not finish starting, and its process has exited\n' % (at, i))
        r = self.build(lines=lines)
        out = [c for c in r['causes'] if c['key'] == 'every_out'][0]
        self.assertEqual((out['count'], out['went_on_min'], out['handled']), (1, None, None))
        self.assertEqual([(s['to'], s['slow']) for s in r['switches']], [('b@example.com', 3)])
        self.assertNotIn('panels', [c['key'] for c in r['causes']])     # never a stop

    def test_vscode_logs_are_read_once_and_only_inside_the_time_budget(self):
        folder = os.path.join(self.logs, '20261126T1', 'window1', 'exthost', 'Anthropic.claude-code')
        os.makedirs(folder)
        at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(NOW - 6620))
        with open(os.path.join(folder, 'Claude VSCode.log'), 'w') as f:
            f.write('%s.381 [warning] Claude Code did not finish starting, and its process has exited\n' % at)
        late, left = keeprun.panel_lines(NOW, time.monotonic() - 1)
        self.assertEqual((late, left), ([], 1))                 # out of time: not read, and said so
        stats = {'from_cache': 0, 'read': 0, 'vanished': 0}
        times, left = keeprun.panel_lines(NOW, None, stats=stats)
        self.assertEqual((len(times), left, stats['read']), (1, 0, 1))
        stats = {'from_cache': 0, 'read': 0, 'vanished': 0}
        keeprun.panel_lines(NOW, time.monotonic() - 1, stats=stats)
        self.assertEqual((stats['read'], stats['from_cache']), (0, 1))  # kept: read once, even out of time

    def test_plugin_record_wins_for_the_same_stop(self):
        sid = 'n' * 8 + '-0014'
        t0 = NOW - 7200
        self.chat(sid, [title('Weekly report'), reply(t0 - 10), stop(t0, 'limit_model')])
        os.makedirs(os.path.join(self.data, 'running'), mode=0o700, exist_ok=True)
        with open(os.path.join(self.data, 'running', 'events.jsonl'), 'w') as f:
            for e in ({'v': 1, 'at': t0 + 1, 'session': sid, 'agent': None, 'kind': 'stop', 'cause': 'limit_model',
                       'asked': 'fable', 'sent': 'fable', 'turn': 't1', 'text': 'Fable limit'},
                      {'v': 1, 'at': t0 + 96, 'session': sid, 'agent': None, 'kind': 'resumed', 'of': t0 + 1,
                       'after_s': 95.0, 'by': 'plugin'},
                      {'v': 1, 'at': t0 + 200, 'session': sid, 'agent': None, 'kind': 'held', 'cause': 'offline',
                       'seconds': 41.2, 'turn': 't2'}):
                f.write(json.dumps(e) + '\n')
            f.write('not json\n')
        r = self.build()
        row = [c for c in r['causes'] if c['key'] == 'limit_model'][0]
        self.assertEqual((row['count'], row['continued'], row['went_on_min']), (1, 1, None))
        self.assertEqual((r['now']['held'], r['now']['resumed']), (1, 1))

    def test_a_plugin_stop_replaces_the_record_of_the_same_kind_never_a_helpers(self):
        sid = 'n' * 8 + '-0015'
        t0 = NOW - 7200
        self.chat(sid, [title('Weekly report'), reply(t0 - 100), stop(t0, 'signed_out')])
        self.chat(sid, [reply(t0 - 100, model='claude-sonnet-5-5'), stop(t0 - 20, 'signed_out')], sub='h1')
        os.makedirs(os.path.join(self.data, 'running'), mode=0o700, exist_ok=True)
        with open(os.path.join(self.data, 'running', 'events.jsonl'), 'w') as f:
            f.write(json.dumps({'v': 1, 'at': t0 + 1, 'session': sid, 'agent': None, 'kind': 'stop',
                                'cause': 'signed_out', 'asked': 'fable', 'turn': 't1'}) + '\n')
        w = self.build()['week']['d7']
        self.assertEqual((w['stops'], w['chats'], w['helpers']), (2, 1, 1))

    def test_a_fault_outside_the_parts_leaves_that_part_out_not_the_section(self):
        self.chat('n' * 8 + '-0016', [reply(NOW - 7300), stop(NOW - 7200, 'offline')])
        with mock.patch.object(keeprun, 'merge_plugin', side_effect=TypeError('made up')):
            r = self.build()
        # a part read from the chats failed: no totals, and never "nothing stopped"
        self.assertEqual((r['week']['d7'], r['week']['sentence']), (None, 'Your chat history could not be read.'))
        self.assertIn('Keep running’s record could not be read.', r['problems'])
        with mock.patch.object(keeprun, '_handled_rows', side_effect=KeyError('made up')), \
                mock.patch.object(keeprun, 'steps_for', side_effect=KeyError('made up')):
            r = self.build()
        self.assertEqual((r['causes'], r['steps']), ([], []))
        real = __import__

        def broken(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 1 and fromlist and 'running' in fromlist:
                raise SyntaxError('made up')
            return real(name, globals, locals, fromlist, level)
        with mock.patch('builtins.__import__', broken):
            self.assertEqual(keeprun.running_status(), (False, None))

    def test_the_plan_line_shows_only_while_fresh(self):
        os.makedirs(os.path.join(self.data, 'running'), mode=0o700, exist_ok=True)
        path = os.path.join(self.data, 'running', 'room.json')
        words = 'Fable is used up on a@example.com until Mon 1:00 PM. Chats run on Opus 5.5 until then.'
        fake = types.ModuleType('switchboard.running')       # the plan is Keep running's: shown only with it
        fake.status = lambda: {'on': True, 'since': NOW, 'plugin': '0.1.0', 'problems': []}
        with mock.patch.dict(sys.modules, {'switchboard.running': fake}):
            core.write_json(path, {'v': 1, 'at': NOW - 60, 'words': words})
            self.assertEqual(self.build()['now']['words'], words)
            core.write_json(path, {'v': 1, 'at': NOW - 1300, 'words': words})
            self.assertIsNone(self.build()['now']['words'])

    def test_many_cases_never_break_the_rules(self):
        """Thousands of made-up weeks: every cause row ends in Handled or names its step or says what is done, a press
        only ever goes to the three endpoints that turn something on, a step shows only with its numbers, and no
        sentence makes a promise about limits."""
        rnd = random.Random(37)
        banned = ('unlimited', 'bypass', 'avoid', 'never hit', 'more usage', '!', 'getting around', 'beat', 'refus',
                  'lost', 'tokens', 'sonnet')
        for i in range(2000):
            stops = []
            for j in range(rnd.randint(0, 6)):
                how = rnd.choice(('never', 'typed', 'itself', 'plugin', 'gave_up'))
                stops.append({'t': NOW - rnd.randint(0, 13 * 86400), 'cause': rnd.choice(keeprun.CAUSES),
                              'asked': rnd.choice(('fable', 'opus', None)), 'text': '', 'records': 1,
                              'session': 's%d' % j, 'kind': rnd.choice(('chat', 'helpers')), 'wf': None,
                              'title': 'Chat %d' % j, 'how': how, 'wait': None,
                              'lost': rnd.choice((None, rnd.randint(0, 9000))), 'resets': None, 'by': 'records'})
            stops.sort(key=lambda s: s['t'])
            dead = rnd.randint(0, 3)
            runs = [{'name': 'run', 'chat': 'Chat', 'session': 's', 'at': keeprun.iso(NOW - 3600), 't': NOW - 3600,
                     'agents': 4, 'returned': 4 - dead, 'dead': dead, 'why': {'offline': dead} if dead else {},
                     'said_completed': bool(dead), 'never_rerun': rnd.randint(0, dead), 'labels': [], 'models': []}]
            tot = {'agents': 4, 'dead': dead, 'why': {'offline': dead} if dead else {}, 'twice': rnd.choice((0, 50)),
                   'delays': [], 'review': rnd.randint(0, 3), 'review_opus': 0, 'never_rerun': runs[0]['never_rerun']}
            on, has = rnd.choice((True, False)), rnd.choice((True, False))
            on = on if has else None
            auto_now, awake_on = rnd.choice((True, False)), rnd.choice((True, False))
            spans = [[NOW - 86400, NOW - 80000, 'Clamshell Sleep', True, 90, 3]] if rnd.random() < .5 else []
            mac = {'spans': spans, 'wakes': [], 'swap': {'total_mb': 10240.0,
                                                         'used_mb': rnd.choice((10.0, 9000.0))},
                   'programs': 5}
            quiet = [{'title': 'Q', 'session': 'q', 'since': keeprun.iso(NOW - 3 * 86400), 't': NOW - 3 * 86400,
                      'days': 3}] if rnd.random() < .5 else []
            cut = [{'title': 'C', 'session': 'c', 'since': keeprun.iso(NOW - 900), 't': NOW - 900}] \
                if rnd.random() < .5 else []
            hist = {'switches': [], 'every_out': [[NOW - 9000, NOW - 8000]] if rnd.random() < .3 else [],
                    'auto_off': [[NOW - 9000, NOW - 8000]] if rnd.random() < .3 else [], 'auto_on': auto_now}
            claude = {'terminal': rnd.choice(((2, 1, 309), (2, 1, 332), None)), 'newest': (2, 1, 332),
                      'terminal_npm': True, 'api_key': rnd.choice((True, False))}
            room_in = rnd.choice((True, False))
            rows = keeprun._handled_rows(stops, tot, runs, mac, hist, on, has, None, auto_now, awake_on, NOW, room_in)
            steps = keeprun.steps_for(on, has, stops, runs, tot, [], cut, quiet, mac, claude, awake_on, auto_now,
                                      NOW, hist)
            for r in rows:
                self.assertNotIn('lost_min', r)
                text = (r['name'] + ' ' + r['doing']).lower()
                for word in banned[:-2]:
                    self.assertNotIn(word, text, (word, r))
                if not has:
                    self.assertNotIn('keep running', text, r)
                self.assertIn(r['handled'], ('yes', None))      # only where measured: on, and it continued some
                if r['handled'] == 'yes':
                    self.assertTrue(on and any(x['how'] == 'plugin' and x['cause'] == r['key'] for x in stops), r)
                if r['step'] == 'on':
                    self.assertTrue(has and not on, r)
                if r['step'] == 'auto':
                    self.assertFalse(auto_now)
            for s in steps:
                a = s.get('action')
                if a and a['kind'] == 'press':
                    self.assertIn(a['post'], ('/api/running', '/api/awake', '/api/auto'))
                    self.assertIn(True, a['body'].values())
                text = (s['title'] + ' ' + s['why']).lower()
                for word in banned:
                    self.assertNotIn(word, text, (word, s))
            keys = [s['key'] for s in steps]
            helped = [x for x in stops if x['cause'] not in ('flagged', 'too_long', 'blocked_403')]
            self.assertEqual('on' in keys, bool(has and not on and any(x['how'] in ('never', 'typed') for x in helped)),
                             (has, on, len(stops)))
            self.assertEqual('memory' in keys, bool(quiet) and mac['swap']['used_mb'] >= 8192)
            self.assertEqual('terminal' in keys, claude['terminal'] == (2, 1, 309))
            self.assertEqual('api_key' in keys, claude['api_key'])
            self.assertEqual('lid' in keys, bool(spans))
            if 'lid' in keys:
                lid = [s for s in steps if s['key'] == 'lid'][0]
                self.assertEqual(lid['action'] is None, awake_on)
            self.assertEqual('cut' in keys, bool(cut))
            self.assertEqual('runs' in keys, bool(runs[0]['never_rerun']))
            in_off = [x for x in stops if x['cause'] in ('limit_5hour', 'limit_weekly')
                      and any(a <= x['t'] <= b for a, b in hist['auto_off'])]
            self.assertEqual('auto' in keys, bool(not auto_now and in_off))
            if not has:
                self.assertFalse(any('keep running' in (s['title'] + ' ' + s['why']).lower() for s in steps))


class SecondPass(Fixture):
    """The second reader's findings: each number and sentence is what was measured, and nothing wider."""

    def runner(self, on=False):
        fake = types.ModuleType('switchboard.running')
        fake.status = lambda: {'on': on, 'since': None, 'plugin': None, 'problems': []}
        return mock.patch.dict(sys.modules, {'switchboard.running': fake})

    def test_a_failed_chat_part_never_says_nothing_stopped(self):
        self.chat('a' * 8 + '-2001', [reply(NOW - 7300), stop(NOW - 7200, 'offline')])
        for name in ('scan', 'chat_stops', 'runs_view', 'merge_plugin'):
            with mock.patch.object(keeprun, name, side_effect=RuntimeError('made up')):
                r = self.build()
            self.assertEqual((r['week']['d7'], r['week']['d14']), (None, None), name)
            self.assertEqual(r['week']['sentence'], 'Your chat history could not be read.', name)
            self.assertEqual([c for c in r['causes'] if c['key'] == 'offline'], [], name)

    def test_files_being_read_again_are_said_in_the_sentence(self):
        t0 = NOW - 7200
        p = self.chat('a' * 8 + '-2002', [reply(t0 - 10), stop(t0, 'offline')])
        q = self.chat('a' * 8 + '-2003', [reply(t0 - 10)])
        self.build()
        for path in (p, q):
            with open(path, 'a') as f:
                f.write(json.dumps(prompt(t0 + 60)) + '\n')
            os.utime(path, (NOW - 100, NOW - 100))
        r = self.build(budget=-1)
        self.assertEqual(r['week']['sentence'], 'Last 7 days: 1 early stop in chats. Of the stops, none went on by '
                                                'themselves, 1 never went on (2 files are being read again).')
        os.remove(p)
        self.build()
        with open(q, 'a') as f:
            f.write(json.dumps(reply(t0 + 90)) + '\n')
        os.utime(q, (NOW - 90, NOW - 90))
        r = self.build(budget=-1)
        self.assertEqual(r['week']['sentence'], 'Last 7 days: no early stop found in your chats so far (1 file is '
                                                'being read again).')

    def test_a_stop_from_minutes_ago_is_not_never(self):
        self.chat('a' * 8 + '-2004', [reply(NOW - 700), stop(NOW - 600, 'offline')], age=600)
        self.chat('a' * 8 + '-2005', [reply(NOW - 7300), stop(NOW - 7200, 'offline')])
        r = self.build()
        w = r['week']['d7']
        self.assertEqual((w['never'], w['recent']), (1, 1))
        self.assertTrue(r['week']['sentence'].endswith('1 never went on, 1 less than an hour ago.'),
                        r['week']['sentence'])
        row = [c for c in r['causes'] if c['key'] == 'offline'][0]
        self.assertEqual((row['never'], row['recent']), (1, 1))

    def test_a_line_claude_code_made_is_not_typed(self):
        t0 = NOW - 7200
        self.chat('a' * 8 + '-2006', [reply(t0 - 10), stop(t0, 'offline'),
                                      prompt(t0 + 30, '<task-notification>made-up</task-notification>'),
                                      reply(t0 + 40)])
        self.chat('a' * 8 + '-2007', [reply(t0 - 10), stop(t0, 'offline'),
                                      prompt(t0 + 30, '<local-command-stdout>x</local-command-stdout>'),
                                      prompt(t0 + 50, 'made-up words she typed'), reply(t0 + 60)])
        w = self.build()['week']['d7']
        self.assertEqual((w['itself'], w['typed']), (1, 1))

    def test_a_synthetic_line_that_is_not_an_error_is_no_stop(self):
        t0 = NOW - 7200
        self.chat('a' * 8 + '-2008', [reply(t0 - 10), stop(t0, 'empty')])
        s = 'a' * 8 + '-2009'
        self.chat(s, [title('Runs'), reply(NOW - 9500)])
        Runs.run_folder(self, s, 'wf_one', [('a1', 'builder', None, [reply(t0), stop(t0 + 10, 'empty')], 'opus')])
        r = self.build()
        self.assertEqual((r['week']['d7']['stops'], r['week']['d7']['agents_dead']), (0, 0))
        self.assertEqual(r['causes'], [])

    def test_what_ended_cut_off_chats_is_not_named(self):
        for i, c in enumerate('xyz'):
            self.chat(c * 8 + '-0000-0000-0000-00000000000%d' % i,
                      [title('Chat %s' % c), prompt(NOW - 1500), reply(NOW - 1400, tools=('Bash',)),
                       result(NOW - 1390 + i * 5, shutdown=True)], age=1390 - i * 5)
        r = self.build()
        step = [x for x in r['steps'] if x['key'] == 'cut'][0]
        self.assertTrue(step['title'].startswith('3 chats were working at '))
        self.assertNotIn('VS Code', json.dumps([r['causes'], r['steps']]))
        self.assertEqual([c for c in r['causes'] if c['key'] == 'restart'], [])     # a step, not a 14-day row

    def test_a_chat_not_found_in_ps_is_not_called_cut_off(self):
        sid = 'abcdef02-1111-2222-3333-444455556666'
        self.chat(sid, [title('Asks a question'), prompt(NOW - 1500), reply(NOW - 1400, tools=('AskUserQuestion',))],
                  age=1400)
        self.cmds['ps'] = '/Users/someone/.local/bin/claude\n/usr/bin/other'     # a terminal install, no --resume
        r = self.build()
        self.assertEqual(r['waiting'], [])                   # not known to run: not said to wait for you either
        self.assertEqual(r['cut_off'], [])
        self.assertEqual(r['mac']['programs'], 1)
        self.cmds['ps'] = '/usr/bin/other'                       # no Claude Code program runs at all: it ended
        self.assertEqual([c['title'] for c in self.build()['cut_off']], ['Asks a question'])

    def test_a_tool_call_with_no_result_is_not_waiting_for_permission(self):
        sid = 'abcdef03-1111-2222-3333-444455556666'
        self.chat(sid, [title('Long command'), prompt(NOW - 1500), reply(NOW - 1400, tools=('Bash',))], age=1400)
        self.cmds['ps'] = '/x/native-binary/claude --resume=%s' % sid
        r = self.build()
        self.assertEqual(r['waiting'], [])
        self.assertNotIn('permission', json.dumps(r))

    def test_the_memory_step_needs_a_fixed_figure_not_a_share(self):
        sid = 'abcdef04-1111-2222-3333-444455556666'
        self.chat(sid, [title('Old'), prompt(NOW - 3 * 86400), reply(NOW - 3 * 86400 + 60)], age=3 * 86400)
        self.cmds['ps'] = '/x/native-binary/claude --resume=%s' % sid
        self.cmds['swap'] = 'vm.swapusage: total = 1024.00M  used = 1000.00M  free = 24.00M  (encrypted)'
        self.assertNotIn('memory', [x['key'] for x in self.build()['steps']])
        self.cmds['swap'] = 'vm.swapusage: total = 12288.00M  used = 8704.00M  free = 3584.00M  (encrypted)'
        mem = [x for x in self.build()['steps'] if x['key'] == 'memory'][0]
        self.assertTrue(mem['why'].startswith('8.5 GB of swap in use (12 GB allocated). 1 open chat'), mem['why'])
        self.assertNotIn('program', mem['why'])

    def test_the_terminal_version_comes_only_from_package_json_or_the_file_name(self):
        os.makedirs(os.path.join(self.ext, 'anthropic.claude-code-2.1.332-darwin-arm64'))
        folder = os.path.join(self.tmp, 'tools-2.1.300', 'bin')    # a folder name with a version in it
        os.makedirs(folder)
        open(os.path.join(folder, 'claude'), 'w').close()
        keeprun.ENV['path'] = [folder]
        r = self.build()
        self.assertIsNone(r['claude']['terminal'])
        self.assertNotIn('terminal', [x['key'] for x in r['steps']])
        versions = os.path.join(self.tmp, 'share', 'claude', 'versions')
        os.makedirs(versions)
        open(os.path.join(versions, '2.1.309'), 'w').close()
        bindir = os.path.join(self.tmp, 'local-bin')
        os.makedirs(bindir)
        os.symlink(os.path.join(versions, '2.1.309'), os.path.join(bindir, 'claude'))
        keeprun.ENV['path'] = [bindir]
        step = [x for x in self.build()['steps'] if x['key'] == 'terminal'][0]
        self.assertEqual(step['title'], 'The claude program at %s is older than the VS Code extension.'
                         % os.path.join(bindir, 'claude'))
        self.assertNotIn('terminal', step['title'])

    def test_the_model_of_a_limit_is_the_one_its_sentence_names(self):
        t0 = NOW - 7200
        opus = stop(t0, 'limit_model')
        opus['message']['content'][0]['text'] = "You've hit your Opus limit · resets Nov 30 at 1pm"
        opus['requestedModel'] = 'claude-fable-5-2'
        self.chat('a' * 8 + '-2010', [reply(t0 - 10), opus])
        r = self.build()
        self.assertEqual([c['name'] for c in r['causes']], ['Opus used up on the account in use'])
        unknown = stop(t0, 'limit_model')
        unknown['message']['content'][0]['text'] = 'Request rejected.'
        self.chat('a' * 8 + '-2010', [reply(t0 - 10), unknown])
        self.assertEqual([c['name'] for c in self.build()['causes']], ['A model used up on the account in use'])

    def test_a_limit_of_no_known_kind_is_not_called_a_5_hour_limit(self):
        d = {'error': 'rate_limit', 'apiErrorStatus': 429, 'isApiErrorMessage': True}
        self.assertEqual(keeprun.cause_of(d, 'API Error: 429 rate limited, try again in a minute'), 'limit_other')
        self.assertEqual(keeprun.NAMES['limit_other'], 'Usage or rate limit')
        self.assertEqual(keeprun.NAMES['blocked_403'], 'Request rejected (403)')
        self.assertEqual(keeprun.WHY_WORDS.get('limit_other'), 'usage or rate limit')     # a dead agent's why

    def test_a_row_is_not_named_after_a_switch_when_only_some_were(self):
        t0 = NOW - 7200
        self.chat('a' * 8 + '-2011', [reply(t0 - 10), stop(t0, 'signed_out')])
        self.chat('a' * 8 + '-2012', [reply(t0 - 9000), stop(t0 - 8000, 'signed_out')])
        lines = [{'t': t0 - 60, 'kind': 'switch', 'from': 'a@example.com', 'to': 'b@example.com', 'by': 'auto'}]
        row = [c for c in self.build(lines=lines)['causes'] if c['key'] == 'signed_out'][0]
        self.assertEqual((row['name'], row['note']), ('Signed out', '1 of 2 within 5 minutes after a switch'))

    def test_what_switchboard_does_claims_nothing_of_claude_code(self):
        rows = []
        for has, on in ((False, None), (True, False)):
            stops = [{'t': NOW - 7200 + i, 'cause': c, 'asked': None, 'text': '', 'records': 1, 'session': 's',
                      'kind': 'chat', 'wf': None, 'title': 'T', 'how': 'never', 'wait': None, 'lost': None,
                      'resets': None, 'by': 'records'} for i, c in enumerate(keeprun.CAUSES)]
            runs = [{'name': 'run', 'chat': 'Chat', 'session': 's', 'at': keeprun.iso(NOW - 3600), 't': NOW - 3600,
                     'agents': 2, 'returned': 1, 'dead': 1, 'why': {'offline': 1}, 'said_completed': False,
                     'never_rerun': 1, 'dead_t': [NOW - 3000], 'labels': [], 'models': []}]
            tot = {'agents': 2, 'dead': 1, 'why': {'offline': 1}, 'never_rerun': 1}
            hist = {'switches': [], 'every_out': [], 'auto_off': [], 'auto_on': True}
            rows += keeprun._handled_rows(stops, tot, runs, {'wakes': []}, hist, on, has, None, True, True, NOW)
        text = json.dumps(rows, ensure_ascii=False)
        for gone in ('retries', 'Nothing continues', 'passed by itself', '1M', 'rephrase', 'started again',
                     'compacts'):
            self.assertNotIn(gone, text)
        self.assertIn('Claude’s safeguards flagged the request.', text)
        agents = [r for r in rows if r['key'] == 'agents'][0]
        self.assertIsNone(agents['never'])                      # "not run again" is not "never went on"
        self.assertEqual((agents['last'], agents['examples'][0]['at']), (keeprun.iso(NOW - 3000),) * 2)

    def test_handled_only_where_it_is_measured(self):
        sid = 'a' * 8 + '-2013'
        t0 = NOW - 7200
        self.chat(sid, [reply(t0 - 10), stop(t0, 'limit_model')])
        with self.runner(on=False):
            row = [c for c in self.build()['causes'] if c['key'] == 'limit_model'][0]
        self.assertIsNone(row['handled'])
        os.makedirs(os.path.join(self.data, 'running'), mode=0o700, exist_ok=True)
        with open(os.path.join(self.data, 'running', 'events.jsonl'), 'w') as f:
            f.write(json.dumps({'v': 1, 'at': t0 + 1, 'session': sid, 'agent': None, 'kind': 'stop',
                                'cause': 'limit_model', 'asked': 'fable'}) + '\n')
            f.write(json.dumps({'v': 1, 'at': t0 + 50, 'session': sid, 'agent': None, 'kind': 'resumed',
                                'of': t0 + 1, 'after_s': 49.0}) + '\n')
        with self.runner(on=True):
            r = self.build()
        row = [c for c in r['causes'] if c['key'] == 'limit_model'][0]
        self.assertEqual((row['handled'], row['continued']), ('yes', 1))
        self.assertNotIn('held_minutes', r['now'])
        self.assertIsNone(r['gap_minutes'])

    def test_the_on_step_needs_a_stop_that_did_not_go_on_by_itself(self):
        t0 = NOW - 7200
        self.chat('a' * 8 + '-2014', [reply(t0 - 10), stop(t0, 'limit_model'), reply(t0 + 30)])
        with self.runner(on=False):
            r = self.build()
        self.assertNotIn('on', [x['key'] for x in r['steps']])

    def test_the_lid_row_says_the_setting_not_what_runs(self):
        self.cmds['pmset_log'] = TheMac.PMSET
        with mock.patch.object(keeprun, 'DAYS', 14):
            r = keeprun.build(now=1795250405.0 + 5 * 86400, lines=[], auto_on=True, awake_on=True)
        lid = [c for c in r['causes'] if c['key'] == 'lid'][0]
        self.assertEqual(lid['doing'], 'Keep the Mac awake is on in Switchboard’s settings.')

    def test_a_later_run_is_found_without_a_journal_and_by_its_whole_name(self):
        s = 'a' * 8 + '-2015'
        self.chat(s, [title('Runs'), reply(NOW - 9500)])
        t0 = NOW - 8000
        Runs.run_folder(self, s, 'wf_one', [('a1', 'builder', None, [reply(t0), stop(t0 + 10, 'offline')], 'opus')],
                        start=t0, name='N' * 60 + 'A')
        Runs.run_folder(self, s, 'wf_two', [('b1', 'builder', 'result', [reply(t0 + 900)], 'opus')],
                        start=t0 + 800, name='N' * 60 + 'B')
        run = [x for x in self.build()['runs'] if x['dead']][0]
        self.assertFalse(run['later_run'])                      # the names differ after the 60th character
        Runs.run_folder(self, s, 'wf_three', [('c1', 'builder', 'result', [reply(t0 + 1900)], 'opus')],
                        start=t0 + 1800, name='N' * 60 + 'A')
        os.remove(os.path.join(self.proj, s, 'subagents', 'workflows', 'wf_three', 'journal.jsonl'))
        run = [x for x in self.build()['runs'] if x['dead']][0]
        self.assertTrue(run['later_run'])                       # no journal rows, but it started

    def test_build_never_raises_from_a_statement_outside_its_parts(self):
        self.chat('a' * 8 + '-2016', [reply(NOW - 7300), stop(NOW - 7200, 'offline')])
        with mock.patch.object(keeprun, '_window', side_effect=IndexError('made up')):
            r = self.build()
        self.assertIsNone(r['week']['d7'])
        self.assertTrue(r['problems'])

    def test_a_growing_vscode_log_is_read_on_not_whole(self):
        folder = os.path.join(self.logs, '20261126T1', 'window1', 'exthost', 'Anthropic.claude-code')
        os.makedirs(folder)
        log = os.path.join(folder, 'Claude VSCode.log')
        at = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(NOW - 6620))
        line = '%s.381 [warning] Claude Code did not finish starting, and its process has exited\n' % at
        with open(log, 'w') as f:
            f.write('x' * 5000 + '\n' + line)
        times, _ = keeprun.panel_lines(NOW, None)
        self.assertEqual(len(times), 1)
        with open(log, 'r+') as f:                              # past the first 4 KB, the old line is changed ...
            f.seek(5001)
            f.write(line.replace('did not finish', 'DID NOT FINISH'))
            f.seek(0, 2)
            f.write(line)                                       # ... and one is added
        times, _ = keeprun.panel_lines(NOW, None)
        self.assertEqual(len(times), 2)                         # read on from where it ended, not again from 0

    def test_a_corrupt_mac_file_keeps_only_spans_of_the_right_shape(self):
        good = [NOW - 86400, NOW - 80000, 'Clamshell Sleep', True, 90, 3]
        core.write_json(os.path.join(self.data, keeprun.MAC_FILE),
                        {'at': NOW - 10, 'spans': [[1, 2], ['x'], 'y', good], 'wakes': ['a', NOW - 80000]})
        out = keeprun.mac_facts(NOW)
        self.assertEqual((out['spans'], out['wakes']), ([good], [NOW - 80000]))
        r = self.build()
        self.assertIn('lid', [x['key'] for x in r['steps']])


def keyed(url):
    """A GET of `url` that carries the page key: every route under /api/ but /api/state needs it."""
    return urllib.request.Request(url, headers=pagekey_help.with_key())


class TheEndpoint(Fixture):
    def serve(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(port)})
        env.start()
        self.addCleanup(env.stop)
        return 'http://127.0.0.1:%d' % port

    def setUp(self):
        super().setUp()
        saved = dict(server.KEEP)
        self.addCleanup(lambda: (server.KEEP.clear(), server.KEEP.update(saved)))
        server.KEEP.update(page=None, at=0, running=False)

    def test_api_keep_on_an_empty_mac(self):
        shutil.rmtree(self.root)                                # no Claude Code history at all
        url = self.serve()
        real, asked = server.read_keep, []
        with mock.patch.object(server.engine, 'auto_settings', lambda: {'enabled': True}), \
                mock.patch.object(server, 'read_keep', lambda: asked.append(1)):
            with urllib.request.urlopen(keyed(url + '/api/keep'), timeout=10) as r:
                first = json.load(r)
            self.assertEqual(first, {'state': 'reading'})
            for _ in range(100):
                if asked:
                    break
                time.sleep(.05)
            self.assertEqual(asked, [1])                        # the first look starts a read of its own
            server.KEEP['running'] = False
            real()
            with urllib.request.urlopen(keyed(url + '/api/keep'), timeout=10) as r:
                got = json.load(r)
        self.assertEqual(got['state'], 'ready')
        self.assertEqual(got['week']['d7']['stops'], 0)
        self.assertEqual(got['week']['sentence'], 'No chat, helper or workflow agent stopped early in the last 7 days.')
        self.assertEqual(got['causes'], [])
        self.assertEqual((got['waiting'], got['cut_off'], got['runs']), ([], [], []))
        for k in ('now', 'week', 'causes', 'waiting', 'cut_off', 'runs', 'steps', 'problems'):
            self.assertIn(k, got)

    def test_a_part_that_fails_is_left_out_with_a_line(self):
        with mock.patch.object(keeprun, 'runs_view', side_effect=RuntimeError('made up')):
            r = self.build()
        self.assertIn('Workflow runs could not be read.', r['problems'])
        self.assertEqual(r['runs'], [])

    def test_post_running_without_the_module_says_so(self):
        url = self.serve()
        req = urllib.request.Request(url + '/api/running', data=b'{"on": true}', method='POST',
                                     headers=pagekey_help.with_key({'Content-Type': 'application/json',
                                                                    'X-Switchboard': '1'}))
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(e.exception.code, 400)
        self.assertEqual(json.load(e.exception)['error'], 'Keep running is not part of this build.')

    def test_post_running_turns_it_on(self):
        calls = []
        fake = types.ModuleType('switchboard.running')

        class RunningError(Exception):
            pass
        fake.RunningError = RunningError

        def set_on(on):
            calls.append(on)
            if on == 'refuse':
                raise RunningError('x')
            return {'on': on, 'since': NOW, 'plugin': '0.1.0', 'problems': []}
        fake.set_on = set_on
        with mock.patch.dict(sys.modules, {'switchboard.running': fake}), \
                mock.patch.object(server, 'locked') as locked:
            locked.return_value.__enter__ = lambda s: {}
            locked.return_value.__exit__ = lambda s, *a: False
            with mock.patch.object(server, 'event'):
                got = server.act('/api/running', {'on': True})
            self.assertEqual(calls, [True])
            self.assertTrue(got['running']['on'])
            with self.assertRaises(core.SwitchError):
                server.act('/api/running', {'on': 'yes'})       # on or off only

    def test_the_demo_answer_has_every_kind_of_row(self):
        with mock.patch.object(keeprun, 'running_status', lambda: (True, {'on': False})):    # as with the plugin's lane
            s = keeprun.sample(NOW)
        keys = set(c['key'] for c in s['causes'])
        self.assertTrue({'limit_model', 'signed_out', 'offline', 'lid', 'every_out', 'agents'} <= keys)
        self.assertEqual(set(x['key'] for x in s['steps']), {'on', 'cut', 'runs', 'waiting', 'lid', 'memory',
                                                              'terminal', 'api_key', 'workflows'})
        real = self.build()
        self.assertEqual(sorted(k for k in real if k not in ('at',)),
                         sorted(k for k in s if k not in ('at', 'sample')))
        for text in json.dumps(s).split('"'):
            self.assertNotIn('!', text)
        self.assertNotIn('refus', json.dumps(s).lower())
        self.assertNotIn('lost_min', json.dumps(s))
        self.assertNotIn('hours_lost', json.dumps(s))
        self.assertEqual(s['week']['d7'].keys(), real['week']['d7'].keys())


if __name__ == '__main__':
    unittest.main()
