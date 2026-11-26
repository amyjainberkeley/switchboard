import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""Claude Code session hooks across a switch. Installed and removed like the notification
hooks, with an exact undo whatever else of Switchboard's is in the file; what each hook says, in plain words that
name the owner and the exact command and never ask anyone to sign in."""
import io
import itertools
import json
import os
import subprocess
import time
import unittest
from unittest import mock

from fakes_links import A, B, C, G, ID1, ID2, ID3, LINK, Home

from switchboard import notify_hooks, session_hooks, statusline
from switchboard.accounts import api

NOW = 1_800_000_000.0
FORBIDDEN = ('sign in', 'sign-in', 'may have been deleted', 'may be deleted', 'might have been deleted',
             'reinstall', 'please run /login', 'then run /login', 'run /logout and /login')


class Settings(Home):
    def setUp(self):
        super().setUp()
        self.path = session_hooks.settings_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def write(self, text):
        with open(self.path, 'w', newline='') as f:
            f.write(text)

    def read(self):
        try:
            with open(self.path, newline='') as f:
                return f.read()
        except FileNotFoundError:
            return None

    def on(self):
        return session_hooks.apply(session_hooks.install_plan(self.path, command_line=session_hooks.command()))

    def off(self):
        return session_hooks.apply(session_hooks.uninstall_plan(self.path))


class InstallAndExactUndo(Settings):
    def test_into_no_file_and_back_to_no_file(self):
        self.assertIsNone(self.read())
        self.assertTrue(self.on()['changed'])
        obj = json.loads(self.read())
        self.assertEqual(sorted(obj['hooks']), sorted(session_hooks.EVENTS))
        self.assertEqual(obj['hooks']['PostToolUse'][0]['matcher'], session_hooks.ITEM_TOOLS)
        self.assertEqual(obj['hooks']['PostToolUseFailure'][0]['matcher'], session_hooks.ITEM_TOOLS)
        self.assertEqual(obj['hooks']['PreToolUse'][0]['matcher'], session_hooks.CHROME_TOOLS)
        self.assertNotIn('matcher', obj['hooks']['SessionStart'][0])
        self.assertTrue(all(h['hooks'][0]['command'].endswith(session_hooks.MARK) for h in
                            (g[0] for g in obj['hooks'].values())))
        self.assertEqual(session_hooks.status(self.path)['in'], True)
        self.off()
        self.assertIsNone(self.read())

    def test_a_file_of_yours_comes_back_byte_for_byte(self):
        for text in ('{\n    "model": "opus",\n    "hooks": {\n        "PostToolUse": [\n            {\n                '
                     '"matcher": "Write|Edit",\n                "hooks": [{"type": "command", "command": "prettier"}]\n'
                     '            }\n        ]\n    },\n    "env": {"X": "1"}\n}\n',
                     '{"permissions":{"allow":["Bash(ls)"]}}',
                     '{\r\n  "hooks": {},\r\n  "statusLine": {"type": "command", "command": "mine"}\r\n}\r\n',
                     '{ }\n', '{"hooks": {   }}', '\n'):
            with self.subTest(text=text[:30]):
                self.write(text)
                self.on()
                after = json.loads(self.read())
                self.assertIn('SessionStart', after['hooks'])
                if 'Write|Edit' in text:
                    self.assertEqual(after['hooks']['PostToolUse'][0]['matcher'], 'Write|Edit')   # yours first, untouched
                    self.assertEqual(after['hooks']['PostToolUse'][1]['matcher'], session_hooks.ITEM_TOOLS)
                self.off()
                self.assertEqual(self.read(), text)

    def test_a_file_it_cannot_understand_is_left_alone_and_off_never_raises(self):
        self.write('{"hooks": [1, 2]}')
        plan = session_hooks.install_plan(self.path)
        self.assertFalse(plan['ok'])
        self.assertIn('not an object', plan['message'])
        r = session_hooks.turn(False)
        self.assertFalse(r['changed'])
        self.write('{nope')
        self.assertIsNone(session_hooks.status(self.path)['in'])

    def test_a_moved_install_has_its_commands_brought_up_to_date_in_place(self):
        self.write('{"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}]}}')
        session_hooks.apply(session_hooks.install_plan(self.path, command_line=session_hooks.command(folder='/old/place')))
        new = session_hooks.command()
        plan = session_hooks.install_plan(self.path, command_line=new)
        self.assertIn('updated', plan['message'])
        session_hooks.apply(plan)
        found = session_hooks._found(json.loads(self.read()))
        self.assertTrue(all(c == new for cs in found.values() for c in cs))
        self.assertEqual(sum(len(cs) for cs in found.values()), len(session_hooks.EVENTS))
        self.off()
        self.assertEqual(json.loads(self.read()), {'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [
            {'type': 'command', 'command': 'x'}]}]}})

    def test_turn_runs_the_command_once_before_it_goes_in(self):
        compiled = []
        self.patch(statusline, 'precompile', lambda *a, **k: compiled.append(k.get('script')) or None)
        r = session_hooks.turn(True)
        self.assertEqual(sorted(os.path.basename(p) for p in compiled), ['session_hooks.py', 'statusline.py'])
        self.assertTrue(r['changed'])
        s = session_hooks.status(self.path)
        self.assertEqual((s['in'], s['works']), (True, True))
        self.assertIsNone(session_hooks.verify(session_hooks.command()))
        bad = session_hooks.command(python='/nowhere/python3')
        self.assertIn('is not there', session_hooks.verify(bad))
        session_hooks.turn(False)
        self.assertIs(session_hooks.status(self.path)['in'], False)


class TogetherWithTheOtherPieces(Settings):
    """The status line, the notification hooks and the session hooks in one file: whichever order they go in and come
    out, the file ends exactly as it began (no file stays no file)."""

    PIECES = ('statusline', 'notify', 'sessions')

    def put(self, name):
        if name == 'statusline':
            statusline.apply(statusline.install_plan(self.path))
        elif name == 'notify':
            notify_hooks.apply(notify_hooks.install_plan(self.path, command_line=notify_hooks.command()))
        else:
            self.on()

    def take(self, name):
        if name == 'statusline':
            statusline.apply(statusline.uninstall_plan(self.path))
        elif name == 'notify':
            notify_hooks.apply(notify_hooks.uninstall_plan(self.path))
        else:
            self.off()

    def test_every_order_in_and_out(self):
        starts = (None, '{\n  "model": "opus"\n}\n', '{}', '{"hooks": {}}\n')
        for start in starts:
            for ins in itertools.permutations(self.PIECES):
                for outs in itertools.permutations(self.PIECES):
                    with self.subTest(start=start, ins=ins, outs=outs):
                        if start is None:
                            if os.path.exists(self.path):
                                os.unlink(self.path)
                        else:
                            self.write(start)
                        for name in ins:
                            self.put(name)
                        obj = json.loads(self.read())
                        self.assertIn('statusLine', obj)
                        self.assertIn('Stop', obj['hooks'])
                        self.assertIn('PreToolUse', obj['hooks'])
                        for name in outs:
                            self.take(name)
                        self.assertEqual(self.read(), start)
                        for rec in (session_hooks._records(), notify_hooks._records(), statusline._records()):
                            self.assertEqual(rec, {})


class HookSandbox(Home):
    """owners.json as the page writes it, and one way to feed a hook its input."""

    def owners(self, active=A, timeline=None, accounts=None, artifacts_=None, chrome=None, fallback=None, auto=True):
        m = {'v': 1, 'written_at': NOW, 'active': active, 'browser': 'Google Chrome', 'auto': auto,
             'timeline': timeline if timeline is not None else [[active, NOW - 7200]],
             'accounts': accounts if accounts is not None else {
                 A: {'id': 'acct-a', 'profile': 'P1', 'profile_name': 'Work', 'link': 'proved', 'opens': 'P1'},
                 B: {'id': 'acct-b', 'profile': 'P2', 'profile_name': 'Home', 'link': 'proved', 'opens': 'P2'},
                 G: {'id': 'acct-g', 'profile': None, 'profile_name': None, 'link': 'none', 'opens': None}},
             'artifacts': artifacts_ if artifacts_ is not None else {
                 ID1: {'o': A, 'p': 'P1', 's': 'seen'}, ID2: {'o': B, 'p': 'P2', 's': 'published'}},
             'fallback': fallback, 'chrome': chrome or {'profiles': {}, 'host': True}}
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, 'owners.json'), 'w') as f:
            json.dump(m, f)
        return m

    def hook(self, event, env=None, now=NOW, ask=None):
        out = io.StringIO()
        code = session_hooks.hook(stdin=io.BytesIO(json.dumps(event).encode()), env=env or {}, now=now, out=out,
                                  ask=ask)
        self.assertEqual(code, 0)
        text = out.getvalue().strip()
        if not text:
            return None
        got = json.loads(text)
        self.assertEqual(sorted(got), ['hookSpecificOutput'])
        hso = got['hookSpecificOutput']
        self.assertEqual(hso['hookEventName'], event['hook_event_name'])
        self.assertEqual(sorted(hso), ['additionalContext', 'hookEventName'])   # never a decision of any kind
        said = hso['additionalContext']
        for word in FORBIDDEN:
            self.assertNotIn(word, said.lower())
        return said

    def event(self, name, sid='s-1', **more):
        return dict({'hook_event_name': name, 'session_id': sid, 'transcript_path': '', 'cwd': self.home}, **more)


class SessionStartSays(HookSandbox):
    def test_the_accounts_move_items_are_not_deleted_and_the_exact_commands(self):
        self.owners(chrome={'profiles': {'P1': {'extension': False, 'account': A}}, 'host': True})
        said = self.hook(self.event('SessionStart', source='startup'))
        self.assertIn('now on %s' % A, said)
        self.assertIn('now on %s; 2 other accounts)' % A, said)            # counted, never named
        self.assertIn('not deleted', said)
        self.assertIn('do not run /login or /logout', said)
        for cmd in ('`switchboard owner <link>`', '`switchboard open <link>`', '`switchboard run <owner> -- -p'):
            self.assertIn(cmd, said)
        self.assertIn('Claude in Chrome follows the account Claude Code is on: for %s it is the Google Chrome profile '
                      '"Work" (the Claude extension is not in that profile yet)' % A, said)
        with open(os.path.join(self.data, 'hooks', 's-1.json')) as f:
            rec = json.load(f)
        self.assertEqual(rec['account'], A)

    def test_after_a_compaction_it_is_said_again_and_a_resumed_session_hears_where_it_was(self):
        self.owners()
        self.hook(self.event('SessionStart', source='startup'))
        self.owners(active=B, timeline=[[A, NOW - 7200], [B, NOW - 60]])
        said = self.hook(self.event('SessionStart', source='resume'))
        self.assertIn('This chat was on %s before; what it made then belongs to %s.' % (A, A), said)
        self.assertIsNone(self.hook(self.event('UserPromptSubmit')))    # already told at its start

    def test_a_session_on_its_own_login_is_told_so_and_nothing_else_moves_it(self):
        self.owners()
        env = {'CLAUDE_CONFIG_DIR': os.path.join(self.tmp, 'own-profile')}
        said = self.hook(self.event('SessionStart', source='startup'), env=env)
        self.assertIn('runs on its own login', said)
        self.assertNotIn('now on', said)
        self.owners(active=B, timeline=[[A, NOW - 7200], [B, NOW - 60]])
        self.assertIsNone(self.hook(self.event('UserPromptSubmit'), env=env))
        said = self.hook(self.event('PostToolUseFailure', tool_name='Artifact', tool_input={'url': LINK % ID1},
                                    error='artifact not found'), env=env)
        self.assertIn('runs on its own login', said)
        self.assertIn('belongs to %s' % A, said)
        self.assertNotIn('Claude Code is on', said)
        chrome = {'profiles': {'P2': {'extension': True, 'account': B}}, 'host': True}
        self.owners(chrome=chrome)
        self.assertIsNone(self.hook(self.event('PreToolUse', tool_name='mcp__claude-in-chrome__navigate'), env=env,
                                    ask=lambda *a: self.fail('an own-login session never asks for a switch')))

    def test_nothing_without_the_page_s_map_but_still_the_commands(self):
        said = self.hook(self.event('SessionStart', source='startup'))
        self.assertIn('`switchboard owner <link>`', said)
        self.assertNotIn('now on', said)


class OncePerSwitch(HookSandbox):
    """A session across a switch hears it once, with both accounts and no sign-in."""

    def test_no_switch_no_words(self):
        self.owners()
        self.hook(self.event('SessionStart', source='startup'))
        for _ in range(3):
            self.assertIsNone(self.hook(self.event('UserPromptSubmit', prompt='go on')))

    def test_exactly_one_line_per_switch_naming_both_accounts(self):
        self.owners()
        self.hook(self.event('SessionStart', source='startup'))
        at = time.mktime(time.strptime('2027-01-15 15:02', '%Y-%m-%d %H:%M'))
        self.owners(active=B, timeline=[[A, at - 7200], [B, at]])
        said = self.hook(self.event('UserPromptSubmit'), now=at + 120)
        self.assertIn('Switchboard moved Claude Code from %s to %s at 3:02 PM' % (A, B), said)
        self.assertIn('Do not run /login', said)
        self.assertIn('belong to %s' % A, said)
        self.assertIn('`switchboard run %s -- -p' % A, said)
        self.assertLess(len(said), 700)
        self.assertIsNone(self.hook(self.event('UserPromptSubmit'), now=at + 180))
        self.owners(active=C, timeline=[[A, at - 7200], [B, at], [C, at + 600]],
                    accounts={C: {'opens': None}, B: {'opens': 'P2'}, A: {'opens': 'P1'}})
        said = self.hook(self.event('UserPromptSubmit'), now=at + 700)
        self.assertIn('from %s to %s' % (B, C), said)

    def test_a_session_that_began_before_the_hooks_is_noted_quietly_first(self):
        self.owners(active=B)
        self.assertIsNone(self.hook(self.event('UserPromptSubmit', sid='old')))
        self.assertIsNone(self.hook(self.event('UserPromptSubmit', sid='old')))

    def test_what_this_session_made_under_the_old_account_is_listed_with_its_watch(self):
        self.owners()
        transcript = os.path.join(self.tmp, 'chat.jsonl')
        before = NOW - 3000

        def line(t, text):
            return json.dumps({'type': 'user', 'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t)),
                               'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'x', 'content': text}]}})
        with open(transcript, 'w') as f:
            f.write(line(before, 'Published /tmp/plan.html at %s (version 1)\nLive subscription: arming in the '
                                 'background' % (LINK % ID1)) + '\n')
            f.write(line(before + 60, '{"verdict":"ok","created":{"frame":{"slug":"doc","url":"%s"}}}' % (LINK % ID3)) + '\n')
            f.write(line(NOW + 10, 'Published /tmp/new.html at %s' % (LINK % ID2)) + '\n')    # after the switch
        self.hook(self.event('SessionStart', source='startup', transcript_path=transcript))
        self.owners(active=B, timeline=[[A, NOW - 7200], [B, NOW]])
        said = self.hook(self.event('UserPromptSubmit', transcript_path=transcript), now=NOW + 30)
        self.assertIn('Made in this chat under %s: %s (artifact, watched: ask to watch it again as %s); %s (Claude Doc)'
                      % (A, LINK % ID1, A, LINK % ID3), said)             # (a chat is a "chat" in its words)
        self.assertNotIn(ID2, said)


class NotFoundAnswers(HookSandbox):
    def test_a_failed_artifact_read_names_its_owner_and_the_commands(self):
        self.owners(active=B)
        said = self.hook(self.event('PostToolUseFailure', tool_name='Artifact',
                                    tool_input={'action': 'read', 'url': LINK % ID1},
                                    error='artifact not found — it may have been deleted, or it has not been shared '
                                          'with you'))
        self.assertIn('belongs to %s, and Claude Code is on %s now' % (A, B), said)
        self.assertIn('It is not deleted', said)
        self.assertIn('`switchboard open %s`' % (LINK % ID1), said)
        self.assertIn('`switchboard run %s -- -p "<what to do with %s>"`' % (A, LINK % ID1), said)
        self.assertIn('(it opens in the Google Chrome profile "Work")', said)

    def test_a_claude_doc_from_another_organization(self):
        self.owners(active=B)
        said = self.hook(self.event('PostToolUse', tool_name='mcp__claude_ai_Claude_Docs__read',
                                    tool_input={'container': {'kind': 'project', 'id': ID1}},
                                    tool_response=[{'type': 'text', 'text': 'Refused: this artifact is in another '
                                                                            'organization (other_org).'}]))
        self.assertIn('this Claude Doc belongs to %s' % A, said)

    def test_the_owner_unknown_still_gets_the_way_to_find_it(self):
        self.owners(active=B)
        self.hook(self.event('SessionStart', source='startup'))
        said = self.hook(self.event('PostToolUseFailure', tool_name='ArtifactComments',
                                    tool_input={'url': LINK % ('u' * 20)},
                                    error='artifact or comment thread not found — it may have been deleted'))
        self.assertIn('That does not mean it was deleted', said)
        self.assertNotIn('most likely', said)           # (a hedge says it might have been)
        self.assertIn('`switchboard owner %s`' % (LINK % ('u' * 20)), said)

    def test_quiet_when_it_is_the_account_in_use_or_nothing_is_wrong(self):
        self.owners(active=A)
        self.assertIsNone(self.hook(self.event('PostToolUseFailure', tool_name='Artifact', tool_input={'url': LINK % ID1},
                                               error='artifact not found')))
        self.assertIsNone(self.hook(self.event('PostToolUse', tool_name='Artifact', tool_input={'url': LINK % ID2},
                                               tool_response='Published /tmp/a.html at %s (version 3)' % (LINK % ID2))))
        body = 'x' * 500 + ' the page said not found when we tested the error handling'
        self.assertIsNone(self.hook(self.event('PostToolUse', tool_name='mcp__claude_ai_Claude_Docs__read',
                                               tool_input={'container': {'id': ID2}},
                                               tool_response=[{'type': 'text', 'text': body}])))

    def test_the_usual_answer_never_reads_the_map(self):
        self.owners(active=B)
        with mock.patch.object(session_hooks, 'owners', side_effect=AssertionError('read')):
            self.assertIsNone(self.hook(self.event('PostToolUse', tool_name='Artifact', tool_input={},
                                                   tool_response='Published /tmp/a.html at %s' % (LINK % ID2))))


class ClaudeInChrome(HookSandbox):
    """The extension follows the account its profile is signed in as."""

    STORE = 'https://chromewebstore.google.com/detail/claude/fcoeoabgfenejglbffodgkkbkcdhcgfn'
    CHROME = {'profiles': {'P1': {'extension': False, 'account': A}, 'P2': {'extension': True, 'account': B}},
              'host': True, 'store': STORE}

    def chrome_event(self, sid='s-1'):
        return self.event('PreToolUse', sid=sid, tool_name='mcp__claude-in-chrome__navigate', tool_input={'url': 'x'})

    def test_a_need_switch_to_the_extensions_account_when_it_has_room(self):
        self.owners(chrome=self.CHROME)
        asked = []

        def ask(accounts, followers, why_of):
            asked.append((followers, why_of(followers[0])))
            return B, object()
        said = self.hook(self.chrome_event(), ask=ask)
        self.assertEqual(asked, [([B], 'Claude in Chrome follows %s (the extension is in its profile)' % B)])
        self.assertIn('Switchboard moved Claude Code to %s so Claude in Chrome can connect' % B, said)
        self.assertIn('"Home"', said)
        self.assertIsNone(self.hook(self.chrome_event(), ask=ask))         # said once in this session
        said2 = self.hook(self.chrome_event(sid='s-2'), now=NOW + 60, ask=ask)
        self.assertEqual(len(asked), 1)                                     # at most once in 10 minutes
        self.assertIn('moved Claude Code to %s' % B, said2)
        with open(os.path.join(self.data, 'hooks', 'chrome-need.json')) as f:
            need = json.load(f)
        self.assertEqual((need['to'], need['went']), (B, True))

    def test_no_room_tells_plainly_with_the_exact_command(self):
        self.owners(chrome=self.CHROME)
        said = self.hook(self.chrome_event(), ask=lambda *a: (None, None))
        self.assertIn('could not move Claude Code there just now', said)
        self.assertIn('`open -na "Google Chrome" --args --profile-directory="P1" %s`' % self.STORE, said)
        self.assertIn('Do not run /login or /logout', said)
        said = self.hook(self.chrome_event(sid='s-3'), now=NOW + 700, ask=lambda *a: (None, None))
        self.assertIsNotNone(said)                                          # asked again after 10 minutes

    def test_quiet_when_the_extension_is_in_the_profile_of_the_account_in_use(self):
        self.owners(active=B, chrome=self.CHROME)
        self.assertIsNone(self.hook(self.chrome_event(), ask=lambda *a: self.fail('no switch needed')))

    def test_claude_codes_way_into_chrome_missing_says_run_chrome(self):
        self.owners(chrome={'profiles': {'P1': {'extension': True, 'account': A}}, 'host': False})
        said = self.hook(self.chrome_event())
        self.assertIn('run /chrome in Claude Code once', said)

    def test_no_extension_anywhere_says_add_it_to_this_accounts_profile(self):
        self.owners(chrome={'profiles': {'P1': {'extension': False, 'account': A}}, 'host': True, 'store': self.STORE})
        said = self.hook(self.chrome_event(), ask=lambda *a: self.fail('nobody to switch to'))
        self.assertIn('--profile-directory="P1" %s' % self.STORE, said)
        self.owners(chrome={'profiles': {'P1': {'extension': False, 'account': A}}, 'host': True})   # an older map
        said = self.hook(self.chrome_event(sid='s-9'), ask=lambda *a: self.fail('nobody to switch to'))
        self.assertIn('Add to this profile, on Switchboard\'s page', said)
        self.assertNotIn('open -na', said)

    def test_the_engine_is_asked_through_the_contract(self):
        self.owners(chrome=self.CHROME)
        done = api.SwitchResult(ok=True, from_id='acct-a', to_id='acct-b', kind='need', source='hook', reason='x')
        # (the part that runs in the need switch's own process: tests/test_server_faults.py runs that process)
        with mock.patch.object(api, 'request_need_switch', return_value=done) as need:
            went = session_hooks._need_in_process({B: {'id': 'acct-b'}}, [B], lambda e: 'why ' + e)
        need.assert_called_once_with('acct-b', 'why ' + B)
        self.assertEqual(went[0], B)
        with mock.patch.object(api, 'request_need_switch', return_value=None):
            self.assertEqual(session_hooks._need_in_process({B: {'id': 'acct-b'}}, [B], str), (None, None))
        # the engine not built yet (or failing) is a plain "could not", never a crash
        self.assertEqual(session_hooks._need_in_process({B: {}}, [B], str), (None, None))


class StaysOutOfTheWay(HookSandbox):
    def test_turned_off_in_switchboard_every_hook_is_quiet(self):
        self.owners(active=B)
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            json.dump({'session_hooks': False}, f)
        for name in session_hooks.EVENTS:
            self.assertIsNone(self.hook(self.event(name, tool_name='Artifact', error='artifact not found',
                                                   tool_input={'url': LINK % ID1})))

    def test_garbage_in_nothing_out(self):
        for raw in (b'', b'nope', b'[1,2]', b'{"hook_event_name": 5}', b'\xff\xfe'):
            out = io.StringIO()
            self.assertEqual(session_hooks.hook(stdin=io.BytesIO(raw), env={}, out=out), 0)
            self.assertEqual(out.getvalue(), '')

    def test_the_check_input_answers(self):
        out = io.StringIO()
        session_hooks.hook(stdin=io.BytesIO(b'{"hook_event_name": "SwitchboardCheck"}'), env={}, out=out)
        self.assertEqual(out.getvalue().strip(), session_hooks.CHECKED)

    def test_old_session_records_are_pruned(self):
        d = os.path.join(self.data, 'hooks')
        os.makedirs(d)
        old = os.path.join(d, 'gone.json')
        with open(old, 'w') as f:
            f.write('{}')
        os.utime(old, (NOW - 8 * 86400, NOW - 8 * 86400))
        self.assertEqual(session_hooks.prune(d, NOW), 1)
        self.assertFalse(os.path.exists(old))


class TheRealCommand(HookSandbox):
    """The command exactly as Claude Code runs it: through a shell, this Python, the folder on the path."""

    def run_hook(self, event):
        cmd = session_hooks.command()
        env = dict(os.environ, SWITCHBOARD_HOOKS_NOW=str(NOW))
        began = time.monotonic()
        r = subprocess.run(['/bin/sh', '-c', cmd], input=json.dumps(event).encode(), capture_output=True, env=env,
                           timeout=60)
        return r, time.monotonic() - began

    def test_session_start_and_a_not_found_through_the_shell(self):
        self.owners(active=B)
        r, took = self.run_hook(self.event('SessionStart', source='startup'))
        self.assertEqual(r.returncode, 0)
        got = json.loads(r.stdout.decode())
        self.assertIn('now on %s' % B, got['hookSpecificOutput']['additionalContext'])
        r, _ = self.run_hook(self.event('PostToolUseFailure', tool_name='Artifact', tool_input={'url': LINK % ID1},
                                        error='artifact not found'))
        self.assertIn('belongs to %s' % A, json.loads(r.stdout.decode())['hookSpecificOutput']['additionalContext'])
        r, _ = self.run_hook(self.event('UserPromptSubmit'))
        self.assertEqual((r.returncode, r.stdout), (0, b''))

    def test_a_broken_python_still_exits_zero(self):
        cmd = session_hooks.command(python='/nowhere/python3')
        r = subprocess.run(['/bin/sh', '-c', cmd], input=b'{}', capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 0)


if __name__ == '__main__':
    unittest.main()
