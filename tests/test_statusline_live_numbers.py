import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Live numbers for the account in use.
#
# Claude Code hands its own limit numbers to the status line on every refresh, and Switchboard's status line saves
# them (statusline.py's live records). So Switchboard's status line goes on by itself at the first run, the way
# notifications do (keep.py); the newer reading wins for the account in use; and the page and the menu
# bar say "now" for it. Fakes only: every settings file, the managed-settings folder and the live folder are throwaway
# ones. No notification is shown and nothing runs claude.
import io
import json
import os
import shutil
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import core, demo, engine, history, keep, menubar, server, term
from switchboard import notify_hooks as nh, statusline as sl
import test_app  # its headless-Chrome helper
import test_menubar  # its made-up page state
import test_notify_hooks  # its throwaway Claude Code folder and managed-settings folder
import test_term  # its stand-in accounts and terminal

MINE = '{\n  "statusLine": {\n    "type": "command",\n    "command": "~/.claude/my-line.sh"\n  }\n}\n'
ON_SAID = 'Status line in Claude Code: on'
THEIRS_SAID = 'You already have a status line in Claude Code, so Switchboard left it.'
THEIRS_LINE = THEIRS_SAID + " To use Switchboard's instead: switchboard statusline on --replace"
# The first run's step 4, with the status line and notifications on (firstrun.where); "done" after a long turn is
# its own setting, off on a new install, so the line names "needs you" only.
STEP_4_BOTH = '  Claude Code   a status line with the account and its limits, and a notification when it needs you\n'
H, D = 3600, 86400


def in_thread(target, *args, **kwargs):
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


def history_lines():
    try:
        with open(history.path(), encoding='utf-8') as f:
            return [json.loads(x) for x in f if x.strip()]
    except OSError:
        return []


# ---------- 1. the status line on by itself, once, at the first run ----------

