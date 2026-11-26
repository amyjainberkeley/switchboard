"""Writes the made-up Claude Code history in chats.json as a projects/ tree, in the transcript
format Claude Code uses (one compact JSON object per line). Standard library only.

    build(root)                      the 40 chats, for the search test
    build(root, copies=N, pad=B)     N copies of every chat with B bytes of tool output per turn,
                                     for timing the index on a tree the size of a real week

Besides the person's prompts, the assistant's text and the titles, every chat also gets the
lines the index must skip: tool output, thinking, an injected notice (isMeta), a background
task notice, a helper's side-chain line, an API request record, and a helper transcript under
<session>/subagents/. Those carry the word NOISE, which no search may find.
"""
import json
import os
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
NOISE = 'zanzibarquux'
NOISE_TEXT = ('%s: stripe webhook signature redirect loop docker image liveness probe heap snapshot websocket '
              'backoff graphql resolvers redis token bucket excel invoice daylight saving' % NOISE)
NS = uuid.UUID('6b1f3a52-2c1e-4a0e-9a57-1d2c3b4a5f60')


def load():
    with open(os.path.join(HERE, 'chats.json')) as f:
        chats = json.load(f)['chats']
    with open(os.path.join(HERE, 'questions.json')) as f:
        questions = json.load(f)['questions']
    return chats, questions


def project_dir(folder):
    """Claude Code's folder name for a working directory: every character other than a letter or digit
    becomes '-'."""
    return ''.join(c if c.isalnum() and c.isascii() else '-' for c in folder)


def session_id(chat_id, copy=0):
    return str(uuid.uuid5(NS, '%s/%d' % (chat_id, copy)))


def _ts(start, minutes):
    # start is "YYYY-MM-DDTHH:MM:SSZ"; add whole minutes without importing datetime arithmetic tricks
    from datetime import datetime, timedelta, timezone
    t = datetime.strptime(start, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc) + timedelta(minutes=minutes)
    return t.strftime('%Y-%m-%dT%H:%M:%S.000Z')


def _line(obj):
    return json.dumps(obj, separators=(',', ':'), ensure_ascii=False) + '\n'


