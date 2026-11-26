import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Switchboard's edits to Claude Code's settings and the uninstaller's tidy. Every file is a throwaway one under a
# temp HOME, data folder and Claude Code folder; nothing runs claude, and no hook command reaches a real notification.
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, notify_hooks as NH, server, statusline as SL
from switchboard.packs import codegraph_install as CGI
from switchboard.packs import claudemem_install as CMI
from switchboard.packs import common as C
from switchboard.packs import facts as F
import test_app  # its sandbox: the server with a fake engine

SETTINGS = b'{\n  "theme": "dark",\n  "permissions": {\n    "allow": [\n      "Bash(ls:*)"\n    ]\n  }\n}\n'
CC_JSON = b'{\n  "numStartups": 3,\n  "mcpServers": {}\n}\n'
ORIGINAL = b'{\n  "model": "opus",\n  "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}\n}\n'


class Home(unittest.TestCase):
    """A throwaway home with Claude Code's folder, and a dotfiles folder its files can link into."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix='switchboard-'))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.cd = os.path.join(self.home, '.claude')
        self.dots = os.path.join(self.tmp, 'dotfiles')
        os.makedirs(self.cd)
        os.makedirs(self.dots)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data'),
                                           'CLAUDE_CONFIG_DIR': self.cd})
        env.start()
        self.addCleanup(env.stop)

    def linked(self, path, data):
        """path becomes a link to a file of the same name in the dotfiles folder, holding data."""
        target = os.path.join(self.dots, os.path.basename(path))
        with open(target, 'wb') as f:
            f.write(data)
        os.symlink(target, path)
        return target

    def read(self, path):
        with open(path, 'rb') as f:
            return f.read()


# ---------------------------------------------------------------- a linked settings file stays a link

class LinkedFilesStayLinks(Home):
    def test_the_code_maps_allow_rule_goes_into_the_linked_file_and_comes_out_again(self):
        path = os.path.join(self.cd, 'settings.json')
        target = self.linked(path, SETTINGS)
        info = CGI.add_allow({'settings': path})
        self.assertTrue(os.path.islink(path), 'the link was replaced by a regular file')
        self.assertIn(b'mcp__codegraph__*', self.read(target))          # the dotfiles copy has the rule
        CGI.remove_allow(info)
        self.assertTrue(os.path.islink(path))
        self.assertEqual(self.read(target), SETTINGS)                   # byte for byte, in the dotfiles copy

    def test_the_mcp_entry_goes_into_a_linked_claude_json_and_comes_out_again(self):
        path = os.path.join(self.home, '.claude.json')
        target = self.linked(path, CC_JSON)
        w = {'claude_json': path}
        entry = {'type': 'stdio', 'command': '/x/codegraph', 'args': ['serve', '--mcp'], 'alwaysLoad': True}
        info = CGI.add_mcp(w, entry, settle=0)
        self.assertTrue(os.path.islink(path))
        self.assertEqual(json.loads(self.read(target))['mcpServers']['codegraph'], entry)
        CGI.remove_mcp(info, settle=0)
        self.assertTrue(os.path.islink(path))
        self.assertEqual(self.read(target), CC_JSON)

    def test_a_member_edit_and_a_shell_file_line_keep_their_links(self):
        path = os.path.join(self.cd, 'settings.json')
        target = self.linked(path, SETTINGS)
        new = F.with_member(SETTINGS, ['enabledPlugins', CMI.PLUGIN], True, path)
        self.assertTrue(F.write_if_same(path, SETTINGS, new))
        self.assertTrue(os.path.islink(path))
        self.assertEqual(self.read(target), new)
        rc = os.path.join(self.home, '.zshrc')
        rc_target = self.linked(rc, b'# mine\n# claude-mem\nexport PATH="$HOME/.local/bin:$PATH"\n')
        CMI._undo_rc({'file': rc, 'appended': '# claude-mem\nexport PATH="$HOME/.local/bin:$PATH"\n', 'at': 7,
                      'existed': True}, {})
        self.assertTrue(os.path.islink(rc), 'the shell file link was replaced by a regular file')
        self.assertEqual(self.read(rc_target), b'# mine\n')

    def test_a_link_to_nothing_is_left_alone(self):
        path = os.path.join(self.cd, 'settings.json')
        os.symlink(os.path.join(self.dots, 'gone.json'), path)
        with self.assertRaises(C.Refused):
            C.atomic_write(path, b'{}\n', 0o600)
        self.assertTrue(os.path.islink(path))
        self.assertFalse(os.path.exists(os.path.join(self.dots, 'gone.json')))

    def test_a_file_made_for_one_entry_is_not_deleted_through_a_link_the_person_made_since(self):
        path = os.path.join(self.cd, 'settings.json')
        info = CGI.add_allow({'settings': path})                        # no file before: made for the rule
        made = self.read(path)
        os.unlink(path)
        target = self.linked(path, made)                                # the person moved it into their dotfiles
        CGI.remove_allow(info)
        self.assertTrue(os.path.islink(path))
        self.assertNotIn(b'mcp__codegraph__', self.read(target))


# ---------------------------------------------------------------- off never raises, on is one plain line

class Page(test_app.Sandbox):
    """The server with a fake engine, a throwaway managed-settings folder, and no bytecode written."""

    def setUp(self):
        super().setUp()
        self.managed = os.path.join(self.tmp, 'managed')
        os.makedirs(self.managed)
        self.patch(NH, 'MANAGED', self.managed)
        self.patch(SL, 'precompile', lambda *a, **k: None)
        self.path = os.path.join(self.claude, 'settings.json')

    def put(self, data, path=None):
        with open(path or self.path, 'wb') as f:
            f.write(data)

    def get(self, path=None):
        with open(path or self.path, 'rb') as f:
            return f.read()

    def locked_folder(self, folder=None):
        folder = folder or self.claude
        os.chmod(folder, 0o555)
        self.addCleanup(os.chmod, folder, 0o755)


class NotifyOffNeverRaises(Page):
    def test_off_says_what_it_left_when_the_folder_cannot_be_written(self):
        self.put(ORIGINAL)
        server.act('/api/notify', {'on': True})
        self.locked_folder()                                     # no backup or temp file can be made beside it
        r = NH.turn(False)
        self.assertFalse(r['changed'])
        self.assertIn('Nothing was changed', r['left'])
        r = server.act('/api/notify', {'on': False})             # the page's switch: no 500, the setting is saved
        self.assertIn('Nothing was changed', r['left'])
        self.assertFalse(core.settings()['notify'])

    def test_on_is_one_plain_line_and_changes_nothing_when_the_folder_cannot_be_written(self):
        self.put(ORIGINAL)
        self.locked_folder()
        with self.assertRaises(core.SwitchError) as e:
            server.act('/api/notify', {'on': True})
        self.assertIn('Nothing was changed', str(e.exception))
        self.assertEqual(self.get(), ORIGINAL)
        self.assertNotIn('notify', core.settings())
        self.assertEqual(NH._records(), {})

    def test_the_status_line_says_it_in_one_line_too(self):
        self.put(ORIGINAL)
        self.locked_folder()
        with self.assertRaises(core.SwitchError) as e:
            server.act('/api/statusline', {'on': True})
        self.assertIn('Nothing was changed', str(e.exception))
        self.assertEqual(self.get(), ORIGINAL)


# ---------------------------------------------------------------- the file the hooks went into is found again

def quiet(fn, *a, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = fn(*a, **kw)
    return rc, out.getvalue()


class FoundWhereverTheyWent(Page):
    """The page (no CLAUDE_CONFIG_DIR of its own) puts them in ~/.claude/settings.json; a shell that exports
    CLAUDE_CONFIG_DIR for another folder then turns them off, or runs the uninstaller's tidy."""

    def setUp(self):
        super().setUp()
        self.other = os.path.join(self.tmp, 'work-claude')
        os.makedirs(self.other)

    def elsewhere(self):
        return mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': self.other})

    def test_off_from_a_shell_with_another_claude_folder_takes_out_the_pages_hooks(self):
        self.put(ORIGINAL)
        server.act('/api/notify', {'on': True})
        with self.elsewhere():
            r = NH.turn(False)
        self.assertTrue(r['changed'])
        self.assertIsNone(r['left'])
        self.assertEqual(self.get(), ORIGINAL)
        self.assertEqual(NH._records(), {})
        self.assertFalse(os.path.exists(os.path.join(self.other, 'settings.json')))

    def test_tidy_from_such_a_shell_puts_back_the_hooks_and_the_status_line(self):
        from switchboard import __main__ as cli
        self.put(ORIGINAL)
        server.act('/api/notify', {'on': True})
        server.act('/api/statusline', {'on': True})
        with self.elsewhere():
            rc, said = quiet(cli.tidy)
        self.assertEqual(self.get(), ORIGINAL, said)
        self.assertIn('Notification hooks: taken out.', said)
        self.assertIn('Status line: put back as it was.', said)


