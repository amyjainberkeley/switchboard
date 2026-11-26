"""Stand-ins for the hook and hold tests (tests/test_hooks_chat_moves*.py, tests/test_autoswitch_connector_hold*.py).

- A chat's own file as Claude Code 2.1.325 writes it: its messages, tool calls and answers, with the two lines that
  name an account (a session-owner line, and a credential_org attachment each time the login it is on changes), in
  the shapes switchboard/chatfile.py reads (tests/test_chats_file.py has the same).
- chats.json as the page's look at every chat leaves it (the comment block near the end of switchboard/chatfile.py).
- The hook inputs Claude Code hands a hook for a claude.ai connector's answer (PostToolUse) and its failure
  (PostToolUseFailure: {..., tool_name, tool_input, tool_use_id, error, is_interrupt, duration_ms}).

The failure wordings are Claude Code's own, read with `strings` from 2.1.309 and 2.1.325 (2026-11), never by running it:
  MCP server "<server>" session expired
  MCP server "<server>" is not connected                       (sometimes followed by " (status: ...)")
  MCP server "<server>" connection closed before the tool call ran
  MCP server "<server>" connection closed before the tool call was answered. The tool may have run: ...
where a claude.ai connector's server is "claude.ai <Name>" ("claude.ai Google Drive").

Every id, name and address here is made up.
"""
import json
import os
import time

from fakes_links import A, B, C, G  # noqa: F401  (the accounts: ana@work.example, ana@home.example, ...)

ORG_A = 'aaaaaaaa-0000-4000-8000-00000000000a'
ORG_B = 'bbbbbbbb-0000-4000-8000-00000000000b'
ORG_C = 'cccccccc-0000-4000-8000-00000000000c'
ORG_X = 'eeeeeeee-0000-4000-8000-00000000000e'       # an organization Switchboard does not keep
ACCT_A = 'a1a1a1a1-0000-4000-8000-00000000000a'
ORGS = {ORG_A: A, ORG_B: B, ORG_C: C}
CHAT = '5e55105e-0000-4000-8000-000000000001'
FOLDER = '/Users/example/project'
DRIVE = 'mcp__claude_ai_Google_Drive__search'
LINEAR = 'mcp__claude_ai_Linear__list_issues'
DOCS = 'mcp__claude_ai_Claude_Docs__batch'

WORDINGS = ('MCP server "claude.ai %s" session expired',
            'MCP server "claude.ai %s" is not connected',
            'MCP server "claude.ai %s" is not connected (status: failed)',
            'MCP server "claude.ai %s" connection closed before the tool call ran',
            'MCP server "claude.ai %s" connection closed before the tool call was answered. The tool may have run: '
            'check whether it did before running it again.')


def stamp(t):
    """Claude Code's own time words for second `t` (UTC, with thousandths)."""
    whole = int(t)
    ms = int(round((t - whole) * 1000))
    y, mo, d, h, mi, s = time.gmtime(whole)[:6]
    return '%04d-%02d-%02dT%02d:%02d:%02d.%03dZ' % (y, mo, d, h, mi, s, ms)


def owner_line(org=ORG_A, acct=ACCT_A, chat=CHAT):
    return {'type': 'session-owner', 'accountUuid': acct, 'organizationUuid': org, 'sessionId': chat}


def org_line(org, t, chat=CHAT, cwd=FOLDER):
    return {'parentUuid': '00000000-0000-4000-8000-0000000000aa', 'isSidechain': False,
            'attachment': {'type': 'credential_org', 'organizationUuid': org}, 'type': 'attachment',
            'uuid': '00000000-0000-4000-8000-0000000000bb', 'timestamp': stamp(t), 'userType': 'external',
            'entrypoint': 'cli', 'cwd': cwd, 'sessionId': chat, 'version': '2.1.325', 'gitBranch': 'main'}


def said(text, t, role='user'):
    return {'type': role, 'timestamp': stamp(t), 'sessionId': CHAT, 'message': {'role': role, 'content': text}}


