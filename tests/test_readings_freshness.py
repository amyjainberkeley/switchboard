import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The status line every minute, each reading's own age, `switchboard models`, the build in the version, and two
# smaller rules: doctor's status line row and the board's top line.
#
# Status line: Switchboard's status line runs every minute ("refreshInterval": 60), even while a session is quiet.
#     On, off and uninstall stay byte for byte; a line of the person's own never gets it; a line an earlier build of
#     Switchboard put in gets it at the next setup or page start (an update restarts the page), only while it is
#     still exactly what Switchboard's record says it put there, and once. With the line run every minute, a quiet
#     session hands the same numbers again and again: its record keeps the time they came, so they never look newer
#     than they are.
# Reading age: each account's own reading age, or "now" for the account in use on Claude Code's own numbers of the
#     last minute, on the page, in the menu bar and in the terminal; never the server's tick.
# Models: `switchboard models [on|off]`: the page's "Sonnet for helpers" switch from the terminal.
# Build: the build in the version: "switchboard 0.2.0 (d635e47)", from a `git archive` download (export-subst), from
#     git in a checkout, and the version alone when neither is there.
# Doctor: the status line row beside a line of the person's own names the --replace route.
# Board: the top line wraps above 640 px, so a long email never runs past the edge.
#
# Made-up accounts only (*.example); every folder is a throwaway one; the page's port is 7982, where nothing
# listens.
import html as htmllib
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import connect, core, demo, history, lanes, linkapp, menubar, server, term
from switchboard import statusline as sl
import test_app  # its Sandbox, its models sandbox and its ChromePage: the real page in headless Chrome
import test_keep  # its Sandbox: a fake engine, for `switchboard setup`
import test_menubar  # its made-up state of the page, as the menu bar reads it
import test_cli_help_and_no_page as cli_help  # its Fresh, nothing_runs and the help
import test_cli_commands  # its Cli: a throwaway home and the fake accounts, for the doctor
import test_term  # its made-up state of the page, as the terminal reads it

PORT = '7982'                   # a port of this file's own: nothing listens on it, so the page is not running
CMD = sl.command(python='/usr/bin/python3', script='/opt/sb/switchboard/statusline.py')
ORIGINAL = '{\n  "model": "opus",\n  "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}\n}\n'
THEIRS = '{\n    "statusLine":   {"type": "command", "command": "~/.claude/mine.sh", "padding": 2},\n  "model": "x"\n}\n'


def git():
    """git, when it can run here without asking for Apple's command line tools; else None."""
    g = shutil.which('git')
    if not g:
        return None
    if os.path.realpath(g) == '/usr/bin/git' and subprocess.call(['/usr/bin/xcode-select', '-p'], stdout=subprocess.DEVNULL,
                                                                   stderr=subprocess.DEVNULL) != 0:
        return None
    return g


GIT_ENV = dict((k, v) for k, v in os.environ.items() if not k.startswith('GIT_'))


def run_git(folder, *args):
    return subprocess.run([git(), '-C', folder, '-c', 'user.name=Switchboard tests', '-c', 'user.email=tests@example.com',
                           '-c', 'commit.gpgsign=false'] + list(args), capture_output=True, check=True,
                          env=dict(GIT_ENV, HOME=folder)).stdout


# ---------- the status line every minute ----------

def older():
    """Switchboard's status line as the earlier builds put it in: its command and nothing more, through the
    same plan (install_plan never read ours() there, so this changes nothing)."""
    return mock.patch.object(sl, 'ours', lambda python=None, script=None, command_line=None:
                             {'type': 'command', 'command': command_line or sl.command(python, script)})


class EveryMinuteBase(unittest.TestCase):
    """A throwaway home and data folder, and Claude Code's settings file in its .claude."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        self.data = os.path.join(self.tmp, 'data')
        self.claude = os.path.join(self.home, '.claude')
        os.makedirs(self.claude)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data, 'CLAUDE_CONFIG_DIR': self.claude,
                                           'SWITCHBOARD_PORT': PORT})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('SWITCHBOARD_STATE', None)
        self.path = os.path.join(self.claude, 'settings.json')
        self.real = os.path.realpath(self.path)

    def write(self, text):
        if text is None:
            if os.path.exists(self.path):
                os.remove(self.path)
            return
        with open(self.path, 'w') as f:
            f.write(text)

    def read(self):
        if not os.path.exists(self.path):
            return None
        with open(self.path) as f:
            return f.read()

    def line(self):
        return json.loads(self.read())['statusLine']

    def on(self, **kw):
        return sl.apply(sl.install_plan(self.path, command_line=CMD, **kw))

    def off(self):
        return sl.apply(sl.uninstall_plan(self.path))

    def older_on(self, text=ORIGINAL):
        """Switchboard's line put in as the earlier builds put it in."""
        self.write(text)
        with older():
            self.on()
        self.assertEqual(self.line(), {'type': 'command', 'command': CMD})


