import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The command line with no page running, its help, and the words the docs and the installer use:
#
# - With no page running, `switchboard check` and `switchboard open LINK` do the work here, the way the page does.
#   When macOS does not let this terminal start osascript or open, the result is one plain line naming how to start the
#   page, and exit 1 (not a traceback). `switchboard page` with no page exits 1.
# - `switchboard CMD --help` (or -h) prints CMD's own lines of the main help and runs nothing, exit 0.
# - A command it does not know: "Unknown command: NAME." then the help, exit 2. A command it knows, missing its words
#   (`switchboard use`), gets its own lines, exit 2.
# - `switchboard auto off` (when it was off already, too) records the choice and its log line, and says plainly that it
#   stays off until you turn it on (keep.py: once you set it, it never turns on by itself).
# - claude-mem runs its observer on the person's own Claude login (--provider claude). The docs say so
#   (docs/how-it-works.md, "claude-mem") and where it stops: Switchboard's weekly cap (10% of Claude Code use over 7
#   days, by default) and claude-mem's own guard, CLAUDE_MEM_QUOTA_THRESHOLD_SEVEN_DAY "0.85": 85% of the weekly limit
#   of the account it runs on, the one in use (claude-mem's docs/configuration: "With subscription auth, the observer
#   stops once a usage window reaches its threshold"). The plan line says what the 85% is of.
# - The installer's download line names Switchboard's own Python and where it is kept (the page runs on it too,
#   not only the menu bar item). install-check's cases 10, 11 and 12 check the installer's lines. (The
#   doctor's engine line is api.health(), tested in test_cli_commands.py.)
# - The plain uninstaller names only what it kept (tests/install-check.sh, case 11: tests here may not run it).
# - The docs say the auto-switch rule in two plain sentences (keep.auto_by_itself), in docs/how-it-works.md,
#   "Auto-switch".
#
# Made-up accounts only (*.example); the engine, the browser and the page are stand-ins, every folder is a throwaway
# one, and the page's port is 7896, where nothing listens.
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import artifacts, browser, core, demo, engine, history, menubar, server, term  # noqa: E402,F401
from switchboard.core import SwitchError
import test_keep  # its Sandbox: a fake engine and automatic-switching host, the page down

PORT = '7896'                   # nothing listens on this port, so the page is not running
LINK = 'https://claude.ai/artifact/0123456789abcdef'
REACH = 'Could not reach Google Chrome from here, and the page is not running. Start it with: switchboard serve'


class Done(object):
    """What core.run returns: an exit code and its output."""

    def __init__(self, code):
        self.returncode, self.stdout, self.stderr = code, '', ''


def refused(argv, *a, **kw):
    """macOS does not let this terminal start the tool (a sandbox can do that)."""
    raise PermissionError(1, 'Operation not permitted', argv[0])


class Fresh(unittest.TestCase):
    """A throwaway home, data folder and Claude Code folder for each test, and the page's port where nothing listens."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, 'home')
        os.makedirs(os.path.join(self.home, '.claude'))
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': os.path.join(self.tmp, 'data'),
                                           'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude'),
                                           'SWITCHBOARD_PORT': PORT})
        env.start()
        self.addCleanup(env.stop)
        for k in ('SWITCHBOARD_DEMO', 'NO_COLOR'):
            os.environ.pop(k, None)

    def cli(self, *args):
        """(exit code, stdout, stderr) of `switchboard ARGS`, run here as the command runs it: a SwitchError is its one
        line on stderr and exit 1."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except SwitchError as e:
                err.write(str(e) + '\n')
                code = 1
        return code, out.getvalue(), err.getvalue()


# ---------- no page running ----------

CHILD = r'''
import runpy, sys
sys.path.insert(0, sys.argv[1])
from switchboard import browser
class Done(object):
    def __init__(self, code): self.returncode, self.stdout, self.stderr = code, '', ''
def run(argv, **kw):
    if argv[0] == '/usr/bin/pgrep':
        return Done(0)                          # Chrome runs
    raise PermissionError(1, 'Operation not permitted', argv[0])
browser.run = run
sys.argv = ['switchboard'] + sys.argv[2:]
runpy.run_module('switchboard', run_name='__main__', alter_sys=True)
'''


