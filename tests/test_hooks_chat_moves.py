import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""A chat that lives through a move knows, exactly, which account it is on and which it was on.

The hooks read the chat's own file (switchboard/chatfile.py: its session-owner line and a credential_org line each time
its login changes), carried on from where the last look stopped (its own record, or the page's chats.json), and name
accounts by it: from and to, when the chat itself moved, what it made under each. When the chat's file says nothing,
or a change is still settling, Switchboard's own timeline speaks, and the account a chat started under is
never taken for the one it is on. One move is told once, whichever source noticed it first.

Chat files follow Claude Code 2.1.325's own shapes (tests/fakes_hooks.py); every id and name is made up."""
import json
import os
import time
import unittest
from unittest import mock

from fakes_links import A, B, C, LINK
from fakes_hooks import (ORG_A, ORG_B, ORG_C, ORG_X, CHAT, ChatFile, entry, org_line, owner_line, said,
                             tool_result, write_index)
from test_hooks_session import NOW, HookSandbox

from switchboard import chatfile, session_hooks

U1, U2 = LINK % ('u' * 22), LINK % ('w' * 22)


def when(t, now=NOW):
    return session_hooks._when(t, now)


class ChatCase(HookSandbox):
    """A chat file in the made-up Claude Code folder, chats.json with the accounts' organizations, and the hook fed as
    Claude Code feeds it (the chat's id and its file's path)."""

    def setUp(self):
        super().setUp()
        self.chat = ChatFile(os.path.join(self.home, '.claude', 'projects'))
        write_index(self.data)
        self.reads = []
        real = chatfile.read

        def counted(path, start=0, **kw):            # every look at the chat's file, from where and up to where
            r = real(path, start=start, **kw)
            self.reads.append((start, r.end))
            return r
        self.patch(chatfile, 'read', counted)

    def ev(self, name, **more):
        return self.event(name, sid=CHAT, transcript_path=self.chat.path, **more)

    def record(self):
        with open(os.path.join(self.data, 'hooks', CHAT + '.json')) as f:
            return json.load(f)

    def started_on_a(self, at=NOW - 7000):
        self.chat.add(said('hello', at - 200), owner_line(ORG_A), org_line(ORG_A, at - 199))
        self.owners(active=A, timeline=[[A, at - 2000]])
        self.hook(self.ev('SessionStart', source='startup'), now=at)


class FromAndToByTheChatsOwnRecord(ChatCase):
    def test_both_sources_make_one_notice_with_the_chats_own_time(self):
        self.started_on_a()
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 600]])
        self.chat.add(said('go on', NOW - 500), org_line(ORG_B, NOW - 500))     # its next request went as B
        got = self.hook(self.ev('UserPromptSubmit'))
        self.assertIn('Switchboard moved Claude Code from %s to %s at %s; this chat has been on %s since %s.'
                      % (A, B, when(NOW - 600), B, when(NOW - 500)), got)
        self.assertIn('Do not run /login and do not ask the person to log in: %s already has its login.' % B, got)
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit'), now=NOW + 60))          # told once
        self.assertEqual(self.record()['org'], ORG_B)

    def test_a_chat_behind_the_clock_hears_where_its_last_request_went_and_the_move_once(self):
        self.started_on_a()
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 60]])
        got = self.hook(self.ev('UserPromptSubmit'))
        self.assertIn('Switchboard moved Claude Code from %s to %s at %s; this chat\'s last request was still under '
                      '%s.' % (A, B, when(NOW - 60), A), got)
        self.chat.add(said('go on', NOW + 5), org_line(ORG_B, NOW + 5))          # its own file catches up
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit'), now=NOW + 200))       # the same move: not again
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit'), now=NOW + 400))

    def test_a_move_switchboard_did_not_make_is_told_by_the_chats_own_record(self):
        self.started_on_a()
        self.chat.add(said('more', NOW - 1100), org_line(ORG_C, NOW - 1000))     # another program moved it
        got = self.hook(self.ev('UserPromptSubmit'))
        self.assertIn('This chat has been on %s since %s by its own file (it was on %s before); Switchboard has Claude '
                      'Code on %s.' % (C, when(NOW - 1000), A, A), got)
        self.assertNotIn('Switchboard moved', got)
        self.assertNotIn('moved from', got)               # the time is the chat's first request on C, not a move's
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit'), now=NOW + 60))

    def test_when_both_moved_the_chats_time_is_said_as_the_chats_and_switchboards_as_its_move(self):
        self.started_on_a()
        self.chat.add(said('more', NOW - 1100), org_line(ORG_C, NOW - 1000))     # another program moved it
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 600]])
        got = self.hook(self.ev('UserPromptSubmit'))
        self.assertIn('This chat has been on %s since %s by its own file (it was on %s before), and Switchboard moved '
                      'Claude Code to %s at %s.' % (C, when(NOW - 1000), A, B, when(NOW - 600)), got)
        self.assertNotIn('This chat moved', got)

    def test_what_a_move_costs_the_chat_in_one_sentence(self):
        self.started_on_a()
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 60]])
        got = self.hook(self.ev('UserPromptSubmit'))
        self.assertIn("The model's private reasoning from before the move stays with %s and may be missing from here "
                      "on; everything said and done in this chat is still here." % A, got)
        self.assertLess(len(got), 1000)

    def test_what_the_chat_made_is_said_under_the_account_it_was_on_at_the_time(self):
        self.started_on_a(at=NOW - 7000)
        self.chat.add(tool_result('Published /tmp/a.html at %s (version 1)' % U1, NOW - 5000))
        self.owners(active=C, timeline=[[A, NOW - 9000], [C, NOW - 4100]])
        self.chat.add(org_line(ORG_C, NOW - 4000))
        first = self.hook(self.ev('UserPromptSubmit'), now=NOW - 3900)
        self.assertIn('Made in this chat under %s: %s (artifact).' % (A, U1), first)
        self.chat.add(tool_result('Published /tmp/b.html at %s (version 1)' % U2, NOW - 3000))
        self.owners(active=B, timeline=[[A, NOW - 9000], [C, NOW - 4100], [B, NOW - 100]])
        got = self.hook(self.ev('UserPromptSubmit'))
        self.assertIn('Switchboard moved Claude Code from %s to %s at %s; this chat\'s last request was still under '
                      '%s.' % (C, B, when(NOW - 100), C), got)
        self.assertIn('Made in this chat under %s: %s (artifact). Made in this chat under %s: %s (artifact).'
                      % (A, U1, C, U2), got)


