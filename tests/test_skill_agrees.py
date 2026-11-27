import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The skill (switchboard/skill/) says only what is true of this program:
#
# - every `switchboard ...` command written in it is a command the program has, with words its own help lines name
#   (the help is the docstring of switchboard/__main__.py, which `switchboard CMD --help` cuts its lines from);
# - every command of docs/commands.md's tables is in reference/commands.md;
# - the switch lines, the ports and the paths it names are the code's;
# - SKILL.md has the front matter skills need (a name equal to its folder's, a description within its length), and is
#   within its own length; every reference file it names is there, and every file there is named, one line each;
# - what it lets an agent run in its own shell only looks and returns: no command that keeps a login, and none
#   that runs until stopped, which it gives to the person for their own Terminal window;
# - none of the words this project never uses is in it.
#
# Reads text only: nothing runs, nothing is written.
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import config, core, gateway, skillfile
from switchboard.accounts import policy

SKILL = skillfile.SOURCE
SKILL_LINES = 120               # SKILL.md's own length, front matter included
NAME_MAX, DESCRIPTION_MAX = 64, 1024    # the Agent Skills form
CAPS_WORD = re.compile(r'^[A-Z][A-Z0-9_]*(\.\.\.)?$')     # NAME, N, LINK, FILE, ID...
FLAG = re.compile(r'(?<![\w-])(--?[A-Za-z][\w-]*)')


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def skill_files():
    """{path relative to the skill folder: text} of every file in it."""
    out = {}
    for top, _, files in os.walk(SKILL):
        for name in files:
            p = os.path.join(top, name)
            out[os.path.relpath(p, SKILL)] = read(p)
    return out


def front_matter(text):
    m = re.match(r'^---\n(.*?)\n---\n', text, re.S)
    if not m:
        return None
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(':')
        out[k.strip()] = v.strip()
    return out


def commands_in(text):
    """Every `switchboard ...` written in backticks: the words of each, up to a `--` (what follows is Claude Code's
    or a job's own)."""
    out = []
    for span in re.findall(r'`([^`\n]+)`', text):
        if not span.startswith('switchboard ') and span != 'switchboard':
            continue
        words = span.replace('\\|', '|').split()
        if '--' in words:
            words = words[:words.index('--')]
        out.append((span, words[1:]))
    return out


def help_words(cmd):
    """Every word the help lines of one command name: flags apart, then the plain words."""
    own = cli.command_help(cmd) or ''
    return set(FLAG.findall(own)), set(re.findall(r'[A-Za-z_][\w.]*', own))


def section(text, head):
    """The text under one `## head...` heading, up to the next `## `."""
    start = text.index('\n## %s' % head)
    end = text.find('\n## ', start + 1)
    return text[start:] if end < 0 else text[start:end]


def table_commands():
    """The first cell of each row of docs/commands.md's command tables (up to "For scripts")."""
    text = read(os.path.join(ROOT, 'docs', 'commands.md'))
    text = text[:text.index('\n## For scripts\n')]
    return [m.replace('\\|', '|') for m in re.findall(r'^\| `(switchboard[^`]*)`', text, re.M)]