class EveryMinute(EveryMinuteBase):
    def test_on_puts_in_refresh_interval_60(self):
        self.write(ORIGINAL)
        plan = sl.install_plan(self.path, command_line=CMD)
        self.assertEqual(plan['after'], {'type': 'command', 'command': CMD, 'refreshInterval': 60})
        self.on()
        self.assertEqual(self.line(), {'type': 'command', 'command': CMD, 'refreshInterval': 60})
        self.assertEqual(sl.ours(python='/usr/bin/python3', script='/opt/sb/switchboard/statusline.py'),
                         {'type': 'command', 'command': CMD, 'refreshInterval': 60})

    def test_on_then_off_byte_for_byte_with_it(self):
        for text in (ORIGINAL, '{}', '{\n}\n', None):
            with self.subTest(text=text):
                self.write(text)
                self.on()
                self.assertEqual(self.line()['refreshInterval'], 60)
                self.off()
                self.assertEqual(self.read(), text)
                self.assertEqual(sl._records(), {})

    def test_a_line_of_your_own_never_gets_it(self):
        self.write(THEIRS)
        self.assertTrue(sl.install_plan(self.path, command_line=CMD)['kept_theirs'])
        self.assertEqual(sl.every_minute(), [])
        self.assertEqual(self.read(), THEIRS)
        self.on(replace=True)                                   # asked for: Switchboard's, every minute
        self.assertEqual(self.line()['refreshInterval'], 60)
        self.off()
        self.assertEqual(self.read(), THEIRS)                    # yours back, byte for byte, nothing of ours in it

    def test_on_again_over_an_older_line_says_what_changed(self):
        self.older_on()
        plan = sl.install_plan(self.path, command_line=CMD)
        self.assertEqual((plan['change'], plan['after']), (True, {'type': 'command', 'command': CMD, 'refreshInterval': 60}))
        self.assertEqual(plan['message'], "Switchboard's status line is updated to run every minute.")
        self.on()
        self.assertFalse(sl.install_plan(self.path, command_line=CMD)['change'])          # and then it is on

    def test_a_refresh_of_your_own_on_switchboards_line_is_yours(self):
        self.on()
        d = json.loads(self.read())
        d['statusLine']['refreshInterval'] = 5
        self.write(json.dumps(d, indent=2))
        before = self.read()
        plan = sl.install_plan(self.path, command_line=CMD)
        self.assertEqual((plan['change'], plan['message']), (False, "Switchboard's status line is already on."))
        self.assertEqual(sl.every_minute(), [])
        self.assertEqual(self.read(), before)


class EveryMinuteFromBefore(EveryMinuteBase):
    """A status line Switchboard put in by an earlier build: every_minute() gives it its minute, only while it is still
    exactly what Switchboard's record says it put there, and once."""

    def test_it_gets_its_minute(self):
        self.older_on()
        self.assertEqual(sl.every_minute(), [self.real])
        self.assertEqual(self.line(), {'type': 'command', 'command': CMD, 'refreshInterval': 60})
        self.assertEqual(sl._records()[self.real]['installed'], self.line())

    def test_off_after_it_gives_back_the_file_from_before_switchboard(self):
        for text in (ORIGINAL, None, '{}', THEIRS):
            with self.subTest(text=text):
                self.write(text)
                with older():
                    self.on(replace=True)
                sl.every_minute()
                self.assertEqual(self.line()['refreshInterval'], 60)
                self.off()
                self.assertEqual(self.read(), text)

    def test_once(self):
        self.older_on()
        sl.every_minute()
        d = json.loads(self.read())
        del d['statusLine']['refreshInterval']                  # taken out by hand: yours
        self.write(json.dumps(d, indent=2))
        before = self.read()
        self.assertEqual(sl.every_minute(), [])
        self.assertEqual(self.read(), before)

    def test_not_when_it_changed_since(self):
        for change in ('padding', 'command'):
            with self.subTest(change=change):
                self.older_on()
                d = json.loads(self.read())
                if change == 'padding':
                    d['statusLine']['padding'] = 1
                else:
                    d['statusLine']['command'] = d['statusLine']['command'].replace('/usr/bin/python3', '/opt/py3')
                self.write(json.dumps(d, indent=2))
                before = self.read()
                self.assertEqual(sl.every_minute(), [])
                self.assertEqual(self.read(), before)
                self.off()

    def test_not_without_switchboards_record(self):
        self.older_on()
        os.remove(sl.record_path())
        before = self.read()
        self.assertEqual(sl.every_minute(), [])
        self.assertEqual(self.read(), before)

    def test_every_settings_file_its_record_names(self):
        other = os.path.join(self.tmp, 'other-claude')
        os.makedirs(other)
        there = os.path.join(other, 'settings.json')
        with open(there, 'w') as f:
            f.write(ORIGINAL)
        with older(), mock.patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': other}):
            sl.apply(sl.install_plan(there, command_line=CMD))      # from a terminal with a profile of its own
        self.assertEqual(sl.every_minute(), [os.path.realpath(there)])
        with open(there) as f:
            self.assertEqual(json.load(f)['statusLine']['refreshInterval'], 60)

    def test_a_file_that_cant_be_read_is_left_for_later(self):     # nothing raised, nothing written
        self.older_on()
        self.write('{not json')
        self.assertEqual(sl.every_minute(), [])
        self.assertEqual(self.read(), '{not json')


