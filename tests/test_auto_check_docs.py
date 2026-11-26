import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# docs/commands.md says what `switchboard auto check` and `switchboard auto why` do: two rows after `switchboard
# auto`, and a section for a scheduled job with every exit code the check has, one crontab line, and how often.
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard.accounts import autoloop, usage

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')


def commands():
    with open(os.path.join(ROOT, 'docs', 'commands.md'), encoding='utf-8') as f:
        return f.read()


def section(text, title):
    start = text.index('\n## %s\n' % title)
    end = text.find('\n## ', start + 1)
    return text[start:end if end >= 0 else len(text)]


class TheRows(unittest.TestCase):
    def test_check_and_why_come_right_after_the_auto_row(self):
        rows = [x for x in section(commands(), 'Auto-switch and models').splitlines() if x.startswith('| `')]
        names = [re.match(r'\| `([^`]*)`', x).group(1) for x in rows]
        at = names.index('switchboard auto')
        self.assertEqual(names[at + 1:at + 3], ['switchboard auto check', 'switchboard auto why'])
        for flag in ('--dry-run', '--json', '--five-hour N', '--week N', '--order WORD'):
            self.assertIn('`%s`' % flag, rows[at + 1])


class TheSection(unittest.TestCase):
    def test_it_comes_before_the_page_and_the_menu_bar(self):
        titles = re.findall(r'^## (.*)$', commands(), re.M)
        at = titles.index('The page and the menu bar')
        self.assertEqual(titles[at - 1], 'A check for a scheduled job')

    def test_it_lists_every_exit_code_and_one_crontab_line(self):
        text = section(commands(), 'A check for a scheduled job')
        codes = dict((int(a), b) for a, b in re.findall(r'^\| `(\d)` \| (.*) \|$', text, re.M))
        self.assertEqual(sorted(codes), sorted([autoloop.EXIT_SWITCHED, autoloop.EXIT_ERROR, 2, autoloop.EXIT_BLOCKED,
                                                autoloop.EXIT_NOTHING, autoloop.EXIT_BUSY]))
        cron = [x for x in text.splitlines() if re.match(r'^\*/\d+ \* \* \* \* ', x)]
        self.assertEqual(len(cron), 1, text)
        self.assertIn('switchboard auto check', cron[0])
        self.assertIn('%d times an hour' % round(3600 / usage.PACE_EVERY), text)
        self.assertIn('switchboard log -f --json', text)


if __name__ == '__main__':
    unittest.main()
