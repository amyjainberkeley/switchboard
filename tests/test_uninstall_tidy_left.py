import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The uninstaller's tidy counts a file as left only when Switchboard's own record says it changed that file. A file
# Switchboard never changed that can't be read, isn't JSON, or is a folder or a link holds nothing of Switchboard's, so
# its step has nothing to take out. A record that can't be read counts as a change.
#
# Every file is a throwaway one under a temp HOME, data folder and Claude Code folder; nothing runs claude, and no
# hook command reaches a real notification.
import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, keep, models, notify_hooks as NH, server, statusline as SL
from switchboard.packs import codemap, common as C
import test_notify_hooks  # its throwaway Claude Code folder and managed-settings folder
import test_settings_edits  # its Tidy: the server with a fake engine, a throwaway home and managed folder

STEPS = ('Notification hooks', 'House rules', 'Code map', 'Models setting', 'claude-mem settings')
SKILL = b'---\nname: graphify\ndescription: "made-up copy for tests"\n---\n\n# /graphify\n'


# ---------------------------------------------------------------- no "left" for a file Switchboard never changed

class Fresh(test_settings_edits.Tidy):
    """A fresh install: nothing was ever turned on, so Switchboard has no record of changing any of Claude Code's
    files. Claude Code's own files are there, as a person has them."""

    def setUp(self):
        super().setUp()
        self.patch(server, 'MODELS_KW', {'environ': {}, 'managed_dir': self.managed,
                                         'versions': [{'where': '/x/claude', 'version': '2.1.309', 'primary': True}]})
        self.md = os.path.join(self.claude, 'CLAUDE.md')
        self.put(b'# mine\n', self.md)
        self.put(b'{\n  "model": "opus"\n}\n')

    def unreadable(self, *paths):
        for p in paths:
            os.chmod(p, 0)
            self.addCleanup(os.chmod, p, 0o644)

    def folder(self, path):
        os.unlink(path)
        os.mkdir(path)

    def linked(self, path, data=None, to=None):
        """path becomes a link: to a file in a dotfiles folder holding data, or to `to` (which need not be there)."""
        if to is None:
            os.makedirs(os.path.join(self.tmp, 'dotfiles'), exist_ok=True)
            to = os.path.join(self.tmp, 'dotfiles', os.path.basename(path))
            with open(to, 'wb') as f:
                f.write(data)
        os.unlink(path)
        os.symlink(to, path)

    def nothing_left(self):
        """Tidy exits 0, and no step names a file as left. One check per step, each as its own subtest."""
        rc, said = self.tidy()
        for step in STEPS:
            with self.subTest(step=step):
                self.assertNotIn(step + ': left', said)
        self.assertEqual(rc, 0, said)
        return said


class Conditions(object):
    """Claude Code's two files in each way Switchboard can't read or edit them. Nothing of Switchboard's is in them."""

    def test_settings_json_that_is_not_json(self):
        self.put(b'not json{')
        self.nothing_left()
        self.assertEqual(self.get(), b'not json{')

    def test_claude_md_that_is_a_folder(self):
        self.folder(self.md)
        self.nothing_left()
        self.assertTrue(os.path.isdir(self.md))

    def test_both_files_that_cannot_be_read(self):
        self.unreadable(self.md, self.path)
        self.nothing_left()

    def test_claude_md_that_is_a_link_into_a_dotfiles_folder(self):
        self.linked(self.md, b'# mine, kept in my dotfiles\n')
        self.nothing_left()
        self.assertTrue(os.path.islink(self.md))

    def test_settings_json_that_is_a_folder(self):
        self.folder(self.path)
        self.nothing_left()

    def test_settings_json_that_is_a_link_to_nothing(self):
        self.linked(self.path, to=os.path.join(self.tmp, 'gone.json'))
        self.nothing_left()
        self.assertTrue(os.path.islink(self.path))


class NothingWasEverTurnedOn(Conditions, Fresh):
    """No record files at all."""

    def test_with_the_files_as_they_are(self):                  # a guard
        self.assertEqual(self.nothing_left(), '')


