import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# One command is said in three places: the main help (the docstring of switchboard/__main__.py, which
# `switchboard CMD --help` cuts its lines from), the usage line the command prints for words it does not take, and
# docs/commands.md. They must name the same flags:
#
# - for each command with a usage line of its own, the flags the usage line names are exactly the flags its lines of
#   the help name;
# - every flag the help names is in docs/commands.md, and so is every command;
# - every exit code the code names beyond 0, 1 and 2 is in the one table of exit codes ("For scripts"), on its
#   command's row, and the help's codes for `auto check` are the loop's own.
#
# Reads text only: nothing runs.
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import about, backup, chats, config, foldercmd, history, leftovers, menubar, places, readings, resume

FLAG = re.compile(r'(?<![\w-])(--?[A-Za-z][\w-]*)')

# The usage line each command prints, by its word. A command whose usage is written inline is not here.
USAGES = {
    'switch': cli.SWITCH_USAGE,
    'add': places.ADD,
    'leftovers': leftovers.FORMS,
    'run': foldercmd.RUN_USAGE,
    'folder': foldercmd.FOLDER_USAGE,
    'folders': foldercmd.FOLDERS_USAGE,
    'resume': resume.USAGE,
    'auto': '\n'.join(cli.AUTO_FORMS),
    'log': history.USAGE,
    'menubar': menubar.USAGE,
    'update': cli.UPDATE_USAGE,
    'chats': chats.USAGE,
    'config': config.USAGE,
    'export': backup.EXPORT_USAGE,
    'import': backup.IMPORT_USAGE,
    'readings': readings.USAGE,
    'about': about.USAGE,
}


def flags(text):
    return set(FLAG.findall(text))


def commands_md():
    with open(os.path.join(ROOT, 'docs', 'commands.md'), encoding='utf-8') as f:
        return f.read()


def help_words():
    """Every command word the main help names (`switchboard WORD ...`), in order."""
    import inspect
    named = []
    for line in inspect.cleandoc(cli.__doc__).splitlines():
        if line.startswith('switchboard '):
            words = re.split(r'\s{2,}', line, maxsplit=1)[0].split()
            if len(words) > 1 and words[1] not in named:
                named.append(words[1])
    return named


class UsageAndHelp(unittest.TestCase):
    def test_each_usage_line_names_the_flags_of_its_help_lines(self):
        for word, usage in sorted(USAGES.items()):
            with self.subTest(command=word):
                own = cli.command_help(word)
                self.assertTrue(own, word)
                self.assertEqual(sorted(flags(usage)), sorted(flags(own)), 'usage: %r\nhelp: %r' % (usage, own))

    def test_every_usage_line_starts_with_its_command(self):
        for word, usage in USAGES.items():
            for line in usage.splitlines():
                with self.subTest(line=line):
                    self.assertTrue(line.startswith('switchboard %s' % word), line)


class HelpAndDocs(unittest.TestCase):
    def test_every_flag_the_help_names_is_in_the_reference(self):
        text = commands_md()
        for flag in sorted(flags(cli.__doc__)):
            with self.subTest(flag=flag):
                self.assertRegex(text, r'(?<![\w-])%s(?![\w-])' % re.escape(flag))

    def test_every_command_the_help_names_is_in_the_reference(self):
        text = commands_md()
        for word in help_words():
            with self.subTest(command=word):
                self.assertIn('`switchboard %s' % word, text)


class FilesAndSettings(unittest.TestCase):
    """The docs list what the code keeps: the data files of the newer modules are rows of "Files", and every setting
    `switchboard config set` can change is named in "Settings from the terminal"."""

    def test_the_data_files_are_in_the_files_table(self):
        from switchboard import newer, trust
        from switchboard.accounts import folders
        text = commands_md()
        table = text[text.index('\n## Files\n'):]
        for name in (folders.FILE, folders.LOCK, readings.STORE, newer.KEPT, trust.KEPT):
            with self.subTest(file=name):
                self.assertIn('| `%s` |' % name, table)

    def test_every_setting_config_sets_is_named(self):
        text = commands_md()
        part = text[text.index('\n## Settings from the terminal\n'):]
        part = part[:part.index('\n## ', 1)]
        for r in config.ROWS:
            if r.post or r.setter or r.kind == 'accounts':
                with self.subTest(key=r.key):
                    self.assertIn('`%s`' % r.key, part)


def for_scripts_rows():
    """{the first command of a row: the row's cells} of the exit-code table in docs/commands.md, "For scripts"."""
    text = commands_md()
    part = text[text.index('\n## For scripts\n'):]
    part = part[:part.index('\n## ', 1)]
    rows = {}
    for line in part.splitlines():
        if line.startswith('| `switchboard'):
            cells = [c.strip() for c in re.split(r'(?<!\\)\|', line)[1:-1]]
            rows[re.match(r'`([^`]+)`', cells[0]).group(1)] = cells
    return rows


class ExitCodes(unittest.TestCase):
    """Every exit code the code names, beyond 0, 1 and 2, is in the "Other codes" cell of its command's row."""

    def test_each_code_the_code_names_has_its_row(self):
        from switchboard import newer
        from switchboard.accounts import autoloop
        rows = for_scripts_rows()
        expected = {
            'switchboard auto check [--dry-run]': {autoloop.EXIT_BLOCKED, autoloop.EXIT_NOTHING, autoloop.EXIT_BUSY},
            'switchboard auto why': {autoloop.EXIT_BLOCKED, autoloop.EXIT_NOTHING},
            'switchboard export --check FILE': set(backup.CODES.values()),
            'switchboard run [NAME] -- ARGS': {foldercmd.STRICT_EXIT},
            'switchboard update --check [--json]': {newer.NEWER_EXIT},
        }
        for cmd, codes in expected.items():
            with self.subTest(command=cmd):
                self.assertIn(cmd, rows)
                other = rows[cmd][4]
                said = set(int(n) for n in re.findall(r'(?:^|; )(\d+) ', other))
                self.assertEqual(said, codes, other)

    def test_every_row_has_a_cell_for_each_column(self):
        rows = for_scripts_rows()
        self.assertGreaterEqual(len(rows), 15)
        for cmd, cells in rows.items():
            with self.subTest(command=cmd):
                self.assertEqual(len(cells), 5, cells)

    def test_the_check_codes_in_the_help_are_the_loops_own(self):
        from switchboard.accounts import autoloop
        said = cli.command_help('auto').split('auto check', 1)[1].split('switchboard auto why')[0]
        for code, words in ((autoloop.EXIT_SWITCHED, 'moved'), (autoloop.EXIT_ERROR, 'failed'), (2, 'wrong words'),
                            (autoloop.EXIT_BLOCKED, 'held back'), (autoloop.EXIT_NOTHING, 'nothing to do'),
                            (autoloop.EXIT_BUSY, "the page's loop runs")):
            with self.subTest(code=code):
                self.assertRegex(' '.join(said.split()), r'\b%d %s' % (code, re.escape(words)))


if __name__ == '__main__':
    unittest.main()
