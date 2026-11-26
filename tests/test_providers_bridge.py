import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The bridge: the one way Switchboard's other assistants reach the session hooks and the account code's chat check.

On a Switchboard whose session hooks deliver a note (PROVIDER_NOTES) and are on, `back` leaves the note for the chat's
next message; anywhere else it says what to paste. The note file holds fixed words, the hop id, times and a path,
is the chat's own (its id must be a chat id), 0600 in a 0700 folder, and is cut to NOTE_TEXT_MAX. Every id, path and
time here is made up.
"""
import json
import os
import shutil
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock

import fakes_providers as fp

from switchboard import session_hooks
from switchboard.providers import bridge, contract

CHAT_ID = 'aaaaaaaa-0000-4000-8000-00000000000a'
HOP = 'h-0a0b0c0d'
NOW = 1_800_000_000.0


class Folders(unittest.TestCase):
    """A made-up home, Claude Code folder and data folder for each test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='sb-bridge-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        os.makedirs(self.home)
        os.makedirs(self.data, mode=0o700)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude')})
        env.start()
        self.addCleanup(env.stop)
        self.settings = os.path.join(self.home, '.claude', 'settings.json')

    def hooks_in(self):
        """Switchboard's session hooks put into the made-up Claude Code settings, as `switchboard hooks on` does
        (without running the hook command: plan, then apply)."""
        os.makedirs(os.path.dirname(self.settings), exist_ok=True)
        plan = session_hooks.install_plan(self.settings, command_line=session_hooks.command())
        session_hooks.apply(plan)
        self.assertIs(session_hooks.status(self.settings)['in'], True)

    def switchboard_settings(self, **values):
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            json.dump(values, f)


class CanDeliver(Folders):
    def test_on_this_tree_with_the_hooks_on_it_is_true(self):
        self.assertIs(session_hooks.PROVIDER_NOTES, True)
        self.hooks_in()
        self.assertIs(bridge.hooks_can_deliver(), True)

    def test_hooks_that_belong_to_another_copy_of_switchboard_is_false(self):
        # A command run from one copy of Switchboard (a try-out, a build's own folder) while Claude Code's settings
        # hold the hooks of another copy (the installed one): the note would be written where those hooks never
        # look, or read by hooks that predate it. The promise is not made; the line to paste is printed.
        other = os.path.join(self.tmp, 'another-copy', 'switchboard')
        os.makedirs(other)
        shutil.copy(session_hooks.__file__, os.path.join(other, 'session_hooks.py'))
        os.makedirs(os.path.dirname(self.settings), exist_ok=True)
        plan = session_hooks.install_plan(self.settings, command_line=session_hooks.command(folder=other))
        session_hooks.apply(plan)
        st = session_hooks.status(self.settings)
        self.assertIs(st['in'], True)
        self.assertTrue(st['works'])
        self.assertIs(session_hooks.hooks_run_from_here(self.settings), False)
        self.assertIs(bridge.hooks_can_deliver(), False)

    def test_the_hooks_of_this_copy_run_from_here(self):
        self.assertIsNone(session_hooks.hooks_run_from_here(self.settings))     # no settings file: cannot tell
        self.hooks_in()
        self.assertIs(session_hooks.hooks_run_from_here(self.settings), True)

    def test_hooks_not_in_claude_codes_settings_is_false(self):
        os.makedirs(os.path.dirname(self.settings))
        with open(self.settings, 'w') as f:
            f.write('{}\n')
        self.assertIs(bridge.hooks_can_deliver(), False)

    def test_hooks_turned_off_in_switchboard_is_false(self):
        self.hooks_in()
        self.switchboard_settings(session_hooks=False)
        self.assertIs(bridge.hooks_can_deliver(), False)

    def test_claude_code_running_no_hooks_at_all_is_false(self):
        self.hooks_in()
        with open(self.settings) as f:
            obj = json.load(f)
        obj['disableAllHooks'] = True
        with open(self.settings, 'w') as f:
            json.dump(obj, f)
        self.assertIs(bridge.hooks_can_deliver(), False)

    def test_a_settings_file_that_cannot_be_read_cannot_tell(self):
        os.makedirs(os.path.dirname(self.settings))
        with open(self.settings, 'w') as f:
            f.write('{not json')
        self.assertIsNone(bridge.hooks_can_deliver())

    def test_a_switchboard_whose_hooks_lack_the_constant_is_false(self):
        old = types.ModuleType('switchboard.session_hooks')       # the older line's hooks: no PROVIDER_NOTES
        old.hooks_on = lambda: True
        old.status = lambda path=None: {'in': True, 'works': True, 'current': True, 'off_by': None, 'path': ''}
        with mock.patch.dict(sys.modules, {'switchboard.session_hooks': old}):
            self.assertIs(bridge.hooks_can_deliver(), False)
            old.PROVIDER_NOTES = False
            self.assertIs(bridge.hooks_can_deliver(), False)
            old.PROVIDER_NOTES = True
            self.assertIs(bridge.hooks_can_deliver(), True)

    def test_hooks_that_cannot_be_imported_cannot_tell(self):
        with mock.patch.dict(sys.modules, {'switchboard.session_hooks': None}):
            self.assertIsNone(bridge.hooks_can_deliver())

    def test_hooks_whose_own_checks_fail_cannot_tell(self):
        broken = types.ModuleType('switchboard.session_hooks')
        broken.PROVIDER_NOTES = True
        broken.hooks_on = lambda: True

        def status(path=None):
            raise OSError('made-up')
        broken.status = status
        with mock.patch.dict(sys.modules, {'switchboard.session_hooks': broken}):
            self.assertIsNone(bridge.hooks_can_deliver())


