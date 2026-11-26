import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Tests for notifications.
# No notification is shown: notify() gets a fake runner. One test asks the real
# AppleScript to read back the quoted text with `return`, which shows nothing.
# The memory of shown notes lives in a throwaway folder.
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import notify as nt

NASTY = ('quote " backslash \\ both \\" end \\', 'tab\there\nnew line\r\x07bell', '$(rm -rf ~) `id` ; \'single\'',
         'café — \U0001f600', '\\n is not a new line', '',
         # curly and other quotes AppleScript source would take as string delimiters
         'x” & (do shell script "id") & “', '‘single’ „double‟ «guillemets»',
         '“" & (do shell script "touch /tmp/x") & "”',
         '-e beep', '-e', '--', '-l JavaScript')            # option-shaped text stays text (after `--`)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-notify-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        env = mock.patch.dict(os.environ, {'HOME': os.path.join(self.tmp, 'home'),
                                           'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data'),
                                           'TZ': 'America/Los_Angeles'})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('SWITCHBOARD_NOTIFY_STATE', None)
        time.tzset()
        self.addCleanup(time.tzset)
        self.shown = []
        self.now = time.mktime((2026, 11, 13, 10, 0, 0, 0, 0, -1))

    def run_ok(self, cmd):
        self.shown.append(cmd)
        return 0


class Showing(Base):
    def test_the_command_is_osascript_with_no_shell_and_the_text_only_as_arguments(self):
        self.assertTrue(nt.notify('Switchboard', 'Hello', run=self.run_ok))
        self.assertEqual(self.shown, [['/usr/bin/osascript', '-e', 'on run argv', '-e',
                                       'display notification (item 2 of argv) with title (item 1 of argv)',
                                       '-e', 'end run', '--', 'Switchboard', 'Hello']])

    def test_no_text_ever_becomes_part_of_the_script(self):
        for text in NASTY:
            self.shown = []
            nt.notify(text, text, run=self.run_ok)
            self.assertEqual(self.shown[0][:7], ['/usr/bin/osascript', '-e', 'on run argv', '-e',
                                                 'display notification (item 2 of argv) with title (item 1 of argv)',
                                                 '-e', 'end run'], text)
            self.assertEqual(self.shown[0][7:], ['--', nt._clean(text, 80), nt._clean(text, 240)])

    def test_control_characters_become_spaces_and_long_text_is_cut(self):
        self.assertEqual(nt._clean('one\ntwo\r\tthree\x00\x7f\u0085 end', 80), 'one two three end')
        self.assertEqual(nt._clean('x' * 300, 240), '%s…' % ('x' * 239))

    def test_failure_is_false_not_an_exception(self):
        self.assertFalse(nt.notify('t', 'b', run=lambda cmd: 1))
        self.assertFalse(nt.notify('t', 'b', run=lambda cmd: None))
        with mock.patch.object(nt.subprocess, 'run', side_effect=OSError('no osascript')):
            self.assertIsNone(nt._run(['x']))

    @unittest.skipUnless(os.path.exists(nt.OSASCRIPT), 'needs macOS')
    def test_applescript_reads_the_text_back_exactly(self):
        for text in NASTY:
            want = nt._clean(text, 240)
            try:     # the way notify() passes text: as an argument; read back with `return`, nothing is shown
                r = subprocess.run([nt.OSASCRIPT, '-e', 'on run argv', '-e', 'return item 1 of argv', '-e', 'end run',
                                    '--', want], capture_output=True, timeout=20)
            except OSError as e:
                self.skipTest('osascript can not run here: %s' % e)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.decode('utf-8')[:-1], want)