class EverythingTurnedOnAndOffAgain(Conditions, Fresh):
    """Each switch on, then off again: the record files are there, and name none of Claude Code's files."""

    def setUp(self):
        super().setUp()
        self.person_installed_claude_mem()
        os.makedirs(os.path.join(self.claude, 'skills', 'graphify'))
        self.put(SKILL, os.path.join(self.claude, 'skills', 'graphify', 'SKILL.md'))
        for on in (True, False):
            server.act('/api/notify', {'on': on})
            server.act('/api/statusline', {'on': on})
            server.act('/api/models', {'enabled': on})
            self.act({'pack': 'house', 'on': on})
            self.act({'pack': 'claudemem', 'on': on})
            if on:                      # the code map's switch would offer codegraph first: its own plan, then apply
                codemap.apply(codemap.plan(True, path=''))
            else:
                codemap.undo(path='')
        for path in (NH.record_path(), SL.record_path(), models.default_state_path(), C.default_state_path()):
            self.assertTrue(os.path.exists(path), path)
        self.assertEqual(self.get(self.md), b'# mine\n')


# ---------------------------------------------------------------- guards: what the record names stays left

class TrueLeftsStay(Fresh):
    """A change Switchboard's record names, in a file that can't be read or understood now, is still left (each a
    guard, but the status line's). A record that can't be read counts as a change."""

    def left(self, step, they=False):
        rc, said = self.tidy()
        self.assertEqual(rc, 1, said)
        self.assertIn('%s: left as %s (' % (step, 'they are' if they else 'it is'), said)
        return said

    def test_notification_hooks_put_in_then_settings_json_not_json(self):
        server.act('/api/notify', {'on': True})
        self.put(self.get() + b'oops')
        self.left('Notification hooks', they=True)

    def test_notification_hooks_put_in_then_settings_json_unreadable(self):
        server.act('/api/notify', {'on': True})
        self.unreadable(self.path)
        self.left('Notification hooks', they=True)

    def test_house_rules_put_in_then_claude_md_a_folder(self):
        self.act({'pack': 'house', 'on': True})
        os.unlink(self.md)
        os.mkdir(self.md)
        self.left('House rules')

    def test_code_map_put_in_then_claude_md_unreadable(self):
        os.makedirs(os.path.join(self.claude, 'skills', 'graphify'))
        self.put(SKILL, os.path.join(self.claude, 'skills', 'graphify', 'SKILL.md'))
        codemap.apply(codemap.plan(True, path=''))
        self.unreadable(self.md)
        self.left('Code map')

    def test_models_setting_put_in_then_settings_json_unreadable(self):
        server.act('/api/models', {'enabled': True})
        self.unreadable(self.path)
        self.left('Models setting')

    def test_claude_mems_settings_changed_then_settings_json_unreadable(self):
        self.person_installed_claude_mem()
        self.act({'pack': 'claudemem', 'on': True})
        self.unreadable(self.path)
        self.left('claude-mem settings')

    def test_status_line_put_in_then_settings_json_unreadable(self):
        server.act('/api/statusline', {'on': True})
        self.unreadable(self.path)
        self.left('Status line')

    def records_that_cannot_be_read(self, *paths):
        for path in paths:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.put(b'{"installs": ', path)                    # cut short

    def test_a_notification_record_that_cannot_be_read_counts_as_a_change(self):
        self.records_that_cannot_be_read(NH.record_path())
        self.put(b'not json{')
        self.left('Notification hooks', they=True)

    def test_a_status_line_record_that_cannot_be_read_counts_as_a_change(self):     # a status line record counts too
        self.records_that_cannot_be_read(SL.record_path())
        self.put(b'not json{')
        self.left('Status line')

    def test_a_packs_record_that_cannot_be_read_counts_as_a_change(self):
        self.records_that_cannot_be_read(C.default_state_path())
        os.unlink(self.md)
        os.mkdir(self.md)
        said = self.left('House rules')
        self.assertIn('Code map: left as it is (', said)

    def test_a_models_record_that_cannot_be_read_counts_as_a_change(self):
        self.records_that_cannot_be_read(models.default_state_path())
        self.unreadable(self.path)
        self.left('Models setting')