class TheCommands(unittest.TestCase):
    def test_each_command_is_one_the_program_has_with_its_own_words(self):
        import inspect
        firsts = [re.split(r'\s{2,}', line, maxsplit=1)[0].split() for line in inspect.cleandoc(cli.__doc__).splitlines()
                  if line.startswith('switchboard ')]
        known = set(words[1] for words in firsts if len(words) > 1)
        known |= set(w for w in cli.UNLISTED if w.isalpha() and w != 'tidy')     # help: the command table has it
        seen = 0
        for rel, text in sorted(skill_files().items()):
            for span, words in commands_in(text):
                seen += 1
                with self.subTest(file=rel, command=span):
                    if not words:
                        continue
                    cmd = cli.SHORT_WORDS.get(words[0], words[0])
                    if CAPS_WORD.match(cmd):                  # `switchboard CMD --help`
                        continue
                    if cmd.isdigit() or cli.EMAIL_RE.match(cmd):
                        self.assertEqual(len(words), 1, span)
                        continue
                    self.assertIn(cmd, known, span)
                    if cmd == 'help':                           # no help line of its own: `help` and `help all`
                        import inspect
                        self.assertIn(words[1:], ([], ['all']), span)
                        self.assertIn("args == ['all']", inspect.getsource(cli.help_command))
                        continue
                    flags, plain = help_words(cmd)
                    row = None
                    for w in words[1:]:
                        for part in re.split(r'[|\[\]]', w):
                            if not part or part in ('...', '…', '-') or CAPS_WORD.match(part):
                                continue
                            if part.startswith('-'):
                                self.assertIn(part, flags, span)
                            elif re.match(r'^\d+[smh]?$', part):
                                continue
                            elif cmd == 'config' and part in config.BY_KEY:
                                row = config.BY_KEY[part]
                            elif cmd == 'config' and row is not None and part in (row.choices or ()):
                                continue
                            else:
                                self.assertIn(part, plain, span)
        self.assertGreater(seen, 100)

    def test_every_command_of_the_reference_tables_is_in_commands_md(self):
        mine = read(os.path.join(SKILL, 'reference', 'commands.md')).replace('\\|', '|')
        rows = table_commands()
        self.assertGreater(len(rows), 80)
        for cmd in rows:
            with self.subTest(command=cmd):
                self.assertIn('`%s`' % cmd, mine)

    def test_every_command_word_the_help_names_is_in_commands_md(self):
        import inspect
        mine = read(os.path.join(SKILL, 'reference', 'commands.md'))
        for line in inspect.cleandoc(cli.__doc__).splitlines():
            words = re.split(r'\s{2,}', line, maxsplit=1)[0].split() if line.startswith('switchboard ') else []
            if len(words) > 1:
                with self.subTest(command=words[1]):
                    self.assertIn('`switchboard %s' % words[1], mine)


