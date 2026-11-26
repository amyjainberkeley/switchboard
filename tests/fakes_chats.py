"""Made-up chat files in Claude Code's own line shapes (2.1.296 to 2.1.325), for the chats tests.

The account lines are the ones tests/test_chats_file.py writes; replies, prompts, tool calls and their results
follow tests/test_autoswitch_sessions.py and the real files (compact JSON, one assistant line per content block with
the request id after the message, results as tool_result blocks with tool_use_id and is_error). Every id, address and
word here is made up."""
import json
import os
import time

ORG_A = 'aaaaaaaa-0000-4000-8000-00000000000a'
ORG_B = 'bbbbbbbb-0000-4000-8000-00000000000b'
ORG_C = 'cccccccc-0000-4000-8000-00000000000c'      # an organization Switchboard does not keep
ACCT = 'a1a1a1a1-0000-4000-8000-00000000000a'
A, B = 'a@example.com', 'b@example.com'
CHAT = '5e55105e-0000-4000-8000-000000000001'
CHAT2 = '5e55105e-0000-4000-8000-000000000002'
CHAT3 = '7a7a7a7a-0000-4000-8000-000000000003'
FOLDER = '/Users/example/work/billing'
PROJECT = '-Users-example-work-billing'


def j(d):
    return json.dumps(d, separators=(',', ':'))


def stamp(t):
    whole = int(t)
    ms = int(round((t - whole) * 1000)) % 1000
    y, mo, d, h, mi, s = time.gmtime(whole)[:6]
    return '%04d-%02d-%02dT%02d:%02d:%02d.%03dZ' % (y, mo, d, h, mi, s, ms)


def _head(chat, cwd, uid='00000000-0000-4000-8000-0000000000aa'):
    return {'parentUuid': uid, 'isSidechain': False, 'userType': 'external', 'cwd': cwd, 'sessionId': chat,
            'version': '2.1.325', 'gitBranch': 'main'}


def owner(org=ORG_A, chat=CHAT, acct=ACCT):
    return j({'type': 'session-owner', 'accountUuid': acct, 'organizationUuid': org, 'sessionId': chat})


def account(org, t, chat=CHAT, cwd=FOLDER):
    d = {'parentUuid': '00000000-0000-4000-8000-0000000000aa', 'isSidechain': False,
         'attachment': {'type': 'credential_org', 'organizationUuid': org}, 'type': 'attachment',
         'uuid': '00000000-0000-4000-8000-0000000000bb', 'timestamp': stamp(t), 'userType': 'external',
         'entrypoint': 'cli', 'cwd': cwd, 'sessionId': chat, 'version': '2.1.325', 'gitBranch': 'main'}
    return j(d)


def prompt(text, t, chat=CHAT, cwd=FOLDER):
    d = _head(chat, cwd)
    d.update(type='user', message={'role': 'user', 'content': text},
             uuid='00000000-0000-4000-8000-0000000000cc', timestamp=stamp(t))
    return j(d)


def reply(t, rid, chat=CHAT, text='done', model='claude-opus-5-5', tools=(), cwd=FOLDER, inp=None):
    """One assistant line: a text block, or one tool_use block per (id, name) in `tools`."""
    content = [{'type': 'tool_use', 'id': tid, 'name': name, 'input': inp or {'query': 'q'}} for tid, name in tools] \
        or [{'type': 'text', 'text': text}]
    d = _head(chat, cwd)
    d.update(message={'id': 'msg_' + rid, 'type': 'message', 'role': 'assistant', 'model': model,
                      'content': content, 'stop_reason': 'tool_use' if tools else 'end_turn',
                      'usage': {'input_tokens': 10, 'output_tokens': 5}},
             requestId='req_' + rid, type='assistant', uuid='00000000-0000-4000-8000-0000000000dd',
             timestamp=stamp(t))
    return j(d)


def result(t, tool_id, error=False, text='ok', chat=CHAT, cwd=FOLDER):
    block = {'tool_use_id': tool_id, 'type': 'tool_result', 'content': text}
    if error:
        block['is_error'] = True
    d = _head(chat, cwd)
    d.update(type='user', message={'role': 'user', 'content': [block]}, uuid='00000000-0000-4000-8000-0000000000ee',
             timestamp=stamp(t), toolUseResult=('Error: ' + text) if error else {'content': text})
    return j(d)


def title(kind, text, chat=CHAT):
    key = 'customTitle' if kind == 'custom-title' else 'aiTitle'
    return j({'type': kind, key: text, 'sessionId': chat})


def progress(t, rid, chat=CHAT):
    """A helper's reply as Claude Code copies it into the chat's own file while the helper runs: a progress line
    whose nested message is an assistant one. It is not one of the chat's own turns."""
    inner = json.loads(reply(t, rid, chat=chat))
    return j({'parentUuid': None, 'isSidechain': False, 'type': 'progress', 'data': {'type': 'agent_progress',
              'message': inner}, 'toolUseID': 'toolu_p', 'uuid': '00000000-0000-4000-8000-0000000000ff',
              'timestamp': stamp(t)})


def write(root, lines, chat=CHAT, project=PROJECT, mtime=None, torn=None, mode='w'):
    """A chat file under Claude Code's projects folder (`root`), its lines whole; `torn` is a last line still
    being written (no line end). `mtime`: the file's change time."""
    path = os.path.join(root, project, chat + '.jsonl')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode + 'b') as fh:
        for line in lines:
            fh.write(line.encode('utf-8') + b'\n')
        if torn:
            fh.write(torn.encode('utf-8'))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def helper(root, chat=CHAT, project=PROJECT, name='agent-1', mtime=None, workflow=None):
    """A helper's file under its chat: <project>/<chat>/subagents/[workflows/<wf>/]agent-<id>.jsonl."""
    parts = [root, project, chat, 'subagents'] + (['workflows', workflow] if workflow else [])
    folder = os.path.join(*parts)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + '.jsonl')
    with open(path, 'w') as fh:
        fh.write(reply(time.time(), 'helper-' + name) + '\n')
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path
