import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The words of Switchboard's other assistants (DESIGN 2 R10, 12.0, 15; LANES lane D; ADDENDA D1 and G6).
#
# Glossary, as switchboard/providers/words.py uses the words, and what this file checks of it:
#   provider   the thing on the board and the word typed after a command (switchboard add codex): Claude, Codex.
#              The help says PROVIDER and "provider", never "assistant".
#   assistant  only in a sentence about carrying work on ("carry on in another assistant"), or in the one sentence
#              that names an unknown word ("does not know an assistant called ...").
#   vendor     the company (Anthropic, OpenAI): a sentence names the company itself; the word "vendor" is never
#              printed.
#   app        the program that runs (Claude Code, Codex, the ChatGPT app).
#
# - Every sentence in words.py, every help line, the new docs sections and the CHANGELOG are held against one regex
#   of words that would say a limit is got around, plus "avoid" near a limit, cap, quota or reset, plus the two
#   phrases "already pay" and "paid for" (the thing is "carry on in another assistant", never "you already pay for").
# - The help lines say what they do, and use none of engine, parity, bring, Rank, Token or a hype word.
# - Every History sentence is filed under the kind the command passes (history.from_event, as core.event files it),
#   and none matches a history.RULES pattern (each of those is a switch, a sign-in or a setting of another kind).
# - PLAN_NAMES covers every PlanType of Codex 0.169.1's app-server schema, by name or as PLAN_OTHER, and the work
#   plans among them are marked as such.
import os
import re
import string
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import history  # noqa: E402
from switchboard.providers import words  # noqa: E402

NEVER = re.compile(r'(?i)\b(bypass\w*|circumvent\w*|evad\w+|sidestep\w*|dodg\w+|beat(ing)? (the|a|your) (limit|cap)|'
                   r'get(ting)? (around|past)|work(ing)?[ -]around|rotat\w+|pool(ed|ing)?|split\w* usage|'
                   r'never (run|hit)\w* (out|a limit)|unlimited|endless|keep going|instead of waiting)\b')
AVOID = re.compile(r'(?i)avoid\w*')
NEAR_A_LIMIT = re.compile(r'(?i)limit|cap|quota|reset')
PAY = re.compile(r'(?i)already pay|paid for')
HELP_NEVER = re.compile(r'(?i)\bengine\b|\bparity\b|\bbring\b|\bRank\b|\bToken\b|seamless|effortless|magic|unlock|'
                        r'leverag|empower|elevat|streamlin|supercharg')
# PlanType of codex-cli 0.169.1's app-server schema (codex_app_server_protocol.v2.schemas.json, read with the
# program's own schema generator under an empty CODEX_HOME; nothing of a person's in it).
PLAN_TYPES_0_169_1 = ('free', 'go', 'plus', 'pro', 'prolite', 'promax', 'team', 'self_serve_business_prolite',
                      'self_serve_business_usage_based', 'business', 'ent26', 'enterprise_cbp_automation',
                      'enterprise_cbp_usage_based', 'enterprise', 'edu', 'edu_plus', 'edu_pro', 'sci', 'sci_plus',
                      'unknown')
WORK = ('team', 'self_serve_business_prolite', 'self_serve_business_usage_based', 'business', 'ent26',
        'enterprise_cbp_automation', 'enterprise_cbp_usage_based', 'enterprise', 'edu', 'edu_plus', 'edu_pro')
SAMPLE = {'to': 'Codex', 'frm': 'Claude', 'basename': 'site', 'size': '14 KB',
          'places': '3 places that looked like a secret', 'took': '42 s', 'why': 'time limit', 'name': 'Codex',
          'names': 'Codex, then Claude', 'p': 'Claude'}


def sentences():
    """(name, text) for every sentence in words.py: its strings, and the strings in its dicts and tuples."""
    out = []

    def take(name, v):
        if isinstance(v, str):
            out.append((name, v))
        elif isinstance(v, dict):
            for k, x in v.items():
                take('%s[%r]' % (name, k), x)
        elif isinstance(v, (tuple, list)):
            for i, x in enumerate(v):
                take('%s[%d]' % (name, i), x)

    for name in dir(words):
        if not name.startswith('_') and name.isupper():
            take(name, getattr(words, name))
    return out


def problems(text):
    found = [m.group(0) for m in NEVER.finditer(text)]
    for m in AVOID.finditer(text):
        if NEAR_A_LIMIT.search(text[max(0, m.start() - 60):m.end() + 60]):
            found.append(m.group(0) + ' (near a limit)')
    found.extend(m.group(0) for m in PAY.finditer(text))
    return found