class TheNumbersAndPaths(unittest.TestCase):
    def setUp(self):
        self.all = '\n'.join(skill_files().values())

    def test_the_switch_lines_are_the_codes_defaults(self):
        five, week, model = (int(policy.DEFAULTS[k]) for k in ('session_at', 'weekly_at', 'model_at'))
        auto = read(os.path.join(SKILL, 'reference', 'auto-switch.md'))
        self.assertIn('%d%% of the 5-hour limit and %d%% of the weekly limit' % (five, week), auto)
        self.assertIn('%d%% by default' % model, auto)
        self.assertIn('Auto-switch at %d%% 5-hour, %d%% weekly' % (five, week),
                      read(os.path.join(SKILL, 'reference', 'the-page.md')))
        self.assertEqual((policy.LINE_MIN, policy.LINE_MAX), (50.0, 99.9))
        self.assertIn('(50 to 99.9)', auto)
        for minutes, words in ((policy.IDLE_FOR, 'idle for %d minutes'), (policy.DWELL, '%d minutes ago or more'),
                               (policy.OPTIONAL_MAX_AGE, 'under %d minutes old')):
            self.assertIn(words % (minutes // 60), auto)
        self.assertIn('At most two such moves go in %d hours' % (policy.OPTIONAL_CAP_WINDOW // 3600), auto)
        self.assertEqual(policy.OPTIONAL_CAP, 2)
        self.assertIn('at least %d points of room' % policy.LAND_MARGIN, auto)
        self.assertIn('%d points of room under every line' % policy.OPTIONAL_ROOM, auto)
        self.assertEqual(policy.STRATEGIES, ('refills-first', 'most-room', 'stay-then-refills'))
        for word in policy.STRATEGIES:
            self.assertIn('`%s`' % word, auto)

    def test_every_wait_the_policy_has_is_named(self):
        auto = read(os.path.join(SKILL, 'reference', 'auto-switch.md'))
        self.assertIn('the battery under %d%%' % policy.BATTERY_LOW, auto)
        self.assertIn('load over %d per core' % policy.LOAD_HIGH, auto)
        self.assertIn('`switchboard auto why` names the one', section(auto, 'When a move waits'))

    def test_the_ports_are_the_codes(self):
        with mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': ''}):
            page = core.port()
        self.assertIn('http://127.0.0.1:%d' % page, self.all)
        for m in re.findall(r'(?:localhost|127\.0\.0\.1):(\d+)', self.all):
            self.assertEqual(int(m), page)
        self.assertIn("setdefault('SWITCHBOARD_PORT', '7798')", read(os.path.join(ROOT, 'switchboard', '__main__.py')))
        self.assertIn('on port 7798', self.all)
        for m in re.findall(r'port (\d{4})', self.all):
            self.assertIn(int(m), (page, 7798, gateway.DEFAULT_PORT))

    def test_the_paths_are_the_codes(self):
        home = '/h'
        with mock.patch.dict(os.environ, {'HOME': home, 'CLAUDE_CONFIG_DIR': '', 'SWITCHBOARD_HOME': ''}), \
                mock.patch.object(core, '_in_tests', lambda: False):
            data = core.data_dir().replace(home, '~')
            claude = core.claude_dir().replace(home, '~')
            codex = skillfile.place_dir('codex').replace(home, '~')
            mine = skillfile.place_dir('claude').replace(home, '~')
        self.assertEqual((data, claude), ('~/.switchboard', '~/.claude'))
        self.assertIn('`%s`' % data, self.all)
        self.assertIn('`%s`' % codex, self.all)
        self.assertEqual(mine, claude + '/skills/switchboard')
        self.assertIn('`skills/switchboard`', self.all)
        from switchboard.providers import contract
        self.assertIn('`~/%s`' % contract.NOTES_DIR_NAME, self.all)
        self.assertIn(skillfile.USAGE, self.all)

    def test_no_address_of_the_project(self):
        # The release fills the project's address in a fixed list of files and refuses a .md that still holds the
        # placeholder, so the skill names none: it points to `switchboard about` and the README instead.
        for rel, text in sorted(skill_files().items()):
            with self.subTest(file=rel):
                # the publish step's placeholder, split so the publish scan does not read it as one left in
                self.assertNotIn('OWN' 'ER/', text)
                self.assertIsNone(re.search(r'github(usercontent)?\.com/[\w.-]+/[\w.-]+', text))
        self.assertIn("the project's GitHub page (`switchboard about` prints its address", self.all)

    def test_the_terms_pages_are_the_faqs(self):
        faq = read(os.path.join(ROOT, 'docs', 'faq.md'))
        pages = re.findall(r'\]\((https://[^)]*(?:terms|legal)[^)]*)\)', faq)
        self.assertEqual(len(pages), 3, pages)
        said = read(os.path.join(SKILL, 'reference', 'what-it-does-not-do.md'))
        for url in pages:
            self.assertIn(url, said)
        self.assertIn('Check that using several accounts fits Anthropic\'s terms.', read(os.path.join(ROOT, 'README.md')))
        self.assertIn('"Check that using several accounts fits Anthropic\'s terms."', said)


class TheSentencesItQuotes(unittest.TestCase):
    """A sentence the skill quotes from a screen is that screen's own, so a change of words there shows here."""

    def test_the_first_runs_steps_and_commands(self):
        # first-run.md describes the steps and names the commands; it quotes none of the first run's sentences,
        # which may change by a word. The step names are the code's, and the install's next step is `switchboard`.
        from switchboard import about, firstrun
        said = read(os.path.join(SKILL, 'reference', 'first-run.md'))
        for title in (firstrun.STEP_ACCOUNT, firstrun.STEP_OTHERS, firstrun.STEP_MOVE, firstrun.STEP_LINKS,
                      firstrun.STEP_WHERE):
            with self.subTest(step=title):
                self.assertIn(title, said)
        self.assertEqual(''.join(t for t, _ in about.next_words(0)), 'Next: switchboard')
        for cmd in ('`switchboard`', '`switchboard setup`', '`switchboard add`', '`switchboard add EMAIL`',
                    '`switchboard about`'):
            with self.subTest(command=cmd):
                self.assertIn(cmd, said)
        self.assertNotIn('/login in Claude Code. Each one is kept', said)

    def test_the_menu_bar_items_it_names(self):
        source = read(os.path.join(ROOT, 'switchboard', 'menubar.py'))
        items = set(re.findall(r"\('do', '([^']+)'", source))
        said = read(os.path.join(SKILL, 'reference', 'menu-bar.md'))
        for item in ('Open Switchboard', 'Add account', 'Start'):
            with self.subTest(item=item):
                self.assertIn(item, said)
                self.assertIn(item, items)
        # The menu has no Quit, and the item is the S with a dot: `menubar off` and `on` are the way to turn it off.
        self.assertNotIn('Quit', items)
        self.assertIn('The menu has no Quit', said)
        self.assertNotIn('two small bars', said)
        from switchboard import menubar_keep
        self.assertIn('Turn it on with: switchboard menubar on', menubar_keep.OFF)
        for cmd in ('`switchboard menubar off`', '`switchboard menubar on`'):
            with self.subTest(command=cmd):
                self.assertIn(cmd, said)
        from switchboard import menubar
        if 'isn\'t running' in said:
            self.assertIn('"%s"' % menubar.NOT_RUNNING, said)


OWN_TERMINAL = 'in their own Terminal window'
SEARCH_KEEPS = "the person's prompts and Claude's text replies"


class WhatAnAgentRunsItself(unittest.TestCase):
    """What the skill lets an agent run in its own shell only looks, and returns."""

    def setUp(self):
        self.commands = read(os.path.join(SKILL, 'reference', 'commands.md'))

    def test_what_only_reads_keeps_no_login(self):
        import inspect
        # Bare `switchboard` and plain `switchboard status` keep the login Claude Code is on when Switchboard does not
        # hold it yet (keep_now: a Keychain write, and auto-switch may turn on). `status --json` only looks.
        keeps = set(words for words, fn in (((), cli.home), (('status',), cli.status))
                    if 'keep_now()' in inspect.getsource(fn))
        self.assertEqual(keeps, {(), ('status',)})
        self.assertNotIn('keep_now()', inspect.getsource(cli.status_json))
        skill = read(os.path.join(SKILL, 'SKILL.md'))
        look = skill[skill.index('1. Look first.'):skill.index('\n2. ')]
        for where, text in (('SKILL.md', look), ('reference/commands.md', section(self.commands, 'Only reads'))):
            for span, words in commands_in(text):
                with self.subTest(where=where, command=span):
                    self.assertNotIn(tuple(words), keeps)
        self.assertIn('`switchboard status`', section(self.commands, 'Changes something'))

    def test_what_runs_until_stopped_is_given_to_the_person(self):
        import inspect
        from switchboard import menubar
        main = inspect.getsource(cli.main)
        for cmd in ('serve', 'demo'):                   # the page itself, in the foreground until stopped
            self.assertRegex(main, r"elif cmd == '%s':\n(.*\n){0,5}?\s+server\.serve\(" % cmd)
        item = inspect.getsource(menubar.main)          # without a terminal, the item runs in the foreground
        self.assertIn('job = our_job() if terminal() else None', item)
        self.assertTrue(item.rstrip().endswith('return run()'))
        seen = 0
        for rel, text in sorted(skill_files().items()):
            for line in text.splitlines():
                for span in re.findall(r'`(switchboard (?:serve|demo|menubar))`', line):
                    seen += 1
                    with self.subTest(file=rel, command=span, line=line[:60]):
                        self.assertIn(OWN_TERMINAL, line)
        self.assertGreater(seen, 3)
        self.assertNotRegex(section(self.commands, 'Only reads'), r'`switchboard (serve|demo|menubar)`')

    def test_every_add_that_keeps_a_login_is_the_persons_own(self):
        for span, words in commands_in(section(self.commands, 'Changes something')):
            if words[:1] == ['add']:
                with self.subTest(command=span):
                    self.assertIn(words[1:2], (['PROVIDER'], ['codex']))
        self.assertIn('`switchboard add --as SHORT --at N`', section(self.commands, "The person's own"))

    def test_a_command_that_is_not_switchboards_is_marked_when_it_changes_something(self):
        self.assertIn('Every `switchboard` command you give must be in `reference/commands.md`',
                      read(os.path.join(SKILL, 'SKILL.md')))
        seen = 0
        for rel, text in sorted(skill_files().items()):
            for line in text.splitlines():
                if '>> ~/.zshrc' in line:
                    seen += 1
                    self.assertIn('only when the person asks', line, rel)
        self.assertEqual(seen, 1)


class WhatItSays(unittest.TestCase):
    def test_privacy_says_what_the_chat_search_keeps(self):
        from switchboard.packs import memory
        for fact in ('your prompts', "the assistant's text replies", 'Not tool output', "not helpers' transcripts"):
            self.assertIn(fact, memory.__doc__)
        text = read(os.path.join(SKILL, 'reference', 'privacy.md'))
        for head in ('Does it send my chats anywhere', 'What it keeps'):
            with self.subTest(section=head):
                self.assertIn(SEARCH_KEEPS, section(text, head))

    def test_lowering_the_shares_names_no_switch(self):
        # A switch's effect on the shares is not in the code (usage.py counts what each request's transcript says),
        # so the advice leaves it out.
        text = section(read(os.path.join(SKILL, 'reference', 'your-week.md')), 'Lowering the first two')
        self.assertEqual(re.findall(r'(?i)\bswitch(?!board)', text), [])


class TheForm(unittest.TestCase):
    def setUp(self):
        self.text = read(os.path.join(SKILL, 'SKILL.md'))

    def test_skill_md_has_its_front_matter(self):
        fm = front_matter(self.text)
        self.assertIsNotNone(fm)
        self.assertEqual(set(fm), {'name', 'description'})
        self.assertEqual(fm['name'], skillfile.NAME)
        self.assertEqual(os.path.basename(skillfile.place_dir('claude')), fm['name'])
        self.assertRegex(fm['name'], r'^[a-z-]{1,%d}$' % NAME_MAX)
        self.assertTrue(0 < len(fm['description']) <= DESCRIPTION_MAX, len(fm['description']))
        self.assertNotRegex(fm['description'], r': | #|^[\'"&*!|>%@`]')     # one plain line of YAML

    def test_skill_md_is_within_its_length(self):
        self.assertLessEqual(len(self.text.splitlines()), SKILL_LINES)
        self.assertEqual(skillfile.NAME, 'switchboard')

    def test_every_reference_file_is_named_once_and_there(self):
        named = re.findall(r'^- `reference/([^`]+)`: \S', self.text, re.M)
        there = sorted(n for n in os.listdir(os.path.join(SKILL, 'reference')) if not n.startswith('.'))
        self.assertEqual(sorted(named), there)
        self.assertEqual(len(named), len(set(named)))
        for rel in re.findall(r'`(reference/[^`]+)`', '\n'.join(skill_files().values())):
            self.assertTrue(os.path.isfile(os.path.join(SKILL, rel)), rel)
        for rel, text in skill_files().items():
            for name in re.findall(r'`([a-z-]+\.md)`', text):
                self.assertIn(name, there + ['SKILL.md'], '%s names %s' % (rel, name))

    def test_only_markdown_is_in_the_folder(self):
        for rel in skill_files():
            self.assertTrue(rel.endswith('.md'), rel)
        self.assertEqual([rel for rel, _ in skillfile.guide_files()], ['SKILL.md'] + sorted(
            'reference/' + n for n in os.listdir(os.path.join(SKILL, 'reference'))))


# Words and phrases the project never uses on any surface: a promise it cannot keep, a claim about limits or about
# Anthropic, words that are not its own, and the program's own owner or story.
NEVER = re.compile(r"\b(rotat\w*|pool\w*|unlimited|more usage|never run out|bypass\w*|get(s|ting)? around|beat\w*|"
                   r"avoid\w*|dodg\w*|circumvent\w*|where it went|never signed out|always signed in|never stop\w*|"
                   r"the best model|routes? tasks|allowed by Anthropic|approved by Anthropic|her|hers|the owner)\b", re.I)


class TheWords(unittest.TestCase):
    def test_none_of_the_words_the_project_never_uses(self):
        from test_docs_words import HYPE, LITERAL
        for rel, text in sorted(skill_files().items()):
            with self.subTest(file=rel):
                self.assertEqual(NEVER.findall(text), [])
                plain = text
                for words in LITERAL:               # the plain sense of a word HYPE lists
                    plain = plain.replace(words, '')
                self.assertEqual(HYPE.findall(plain), [])
                self.assertNotIn('!', text)
                self.assertFalse(re.search(r'[\U0001F300-\U0001FAFF☀-⛿✀-➿]', text), rel)

    def test_every_email_is_a_made_up_one(self):
        for rel, text in sorted(skill_files().items()):
            for email in re.findall(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+', text):
                with self.subTest(file=rel, email=email):
                    self.assertTrue(email.endswith('.example'), email)

    def test_lines_and_days_of_a_sign_in_are_not_promised(self):
        for rel, text in sorted(skill_files().items()):
            self.assertNotRegex(text, r'(?i)sign-?in(s)? (lasts?|is good for) \d+ days', rel)


if __name__ == '__main__':
    unittest.main()
