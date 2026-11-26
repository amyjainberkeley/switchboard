"""Made-up chat files in Claude Code's and Codex's own line shapes, for the chat readers' tests.

Claude Code: the line kinds, keys and value types of 2.1.309 to 2.1.330 (the VS Code extension's files and headless
sdk-cli runs), from the shape tables in the round's stand-in-sources/claude-shapes and the step-0 census (shapes and
counts only): user lines with origin {kind}, turnOrigin, promptSource, the flags isMeta, isCompactSummary,
isSidechain, turnCompanion, isVisibleInTranscriptOnly; queued_command attachments; queue-operation lines; the window
files <claude dir>/sessions/<pid>.json (2.1.329) beside their <pid>.<hex>.key.
Codex: the line kinds of 0.153.4 and 0.169.x session files (stand-in-sources/codex-help/shape-session-a.txt and -b.txt
and the census): {timestamp, type, payload}; session_meta first; item_completed items (UserMessage, CommandExecution,
FileChange, McpToolCall, ...); response_item messages, calls and outputs; compacted; token_count; and
session_index.jsonl.
Every id, folder, name, tool, connector and word here is made up; no line is copied from a real chat. No test here.
"""
import calendar
import json
import os
import time

FOLDER = '/Users/maya/site'
CHAT = 'aaaaaaaa-0000-4000-8000-00000000000a'
CHAT2 = 'aaaaaaaa-0000-4000-8000-00000000000b'
CHAT3 = 'aaaaaaaa-0000-4000-8000-00000000000c'
THREAD = 'bbbbbbbb-0000-4000-8000-00000000000a'
THREAD2 = 'bbbbbbbb-0000-4000-8000-00000000000b'
THREAD3 = 'bbbbbbbb-0000-4000-8000-00000000000c'
ACCOUNT = 'eeeeeeee-0000-4000-8000-00000000000a'
ORG = 'ffffffff-0000-4000-8000-00000000000a'
T0 = float(calendar.timegm((2026, 11, 23, 10, 0, 0, 0, 0, 0)))


def iso(t):
    whole = int(t)
    y, mo, d, h, mi, s = time.gmtime(whole)[:6]
    return '%04d-%02d-%02dT%02d:%02d:%02d.%03dZ' % (y, mo, d, h, mi, s, min(999, int((t - whole) * 1000)))


def dump(d):
    return json.dumps(d, separators=(',', ':'))