# ---------------------------------------------------------------- tidy names what it left, and fails

class Tidy(Page):
    def act(self, body):
        return server.act('/api/pack', body)

    def person_installed_claude_mem(self):
        os.makedirs(os.path.join(self.claude, 'plugins'), exist_ok=True)
        with open(os.path.join(self.claude, 'plugins', 'installed_plugins.json'), 'w') as f:
            json.dump({'plugins': {CMI.PLUGIN: [{'scope': 'user'}]}}, f)

    def tidy(self, *args):
        from switchboard import __main__ as cli
        return quiet(cli.main, ['tidy'] + list(args))


class TidyNamesWhatItLeft(Tidy):
    def test_an_unreadable_claude_md_is_named_and_every_step_after_it_still_runs(self):
        self.act({'pack': 'house', 'on': True})
        self.person_installed_claude_mem()
        self.act({'pack': 'claudemem', 'on': True})
        md = os.path.join(self.claude, 'CLAUDE.md')
        os.chmod(md, 0)
        self.addCleanup(os.chmod, md, 0o644)
        rc, said = self.tidy()
        self.assertEqual(rc, 1, said)
        self.assertIn('House rules: left as it is (%s: Permission denied)' % os.path.realpath(md), said)
        self.assertIn('claude-mem settings: put back as it was.', said)      # the steps after it ran

    def test_a_refused_undo_fails_tidy_and_a_clean_one_does_not(self):
        self.put(ORIGINAL)
        server.act('/api/notify', {'on': True})
        good = self.get()
        self.put(good + b'oops')                                     # not JSON any more: the hooks can't come out
        rc, said = self.tidy()
        self.assertEqual(rc, 1, said)
        self.assertIn('Notification hooks: left as they are (', said)
        self.put(good)
        rc, said = self.tidy()
        self.assertEqual((rc, self.get()), (0, ORIGINAL), said)

    def test_an_edited_house_rules_block_fails_tidy(self):
        self.act({'pack': 'house', 'on': True})
        md = os.path.join(self.claude, 'CLAUDE.md')
        text = self.get(md)
        self.put(text.replace(b'\n', b'\nmy own line\n', 2), md)
        rc, said = self.tidy()
        self.assertEqual(rc, 1, said)
        self.assertIn('House rules: left as it is (You edited inside', said)

    def test_with_no_claude_code_folder_at_all_nothing_is_left_and_tidy_succeeds(self):
        self.act({'pack': 'house', 'on': True})                      # a record of a block in a folder that then goes
        shutil.rmtree(self.claude)
        rc, said = self.tidy()
        self.assertEqual(rc, 0, said)
        self.assertNotIn('left as', said)
        rc, said = self.tidy()
        self.assertEqual((rc, said), (0, ''))

    def test_claude_mems_gate_stays_while_claude_mem_itself_could_not_be_taken_out(self):
        from switchboard import claudecode
        from switchboard.packs import install as PI
        left = []
        with mock.patch.object(PI, 'remove', side_effect=lambda pack, delete_maps=False: (_ for _ in ()).throw(
                C.Refused('its worker did not stop')) if pack == 'claude-mem' else []):
            said = claudecode.undo_all(left)
        self.assertIn('claude-mem: left as it is (its worker did not stop)', said)
        self.assertIn('claude-mem settings: left as it is (claude-mem itself is still installed)', said)
        self.assertEqual(left, ['claude-mem', 'claude-mem settings'])


