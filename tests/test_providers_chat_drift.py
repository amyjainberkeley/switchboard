import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Mutation drift (DESIGN 18): for every row of both readers' tables, the same chat with that row's key renamed, its
# value's type changed, a new block kind beside it, a new line kind, a torn last line, and other versions of the app.
# Pass: no exception, and what was lost is never more than what was counted: the expected events' words missing from
# what was read are at most the unknown lines and blocks the census counted plus a torn tail. Only keys every real
# line of that row carries are renamed (a key the app leaves out on some lines, such as retained_context or
# client_id, is a known shape, not drift). Every file is made up.
import collections
import copy
import os
import shutil
import tempfile
import unittest

import fakes_provider_chats as F
from fakes_providers import make_reach
from switchboard.providers import claude_chats, codex_chats, contract, lines
from switchboard.providers.contract import ChatRef, ReadLimits


class StandInRedact:
    @staticmethod
    def secret_file(path):
        return os.path.basename(path).startswith('.env')


def retyped(v):
    if isinstance(v, bool):
        return 'yes'
    if isinstance(v, str):
        return 12345
    if isinstance(v, (int, float)):
        return 'x'
    if isinstance(v, list):
        return {'x': 1}
    if isinstance(v, dict):
        return ['x']
    return 'x'


def at_path(d, path):
    for k in path[:-1]:
        d = d[k]
    return d, path[-1]


def signature(t):
    """What a reader handed on, as words: each event's text, or its kind when it has none."""
    return collections.Counter(e.text if e.text else e.kind for e in t.events)


def counted(t):
    return sum(r[4] for r in t.info.census) + t.info.torn_tail + t.info.skipped_large


# Claude Code rows: (name, how to add the line, keys every such line carries). Paths index into the line's dict.
def claude_rows():
    rows = []

    def add(name, keys):
        def deco(fn):
            rows.append((name, fn, keys))
            return fn
        return deco

    @add('person', [('type',), ('message',), ('message', 'content'), ('message', 'content', 0, 'text'),
                    ('message', 'content', 0, 'type'), ('origin',), ('origin', 'kind')])
    def person(c):
        return c.person('PERSON WORDS')

    @add('person-picture', [('message', 'content', 1, 'type')])
    def picture(c):
        return c.person('WITH A PICTURE', images=1)

    @add('sidechain', [('isSidechain',)])
    def side(c):
        return c.sidechain('SIDECHAIN WORDS')

    @add('meta', [('isMeta',)])
    def meta(c):
        return c.meta('META WORDS')

    @add('task-notice', [('origin', 'kind'), ('message', 'content')])
    def notice(c):
        return c.task_notice('TASK WORDS')

    @add('tagged', [('message', 'content')])
    def tagged(c):
        return c.local_command('COMMAND WORDS')

    @add('note-mark', [('message', 'content', 0, 'text')])
    def note(c):
        return c.person('Switchboard note h-0a0b0c0d. Read the file /tmp/n.md first.')

    @add('assistant-text', [('message', 'content'), ('message', 'content', 0, 'text'),
                            ('message', 'content', 0, 'type'), ('message',)])
    def say(c):
        return c.say('ASSISTANT WORDS')

    @add('thinking', [('message', 'content', 0, 'type')])
    def think(c):
        return c.think()

    @add('tool-use', [('message', 'content', 0, 'name'), ('message', 'content', 0, 'input'),
                      ('message', 'content', 0, 'id'), ('message', 'content', 0, 'type')])
    def use(c):
        c.tool_use('Read', {'file_path': '/Users/maya/site/USE.py'}, tool_id='toolu_drift')
        return c.rows[-1]

    @add('tool-result', [('message', 'content', 0, 'content'), ('message', 'content', 0, 'tool_use_id'),
                         ('message', 'content', 0, 'type'), ('message',)])
    def result(c):
        return c.tool_result('toolu_drift', 'RESULT WORDS', is_error=False)

    @add('api-error', [('error',)])
    def err(c):
        return c.api_error('rate_limit')

    @add('queued', [('attachment',), ('attachment', 'type'), ('attachment', 'prompt'),
                    ('attachment', 'prompt', 0, 'text'), ('attachment', 'origin', 'kind')])
    def queued(c):
        return c.queued('QUEUED WORDS')

    @add('queued-task', [('attachment', 'prompt'), ('attachment', 'origin', 'kind')])
    def queued_task(c):
        return c.queued('QUEUED TASK', kind='task-notification')

    @add('boundary', [('subtype',), ('type',)])
    def boundary(c):
        return c.boundary()

    @add('summary', [('message', 'content'), ('isCompactSummary',)])
    def summary(c):
        return c.compact_summary('SUMMARY WORDS')

    @add('newest', [('message', 'content', 0, 'text')])
    def newest(c):
        return c.person('THE NEWEST WORDS')
    return rows