def write_lines(path, rows, newline=True, torn=None):
    """rows: dicts (or raw str lines); torn: a str written last with no newline (a line cut mid-write)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = '\n'.join(r if isinstance(r, str) else dump(r) for r in rows)
    if rows and newline:
        text += '\n'
    if torn is not None:
        text += torn
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def project_name(folder):
    import re
    return re.sub(r'[^a-zA-Z0-9]', '-', folder)


# ---------- Claude Code ----------


class ClaudeChat:
    """One made-up Claude Code chat, line by line (2.1.330 unless version says). origin=True writes the VS Code
    extension's shapes (origin {kind} on the person's lines); origin=False a file with no origin at all (an sdk-cli
    run, an older version, perhaps the terminal program)."""

    def __init__(self, cwd=FOLDER, session=CHAT, version='2.1.330', entrypoint='claude-vscode', origin=True, t0=T0):
        self.cwd, self.session, self.version, self.entrypoint, self.origin = cwd, session, version, entrypoint, origin
        self.t = t0
        self.rows = []
        self.count = 0
        self.last_uuid = None

    def _uuid(self):
        self.count += 1
        return 'dddddddd-0000-4000-8000-%012x' % self.count

    def _base(self, kind, **extra):
        self.t += 1.0
        u = self._uuid()
        d = {'parentUuid': self.last_uuid, 'isSidechain': False, 'userType': 'external', 'cwd': self.cwd,
             'sessionId': self.session, 'version': self.version, 'gitBranch': 'main',
             'entrypoint': self.entrypoint, 'type': kind, 'uuid': u, 'timestamp': iso(self.t)}
        d.update(extra)
        self.last_uuid = u
        return d

    def add(self, d):
        self.rows.append(d)
        return d

    # the person, and what writes in their place
    def person(self, text='', images=0, documents=0, **extra):
        if self.origin:
            content = [{'type': 'text', 'text': text}] if text else []
            content += [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'AAAA'}}
                        for _ in range(images)]
            content += [{'type': 'document', 'source': {'type': 'base64', 'data': 'AAAA'}} for _ in range(documents)]
            d = self._base('user', message={'role': 'user', 'content': content}, origin={'kind': 'human'},
                           turnOrigin='human', promptSource='sdk', promptId='p-%d' % self.count,
                           permissionMode='auto')
        else:
            if images or documents:
                content = [{'type': 'text', 'text': text}] if text else []
                content += [{'type': 'image', 'source': {'type': 'base64', 'data': 'AAAA'}} for _ in range(images)]
            else:
                content = text
            d = self._base('user', message={'role': 'user', 'content': content}, promptId='p-%d' % self.count)
        d.update(extra)
        return self.add(d)

    def headless(self, text):
        """An sdk-cli run's prompt: turnOrigin and promptSource 'sdk', no origin."""
        return self.add(self._base('user', message={'role': 'user', 'content': text}, turnOrigin='sdk',
                                   promptSource='sdk'))

    def user_line(self, content, **extra):
        return self.add(self._base('user', message={'role': 'user', 'content': content}, **extra))

    def task_notice(self, words):
        return self.user_line('<task-notification>\n<summary>%s</summary>\n</task-notification>' % words,
                              origin={'kind': 'task-notification'}, turnOrigin='task_notification')

    def peer(self, words):
        return self.user_line('<cross-session-message from="other">%s</cross-session-message>' % words,
                              origin={'kind': 'peer'}, isMeta=True, turnOrigin='peer')

    def companion(self, words):
        return self.user_line(words, isMeta=True, turnCompanion=True)

    def scheduled(self, words):
        return self.user_line(words, isMeta=True, turnOrigin='scheduled')

    def system_turn(self, words):
        return self.user_line(words, turnOrigin='system')

    def local_command(self, words):
        return self.user_line('<command-name>/%s</command-name>\n<command-message>%s</command-message>'
                              % ('status', words))

    def local_output(self, words):
        return self.user_line('<local-command-stdout>%s</local-command-stdout>' % words)

    def tagged(self, tag, words, **extra):
        return self.user_line('<%s>%s</%s>' % (tag, words, tag), **extra)

    def meta(self, words):
        return self.user_line(words, isMeta=True)

    def sidechain(self, words):
        return self.add(self._base('user', message={'role': 'user', 'content': words}, isSidechain=True))

    def compact_summary(self, words, turn='human'):
        return self.user_line(words, isCompactSummary=True, isVisibleInTranscriptOnly=True, turnOrigin=turn)

    def tool_result(self, tool_id, content, is_error=None, **extra):
        block = {'tool_use_id': tool_id, 'type': 'tool_result', 'content': content}
        if is_error is not None:
            block['is_error'] = is_error
        return self.user_line([block], toolUseResult={'stdout': 'duplicate of the result'},
                              sourceToolAssistantUUID=self.last_uuid, **extra)

    # the assistant
    def assistant(self, blocks, **extra):
        msg = {'id': 'msg_%d' % self.count, 'type': 'message', 'role': 'assistant', 'model': 'claude-example',
               'content': blocks, 'stop_reason': 'end_turn', 'stop_sequence': None,
               'usage': {'input_tokens': 1, 'output_tokens': 1}}
        return self.add(self._base('assistant', message=msg, requestId='req_%d' % self.count, **extra))

    def say(self, words):
        return self.assistant([{'type': 'text', 'text': words}])

    def think(self):
        return self.assistant([{'type': 'thinking', 'thinking': '', 'signature': 'c2lnbmF0dXJl'}])

    def tool_use(self, name, inp, tool_id=None):
        tool_id = tool_id or 'toolu_%04d' % (self.count + 1)
        self.assistant([{'type': 'tool_use', 'id': tool_id, 'name': name, 'input': inp, 'caller': {'type': 'direct'}}])
        return tool_id

    def api_error(self, error='rate_limit', words='API Error: made-up'):
        return self.assistant([{'type': 'text', 'text': words}], isApiErrorMessage=True, error=error,
                              apiError=error)

    # summaries and other lines
    def boundary(self):
        return self.add(self._base('system', subtype='compact_boundary', content='Conversation compacted',
                                   level='info', compactMetadata={'trigger': 'auto', 'preTokens': 1},
                                   logicalParentUuid=self.last_uuid))

    def system(self, subtype, **extra):
        return self.add(self._base('system', subtype=subtype, level='info', **extra))

    def queued(self, words='', kind='human', images=0):
        """A queued_command attachment (a message typed while the assistant worked, or the app's own)."""
        self.t += 1.0
        if kind == 'human':
            prompt = [{'type': 'image', 'source': {'type': 'base64', 'data': 'AAAA'}} for _ in range(images)]
            if words:
                prompt.append({'type': 'text', 'text': words})
            a = {'type': 'queued_command', 'prompt': prompt, 'commandMode': 'prompt', 'humanTurn': True,
                 'origin': {'kind': 'human'}, 'delivery_id': 'dlv-%d' % self.count, 'source_uuid': self.last_uuid,
                 'timestamp': iso(self.t)}
        elif kind == 'task-notification':
            a = {'type': 'queued_command', 'prompt': '<task-notification>%s</task-notification>' % words,
                 'commandMode': 'task-notification', 'origin': {'kind': 'task-notification'},
                 'delivery_id': 'dlv-%d' % self.count, 'runId': 'run-1', 'source_uuid': self.last_uuid,
                 'timestamp': iso(self.t)}
        elif kind == 'peer':
            a = {'type': 'queued_command', 'prompt': '<cross-session-message>%s</cross-session-message>' % words,
                 'commandMode': 'prompt', 'isMeta': True, 'origin': {'kind': 'peer'}, 'reminderId': 'r-1',
                 'delivery_id': 'dlv-%d' % self.count, 'source_uuid': self.last_uuid, 'timestamp': iso(self.t)}
        elif kind == 'legacy':
            a = {'type': 'queued_command', 'prompt': words, 'commandMode': 'prompt', 'timestamp': iso(self.t)}
        else:
            raise ValueError(kind)
        return self.add(self._base('attachment', attachment=a))

    def attachment(self, kind, **fields):
        a = {'type': kind}
        a.update(fields)
        return self.add(self._base('attachment', attachment=a))

    def queue(self, operation, content=None, reason=None):
        self.t += 1.0
        d = {'type': 'queue-operation', 'operation': operation, 'timestamp': iso(self.t), 'sessionId': self.session}
        if content is not None:
            d['content'] = content
        if reason is not None:
            d['reason'] = reason
            d['commandUuid'] = 'cmd-1'
            d['deliveryId'] = 'dlv-1'
        return self.add(d)

    def last_prompt(self, words):
        return self.add({'type': 'last-prompt', 'lastPrompt': words, 'leafUuid': self.last_uuid,
                         'sessionId': self.session})

    def custom_title(self, words):
        return self.add({'type': 'custom-title', 'customTitle': words, 'sessionId': self.session})

    def ai_title(self, words):
        return self.add({'type': 'ai-title', 'aiTitle': words, 'sessionId': self.session})

    def skip_lines(self, canary='made-up'):
        """One line of each kind the table leaves out, as 2.1.309-2.1.330 write them (made-up values)."""
        s = self.session
        rows = [
            {'type': 'api-request', 'id': 'r1', 'base': canary, 'params': {}, 'tail': [canary], 'cacheControl': [],
             'keep': 1, 'querySource': 'x', 'requestDigest': 'd', 'shapeHash': 'h', 'sessionId': s,
             'timestamp': iso(self.t), 'version': self.version},
            {'type': 'api-request-blob', 'hash': 'h', 'message': {'role': 'user', 'content': canary},
             'sessionId': s},
            {'type': 'api-request-shape', 'shape': {'k': canary}, 'shapeHash': 'h', 'sessionId': s},
            {'type': 'atis-latch', 'atis': canary, 'sessionId': s},
            {'type': 'agent-name', 'agentName': canary, 'sessionId': s},
            {'type': 'mode', 'mode': canary, 'sessionId': s},
            {'type': 'session-owner', 'accountUuid': ACCOUNT, 'organizationUuid': ORG, 'sessionId': s},
            {'type': 'file-history-snapshot', 'messageId': 'm', 'snapshot': {'f': canary}, 'isSnapshotUpdate': False},
            {'type': 'file-history-delta', 'messageId': 'm', 'snapshotMessageId': 'm', 'backup': {'f': canary},
             'trackingPath': canary, 'timestamp': iso(self.t)},
            {'type': 'cost-state', 'modelUsage': {'m': canary}, 'totalCostUSD': 0.0, 'sessionId': s},
            {'type': 'tools-used', 'tools': [canary], 'sessionId': s},
            {'type': 'data-flow-records', 'records': [canary], 'v': 1, 'sessionId': s},
            {'type': 'derived-from', 'sessions': [canary], 'v': 1, 'sessionId': s},
        ]
        for r in rows:
            self.add(r)

    def write(self, path, newline=True, torn=None):
        return write_lines(path, self.rows, newline=newline, torn=torn)