class EveryMinuteAtSetup(test_keep.Sandbox):
    def test_switchboard_setup_gives_an_older_line_its_minute(self):
        path = os.path.join(self.home, '.claude', 'settings.json')
        with open(path, 'w') as f:
            f.write(ORIGINAL)
        with older():
            sl.apply(sl.install_plan(path, command_line=CMD))
        self.signed_in(test_keep.A)
        self.assertEqual(self.run_cli('setup')[0], 0)
        with open(path) as f:
            self.assertEqual(json.load(f)['statusLine'], {'type': 'command', 'command': CMD, 'refreshInterval': 60})

    def test_models_stays_off_unless_you_turn_it_on(self):          # a first run never turns it on
        self.signed_in(test_keep.A)
        self.run_cli('setup')
        self.look(1000.0)
        path, got = os.path.join(self.home, '.claude', 'settings.json'), {}
        if os.path.exists(path):
            with open(path) as f:
                got = json.load(f)
        self.assertNotIn('CLAUDE_CODE_SUBAGENT_MODEL', got.get('env') or {})


class Stub(object):
    """Stands in for the page's HTTP server: nothing listens."""

    def __init__(self, *a, **kw):
        pass

    def serve_forever(self):
        pass


class EveryMinuteAtThePagesStart(test_app.Sandbox):
    def test_the_page_starting_after_an_update_gives_an_older_line_its_minute(self):
        os.environ['SWITCHBOARD_PORT'] = PORT
        path = os.path.join(self.claude, 'settings.json')
        with open(path, 'w') as f:
            f.write(ORIGINAL)
        with older():
            sl.apply(sl.install_plan(path, command_line=CMD))
        with mock.patch.object(server, 'ThreadingHTTPServer', Stub), mock.patch.object(server, 'loop', lambda *a, **k: None), \
                mock.patch.object(server.signal, 'signal', lambda *a: None), mock.patch.object(connect, 'sweep', lambda: None), \
                mock.patch.object(server, 'resolve_journal', lambda: None), mock.patch.object(linkapp, 'announce_port', lambda: None), \
                mock.patch.object(lanes, 'resume', lambda: None):
            server.serve()
        with open(path) as f:
            self.assertEqual(json.load(f)['statusLine'], {'type': 'command', 'command': CMD, 'refreshInterval': 60})


class EveryMinuteUninstall(test_app.Sandbox):
    def test_the_uninstaller_gives_back_the_same_bytes(self):
        path = os.path.join(self.claude, 'settings.json')
        with open(path, 'w') as f:
            f.write(ORIGINAL)
        server.act('/api/statusline', {'on': True})
        with open(path) as f:
            self.assertEqual(json.load(f)['statusLine']['refreshInterval'], 60)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.tidy(), 0)
        self.assertIn('Status line: put back as it was.', out.getvalue())
        with open(path) as f:
            self.assertEqual(f.read(), ORIGINAL)


class QuietSessions(EveryMinuteBase):
    """Run every minute, a quiet Claude Code hands the status line the same numbers again and again. Its record keeps the
    time they came, so the page never says "now" over numbers an idle session has held since its last answer. (The
    clock is the real one: the hourly prune removes a record dated far from it.)"""

    def setUp(self):
        super().setUp()
        self.T0 = float(int(time.time()))          # whole seconds: exact in a float, as the record writes it

    def run_line(self, pct, now, sid='s1'):
        data = {'session_id': sid, 'rate_limits': {'seven_day': {'used_percentage': pct, 'resets_at': self.T0 + 3 * 86400}}}
        sl.main(stdin=io.BytesIO(json.dumps(data).encode()), stdout=io.StringIO(), env={}, now=now)
        with open(os.path.join(sl.live_dir(), sid + '.json')) as f:
            return json.load(f)

    def test_the_same_numbers_again_keep_the_time_they_came(self):
        self.assertEqual(self.run_line(41.2, self.T0)['at'], self.T0)
        self.assertEqual(self.run_line(41.2, self.T0 + 60)['at'], self.T0)
        self.assertEqual(self.run_line(41.2, self.T0 + 600)['at'], self.T0)

    def test_new_numbers_are_saved_at_once(self):
        self.run_line(41.2, self.T0)
        rec = self.run_line(41.5, self.T0 + 120)
        self.assertEqual((rec['at'], rec['rate_limits']['seven_day']['used_percentage']), (self.T0 + 120, 41.5))


# ---------- each account's own age ----------

