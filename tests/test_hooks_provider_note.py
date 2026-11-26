import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""A note from `switchboard back` reaches the Claude chat it is for with the person's next message, once.

The session hooks' UserPromptSubmit reads <data>/hooks/notes/<session id>.json (written by the providers' bridge),
says its fixed words first, and removes it. A switch notice in the same prompt is built first and kept whole: the note
gets what is left of the hook's 2,400 characters. A helper never hears it; a chat on its own login does (a note is not
about a login move); a note a week old goes unsaid; a broken note file says nothing and stops nothing; a session id of
another shape reads nothing. The hook inputs are Claude Code's own UserPromptSubmit shape (tests/fakes_hooks.py,
2.1.309 and 2.1.325). Every address, id, path and time here is made up.
"""
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

from switchboard import session_hooks
from switchboard.providers import bridge, contract, words

A, B = 'ana@work.example', 'ana@home.example'
CHAT = '5e55105e-0000-4000-8000-000000000001'
OTHER = '5e55105e-0000-4000-8000-000000000002'
HOP = 'h-0a0b0c0d'
NOW = 1_800_000_000.0
NOTE = words.BACK_HOOK.format(hop=HOP, other_name='Codex', start='2:10 PM', end='3:40 PM',
                              path='/Users/maya/.switchboard-notes/t-0a0b0c0d/%s.md' % HOP)
FORBIDDEN = ('sign in', 'sign-in', 'may have been deleted', 'may be deleted', 'reinstall', 'please run /login')


class Hooks(unittest.TestCase):
    """A made-up home and data folder, owners.json as the page writes it, and the hook fed as Claude Code feeds it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='sb-note-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        os.makedirs(self.home)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude')})
        env.start()
        self.addCleanup(env.stop)
        self.owners(active=A, timeline=[[A, NOW - 7200]])

    def owners(self, active, timeline):
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, 'owners.json'), 'w') as f:
            json.dump({'v': 1, 'written_at': NOW, 'active': active, 'browser': 'Google Chrome', 'auto': True,
                       'timeline': timeline, 'artifacts': {}, 'fallback': None,
                       'accounts': {A: {'id': 'acct-a'}, B: {'id': 'acct-b'}},
                       'chrome': {'profiles': {}, 'host': True}}, f)

    def hook(self, name, sid=CHAT, env=None, now=NOW, **more):
        event = dict({'hook_event_name': name, 'session_id': sid, 'transcript_path': '', 'cwd': self.home,
                      'prompt': 'made-up prompt'}, **more)
        out = io.StringIO()
        code = session_hooks.hook(stdin=io.BytesIO(json.dumps(event).encode()), env=env or {}, now=now, out=out)
        self.assertEqual(code, 0)
        text = out.getvalue().strip()
        if not text:
            return None
        got = json.loads(text)
        self.assertEqual(sorted(got['hookSpecificOutput']), ['additionalContext', 'hookEventName'])
        self.assertEqual(got['hookSpecificOutput']['hookEventName'], name)
        said = got['hookSpecificOutput']['additionalContext']
        self.assertLessEqual(len(said), session_hooks.CAP)
        for word in FORBIDDEN:
            self.assertNotIn(word, said.lower())
        return said

    def put(self, text=NOTE, sid=CHAT, made_at=NOW - 60):
        reach = type('R', (), {'data_dir': self.data, 'wall': staticmethod(lambda: made_at)})()
        self.assertTrue(bridge.put_note(sid, text, '/Users/maya/.switchboard-notes/t-0a0b0c0d/%s.md' % HOP, HOP,
                                        reach=reach))
        return self.note_file(sid)

    def note_file(self, sid=CHAT):
        return os.path.join(self.data, contract.NOTE_DIR, sid + '.json')

    def moved_to_b(self):
        """The chat started on A (its record says so), then Switchboard moved Claude Code to B a minute ago."""
        self.owners(active=A, timeline=[[A, NOW - 7200]])
        self.hook('SessionStart', source='startup', now=NOW - 3600)
        self.owners(active=B, timeline=[[A, NOW - 7200], [B, NOW - 60]])


