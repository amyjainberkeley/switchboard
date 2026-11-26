import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Tests for the status line.
# Every test runs with HOME, SWITCHBOARD_HOME and CLAUDE_CONFIG_DIR pointed at a
# throwaway folder, and every settings file is a temp file. Nothing here reads or
# writes the real ~/.claude or ~/.switchboard.
import io
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

from switchboard import statusline as sl

REAL_HOME = __import__('pwd').getpwuid(os.getuid()).pw_dir   # the real one: $HOME is a temp folder in tests
ESC = '\033'


def local(y, mo, d, h=0, mi=0):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


class Sandbox(unittest.TestCase):
    """A throwaway home, a fixed time zone, and the clock at Friday Nov 13 2026, 10:00 local time."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-statusline-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.home, '.switchboard')
        os.makedirs(self.data)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude'),
                                           'TZ': 'America/Los_Angeles'})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('SWITCHBOARD_STATE', None)
        os.environ.pop('NO_COLOR', None)
        time.tzset()
        self.addCleanup(time.tzset)
        self.assertTrue(sl.data_dir().startswith(self.tmp))
        self.assertTrue(sl.settings_path().startswith(self.tmp))
        self.now = local(2026, 11, 13, 10, 0)

    def state(self, **st):
        with open(os.path.join(self.data, 'statusline.json'), 'w') as f:
            json.dump(st, f)

    def owner_state(self):
        self.state(active='ana@work.example', next='ana@home.example',
                   accounts={'ana@work.example': {'alias': 'atlas'}, 'ana@home.example': {'alias': 'birch'}})

    def data_in(self, week=None, five=None, sid='sess-1', **more):
        rl = {}
        if week:
            rl['seven_day'] = {'used_percentage': week[0], 'resets_at': week[1]}
        if five:
            rl['five_hour'] = {'used_percentage': five[0], 'resets_at': five[1]}
        d = dict({'session_id': sid, 'model': {'id': 'claude-opus-5-5', 'display_name': 'Opus'}}, **more)
        if rl:
            d['rate_limits'] = rl
        return d

    def run_main(self, data, env=None, raw=None):
        out = io.StringIO()
        raw = raw if raw is not None else json.dumps(data).encode()
        code = sl.main(stdin=io.BytesIO(raw), stdout=out, env=env if env is not None else {}, now=self.now)
        self.assertEqual(code, 0)
        return out.getvalue()


class Line(Sandbox):
    def test_the_line_the_owner_liked(self):
        self.owner_state()
        sun_2pm = local(2026, 11, 15, 14, 0)
        data = self.data_in(week=(49, sun_2pm), five=(10, local(2026, 11, 13, 12, 0)))
        # The line, with each account named by its email (aliases read as made-up titles), and
        # the model Claude Code says is in use (this file's input always names Opus)
        self.assertEqual(self.run_main(data, env={'NO_COLOR': '1'}),
                         '● ana@work.example on Opus · 51% left · refills Sun 2 PM · next: ana@home.example\n')

    def test_colour_is_only_on_the_dot(self):
        self.owner_state()
        line = self.run_main(self.data_in(week=(49, local(2026, 11, 15, 14))))
        self.assertTrue(line.startswith(ESC + '[32m●' + ESC + '[0m '))
        self.assertNotIn(ESC, line[len(ESC + '[32m●' + ESC + '[0m'):])

    def test_dot_colour_follows_what_is_left(self):
        for used, colour in ((74, sl.GREEN), (75, sl.ORANGE), (89, sl.ORANGE), (90, sl.RED), (100, sl.RED)):
            line = sl.build(self.data_in(week=(used, self.now + 9999)), {}, self.now)
            self.assertTrue(line.startswith(colour + '●'), (used, line))
        self.assertTrue(sl.build({}, {}, self.now).startswith(sl.GREY + '●'))

    def test_no_color_is_respected_and_empty_no_color_is_not(self):
        self.owner_state()
        data = self.data_in(week=(10, self.now + 9999))
        self.assertNotIn(ESC, self.run_main(data, env={'NO_COLOR': '1'}))
        self.assertIn(ESC, self.run_main(data, env={'NO_COLOR': ''}))

    def test_five_hour_shows_when_it_is_the_nearer_wall(self):
        self.owner_state()
        data = self.data_in(week=(40, local(2026, 11, 15, 14)), five=(88, local(2026, 11, 13, 16, 30)))
        self.assertEqual(sl.build(data, json.load(open(os.path.join(self.data, 'statusline.json'))), self.now, colour=False),
                         '● ana@work.example on Opus · 12% left (5h) · refills 4:30 PM · next: ana@home.example')

    def test_weekly_shows_when_it_is_tighter_or_equal(self):
        for five_used in (30, 40):
            line = sl.build(self.data_in(week=(40, local(2026, 11, 15, 14)), five=(five_used, self.now + 600)),
                            {}, self.now, colour=False)
            self.assertEqual(line, '● Opus · 60% left · refills Sun 2 PM')

    def test_only_one_window(self):
        line = sl.build(self.data_in(five=(20, local(2026, 11, 13, 15))), {}, self.now, colour=False)
        self.assertEqual(line, '● Opus · 80% left (5h) · refills 3 PM')
        line = sl.build(self.data_in(week=(20, local(2026, 11, 16, 9))), {}, self.now, colour=False)
        self.assertEqual(line, '● Opus · 80% left · refills Mon 9 AM')

    def test_a_window_whose_reset_has_passed_is_not_shown(self):
        data = self.data_in(week=(40, self.now + 3600), five=(99, self.now - 1))
        self.assertEqual(sl.build(data, {}, self.now, colour=False), '● Opus · 60% left · refills 11 AM')

    def test_before_the_first_reply_there_are_no_numbers(self):
        self.owner_state()
        self.assertEqual(self.run_main(self.data_in(), env={'NO_COLOR': '1'}),
                         '● ana@work.example on Opus · next: ana@home.example\n')

    def test_no_state_file_means_no_name(self):
        line = self.run_main(self.data_in(week=(49, local(2026, 11, 15, 14))), env={'NO_COLOR': '1'})
        self.assertEqual(line, '● Opus · 51% left · refills Sun 2 PM\n')
        self.assertEqual(self.run_main({}, env={'NO_COLOR': '1'}), '●\n')

    def test_state_path_has_an_env_override(self):
        other = os.path.join(self.tmp, 'elsewhere.json')
        with open(other, 'w') as f:
            json.dump({'active': 'ana.lee@mail.example'}, f)
        os.environ['SWITCHBOARD_STATE'] = other
        self.assertEqual(sl.state_path(), other)
        self.assertEqual(self.run_main({}, env={'NO_COLOR': '1'}), '● ana.lee@mail.example\n')

    def test_next_is_left_out_when_it_is_the_same_account(self):
        self.state(active='ana@work.example', next='ana@work.example')
        self.assertEqual(self.run_main({}, env={'NO_COLOR': '1'}), '● ana@work.example\n')

    def test_percent_left_never_claims_more_than_there_is(self):
        for used, shown in ((48.6, '51%'), (0, '100%'), (0.4, '99%'), (99.9, '0%'), (100, '0%'),
                            (130, '0%'), (-5, '100%')):
            line = sl.build(self.data_in(week=(used, self.now + 600)), {}, self.now, colour=False)
            self.assertIn(' %s left' % shown, line, used)

    def test_narrow_terminals_drop_parts_from_the_right(self):
        self.owner_state()
        st = json.load(open(os.path.join(self.data, 'statusline.json')))
        data = self.data_in(week=(49, local(2026, 11, 15, 14)))
        full = '● ana@work.example on Opus · 51% left · refills Sun 2 PM · next: ana@home.example'
        self.assertEqual(sl.build(data, st, self.now, columns=len(full) + sl.MARGIN, colour=False), full)
        self.assertEqual(sl.build(data, st, self.now, columns=len(full) + 1, colour=False),
                         '● ana@work.example on Opus · 51% left · refills Sun 2 PM')
        self.assertEqual(sl.build(data, st, self.now, columns=36, colour=False), '● ana@work.example · 51% left')
        self.assertEqual(sl.build(data, st, self.now, columns=30, colour=False), '● ana@work.example')
        self.assertEqual(sl.build(data, st, self.now, columns=21, colour=False), '● ana@work.example')
        self.assertEqual(sl.build(data, st, self.now, columns=12, colour=False), '● ana@wor…')
        self.assertEqual(sl.build(data, st, self.now, columns=6, colour=False), '● a…')
        self.assertEqual(sl.build(data, st, self.now, columns=5, colour=False), '●')
        self.assertEqual(sl.build(data, st, self.now, columns=1, colour=False), '●')
        nameless = sl.build(data, {}, self.now, columns=20, colour=False)
        self.assertEqual(nameless, '● Opus · 51% left')
        self.assertEqual(self.run_main(data, env={'COLUMNS': '36', 'NO_COLOR': '1'}), '● ana@work.example · 51% left\n')
        self.assertEqual(self.run_main(data, env={'COLUMNS': 'wide', 'NO_COLOR': '1'}), full + '\n')

    def test_names_cannot_carry_terminal_codes(self):
        self.state(active='ev\x1b[2Jil\x07@y.example', accounts={'x@y.example': {'alias': 'ev\x1b[2Jil\x07'}})
        self.assertEqual(self.run_main({}, env={'NO_COLOR': '1'}), '● ev[2Jil@y.example\n')

    def test_an_account_is_named_by_its_email_as_everywhere(self):
        st = {'accounts': {'ana@home.example': {'alias': 'home'}}}
        self.assertEqual(sl.short_name(st, 'ana@work.example'), 'ana@work.example')
        self.assertEqual(sl.short_name(st, 'ana@home.example'), 'ana@home.example')      # never the alias
        self.assertEqual(sl.short_name(st, 'ana.lee@mail.example'), 'ana.lee@mail.example')
        self.assertEqual(sl.short_name(st, 'birch'), 'birch')
        self.assertEqual(sl.short_name(st, 'a' * 70 + '@x.example'), 'a' * 63 + '…')         # still cut, so nothing huge prints
        self.assertEqual(sl.short_name(st, None), '')
        self.assertEqual(sl.short_name(st, 42), '')


class RefillTimes(Sandbox):
    def test_today_this_week_and_further(self):
        self.assertEqual(sl.when(local(2026, 11, 13, 16, 30), self.now), '4:30 PM')
        self.assertEqual(sl.when(local(2026, 11, 13, 12, 0), self.now), '12 PM')
        self.assertEqual(sl.when(local(2026, 11, 14, 0, 0), self.now), 'Sat 12 AM')
        self.assertEqual(sl.when(local(2026, 11, 19, 23, 59), self.now), 'Thu 11:59 PM')
        self.assertEqual(sl.when(local(2026, 11, 20, 10, 0), self.now), 'Nov 20')

    def test_rounds_to_the_minute_and_crosses_years(self):
        self.assertEqual(sl.when(local(2026, 11, 13, 13, 59) + 45, self.now), '2 PM')
        self.assertEqual(sl.when(local(2027, 1, 2, 9, 5), local(2026, 12, 31, 22, 0)), 'Sat 9:05 AM')
        self.assertEqual(sl.when(local(2028, 3, 1, 9), local(2028, 2, 28, 9)), 'Wed 9 AM')   # 2028 is a leap year


class NeverLoud(Sandbox):
    def test_bad_input_prints_a_short_plain_line(self):
        for raw in (b'not json', b'{"a": ', b'\xff\xfe', b'{} trailing'):
            self.assertEqual(self.run_main(None, raw=raw), sl.FALLBACK + '\n')
        self.assertEqual(os.listdir(self.data), [])          # and saved nothing

    def test_odd_but_valid_input(self):
        self.assertEqual(self.run_main(None, raw=b'[1, 2]', env={'NO_COLOR': '1'}), '●\n')
        self.assertEqual(self.run_main(None, raw=b'', env={'NO_COLOR': '1'}), '●\n')
        weird = {'session_id': 's', 'rate_limits': {'seven_day': {'used_percentage': '50', 'resets_at': 1},
                                                    'five_hour': {'used_percentage': True},
                                                    'spend_limit': 'x'}}
        self.assertEqual(self.run_main(weird, env={'NO_COLOR': '1'}), '●\n')
        nan = b'{"rate_limits": {"seven_day": {"used_percentage": NaN, "resets_at": Infinity}}}'
        self.assertEqual(self.run_main(None, raw=nan, env={'NO_COLOR': '1'}), '●\n')
        self.assertEqual(self.run_main({'rate_limits': [], 'session_id': None}, env={'NO_COLOR': '1'}), '●\n')

    def test_a_broken_state_file_is_the_same_as_none(self):
        with open(os.path.join(self.data, 'statusline.json'), 'w') as f:
            f.write('{"active": ')
        self.assertEqual(self.run_main({}, env={'NO_COLOR': '1'}), '●\n')

    def test_any_surprise_inside_becomes_the_plain_line(self):
        with mock.patch.object(sl, 'build', side_effect=RuntimeError('boom')):
            self.assertEqual(self.run_main({}), sl.FALLBACK + '\n')

    def test_a_closed_pipe_is_not_an_error(self):
        class Closed(io.StringIO):
            def write(self, s):
                raise BrokenPipeError()
        with mock.patch.object(sl, '_silence_stdout') as silence:
            self.assertEqual(sl.main(stdin=io.BytesIO(b'{}'), stdout=Closed(), env={}, now=self.now), 0)
        silence.assert_called_once()

    def test_failing_to_save_does_not_change_the_line(self):
        with mock.patch.object(sl, 'save_live', side_effect=OSError('disk full')):
            line = self.run_main(self.data_in(week=(10, self.now + 600)), env={'NO_COLOR': '1'})
        self.assertEqual(line, '● Opus · 90% left · refills 10:10 AM\n')

    def test_json_reader_matches_the_json_module(self):
        for text in ('{"a": [1, 2.5, -3e2, true, false, null, "x\\u00e9\\n"], "b": {"c": {}}}', ' [] ',
                     '"str"', '12', '{"n": NaN, "i": -Infinity}', '\n{\t"k" : "v" }\r\n'):
            got, want = sl._loads(text), json.loads(text)
            self.assertEqual(json.dumps(got), json.dumps(want))
        for bad in ('', '   ', '{}{}', '{"a":1,}', "{'a': 1}", '[1 2]'):
            with self.assertRaises(ValueError):
                sl._loads(bad)

    def test_json_writer_reads_back_the_same(self):
        rec = {'v': 1, 's': 'a"b\\c\né\U0001f600', 'n': None, 't': True, 'f': False, 'x': 1794567689.858,
               'l': [1, 2.5, {'k': -0.0}], 'big': 10 ** 12}
        self.assertEqual(json.loads(sl._dumps(rec)), rec)
        for bad in (float('nan'), float('inf'), {1, 2}, object()):
            with self.assertRaises((ValueError, TypeError)):
                sl._dumps(bad)


class LiveFeed(Sandbox):
    def setUp(self):
        super().setUp()
        # The live files carry the file system's own times, and prune() drops a file dated past its clock: these
        # tests run on the real clock.
        self.now = time.time()

    def live(self, sid='sess-1'):
        with open(os.path.join(self.data, 'live', sid + '.json')) as f:
            return json.load(f)

    def test_saves_only_the_numbers(self):
        self.owner_state()
        canary = 'SECRET-PROMPT-TEXT'
        data = self.data_in(week=(41.2, 1738857600), five=(23.5, 1738425600), session_name=canary,
                            transcript_path='/x/' + canary, prompt_id=canary, cwd='/' + canary,
                            last_assistant_message=canary)
        data['rate_limits']['spend_limit'] = {'used_percentage': 62.8, 'resets_at': 1740787200, 'used_usd': 314.12,
                                              'limit_usd': 500, 'period': 'monthly', 'note': canary}
        data['rate_limits']['other'] = {'used_percentage': 1, 'text': canary}
        self.run_main(data)
        path = os.path.join(self.data, 'live', 'sess-1.json')
        with open(path) as f:
            raw = f.read()
        self.assertNotIn(canary, raw)
        self.assertEqual(json.loads(raw), {
            'v': 1, 'session_id': 'sess-1', 'at': round(self.now, 3), 'account': 'ana@work.example',
            'rate_limits': {'seven_day': {'used_percentage': 41.2, 'resets_at': 1738857600.0},
                            'five_hour': {'used_percentage': 23.5, 'resets_at': 1738425600.0},
                            'spend_limit': {'used_percentage': 62.8, 'resets_at': 1740787200.0, 'used_usd': 314.12,
                                            'limit_usd': 500.0, 'period': 'monthly'}}})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) & 0o077, 0)
        self.assertEqual([n for n in os.listdir(os.path.dirname(path)) if n.endswith('.tmp')], [])

    def test_each_run_replaces_the_file(self):
        self.run_main(self.data_in(week=(10, self.now + 600)))
        self.now += 30
        self.run_main(self.data_in(week=(11, self.now + 600)))
        rec = self.live()
        self.assertEqual(rec['rate_limits']['seven_day']['used_percentage'], 11.0)
        self.assertEqual(rec['at'], round(self.now, 3))
        self.assertIsNone(rec['account'])

    def test_nothing_saved_without_numbers_or_with_an_unsafe_session_id(self):
        self.run_main(self.data_in())
        for sid in ('../escape', '.hidden', 'a/b', '', 'x' * 129, None, 7, 'café'):
            self.run_main(self.data_in(week=(10, self.now + 600), sid=sid))
        live = os.path.join(self.data, 'live')
        self.assertFalse(os.path.exists(live) and [n for n in os.listdir(live) if n != '.pruned'])
        self.assertFalse(os.path.exists(os.path.join(self.data, 'escape.json')))

    def test_old_files_are_pruned_at_most_once_an_hour(self):
        live = os.path.join(self.data, 'live')
        os.makedirs(live)
        for name, age in (('old.json', 25 * 3600), ('stale.json.123.tmp', 30 * 3600), ('fresh.json', 3600)):
            p = os.path.join(live, name)
            open(p, 'w').close()
            os.utime(p, (self.now - age, self.now - age))
        self.run_main(self.data_in(week=(10, self.now + 600)))
        self.assertEqual(sorted(os.listdir(live)), ['.pruned', 'fresh.json', 'sess-1.json'])
        old = os.path.join(live, 'old2.json')
        open(old, 'w').close()
        os.utime(old, (self.now - 25 * 3600, self.now - 25 * 3600))
        self.now += 1800
        self.run_main(self.data_in(week=(10, self.now + 600)))
        self.assertIn('old2.json', os.listdir(live))        # not looked at again within the hour
        self.now += 1801
        self.run_main(self.data_in(week=(10, self.now + 600)))
        self.assertNotIn('old2.json', os.listdir(live))


class RealCommand(Sandbox):
    """The command exactly as it would sit in settings.json, run by a shell as Claude Code runs it."""

    def sh(self, cmd, stdin, **env):
        e = dict(os.environ, COLUMNS='200', NO_COLOR='1', **env)
        return subprocess.run(['/bin/sh', '-c', cmd], input=stdin, capture_output=True, env=e, timeout=30)

    def test_runs_from_a_shell_and_exits_0(self):
        self.owner_state()
        cmd = sl.command(python=sys.executable)
        self.assertTrue(sl.is_ours({'type': 'command', 'command': cmd}))
        data = self.data_in(week=(49, time.time() + 3 * 86400))
        r = self.sh(cmd, json.dumps(data).encode())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout.decode(), r'^● ana@work\.example on Opus · 51% left · refills \w{3} .+ · next: ana@home\.example\n$')
        self.assertEqual(r.stderr, b'')
        self.assertTrue(os.path.exists(os.path.join(self.data, 'live', 'sess-1.json')))

    def test_garbage_in_is_a_plain_line_with_no_traceback(self):
        r = self.sh(sl.command(python=sys.executable), b'\x00garbage')
        self.assertEqual((r.returncode, r.stdout.decode(), r.stderr), (0, sl.FALLBACK + '\n', b''))

    def test_a_moved_install_says_so_instead_of_failing(self):
        cmd = sl.command(python=sys.executable, script=os.path.join(self.tmp, 'gone', 'statusline.py'))
        r = self.sh(cmd, b'{}')
        self.assertEqual((r.returncode, r.stdout.decode()), (0, 'Switchboard: status line not found\n'))

    def test_a_folder_with_a_quote_in_its_name(self):
        folder = os.path.join(self.tmp, "Ana's \"stuff\" $(x)")
        os.makedirs(folder)
        shutil.copy(sl.__file__, folder)
        r = self.sh(sl.command(python=sys.executable, script=os.path.join(folder, 'statusline.py')), b'{}')
        self.assertEqual((r.returncode, r.stdout.decode(), r.stderr), (0, '●\n', b''))

    def test_the_hot_path_does_not_load_the_json_package_or_the_rest_of_switchboard(self):
        folder = os.path.dirname(os.path.abspath(sl.__file__))
        probe = ('import sys; sys.path.append(%r); import io, statusline; '
                 'statusline.main(stdin=io.BytesIO(b\'{"session_id": "p", "rate_limits": {"seven_day": '
                 '{"used_percentage": 5, "resets_at": 9999999999}}}\'), stdout=io.StringIO()); '
                 'print(sorted(m for m in sys.modules if m == "json" or m == "re" or "switchboard" in m or m == "core"))'
                 % folder)
        r = subprocess.run([sys.executable, '-I', '-S', '-c', probe], capture_output=True, timeout=30,
                           env=dict(os.environ))
        self.assertEqual(r.stdout.decode().strip(), '[]', r.stderr)


class Settings(Sandbox):
    ORIGINAL = ('{\n'
                '  "$schema": "https://json.schemastore.org/claude-code-settings.json",\n'
                '  "model": "claude-opus-5-5",\n'
                '  "permissions": {\n'
                '    "allow": ["Bash(npm test)", "Read(~/notes/**)"],\n'
                '    "deny": []\n'
                '  },\n'
                '  "env": {"A": "caf\\u00e9 é", "N": "1e3"},\n'
                '  "cleanupPeriodDays": 1e3,\n'
                '  "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}\n'
                '}\n')
    THEIRS = ('{\n  "model": "x",\n  "statusLine": { "type":"command",  "command": "~/.claude/mine.sh",'
              '"padding": 2 },\n  "theme": "dark"\n}\n')

    def setUp(self):
        super().setUp()
        self.claude = os.path.join(self.home, '.claude')
        os.makedirs(self.claude)
        self.path = os.path.join(self.claude, 'settings.json')
        self.cmd = sl.command(python='/usr/bin/python3', script='/opt/sb/switchboard/statusline.py')

    def write(self, text, mode=0o644):
        with open(self.path, 'w') as f:
            f.write(text)
        os.chmod(self.path, mode)

    def read(self):
        with open(self.path) as f:
            return f.read()

    def others(self, text):
        d = json.loads(text)
        d.pop('statusLine', None)
        return d

    def test_plans_change_nothing(self):
        self.write(self.ORIGINAL)
        before = sorted(os.listdir(self.claude)), sorted(os.listdir(self.data)), self.read()
        sl.install_plan(self.path, command_line=self.cmd)
        sl.install_plan(self.path, replace=True, command_line=self.cmd)
        sl.uninstall_plan(self.path)
        self.assertEqual((sorted(os.listdir(self.claude)), sorted(os.listdir(self.data)), self.read()), before)

    def test_install_then_uninstall_gives_back_the_same_bytes(self):
        self.write(self.ORIGINAL)
        plan = sl.install_plan(self.path, command_line=self.cmd)
        self.assertTrue(plan['ok'] and plan['change'])
        self.assertEqual((plan['had_before'], plan['after']),
                         (False, {'type': 'command', 'command': self.cmd, 'refreshInterval': 60}))   # every minute
        self.assertIn('+  "statusLine": {', plan['diff'])
        self.assertEqual([l for l in plan['diff'].splitlines() if l.startswith('-') and not l.startswith('---')],
                         ['-  "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}'])
        done = sl.apply(plan)
        after = self.read()
        self.assertTrue(after.startswith(self.ORIGINAL[:self.ORIGINAL.rindex('}\n')].rstrip('\n')))
        self.assertEqual(self.others(after), self.others(self.ORIGINAL))
        self.assertEqual(json.loads(after)['statusLine'], {'type': 'command', 'command': self.cmd, 'refreshInterval': 60})
        with open(done['backup']) as f:
            self.assertEqual(f.read(), self.ORIGINAL)
        self.assertEqual(os.path.dirname(done['backup']), self.claude)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o644)

        sl.apply(sl.uninstall_plan(self.path))
        self.assertEqual(self.read(), self.ORIGINAL)
        self.assertEqual(sl._records(), {})

    def test_someone_elses_status_line_is_kept_unless_asked(self):
        self.write(self.THEIRS)
        plan = sl.install_plan(self.path, command_line=self.cmd)
        self.assertTrue(plan['ok'])
        self.assertFalse(plan['change'])
        self.assertTrue(plan['kept_theirs'])
        self.assertIn('~/.claude/mine.sh', plan['message'])
        self.assertEqual(sl.apply(plan)['changed'], False)
        self.assertEqual(self.read(), self.THEIRS)
        self.assertEqual([n for n in os.listdir(self.claude) if 'backup' in n], [])

    def test_replace_keeps_theirs_and_uninstall_restores_it_exactly(self):
        self.write(self.THEIRS)
        plan = sl.install_plan(self.path, replace=True, command_line=self.cmd)
        self.assertTrue(plan['change'])
        self.assertEqual(plan['before'], {'type': 'command', 'command': '~/.claude/mine.sh', 'padding': 2})
        sl.apply(plan)
        rec = sl._records()[os.path.realpath(self.path)]
        self.assertEqual(rec['previous'], plan['before'])
        self.assertTrue(rec['had_previous'])
        self.assertEqual(self.others(self.read()), self.others(self.THEIRS))
        self.assertTrue(sl.is_ours(json.loads(self.read())['statusLine']))

        undo = sl.uninstall_plan(self.path)
        self.assertEqual(undo['after'], plan['before'])
        sl.apply(undo)
        self.assertEqual(self.read(), self.THEIRS)                          # byte for byte, odd spacing and all

    def test_installing_again_is_nothing_and_a_moved_install_keeps_what_was_before(self):
        self.write(self.THEIRS)
        sl.apply(sl.install_plan(self.path, replace=True, command_line=self.cmd))
        self.assertFalse(sl.install_plan(self.path, command_line=self.cmd)['change'])
        moved = sl.command(python='/usr/bin/python3', script='/elsewhere/switchboard/statusline.py')
        plan = sl.install_plan(self.path, command_line=moved)              # no replace=True needed: it is ours
        self.assertTrue(plan['change'])
        sl.apply(plan)
        self.assertEqual(json.loads(self.read())['statusLine']['command'], moved)
        sl.apply(sl.uninstall_plan(self.path))
        self.assertEqual(self.read(), self.THEIRS)

    def test_uninstall_leaves_other_status_lines_alone(self):
        self.write(self.THEIRS)
        plan = sl.uninstall_plan(self.path)
        self.assertEqual((plan['change'], plan['kept_theirs']), (False, True))
        self.write('{"model": "x"}')
        self.assertFalse(sl.uninstall_plan(self.path)['change'])
        sl.apply(sl.uninstall_plan(self.path))
        self.assertEqual(self.read(), '{"model": "x"}')

    def test_no_file_is_made_and_then_removed(self):
        plan = sl.install_plan(self.path, command_line=self.cmd)
        self.assertTrue(plan['change'])
        self.assertIsNone(sl.apply(plan)['backup'])
        self.assertEqual(json.loads(self.read()), {'statusLine': {'type': 'command', 'command': self.cmd,
                                                                  'refreshInterval': 60}})
        sl.apply(sl.uninstall_plan(self.path))
        self.assertFalse(os.path.exists(self.path))

    def test_an_empty_file_comes_back_empty(self):
        self.write('')
        sl.apply(sl.install_plan(self.path, command_line=self.cmd))
        sl.apply(sl.uninstall_plan(self.path))
        self.assertEqual(self.read(), '')

    def test_a_made_file_that_gained_other_settings_is_kept(self):
        sl.apply(sl.install_plan(self.path, command_line=self.cmd))
        d = json.loads(self.read())
        d['theme'] = 'dark'
        self.write(json.dumps(d, indent=2))
        sl.apply(sl.uninstall_plan(self.path))
        self.assertEqual(json.loads(self.read()), {'theme': 'dark'})

    def test_other_layouts_keep_their_style(self):
        for text in ('{"model": "x", "theme": "dark"}', '{\n\t"model": "x"\n}\n', '{}', '{\n}\n',
                     '  {"statusLine": {"type": "command", "command": "me"}, "a": 1}  '):
            self.write(text)
            plan = sl.install_plan(self.path, replace=True, command_line=self.cmd)
            self.assertFalse(plan['rewritten'], text)
            sl.apply(plan)
            self.assertEqual(self.others(self.read()), self.others(text))
            sl.apply(sl.uninstall_plan(self.path))
            self.assertEqual(self.read(), text)          # '{\n}\n' too: its own spacing comes back
        self.write('{\n\t"model": "x"\n}\n')
        sl.apply(sl.install_plan(self.path, command_line=self.cmd))
        self.assertIn('\n\t"statusLine": {\n\t\t"type": "command",', self.read())

    def test_broken_files_are_refused_and_left_alone(self):
        for text, why in (('{"a": 1,}', "isn't valid JSON"), ('{"a": 1} // note', "isn't valid JSON"),
                          ('[1, 2]', "doesn't hold a JSON object"), ('"x"', "doesn't hold a JSON object"),
                          ('{"statusLine": 1, "statusLine": 2}', 'statusLine twice')):
            self.write(text)
            for plan in (sl.install_plan(self.path, command_line=self.cmd), sl.uninstall_plan(self.path)):
                self.assertFalse(plan['ok'], text)
                self.assertIn(why, plan['message'])
                with self.assertRaises(sl.PlanError):
                    sl.apply(plan)
            self.assertEqual(self.read(), text)
        with open(self.path, 'wb') as f:
            f.write(b'{"a": "\xff"}')
        self.assertIn('not UTF-8', sl.install_plan(self.path)['message'])

    def test_a_file_changed_after_the_plan_is_not_overwritten(self):
        self.write(self.ORIGINAL)
        plan = sl.install_plan(self.path, command_line=self.cmd)
        changed = self.ORIGINAL.replace('claude-opus-5-5', 'claude-sonnet-5')
        self.write(changed)
        with self.assertRaises(sl.PlanError):
            sl.apply(plan)
        self.assertEqual(self.read(), changed)
        self.assertEqual(sl._records(), {})
        os.remove(self.path)
        with self.assertRaises(sl.PlanError):
            sl.apply(plan)
        self.assertFalse(os.path.exists(self.path))

    def test_a_symlinked_settings_file_stays_a_symlink(self):
        real = os.path.join(self.tmp, 'dotfiles', 'claude-settings.json')
        os.makedirs(os.path.dirname(real))
        with open(real, 'w') as f:
            f.write(self.ORIGINAL)
        os.symlink(real, self.path)
        done = sl.apply(sl.install_plan(self.path, command_line=self.cmd))
        self.assertTrue(os.path.islink(self.path))
        self.assertEqual(os.path.dirname(done['backup']), self.claude)
        with open(real) as f:
            self.assertIn('statusLine', f.read())
        sl.apply(sl.uninstall_plan(self.path))
        self.assertTrue(os.path.islink(self.path))
        with open(real) as f:
            self.assertEqual(f.read(), self.ORIGINAL)

    def test_one_private_backup_replaced_each_time(self):
        self.write(self.THEIRS)
        b1 = sl.apply(sl.install_plan(self.path, replace=True, command_line=self.cmd))['backup']
        with open(b1) as f:
            self.assertEqual(f.read(), self.THEIRS)
        b2 = sl.apply(sl.uninstall_plan(self.path))['backup']
        self.assertEqual(b1, b2)
        self.assertEqual(b1, self.path + '.switchboard-backup')
        self.assertEqual(stat.S_IMODE(os.stat(b2).st_mode), 0o600)
        with open(b2) as f:
            self.assertIn('switchboard statusline', f.read())
        self.assertEqual([n for n in os.listdir(self.claude) if 'backup' in n], ['settings.json.switchboard-backup'])

    def test_only_status_lines_that_run_this_file_count_as_ours(self):
        yes = (self.cmd, sl.command(), 'python3 /x/switchboard/statusline.py', 'python3 -m switchboard.statusline')
        no = ('~/.claude/statusline.sh', 'echo switchboard', 'python3 /x/statusline.py', "unterminated 'quote")
        for c in yes:
            self.assertTrue(sl.is_ours({'type': 'command', 'command': c}), c)
        for c in no:
            self.assertFalse(sl.is_ours({'type': 'command', 'command': c}), c)
        for v in (None, 'x', {'type': 'command'}, {'command': 5}):
            self.assertFalse(sl.is_ours(v))

    def test_the_real_settings_file_is_never_the_target(self):
        self.assertNotEqual(os.path.realpath(sl.settings_path()),
                            os.path.realpath(os.path.join(REAL_HOME, '.claude', 'settings.json')))


if __name__ == '__main__':
    unittest.main()
