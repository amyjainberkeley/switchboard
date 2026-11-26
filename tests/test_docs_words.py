import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The words of the docs, checked as a list:
#
# - The README's first screen is the name, one line, the three headline features (artifacts first) and the install
#   line, nothing else. The install line is Homebrew's; the one-line installer is the second way, further down. The
#   name is the logo: the mark and the word, one SVG for light grounds and one for dark, as GitHub's themes
#   pick them (<picture>), with "Switchboard" as its text.
# - No hype words.
# - The changelog's first version heading is this version (core.VERSION), dated or "not released yet"; every later
#   one is a dated release; versions go newest first; there is no Unreleased section.
# - Every link between the docs leads to a file that is there, and every #anchor to a heading in it.
# - The README's commands are each in docs/commands.md.
#
# Reads files only: nothing runs, nothing is written.
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import core

TOP = ('README.md', 'CHANGELOG.md', 'SECURITY.md', 'CONTRIBUTING.md', 'CODE_OF_CONDUCT.md')
THIRD_PARTY = ('CODE_OF_CONDUCT.md',)       # the Contributor Covenant's own words: not checked for hype

HYPE = re.compile(r'\b(seamless\w*|effortless\w*|supercharg\w*|magic\w*|unlock\w*|game[- ]chang\w*|leverag\w*|'
                  r'empower\w*|elevat\w*|delv\w*|streamlin\w*|revolutioni\w*|cutting[- ]edge|best[- ]in[- ]class|'
                  r'blazing\w*)', re.I)
LITERAL = ('unlock the Mac',)               # the plain sense of a word HYPE lists
BREW = 'brew install amyjainberkeley/apps/switchboard'
LOGO = ('# <picture><source media="(prefers-color-scheme: dark)" srcset="docs/logo-dark.svg">'
        '<img src="docs/logo.svg" alt="Switchboard" width="215" height="40"></picture>\n')
INSTALL = 'curl -fsSL https://raw.githubusercontent.com/amyjainberkeley/switchboard/main/install.sh | sh'


def docs():
    """Every doc a person reads, relative to ROOT: the files at the top and docs/*.md."""
    names = [n for n in TOP if os.path.isfile(os.path.join(ROOT, n))]
    return names + sorted(os.path.join('docs', n) for n in os.listdir(os.path.join(ROOT, 'docs')) if n.endswith('.md'))


def read(name):
    with open(os.path.join(ROOT, name), encoding='utf-8') as f:
        return f.read()


def slug(heading):
    """The anchor GitHub gives a heading: lower case, punctuation out, spaces to hyphens."""
    return re.sub(r'[^\w\- ]', '', heading.strip().lower()).replace(' ', '-')


def anchors(text):
    out, seen = set(), {}
    fenced = False
    for line in text.splitlines():
        if line.startswith('```'):
            fenced = not fenced
        elif not fenced and line.startswith('#'):
            s = slug(line.lstrip('#'))
            n = seen.get(s, 0)
            seen[s] = n + 1
            out.add(s if n == 0 else '%s-%d' % (s, n))
    return out


class ReadmeFirstScreen(unittest.TestCase):
    def test_name_one_line_three_features_then_the_install_line(self):
        text = read('README.md')
        self.assertTrue(text.startswith(LOGO), text[:200])
        for name in ('logo.svg', 'logo-dark.svg'):
            self.assertIn('aria-label="Switchboard"', read(os.path.join('docs', name)))
        head = text[len(LOGO):text.index('```')]
        lines = [x for x in head.splitlines() if x.strip()]
        self.assertEqual(len(lines), 4, lines)
        self.assertFalse(lines[0].startswith(('-', '#', '!', '<')), lines[0])
        features = lines[1:]
        self.assertTrue(all(x.startswith('- **') for x in features), features)
        self.assertIn('Artifacts', features[0].split('**')[1])
        block = text[text.index('```'):]
        self.assertTrue(block.startswith('```sh\n' + BREW + '\n```\n'), block[:120])
        self.assertIn('```sh\n' + INSTALL + '\n```\n', block)               # the second way, further down

    def test_each_feature_is_said_again_in_the_docs(self):
        how = read(os.path.join('docs', 'how-it-works.md'))
        for heading in ('## Artifacts', '## Auto-switch', '## Running as another account'):
            self.assertIn('\n' + heading + '\n', how)