class PutNote(Folders):
    def note_path(self, sid=CHAT_ID):
        return os.path.join(self.data, contract.NOTE_DIR, sid + '.json')

    def test_it_writes_the_chats_note_0600_in_a_0700_folder(self):
        reach, _ = fp.make_reach(self.tmp, wall=lambda: NOW)
        self.assertIs(bridge.put_note(CHAT_ID, 'Switchboard note %s: made-up words.' % HOP, '/made/up/n.md', HOP,
                                      reach=reach), True)
        path = self.note_path()
        with open(path) as f:
            got = json.load(f)
        self.assertEqual(got, {'v': contract.NOTE_V, 'text': 'Switchboard note %s: made-up words.' % HOP,
                               'path': '/made/up/n.md', 'hop': HOP, 'made_at': NOW})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        self.assertEqual(os.stat(path).st_mtime, NOW)              # dated by its own clock, as prune reads it
        self.assertEqual(os.listdir(os.path.dirname(path)), [CHAT_ID + '.json'])     # nothing half-written left

    def test_its_folder_is_made_0700_even_when_it_was_wider(self):
        folder = os.path.join(self.data, contract.NOTE_DIR)
        os.makedirs(folder, mode=0o755)
        os.chmod(folder, 0o755)
        self.assertTrue(bridge.put_note(CHAT_ID, 'words', '/n.md', HOP))
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)

    def test_the_text_is_cut_to_its_most(self):
        self.assertTrue(bridge.put_note(CHAT_ID, 'x' * (contract.NOTE_TEXT_MAX + 500), '/n.md', HOP))
        with open(self.note_path()) as f:
            self.assertEqual(len(json.load(f)['text']), contract.NOTE_TEXT_MAX)

    def test_a_session_id_of_another_shape_is_refused_and_nothing_is_written(self):
        for bad in ('../x', '..', 'x/../y', 'AAAAAAAA-0000-4000-8000-00000000000A', CHAT_ID + '.json', '',
                    CHAT_ID + '\n', None, 42):
            self.assertIs(bridge.put_note(bad, 'words', '/n.md', HOP), False, repr(bad))
        self.assertFalse(os.path.exists(os.path.join(self.data, contract.NOTE_DIR)))
        self.assertEqual(sorted(os.listdir(self.data)), [])

    def test_a_hop_or_text_of_another_shape_is_refused(self):
        for hop, text, path in (('h-xyz', 'w', '/n.md'), ('h-0a0b0c0d0', 'w', '/n.md'), (None, 'w', '/n.md'),
                                (HOP, None, '/n.md'), (HOP, 'w', None)):
            self.assertIs(bridge.put_note(CHAT_ID, text, path, hop), False, (hop, text, path))
        self.assertFalse(os.path.exists(self.note_path()))

    def test_a_folder_that_cannot_be_written_is_false_not_an_error(self):
        os.chmod(self.data, 0o500)
        self.addCleanup(os.chmod, self.data, 0o700)
        self.assertIs(bridge.put_note(CHAT_ID, 'words', '/n.md', HOP), False)

    def test_the_hooks_read_it_where_it_was_put(self):
        self.assertTrue(bridge.put_note(CHAT_ID, 'words', '/n.md', HOP))
        self.assertEqual(session_hooks._note_path({'session_id': CHAT_ID}), self.note_path())