class AResumedChatHearsWhereItBegan(ChatCase):
    def test_started_under_and_last_request_exactly(self):
        self.chat.add(owner_line(ORG_A), org_line(ORG_A, NOW - 9000), said('x', NOW - 8000),
                      org_line(ORG_C, NOW - 5000))
        self.owners(active=B, timeline=[[B, NOW - 3000]])
        got = self.hook(self.ev('SessionStart', source='resume'))
        self.assertIn('This chat started under %s, and its last request was under %s; what it made under each of %s, '
                      '%s belongs to that account.' % (A, C, A, C), got)
        self.assertNotIn('This chat was on', got)

    def test_a_chat_that_started_elsewhere_and_came_back(self):
        self.chat.add(owner_line(ORG_A), org_line(ORG_A, NOW - 9000), org_line(ORG_C, NOW - 5000),
                      org_line(ORG_B, NOW - 3000))
        self.owners(active=B, timeline=[[B, NOW - 3000]])
        got = self.hook(self.ev('SessionStart', source='resume'))
        self.assertIn('This chat started under %s, and is on %s now; what it made under each of %s, %s belongs to '
                      'that account.' % (A, B, A, C), got)

    def test_the_pointer_to_every_accounts_chats(self):
        self.owners(active=A)
        got = self.hook(self.ev('SessionStart', source='startup'))
        self.assertIn("`switchboard chats` shows what every account's chats are doing.", got)
        self.assertLess(len(got), session_hooks.CAP)
        own = self.hook(self.ev('SessionStart', source='startup'),
                        env={'CLAUDE_CONFIG_DIR': os.path.join(self.tmp, 'own-login')})
        self.assertIn('runs on its own login', own)                     # a chat on its own login: as it was
        self.assertNotIn('switchboard chats', own)