class Changes(Base):
    def test_switched(self):
        n = nt.switched('atlas', 'birch', 'atlas had used 95% of its weekly limit.', at=1000)
        self.assertEqual((n.title, n.body), ('Switched to birch', 'From atlas. atlas had used 95% of its weekly limit.'))
        self.assertEqual(nt.switched(None, 'birch').body, 'Claude Code switched.')
        self.assertEqual(nt.switched(None, 'birch').every, 60)
        self.assertIsNone(nt.switched('a', 'a'))
        self.assertIsNone(nt.switched('a', None))

    def test_nearing_its_line(self):
        sun = time.mktime((2026, 11, 15, 14, 0, 0, 0, 0, -1))
        n = nt.nearing('atlas', 'seven_day', {'used_percentage': 92.4, 'resets_at': sun}, now=self.now)
        self.assertEqual((n.title, n.body, n.instance), ('atlas is near its limit',
                                                         '7% of its weekly limit left. Refills Sun 2 PM.', sun))
        n = nt.nearing('atlas', 'five_hour', {'pct': 95, 'resets': '2026-11-13T20:30:00Z'}, now=self.now)
        self.assertEqual(n.body, '5% of its 5-hour limit left. Refills 12:30 PM.')
        self.assertIsNone(nt.nearing('atlas', 'seven_day', {'used_percentage': 89.9, 'resets_at': sun}, now=self.now))
        self.assertIsNone(nt.nearing('atlas', 'seven_day', {'used_percentage': 99, 'resets_at': self.now - 1},
                                     now=self.now))
        self.assertIsNone(nt.nearing('atlas', 'monthly', {'used_percentage': 99}, now=self.now))
        self.assertIsNone(nt.nearing('atlas', 'seven_day', None, now=self.now))
        self.assertEqual(nt.nearing('x', 'seven_day', {'used_percentage': 120}, now=self.now).body,
                         '0% of its weekly limit left.')

    def test_refilled_only_after_it_mattered(self):
        before = {'used_percentage': 97, 'resets_at': self.now - 60}
        n = nt.refilled('atlas', 'five_hour', before, {'used_percentage': 3, 'resets_at': self.now + 5 * 3600},
                        now=self.now)
        self.assertEqual((n.title, n.body), ('atlas refilled', 'Its 5-hour limit is fresh again.'))
        self.assertIsNotNone(nt.refilled('atlas', 'five_hour', before, None, now=self.now))
        moved = {'used_percentage': 95, 'resets_at': self.now + 600}
        self.assertIsNotNone(nt.refilled('b', 'seven_day', moved, {'used_percentage': 0, 'resets_at': self.now + 7 * 86400},
                                         now=self.now))
        self.assertIsNone(nt.refilled('b', 'seven_day', moved, {'used_percentage': 96, 'resets_at': self.now + 620},
                                      now=self.now))                          # same window, still near
        self.assertIsNone(nt.refilled('b', 'seven_day', {'used_percentage': 40, 'resets_at': self.now - 1},
                                      {'used_percentage': 0}, now=self.now))  # never got close: no news
        self.assertIsNone(nt.refilled('b', 'seven_day', None, {'used_percentage': 0}, now=self.now))

    def test_plug_in(self):
        n = nt.plug_in('Battery at 18% while Claude Code is working.')
        self.assertEqual((n.title, n.every), ('Plug in your Mac', 30 * 60))
        self.assertIsNone(nt.plug_in(''))
        self.assertIsNone(nt.plug_in(None))

    def test_from_states(self):
        old = {'active': 'ana@work.example', 'accounts': {
            'ana@work.example': {'alias': 'atlas', 'usage': {'weekly': {'pct': 96, 'resets': '2026-11-15T22:00:00Z'},
                                                              'session': {'pct': 20, 'resets': None}}},
            'ana@home.example': {'alias': 'birch', 'usage': {'session': {'pct': 98, 'resets': '2026-11-13T17:00:00Z'}}}}}
        new = json.loads(json.dumps(old))
        new['active'] = 'ana@home.example'
        new['accounts']['ana@home.example']['usage']['session'] = {'pct': 2, 'resets': '2026-11-13T23:00:00Z'}
        notes = nt.from_states(old, new, now=self.now + 3 * 3600)
        # each account by its whole email, never its alias (atlas, birch)
        self.assertEqual([n.title for n in notes], ['Switched to ana@home.example', 'ana@work.example is near its limit',
                                                    'ana@home.example refilled'])
        self.assertEqual(notes[0].body, 'From ana@work.example. ana@work.example had used 96% of its weekly limit.')
        self.assertEqual(nt.from_states(new, new, now=self.now + 3 * 3600)[0].title, 'ana@work.example is near its limit')
        self.assertEqual(nt.from_states({}, {}), [])
        self.assertEqual([n.title for n in nt.from_states({'active': 'a@one.example', 'accounts': {'a@one.example': None}},
                                                          {'active': 'b@two.example', 'accounts': {'b@two.example': None}})],
                         ['Switched to b@two.example'])
        self.assertEqual([n.title for n in nt.from_states({'active': 'a@one.example', 'accounts': {}},
                                                          {'active': 'b@two.example',
                                                           'accounts': {'b@two.example': {'slot': 2}}})],
                         ['Switched to b@two.example'])                  # not "Account 2"