def claude_dir(home):
    return os.path.join(home, '.claude')


def chat_path(cdir, folder, session):
    return os.path.join(cdir, 'projects', project_name(folder), session + '.jsonl')


def window(cdir, pid, session, cwd, status='idle', name=None, version='2.1.329', entrypoint='claude-vscode',
           peer_token='made-up-peer-token'):
    """A running window's file (2.1.329 keys) and the .key file Claude Code keeps beside it."""
    folder = os.path.join(cdir, 'sessions')
    os.makedirs(folder, exist_ok=True)
    d = {'pid': pid, 'sessionId': session, 'cwd': cwd, 'startedAt': int(T0 * 1000), 'kind': 'interactive',
         'status': status, 'entrypoint': entrypoint, 'version': version, 'updatedAt': int(T0 * 1000),
         'peerToken': peer_token, 'procStart': 'made-up'}
    if name is not None:
        d['name'] = name
    path = os.path.join(folder, '%d.json' % pid)
    with open(path, 'w') as f:
        json.dump(d, f)
    key = os.path.join(folder, '%d.0123abcd.key' % pid)
    with open(key, 'w') as f:
        f.write(peer_token)
    return path, key


# ---------- Codex ----------


class CodexChat:
    """One made-up Codex session file, line by line (0.169.1 unless version says)."""

    def __init__(self, thread=THREAD, cwd=FOLDER, version='0.169.1', source='vscode', thread_source='user',
                 t0=T0, meta=True, canary='made-up'):
        self.thread, self.cwd, self.version = thread, cwd, version
        self.t = t0
        self.t0 = t0
        self.rows = []
        self.n = 0
        if meta:
            self.session_meta(thread, cwd, version, source, thread_source, canary)

    def _line(self, kind, payload, **extra):
        self.t += 1.0
        self.n += 1
        d = {'timestamp': iso(self.t), 'ordinal': self.n, 'type': kind, 'payload': payload}
        d.update(extra)
        self.rows.append(d)
        return d

    def session_meta(self, thread, cwd, version, source, thread_source, canary='made-up'):
        p = {'id': thread, 'session_id': thread, 'timestamp': iso(self.t), 'cwd': cwd, 'originator': 'codex_vscode',
             'cli_version': version, 'source': source, 'thread_source': thread_source, 'model_provider': 'openai',
             'account_scoped': True, 'creator_user_id': 'user-' + canary, 'creator_account_id': ACCOUNT,
             'base_instructions': {'text': 'instructions ' + canary, 'provenance': {'type': 'default'}},
             'history_mode': 'full', 'context_window': {'window_id': 'w1'},
             'runtime_workspace_roots': [cwd], 'git': {'branch': canary, 'commit_hash': '0' * 40}}
        return self._line('session_meta', p)

    def turn_context(self, model='gpt-example', cwd=None):
        return self._line('turn_context', {'turn_id': 't1', 'cwd': cwd or self.cwd, 'model': model,
                                           'approval_policy': 'on-request', 'effort': 'medium', 'summary': 'auto'})

    def item(self, item, **extra):
        p = {'type': 'item_completed', 'thread_id': self.thread, 'turn_id': 't1', 'item': item,
             'started_at_ms': 1, 'completed_at_ms': 2}
        p.update(extra)
        return self._line('event_msg', p)

    def user_item(self, words='', client=True, images=0, mirror=True):
        """The person's message as an item (0.169: client_id on the ones a person sent in the app), and the same
        words as a response_item user-role message (every item's text is one, 132 of 132)."""
        content = [{'type': 'text', 'text': words, 'text_elements': []}] if words else []
        content += [{'type': 'local_image', 'path': '/Users/maya/Pictures/made-up-%d.png' % i} for i in range(images)]
        item = {'type': 'UserMessage', 'id': 'um-%d' % self.n, 'content': content}
        if client:
            item['client_id'] = 'client-%d' % self.n
        line = self.item(item)
        if mirror:
            self.user_message(words)
        return line

    def user_message(self, words, client_authored=None):
        meta = {'history_sequence': self.n}
        if client_authored is not None:
            meta['client_authored'] = client_authored
        return self._line('response_item', {'type': 'message', 'id': 'm-%d' % self.n, 'role': 'user',
                                            'content': [{'type': 'input_text', 'text': words}]}, metadata=meta)

    def env_context(self):
        return self.user_message('<environment_context>\n  <cwd>%s</cwd>\n</environment_context>' % self.cwd)

    def heartbeat(self, words):
        return self.user_item('<heartbeat>%s</heartbeat>' % words, client=False)

    def question_reply(self, words, client=True):
        return self.user_item('<send_user_message_question_reply>%s</send_user_message_question_reply>' % words,
                              client=client)

    def assistant(self, words, phase='final_answer', item=True):
        line = self._line('response_item', {'type': 'message', 'id': 'a-%d' % self.n, 'role': 'assistant',
                                            'content': [{'type': 'output_text', 'text': words, 'annotations': []}],
                                            'phase': phase})
        if item:
            self.item({'type': 'AgentMessage', 'id': 'am-%d' % self.n,
                       'content': [{'type': 'Text', 'text': words}], 'phase': phase})
        return line

    def developer(self, words):
        return self._line('response_item', {'type': 'message', 'role': 'developer',
                                            'content': [{'type': 'input_text', 'text': words}]})

    def task_complete(self, words):
        return self._line('event_msg', {'type': 'task_complete', 'turn_id': 't1', 'last_agent_message': words,
                                        'started_at': 1, 'completed_at': 2, 'duration_ms': 1})

    def command(self, argv, output='', exit_code=0, status='completed', exec_call=True):
        """A shell command as 0.169 writes it: the item, and beside it an `exec` custom tool call and its output."""
        if exec_call:
            self.custom_call('exec', 'await tools.exec(%s)' % json.dumps(argv), 'call-%d' % self.n)
            self.custom_output('call-%d' % (self.n - 1), [{'type': 'input_text', 'text': output}])
        return self.item({'type': 'CommandExecution', 'id': 'ce-%d' % self.n, 'command': argv, 'cwd': self.cwd,
                          'process_id': '1', 'source': 'unified_exec_startup', 'status': status,
                          'aggregated_output': output, 'exit_code': exit_code,
                          'duration': {'secs': 0, 'nanos': 1}, 'parsed_cmd': [{'cmd': 'x', 'type': 'unknown'}]})

    def file_change(self, changes, status='completed'):
        """changes: path -> ('add', content) or ('update', unified diff). status: 'completed', or another word
        ('failed', 'declined') for a change that was not made."""
        out = {}
        for path, (how, body) in changes.items():
            out[path] = {'type': how, 'content': body} if how == 'add' else \
                {'type': how, 'unified_diff': body, 'move_path': None}
        return self.item({'type': 'FileChange', 'id': 'fc-%d' % self.n, 'changes': out, 'status': status})

    def mcp(self, server, tool, arguments, result_text, is_error=False, status='completed', images=0):
        content = [{'type': 'text', 'text': result_text}]
        content += [{'type': 'image', 'data': 'AAAA', 'mimeType': 'image/png'} for _ in range(images)]
        return self.item({'type': 'McpToolCall', 'id': 'mc-%d' % self.n, 'server': server, 'tool': tool,
                          'arguments': arguments, 'status': status,
                          'result': {'content': content, 'isError': is_error, '_meta': {'k': 'v'}}})

    def mcp_failed(self, server, tool, arguments, message):
        """A connector call that failed before it had a result (0.169.1): no result, the reason under error."""
        return self.item({'type': 'McpToolCall', 'id': 'mc-%d' % self.n, 'server': server, 'tool': tool,
                          'arguments': arguments, 'status': 'failed', 'result': None, 'error': {'message': message}})

    def image_view(self):
        return self.item({'type': 'ImageView', 'id': 'iv-%d' % self.n, 'path': '/Users/maya/site/made-up.png'})

    def reasoning_item(self):
        return self.item({'type': 'Reasoning', 'id': 'r-%d' % self.n, 'summary_text': [], 'raw_content': []})

    def reasoning(self):
        return self._line('response_item', {'type': 'reasoning', 'id': 'rs-%d' % self.n, 'summary': [],
                                            'encrypted_content': 'ZW5jcnlwdGVk'})

    def function_call(self, name, arguments, call_id, namespace=None):
        p = {'type': 'function_call', 'id': 'fc-%d' % self.n, 'name': name, 'arguments': arguments,
             'call_id': call_id, 'status': 'completed'}
        if namespace:
            p['namespace'] = namespace
        return self._line('response_item', p)

    def function_output(self, call_id, output):
        return self._line('response_item', {'type': 'function_call_output', 'id': 'fo-%d' % self.n,
                                            'call_id': call_id, 'output': output})

    def custom_call(self, name, body, call_id):
        return self._line('response_item', {'type': 'custom_tool_call', 'id': 'cc-%d' % self.n, 'name': name,
                                            'input': body, 'call_id': call_id, 'status': 'completed'})

    def custom_output(self, call_id, output):
        return self._line('response_item', {'type': 'custom_tool_call_output', 'id': 'co-%d' % self.n,
                                            'call_id': call_id, 'output': output})

    def compaction_item(self):
        """The response_item a compaction writes beside its compacted line: the app's summary, encrypted."""
        return self._line('response_item', {'type': 'compaction', 'encrypted_content': 'ZW5jcnlwdGVkIHN1bW1hcnk='})

    def compacted(self, message, retained=None, incomplete=False, pad=0):
        """A compacted line (keys in the order 0.153.4 to 0.169.1 write them). Every one seen has message '' and
        the summary kept encrypted; pad fills replacement_history with that many made-up bytes, as the large ones
        (5.4 to 7.8 MiB) carry the history they replace."""
        history = [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'p' * pad}]}] \
            if pad else []
        p = {'message': message, 'replacement_history': history, 'window_number': 1, 'window_id': 'w2',
             'first_window_id': 'w1', 'compaction_response_id': None}
        if retained is not None:
            p['retained_context'] = {'user_messages': retained, 'user_messages_incomplete': incomplete,
                                     'assistant_messages': [], 'assistant_messages_incomplete': False,
                                     'incomplete': False, 'next_order': len(retained), 'verified_answers': []}
        return self._line('compacted', p)

    def token_count(self, primary=None, secondary=None, limit_id='codex', plan='pro', reached=None, at=None):
        rl = {'limit_id': limit_id, 'limit_name': None, 'primary': primary, 'secondary': secondary,
              'credits': {'has_credits': False, 'unlimited': False, 'balance': '0'}, 'individual_limit': None,
              'spend_control_reached': None, 'plan_type': plan, 'rate_limit_reached_type': reached}
        if limit_id is None:
            del rl['limit_id']
        line = self._line('event_msg', {'type': 'token_count', 'info': {'total_token_usage': {'total_tokens': 1}},
                                        'rate_limits': rl})
        if at is not None:
            line['timestamp'] = iso(at)
        return line

    def skip_lines(self, canary='made-up'):
        """One line of each kind the table leaves out (made-up values)."""
        self._line('world_state', {'full': True, 'state': {'agents_md': {'text': canary},
                                                           'tools': {'mcp__example__tool': canary}}})
        self._line('token_usage_record', {'thread_id': self.thread, 'usage': {'total_tokens': 1}})
        self._line('inter_agent_communication_metadata', {'trigger_turn': True})
        self._line('event_msg', {'type': 'task_started', 'turn_id': 't1', 'turn_attribution': {'k': canary}})
        self._line('event_msg', {'type': 'thread_settings_applied', 'thread_settings': {'cwd': canary}})
        self._line('event_msg', {'type': 'turn_aborted', 'reason': canary})
        self._line('response_item', {'type': 'additional_tools', 'tools': [canary]})
        self._line('response_item', {'type': 'configuration_update', 'reasoning': {'effort': canary}})
        self._line('response_item', {'type': 'agent_message', 'author': 'a', 'recipient': 'b',
                                     'content': [{'type': 'input_text', 'text': canary}]})
        self.item({'type': 'AgentMessage', 'id': 'x', 'content': [{'type': 'Text', 'text': canary}]})
        self.item({'type': 'SubAgentActivity', 'id': 'x', 'text': canary})
        self.item({'type': 'Extension', 'id': 'x', 'query': canary})
        self.item({'type': 'ContextCompaction', 'id': 'x'})
        self.item({'type': 'FunctionCallOutput', 'id': 'x', 'output': canary})
        self.item({'type': 'CollabAgentToolCall', 'id': 'x', 'prompt': canary})

    def file_name(self):
        y, mo, d, h, mi, s = time.localtime(self.t0)[:6]
        return 'rollout-%04d-%02d-%02dT%02d-%02d-%02d-%s.jsonl' % (y, mo, d, h, mi, s, self.thread)

    def day_folder(self, home):
        y, mo, d = time.localtime(self.t0)[:3]
        return os.path.join(home, 'sessions', '%04d' % y, '%02d' % mo, '%02d' % d)

    def write(self, home, newline=True, torn=None):
        """Into <codex home>/sessions/YYYY/MM/DD/ (the local date the thread began). Returns the path."""
        return write_lines(os.path.join(self.day_folder(home), self.file_name()), self.rows, newline=newline,
                           torn=torn)


def codex_home(home):
    return os.path.join(home, '.codex')


def write_index(chome, rows):
    """session_index.jsonl: rows of (id, thread_name, updated_at epoch)."""
    os.makedirs(chome, exist_ok=True)
    path = os.path.join(chome, 'session_index.jsonl')
    with open(path, 'w') as f:
        for tid, name, at in rows:
            f.write(dump({'id': tid, 'thread_name': name, 'updated_at': iso(at)}) + '\n')
    return path


def touch(path, t):
    os.utime(path, (t, t))