class StatusLineByItself(test_notify_hooks.Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        keep.reset()
        self.addCleanup(keep.reset)

    def ours(self):
        """Switchboard's status line is in the throwaway Claude Code settings file."""
        try:
            return sl.is_ours(json.loads(self.read()).get('statusLine'))
        except (OSError, ValueError):
            return False

    def test_a_fresh_install_turns_it_on_once_and_the_history_says_so(self):
        self.assertEqual(keep.statusline_by_itself(now=self.now), 'on')
        self.assertTrue(self.ours())
        self.assertEqual(server.statusline_status(), {'on': True, 'theirs': False})
        self.assertIs(server.mac_facts()['statusline'], True)                 # the page's switch shows on
        s = core.settings()
        self.assertEqual(s.get('statusline_by_itself'), self.now)
        self.assertNotIn('statusline_set_by_you', s)
        last = history_lines()[-1]
        self.assertEqual((last['kind'], last['text'], last['by'], last['reason']),
                         ('setting', ON_SAID, 'auto', 'first run'))
        before, n = self.read(), len(history_lines())
        self.assertIsNone(keep.statusline_by_itself())                       # once
        self.assertEqual((self.read(), len(history_lines())), (before, n))

    def test_once_even_when_it_was_taken_out_by_hand(self):
        self.assertEqual(keep.statusline_by_itself(), 'on')
        os.remove(self.path)
        self.assertIsNone(keep.statusline_by_itself())
        self.assertFalse(os.path.exists(self.path))

    def test_never_once_you_set_it(self):
        os.makedirs(self.data, exist_ok=True)
        with open(os.path.join(self.data, 'settings.json'), 'w') as f:
            json.dump({'statusline_set_by_you': True}, f)
        self.assertIsNone(keep.statusline_by_itself())
        self.assertFalse(os.path.exists(self.path))
        self.assertNotIn('statusline_by_itself', core.settings())

    def test_an_install_record_with_no_installs_counts_as_your_choice(self):
        """Turned on, then off, with the page's switch: its install record is what is left of it."""
        os.makedirs(self.data, exist_ok=True)
        with open(sl.record_path(), 'w') as f:
            json.dump({'v': 1, 'installs': {}}, f)
        self.assertIsNone(keep.statusline_by_itself())
        self.assertFalse(os.path.exists(self.path))

    def test_never_over_a_status_line_of_your_own_and_one_line_says_so(self):
        for text in (MINE, '{\n  "statusLine": null\n}\n'):
            with self.subTest(text):
                self.write(text)
                keep.reset()
                self.assertEqual(keep.statusline_by_itself(), 'theirs')
                self.assertEqual(self.read(), text)                          # byte for byte
                self.assertEqual(sl._records(), {})
                self.assertFalse(os.path.exists(sl.backup_path(self.path)))
                self.assertIs(server.mac_facts()['statusline_theirs'], True)
        said = [x for x in history_lines() if x['text'] == THEIRS_SAID]
        self.assertEqual([(x['kind'], x['by'], x['reason']) for x in said], [('setting', 'auto', 'first run')])   # once
        self.assertNotIn('statusline_by_itself', core.settings())

    def test_not_when_claude_codes_settings_file_cant_be_read(self):
        for text in ('{"model": "opus",}\n', '[]\n', '{"statusLine": 1, "statusLine": 2}\n'):
            with self.subTest(text):
                self.write(text)
                self.assertIsNone(keep.statusline_by_itself())
                self.assertEqual(self.read(), text)
        with open(self.path, 'wb') as f:
            f.write(b'{"model": "\xff"}\n')                                  # not UTF-8
        self.assertIsNone(keep.statusline_by_itself())
        self.write('{}\n', mode=0o000)                                       # can't be opened
        self.addCleanup(os.chmod, self.path, 0o644)
        self.assertIsNone(keep.statusline_by_itself())
        os.chmod(self.path, 0o644)
        self.assertEqual(self.read(), '{}\n')
        self.assertEqual((core.settings().get('statusline_by_itself'), history_lines()), (None, []))

    def test_not_when_claude_code_would_not_run_it(self):
        """disableAllHooks turns off a custom status line; under managed allowManagedHooksOnly, or with a status line
        in the managed settings, only the managed one runs (Claude Code's settings reference, statusLine's scope)."""
        text = '{\n  "disableAllHooks": true\n}\n'
        self.write(text)
        self.assertIsNone(keep.statusline_by_itself())
        self.assertEqual(self.read(), text)
        os.remove(self.path)
        managed = os.path.join(self.managed, 'managed-settings.json')
        for doc in ({'allowManagedHooksOnly': True}, {'disableAllHooks': True},
                    {'statusLine': {'type': 'command', 'command': '/opt/company/line.sh'}}):
            with self.subTest(doc):
                with open(managed, 'w') as f:
                    json.dump(doc, f)
                self.assertIsNone(keep.statusline_by_itself())
                self.assertFalse(os.path.exists(self.path))
        self.assertEqual((core.settings().get('statusline_by_itself'), history_lines()), (None, []))
        os.remove(managed)
        self.assertEqual(keep.statusline_by_itself(), 'on')                  # nothing stops it any more

    def test_not_without_claude_codes_own_folder(self):
        os.rmdir(self.claude)
        self.assertIsNone(keep.statusline_by_itself())
        self.assertFalse(os.path.exists(self.claude))                        # nothing is made for it

    def test_not_in_the_demo(self):
        core.DEMO = True
        self.assertIsNone(keep.statusline_by_itself())
        self.assertFalse(os.path.exists(self.path))

    def test_when_it_cant_go_in_nothing_changes_and_nothing_is_said(self):
        def refuse(plan):
            raise sl.PlanError("Switchboard can't write beside the settings file (Read-only file system). Nothing was "
                               "changed.")
        self.patch(sl, 'apply', refuse)
        self.assertIsNone(keep.statusline_by_itself())
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual((core.settings().get('statusline_by_itself'), history_lines()), (None, []))

    def test_a_note_that_cannot_be_written_never_stops_the_first_run(self):
        real = core.set_setting

        def set_setting(key, value):
            if key == 'statusline_by_itself':
                raise OSError(28, 'No space left on device')
            return real(key, value)
        self.patch(core, 'set_setting', set_setting)
        self.assertEqual(keep.statusline_by_itself(), 'on')
        self.assertTrue(self.ours())

    def test_yours_is_still_said_when_its_note_cannot_be_written(self):
        self.write(MINE)
        real = core.set_setting

        def set_setting(key, value):
            if key == 'statusline_theirs_said':
                raise OSError(28, 'No space left on device')
            return real(key, value)
        self.patch(core, 'set_setting', set_setting)
        self.assertEqual(keep.statusline_by_itself(), 'theirs')              # the terminal still says its line
        self.assertEqual(self.read(), MINE)


# ---------- 2. the person's switch makes it theirs, and the two take turns ----------

class StatusLineChoiceStaysYours(test_notify_hooks.SwitchBase):
    """keep.statusline_by_itself runs at the end of a first run; the person's on or off comes from the page's switch
    (server.act) or `switchboard statusline on|off`. Each race test puts the other side exactly in the window."""

    def setUp(self):
        super().setUp()
        keep.reset()
        self.addCleanup(keep.reset)

    def has_ours(self):
        return os.path.exists(self.path) and sl.is_ours(json.loads(self.read().decode('utf-8')).get('statusLine'))

    def test_the_switch_and_switchboard_statusline_make_it_yours(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(['statusline', 'off']), 0)
        self.assertIs(core.settings().get('statusline_set_by_you'), True)
        self.assertIsNone(keep.statusline_by_itself())                       # off stays off
        self.assertFalse(os.path.exists(self.path))
        core.set_setting('statusline_set_by_you', None)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['statusline', 'on']), 0)
        self.assertIs(core.settings().get('statusline_set_by_you'), True)
        self.assertTrue(self.has_ours())
        core.set_setting('statusline_set_by_you', None)
        server.act('/api/statusline', {'on': False})                         # the page's switch
        self.assertIs(core.settings().get('statusline_set_by_you'), True)
        self.assertNotIn('statusline_by_itself', core.settings())
        self.assertIsNone(keep.statusline_by_itself())
        self.assertFalse(os.path.exists(self.path))

    def test_off_while_it_goes_on_by_itself_stays_off(self):
        person, real = [], server.statusline_plan

        def plan(*a, **k):                                     # the first run has looked at the settings: off now
            if not person:
                person.append(in_thread(server.act, '/api/statusline', {'on': False}))
                person[0].join(1.0)
            return real(*a, **k)
        self.patch(server, 'statusline_plan', plan)
        keep.statusline_by_itself()
        person[0].join(10)
        self.assertFalse(person[0].is_alive())
        self.assertFalse(self.has_ours())                      # the person's off is what holds
        self.assertIs(core.settings().get('statusline_set_by_you'), True)
        self.assertIsNone(keep.statusline_by_itself())
        self.assertFalse(self.has_ours())

    def test_a_first_run_between_the_change_and_the_mark_does_not_put_it_back(self):
        server.statusline_turn(True)                           # on, by an earlier switch: the person turns it off
        self.assertTrue(self.has_ours())
        first, real = [], server.statusline_turn

        def turn(on, *a, **k):                                 # it is out: a first run ends now
            said = real(on, *a, **k)
            if not on and not first:
                first.append(in_thread(keep.statusline_by_itself))
                first[0].join(1.0)
            return said
        self.patch(server, 'statusline_turn', turn)
        server.act('/api/statusline', {'on': False})
        first[0].join(10)
        self.assertFalse(first[0].is_alive())
        self.assertFalse(self.has_ours())
        self.assertIs(core.settings().get('statusline_set_by_you'), True)

    def test_a_switch_that_fails_is_still_yours(self):
        def refuse(on, *a, **k):
            raise core.SwitchError("Claude Code's settings file is set read-only, so Switchboard leaves it alone.")
        with mock.patch.object(server, 'statusline_turn', refuse):
            with self.assertRaises(core.SwitchError):
                server.act('/api/statusline', {'on': False})
        self.assertIs(core.settings().get('statusline_set_by_you'), True)
        self.assertIsNone(keep.statusline_by_itself())         # off, as the person asked, even though it failed
        self.assertFalse(self.has_ours())