class NotKnownFallsBack(ChatCase):
    def test_a_long_chat_with_no_account_line_in_its_tail_is_not_known_and_never_its_starting_account(self):
        self.patch(session_hooks, 'TAIL', 64 << 10)
        self.chat.add(owner_line(ORG_A), org_line(ORG_A, NOW - 90000),
                      *[said('line %d %s' % (i, 'x' * 900), NOW - 80000 + i) for i in range(200)])
        self.owners(active=B, timeline=[[A, NOW - 90000], [B, NOW - 50000]])
        got = self.hook(self.ev('SessionStart', source='resume'))
        size = self.chat.size()
        self.assertEqual(self.reads, [(size - (64 << 10), size)])          # a bounded first look: the tail only
        for words in ('started under', 'last request', 'is on %s now' % A):
            self.assertNotIn(words, got)
        self.owners(active=C, timeline=[[A, NOW - 90000], [B, NOW - 50000], [C, NOW - 60]])
        got = self.hook(self.ev('UserPromptSubmit'))
        self.assertIn('Switchboard moved Claude Code from %s to %s at %s.' % (B, C, when(NOW - 60)), got)
        self.assertNotIn('last request', got)
        self.assertNotIn(A, got)

    def test_with_no_names_for_the_accounts_switchboards_own_timeline_speaks(self):
        os.unlink(os.path.join(self.data, 'chats.json'))
        self.started_on_a()
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 60]])
        got = self.hook(self.ev('UserPromptSubmit'))
        self.assertIn('Switchboard moved Claude Code from %s to %s at %s.' % (A, B, when(NOW - 60)), got)
        self.assertNotIn('last request', got)

    def test_an_organization_switchboard_does_not_keep_is_not_named(self):
        self.started_on_a()
        self.chat.add(org_line(ORG_X, NOW - 1000))
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit')))
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 60]])
        got = self.hook(self.ev('UserPromptSubmit'), now=NOW + 10)
        self.assertIn('Switchboard moved Claude Code from %s to %s at %s.' % (A, B, when(NOW - 60, NOW + 10)), got)
        self.assertNotIn(ORG_X, got)
        self.assertNotIn('last request was still under', got)


class TheCarriedRead(ChatCase):
    def test_only_the_new_bytes_are_read(self):
        self.started_on_a()
        first_end = self.chat.size()
        self.assertEqual(self.reads, [(0, first_end)])
        self.chat.add(*[said('more %d %s' % (i, 'y' * 2000), NOW - 3000 + i) for i in range(100)])
        self.chat.scribble_head(first_end - 1)          # its head is now junk that names another organization
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 60]])
        got = self.hook(self.ev('UserPromptSubmit'))
        self.assertEqual(self.reads[1], (first_end, self.chat.size()))   # on from where it stopped, to the end
        self.assertIn("this chat's last request was still under %s" % A, got)
        self.assertNotIn(ORG_X, json.dumps(self.record()))

    def test_the_first_look_carries_on_from_the_pages_look(self):
        self.chat.add(owner_line(ORG_A), org_line(ORG_A, NOW - 9000),
                      *[said('old %d %s' % (i, 'z' * 900), NOW - 8000 + i) for i in range(50)])
        saved = self.chat.size()
        self.chat.scribble_head(saved - 1)              # what the page read already: junk now, never read again
        self.chat.add(said('new', NOW - 1100), org_line(ORG_C, NOW - 1000))
        write_index(self.data, chats={CHAT: entry(self.chat.path, saved, owner=ORG_A, steps=[(NOW - 9000, ORG_A)])})
        self.owners(active=A, timeline=[[A, NOW - 9000]])
        got = self.hook(self.ev('SessionStart', source='resume'))
        self.assertEqual(self.reads, [(saved, self.chat.size())])
        self.assertIn("This chat's last request was under %s; what it made under %s belongs to %s." % (C, C, C), got)
        self.assertEqual([s[1] for s in self.record()['cf']['steps']], [ORG_A, ORG_C])

    def test_a_saved_look_for_another_file_or_a_cut_file_is_not_carried_on(self):
        self.chat.add(owner_line(ORG_A), org_line(ORG_A, NOW - 9000))
        size = self.chat.size()
        write_index(self.data, chats={CHAT: entry('/elsewhere/other.jsonl', 10, steps=[(NOW - 9000, ORG_C)])})
        self.owners(active=A)
        self.hook(self.ev('SessionStart', source='startup'))
        self.assertEqual(self.reads, [(0, size)])
        self.assertEqual([s[1] for s in self.record()['cf']['steps']], [ORG_A])

    def test_a_gap_past_the_carry_limit_starts_again_from_a_tail(self):
        self.patch(session_hooks, 'CARRY_MAX', 32 << 10)
        self.patch(session_hooks, 'TAIL', 16 << 10)
        self.started_on_a()
        self.chat.add(*[said('lots %d %s' % (i, 'q' * 900), NOW - 3000 + i) for i in range(60)])
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 60]])
        got = self.hook(self.ev('UserPromptSubmit'))
        size = self.chat.size()
        self.assertEqual(self.reads[-1], (size - (16 << 10), size))
        self.assertIn('Switchboard moved Claude Code from %s to %s at %s.' % (A, B, when(NOW - 60)), got)
        self.assertNotIn('last request', got)                       # the tail says nothing: not known

    def test_a_replaced_file_is_read_again_from_its_start(self):
        self.started_on_a()
        os.unlink(self.chat.path)
        self.chat.add(owner_line(ORG_C), org_line(ORG_C, NOW - 100))
        self.hook(self.ev('UserPromptSubmit'))
        self.assertEqual(self.reads[-1], (0, self.chat.size()))
        self.assertEqual([s[1] for s in self.record()['cf']['steps']], [ORG_C])

    def test_a_look_with_nothing_new_is_cheap(self):
        self.started_on_a()
        self.chat.add(*[said('more %d %s' % (i, 'y' * 4000), NOW - 3000 + i) for i in range(1000)])   # 4 MB
        began = time.process_time()
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit')))
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit'), now=NOW + 5))
        self.assertLess(time.process_time() - began, 0.25)
        self.assertEqual(self.reads[-1][0], self.reads[-1][1])          # the second look had nothing new to read