class Hooks(Base):
    STOP = {'session_id': 's1', 'prompt_id': 'p1', 'cwd': '/work/my-app', 'hook_event_name': 'Stop',
            'stop_hook_active': False, 'last_assistant_message': 'SECRET-TEXT', 'background_tasks': [],
            'session_crons': []}

    def test_stop_means_done_without_any_conversation_text(self):
        n = nt.from_hook(self.STOP)
        self.assertEqual((n.title, n.body, n.key, n.instance), ('Claude Code is done', 'In my-app.', 'done:s1', 'p1'))
        self.assertNotIn('SECRET', ''.join(map(str, n)))
        self.assertEqual(nt.from_hook(dict(self.STOP, cwd='')).body, 'Your turn.')
        self.assertIsNotNone(nt.from_hook(dict(self.STOP, session_crons=[{'id': 'c', 'recurring': True}])))

    def test_stop_while_continuing_or_waiting_on_background_work_is_not_done(self):
        self.assertIsNone(nt.from_hook(dict(self.STOP, stop_hook_active=True)))
        self.assertIsNone(nt.from_hook(dict(self.STOP, background_tasks=[{'id': 't', 'type': 'shell', 'status': 'running'}])))

    def test_notifications_that_need_you(self):
        for kind in ('permission_prompt', 'elicitation_dialog', 'elicitation_url_dialog', 'agent_needs_input'):
            n = nt.from_hook({'session_id': 's1', 'cwd': '/work/my-app', 'hook_event_name': 'Notification',
                              'message': 'Claude needs your permission to use Bash', 'notification_type': kind})
            said = nt.OUTSIDE.get(kind, 'Claude needs your permission to use Bash.')     # an outside text never shows
            self.assertEqual((n.title, n.body), ('Claude Code needs you', said + ' In my-app.'))
        idle = nt.from_hook({'session_id': 's1', 'prompt_id': 'p1', 'hook_event_name': 'Notification',
                             'notification_type': 'idle_prompt', 'message': 'Claude is waiting for your input'})
        self.assertIsNone(idle)          # it follows every answer, short ones too; Stop's "done" is the one

    def test_other_events_are_silent(self):
        for ev in ({'hook_event_name': 'Notification', 'notification_type': 'auth_success'},
                   {'hook_event_name': 'PreToolUse'}, {'hook_event_name': 'SubagentStop'}, {}, None, 'Stop'):
            self.assertIsNone(nt.from_hook(ev))


class Sending(Base):
    def test_once_per_account_per_window(self):
        sun = self.now + 2 * 86400
        w = {'used_percentage': 92, 'resets_at': sun}
        self.assertTrue(nt.send(nt.nearing('atlas', 'seven_day', w, now=self.now), run=self.run_ok, now=self.now))
        w2 = {'used_percentage': 97, 'resets_at': sun + 7}                       # same window, reset time jitters
        self.assertFalse(nt.send(nt.nearing('atlas', 'seven_day', w2, now=self.now), run=self.run_ok, now=self.now + 60))
        self.assertTrue(nt.send(nt.nearing('birch', 'seven_day', w, now=self.now), run=self.run_ok, now=self.now))
        self.assertTrue(nt.send(nt.nearing('atlas', 'five_hour', dict(w, resets_at=self.now + 3600), now=self.now),
                                run=self.run_ok, now=self.now))
        nxt = {'used_percentage': 91, 'resets_at': sun + 7 * 86400}              # next week's window
        self.assertTrue(nt.send(nt.nearing('atlas', 'seven_day', nxt, now=sun + 1), run=self.run_ok, now=sun + 1))
        self.assertEqual(len(self.shown), 4)

    def test_plug_in_at_most_every_half_hour(self):
        note = nt.plug_in('Battery at 18% while Claude Code is working.')
        self.assertTrue(nt.send(note, run=self.run_ok, now=0))
        self.assertFalse(nt.send(note, run=self.run_ok, now=29 * 60))
        self.assertTrue(nt.send(note, run=self.run_ok, now=30 * 60))

    def test_a_note_that_failed_to_show_is_tried_again(self):
        note = nt.switched('a', 'b', at=5)
        self.assertFalse(nt.send(note, run=lambda cmd: 1, now=10))
        self.assertTrue(nt.send(note, run=self.run_ok, now=11))
        self.assertFalse(nt.send(note, run=self.run_ok, now=12))
        self.assertFalse(nt.send(None, run=self.run_ok))

    def test_memory_file_location_permissions_and_pruning(self):
        default = os.path.join(self.tmp, 'data', 'notify.json')
        self.assertEqual(nt.state_path(), default)
        nt.send(nt.switched('a', 'b', at=1), run=self.run_ok, now=1)
        self.assertEqual(stat.S_IMODE(os.stat(default).st_mode), 0o600)
        nt.send(nt.switched('b', 'c', at=nt.KEEP + 10), run=self.run_ok, now=nt.KEEP + 10)
        with open(default) as f:
            self.assertEqual(list(json.load(f)['seen']), ['switch:b>c'])
        other = os.path.join(self.tmp, 'elsewhere', 'n.json')
        os.environ['SWITCHBOARD_NOTIFY_STATE'] = other
        self.assertEqual(nt.state_path(), other)
        nt.send(nt.plug_in('x'), run=self.run_ok, now=1)
        self.assertTrue(os.path.exists(other))

    def test_a_broken_memory_file_is_started_again(self):
        path = os.path.join(self.tmp, 'data', 'notify.json')
        os.makedirs(os.path.dirname(path))
        for junk in ('{"seen": ', '[]', '{"seen": [1]}'):
            with open(path, 'w') as f:
                f.write(junk)
            self.assertTrue(nt.send(nt.plug_in('x'), run=self.run_ok, now=len(self.shown) * 3600))


if __name__ == '__main__':
    unittest.main()