class AgeWords(unittest.TestCase):
    def test_how_old_a_reading_is(self):
        now = test_term.NOW
        self.assertEqual([term.age(now - s, now) for s in (0, 20, 40, 59, 60, 89, 150, 600, 61 * 60, 26 * 3600)],
                         ['just now', 'just now', 'just now', 'just now', '1 min ago', '1 min ago', '3 min ago',
                          '10 min ago', '1 hour ago', '1 day ago'])
        self.assertEqual([term.age(None, now), term.age(now + 30, now), term.age(now + 3, now)], ['', '', 'just now'])

    def test_now_only_for_the_account_in_use_on_claude_codes_own_numbers(self):
        now = test_term.NOW
        a = {'usage_at': now - 30, 'live': True, 'active': True}
        self.assertEqual(term.age_words(a, now), 'now')
        self.assertEqual(term.age_words(dict(a, active=False), now), 'checked just now')
        self.assertEqual(term.age_words(dict(a, live=False), now), 'checked just now')
        self.assertEqual(term.age_words({'usage_at': now - 480}, now), 'checked 8 min ago')
        self.assertEqual(term.age_words({}, now), '')


class AgeInTheTerminal(unittest.TestCase):
    def rows(self, view=test_term.VIEW):
        return [x for x in test_term.lines(view) if x[:1].isdigit()]

    def test_switchboard_shows_every_accounts_own_age(self):
        self.assertEqual(self.rows(), [
            '1  ana@northwind.example  Max 20x · Personal  checked 2 min ago',
            '2  ana@fernhill.example   Max 20x · Personal  checked 10 min ago',
            '3  ana@harbor.example     Team · Harbor       checked 5 min ago',
            '4  ana@lab.example        Max 20x · Personal  next · checked 5 min ago',
            '5  ana@mail.example       Max 20x · Personal  in use · checked 3 min ago'])

    def test_the_account_in_use_says_now_on_claude_codes_own_numbers(self):
        view = json.loads(json.dumps(test_term.VIEW))
        mail = next(a for a in view['accounts'] if a['email'] == 'ana@mail.example')
        mail.update(live=True, usage_at=test_term.NOW - 20)
        self.assertEqual(self.rows(view)[-1], '5  ana@mail.example       Max 20x · Personal  in use · now')


