import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# "Done" after a long turn is a choice of its own, off for a new install. "Needs you" and Switchboard's own notes stay
# as they are. An install an older version set up keeps "done" as it had it: the reading of the setting decides it.
#
# Fakes only. Every settings file is a throwaway one under a temp HOME and CLAUDE_CONFIG_DIR, and the managed settings
# folder is a temp one too (test_notify_hooks.Base). No notification is shown: the hook program runs with
# SWITCHBOARD_OSASCRIPT pointing at a stand-in that writes its arguments to a file. Nothing runs claude, and no port
# is opened: the terminal tests name the run's own port, where nothing listens.
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import config, core, keep, notify as nt, notify_hooks as nh, server
import test_cli_commands
import test_notify_hooks
from test_notify_hooks import needs, stop, ups

RUN_PORT = int(os.environ.get('SWITCHBOARD_PORT') or 7937)     # the run's own port: nothing listens there
DONE = 'Claude Code is done'
NEEDS = 'Claude Code needs you'


class HookSandbox(test_notify_hooks.Base):
    """test_notify_hooks.Base, with the hook program run exactly as Claude Code runs the command in settings.json."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        keep.reset()
        self.addCleanup(keep.reset)
        self.patch(server, 'NOTIFY_RUN', self.run_ok)           # an in-process note is only written down
        bin_dir = os.path.join(self.tmp, 'bin')
        os.makedirs(bin_dir)
        self.log = os.path.join(self.tmp, 'shown.jsonl')
        self.osa = os.path.join(bin_dir, 'osascript')
        with open(self.osa, 'w') as f:
            f.write('#!%s -I\nimport json, sys\nwith open(%r, "a") as f:\n'
                    '    f.write(json.dumps(sys.argv[1:]) + "\\n")\n' % (sys.executable, self.log))
        os.chmod(self.osa, 0o755)

    def own(self, values):
        """Switchboard's settings file holds exactly `values`."""
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            json.dump(values, f)

    def stop_command(self):
        """The Stop hook's command, as it sits in Claude Code's settings file."""
        return nh._found(nh._settings(self.path)['obj'])['Stop'][0]

    def hook(self, event, at):
        e = dict(os.environ, SWITCHBOARD_OSASCRIPT=self.osa, SWITCHBOARD_NOTIFY_NOW=repr(at))
        r = subprocess.run(['/bin/sh', '-c', self.stop_command()], input=json.dumps(event).encode(),
                           capture_output=True, env=e, cwd=self.tmp, timeout=90)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b'', b''))

    def titles(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line)[-2] for line in f]

    def long_turn_then_needs_you(self):
        """A turn of 5 minutes, then a permission prompt: the titles shown."""
        self.hook(ups(pid='p1'), self.now)
        self.hook(stop(pid='p1'), self.now + 300)
        self.hook(needs(), self.now + 310)
        return self.titles()


class ANewInstall(HookSandbox):
    def test_after_a_first_run_a_long_turn_gives_no_done_and_needs_you_comes_as_before(self):
        self.assertTrue(keep.notify_by_itself(now=self.now))
        self.assertEqual(self.long_turn_then_needs_you(), [NEEDS])
        self.assertIs(core.settings().get('notify_done'), False)
        self.assertIs(nh.done_on(), False)

    def test_a_choice_made_before_the_first_run_stays(self):
        self.own({'notify_done': True})
        self.assertTrue(keep.notify_by_itself(now=self.now))
        self.assertIs(core.settings().get('notify_done'), True)
        self.assertEqual(self.long_turn_then_needs_you(), [DONE, NEEDS])

    def test_with_no_record_of_hooks_an_empty_settings_file_reads_off(self):
        self.assertEqual(nh._records(), {})
        self.assertIs(nh.done_on(), False)                      # no settings file
        self.own({})
        self.assertIs(nh.done_on(), False)                      # one restored empty

    def test_turning_notifications_on_by_hand_on_a_fresh_folder_starts_done_off(self):
        server.act('/api/notify', {'on': True})              # the page's switch, and `switchboard notify on`
        self.assertEqual(self.long_turn_then_needs_you(), [NEEDS])
        self.assertIs(core.settings().get('notify_done'), False)

    def test_a_value_set_before_the_hooks_exist_stays_through_notify_on(self):
        self.own({'notify_done': True})
        server.act('/api/notify', {'on': True})
        self.assertIs(core.settings().get('notify_done'), True)
        self.assertEqual(self.long_turn_then_needs_you(), [DONE, NEEDS])

    def test_notify_off_first_then_on_leaves_done_off(self):
        server.act('/api/notify', {'on': False})
        server.act('/api/notify', {'on': True})
        self.assertIs(core.settings().get('notify_done'), False)
        self.assertEqual(self.long_turn_then_needs_you(), [NEEDS])

    def test_a_first_run_whose_hooks_cannot_go_in_leaves_only_done_off(self):
        self.patch(nh, 'verify', lambda cmd, timeout=30: "The notification hooks didn't run here (no answer).")
        self.assertFalse(keep.notify_by_itself(now=self.now))     # putting them in raised after the value was written
        self.assertEqual(core.settings(), {'notify_done': False})  # no mark: a later first run may try again
        self.assertEqual((nh.status()['in'], nh._records()), (False, {}))