class NoPage(Fresh):
    def test_page_down_is_seen_as_down(self):                   # the port really has nothing on it
        self.assertIsNone(cli.page_state(timeout=2))
        with self.assertRaises(OSError):
            cli.ask_page('/api/check', {}, timeout=2)

    def test_check_with_osascript_refused_is_one_line_and_exit_1(self):
        """The command itself, as a stranger runs it: a child Python, osascript refused, no page."""
        r = subprocess.run([sys.executable, '-I', '-B', '-c', CHILD, ROOT, 'check'], capture_output=True, text=True,
                           timeout=120)                         # -B: -I ignores PYTHONDONTWRITEBYTECODE
        self.assertNotIn('Traceback', r.stderr)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (1, '', REACH + '\n'))

    def test_check_with_osascript_refused_names_how_to_start_the_page(self):
        with mock.patch.object(browser, 'run', lambda argv, **kw: Done(0) if argv[0] == '/usr/bin/pgrep'
                               else refused(argv)):
            self.assertEqual(self.cli('check'), (1, '', REACH + '\n'))

    def test_check_with_open_refused_names_how_to_start_the_page(self):
        """A profile is opened to be checked: /usr/bin/open refused."""
        with mock.patch.object(browser, 'can_see_tabs', lambda: None), \
                mock.patch.object(artifacts, 'check_profiles', lambda: refused(['/usr/bin/open'])):
            self.assertEqual(self.cli('check'), (1, '', REACH + '\n'))

    def test_check_runs_here_without_the_page(self):
        seen = [{'email': 'maya@northwind.example', 'result': 'no_page'}]
        with mock.patch.object(browser, 'can_see_tabs', lambda: None), \
                mock.patch.object(artifacts, 'check_profiles', lambda: seen):
            code, out, err = self.cli('check')
        self.assertEqual((code, err), (0, ''))
        self.assertIn('maya@northwind.example', out)
        self.assertIn('no page of its own is known yet', out)

    def test_check_that_macos_kept_from_the_tabs_exits_1(self):
        with mock.patch.object(browser, 'can_see_tabs', lambda: False):
            code, out, err = self.cli('check')
        self.assertEqual((code, err), (1, ''))
        self.assertEqual(out, 'macOS has not allowed Switchboard to look at Google Chrome tabs.\n'
                              'Allow it in System Settings > Privacy & Security > Automation, then run this again.\n')

    def test_open_with_open_refused_names_how_to_start_the_page(self):
        with mock.patch.object(artifacts, 'candidates', lambda st, item: [('Profile 1', None)]), \
                mock.patch.object(browser, 'run', lambda argv, **kw: Done(1)), \
                mock.patch.object(browser.subprocess, 'Popen', refused):
            self.assertEqual(self.cli('open', LINK), (1, '', REACH + '\n'))

    def test_open_that_is_not_a_link_says_so(self):             # its own refusal, not the start-the-page line
        self.assertEqual(self.cli('open', 'https://example.com/x'),
                         (1, '', 'That is not a claude.ai artifact link.\n'))

    def test_page_not_running_exits_1(self):
        self.assertEqual(self.cli('page'), (1, 'The page is not running. Start it with `switchboard serve`, or run '
                                               'the installer again.\n', ''))


# ---------- --help after a command; a command it does not know ----------

# Every command the main help names (`switchboard WORD ...`); `2` is `switchboard 2`, an account's number or email.
COMMANDS = ['2', 'switch', 'status', 'add', 'signin', 'remove', 'move', 'swap', 'leftovers', 'alias', 'run', 'folder',
            'folders', 'resume', 'auto', 'log', 'setup', 'page', 'menubar', 'use', 'current', 'update', 'open', 'owner',
            'pages', 'check', 'links', 'usage', 'chats', 'providers', 'continue', 'back', 'hand', 'allow', 'stay',
            'order', 'awake', 'config', 'statusline', 'notify', 'hooks', 'skill', 'models', 'keys', 'claude', 'export',
            'import', 'readings', 'doctor', 'demo', 'serve', 'version', 'about']
HELP_LINES = inspect.cleandoc(cli.__doc__).splitlines()    # as Python 3.13 and newer keep it, on every Python
MAIN_HELP = cli.__doc__.strip() + '\n'