class SaidOnceFirstThenRemoved(Hooks):
    def test_the_next_prompt_says_the_note_and_it_is_gone(self):
        path = self.put()
        self.assertEqual(self.hook('UserPromptSubmit'), NOTE)
        self.assertFalse(os.path.exists(path))
        self.assertIsNone(self.hook('UserPromptSubmit', now=NOW + 30))        # said once

    def test_with_a_switch_notice_the_note_goes_first_and_the_notice_follows_whole(self):
        self.moved_to_b()
        notice_alone = self.hook('UserPromptSubmit', sid=OTHER)                  # what a chat with no note hears
        self.assertIsNone(notice_alone)                                          # (OTHER never started: no record)
        self.put()
        said = self.hook('UserPromptSubmit')
        self.assertTrue(said.startswith(NOTE + '\n\n'), said[:120])
        notice = said[len(NOTE) + 2:]
        self.assertTrue(notice.startswith('Switchboard moved Claude Code from %s to %s' % (A, B)), notice[:120])
        self.assertIn('Do not run /login and do not ask the person to log in: %s already has its login.' % B, notice)
        self.assertIsNone(self.hook('UserPromptSubmit', now=NOW + 30))        # neither is said twice

    def test_a_note_for_another_chat_is_not_said_here(self):
        other = self.put(sid=OTHER)
        self.assertIsNone(self.hook('UserPromptSubmit'))
        self.assertTrue(os.path.exists(other))
        self.assertEqual(self.hook('UserPromptSubmit', sid=OTHER), NOTE)

    def test_a_chats_start_does_not_say_it_the_next_prompt_does(self):
        path = self.put()
        said = self.hook('SessionStart', source='resume')
        self.assertNotIn(HOP, said or '')
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.hook('UserPromptSubmit'), NOTE)

    def test_the_words_said_are_the_notes_own_without_control_characters(self):
        self.put(text='Switchboard note %s:\x1b[2J one\nline\x07 end' % HOP)
        self.assertEqual(self.hook('UserPromptSubmit'), 'Switchboard note %s:[2J oneline end' % HOP)


class WhoHearsIt(Hooks):
    def test_a_helper_never_hears_it_and_it_waits_for_the_chat_itself(self):
        path = self.put()
        self.assertIsNone(self.hook('UserPromptSubmit', agent_id='agent-made-up', agent_type='general-purpose'))
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.hook('UserPromptSubmit'), NOTE)

    def test_a_chat_on_its_own_login_hears_it(self):
        env = {'CLAUDE_CONFIG_DIR': os.path.join(self.tmp, 'own-profile')}
        path = self.put()
        self.assertEqual(self.hook('UserPromptSubmit', env=env), NOTE)
        self.assertFalse(os.path.exists(path))

    def test_with_the_hooks_turned_off_nothing_is_said_and_the_note_stays(self):
        path = self.put()
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            json.dump({'session_hooks': False}, f)
        self.assertIsNone(self.hook('UserPromptSubmit'))
        self.assertTrue(os.path.exists(path))

    def test_while_another_hook_holds_the_chats_records_nothing_is_said_and_the_note_stays(self):
        path = self.put()
        fd = session_hooks._lock(session_hooks.RECORDS_LOCK)
        self.assertIsNotNone(fd)
        try:
            with mock.patch.object(session_hooks, 'RECORD_WAIT', 0.05):
                self.assertIsNone(self.hook('UserPromptSubmit'))
        finally:
            session_hooks._unlock(fd)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.hook('UserPromptSubmit'), NOTE)


class OldOrBrokenOrElsewhere(Hooks):
    def test_a_note_a_week_old_is_dropped_unsaid(self):
        path = self.put(made_at=NOW - session_hooks.NOTE_KEEP - 1)
        self.assertIsNone(self.hook('UserPromptSubmit'))
        self.assertFalse(os.path.exists(path))

    def test_a_note_dated_ahead_of_the_clock_is_dropped_unsaid(self):
        path = self.put(made_at=NOW + 3600)
        self.assertIsNone(self.hook('UserPromptSubmit'))
        self.assertFalse(os.path.exists(path))

    def test_prune_removes_notes_past_a_week_and_keeps_the_rest(self):
        old, fresh = self.put(sid=OTHER, made_at=NOW - session_hooks.NOTE_KEEP - 60), self.put(made_at=NOW - 60)
        folder = session_hooks.hooks_dir()
        self.assertEqual(session_hooks.prune(folder, NOW), 1)
        self.assertEqual((os.path.exists(old), os.path.exists(fresh)), (False, True))

    def test_a_broken_note_says_nothing_and_does_not_stop_the_switch_notice(self):
        folder = os.path.dirname(self.note_file())
        os.makedirs(folder, exist_ok=True)
        for raw in (b'{not json', b'[]', b'{"v": 2, "text": "x", "made_at": 1800000000}',
                    b'{"v": 1, "text": 42, "made_at": 1800000000}', b'{"v": 1, "text": "x", "made_at": "soon"}',
                    b'{"v": 1, "text": "   ", "made_at": 1800000000}', b'\xff\xfe',
                    b'{"v": 1, "text": "' + b'x' * (20 << 10) + b'", "made_at": 1800000000}'):
            with open(self.note_file(), 'wb') as f:
                f.write(raw)
            self.moved_to_b()
            said = self.hook('UserPromptSubmit')
            self.assertTrue(said and said.startswith('Switchboard moved Claude Code from %s to %s' % (A, B)), raw)
            self.assertNotIn('Switchboard note', said)

    def test_a_session_id_of_another_shape_reads_nothing(self):
        planted = os.path.join(self.data, 'hooks', 'x.json')         # where ../x would lead from the notes folder
        os.makedirs(os.path.dirname(planted), exist_ok=True)
        with open(planted, 'w') as f:
            json.dump({'v': 1, 'text': 'planted words', 'path': '/p', 'hop': HOP, 'made_at': NOW - 60}, f)
        for sid in ('../x', '../../hooks/x', '.x', '', None, 42, 'x/../../x'):
            said = self.hook('UserPromptSubmit', sid=sid)
            self.assertNotIn('planted words', said or '', sid)
        self.assertTrue(os.path.exists(planted))