# ---------- 3. the first runs: the terminal's and the page's ----------

class TerminalFirstRun(test_term.Sandbox):
    """`switchboard setup` (the installer ends with it) with the stand-in accounts (none kept yet)."""

    def setUp(self):
        super().setUp()
        self.signed_in(test_term.A, 'org-a')
        self.path = os.path.join(self.home, '.claude', 'settings.json')

    def settings(self):
        with open(self.path) as f:
            return json.load(f)

    def test_setup_turns_it_on_and_says_so_with_notifications(self):
        code, out = self.run_cli('setup', tty=test_term.FakeTty(['s']))
        self.assertEqual(code, 0)
        self.assertIn(STEP_4_BOTH, out)
        self.assertTrue(sl.is_ours(self.settings()['statusLine']))
        self.assertEqual(json.dumps(self.settings()).count(nh.MARK), 3)      # the hooks beside it
        code, again = self.run_cli('setup', tty=test_term.FakeTty(['s']))   # the first run again: done once, still on
        # It says all the same, except what it changed in Claude Code's settings: this time, nothing. The routing
        # step says that of its own key in its own words: the change with its undo the first time (its lines run to
        # the step's end), only the list's own sentence the second.
        from switchboard.router import wire_words as rw
        lines = out.split('\n')
        at = next(i for i, x in enumerate(lines) if x.startswith("  Changed in Claude Code's settings: status line"))
        lines = lines[:at] + lines[lines.index('', at):]
        added = next(i for i, x in enumerate(lines) if x.startswith('  ' + rw.BRIEF_PLUGIN.split(' (undo')[0]))
        del lines[added:lines.index('', added)]
        self.assertEqual((code, again), (0, '\n'.join(lines)))

    def test_without_a_terminal_its_one_line_says_so(self):
        self.assertEqual(self.run_cli('setup'), (0, 'Claude Code is signed in as ana@work.example. Kept.\n'
                                                    'Notifications and the status line are on.\n'
                                                    'Add your other accounts at a terminal with switchboard setup.\n'))

    def test_a_status_line_of_your_own_stays_and_one_line_says_so(self):
        with open(self.path, 'w') as f:
            f.write(MINE)
        code, out = self.run_cli('setup', tty=test_term.FakeTty(['s']))
        self.assertEqual(code, 0)
        self.assertIn('  Claude Code   a notification when it needs you\n', out)
        self.assertIn(' '.join(THEIRS_LINE.split()), ' '.join(out.split()))
        self.assertEqual(self.settings()['statusLine'], json.loads(MINE)['statusLine'])
        code, out = self.run_cli('setup')                                    # without a terminal: in its three lines
        self.assertEqual(out, 'Claude Code is signed in as ana@work.example. Kept.\nNotifications are on. %s\n'
                              'Add your other accounts at a terminal with switchboard setup.\n' % THEIRS_LINE)

    def test_one_you_turned_off_stays_off(self):
        self.run_cli('statusline', 'off')
        code, out = self.run_cli('setup')
        self.assertEqual(out.splitlines()[1], 'Notifications are on.')
        self.assertNotIn('statusLine', self.settings())