def short_help():
    """`switchboard help`: what a new person needs, on one screen (the page's port is read when it prints)."""
    from switchboard import firstrun
    return firstrun.short_help() + '\n'
AUTO = ('switchboard auto [on|off]   whether auto-switch is on; on or off changes it, and it stays as you set it\n'
        'switchboard auto check [--dry-run] [--json] [--five-hour N] [--week N] [--order WORD]\n'
        '                            one pass of auto-switch now, then exit, also with auto-switch off, for a scheduled\n'
        '                            job on a Mac where the page is not running: 0 moved, 1 failed or could not look,\n'
        "                            2 wrong words, 3 held back, 4 nothing to do, 5 the page's loop runs (so it moved\n"
        '                            nothing); --dry-run decides and says why, and changes nothing; --five-hour, --week\n'
        '                            and --order are for this check only; --json prints it as JSON\n'
        'switchboard auto why [--json] [--five-hour N] [--week N] [--order WORD]\n'
        '                            what auto-switch would do now, and why (the same as auto check --dry-run, with its\n'
        '                            codes: 0 it would move, 1 it could not look, 3 it would be held back, 4 it stays)\n'
        'switchboard auto last NAME  auto-switch goes to that account only when no other account has room\n'
        'switchboard auto out NAME   keep that account out of auto-switch: it never moves Claude Code there, or away '
        'from it\n'
        'switchboard auto in NAME    auto-switch uses that account as usual\n'
        'switchboard auto line NAME [--five-hour N] [--week N]\n'
        '                            that account switches at lines of its own, from 50 to 99.9; alone, its lines;\n'
        '                            switchboard auto line NAME off puts it back on the shared ones\n'
        'switchboard auto rank NAME N|off\n'
        '                            auto-switch lands on rank 1 first, then 2, and on an account with no rank after\n'
        '                            every ranked one; N is 1 to 99; off takes its rank away\n')
UPDATE = ('switchboard update          install the newest version, the way it was installed, with the same choices\n'
          "switchboard update --link-app    the same, and add the link app (it needs Apple's command line tools)\n"
          'switchboard update --menu-app    the same, and add the menu bar item (--no-menu-app takes it out)\n'
          'switchboard update --port N      the same, with the page on port N\n'
          'switchboard update --check       whether a newer version is out (it changes nothing); --json prints it as '
          'JSON\n')
LOG = ('switchboard log [-n N] [-f] [--json]\n'
       '                            what happened, newest at the bottom: switches and why, keeps, auto-switch, limits,\n'
       '                            sign-ins, problems; -f follows it, --json prints each line as JSON\n')
DEMO = 'switchboard demo            the page with made-up accounts, to try it first\n'
USE = ('switchboard use NAME [--json]\n'
       '                            move Claude Code to an account (the same as switch NAME)\n')
TWO = 'switchboard 2               move Claude Code to account 2 (or by its email: switchboard you@example.com)\n'


@contextmanager
def nothing_runs():
    """Each command's own code, and anything that starts, opens or replaces a program, stands in: a call is noted
    (yielded) and nothing runs."""
    ran = []

    def stand_in(name):
        def f(*a, **kw):
            ran.append(name)
            return 0
        return f
    targets = [(cli, n) for n in ('home', 'status', 'setup', 'add', 'update', 'use', 'open_link', 'print_pages', 'check',
                                  'doctor', 'usage_command', 'awake_command', 'statusline_command', 'notify_command',
                                  'models_command', 'tidy', 'keys_command', 'claude_on_keys', 'auto_command', 'go_to',
                                  'page_running', 'switch_command', 'remove_command', 'alias_command', 'run_command',
                                  'resume_command', 'owner_command', 'signin_command')]
    targets += [(subprocess, 'call'), (subprocess, 'Popen'), (subprocess, 'run'), (os, 'execv'), (os, 'execve'),
                (server, 'serve'), (menubar, 'main'), (history, 'main')]
    with ExitStack() as stack:
        for obj, name in targets:
            stack.enter_context(mock.patch.object(obj, name, stand_in('%s.%s' % (obj.__name__, name))))
        yield ran