class PlainWords(unittest.TestCase):
    def test_no_hype(self):
        for name in docs():
            if name in THIRD_PARTY:
                continue
            text = read(name)
            for keep in LITERAL:
                text = text.replace(keep, '')
            with self.subTest(doc=name):
                self.assertEqual(HYPE.findall(text), [])


VERSION_HEADING = re.compile(r'(\d+)\.(\d+)\.(\d+) \((\d{4}-\d{2}-\d{2}|not released yet)\)\Z')


class ChangelogVersionHeadings(unittest.TestCase):
    """Keep a Changelog's shape: one `## X.Y.Z (YYYY-MM-DD)` heading per version, newest first. Only the first may say
    "not released yet" in place of its date, and it is the version the program says it is."""

    def headings(self):
        return re.findall(r'^## (.+)$', read('CHANGELOG.md'), re.M)

    def test_the_first_is_this_version_dated_or_not_released_yet(self):
        headings = self.headings()
        self.assertTrue(headings, 'CHANGELOG.md has no version heading')
        self.assertRegex(headings[0], r'^%s \((\d{4}-\d{2}-\d{2}|not released yet)\)\Z' % re.escape(core.VERSION))

    def test_every_later_one_is_a_dated_release_and_versions_go_newest_first(self):
        headings = self.headings()
        for heading in headings[1:]:
            with self.subTest(heading=heading):
                self.assertRegex(heading, r'^\d+\.\d+\.\d+ \(\d{4}-\d{2}-\d{2}\)\Z')
        versions = []
        for heading in headings:
            m = VERSION_HEADING.match(heading)
            self.assertIsNotNone(m, heading)
            versions.append(tuple(int(x) for x in m.groups()[:3]))
        self.assertEqual(versions, sorted(set(versions), reverse=True))

    def test_no_unreleased_heading(self):
        self.assertEqual(re.findall(r'^#+ .*\bunreleased\b.*$', read('CHANGELOG.md'), re.M | re.I), [])


class Links(unittest.TestCase):
    def test_every_link_between_the_docs_lands(self):
        link = re.compile(r'\]\(([^)\s]+)\)')
        for name in docs():
            text = read(name)
            for target in link.findall(text):
                if re.match(r'[a-z]+:', target):
                    continue
                path, _, anchor = target.partition('#')
                where = os.path.normpath(os.path.join(os.path.dirname(name), path)) if path else name
                with self.subTest(doc=name, link=target):
                    self.assertTrue(os.path.exists(os.path.join(ROOT, where)), where)
                    if anchor and where.endswith('.md'):
                        self.assertIn(anchor, anchors(read(where)))


class WhereEachAccountIsSet(unittest.TestCase):
    """An account's rule, its own lines and its rank are set on the page in one Settings row of their own, "Each
    account" (not under Order, which holds only the three orders). Every doc sentence that says where on the page
    one of them is set names that row, so a reader looks in the right place."""

    COMMANDS = ('`switchboard auto last', '`switchboard auto line', '`switchboard auto rank', '`auto last NAME`',
                '`auto line NAME`', '`auto rank NAME')

    def test_the_page_row_holds_all_three(self):
        page = read(os.path.join('switchboard', 'web', 'index.html'))
        row = page[page.index("title: 'Each account'"):]
        row = row[:row.index('\n  ]}')]                # to the end of its group
        for key in ('auto_policy.rules', 'auto_policy.own_lines', 'auto_policy.ranks'):
            self.assertIn(key, row)

    def test_every_doc_sentence_names_that_row(self):
        found = 0
        for name in docs():
            for line in read(name).splitlines():
                if re.search(r'set on the page|: on the page', line) and any(c in line for c in self.COMMANDS):
                    found += 1
                    with self.subTest(doc=name, line=line[:80]):
                        self.assertIn('Settings, Each account', line)
                        self.assertNotIn('under Order', line)
        self.assertGreaterEqual(found, 4)       # the rule (changelog, how it works), own lines, ranks