class PageFirstRun(test_notify_hooks.Base):
    """The page's first run ends when its tick finds an account held after a look that found none."""

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False
        keep.reset()
        self.addCleanup(keep.reset)
        for name in ('look', 'auto_by_itself'):                             # the tick's other steps: not these tests'
            self.patch(keep, name, lambda *a, **k: None)

    def held(self):
        return [engine._row(id='acct-1', number=1, email='maya@northwind.example')]

    def test_from_none_to_one_account_turns_it_on_once(self):
        server.keep_tick({'rows': [], 'repair': None})
        self.assertFalse(os.path.exists(self.path))
        server.keep_tick({'rows': self.held(), 'repair': None})
        self.assertTrue(sl.is_ours(json.loads(self.read())['statusLine']))
        self.assertEqual(self.read().count(nh.MARK), 3)                      # notifications too
        self.assertEqual([x['text'] for x in history_lines()].count(ON_SAID), 1)
        os.remove(self.path)                                                 # taken out by hand, then a first run again
        server.keep_tick({'rows': [], 'repair': None})
        server.keep_tick({'rows': self.held(), 'repair': None})
        self.assertFalse(os.path.exists(self.path))

    def test_a_status_line_of_your_own_is_kept_and_said_once(self):
        self.write(MINE)
        for _ in range(2):
            server.keep_tick({'rows': [], 'repair': None})
            server.keep_tick({'rows': self.held(), 'repair': None})
        self.assertEqual(json.loads(self.read())['statusLine'], json.loads(MINE)['statusLine'])
        self.assertEqual([x['text'] for x in history_lines()].count(THEIRS_SAID), 1)


# ---------- 4. the uninstaller puts back exactly what was there ----------