class HelpAfterACommand(Fresh):
    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)          # `demo --help` must not turn the demo on

    def asked(self, *args):
        """(exit code, stdout, stderr, what ran) of `switchboard ARGS` with nothing able to run."""
        with nothing_runs() as ran:
            code, out, err = self.cli(*args)
        return code, out, err, ran

    def test_every_command_in_the_help_is_here(self):          # a new command needs its help tested too
        named = []
        for line in HELP_LINES:
            if line.startswith('switchboard '):
                words = re.split(r'\s{2,}', line, maxsplit=1)[0].split()
                if len(words) > 1 and words[1] not in named:
                    named.append(words[1])
        self.assertEqual(named, COMMANDS)

    def test_help_after_each_command_prints_its_own_lines_and_runs_nothing(self):
        for cmd in COMMANDS:
            for flag in ('--help', '-h'):
                with self.subTest(cmd=cmd, flag=flag):
                    code, out, err, ran = self.asked(cmd, flag)
                    self.assertEqual((code, err, ran), (0, '', []), 'switchboard %s %s: printed %r' % (cmd, flag, out))
                    lines = out.splitlines()
                    self.assertTrue(lines and lines[0].startswith('switchboard %s' % cmd), out)
                    self.assertTrue(set(lines) <= set(HELP_LINES), out)
                    self.assertNotEqual(out, MAIN_HELP)

    def test_a_few_commands_word_for_word(self):
        for args, said in ((('auto', '--help'), AUTO), (('update', '-h'), UPDATE), (('log', '--help'), LOG),
                           (('demo', '--help'), DEMO), (('use', '-h'), USE), (('2', '--help'), TWO),
                           (('maya@ivy.example', '--help'), TWO)):
            with self.subTest(args=args):
                self.assertEqual(self.asked(*args), (0, said, '', []))

    def test_help_anywhere_after_the_command_wins(self):
        for args, said in ((('auto', 'on', '-h'), AUTO), (('update', '--port', '7801', '--help'), UPDATE),
                           (('use', 'maya@ivy.example', '--help'), USE)):
            with self.subTest(args=args):
                self.assertEqual(self.asked(*args), (0, said, '', []))
        code, out, err, ran = self.asked('keys', 'add', 'work', '--help')
        self.assertEqual((code, err, ran), (0, '', []))
        self.assertTrue(out.startswith('switchboard keys list|add NAME|remove NAME\n'), out)

    def test_the_uninstallers_own_command_runs_nothing_either(self):
        self.assertEqual(self.asked('tidy', '--help'), (0, short_help(), '', []))   # not in the help: the short help

    def test_an_unknown_command_stays_unknown_whatever_follows(self):
        for args in (('bogus', '--help'), ('bogus', 'list'), ('bogus',), ('bogus', '-h'), ('bogus', '--version')):
            with self.subTest(args=args):
                self.assertEqual(self.asked(*args), (2, 'Unknown command: %s.\n' % args[0] + short_help(), '', []))

    def test_run_keeps_claudes_arguments_after_the_double_dash(self):   # only those before `--` are ours
        seen = []
        with mock.patch.object(cli, 'run_command', lambda args: seen.append(args) or 0):
            self.assertEqual(self.cli('run', 'work', '--', '--help'), (0, '', ''))
        self.assertEqual(seen, [['work', '--', '--help']])

    def test_hand_keeps_the_jobs_words_after_the_double_dash(self):    # the job's words are the job's
        from switchboard.providers import cli as providers_cli
        seen = []
        with mock.patch.object(providers_cli, 'main', lambda args: seen.append(args) or 0):
            self.assertEqual(self.cli('hand', 'codex', '--', 'x --help'), (0, '', ''))
            self.assertEqual(self.cli('hand', 'codex', '--', '-h'), (0, '', ''))
        self.assertEqual(seen, [['hand', 'codex', '--', 'x --help'], ['hand', 'codex', '--', '-h']])
        code, out, err, ran = self.asked('hand', 'codex', '--help', '--', 'x')
        self.assertTrue(out.startswith('switchboard hand PROVIDER [--chat NAME] [--] JOB\n'), out)

    def test_the_other_assistants_commands_are_handed_on(self):
        from switchboard.providers import cli as providers_cli
        seen = []
        with mock.patch.object(providers_cli, 'main', lambda args: seen.append(('providers', args)) or 0), \
                mock.patch.object(cli, 'add', lambda args: seen.append(('add', args)) or 0):
            for args in (('add',), ('add', 'claude'), ('add', 'codex'), ('providers', '--json'), ('continue',),
                         ('back', '--show'), ('hand', 'codex', 'x'), ('allow', 'codex'), ('stay', 'off'),
                         ('order',)):
                self.assertEqual(self.cli(*args), (0, '', ''), args)
        self.assertEqual(seen, [('add', []), ('add', []), ('providers', ['add', 'codex']),
                                ('providers', ['providers', '--json']), ('providers', ['continue']),
                                ('providers', ['back', '--show']), ('providers', ['hand', 'codex', 'x']),
                                ('providers', ['allow', 'codex']), ('providers', ['stay', 'off']),
                                ('providers', ['order'])])

    def test_the_other_assistants_help_is_their_own(self):     # words.py and this help hold the same lines
        from switchboard.providers import words
        doc = '\n'.join(HELP_LINES)
        for cmd, block in words.HELP.items():
            with self.subTest(cmd=cmd):
                self.assertIn(block, doc)

    def test_claude_passes_its_later_arguments_on(self):        # only its first word can be Switchboard's
        seen = []
        with mock.patch.object(cli, 'claude_on_keys', lambda args: seen.append(args) or 0):
            self.cli('claude', '--model', 'opus', '-h')
        self.assertEqual(seen, [['--model', 'opus', '-h']])

    def test_help_alone_is_the_short_help_and_help_all_the_whole_help(self):
        for args in (('--help',), ('-h',), ('help',)):
            with self.subTest(args=args):
                self.assertEqual(self.asked(*args), (0, short_help(), '', []))
        self.assertEqual(self.asked('help', 'all'), (0, MAIN_HELP, '', []))