# ---------------------------------------------------------------- the docs say when the program stays
# (the sentence is in docs/troubleshooting.md, under its own heading, where the README's Help sends a person whose
# uninstall stopped)

class TheDocsSayWhenTheProgramStays(unittest.TestCase):
    SAID = ("When something Switchboard changed in Claude Code can't be put back, the uninstaller names it, keeps the "
            'program so it can run again, and exits with an error. Fix that, then run it again.')

    def test_troubleshooting_says_so(self):
        with open(os.path.join(HERE, '..', 'docs', 'troubleshooting.md'), encoding='utf-8') as f:
            doc = f.read()
        heading = '## The uninstaller says something could not be put back'
        self.assertIn(self.SAID, doc[doc.index(heading):doc.index('## Still stuck')])

    def test_how_it_works_sends_you_there(self):
        with open(os.path.join(HERE, '..', 'docs', 'how-it-works.md'), encoding='utf-8') as f:
            doc = f.read()
        self.assertIn('(troubleshooting.md#the-uninstaller-says-something-could-not-be-put-back)',
                      doc[doc.index('## Update and remove'):doc.index('## Limits')])

    def test_and_the_uninstaller_does_what_it_says(self):          # a guard (install-check 14)
        with open(os.path.join(HERE, '..', 'uninstall.sh'), encoding='utf-8') as f:
            script = f.read()
        tidy = script[script.index('"$DATA/bin/switchboard" tidy'):script.index('for d in "$HOME/.local/bin"')]
        self.assertIn('Not everything could be put back (named above), so the program and ~/.switchboard were kept. '
                      'Fix that, then run this again', tidy)
        self.assertIn('exit 1', tidy)
        self.assertLess(script.index('exit 1', script.index('"$DATA/bin/switchboard" tidy')),
                        script.index('rm -rf "$DATA/app"'))      # it stops before the program goes


# ---------------------------------------------------------------- the setting is written in the same turn

class TwoSwitchesAtOnce(test_notify_hooks.Base):
    """The page's switch and `switchboard notify on|off` both go through server.act('/api/notify'). The person turns
    Notifications on, then off while the on is still writing Switchboard's own setting: the last choice holds for the
    hooks and for the setting."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        keep.reset()
        self.addCleanup(keep.reset)
        self.patch(server, 'NOTIFY_RUN', self.run_ok)
        self.write('{\n  "model": "opus"\n}\n')

    def test_on_then_off_leave_the_setting_and_the_hooks_agreeing(self):
        real, failed = server.set_setting, []
        on_writing, off_done = threading.Event(), threading.Event()

        def slow(key, value):                                   # the on's write of its setting, delayed
            if key == 'notify' and value is True:
                on_writing.set()
                off_done.wait(1.0)                              # the off ends in here, unless the on holds the turn
            return real(key, value)
        self.patch(server, 'set_setting', slow)

        def switch(on):
            try:
                server.act('/api/notify', {'on': on})
            except Exception as e:  # noqa: BLE001 - said by the test, not lost in its thread
                failed.append(e)
            if not on:
                off_done.set()
        first = threading.Thread(target=switch, args=(True,), daemon=True)
        first.start()
        self.assertTrue(on_writing.wait(30))                    # the on's hooks are in; its setting is being written
        second = threading.Thread(target=switch, args=(False,), daemon=True)
        second.start()
        first.join(30)
        second.join(30)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(failed, [])
        self.assertEqual(self.read().count(NH.MARK), 0)         # the off came last: the hooks are out,
        self.assertIs(core.settings().get('notify'), False)     # and Switchboard's own setting says off too


if __name__ == '__main__':
    unittest.main()