class AnInstallThatExists(HookSandbox):
    def test_a_folder_an_older_first_run_marked_still_gives_done(self):
        self.own({'notify_by_itself': self.now - 86400})        # an older version's first run, a day ago
        nh.turn(True)                                           # the hooks it put in
        self.assertNotIn('notify_done', core.settings())
        self.assertIs(nh.done_on(), True)
        self.assertEqual(self.long_turn_then_needs_you(), [DONE, NEEDS])

    def test_where_you_set_notifications_before_done_stays_as_you_had_it(self):
        for older in ({'notify_set_by_you': True, 'notify': True}, {'notify': True}):
            with self.subTest(older):
                self.own(older)
                server.act('/api/notify', {'on': True})          # again, with this version: nothing new is written
                self.assertNotIn('notify_done', core.settings())
                self.assertIs(nh.done_on(), True)

    def test_turning_done_off_there_holds(self):
        self.own({'notify_by_itself': self.now - 86400})
        nh.turn(True)
        keep.notify_done_by_you(False)
        self.assertEqual(self.long_turn_then_needs_you(), [NEEDS])

    def test_switchboards_record_of_its_hooks_counts_when_the_marks_are_gone(self):
        nh.turn(True)                       # an older version put the hooks in, and its mark was never written
        self.assertTrue(nh._records())
        self.assertFalse(os.path.exists(os.path.join(self.data, 'settings.json')))
        self.assertIs(nh.done_on(), True)                       # no settings file
        self.own({})
        self.assertIs(nh.done_on(), True)                       # one restored empty
        server.act('/api/notify', {'on': False})            # a later choice writes no "done off" over it
        server.act('/api/notify', {'on': True})
        self.assertNotIn('notify_done', core.settings())
        self.assertEqual(self.long_turn_then_needs_you(), [DONE, NEEDS])

    def test_a_damaged_settings_file_as_the_hook_program_reads_it(self):
        self.own({'notify_by_itself': self.now - 86400})
        nh.turn(True)
        with open(os.path.join(self.data, 'settings.json'), 'wb') as f:
            f.write(b'{"notify_by_itself": 1.8e9, "aw')          # cut off part-way
        self.assertEqual((nh.notes_on(), nh.done_on()), (True, True))     # read as nothing: the hooks record decides
        self.assertEqual(self.long_turn_then_needs_you(), [DONE, NEEDS])


class TheReading(unittest.TestCase):
    def test_its_value_first_then_whether_an_older_version_set_notifications(self):
        for s, want in (({}, False), ({'notify_done': True}, True), ({'notify_done': False}, False),
                        ({'notify_by_itself': 1.8e9}, True), ({'notify_by_itself': None}, False),
                        ({'notify_by_itself': 1.8e9, 'notify_done': False}, False),
                        ({'notify_set_by_you': True}, True), ({'notify': False}, True), ({'notify': True}, True),
                        ({'notify_done': 'yes'}, False), ({'notify_done': 1, 'notify': True}, True)):
            with self.subTest(s):
                self.assertIs(nh.done_wanted(s), want)

    def test_from_hook_with_done_off_gives_no_done_and_the_same_needs_you(self):
        now = 1.8e9
        self.assertIsNone(nt.from_hook(stop(), started=now - 300, now=now, done=False))
        self.assertIsNone(nt.from_hook(stop(), now=now, done=False))                    # no start known: still none
        self.assertEqual(nt.from_hook(stop(), started=now - 300, now=now, done=True).title, DONE)
        for kind in nt.NEEDS_YOU:
            with self.subTest(kind):
                self.assertEqual(nt.from_hook(needs(kind), now=now, done=False), nt.from_hook(needs(kind), now=now))


class CliSandbox(test_cli_commands.Cli):
    """test_cli_commands.Cli (a fake account engine, nothing shown), on the run's own port."""

    def setUp(self):
        p = mock.patch.object(test_cli_commands, 'free_port', lambda: RUN_PORT)
        p.start()
        self.addCleanup(p.stop)
        super().setUp()
        self.claude_file = os.path.join(core.claude_dir(), 'settings.json')