def codex_rows(response_mode=False):
    rows = []

    def add(name, keys):
        def deco(fn):
            rows.append((name, fn, keys))
            return fn
        return deco

    @add('person', [('type',), ('payload',), ('payload', 'type'), ('payload', 'item'), ('payload', 'item', 'type'),
                    ('payload', 'item', 'content'), ('payload', 'item', 'content', 0, 'text'),
                    ('payload', 'item', 'content', 0, 'type')])
    def person(c):
        c.user_item('PERSON WORDS', mirror=False)
        return c.rows[-1]

    @add('heartbeat', [('payload', 'item', 'content', 0, 'text')])
    def beat(c):
        c.user_item('<heartbeat>BEAT</heartbeat>', client=False, mirror=False)
        return c.rows[-1]

    @add('assistant', [('payload', 'content'), ('payload', 'content', 0, 'text'), ('payload', 'content', 0, 'type'),
                       ('payload', 'role'), ('payload', 'type')])
    def say(c):
        return c.assistant('ASSISTANT WORDS', item=False)

    @add('task-complete', [('payload', 'last_agent_message')])
    def done(c):
        return c.task_complete('A FINAL WORD')

    @add('reasoning', [('payload', 'type')])
    def reasoning(c):
        return c.reasoning()

    if response_mode:
        @add('function-call', [('payload', 'name'), ('payload', 'arguments'), ('payload', 'call_id')])
        def call(c):
            return c.function_call('lookup', '{"q":"CALL WORDS"}', 'call-1')

        @add('function-output', [('payload', 'output'), ('payload', 'call_id')])
        def output(c):
            return c.function_output('call-1', 'OUTPUT WORDS')

        @add('custom-call', [('payload', 'name'), ('payload', 'input')])
        def custom(c):
            return c.custom_call('exec', 'CUSTOM INPUT', 'call-2')

        @add('custom-output', [('payload', 'output')])
        def custom_out(c):
            return c.custom_output('call-2', [{'type': 'input_text', 'text': 'CUSTOM OUTPUT'}])
    else:
        @add('command', [('payload', 'item', 'command'), ('payload', 'item', 'aggregated_output'),
                         ('payload', 'item', 'exit_code')])
        def command(c):
            return c.command(['ls', 'COMMANDWORD'], output='COMMAND OUTPUT', exec_call=False)

        @add('file-change', [('payload', 'item', 'changes')])
        def change(c):
            return c.file_change({'/Users/maya/site/CHANGED.py': ('add', 'x')})

        @add('mcp', [('payload', 'item', 'server'), ('payload', 'item', 'tool'), ('payload', 'item', 'arguments'),
                     ('payload', 'item', 'result'), ('payload', 'item', 'result', 'content'),
                     ('payload', 'item', 'status')])
        def mcp(c):
            return c.mcp('example', 'lookup', {'q': 'MCP ARGS'}, 'MCP RESULT')

    @add('compacted', [('type',), ('payload', 'message'), ('payload', 'retained_context', 'user_messages', 0, 'text')])
    def compacted(c):
        return c.compacted('SUMMARY WORDS', retained=[{'text': 'KEPT WORDS', 'order': 0}])

    @add('newest', [('payload', 'item', 'content', 0, 'text')])
    def newest(c):
        c.user_item('THE NEWEST WORDS', mirror=False)
        return c.rows[-1]
    return rows