class UnknownCommand(Fresh):
    def test_an_unknown_command_says_so_first(self):
        with nothing_runs() as ran:
            self.assertEqual(self.cli('bogus'), (2, 'Unknown command: bogus.\n' + short_help(), ''))
            self.assertEqual(self.cli('--colour'), (2, 'Unknown command: --colour.\n' + short_help(), ''))
        self.assertEqual(ran, [])

    def test_a_command_it_knows_without_its_words_gets_its_own_lines(self):
        awake = 'switchboard awake --once [--json]\n' \
                '                            is Claude Code working, and what the power is (holds nothing)\n'
        with nothing_runs() as ran:
            for args, said in ((('use',), USE), (('open',), 'switchboard open LINK       open a claude.ai artifact '
                                                            'where its owner is signed in\n'),
                               (('awake',), awake), (('2', '3'), TWO)):
                with self.subTest(args=args):
                    self.assertEqual(self.cli(*args), (2, said, ''))
        self.assertEqual(ran, [])


# ---------- auto off, when it is off already ----------

STAYS_OFF = 'Auto-switch is off until you turn it on: switchboard auto on\n'


class AutoOffWhenOff(test_keep.Sandbox):
    def setUp(self):
        super().setUp()
        os.environ['SWITCHBOARD_PORT'] = PORT                 # the page down, on its own port
        self.job()                                              # its background job is set up

    def last_log(self):
        code, out = self.run_cli('log', '--json')
        return json.loads(out.splitlines()[-1])

    def test_off_when_off_already_says_it_stays_off_and_keeps_the_record(self):
        self.assertIs(self.fake.auto['enabled'], False)         # off: never turned on (the engine's own setting)
        self.assertEqual(self.run_cli('auto', 'off'), (0, STAYS_OFF))
        self.assertIs(core.settings().get('auto_set_by_you'), True)            # the choice is recorded
        line = self.last_log()                                                  # and its log line
        self.assertEqual((line.get('kind'), line.get('on'), line.get('by')), ('auto', False, 'you'))

    def test_off_from_on_says_the_same(self):
        self.fake.auto['enabled'] = True
        self.assertEqual(self.run_cli('auto', 'off'), (0, STAYS_OFF))
        self.assertIs(self.fake.auto['enabled'], False)

    def test_asking_after_your_off_says_it_too(self):
        self.run_cli('auto', 'off')
        self.assertEqual(self.run_cli('auto'), (0, STAYS_OFF))

    def test_off_before_any_choice_says_only_how_to_turn_it_on(self):     # it may still turn on by itself
        self.assertEqual(self.run_cli('auto'), (0, 'Auto-switch is off. Turn it on: switchboard auto on\n'))

    def test_on_says_it_is_on_and_at_which_lines(self):
        code, out = self.run_cli('auto', 'on')
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('Auto-switch is on: at 90% 5-hour'), out)