def read(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return f.read()


def section(text, heading):
    """The lines under a '## ' heading, up to the next one."""
    start = text.index('\n' + heading + '\n')
    end = text.find('\n## ', start + 1)
    return text[start:end if end > 0 else len(text)]


class NoWayAround(unittest.TestCase):
    def test_the_regex_catches_what_it_must(self):            # a guard that catches nothing proves nothing
        for bad in ('bypass the limit', 'Get around your cap', 'work-around', 'rotating accounts', 'pooled plans',
                    'split usage', 'never run out', 'unlimited', 'keep going', 'instead of waiting',
                    'avoid the weekly limit', 'the plans you already pay for', 'a plan you paid for'):
            self.assertTrue(problems(bad), bad)
        for good in ('carry on in another assistant', 'Codex is out until Wed 2:00 AM.', 'keep it'):
            self.assertEqual(problems(good), [], good)

    def test_every_sentence_in_words(self):
        got = sentences()
        self.assertGreater(len(got), 100)
        for name, text in got:
            with self.subTest(name=name):
                self.assertEqual(problems(text), [], text)

    def test_the_docs_and_the_changelog(self):
        for rel in ('docs/commands.md', 'docs/how-it-works.md', 'docs/faq.md', 'SECURITY.md', 'CHANGELOG.md'):
            text = read(rel)
            for i, line in enumerate(text.splitlines(), 1):
                with self.subTest(file=rel, line=i):
                    self.assertEqual(problems(line), [], line)

    def test_the_new_docs_are_there_and_say_the_sentence(self):
        commands = section(read('docs/commands.md'), '## Other assistants')
        for cmd in ('providers', 'add PROVIDER', 'continue', 'back', 'hand', 'allow', 'stay', 'order'):
            self.assertIn('| `switchboard %s' % cmd, commands)
        how = section(read('docs/how-it-works.md'), '## Other assistants')
        self.assertIn('carry on in another assistant', how)
        changelog = read('CHANGELOG.md')
        self.assertIn('**Other assistants**', changelog)
        self.assertIn('Is using Codex through Switchboard allowed by OpenAI', read('docs/faq.md'))

    def test_security_says_what_the_claude_provider_reads(self):
        line = next(x for x in read('SECURITY.md').splitlines() if 'other assistants' in x)
        self.assertIn("reads the `active` key of Switchboard's own `~/.switchboard/state.json`, which never holds a "
                      "login", line)

    def test_the_readme_names_what_leaves_the_mac(self):
        # A note of a chat, or a job, goes to another assistant's maker after your yes for that folder: the README's
        # sentence on what leaves the Mac says so.
        line = next(x for x in read('README.md').splitlines() if 'sends nothing of yours off your Mac' in x)
        self.assertRegex(line, r'Codex|OpenAI|another assistant')
        self.assertRegex(line, r'(?i)\byes\b.*\bfolder|\bfolder\b.*\byes\b')

    def test_one_true_sentence_about_git(self):           # lane D second pass, D6
        how = section(read('docs/how-it-works.md'), '## Other assistants')
        security = [line for line in read('SECURITY.md').splitlines() if 'other assistants' in line]
        self.assertEqual(len(security), 1)
        for where, text in (('how-it-works', how), ('SECURITY', security[0])):
            with self.subTest(where=where):
                flat = ' '.join(text.split())
                self.assertIn('runs git in the folder', flat)
                self.assertIn("with the folder's hooks and its file monitor turned off", flat)
                self.assertIn("a folder's own git settings can still make git start a program", flat)
                self.assertIn('only from folders you trust', flat)
        self.assertNotIn('The next app.** It starts in the same folder', how)  # it starts where the work is (D4)


class TheGlossary(unittest.TestCase):
    def test_vendor_is_never_printed(self):
        for name, text in sentences():
            with self.subTest(name=name):
                self.assertNotRegex(re.sub(r'\{\w*\}', '', text), r'(?i)\bvendors?\b')   # {vendor} is filled in

    def test_assistant_only_about_carrying_work_on(self):
        for name, text in sentences():
            if re.search(r'(?i)\bassistants?\b', text):
                with self.subTest(name=name):
                    self.assertRegex(text, r'(?i)carr|another assistant|an assistant called')

    def test_the_help_says_provider(self):
        for cmd, block in words.HELP.items():
            with self.subTest(cmd=cmd):
                self.assertNotRegex(block, r'(?i)\bassistant')


class TheHelp(unittest.TestCase):
    def test_each_line_says_what_it_does(self):          # as test_cli_words.py asks of every help line
        for cmd, block in words.HELP.items():
            with self.subTest(cmd=cmd):
                lines = block.splitlines()
                self.assertTrue(lines[0].startswith('switchboard %s' % cmd), lines[0])
                first = re.split(r'\s{2,}', lines[0], maxsplit=1)
                says = first[1] if len(first) > 1 else (lines[1].strip() if len(lines) > 1 else '')
                self.assertGreater(len(says.split()), 3, block)
                for line in lines:
                    self.assertLessEqual(len(line) + 2, 120, line)          # with the docstring's own indent
                    self.assertEqual(problems(line), [], line)
                    self.assertNotRegex(line, HELP_NEVER)

    def test_every_command_has_its_lines(self):
        from switchboard.providers import cli
        self.assertEqual(sorted(words.HELP), sorted(cli.COMMANDS))


class History(unittest.TestCase):
    def fill(self, template):
        names = [f for _, f, _, _ in string.Formatter().parse(template) if f]
        return template.format(**{n: SAMPLE[n] for n in names})

    def test_each_sentence_is_filed_under_its_own_kind(self):
        self.assertEqual(len(words.HISTORY), 17)          # with switchboard do's two, and remove's one
        for template, kind in words.HISTORY:
            text = self.fill(template)
            with self.subTest(text=text):
                self.assertIn(kind, ('info', 'setting', 'problem'))
                self.assertIn(kind, history.KINDS)
                self.assertFalse(any(rx.match(text) for rx, _, _, _ in history.RULES), text)
                rec = history.from_event(text, 'warn' if kind == 'problem' else 'info', {'h': kind, 'by': 'you'})
                self.assertEqual((rec['kind'], rec['text']), (kind, text))
                self.assertNotIn('account', rec)
                self.assertEqual(history.classify(text, 'warn' if kind == 'problem' else 'info'),
                                 ('problem' if kind == 'problem' else 'info', {}))

    def test_no_sentence_holds_a_path_or_a_chat_name(self):
        for template, _ in words.HISTORY:
            fields = {f for _, f, _, _ in string.Formatter().parse(template) if f}
            with self.subTest(template=template):
                self.assertLessEqual(fields, {'to', 'frm', 'basename', 'size', 'places', 'took', 'why', 'name',
                                              'names', 'p'})


class Plans(unittest.TestCase):
    def test_every_plan_type_prints(self):
        for plan in PLAN_TYPES_0_169_1:
            with self.subTest(plan=plan):
                shown = words.PLAN_NAMES.get(plan, words.PLAN_OTHER)
                self.assertTrue(shown.startswith('ChatGPT'), shown)
                self.assertEqual(bool(words.WORK_PLAN_RE.match(plan)), plan in WORK, plan)
        self.assertLessEqual(set(words.PLAN_NAMES), set(PLAN_TYPES_0_169_1))


class TimeAndSize(unittest.TestCase):
    NOW = 1795600000.0

    def test_ago(self):
        for s, want in ((0, 'just now'), (59, 'just now'), (60, '1 min ago'), (720, '12 min ago'),
                        (3600, '1 hour ago'), (7200, '2 hours ago'), (86400, '1 day ago'), (9 * 86400, '9 days ago'),
                        (-5, 'just now')):
            self.assertEqual(words.ago(s), want, s)

    def test_size_took_places_names(self):
        self.assertEqual([words.size(n) for n in (0, 820, 14000, 1234567)], ['0 bytes', '820 bytes', '14 KB',
                                                                              '1.2 MB'])
        self.assertEqual([words.took(s) for s in (42.4, 89, 180)], ['42 s', '89 s', '3 min'])
        self.assertEqual(words.places(1), '1 place that looked like a secret')
        self.assertEqual(words.places(3), '3 places that looked like a secret')
        self.assertEqual(words.names(['Claude', 'Codex']), 'Claude, then Codex')

    def test_when(self):
        import time
        lt = time.localtime(self.NOW)
        noon = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 12, 0, 0, 0, 0, -1))
        self.assertEqual(words.when(noon + 4 * 3600 + 600, noon), '4:10 PM')
        two_days = noon + 2 * 86400 - 10 * 3600
        self.assertEqual(words.when(two_days, noon), time.strftime('%a', time.localtime(two_days)) + ' 2:00 AM')
        far = noon + 30 * 86400
        self.assertEqual(words.when(far, noon), words.date(far, noon))
        self.assertRegex(words.date(far, noon), r'^[A-Z][a-z]{2} \d{1,2}$')
        self.assertEqual(words.when(None, noon), 'a time not known')


if __name__ == '__main__':
    unittest.main()