# ---------------------------------------------------------------- no copy of the settings is left behind

class NoBackupLeftBehind(Tidy):
    def backups(self):
        return sorted(n for n in os.listdir(self.claude) if 'switchboard-backup' in n)

    def test_tidy_takes_the_backup_copy_away_once_everything_is_put_back(self):
        self.put(ORIGINAL)
        server.act('/api/notify', {'on': True})
        server.act('/api/statusline', {'on': True})
        self.assertEqual(self.backups(), ['settings.json.switchboard-backup'])
        rc, said = self.tidy()
        self.assertEqual((rc, self.get()), (0, ORIGINAL), said)
        self.assertEqual(self.backups(), [])

    def test_the_backup_stays_while_anything_is_left(self):
        self.put(ORIGINAL)
        server.act('/api/notify', {'on': True})
        self.act({'pack': 'house', 'on': True})
        md = os.path.join(self.claude, 'CLAUDE.md')
        os.chmod(md, 0)
        self.addCleanup(os.chmod, md, 0o644)
        rc, said = self.tidy()
        self.assertEqual(rc, 1, said)
        self.assertEqual(self.backups(), ['settings.json.switchboard-backup'])



# ---------------------------------------------------------------- what Switchboard made, or found empty, comes back

class EmptyStartsComeBackTheSame(Home):
    """The status line and the notification hooks in each order, and the code map's rule and entry, from a settings
    file that is missing, empty, or an empty object on lines of its own."""

    CMD = NH.command(python='/usr/bin/python3', folder='/opt/sb/switchboard')
    LINE = '/usr/bin/python3 -I -S -c x # switchboard statusline'

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.cd, 'settings.json')

    def start(self, data):
        shutil.rmtree(self.cd, True)
        os.makedirs(self.cd)
        shutil.rmtree(os.environ['SWITCHBOARD_HOME'], True)
        if data is not None:
            with open(self.path, 'wb') as f:
                f.write(data)

    def now(self):
        return self.read(self.path) if os.path.exists(self.path) else None

    def n_on(self):
        NH.apply(NH.install_plan(self.path, command_line=self.CMD))

    def n_off(self):
        NH.apply(NH.uninstall_plan(self.path))

    def s_on(self):
        SL.apply(SL.install_plan(self.path, command_line=self.LINE))

    def s_off(self):
        SL.apply(SL.uninstall_plan(self.path))

    def test_every_order_of_the_two_gives_the_file_back(self):
        orders = {'n s n s': (self.n_on, self.s_on, self.n_off, self.s_off),
                  'n s s n': (self.n_on, self.s_on, self.s_off, self.n_off),
                  's n s n': (self.s_on, self.n_on, self.s_off, self.n_off),
                  's n n s': (self.s_on, self.n_on, self.n_off, self.s_off),
                  'n n': (self.n_on, self.n_off), 's s': (self.s_on, self.s_off)}
        for name, data in (('no file', None), ('empty', b''), ('a newline', b'\n'), ('{}', b'{}\n'),
                           ('{ on two lines }', b'{\n}\n'), ('{ spaced }', b'{ \n\n }\n'), ('one key', b'{"a": 1}\n')):
            for order, steps in orders.items():
                with self.subTest(start=name, order=order):
                    self.start(data)
                    for step in steps:
                        step()
                    self.assertEqual(self.now(), data)

    def test_the_code_maps_rule_and_entry_give_back_an_empty_or_blank_file(self):
        cj = os.path.join(self.home, '.claude.json')
        entry = {'type': 'stdio', 'command': '/x/codegraph', 'args': ['serve', '--mcp'], 'alwaysLoad': True}
        for name, data in (('empty', b''), ('a newline', b'\n'), ('{ on two lines }', b'{\n}\n'), ('{}', b'{}\n')):
            with self.subTest(start=name):
                self.start(data)
                CGI.remove_allow(CGI.add_allow({'settings': self.path}))
                self.assertEqual(self.now(), data)
                with open(cj, 'wb') as f:
                    f.write(data)
                CGI.remove_mcp(CGI.add_mcp({'claude_json': cj}, entry, settle=0), settle=0)
                self.assertEqual(self.read(cj), data)



