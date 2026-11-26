import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The page's start repairs Switchboard's own status line and hooks when the Python they name is gone.

The commands Switchboard writes into Claude Code's settings name the Python that turned them on. When that Python goes
away (the app that held it removed, an update that chose another), the status line fails on every run and the hooks go
silent. At the page's start each piece's repair() writes Switchboard's own commands again to run on the page's Python,
through the same plan and apply as ever: the undo stays exact. A command of the person's own, or one Switchboard's
record does not name exactly, is never touched; a refused edit changes nothing and says so in one line.

Every settings file here is a throwaway one; no command is run but the hooks' own check, on this test's Python.
  SWITCHBOARD_HOME="$(mktemp -d)/data" python3 -m unittest discover -s tests -p 'test_hooks_repair_gone_python.py'"""
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import notify_hooks as nh, server, session_hooks as sh, statusline as sl

GONE = '/nonexistent/Switchboard.app/Contents/MacOS/python'
HERS = '{\n  "model": "opus",\n  "permissions": {\n    "allow": ["Bash(ls:*)"]\n  }\n}\n'


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-repair-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        self.claude = os.path.join(self.home, '.claude')
        os.makedirs(self.claude)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': self.claude})
        env.start()
        self.addCleanup(env.stop)
        self.managed = os.path.join(self.tmp, 'managed')
        os.makedirs(self.managed)
        for target, name, value in ((nh, 'MANAGED', self.managed),
                                    (sl, 'precompile', lambda *a, **k: None)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.path = os.path.join(self.claude, 'settings.json')
        self.real = os.path.realpath(self.path)             # what the log names: the file really written
        self.assertTrue(sl.settings_path().startswith(self.tmp))
        with open(self.path, 'w') as f:
            f.write(HERS)

    def read(self):
        with open(self.path, 'rb') as f:
            return f.read()

    def put_in(self, mod, python):
        if mod is sl:
            plan = sl.install_plan(self.path, command_line=sl.command(python=python))
        else:
            plan = mod.install_plan(self.path, command_line=mod.command(python=python))
        self.assertTrue(mod.apply(plan)['changed'])

    def commands(self, mod):
        obj = json.loads(self.read().decode('utf-8'))
        if mod is sl:
            return {obj['statusLine']['command']}
        return set(c for e in mod.EVENTS for c in mod._found(obj)[e])

    def take_out(self, mod):
        self.assertTrue(mod.apply(mod.uninstall_plan(self.path))['changed'])


class Repaired(Base):
    def test_each_piece_whose_python_is_gone_runs_on_this_one_and_still_comes_out_exactly(self):
        for mod, name in ((sl, 'the status line'), (nh, 'the notification hooks'), (sh, 'the session hooks')):
            with self.subTest(piece=name):
                self.put_in(mod, GONE)
                self.assertEqual(self.commands(mod), {mod.command(python=GONE)})
                said = mod.repair(python=sys.executable)
                self.assertEqual(len(said), 1)
                verb = 'runs' if mod is sl else 'run'
                self.assertTrue(said[0].startswith('%s in %s %s on %s now' % (name, self.real, verb, sys.executable)),
                                said)
                self.assertEqual(self.commands(mod), {mod.command(python=sys.executable)})
                self.assertTrue(os.path.exists(self.path + '.switchboard-backup'))
                self.assertEqual(mod.repair(python=sys.executable), [])          # once is enough
                self.take_out(mod)
                self.assertEqual(self.read().decode('utf-8'), HERS)              # the undo is exact

    def test_all_three_together(self):
        for mod in (nh, sh, sl):
            self.put_in(mod, GONE)
        for mod in (sl, nh, sh):
            self.assertEqual(len(mod.repair(python=sys.executable)), 1)
            self.assertEqual(self.commands(mod), {mod.command(python=sys.executable)})
        for mod in (sl, sh, nh):
            self.take_out(mod)
        self.assertEqual(self.read().decode('utf-8'), HERS)

    def test_the_page_start_repairs_the_session_hooks_and_logs_one_line(self):
        self.put_in(sh, GONE)
        logged = []
        with mock.patch.object(server, 'log', logged.append), mock.patch.object(sh, 'precompile', lambda: None):
            server.start_session_hooks()
        self.assertEqual(len(logged), 1, logged)
        self.assertIn('the session hooks in %s run on' % self.real, logged[0])
        self.assertEqual(self.commands(sh), {sh.command(python=sys.executable)})


class LeftAlone(Base):
    def test_a_python_that_is_there_leaves_the_file_byte_for_byte(self):
        for mod in (sl, nh, sh):
            self.put_in(mod, sys.executable)
        before = self.read()
        for mod in (sl, nh, sh):
            self.assertEqual(mod.repair(python=sys.executable), [])
        self.assertEqual(self.read(), before)

    def test_hooks_turned_off_in_switchboard_are_not_written_again(self):
        for mod in (nh, sh):
            self.put_in(mod, GONE)                          # turned off since, and they could not be taken out
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            json.dump({'notify': False, 'session_hooks': False}, f)
        before = self.read()
        for mod in (nh, sh):
            self.assertEqual(mod.repair(python=sys.executable), [])
        self.assertEqual(self.read(), before)

    def test_your_own_status_line_and_hooks_naming_a_missing_program_are_never_touched(self):
        mine = {'statusLine': {'type': 'command', 'command': '/nonexistent/bin/python3 ~/bin/line.py'},
                'hooks': dict((e, [{'hooks': [{'type': 'command', 'command': '/nonexistent/bin/python3 ~/h.py'}]}])
                              for e in set(nh.EVENTS) | set(sh.EVENTS))}
        with open(self.path, 'w') as f:
            json.dump(mine, f, indent=2)
        before = self.read()
        for mod in (sl, nh, sh):
            self.assertEqual(mod.repair(python=sys.executable), [])
        self.assertEqual(self.read(), before)

    def test_a_marked_command_the_record_does_not_name_is_never_touched(self):
        for mod in (sl, nh, sh):
            self.put_in(mod, GONE)
        for rec in (sl.record_path(), nh.record_path(), sh.record_path()):
            os.unlink(rec)                                  # Switchboard's record is gone: no proof it put them there
        before = self.read()
        for mod in (sl, nh, sh):
            self.assertEqual(mod.repair(python=sys.executable), [])
        self.assertEqual(self.read(), before)

    def test_a_marked_command_you_changed_since_is_never_touched(self):
        self.put_in(nh, GONE)
        text = self.read().decode('utf-8')
        edited = text.replace(json.dumps(nh.command(python=GONE))[1:-1], json.dumps(
            nh.command(python='/nonexistent/other/python3'))[1:-1], 1)
        self.assertNotEqual(edited, text)
        with open(self.path, 'w') as f:
            f.write(edited)
        self.assertEqual(nh.repair(python=sys.executable), [])
        self.assertEqual(self.read().decode('utf-8'), edited)

    def test_a_refused_edit_changes_nothing_and_says_so_in_one_line(self):
        for mod in (sl, nh, sh):
            self.put_in(mod, GONE)
        os.chmod(self.path, stat.S_IRUSR)                   # read-only: apply refuses before any write
        self.addCleanup(os.chmod, self.path, stat.S_IRUSR | stat.S_IWUSR)
        before = self.read()
        for mod in (sl, nh, sh):
            said = mod.repair(python=sys.executable)
            self.assertEqual(len(said), 1, said)
            self.assertIn('was left as it is' if mod is sl else 'were left as they are', said[0])
            self.assertIn('read-only', said[0])
        self.assertEqual(self.read(), before)

    def test_no_python_of_its_own_means_nothing_is_done(self):
        self.put_in(nh, GONE)
        before = self.read()
        self.assertEqual(nh.repair(python=GONE + '-too'), [])
        self.assertEqual(self.read(), before)


if __name__ == '__main__':
    unittest.main()