def tool_use(name, t, uid='toolu_01'):
    """The assistant line that calls a tool (its answer comes back as a tool_result with tool_use_id `uid`)."""
    return {'type': 'assistant', 'timestamp': stamp(t), 'sessionId': CHAT,
            'message': {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': uid, 'name': name, 'input': {}}]}}


def tool_result(text, t, uid='toolu_01', error=False):
    block = {'type': 'tool_result', 'tool_use_id': uid, 'content': text}
    if error:
        block['is_error'] = True
    return {'type': 'user', 'timestamp': stamp(t), 'sessionId': CHAT, 'message': {'role': 'user', 'content': [block]}}


def doc_born(t, url, uid='toolu_01'):
    """The Claude Docs connector's answer when a doc is born (its link inside)."""
    text = json.dumps({'verdict': 'ok', 'created': {'id': 'd1'}, 'frame': {'slug': 'doc', 'url': url}},
                      separators=(',', ':'))
    return tool_result(text, t, uid)


class ChatFile(object):
    """One chat's file in a made-up Claude Code projects folder."""

    def __init__(self, folder, chat=CHAT, project='-Users-example-project'):
        self.path = os.path.join(folder, project, chat + '.jsonl')
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def add(self, *lines):
        with open(self.path, 'ab') as fh:
            for d in lines:
                fh.write((d if isinstance(d, bytes) else json.dumps(d).encode('utf-8')) + b'\n')
        return os.path.getsize(self.path)

    def size(self):
        return os.path.getsize(self.path)

    def scribble_head(self, nbytes, org=ORG_X):
        """Overwrite the file's first `nbytes` in place (same file, same size): unreadable junk between whole account
        lines that name `org`, so a look that read those bytes again would be caught naming it."""
        junk = b'\xff\xfe not a line of the chat\n' + json.dumps(org_line(org, 1.0)).encode() + b'\n'
        data = (junk * (nbytes // len(junk) + 1))[:nbytes]
        fd = os.open(self.path, os.O_RDWR)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)


def write_index(data_dir, chats=None, orgs=None, connectors=None, at=None, done=True, since=None):
    """chats.json as the page's look leaves it. `done`: that look read every chat of its window whole, so the file has
    "done" and "since" (the window's start, 7 days before `at` unless given); without it, neither."""
    at = at or time.time()
    doc = {'v': 1, 'at': at, 'orgs': dict(ORGS if orgs is None else orgs), 'chats': chats or {},
           'connectors': connectors if connectors is not None else {}}
    if done:
        doc.update(done=True, since=since if since is not None else at - 7 * 86400)
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, 'chats.json')
    with open(path, 'w') as fh:
        json.dump(doc, fh)
    return path


def entry(path, end, owner=ORG_A, steps=(), last=None):
    return {'file': path, 'end': end, 'owner': owner, 'steps': [list(s) for s in steps], 'cwd': FOLDER, 'last': last}


def failure(tool, words, sid=CHAT, transcript='', name='Google Drive', **more):
    """PostToolUseFailure for a connector's call, with Claude Code's own error words."""
    return dict({'hook_event_name': 'PostToolUseFailure', 'session_id': sid, 'transcript_path': transcript,
                 'cwd': FOLDER, 'permission_mode': 'default', 'tool_name': tool, 'tool_input': {'query': 'plan'},
                 'tool_use_id': 'toolu_02', 'error': words % name if '%s' in words else words,
                 'is_interrupt': False, 'duration_ms': 412}, **more)


def answered(tool, text='[{"id": "f1", "name": "Plan"}]', sid=CHAT, transcript='', error=False, **more):
    """PostToolUse for a connector's call: the tool's own answer, as Claude Code's content blocks."""
    resp = [{'type': 'text', 'text': text}]
    if error:
        resp = {'content': resp, 'isError': True}
    return dict({'hook_event_name': 'PostToolUse', 'session_id': sid, 'transcript_path': transcript, 'cwd': FOLDER,
                 'permission_mode': 'default', 'tool_name': tool, 'tool_input': {'query': 'plan'},
                 'tool_response': resp, 'tool_use_id': 'toolu_03', 'duration_ms': 640}, **more)