class Drift(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='drift-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.reach, _ = make_reach(self.root, env={'HOME': os.path.join(self.root, 'home')})
        old = lines.redact
        self.addCleanup(setattr, lines, 'redact', old)
        lines.redact = StandInRedact
        self.k = 0
        self.runs = 0

    def read(self, app, rows, version=None, torn=False):
        self.k += 1
        path = os.path.join(self.root, '%s-%d.jsonl' % (app, self.k))
        rows = copy.deepcopy(rows)
        if version:
            for r in rows:
                if app == 'claude' and isinstance(r, dict) and 'version' in r:
                    r['version'] = version
                elif app == 'codex' and isinstance(r, dict) and r.get('type') == 'session_meta':
                    r['payload']['cli_version'] = version
        tail = None
        if torn:
            last = F.dump(rows.pop())
            tail = last[:len(last) // 2]
        F.write_lines(path, rows, torn=tail)
        if app == 'claude':
            ref = ChatRef(provider='claude', id=F.CHAT, folder=F.FOLDER, path=path)
            t = claude_chats.read(self.reach, ref, ReadLimits(since_boundary=False))
        else:
            ref = ChatRef(provider='codex', id=F.THREAD, folder=F.FOLDER, path=path)
            t = codex_chats.read(self.reach, ref, ReadLimits(since_boundary=False))
        self.runs += 1
        return t

    def check(self, app, want, own, variant, what):
        """Read variant: whatever of want is missing must be the changed line's own events (own), and when anything
        is missing the census must have counted something. So no loss is silent, and no line's change costs another
        line's words."""
        t = self.read(app, variant)
        lost = want - signature(t)
        beyond = {k: v for k, v in lost.items() if v > own.get(k, 0)}
        self.assertEqual(beyond, {}, '%s %s: lost beyond the changed line' % (app, what))
        if lost:
            self.assertGreater(counted(t), 0, '%s %s: lost %s, counted nothing' % (app, what, dict(lost)))
        return t

    def mutate_each(self, app, chat, rows):
        lines_at = {}
        for name, build, keys in rows:
            lines_at[name] = (len(chat.rows), keys)
            build(chat)
            self.assertEqual(len(chat.rows), lines_at[name][0] + 1, name)      # one line a row
        base = chat.rows
        want = signature(self.read(app, base))
        for name, (index, keys) in lines_at.items():
            own = want - signature(self.read(app, base[:index] + base[index + 1:]))
            self.assertTrue(own or name in ('thinking', 'reasoning', 'sidechain', 'meta'), name)
            for path in keys:
                for how in ('rename', 'retype'):
                    variant = copy.deepcopy(base)
                    try:
                        parent, key = at_path(variant[index], path)
                        value = parent[key]
                    except (KeyError, IndexError, TypeError):
                        self.fail('%s %s: the fixture has no %s' % (app, name, path))
                    if how == 'rename':
                        if isinstance(parent, list):
                            continue
                        parent[str(key) + '_renamed'] = parent.pop(key)
                    else:
                        parent[key] = retyped(value)
                    self.check(app, want, own, variant, '%s %s %s' % (name, how, '.'.join(map(str, path))))
            # a new block kind beside the row's own blocks
            variant = copy.deepcopy(base)
            line = variant[index]
            blocks = None
            for path in (('message', 'content'), ('payload', 'content'), ('payload', 'item', 'content'),
                         ('attachment', 'prompt')):
                try:
                    parent, key = at_path(line, path)
                    if isinstance(parent[key], list):
                        blocks = parent[key]
                        break
                except (KeyError, IndexError, TypeError):
                    continue
            if blocks is not None:
                blocks.append({'type': 'brand_new_block', 'data': 'x'})
                self.check(app, want, own, variant, '%s new block' % name)
        # a new line kind loses nothing; a torn last line loses only its own events, and is counted apart
        t = self.check(app, want, collections.Counter(), base + [{'type': 'brand-new-line', 'payload': {'x': 1}}],
                       'new line kind')
        self.assertEqual(t.info.unknown_lines, 1)
        self.assertEqual(signature(self.read(app, base)), want)
        last = want - signature(self.read(app, base[:-1]))
        t = self.read(app, base, torn=True)
        self.assertEqual(want - signature(t), last)
        self.assertEqual((t.info.torn_tail, t.info.unknown_lines), (1, 0))
        return base

    def test_claude_code_every_row(self):
        c = F.ClaudeChat()
        base = self.mutate_each('claude', c, claude_rows())
        for version in ('2.1.309', '2.1.400', '2.1.330'):
            t = self.read('claude', base, version=version)
            self.assertEqual(signature(t), signature(self.read('claude', base)), version)
            self.assertEqual(t.info.version, version)
        self.assertGreater(self.runs, 100)

    def test_claude_code_legacy_mode(self):
        c = F.ClaudeChat(origin=False, entrypoint='sdk-cli')
        rows = [r for r in claude_rows() if r[0] not in ('person', 'queued', 'queued-task', 'task-notice',
                                                         'note-mark', 'newest')]
        rows.insert(0, ('headless', lambda c: c.headless('HEADLESS WORDS'), [('message', 'content'), ('type',)]))
        rows.append(('queued-legacy', lambda c: c.queued('QUEUED WORDS', kind='legacy'),
                     [('attachment', 'prompt'), ('attachment', 'type'), ('attachment', 'commandMode')]))
        rows.append(('newest', lambda c: c.headless('THE NEWEST WORDS'), [('message', 'content')]))
        self.mutate_each('claude', c, rows)

    def test_codex_items_mode_every_row(self):
        c = F.CodexChat()
        base = self.mutate_each('codex', c, codex_rows())
        for version in ('0.153.4', '0.169.0-alpha.11.1', '0.180.0'):
            t = self.read('codex', base, version=version)
            self.assertEqual(signature(t), signature(self.read('codex', base)), version)
            self.assertEqual(t.info.version, version)

    def test_codex_response_mode_every_row(self):
        c = F.CodexChat(version='0.153.4')
        base = self.mutate_each('codex', c, codex_rows(response_mode=True))
        self.assertIn('mode/responses', {r[0] for r in self.read('codex', base).info.census})

    def test_a_reader_never_raises_on_any_line(self):
        odd = [None, 1, 'x', [], {}, {'type': None}, {'type': 'user'}, {'type': 'assistant', 'message': []},
               {'type': 'user', 'message': {'content': [None, 1, 'x', {'type': None}]}},
               {'type': 'attachment', 'attachment': {'type': 'queued_command', 'prompt': [None]}},
               {'type': 'system'}, {'type': 'event_msg'}, {'type': 'event_msg', 'payload': {'type': 'item_completed'}},
               {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {'type': 'McpToolCall'}}},
               {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [None]}},
               {'type': 'compacted', 'payload': {'retained_context': {'user_messages': [None, {}],
                                                                      'user_messages_incomplete': False}}}]
        rows = ['null', '1', '"x"', '[]', '{}'] + [F.dump(o) for o in odd if isinstance(o, dict)]
        for app in ('claude', 'codex'):
            path = os.path.join(self.root, 'odd-%s.jsonl' % app)
            F.write_lines(path, rows)
            ref = ChatRef(provider=app, id=F.CHAT, folder=F.FOLDER, path=path)
            reader = claude_chats if app == 'claude' else codex_chats
            t = reader.read(self.reach, ref)
            self.assertIsInstance(t, contract.Transcript)
            self.assertGreater(t.info.unknown_lines, 0)


if __name__ == '__main__':
    unittest.main()