class TheRoomItGets(Hooks):
    def test_a_long_note_and_a_switch_notice_every_sentence_of_the_notice_survives_whole(self):
        self.moved_to_b()
        long_note = ('Switchboard note %s: ' % HOP) + ' '.join('word%04d' % i for i in range(220))
        long_note = long_note[:2000]
        self.assertEqual(len(long_note), 2000)
        self.put(text=long_note)
        said = self.hook('UserPromptSubmit')
        self.assertLessEqual(len(said), session_hooks.CAP)
        head, sep, notice = said.partition('\n\n')
        self.assertTrue(head.startswith('Switchboard note %s: word0000' % HOP))
        self.assertTrue(head.endswith('…'))
        self.assertTrue(long_note.startswith(head[:-1]))
        self.assertRegex(head[:-1], r'word\d{4}$')                   # cut at a word, never inside one
        alone = self.notice_alone()
        for sentence in re.split(r'(?<=[.;:]) ', alone):
            self.assertIn(sentence, notice)
        self.assertIn('Do not run /login and do not ask the person to log in:', notice)
        self.assertEqual(notice, alone)

    def notice_alone(self):
        """The same switch notice said to a second chat that started on A and has no note."""
        self.owners(active=A, timeline=[[A, NOW - 7200]])
        self.hook('SessionStart', sid=OTHER, source='startup', now=NOW - 3600)
        self.owners(active=B, timeline=[[A, NOW - 7200], [B, NOW - 60]])
        return self.hook('UserPromptSubmit', sid=OTHER)

    def test_when_the_notice_leaves_too_little_room_the_note_waits_for_the_next_prompt(self):
        notice = 'n' * (session_hooks.CAP - session_hooks.NOTE_MIN)
        self.assertIsNone(session_hooks.with_note(NOTE, notice))
        self.assertEqual(session_hooks.with_note(NOTE, None), NOTE)
        short = 'n' * 100
        self.assertEqual(session_hooks.with_note(NOTE, short), NOTE + '\n\n' + short)

    def test_a_note_cut_never_ends_inside_a_word_and_says_it_was_cut(self):
        got = session_hooks.with_note('alpha beta gamma delta', 'n' * (session_hooks.CAP - 2 - 15))
        self.assertIsNone(got)                                       # 15 characters of room: under NOTE_MIN
        with mock.patch.object(session_hooks, 'NOTE_MIN', 5):
            got = session_hooks.with_note('alpha beta gamma delta', 'n' * (session_hooks.CAP - 2 - 15))
        self.assertEqual(got.split('\n\n')[0], 'alpha beta…')


class TiedToTheContract(unittest.TestCase):
    def test_the_four_repeated_names_match_the_providers_contract(self):
        self.assertIs(session_hooks.PROVIDER_NOTES, True)
        self.assertEqual(session_hooks.NOTE_DIR, contract.NOTE_DIR)
        self.assertEqual(session_hooks.NOTE_V, contract.NOTE_V)
        self.assertEqual(session_hooks.NOTE_TEXT_MAX, contract.NOTE_TEXT_MAX)
        self.assertEqual(session_hooks.NOTE_KEEP, contract.NOTE_KEEP)

    def test_the_hook_program_still_imports_nothing_of_the_providers_package(self):
        with open(session_hooks.__file__) as f:
            text = f.read()
        self.assertNotIn('providers', ''.join(line for line in text.splitlines(True)
                                              if line.lstrip().startswith(('import ', 'from '))))


if __name__ == '__main__':
    unittest.main()