class NoAgeInTheMenuBar(unittest.TestCase):
    """How old each account's numbers are is said on the page and in the terminal. The menu bar's row is its email,
    one word and its bars, so it never says it, Now included, and VoiceOver reads what the row shows."""

    def state(self):
        s = test_menubar.view()
        test_menubar.find(s, 'ana@fernhill.example')['usage_at'] = test_menubar.NOW - 480
        return s

    def test_no_row_says_how_old_its_numbers_are(self):
        marks = dict((r['email'], r['marks']) for r in menubar.rows(self.state(), test_menubar.TZ))
        self.assertEqual(marks, {
            'ana@northwind.example': [('Use', 'use')],
            'ana@fernhill.example': [('Use', 'use')],
            'ana@harbor.example': [('Use', 'use')],
            'ana@lab.example': [('Next', 'next')],
            'ana@mail.example': [('In use', 'in-use')]})
        s = self.state()
        test_menubar.find(s, 'ana@mail.example')['live'] = True
        self.assertEqual(test_menubar.row_of(s, 'ana@mail.example')['marks'], [('In use', 'in-use')])

    def test_voiceover_reads_what_the_row_shows(self):
        s = self.state()
        label = lambda e: menubar.row_label(test_menubar.row_of(s, e))      # noqa: E731
        self.assertEqual(label('ana@fernhill.example'), 'Use ana@fernhill.example, weekly 94%')
        self.assertEqual(label('ana@lab.example'), 'Use ana@lab.example, next, weekly 65%')
        self.assertEqual(label('ana@mail.example'), 'ana@mail.example, in use, weekly 27%')


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class AgeInThePage(test_app.ChromePage):
    LOOK = """
      await wait(300);
      const c = $('#top .checked');
      done({checked: c.textContent, warn: c.classList.contains('warn'), h1: $('#top h1').textContent,
            metas: all('#acc .arow').map((b) => { const n = b.querySelector('.note[data-kind="age"]');   // under its numbers, when old
              return [b.querySelector('.em').textContent, n ? n.textContent : '', n ? n.title || null : null]; })});
"""

    def setUp(self):
        super().setUp()
        # The page dates every number by the view's own clock (demo.view's time.time()). Ages set from the wall clock
        # went a minute off whenever headless Chrome took over 30 s to start on a busy Mac ("checked 6 min ago" for 5
        # minutes): here the demo's clock stands still, so each age is exactly what the test
        # set, however long Chrome takes.
        self.now = time.time()
        clock = mock.patch.object(demo, 'time', mock.Mock(time=lambda: self.now, sleep=time.sleep))
        clock.start()
        self.addCleanup(clock.stop)

    def look(self):
        got = self.run_page(self.LOOK)
        got['metas'] = dict((e, (m, t)) for e, m, t in got['metas'])
        return got

    def test_the_header_says_the_account_in_uses_own_age_not_the_ticks(self):
        """The trap: the server read the engine 20 s ago, but the account in
        use's number is 8 minutes old. The header must not say "checked 3:31 PM", the tick's own time."""
        v = demo.view()
        demo.STATE['accounts'][v['active']]['usage_at'] = self.now - 8 * 60
        got = self.look()
        self.assertEqual((got['checked'], got['warn']), ('checked 8 min ago', False))

    def test_the_header_says_now_on_claude_codes_own_numbers(self):
        v = demo.view()
        now, a = self.now, demo.STATE['accounts'][v['active']]
        a.update(usage_at=now - 5, live={'at': now - 5, 'usage': a['usage'], 'was': {}})
        got = self.look()
        self.assertEqual(got['checked'], 'now')
        self.assertEqual(got['metas'][v['active']][0], '')                  # the title line says it, once

    def test_every_card_says_its_own_age(self):
        v = demo.view()
        now, st = self.now, demo.STATE['accounts']
        others = [a['email'] for a in v['accounts'] if a['email'] != v['active'] and not a['unmanaged']]
        st[others[0]]['usage_at'] = now - 12 * 60
        st[others[1]]['usage_at'] = now - 20 * 60
        got = self.look()
        # a reading older than 15 minutes says its own age under its numbers; the rest, the title line covers
        for a in v['accounts']:
            if not a['unmanaged']:
                want = 'checked 20 min ago' if a['email'] == others[1] else ''          # 12 min and the demo's 40 s: nothing
                self.assertEqual(got['metas'][a['email']][0], want, a['email'])

    def test_how_often_is_a_tooltip_true_to_claude_codes_own_numbers(self):
        # live while Claude Code's own numbers come in (the account in use's live mark); otherwise the loop's pace
        # (switchboard/accounts/usage.py) and the page's own asking
        live = ('The account in use is live while you work: its numbers come from Claude Code itself. The others are '
                'read every 5 to 15 minutes, and at once on Read again.')
        read = ('The account in use is read every 5 minutes, about every 3 while this page is open, the others every 5 '
                'to 15, and all at once on Read again. Not more often: Anthropic limits reads, so Switchboard keeps to '
                'about 20 an hour for each account.')
        for on, want in ((True, live), (False, read)):
            with self.subTest(live=on):
                demo.STATE.clear()
                demo.FACTS.clear()
                v = demo.view()
                demo.FACTS['mac']['statusline'] = True              # installed either way: what counts is what comes in
                for a in demo.STATE['accounts'].values():          # old readings, so each says its age
                    a['usage_at'] = self.now - 20 * 60
                if on:
                    a = demo.STATE['accounts'][v['active']]
                    a.update(usage_at=self.now - 5, live={'at': self.now - 5, 'usage': a['usage'], 'was': {}})
                titles = set(t for m, t in self.look()['metas'].values() if m)     # each row that says an age
                self.assertEqual(titles, {want})

    def test_with_nothing_read_for_a_while_it_still_says_how_long(self):   # as the page runs
        demo.view()
        now = self.now
        demo.STATE.update(tick_at=now - 300, ticking=False)
        for a in demo.STATE['accounts'].values():
            a['usage_at'] = now - 300                            # nothing read: every reading is as old as the tick
        got = self.look()
        self.assertEqual((got['checked'], got['warn']), ('checked 5 min ago', True))

    def test_with_no_account_in_use_the_tick_only_as_a_warning(self):
        demo.view()
        demo.STATE['active'] = None
        got = self.look()
        self.assertEqual((got['h1'], got['checked']), ('Claude Code is on none of these accounts', 'Read again'))
        demo.STATE.update(tick_at=self.now - 300, ticking=False)
        got = self.look()
        self.assertEqual((got['checked'], got['warn']), ('checked 5 min ago', True))


# ---------- `switchboard models [on|off]` ----------

MODELS_HELP = ("switchboard models [on|off] Sonnet for helpers: Claude Code's helpers that name no model run on Sonnet\n"
               + ' ' * 28 + "instead of the chat's model (one setting in its settings file; off until you turn it on)\n")