# ---------------------------------------------------------------- a project's own disableAllHooks is named

class ProjectsThatTurnHooksOff(Page):
    """Claude Code reads disableAllHooks from the managed, local, project and user settings, the first that sets it
    deciding. The page's switch reads the managed and user files (they apply everywhere); `switchboard notify` names
    the projects Claude Code knows (its .claude.json "projects") whose own files turn them off there."""

    def project(self, name, local=None, shared=None):
        p = os.path.join(self.tmp, 'code', name)
        os.makedirs(os.path.join(p, '.claude'))
        for fname, obj in (('settings.local.json', local), ('settings.json', shared)):
            if obj is not None:
                with open(os.path.join(p, '.claude', fname), 'w') as f:
                    json.dump(obj, f)
        return p

    def known(self, *paths):
        with open(os.path.join(self.claude, '.claude.json'), 'w') as f:      # CLAUDE_CONFIG_DIR is set here
            json.dump({'numStartups': 3, 'projects': dict((p, {'allowedTools': []}) for p in paths)}, f)

    def say(self):
        from switchboard import __main__ as cli
        return quiet(cli.main, ['notify'])[1]

    def test_switchboard_notify_names_the_projects_whose_own_settings_turn_them_off(self):
        off = self.project('legacy-app', local={'disableAllHooks': True})
        shared = self.project('team-repo', shared={'disableAllHooks': True})
        back_on = self.project('mine', local={'disableAllHooks': False}, shared={'disableAllHooks': True})
        plain = self.project('plain')
        self.known(off, shared, back_on, plain, os.path.join(self.tmp, 'code', 'gone'))
        said = self.say()
        line = [x for x in said.splitlines() if x.startswith('Off in')]
        self.assertEqual(line, ['Off in 2 projects by their own disableAllHooks: %s, %s' % (off, shared)], said)

    def test_the_managed_settings_decide_for_every_project(self):
        self.known(self.project('legacy-app', local={'disableAllHooks': True}))
        with open(os.path.join(self.managed, 'managed-settings.json'), 'w') as f:
            json.dump({'disableAllHooks': False}, f)
        self.assertNotIn('Off in', self.say())


if __name__ == '__main__':
    unittest.main()