def readme(name='README.md'):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


HOW = os.path.join('docs', 'how-it-works.md')       # what the README's "What it does" and "What it never
                                                    # does" said is told here now


# ---------- claude-mem runs on your own Claude usage, and where it stops ----------

STOP_85 = 'its own stop at 85% of the weekly limit of the account in use'
README_MEM = ('claude-mem runs a model on your own Claude usage, and pauses when that passes 10% of your Claude Code use '
              'over 7 days (you can change the 10%). It also pauses when the account in use is at 85% of its weekly '
              'limit.')                         # one limit to a sentence


class ClaudeMemUsage(unittest.TestCase):
    def test_the_docs_say_it_uses_your_own_usage_and_where_it_stops(self):
        text = readme(HOW)
        part = text[text.index('### claude-mem'):text.index('## The page')]
        self.assertIn(README_MEM, part)

    def test_the_plan_line_says_what_the_85_is_of(self):
        from switchboard.packs import install
        lines = install.plan_lines({'pack': 'claude-mem', 'version': '15.18.3', 'uses': [], 'mb': 2150, 'notes': []})
        self.assertIn('Sets chats only, no auto-update, File Read Gate off, no quota fallback, and %s.' % STOP_85, lines)
        self.assertFalse([x for x in lines if '85% of a week' in x])

    def test_the_setting_says_it_too(self):                    # the detailed plan and the install log
        from switchboard.packs import claudemem_install
        why = dict((k, w) for k, _, w in claudemem_install.EXTRA)['CLAUDE_MEM_QUOTA_THRESHOLD_SEVEN_DAY']
        self.assertEqual(why, STOP_85)

    def test_the_words_are_the_values_written(self):           # the numbers said are the ones set
        from switchboard.packs import claudemem_install, memory
        value = dict((k, v) for k, v, _ in claudemem_install.EXTRA)['CLAUDE_MEM_QUOTA_THRESHOLD_SEVEN_DAY']
        self.assertEqual('%g%%' % (100 * float(value.strip('"'))), '85%')
        self.assertEqual('%g%%' % memory.CAP_DEFAULT, '10%')


# ---------- Switchboard's own Python, kept in ~/.switchboard ----------

class InstallWords(unittest.TestCase):
    def test_the_download_line_says_whose_python_it_is_and_where_it_is_kept(self):
        with open(os.path.join(ROOT, 'install.sh'), encoding='utf-8') as f:
            text = f.read()
        self.assertIn('begin "Downloading Switchboard\'s own Python $PBS_VERSION, kept in $(short "$DATA") '
                      '(27 MB, once)"', text)
        self.assertIn('OWN_WHY="Switchboard\'s own Python could not be downloaded (no internet?)."', text)
        self.assertNotIn('fetch_own_python "for', text)            # one line for all three reasons it is fetched
        self.assertNotIn('FOR="for the', text)


# ---------- the auto-switch rule, in README's "What it never does" ----------

NEVER_AUTO = ('- It never switches by itself while auto-switch is off. Auto-switch turns on by itself when a second '
              'account is in, unless you have turned it on or off yourself (on the page, in the menu bar, or with '
              '`switchboard auto`).\n')


class NeverBullet(unittest.TestCase):
    def test_two_plain_sentences(self):
        text = readme(HOW)
        never = text[text.index('## Auto-switch'):text.index("## What it puts in Claude Code's settings")]
        self.assertIn(NEVER_AUTO, never)
        self.assertNotIn('until you turn it on or off yourself', never)


if __name__ == '__main__':
    unittest.main()