def chat_lines(chat, sid, pad=0, folder=None, start=None):
    folder = folder or chat['folder']
    start = start or chat['start']
    noise = chat.get('noise') or {}
    out = []
    base = {'isSidechain': False, 'userType': 'external', 'cwd': folder, 'sessionId': sid,
            'version': '2.1.320', 'gitBranch': 'main'}
    n = [0]

    def u(i):
        n[0] += 1
        return str(uuid.uuid5(NS, '%s/%d/%d' % (sid, i, n[0])))

    def add(kind, minute, **fields):
        d = dict(base)
        d.update(fields)
        d['type'] = kind
        d['uuid'] = u(minute)
        d['timestamp'] = _ts(start, minute)
        out.append(_line(d))

    out.append(_line({'type': 'permission-mode', 'permissionMode': 'default', 'sessionId': sid}))
    minute = 0
    for t, (prompt, reply) in enumerate(chat['turns']):
        add('user', minute, message={'role': 'user', 'content': prompt})
        mid = 'msg_%s_%d' % (sid[:8], t)
        rid = 'req_%s_%d' % (sid[:8], t)
        usage = {'input_tokens': 1200, 'output_tokens': 300, 'cache_creation_input_tokens': 0,
                 'cache_read_input_tokens': 9000}
        think = noise.get('thinking') or 'Thinking it over. %s' % NOISE_TEXT
        add('assistant', minute, requestId=rid, message={
            'id': mid, 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5-5',
            'content': [{'type': 'thinking', 'thinking': think + ' ' + NOISE, 'signature': 'sig'}], 'usage': usage})
        tool_id = 'toolu_%s_%d' % (sid[:8], t)
        add('assistant', minute, requestId=rid, message={
            'id': mid, 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5-5',
            'content': [{'type': 'tool_use', 'id': tool_id, 'name': 'Bash',
                         'input': {'command': 'grep -rn %s .' % NOISE, 'description': NOISE_TEXT}}],
            'usage': usage})
        output = (noise.get('tool_output') or 'ok') + ' ' + NOISE_TEXT
        if pad:
            filler = ('%s line of tool output that the index never reads\n' % NOISE)
            output += '\n' + filler * max(1, pad // len(filler))
        add('user', minute, toolUseResult={'stdout': NOISE}, message={'role': 'user', 'content': [
            {'tool_use_id': tool_id, 'type': 'tool_result', 'content': output, 'is_error': False}]})
        add('assistant', minute + 1, requestId=rid + 'b', message={
            'id': mid + 'b', 'type': 'message', 'role': 'assistant', 'model': 'claude-opus-5-5',
            'content': [{'type': 'text', 'text': reply}], 'usage': usage})
        if t == 0:
            if chat.get('ai_title'):
                out.append(_line({'type': 'ai-title', 'aiTitle': chat['ai_title'], 'sessionId': sid}))
            if chat.get('custom_title'):
                out.append(_line({'type': 'custom-title', 'customTitle': chat['custom_title'], 'sessionId': sid}))
            # Lines that are not the person's words: an injected notice, a background task notice,
            # a helper's side-chain line, and an API request record.
            add('user', minute + 1, isMeta=True, message={'role': 'user', 'content': NOISE_TEXT})
            add('user', minute + 1, message={'role': 'user', 'content':
                '<task-notification><task-id>x1</task-id><summary>%s</summary></task-notification>' % NOISE_TEXT})
            d = dict(base, isSidechain=True)
            d.update({'type': 'user', 'uuid': u(99), 'timestamp': _ts(start, minute + 1),
                      'message': {'role': 'user', 'content': NOISE_TEXT}})
            out.append(_line(d))
            out.append(_line({'type': 'api-request', 'sessionId': sid, 'body': json.dumps(
                {'messages': [{'role': 'user', 'content': NOISE_TEXT}]})}))
            out.append(_line({'type': 'last-prompt', 'lastPrompt': NOISE_TEXT, 'sessionId': sid}))
        minute += 3
    return out


def helper_lines(sid, folder, start):
    d = {'isSidechain': True, 'userType': 'external', 'cwd': folder, 'sessionId': sid, 'agentId': 'a1',
         'type': 'user', 'uuid': str(uuid.uuid5(NS, sid + '/helper')), 'timestamp': _ts(start, 1),
         'message': {'role': 'user', 'content': 'Helper brief: ' + NOISE_TEXT}}
    a = dict(d, type='assistant', uuid=str(uuid.uuid5(NS, sid + '/helper/a')), requestId='req_h_' + sid[:8],
             message={'id': 'msg_h_' + sid[:8], 'type': 'message', 'role': 'assistant', 'model': 'claude-sonnet-5',
                      'content': [{'type': 'text', 'text': 'Helper report: ' + NOISE_TEXT}],
                      'usage': {'input_tokens': 10, 'output_tokens': 10}})
    return [_line(d), _line(a)]


def build(root, copies=1, pad=0, chats=None):
    """Write the tree under root (Claude Code's projects folder). Returns {chat id: session id} for copy 0
    and the number of bytes written."""
    chats = chats or load()[0]
    ids = {}
    total = 0
    for copy in range(copies):
        for chat in chats:
            sid = session_id(chat['id'], copy)
            if copy == 0:
                ids[chat['id']] = sid
            folder = chat['folder'] if copy == 0 else '%s-%d' % (chat['folder'], copy % 7)
            pdir = os.path.join(root, project_dir(folder))
            os.makedirs(pdir, exist_ok=True)
            data = ''.join(chat_lines(chat, sid, pad=pad, folder=folder)).encode('utf-8')
            with open(os.path.join(pdir, sid + '.jsonl'), 'wb') as f:
                f.write(data)
            sub = os.path.join(pdir, sid, 'subagents')
            os.makedirs(sub, exist_ok=True)
            hdata = ''.join(helper_lines(sid, folder, chat['start'])).encode('utf-8')
            if pad:
                hdata += (('{"type":"assistant","isSidechain":true,"message":{"content":[{"type":"text","text":"%s"}]}}\n'
                           % NOISE) * max(1, pad // 90)).encode('utf-8')
            with open(os.path.join(sub, 'agent-a1.jsonl'), 'wb') as f:
                f.write(hdata)
            total += len(data) + len(hdata)
    return ids, total