class ModelsCommandHelp(cli_help.Fresh):
    def test_the_main_help_has_its_line(self):
        self.assertIn(MODELS_HELP, inspect.cleandoc(cli.__doc__) + '\n')    # as Python 3.13 and newer keep a docstring

    def test_its_own_help_is_its_lines(self):
        with cli_help.nothing_runs() as ran:
            got = self.cli('models', '--help')
        self.assertEqual(got + (ran,), (0, MODELS_HELP, '', []))

    def test_other_words_say_how_it_is_used(self):
        for args in (('models', 'bogus'), ('models', 'on', 'off'), ('models', 'ON')):
            with self.subTest(args=args):
                self.assertEqual(self.cli(*args), (2, 'switchboard models [on|off]\n', ''))

    def test_the_demo_changes_nothing(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        self.assertEqual(self.cli('models', 'on'),
                         (1, '', 'The demo has made-up accounts; turn Sonnet for helpers on or off on its page.\n'))


class ModelsCommand(test_app.ModelsBase):
    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except core.SwitchError as e:
                err.write(str(e) + '\n')
                code = 1
        return code, out.getvalue(), err.getvalue()

    def env(self):
        return json.loads(self.read()).get('env') or {}

    def test_alone_it_says_whether_it_is_on(self):
        self.assertEqual(self.cli('models'), (0, 'Sonnet for helpers: off\n', ''))
        server.act('/api/models', {'enabled': True})
        self.assertEqual(self.cli('models'), (0, 'Sonnet for helpers: on\n', ''))

    def test_on_and_off_are_the_pages_switch(self):
        before = self.read()
        self.assertEqual(self.cli('models', 'on'), (0, 'Sonnet for helpers: on\n', ''))
        self.assertEqual(self.env().get('CLAUDE_CODE_SUBAGENT_MODEL'), 'sonnet')
        last = history.read(1)[-1]
        self.assertEqual((last['kind'], last['text'], last.get('by')), ('setting', 'Sonnet for helpers: on', 'you'))
        self.assertEqual(self.cli('models', 'off'), (0, 'Sonnet for helpers: off\n', ''))
        self.assertEqual(self.read(), before)                        # byte for byte, as the page's Off
        last = history.read(1)[-1]
        self.assertEqual((last['kind'], last['text'], last.get('by')), ('setting', 'Sonnet for helpers: off', 'you'))

    def test_open_chats_get_the_pages_note(self):
        self.transcript()                                           # a chat written seconds ago
        self.assertEqual(self.cli('models', 'on'),
                         (0, 'Sonnet for helpers: on\nOpen chats use it for their next helper.\n', ''))
        self.assertEqual(self.cli('models', 'off'),
                         (0, 'Sonnet for helpers: off\nOpen chats keep their model until they restart.\n', ''))

    def test_the_notes_are_the_pages_words(self):
        with open(os.path.join(ROOT, 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            m = re.search(r"const MODELS_NOTE = \{next: '([^']*)', restart: '([^']*)'\};", f.read())
        self.assertEqual(term.MODELS_NOTE, {'next': m.group(1), 'restart': m.group(2)})

    def test_again_changes_nothing_and_says_so(self):
        self.cli('models', 'on')
        on = self.read()
        self.assertEqual(self.cli('models', 'on'), (0, 'Sonnet for helpers: on\n', ''))
        self.assertEqual(self.read(), on)
        self.assertEqual(len([r for r in history.read() if r['text'] == 'Sonnet for helpers: on']), 1)

    def test_when_something_else_decides_it_says_what_and_changes_nothing(self):
        mine = '{"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"}}'
        with open(self.settings, 'w') as f:
            f.write(mine)
        self.assertEqual(self.cli('models'), (0, 'Sonnet for helpers: off. Already set in your user settings.\n', ''))
        self.assertEqual(self.cli('models', 'on'), (1, '', 'Already set in your user settings.\n'))
        self.assertEqual(self.read(), mine.encode())

    def test_an_old_claude_code_is_named(self):
        self.patch(server, 'MODELS_KW', dict(server.MODELS_KW, versions=[{'where': '/x', 'version': '2.1.126',
                                                                           'primary': True}]))
        self.assertEqual(self.cli('models'), (0, 'Sonnet for helpers: off. Needs Claude Code 2.1.251 or newer.\n', ''))
        self.assertEqual(self.cli('models', 'on'), (1, '', 'Needs Claude Code 2.1.251 or newer.\n'))

    def test_a_settings_file_that_cant_be_read_is_unknown_never_off(self):
        server.act('/api/models', {'enabled': True})
        with open(self.settings, 'w') as f:
            f.write('{not json')
        self.assertEqual(self.cli('models'),
                         (0, "Sonnet for helpers: unknown. Claude Code's settings file isn't valid JSON.\n", ''))
        self.assertEqual(self.cli('models', 'off'), (1, '', "Claude Code's settings file isn't valid JSON.\n"))


# ---------- the build in the version ----------

GIT = git()


class BuildInTheVersion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-build-')
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def package(self, text=None):
        """A copy of the program's folder, switchboard/, in a folder of its own, with build.txt holding `text`."""
        pkg = os.path.join(self.tmp, 'copy', 'switchboard')
        os.makedirs(pkg, exist_ok=True)
        if text is not None:
            with open(os.path.join(pkg, 'build.txt'), 'w') as f:
                f.write(text)
        return pkg

    def test_a_git_archive_download_has_it_in_build_txt(self):
        self.assertEqual(core.build_of(self.package('d635e47\n')), 'd635e47')

    def test_with_neither_there_is_none(self):
        for text in ('$Format:%h$\n', None, 'not a commit\n', '', 'd635e47 and more\n'):
            with self.subTest(text=text):
                self.assertIsNone(core.build_of(self.package(text)))

    @unittest.skipUnless(GIT, 'needs git')
    def test_a_checkout_asks_git(self):
        pkg = self.package('$Format:%h$\n')
        root = os.path.dirname(pkg)
        run_git(root, 'init', '-q')
        run_git(root, 'add', '-A')
        run_git(root, 'commit', '-q', '-m', 'one')
        short = run_git(root, 'rev-parse', '--short', 'HEAD').decode().strip()
        self.assertEqual(core.build_of(pkg), short)

    def test_the_repository_says_how(self):
        with open(os.path.join(ROOT, '.gitattributes')) as f:
            self.assertIn('switchboard/build.txt export-subst', f.read().splitlines())
        with open(os.path.join(ROOT, 'switchboard', 'build.txt')) as f:
            text = f.read()
        # a checkout holds the placeholder; a download made by `git archive` (the tests come with it) holds its commit
        self.assertTrue(text == '$Format:%h$\n' or re.fullmatch(r'[0-9a-f]{7,40}\n', text), text)

    @unittest.skipUnless(GIT, 'needs git')
    def test_git_archive_fills_it_in(self):
        repo = os.path.join(self.tmp, 'repo')
        os.makedirs(os.path.join(repo, 'switchboard'))
        shutil.copy(os.path.join(ROOT, '.gitattributes'), repo)
        with open(os.path.join(repo, 'switchboard', 'build.txt'), 'w') as f:
            f.write('$Format:%h$\n')          # as the repository holds it (in a download of it, it holds the commit)
        run_git(repo, 'init', '-q')
        run_git(repo, 'add', '-A')
        run_git(repo, 'commit', '-q', '-m', 'one')
        short = run_git(repo, 'rev-parse', '--short', 'HEAD').decode().strip()
        tar = run_git(repo, 'archive', '--format=tar', '--prefix=switchboard-0.2.0/', 'HEAD')
        with tarfile.open(fileobj=io.BytesIO(tar)) as t:
            got = t.extractfile('switchboard-0.2.0/switchboard/build.txt').read().decode()
        self.assertEqual(got, short + '\n')


class VersionCommand(cli_help.Fresh):
    def test_version_names_the_build(self):
        with mock.patch.object(core, 'build', lambda: 'd635e47'):
            for args in (('version',), ('--version',), ('-v',)):
                with self.subTest(args=args):
                    self.assertEqual(self.cli(*args), (0, 'switchboard %s (d635e47)\n' % core.VERSION, ''))

    def test_without_one_it_is_the_version_alone(self):
        with mock.patch.object(core, 'build', lambda: None):
            self.assertEqual(self.cli('version'), (0, 'switchboard %s\n' % core.VERSION, ''))


class DoctorBase(test_cli_commands.Cli):
    """`switchboard doctor` in a throwaway home, two made-up accounts in the fake engine, nothing else running."""

    def doctor(self):
        if not self.fake.saved:
            self.two()
        with mock.patch.object(cli, 'page_running', lambda: False), \
                mock.patch.object(linkapp, 'status', lambda fresh=False: {'installed': False, 'on': False}), \
                mock.patch.object(cli.browser, 'present', lambda: False), \
                mock.patch.object(connect, 'claude_bin', lambda: None):
            return self.run_cli('doctor')[1]


class DoctorShowsTheBuild(DoctorBase):
    def test_doctors_first_line_agrees(self):
        for build, head in (('d635e47', 'Switchboard %s (d635e47), in ' % core.VERSION),
                            (None, 'Switchboard %s, in ' % core.VERSION)):
            with self.subTest(build=build), mock.patch.object(core, 'build', lambda: build):
                self.assertTrue(self.doctor().startswith(head))


# ---------- the doctor's status line row beside a line of your own ----------

class DoctorBesideYourOwnLine(DoctorBase):
    MINE = '{"statusLine": {"type": "command", "command": "echo mine"}}\n'
    YOURS = (" --  Status line in Claude Code: your own\n       Optional. To use Switchboard's instead, turn it on from "
             "the page, or run: switchboard statusline on --replace\n")
    NONE = ' --  Status line in Claude Code\n       Optional. Turn it on from the page, or run: switchboard statusline on\n'

    def setUp(self):
        super().setUp()
        self.path = os.path.join(os.environ['CLAUDE_CONFIG_DIR'], 'settings.json')

    def settings(self, text):
        with open(self.path, 'w') as f:
            f.write(text)

    def test_beside_a_line_of_your_own_it_names_replace(self):
        self.settings(self.MINE)
        self.assertIn(self.YOURS, self.doctor())

    def test_the_route_it_names_works_and_off_puts_yours_back(self):
        self.settings(self.MINE)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(['statusline', 'on', '--replace']), 0)
            self.assertEqual(cli.main(['statusline', 'off']), 0)
        with open(self.path) as f:
            self.assertEqual(f.read(), self.MINE)

    def test_with_no_line_it_says_how_to_turn_it_on(self):
        self.assertIn(self.NONE, self.doctor())


# ---------- the board's top line wraps ----------

LONG = 'maya.reyes-longname@northwind-industries.example'          # made up, as long as real ones get

# The board in one frame per width, light and dark: what in its header ends past the frame's right edge, how wide the
# page is, and the header's layout, in #result (the empty <main> is what test_app's dom() waits for).
BOARD_FRAMES = r"""<!doctype html><meta charset="utf-8"><body style="margin:0"><main hidden></main>%(frames)s<pre id="result"></pre><script>
(async () => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms)), out = {found: [], widths: [], looks: 0, rows: []};
  const frames = [...document.querySelectorAll('iframe')], doc = (f) => f.contentDocument;
  try {
    for (let i = 0; i < 300 && !frames.every((f) => doc(f) && doc(f).querySelector('main[data-drawn] #top h1')); i++) await wait(50);
    await Promise.all(frames.map((f) => doc(f).fonts.ready));
    await wait(300);
    out.widths = frames.map((f) => f.contentWindow.innerWidth);
    for (const f of frames) {
      const d = doc(f), w = f.contentWindow.innerWidth, say = (theme, what) => out.found.push(w + ' px, ' + theme + ': ' + what);
      for (const theme of ['light', 'dark']) {
        d.documentElement.dataset.theme = theme;
        out.looks++;
        if (d.documentElement.scrollWidth > w) say(theme, 'the page is ' + d.documentElement.scrollWidth + ' px wide');
        const h1 = d.querySelector('#top h1');
        if (h1.scrollWidth > h1.clientWidth + 1) say(theme, 'the heading runs past its own box');
        for (const el of d.querySelectorAll('#top, #top *')) {
          if (el.getClientRects().length && el.getBoundingClientRect().right > w + 0.5) say(theme, (el.className || el.tagName) + ' ends at ' + Math.round(el.getBoundingClientRect().right));
        }
      }
      delete d.documentElement.dataset.theme;
      const h1 = d.querySelector('#top h1').getBoundingClientRect(), c = d.querySelector('#top .checked').getBoundingClientRect();
      out.rows.push({width: w, h1: Math.round(h1.height), beside: c.top < h1.bottom && c.left >= h1.right - 1});
    }
  } catch (e) { out.error = String(e && e.stack || e); }
  document.getElementById('result').textContent = JSON.stringify(out);
})();
</script>"""


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class BoardTopLineWraps(test_app.ChromePage):
    PHONES = (320, 390, 430)
    NARROW = (641, 720, 800)            # narrow windows, just over the 640 px rule
    maxDiff = None

    def look(self, email, widths):
        demo._ready()
        st, old = demo.STATE, demo.STATE['active']
        if email != old:
            st['accounts'][email] = dict(st['accounts'].pop(old), email=email)
            st['order'] = [email if e == old else e for e in st['order']]
            st['timeline'] = [[email if r[0] == old else r[0]] + r[1:] for r in st['timeline']]
            st['active'] = email

        def page(handler, name):
            if 'frames' in handler.path:
                body = (BOARD_FRAMES % {'frames': ''.join(
                    '<iframe src="/" style="display:block;width:%dpx;height:700px;border:0"></iframe>' % w
                    for w in widths)}).encode()
            else:
                with open(os.path.join(core.WEB, name), 'rb') as f:
                    body = f.read()
            handler.send_response(200)                  # framed here only: the real page refuses to be framed
            handler.send_header('Content-Type', 'text/html; charset=utf-8')
            handler.send_header('Content-Length', str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
        with mock.patch.object(server.Handler, '_page', page):
            dom = self.dom('?frames=1', budget=20000)
        m = re.search(r'<pre id="result">(.*?)</pre>', dom, re.S)
        if not m or not m.group(1).strip():
            self.fail('the frames did not finish')
        got = json.loads(htmllib.unescape(m.group(1)))
        self.assertNotIn('error', got)
        self.assertEqual(got['widths'], list(widths))           # each frame is laid out at its own width
        self.assertEqual(got['looks'], 2 * len(widths))
        return got

    def test_a_long_email_wraps_in_a_narrow_window(self):
        """At phone widths it wrapped already (the 640 px rule); at 641, 720 and 800 px it ran past the edge."""
        found = self.look(LONG, self.PHONES + self.NARROW)['found']
        self.assertEqual([x for x in found if int(x.split(' ')[0]) in self.PHONES], [])
        self.assertEqual(found, [])

    def test_a_wide_window_keeps_one_line(self):
        rows = self.look('maya@northwind.example', (1100,))['rows']
        self.assertEqual(rows, [{'width': 1100, 'h1': 28, 'beside': True}])


if __name__ == '__main__':
    unittest.main()