class AFlutterIsNotAMove(ChatCase):
    def test_back_and_forth_within_seconds_says_nothing(self):
        self.started_on_a()
        self.chat.add(org_line(ORG_B, NOW - 1000), org_line(ORG_A, NOW - 980), org_line(ORG_C, NOW - 970),
                      org_line(ORG_A, NOW - 950))
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit')))

    def test_a_change_still_settling_waits_and_is_then_told_once(self):
        self.started_on_a()
        self.chat.add(org_line(ORG_C, NOW - 10))
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit')))          # it may still fold back
        got = self.hook(self.ev('UserPromptSubmit'), now=NOW + 120)
        self.assertIn('This chat has been on %s since %s by its own file (it was on %s before)'
                      % (C, when(NOW - 10, NOW + 120), A), got)
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit'), now=NOW + 200))

    def test_a_fresh_change_to_the_account_switchboard_moved_to_needs_no_wait(self):
        self.started_on_a()
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 40]])
        self.chat.add(org_line(ORG_B, NOW - 20))
        got = self.hook(self.ev('UserPromptSubmit'))
        self.assertIn('this chat has been on %s since %s.' % (B, when(NOW - 20)), got)


class ThroughTheRealCommand(ChatCase):
    """As Claude Code runs it: through a shell, this Python, isolated, the hooks' folder on the path (chatfile.py is
    found there too)."""

    def run_hook(self, event):
        import subprocess
        env = dict(os.environ, SWITCHBOARD_HOOKS_NOW=str(NOW))
        r = subprocess.run(['/bin/sh', '-c', session_hooks.command()], input=json.dumps(event).encode(),
                           capture_output=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0)
        return json.loads(r.stdout.decode())['hookSpecificOutput']['additionalContext'] if r.stdout.strip() else None

    def test_a_chat_behind_the_clock_through_the_shell(self):
        self.chat.add(owner_line(ORG_A), org_line(ORG_A, NOW - 7000))
        self.owners(active=A, timeline=[[A, NOW - 9000]])
        self.assertIn(CHATS_WORDS, self.run_hook(self.ev('SessionStart', source='startup')))
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 60]])
        got = self.run_hook(self.ev('UserPromptSubmit'))
        self.assertIn("Switchboard moved Claude Code from %s to %s at %s; this chat's last request was still under %s."
                      % (A, B, when(NOW - 60), A), got)


CHATS_WORDS = "`switchboard chats` shows what every account's chats are doing."


class StaysAsItWas(ChatCase):
    def test_helper_agents_hear_no_move(self):
        self.started_on_a()
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 60]])
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit', agent_id='agent-1', agent_type='Explore')))
        self.assertIsNone(self.hook(self.ev('SessionStart', source='startup', agent_id='agent-1')))
        self.assertIsNotNone(self.hook(self.ev('UserPromptSubmit')))         # the chat itself still does

    def test_a_chat_on_its_own_login_reads_no_chat_file(self):
        self.chat.add(owner_line(ORG_A), org_line(ORG_A, NOW - 9000))
        self.owners(active=B)
        env = {'CLAUDE_CONFIG_DIR': os.path.join(self.tmp, 'own-login')}
        self.hook(self.ev('SessionStart', source='startup'), env=env)
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit'), env=env))
        self.assertEqual(self.reads, [])

    def test_a_chat_with_no_file_given_is_told_from_the_timeline(self):
        self.owners()
        self.hook(self.event('SessionStart', sid=CHAT, source='startup'))
        self.owners(active=B, timeline=[[A, NOW - 7200], [B, NOW - 60]])
        got = self.hook(self.event('UserPromptSubmit', sid=CHAT))
        self.assertIn('Switchboard moved Claude Code from %s to %s at %s.' % (A, B, when(NOW - 60)), got)
        self.assertEqual(self.reads, [])

    def test_the_words_say_chat_never_session(self):
        self.started_on_a()
        self.chat.add(tool_result('Published /tmp/a.html at %s (version 1)' % U1, NOW - 5000))
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 60]])
        got = self.hook(self.ev('UserPromptSubmit'))
        start = self.hook(self.ev('SessionStart', source='resume'), now=NOW + 10)
        for text in (got, start):
            self.assertNotIn('session', text.lower())