class NoVendorPosition(unittest.TestCase):
    """Switchboard makes no claim either way about what Anthropic's or OpenAI's terms allow: the docs and the question
    `switchboard hand` asks say what Switchboard does and point to the terms, and never report what a vendor
    recommends, says or does not say."""

    REPORTS = re.compile(r"(OpenAI|Anthropic)(?:'s)? (?:pages? )?(recommends?|does not say|do not say|rules? forbids?)|"
                         r"(recommend|does not say|do not say)[^.]*(a plan sign-in|API keys?)", re.I)

    def test_the_docs(self):
        for name in docs():
            with self.subTest(doc=name):
                self.assertIsNone(self.REPORTS.search(read(name)), name)

    def test_the_codex_answer_says_what_hand_does_and_points_to_the_terms(self):
        faq = read(os.path.join('docs', 'faq.md'))
        answer = faq[faq.index("allowed by OpenAI's terms?"):]
        answer = answer[:answer.index('\n## ')]
        self.assertIn('Switchboard makes no claim either way.', answer)
        self.assertIn('https://openai.com/policies/terms-of-use/', answer)
        self.assertIn("`switchboard hand` runs Codex's exec mode on your ChatGPT plan, and asks you once on this Mac "
                      "before its first job.", answer)

    def test_the_question_hand_asks(self):
        from switchboard.providers import words
        self.assertIsNone(self.REPORTS.search(words.HAND_TERMS), words.HAND_TERMS)
        self.assertIn('no claim', words.HAND_TERMS)
        self.assertIn('openai.com/policies/terms-of-use', words.HAND_TERMS)
        self.assertTrue(words.HAND_TERMS.endswith('Go on? [y/N] '))


class WhatPlainDoctorDoes(unittest.TestCase):
    """Plain `switchboard doctor` repairs nothing (`--fix` does), but it is not true that it writes nothing: like other
    commands it notes where a terminal found `claude` (connect._remember), and it refreshes the cache of what macOS
    trusts (trust.py, mac-trust.json). The docs claim exactly what is so."""

    def test_repairs_nothing_never_writes_nothing(self):
        for name in docs():
            text = read(name)
            for line in text.splitlines():
                if 'doctor' in line and ('only looks' in line or 'writes nothing' in line):
                    with self.subTest(doc=name):
                        self.fail('%s claims plain doctor writes nothing: %s' % (name, line[:120]))
        row = [x for x in read(os.path.join('docs', 'commands.md')).splitlines() if x.startswith('| `switchboard doctor` |')]
        self.assertEqual(len(row), 1)
        self.assertIn('It repairs nothing', row[0])
        self.assertIn('`claude`', row[0])


class NoPlanNobodyWasTold(unittest.TestCase):
    def test_no_5_hour_limit_is_said_without_a_plans_name(self):
        how = read(os.path.join('docs', 'how-it-works.md'))
        self.assertNotIn('verified Ultra', how)
        self.assertIn('An account with no 5-hour limit (some plans report none) has no 5-hour line', how)


class Commands(unittest.TestCase):
    def test_the_readmes_commands_are_in_the_reference(self):
        text = read('README.md')
        block = text[text.index('## Usage'):text.index('## How fresh the numbers are')]
        block = block[block.index('```') + 3:]
        block = block[:block.index('```')]
        reference = read(os.path.join('docs', 'commands.md'))
        for line in block.strip().splitlines():
            words = re.split(r'\s{2,}', line.strip())[0].split()
            if len(words) > 1 and not words[1].isdigit():
                with self.subTest(command=words[1]):
                    self.assertIn('`switchboard %s' % words[1], reference)


if __name__ == '__main__':
    unittest.main()