class Uninstall(test_notify_hooks.SwitchBase):
    def setUp(self):
        super().setUp()
        keep.reset()
        self.addCleanup(keep.reset)

    def first_run(self):
        """What a first run turns on by itself, in its order (keep.first_run_tick, cli.ended)."""
        keep.notify_by_itself()
        return keep.statusline_by_itself()

    def tidy(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.tidy()
        return code, out.getvalue().splitlines()

    def test_every_byte_comes_back(self):
        for before in (None, '{\n  "model": "opus"\n}\n', '{}\n', '{\n}\n', '{"env": {"A": "1"}}'):
            with self.subTest(before=before):
                if before is not None:
                    with open(self.path, 'w') as f:
                        f.write(before)
                self.assertEqual(self.first_run(), 'on')
                after = json.loads(self.read().decode('utf-8'))
                self.assertTrue(sl.is_ours(after['statusLine']))
                self.assertEqual(json.dumps(after).count(nh.MARK), 3)
                code, said = self.tidy()
                self.assertEqual(code, 0, said)
                self.assertIn('Status line: put back as it was.', said)
                self.assertIn('Notification hooks: taken out.', said)
                if before is None:
                    self.assertFalse(os.path.exists(self.path))              # Switchboard made the file: it goes
                else:
                    self.assertEqual(self.read().decode('utf-8'), before)
                    os.remove(self.path)
                self.assertFalse(os.path.exists(sl.backup_path(self.path)))

    def test_an_install_again_puts_it_back_at_its_first_run(self):
        self.assertEqual(self.first_run(), 'on')
        self.tidy()
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(keep.statusline_by_itself(), 'on')                  # the next install's first run
        self.assertTrue(sl.is_ours(json.loads(self.read().decode('utf-8'))['statusLine']))

    def test_once_you_set_it_an_install_again_leaves_it_to_you(self):
        server.act('/api/statusline', {'on': True})
        self.tidy()
        self.assertIsNone(keep.statusline_by_itself())
        self.assertFalse(os.path.exists(self.path))

    def test_your_own_status_line_is_never_touched(self):
        with open(self.path, 'w') as f:
            f.write(MINE)
        self.assertEqual(self.first_run(), 'theirs')
        self.tidy()
        self.assertEqual(self.read().decode('utf-8'), MINE)


# ---------- 5. the newer reading wins for the account in use, and never crosses accounts ----------

A, B = 'maya@northwind.example', 'maya@ivy.example'


class LiveBase(test_app.Sandbox):
    """A in use, B beside it, and the status line's live records (made by statusline.live_record, as a Claude Code run
    makes them)."""

    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.wa, self.wb, self.fa = self.now + 3 * D, self.now + 4 * D, self.now + 2 * H

    def state(self, age, switched=3 * H):
        """A in use, its engine reading `age` seconds old; B beside it, read a minute ago."""
        st = core.blank_state()
        a = core.ensure_account(st, A)
        a.update(connected=True, slot=1, usage_status='ok', usage_error=None, note=None, usage_at=self.now - age,
                 usage={'session': {'pct': 10.0, 'resets': server.iso(self.fa), 'pace': None, 'lasts': None},
                        'weekly': {'pct': 30.0, 'resets': server.iso(self.wa), 'pace': None, 'lasts': None},
                        'scoped': [], 'spend': None})
        b = core.ensure_account(st, B)
        b.update(connected=True, slot=2, usage_status='ok', usage_at=self.now - 60,
                 usage={'session': None, 'weekly': {'pct': 5.0, 'resets': server.iso(self.wb), 'pace': None,
                                                    'lasts': None}, 'scoped': [], 'spend': None})
        st['active'] = A
        st['timeline'] = [[B, self.now - D], [A, self.now - switched]]
        return st

    def live(self, sid='s1', age=10, week=55.0, five=20.0, wreset=None, freset=None, account=A, env=None):
        """One live record; five=None: Claude Code sent no 5-hour window."""
        rl = {'seven_day': {'used_percentage': week, 'resets_at': self.wa if wreset is None else wreset}}
        if five is not None:
            rl['five_hour'] = {'used_percentage': five, 'resets_at': self.fa if freset is None else freset}
        rec = sl.live_record({'session_id': sid, 'rate_limits': rl}, {'active': account}, self.now - age,
                             env={} if env is None else env)
        os.makedirs(sl.live_dir(), exist_ok=True)
        with open(os.path.join(sl.live_dir(), sid + '.json'), 'w') as f:
            json.dump(rec, f)
        return rec

    def row(self, email, number, active, age, five=None, week=None):
        """One account as engine.listing describes it: five/week = (pct, resets_at) or None, read `age` s ago."""
        def win(w):
            return None if w is None else {'pct': w[0], 'resets': server.iso(w[1]), 'pace': None, 'lasts': None}
        return engine._row(id='acct-%d' % number, number=number, email=email, active=active, source='endpoint',
                           usage={'session': win(five), 'weekly': win(week), 'scoped': [], 'spend': None},
                           usage_at=self.now - age)

    def numbers(self, st, email=A):
        u = st['accounts'][email]['usage'] or {}
        return (u.get('session') or {}).get('pct'), (u.get('weekly') or {}).get('pct')


class NewerReadingWins(LiveBase):
    """server.apply_live_numbers: the status line's live record and the engine's reading of the account in use; the
    newer one is shown. Only a record from a Claude Code on the default profile, written after the last switch had
    settled, labelled with this account (or with none) and in this account's windows counts."""

    def test_a_live_number_seconds_old_beats_an_engine_reading_minutes_old(self):
        for engine_age in (120, 300, 590):
            with self.subTest(engine_age=engine_age):
                st = self.state(age=engine_age)
                rec = self.live(age=10)
                server.apply_live_numbers(st, self.now)
                self.assertEqual(self.numbers(st), (20.0, 55.0))
                self.assertEqual(st['accounts'][A]['usage_at'], rec['at'])
                self.assertEqual(self.numbers(st, B), (None, 5.0))            # only the account in use

    def test_an_engine_reading_newer_than_the_live_one_stays(self):
        st = self.state(age=5)
        self.live(age=60)
        server.apply_live_numbers(st, self.now)
        self.assertEqual(self.numbers(st), (10.0, 30.0))
        self.assertNotIn('live', st['accounts'][A])

    def test_the_engines_next_reading_takes_over_once_it_is_newer(self):
        st = self.state(age=200)
        self.live(age=30)
        server.apply_live_numbers(st, self.now)
        self.assertEqual(self.numbers(st), (20.0, 55.0))
        rows = [self.row(A, 1, True, 2, five=(21.0, self.fa), week=(56.0, self.wa)),
                self.row(B, 2, False, 60, week=(5.0, self.wb))]
        server.apply_rows(st, rows)                                          # the engine read A two seconds ago
        server.apply_live_numbers(st, self.now)
        self.assertEqual(self.numbers(st), (21.0, 56.0))
        self.assertNotIn('live', st['accounts'][A])

    def test_newer_never_means_another_accounts(self):
        own = {'CLAUDE_CONFIG_DIR': os.path.join(self.tmp, 'x')}
        cases = {'from a Claude Code on a profile of its own': dict(env=own),
                 'labelled with another account': dict(account=B),
                 "in another account's weekly window": dict(wreset=self.wb + 30),
                 "not this account's open weekly window": dict(wreset=self.wa - 2 * D)}
        for why, kw in cases.items():
            with self.subTest(why):
                shutil.rmtree(sl.live_dir(), True)
                st = self.state(age=120)
                self.live(age=5, **kw)
                server.apply_live_numbers(st, self.now)
                self.assertEqual(self.numbers(st), (10.0, 30.0), why)
                self.assertNotIn('live', st['accounts'][A])
        for why, switched in (('from before the switch', 60), ('while the switch settles', 20)):
            with self.subTest(why):
                shutil.rmtree(sl.live_dir(), True)
                st = self.state(age=120, switched=switched)
                self.live(age=max(0, switched - 10) if why == 'while the switch settles' else switched + 30)
                server.apply_live_numbers(st, self.now)
                self.assertEqual(self.numbers(st), (10.0, 30.0), why)

    def test_a_switch_between_two_ticks_takes_nothing_across(self):
        """The tick gave A Claude Code's numbers; then the page switches to B. Until the engine reads again, B shows
        its own reading and A its engine reading: neither gets the other's. A's engine reading is what the engine's
        store holds for it, which the tick fed with A's own numbers from Claude Code (55%), never B's (56%)."""
        self.rows = [self.row(A, 1, True, 200, five=(10.0, self.fa), week=(30.0, self.wa)),
                     self.row(B, 2, False, 60, week=(5.0, self.wb))]
        with core.locked() as st:
            st['timeline'] = [[A, self.now - D]]
            st['active'] = A
        self.live(age=10)
        server.tick()
        self.assertEqual(self.numbers(core.load()), (20.0, 55.0))
        self.patch(engine, 'switch', lambda *a, **k: None)
        server.switch_to(B)
        self.live(sid='s2', age=0, week=56.0, account=B)                     # A's numbers, now labelled B
        v = server.view()
        got = dict((x['email'], x) for x in v['accounts'])
        self.assertEqual((got[B]['usage']['weekly']['pct'], got[B]['live']), (5.0, False))
        self.assertEqual((got[A]['usage']['weekly']['pct'], got[A]['live']), (55.0, False))

    def test_the_record_the_tick_fed_the_engine_still_says_now_between_ticks(self):
        """The tick hands Claude Code's record to the engine (feed_live), and every look between ticks takes the
        engine's store: the store then holds that very record, at its own time. It is still Claude Code's own numbers,
        so the account in use still says "now" for its minute, not "checked"."""
        self.rows = [self.row(A, 1, True, 200, five=(10.0, self.fa), week=(30.0, self.wa))]
        with core.locked() as st:
            st['timeline'] = [[A, self.now - D]]
            st['active'] = A
        self.live(age=10)
        server.tick()
        got = dict((x['email'], x) for x in server.view()['accounts'])
        self.assertEqual((got[A]['usage']['weekly']['pct'], got[A]['live']), (55.0, True))

    def test_the_page_gets_them_between_ticks(self):
        """GET /api/state (the page every 5 s, the menu bar every 15 s) takes the newest record, not only the tick."""
        self.rows = [self.row(A, 1, True, 100, five=(10.0, self.fa), week=(30.0, self.wa))]
        with core.locked() as st:
            st['timeline'] = [[A, self.now - D]]
            st['active'] = A
        server.tick()
        self.assertEqual(self.numbers(core.load()), (10.0, 30.0))
        self.live(age=0, week=57.0)                                          # Claude Code answered just now
        a = next(x for x in server.view()['accounts'] if x['email'] == A)
        self.assertEqual((a['usage']['weekly']['pct'], a['live']), (57.0, True))
        self.assertEqual(self.numbers(core.load()), (10.0, 30.0))            # the saved state is the tick's


class AWindowThatRefilled(LiveBase):
    """With the newer reading winning, a record from before a window's reset is often read after it. That window has
    refilled: 0%, its reset passed, which the page, the menu bar and the terminal all draw as not started. It is never
    left out: a window that is not there means the account has no such limit (an Ultra account's 5-hour one), and a
    Team account in use would lose its 5-hour row."""

    def test_a_window_whose_reset_passed_reads_zero_and_not_started(self):
        st = self.state(age=300)
        st['accounts'][A]['usage']['session'].update(pct=40.0, resets=server.iso(self.now - 30))
        self.live(age=120, five=40.0, freset=self.now - 30)              # read before the 5-hour reset, 30 s ago
        server.apply_live_numbers(st, self.now)
        u = st['accounts'][A]['usage']
        self.assertEqual((u['session']['pct'], u['session']['resets']), (0.0, server.iso(self.now - 30)))
        self.assertEqual(u['weekly']['pct'], 55.0)
        self.assertEqual(term.window(u['session'], self.now, 90, '5-hour')['words'], 'not started')
        five = menubar._limit('five', '5-hour', menubar._window(u, 'session'), 90)
        self.assertEqual((menubar.pct_text(five), five['over']), ('0%', False))      # the menu bar's bar: 0%

    def test_a_window_the_record_does_not_have(self):
        """Claude Code may send one window without the other, and drops one once its reset passes."""
        cases = (('the engine has it open: it stays', (12.0, self.fa), (12.0, server.iso(self.fa))),
                 ('the engine has it, refilled since', (12.0, self.now - 60), (0.0, server.iso(self.now - 60))),
                 ('neither has one: no 5-hour limit', None, None))
        for why, read, want in cases:
            with self.subTest(why):
                shutil.rmtree(sl.live_dir(), True)
                st = self.state(age=300)
                if read is None:
                    st['accounts'][A]['usage']['session'] = None
                else:
                    st['accounts'][A]['usage']['session'].update(pct=read[0], resets=server.iso(read[1]))
                self.live(age=10, five=None)
                server.apply_live_numbers(st, self.now)
                u = st['accounts'][A]['usage']
                self.assertEqual(u['weekly']['pct'], 55.0, why)                 # the record's, the newer
                self.assertEqual(None if u['session'] is None else (u['session']['pct'], u['session']['resets']),
                                 want, why)

    def test_a_record_with_nothing_still_open_is_not_used(self):
        """As before: what tells whose numbers a record holds is a window still open, so one whose every window has
        refilled is passed over."""
        st = self.state(age=300)
        self.live(age=10, freset=self.now - 60, wreset=self.now - 30)
        server.apply_live_numbers(st, self.now)
        self.assertNotIn('live', st['accounts'][A])
        self.assertEqual(self.numbers(st), (10.0, 30.0))


# ---------- 6. "now" for the account in use: the server's word, the page's and the menu bar's ----------

class NowForTheAccountInUse(LiveBase):
    """Only the account in use, only on Claude Code's own numbers, only while they are under a minute old."""

    def test_the_server_marks_it(self):
        facts = {'profiles': {}, 'browser': {'name': 'Google Chrome', 'present': False, 'tabs_blocked': False},
                 'engine': {'installed': True, 'claude': True}, 'links': {'installed': False, 'on': False},
                 'auto': dict(self.auto), 'setup_hidden': True, 'now': self.now, 'demo': False}
        for engine_age, live_age, want in ((300, 5, True), (300, 59, True), (300, 90, False), (5, 30, False)):
            with self.subTest(engine_age=engine_age, live_age=live_age):
                shutil.rmtree(sl.live_dir(), True)
                st = self.state(age=engine_age)
                self.live(age=live_age)
                server.apply_live_numbers(st, self.now)
                got = dict((x['email'], x['live']) for x in server.render(st, facts)['accounts'])
                self.assertEqual(got, {A: want, B: False})

    def test_the_menu_bar_row_is_the_same_on_claude_codes_own_numbers(self):
        # "now" is said on the page and in the terminal; the menu bar's row is its email, one word and its bars.
        state = test_menubar.view()
        state['accounts'][0]['live'] = True                                  # ana@mail.example, in use
        marks = dict((r['email'], r['marks']) for r in menubar.rows(state, test_menubar.TZ))
        self.assertEqual(marks['ana@mail.example'], [('In use', 'in-use')])
        row = next(r for r in menubar.rows(state, test_menubar.TZ) if r['email'] == 'ana@mail.example')
        # ana@mail.example has no 5-hour window (Ultra): its row says it has no 5-hour limit
        self.assertEqual(menubar.row_label(row), 'ana@mail.example, in use, no 5-hour limit, weekly 27%')
        state['accounts'][0]['live'] = False
        self.assertEqual(dict((r['email'], r['marks']) for r in menubar.rows(state, test_menubar.TZ)), marks)


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class NowInThePage(test_app.ChromePage):
    def test_the_account_in_use_says_now_and_the_others_their_readings_age(self):
        v = demo.view()
        now, active = time.time(), v['active']
        other = next(a['email'] for a in v['accounts'] if a['email'] != active and not a['unmanaged'])
        st = demo.STATE['accounts']
        st[active].update(usage_at=now - 5, live={'at': now - 5, 'usage': st[active]['usage'], 'was': {}})
        st[other]['usage_at'] = now - 12 * 60
        third = next(a['email'] for a in v['accounts'] if a['email'] not in (active, other) and not a['unmanaged'])
        st[third]['usage_at'] = now - 20 * 60
        got = self.run_page("""
      await wait(300);
      done({top: $('#top .checked').textContent,
            rows: all('#acc .arow').map((b) => [b.querySelector('.em').textContent, (b.querySelector('.note[data-kind="age"]') || {}).textContent || ''])});
""")
        # the title line says the account in use's own age ("now", on Claude Code's live numbers); a row
        # says its reading's age under its numbers only when it is older than 15 minutes
        self.assertEqual(got['top'], 'now')
        meta = dict(got['rows'])
        self.assertEqual(meta[active], '')
        self.assertEqual(meta[other], '')                                   # 12 minutes: the title line covers it
        self.assertEqual(meta[third], 'checked 20 min ago')


# ---------- 7. the README says it is on by itself, and how to turn it off ----------

class Readme(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, 'README.md'), encoding='utf-8') as f:
            self.readme = f.read()

    # the README has no "What it does" list; what goes into Claude Code's settings is said under "What it
    # changes on your Mac", and "What follows a switch" comes after the first minute
    def test_the_status_line_paragraph(self):
        does = self.readme[self.readme.index('## What it changes on your Mac'):self.readme.index('## Not for you if')]
        line = next((x for x in does.splitlines() if 'status line' in x.lower() and 'by itself' in x), '')
        self.assertIn('switchboard statusline off', line)

    def test_the_first_minute_is_what_the_installer_prints(self):
        # the first run keeps the account Claude Code is on, then goes on to the others, as firstrun.run prints it
        # for a login it kept
        from switchboard import firstrun
        first = self.readme[self.readme.index('## Quick start'):self.readme.index('## What follows a switch')]
        said = '\n'.join((firstrun.TITLE, '', '1 of 4  ' + firstrun.STEP_ACCOUNT,
                          '  ' + firstrun.SIGNED_IN_KEPT % 'maya@northwind.example', '',
                          '2 of 4  ' + firstrun.STEP_OTHERS))
        self.assertIn('```\n' + said + '\n```\n', first)


if __name__ == '__main__':
    unittest.main()