if __name__ == '__main__':
    unittest.main()


class AChatOnItsOwnLogin(ChatCase):
    """A chat a `switchboard run` or `resume` opened on a login of its own (CLAUDE_CONFIG_DIR elsewhere): its record
    says so from its first hook, where the chats view looks (chats.own_logins), so it is shown as on its own login
    and never as "its next turn goes to" the account Claude Code is on."""

    def test_its_record_says_so_from_its_first_hook_and_no_longer_once_it_follows_claude_codes_login(self):
        from switchboard import chats
        own = {'CLAUDE_CONFIG_DIR': os.path.join(self.tmp, 'run-folder')}
        self.chat.add(said('hello', NOW - 300), owner_line(ORG_A), org_line(ORG_A, NOW - 299))
        self.owners(active=B, timeline=[[B, NOW - 9000]])
        self.hook(self.ev('SessionStart', source='resume'), env=own)
        self.assertEqual((self.record().get('own'), self.record().get('session_id')), (True, CHAT))
        self.assertEqual(chats.own_logins(), {CHAT: True})
        os.unlink(os.path.join(self.data, 'hooks', CHAT + '.json'))
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit'), env=own))    # its start was not heard: its prompt
        self.assertIs(self.record().get('own'), True)
        self.assertEqual(self.reads, [])                                      # still reads no chat file
        self.hook(self.ev('SessionStart', source='resume'), now=NOW + 60)    # opened again where it follows the login
        self.assertNotIn('own', self.record())
        self.assertEqual(chats.own_logins(), {})


class OneWriterOfARecordAtATime(ChatCase):
    """Two hooks of one chat at once (parallel tool calls, a prompt while a helper's connector answers) each read,
    change and write hooks/<chat>.json. A connector's answer note never undoes what a prompt's hook wrote, so a move
    is never told twice; the note itself is the write that may be skipped when it would have to wait."""

    def test_an_answer_noted_while_a_prompt_is_told_of_a_move_never_undoes_the_telling(self):
        self.started_on_a()
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 600]])
        self.chat.add(said('go on', NOW - 500), org_line(ORG_B, NOW - 500))
        p = mock.patch.object(session_hooks, 'RECORD_WAIT', 0.05, create=True)    # short, for the test
        p.start()
        self.addCleanup(p.stop)
        real, said_then = session_hooks._read, []

        def racing(path):                       # the note's read; the prompt's hook runs before the note writes
            rec = real(path)
            if not said_then and path.endswith(CHAT + '.json'):
                said_then.append(None)
                said_then[0] = self.hook(self.ev('UserPromptSubmit'))
            return rec
        with mock.patch.object(session_hooks, '_read', racing):
            session_hooks._note_answer(dict(self.ev('PostToolUse'), tool_name='mcp__claude_ai_Linear__list_issues'),
                                       {}, NOW)
        later = [self.hook(self.ev('UserPromptSubmit'), now=NOW + t) for t in (60, 120, 180)]
        told = [x for x in said_then + later if x and 'Switchboard moved Claude Code from' in x]
        self.assertEqual(len(told), 1, told)
        self.assertIn('Linear', self.record().get('ok') or {})

    def test_an_answer_note_skips_rather_than_wait_long_for_the_lock(self):
        self.started_on_a()
        before = self.record()
        import fcntl
        fd = os.open(os.path.join(self.data, 'hooks', '.records.lock'), os.O_WRONLY | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)                                        # another hook is writing a record
        began = time.monotonic()
        session_hooks._note_answer(dict(self.ev('PostToolUse'), tool_name='mcp__claude_ai_Linear__list_issues'), {},
                                   NOW)
        self.assertLess(time.monotonic() - began, 1.0)
        self.assertEqual(self.record(), before)                               # skipped, nothing overwritten
        self.assertIsNone(self.hook(self.ev('UserPromptSubmit')))             # a prompt's hook says nothing for now
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.owners(active=B, timeline=[[A, NOW - 9000], [B, NOW - 600]])
        self.assertIn('Switchboard moved Claude Code from', self.hook(self.ev('UserPromptSubmit'), now=NOW + 5))
