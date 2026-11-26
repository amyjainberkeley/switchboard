import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The real shapes, made up: one Claude Code file in origin mode with every kind of line the step-0 census found (and
# the counts it found them in, scaled down), one in legacy mode (a headless sdk-cli run), one Codex 0.169 file in items
# mode with the three typed tool items, one in response mode, and a helper's file with two session_meta lines. Each
# reads with nothing unknown and nothing degraded, and only the person's own messages are PERSON. Last: no fixture
# carries a connector name but the made-up mcp__example.
import os
import re
import shutil
import tempfile
import time
import unittest

import fakes_provider_chats as F
from fakes_providers import make_reach
from switchboard.providers import claude_chats, codex_chats, contract, lines
from switchboard.providers.contract import ChatRef

HERE = os.path.dirname(os.path.abspath(__file__))


class StandInRedact:
    @staticmethod
    def secret_file(path):
        return os.path.basename(path).startswith('.env')


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='chat-shapes-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = os.path.join(self.root, 'home')
        self.reach, _ = make_reach(self.root, env={'HOME': self.home})
        old = lines.redact
        self.addCleanup(setattr, lines, 'redact', old)
        lines.redact = StandInRedact

    def check_clean(self, t):
        self.assertEqual(t.info.unknown_lines, 0, [r for r in t.info.census if r[4]])
        self.assertEqual(t.info.new_kinds, ())
        self.assertIsNone(t.info.degraded)
        self.assertEqual(t.info.torn_tail, 0)

    @staticmethod
    def of(t, kind):
        return [e for e in t.events if e.kind == kind]


class ClaudeOriginMode(Base):
    """2.1.309-2.1.330, the VS Code extension: every kind of user line of census F1, queued_command attachments of F2,
    queue-operation lines of F3, the compaction of F5, last-prompt of F6, the blocks of F7, the tools of F8 and the
    line kinds of F9."""

    def build(self):
        c = F.ClaudeChat(version='2.1.325')
        c.skip_lines()
        for kind in ('agent-name', 'mode', 'atis-latch', 'frame-link', 'bridge-session', 'pr-link',
                     'artifact-autoreact-ledger', 'artifact-comment-monitor', 'data-flow-incomplete'):
            c.add({'type': kind, 'sessionId': c.session, 'v': 1})
        c.attachment('environment', content='made-up')
        c.attachment('skill_listing', content='made-up')
        c.attachment('deferred_tools_record', tools=['mcp__example__tool'])
        c.person('the first request')                                       # origin.kind human, list of text
        c.person('a picture', images=1)                                     # image + text
        c.person('a document', documents=1)                                 # document + text
        c.user_line([{'type': 'text', 'text': '<browser_instruction>\nfixed app words\n</browser_instruction>'},
                     {'type': 'text', 'text': 'after the app block'}], origin={'kind': 'human'},
                    turnOrigin='human', promptSource='sdk')                 # F16: the app's block first
        c.queue('enqueue')                                                  # the person's prompt, queued with no words
        c.queue('dequeue')
        c.say('on it')
        c.think()
        tid = c.tool_use('Bash', {'command': 'ls -la', 'description': 'list'})
        c.tool_result(tid, 'a str result', is_error=False)
        tid = c.tool_use('Read', {'file_path': '/Users/maya/site/a.png'})
        c.tool_result(tid, [{'type': 'image', 'source': {}}])
        tid = c.tool_use('Read', {'file_path': '/Users/maya/site/b.png'})
        c.tool_result(tid, [{'type': 'image', 'source': {}}, {'type': 'text', 'text': 'and words'}])
        tid = c.tool_use('ToolSearch', {'query': 'select:x'})
        c.tool_result(tid, [{'type': 'tool_reference', 'tool_name': 'x'}])
        tid = c.tool_use('Read', {'file_path': '/Users/maya/site/c.pdf'})
        c.tool_result(tid, [{'type': 'document', 'source': {}}, {'type': 'text', 'text': 'pdf words'}])
        for name, inp in (('Edit', {'file_path': '/Users/maya/site/a.py', 'old_string': 'a', 'new_string': 'b'}),
                          ('Write', {'file_path': '/Users/maya/site/n.py', 'content': 'x'}),
                          ('SendMessage', {'to': 'other', 'message': 'hi'}), ('Workflow', {'script': 'x'}),
                          ('Agent', {'prompt': 'x'}), ('mcp__example__tool', {'q': 'x'})):
            tid = c.tool_use(name, inp)
            c.tool_result(tid, 'ok')
        c.queued('typed while it worked')                                   # a human queued_command, list of text
        c.queued('and with a picture', images=1)                            # image + text
        c.queued('a helper is done', kind='task-notification')
        c.queued('a message from another session', kind='peer')
        c.queue('enqueue', '<task-notification>done</task-notification>')
        c.queue('remove', '<task-notification>done</task-notification>', reason='absorbed_mid_turn')
        c.queue('enqueue', '<cross-session-message>hi</cross-session-message>')
        c.queue('remove', '<agent-message>hi</agent-message>', reason='delivered_to_agent')
        c.queue('enqueue', 'check the deploy every morning')               # plain, a scheduled prompt's words
        c.task_notice('a background task ended')                            # origin.kind task-notification
        c.peer('another session says hello')                                # origin.kind peer, isMeta
        c.companion('a turn companion')                                     # isMeta + turnCompanion
        c.scheduled('the scheduled prompt')                                 # turnOrigin scheduled + isMeta
        c.system_turn('a system turn')                                      # turnOrigin system
        c.local_command('status')
        c.local_output('the output')
        c.tagged('local-command-caveat', 'caveat words')
        c.system('stop_hook_summary', hookCount=1, hookInfos=[], hookErrors=[], preventedContinuation=False)
        c.system('api_error', error={'status': 529}, retryAttempt=1, maxRetries=10, retryInMs=500)
        c.system('informational', content='made-up')
        c.api_error('rate_limit')
        c.person('the request just before the summary')
        c.boundary()
        c.attachment('compact_file_reference', filename='/Users/maya/site/a.py')
        c.attachment('date', date='2026-11-23')
        c.attachment('silent_turn_reminder', content='x')
        c.compact_summary('the summary of the work so far', turn='task_notification')
        c.person('the newest request')
        c.say('carrying on')
        c.last_prompt('the newest request')
        c.ai_title('Made-up title')
        c.custom_title('My made-up name')
        return c

    def test_it_reads_clean_and_only_the_persons_words_are_person(self):
        c = self.build()
        path = c.write(os.path.join(self.root, 'a.jsonl'))
        t = claude_chats.read(self.reach, ChatRef(provider='claude', id=c.session, folder=c.cwd, path=path),
                              contract.ReadLimits(since_boundary=False))
        self.check_clean(t)
        self.assertEqual([(e.text, e.queued) for e in self.of(t, contract.PERSON)], [
            ('the first request', False), ('a picture', False), ('a document', False),
            ('after the app block', False),
            ('typed while it worked', True), ('and with a picture', True),
            ('the request just before the summary', False), ('the newest request', False)])
        self.assertEqual(sorted({e.origin for e in self.of(t, contract.NOTICE)}),
                         ['command-name', 'local-command', 'peer', 'system', 'task-notification'])
        self.assertEqual(len(self.of(t, contract.SUMMARY)), 1)
        self.assertEqual(len(self.of(t, contract.ERROR)), 1)
        self.assertEqual(t.info.images, 2 + 1 + 1 + 1 + 1 + 1)
        self.assertEqual(t.info.title, 'My made-up name')
        self.assertIn(('mode/origin', 1, 1, 0, 0), t.info.census)
        self.assertNotIn(contract.PLAN, {e.kind for e in t.events})

    def test_read_from_its_newest_summary(self):
        c = self.build()
        path = c.write(os.path.join(self.root, 'a.jsonl'))
        t = claude_chats.read(self.reach, ChatRef(provider='claude', id=c.session, folder=c.cwd, path=path))
        self.check_clean(t)
        people = self.of(t, contract.PERSON)
        self.assertEqual([(e.text, e.before_boundary) for e in people][-2:],
                         [('the request just before the summary', True), ('the newest request', False)])
        self.assertEqual([e.text for e in people][:4], ['a document', 'after the app block', 'typed while it worked',
                                                        'and with a picture'])
        self.assertEqual(len(people), 6)            # five before the summary, one after
        self.assertEqual(t.info.stopped, 'boundary')


class ClaudeLegacyMode(Base):
    def test_a_headless_run_with_no_origin_at_all(self):
        c = F.ClaudeChat(entrypoint='sdk-cli', origin=False, version='2.1.330')
        c.queue('enqueue', 'the headless prompt')
        c.queue('dequeue')
        c.headless('the headless prompt')
        c.attachment('deferred_tools_record', tools=[])
        c.say('the answer')
        c.add({'type': 'api-request', 'id': 'r', 'sessionId': c.session})
        c.last_prompt('the headless prompt')
        path = c.write(os.path.join(self.root, 'd.jsonl'))
        t = claude_chats.read(self.reach, ChatRef(provider='claude', id=c.session, folder=c.cwd, path=path))
        self.check_clean(t)
        self.assertEqual([e.text for e in self.of(t, contract.PERSON)], ['the headless prompt'])
        self.assertIn(('mode/legacy', 1, 1, 0, 0), t.info.census)


class CodexItemsMode(Base):
    def build(self):
        c = F.CodexChat(version='0.169.1')
        c.skip_lines()
        c.turn_context()
        c.env_context()
        c.developer('the permissions instructions')
        c.user_item('make the page load faster')
        c.user_item('# Files mentioned by the user:\n\n## a.ts: /Users/maya/site/src/a.ts\n\n'
                    '## My request for Codex:\nlook at this one')                 # F20: whole, one PERSON
        c.reasoning()
        c.reasoning_item()
        c.command(['/bin/zsh', '-lc', 'rg -n slow src'], output='src/a.ts:1: slow', exit_code=0)
        c.command(['/bin/zsh', '-lc', 'false'], output='', exit_code=1, status='failed')
        c.file_change({'/Users/maya/site/src/a.ts': ('update', '@@ -1 +1 @@'),
                       '/Users/maya/site/src/b.ts': ('add', 'export {}')})
        c.mcp('example', 'search', {'focus': 'x', 'max_tokens': 10}, 'two hits')
        c.mcp('example', 'search', {'focus': 'y'}, 'failed', status='failed', is_error=True)
        c.function_call('search', '{"q":"x"}', 'call-9', namespace='mcp__example')
        c.function_output('call-9', [{'type': 'input_text', 'text': 'x'}])
        c.image_view()
        c.assistant('looking', phase='commentary')
        c.question_reply('the second option')
        c.heartbeat('a timed check-in')
        c.assistant('it is faster now')
        c.task_complete('it is faster now')
        c.compacted('what was done', retained=[{'text': 'make the page load faster', 'complete': False, 'order': 0},
                                               {'text': '<heartbeat>x</heartbeat>', 'origin': 'heartbeat',
                                                'order': 1}])
        c.user_item('now the images')
        c.token_count(primary={'used_percent': 10.0, 'window_minutes': 10080, 'resets_at': 2000000000})
        c.assistant('done')
        return c

    def test_it_reads_clean_with_the_typed_items(self):
        c = self.build()
        path = c.write(F.codex_home(self.home))
        ref = ChatRef(provider='codex', id=c.thread, folder=c.cwd, path=path)
        t = codex_chats.read(self.reach, ref, contract.ReadLimits(since_boundary=False))
        self.check_clean(t)
        self.assertEqual([e.text for e in self.of(t, contract.PERSON)][:3], [
            'make the page load faster',
            '# Files mentioned by the user:\n\n## a.ts: /Users/maya/site/src/a.ts\n\n## My request for Codex:\n'
            'look at this one',
            '<send_user_message_question_reply>the second option</send_user_message_question_reply>'])
        self.assertEqual([e.name for e in self.of(t, contract.TOOL_CALL)],
                         ['shell', 'shell', 'file_change', 'example.search', 'example.search'])
        self.assertEqual([e.ok for e in self.of(t, contract.TOOL_RESULT)], [True, False, True, False])
        self.assertEqual([e.origin for e in self.of(t, contract.NOTICE)], ['app-message'])
        census = {r[0] for r in t.info.census}
        self.assertTrue({'mode/items', 'mode/client-id'} <= census)

    def test_read_from_its_summary(self):
        c = self.build()
        path = c.write(F.codex_home(self.home))
        t = codex_chats.read(self.reach, ChatRef(provider='codex', id=c.thread, folder=c.cwd, path=path))
        self.check_clean(t)
        files = ('# Files mentioned by the user:\n\n## a.ts: /Users/maya/site/src/a.ts\n\n## My request for Codex:\n'
                 'look at this one')
        self.assertEqual([(e.text, e.before_boundary) for e in self.of(t, contract.PERSON)], [
            ('make the page load faster', True),        # read back, and kept by the compaction: once
            (files, True),
            ('<send_user_message_question_reply>the second option</send_user_message_question_reply>', True),
            ('now the images', False)])


class CodexResponseMode(Base):
    def test_named_calls_when_no_typed_tool_item_exists(self):
        c = F.CodexChat(version='0.153.4')
        c.user_item('typed', client=True)
        c.heartbeat('tick')
        c.function_call('exec_command', '{"cmd":"ls"}', 'call-1')
        c.function_output('call-1', 'a\nb')
        c.custom_call('exec', 'await tools.exec("ls")', 'call-2')
        c.custom_output('call-2', [{'type': 'input_text', 'text': 'ls output'}])
        c.assistant('done')
        path = c.write(F.codex_home(self.home))
        t = codex_chats.read(self.reach, ChatRef(provider='codex', id=c.thread, folder=c.cwd, path=path))
        self.check_clean(t)
        self.assertEqual([e.name for e in self.of(t, contract.TOOL_CALL)], ['exec_command', 'exec'])
        self.assertEqual([(e.name, e.ok) for e in self.of(t, contract.TOOL_RESULT)], [('exec_command', None),
                                                                                     ('exec', None)])
        self.assertIn('mode/responses', {r[0] for r in t.info.census})


class HelperFile(Base):
    def test_a_helpers_file_is_never_a_chat_and_reads_only_its_own_first_line(self):
        now = time.time()
        site = os.path.realpath(os.path.join(self.root, 'site'))
        os.makedirs(site)
        c = F.CodexChat(thread=F.THREAD2, cwd=site, source={'subagent': {'thread_spawn': {'depth': 1}}},
                        thread_source='subagent', t0=now)
        c.session_meta(F.THREAD, site, '0.169.1', 'vscode', 'user')
        c.user_item('the helpers task', client=False)
        path = c.write(F.codex_home(self.home))
        self.assertEqual(codex_chats.chats(self.reach, site, now), ())
        t = codex_chats.read(self.reach, ChatRef(provider='codex', id=F.THREAD2, folder=site, path=path))
        self.check_clean(t)
        self.assertEqual({r[0]: r[1:] for r in t.info.census}['session_meta/later'], (1, 0, 1, 0))


class PrivateNames(unittest.TestCase):
    def test_no_fixture_names_a_connector_but_the_made_up_one(self):
        """tests/fixtures/ and this lane's fixture writer hold no connector or tool name from a real Mac. The
        packs' own hook inputs are left out: they are the public tool a pack installs, not a person's."""
        bad = re.compile(r'mcp__(?!example)[A-Za-z0-9_-]*')
        paths = [os.path.join(HERE, 'fakes_provider_chats.py')]
        for folder, dirs, names in os.walk(os.path.join(HERE, 'fixtures')):
            dirs[:] = [d for d in dirs if os.path.join(folder, d) != os.path.join(HERE, 'fixtures', 'packs')]
            paths.extend(os.path.join(folder, n) for n in names)
        hits = []
        for p in paths:
            with open(p, 'rb') as f:
                text = f.read().decode('utf-8', 'replace')
            hits.extend('%s: %s' % (os.path.relpath(p, HERE), m) for m in bad.findall(text))
        self.assertEqual(hits, [])
        self.assertIn('fakes_provider_chats.py', ' '.join(paths))


if __name__ == '__main__':
    unittest.main()