class ChatInUse(Folders):
    def chat(self, written):
        project = os.path.join(self.home, '.claude', 'projects', '-made-up')
        os.makedirs(project, exist_ok=True)
        path = os.path.join(project, CHAT_ID + '.jsonl')
        with open(path, 'w') as f:
            f.write('{}\n')
        os.utime(path, (written, written))
        return path

    def test_it_asks_the_account_codes_own_check(self):
        import time
        self.assertIs(bridge.chat_in_use(CHAT_ID, self.chat(time.time() - 5)), True)
        self.assertIs(bridge.chat_in_use(CHAT_ID, self.chat(time.time() - 7 * 86400)), False)

    def test_without_that_check_it_cannot_tell(self):
        with mock.patch.dict(sys.modules, {'switchboard.resume': None}):
            self.assertIsNone(bridge.chat_in_use(CHAT_ID, '/made/up.jsonl'))
        bare = types.ModuleType('switchboard.resume')
        with mock.patch.dict(sys.modules, {'switchboard.resume': bare}):
            self.assertIsNone(bridge.chat_in_use(CHAT_ID, '/made/up.jsonl'))

    def test_a_check_that_fails_cannot_tell(self):
        broken = types.ModuleType('switchboard.resume')

        def in_use(cid, path, now=None):
            raise OSError('made-up')
        broken.in_use = in_use
        with mock.patch.dict(sys.modules, {'switchboard.resume': broken}):
            self.assertIsNone(bridge.chat_in_use(CHAT_ID, '/made/up.jsonl'))

    def test_an_id_of_another_shape_is_never_asked(self):
        asked = []
        stand_in = types.ModuleType('switchboard.resume')
        stand_in.in_use = lambda cid, path, now=None: asked.append(cid) or True
        with mock.patch.dict(sys.modules, {'switchboard.resume': stand_in}):
            self.assertIsNone(bridge.chat_in_use('../x', '/made/up.jsonl'))
        self.assertEqual(asked, [])


class OnlyTheBridge(unittest.TestCase):
    def test_no_other_module_of_the_package_imports_the_hooks_or_the_resume_code(self):
        folder = os.path.dirname(bridge.__file__)
        for name in sorted(os.listdir(folder)):
            if not name.endswith('.py') or name == 'bridge.py':
                continue
            with open(os.path.join(folder, name)) as f:
                imports = [line.strip() for line in f if line.lstrip().startswith(('import ', 'from '))]
            for line in imports:
                for word in ('session_hooks', 'resume', 'import_module'):
                    self.assertNotIn(word, line, (name, line))


if __name__ == '__main__':
    unittest.main()