class Terminal(CliSandbox):

    def files(self):
        """Every file a change could touch, but the one setting: Switchboard's settings without it, and Claude Code's
        settings file as bytes."""
        s = dict(core.settings())
        s.pop('notify_done', None)
        try:
            with open(self.claude_file, 'rb') as f:
                theirs = f.read()
        except OSError:
            theirs = None
        return s, theirs

    def test_on_and_off_change_exactly_that_in_one_line(self):
        core.set_setting('awake', False)                     # something of yours already there, to stay as it is
        before = self.files()
        code, out, err = self.run_cli('notify', 'done', 'on')
        self.assertEqual((code, err, len(out.splitlines())), (0, '', 1), out)
        self.assertIs(core.settings()['notify_done'], True)
        self.assertEqual(self.files(), before)
        self.assertEqual(out, 'Done after a long turn: on. Notifications are off, so none comes until you run: '
                              'switchboard notify on\n')
        code, out, err = self.run_cli('notify', 'done', 'off')
        self.assertEqual((code, err, out), (0, '', 'Done after a long turn: off. A turn that ends sends no '
                                                   'notification.\n'))
        self.assertIs(core.settings()['notify_done'], False)
        self.assertEqual(self.files(), before)

    def test_with_notifications_on_it_says_what_comes(self):
        self.run_cli('notify', 'on')
        self.assertEqual(self.run_cli('notify', 'done', 'on'),
                         (0, 'Done after a long turn: on. A notification comes when a turn of a minute or more '
                             'ends.\n', ''))

    def test_alone_it_says_whether_done_is_on(self):
        code, out, _ = self.run_cli('notify')
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[:3], ['Notifications: off', 'Notification hooks in Claude Code: off',
                                                'Done after a long turn: off. Turn it on with: switchboard notify '
                                                'done on'])
        self.run_cli('notify', 'done', 'on')                   # on, while Notifications are off: says so
        self.assertEqual(self.run_cli('notify')[1].splitlines()[2],
                         'Done after a long turn: on, but Notifications are off. Turn them on with: '
                         'switchboard notify on')
        self.run_cli('notify', 'on')
        self.assertEqual(self.run_cli('notify')[1].splitlines()[:3],
                         ['Notifications: on', 'Notification hooks in Claude Code: on',
                          'Done after a long turn: on. Turn it off with: switchboard notify done off'])

    def test_words_it_does_not_take(self):
        usage = 'switchboard notify [on|off]\nswitchboard notify done on|off\n'
        for args in (('done',), ('done', 'maybe'), ('done', 'on', 'now'), ('sideways',)):
            with self.subTest(args=args):
                self.assertEqual(self.run_cli('notify', *args), (2, usage, ''))
        self.assertNotIn('notify_done', core.settings())


class Config(CliSandbox):
    def test_listed_with_its_default_and_words(self):
        self.two()
        code, out, _ = self.run_cli('config')
        self.assertEqual(code, 0)
        row = config.BY_KEY['notify_done']
        line = next(x for x in out.splitlines() if x.startswith('  notify_done '))
        self.assertRegex(line, r'^  notify_done +off +default off$')
        self.assertIn(row.words, out)
        self.assertEqual((row.group, row.kind, row.file_key), ('working', 'on-off', 'notify_done'))
        doc = json.loads(self.run_cli('config', '--json')[1])
        self.assertEqual(next(x for x in doc['settings'] if x['key'] == 'notify_done')['set_with'],
                         'switchboard config set notify_done VALUE')
        self.assertEqual(doc['not_read'], [])

    def test_set_get_and_unset_as_for_the_other_on_off_rows(self):
        self.two()
        self.assertEqual(self.run_cli('config', 'get', 'notify_done'), (0, 'off\n', ''))
        self.assertEqual(self.run_cli('config', 'set', 'notify_done', 'on'),
                         (0, 'notify_done is on now (it was off).\n', ''))
        self.assertIs(core.settings().get('notify_done'), True)
        self.assertEqual(self.run_cli('config', 'get', 'notify_done'), (0, 'on\n', ''))
        self.assertEqual(self.run_cli('config', 'set', 'notify_done', 'on'), (0, 'notify_done is on already.\n', ''))
        self.assertIn('switchboard notify done on\n', self.run_cli('config', '--as-commands')[1])
        self.assertEqual(self.run_cli('config', 'set', 'notify_done', 'maybe')[0],
                         'refused: notify_done takes on or off.')
        self.assertEqual(self.run_cli('config', 'unset', 'notify_done')[0], 0)
        self.assertEqual(self.run_cli('config', 'get', 'notify_done'), (0, 'off\n', ''))
        self.assertIs(core.settings().get('notify_done'), False)

    def test_an_install_an_older_version_set_up_lists_it_on(self):
        core.set_setting('notify_by_itself', 1.8e9)
        self.assertEqual(self.run_cli('config', 'get', 'notify_done'), (0, 'on\n', ''))


class Doctor(CliSandbox):
    def test_doctor_names_the_hooks_as_switchboard_notify_does(self):
        from switchboard import __main__ as cli, browser, connect, engine, lanes, linkapp
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(linkapp, 'status', lambda fresh=False: {'installed': False,
                                                                                            'on': False}), \
                mock.patch.object(browser, 'present', lambda: False), \
                mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(cli, 'page_status', lambda: 'down'), \
                mock.patch.object(engine, 'foreign_switcher', lambda: None), \
                mock.patch.object(connect, 'claude_bin', lambda: None), \
                mock.patch.object(lanes, 'answers', lambda *a, **k: False), \
                mock.patch.object(cli, 'auth_said', lambda after_switch=False: None, create=True):
            cli.doctor()
        self.assertIn('Notification hooks in Claude Code', out.getvalue())
        self.assertNotIn('Done and needs you', out.getvalue())


if __name__ == '__main__':
    unittest.main()
