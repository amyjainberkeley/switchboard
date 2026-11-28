import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The kind of job a task is (switchboard/providers/kinds.py): fixed rules over the task's first sentence.

- A table of made-up tasks, each with the kind and the word or phrase that decided it.
- The rules: the first sentence only (and at most its first CUT characters); whole words only ("address" is not
  "add"); the earliest match wins; at the same place the longer phrase wins; then the order of KINDS.
- Nothing matched, an empty task, or a task of blanks: ('other', None).
- The same words always give the same answer, and the table holds each word once, in lower case.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard.providers import kinds  # noqa: E402

CASES = (
    ('review the changes on this branch', 'review', 'review'),
    ('Review my pull request before I merge it', 'review', 'review'),
    ('audit the auth module for injection risks', 'review', 'audit'),
    ('Give me a second opinion on this migration plan', 'review', 'second opinion'),
    ('Can you look over the new parser?', 'review', 'look over'),
    ('critique the onboarding copy', 'review', 'critique'),
    ('fix the failing login test', 'fix', 'fix'),
    ('debug why the worker crashes on start', 'fix', 'debug'),
    ('The upload button is broken on Safari', 'fix', 'broken'),
    ('Why does the cache never expire?', 'fix', 'why does'),
    ('why is the build so slow', 'fix', 'why is'),
    ('tests are failing after the upgrade', 'fix', 'failing'),
    ('what is wrong with the cache', 'fix', 'what is wrong'),
    ('the parser in foo.py is broken', 'fix', 'broken'),
    ('find where we parse the config file', 'find', 'find'),
    ('Where is the retry logic?', 'find', 'where'),
    ('which files import the old client', 'find', 'which'),
    ('list every endpoint that needs a login', 'find', 'list'),
    ('explain what this regex does', 'find', 'explain'),
    ('What does the --strict flag do?', 'find', 'what does'),
    ('look up how retries are configured', 'find', 'look up'),
    ('what is the default timeout', 'find', 'what is'),
    ('plan the move to the new database', 'plan', 'plan'),
    ('design a schema for the audit log', 'plan', 'design'),
    ('propose three ways to speed up the tests', 'plan', 'propose'),
    ('outline the steps for the release', 'plan', 'outline'),
    ('How should we split this service?', 'plan', 'how should'),
    ('draft a reply to the issue', 'write', 'draft'),
    ('summarize the last week of commits', 'write', 'summarize'),
    ('summarise the discussion in the thread', 'write', 'summarise'),
    ('document the public functions', 'write', 'document'),
    ('write up what changed today', 'write', 'write up'),
    ('write the readme for the importer', 'write', 'write the readme'),
    ('write the release notes for this week', 'write', 'write the release notes'),
    ('Write a blog post about the launch', 'write', 'write a blog post'),
    ('write an announcement for the team', 'write', 'write an announcement'),
    ('update the README with the new flags', 'build', 'update'),
    ('Add a dark mode toggle', 'build', 'add'),
    ('implement retries in the HTTP client', 'build', 'implement'),
    ('build a CLI for the importer', 'build', 'build'),
    ('create a new settings page', 'build', 'create'),
    ('make the sidebar collapsible', 'build', 'make'),
    ('refactor the payment module', 'build', 'refactor'),
    ('migrate the tests to pytest', 'build', 'migrate'),
    ('rename getUser to fetchUser', 'build', 'rename'),
    ('port the script to Python 3', 'build', 'port'),
    ('write a function that parses dates', 'build', 'write a'),
    ('write the tests for the parser', 'build', 'write the'),
    ('Fix the header. Then review the footer.', 'fix', 'fix'),
    ('Thanks! Now review this', 'other', None),
    ('Please address the comments on my pull request', 'other', None),
    ('hello there', 'other', None),
    ('1. fix the flaky upload test', 'fix', 'fix'),
    ('Find and fix the bug in the uploader', 'fix', 'fix'),
    ('The button which opens settings is broken', 'fix', 'broken'),
    ('list the failing tests and fix them', 'fix', 'failing'),
    ('explain how to add a new provider', 'build', 'add'),
)


class TheTable(unittest.TestCase):
    def test_each_made_up_task(self):
        self.assertGreaterEqual(len(CASES), 40)
        for task, kind, word in CASES:
            with self.subTest(task=task):
                self.assertEqual(kinds.kind_of(task), (kind, word))

    def test_every_kind_is_reached_by_the_table(self):
        self.assertEqual({k for _, k, _ in CASES}, set(kinds.KINDS))

    def test_the_kinds(self):
        self.assertEqual(kinds.KINDS, ('build', 'fix', 'review', 'plan', 'find', 'write', 'other'))
        self.assertEqual(set(kinds.TABLE), set(kinds.KINDS) - {'other'})

    def test_each_word_once_in_lower_case_single_spaced(self):
        seen = {}
        for kind, phrases in kinds.TABLE.items():
            for p in phrases:
                with self.subTest(phrase=p):
                    self.assertEqual(p, ' '.join(p.lower().split()))
                    self.assertNotIn(p, seen, 'in %s and %s' % (seen.get(p), kind))
                    seen[p] = kind


class TheRules(unittest.TestCase):
    def test_whole_words_only(self):
        self.assertEqual(kinds.kind_of('addresses and adders'), ('other', None))
        self.assertEqual(kinds.kind_of('the reviewer_id column'), ('other', None))
        self.assertEqual(kinds.kind_of('re-add the guard'), ('build', 'add'))

    def test_the_earliest_match_wins(self):
        self.assertEqual(kinds.kind_of('update the docs, then review them'), ('build', 'update'))
        self.assertEqual(kinds.kind_of('review the update'), ('review', 'review'))

    def test_a_lookup_only_when_no_word_of_another_kind_is_there(self):
        self.assertEqual(kinds.kind_of('where is the config read'), ('find', 'where'))
        self.assertEqual(kinds.kind_of('where is the bug'), ('fix', 'bug'))
        self.assertEqual(kinds.kind_of('which test fails and why'), ('fix', 'fails'))
        self.assertEqual(kinds.kind_of('list the routes, then review them'), ('review', 'review'))
        self.assertEqual(kinds.kind_of('find the summary and draft a reply'), ('write', 'summary'))   # earliest of the rest

    def test_at_the_same_place_the_longer_phrase_wins(self):
        self.assertEqual(kinds.kind_of('what is wrong here'), ('fix', 'what is wrong'))
        self.assertEqual(kinds.kind_of('what is here'), ('find', 'what is'))
        self.assertEqual(kinds.kind_of('write the readme'), ('write', 'write the readme'))
        self.assertEqual(kinds.kind_of('write the parser'), ('build', 'write the'))

    def test_write_a_or_the_is_writing_when_prose_comes_next(self):
        for noun in kinds.PROSE:               # "write the release notes" is writing, "write the parser" a build
            for article in ('a', 'an', 'the'):
                phrase = 'write %s %s' % (article, noun)
                with self.subTest(phrase=phrase):
                    self.assertIn(phrase, kinds.TABLE['write'])
                    self.assertEqual(kinds.kind_of(phrase + ' for this week'), ('write', phrase))
        self.assertEqual(kinds.kind_of('write a test for the summary endpoint'), ('build', 'write a'))
        self.assertEqual(kinds.kind_of('write the code that sends the email'), ('build', 'write the'))

    def test_then_the_order_of_the_kinds(self):          # a made-up table holding one word under two kinds
        table = {'review': ('look',), 'fix': ('look',)}
        self.assertEqual(kinds.kind_of('look here', table=table), ('fix', 'look'))
        table = {'write': ('look',), 'plan': ('look',)}
        self.assertEqual(kinds.kind_of('look here', table=table), ('plan', 'look'))

    def test_only_the_first_sentence(self):
        self.assertEqual(kinds.kind_of('Thanks. Fix the login.'), ('other', None))
        self.assertEqual(kinds.kind_of('Hello!\nfix the login'), ('other', None))
        self.assertEqual(kinds.kind_of('Is it done? review it'), ('other', None))
        self.assertEqual(kinds.kind_of('see foo.py and fix it'), ('fix', 'fix'))     # a dot inside a word goes on

    def test_a_first_sentence_longer_than_the_cut(self):
        filler = 'we have a long story here about the project and its history ' * 6
        task = filler + 'and now please review it'
        self.assertGreater(len(filler), kinds.CUT)
        self.assertEqual(kinds.kind_of(task), ('other', None))
        near = 'x' * (kinds.CUT - len(' review')) + ' review'
        self.assertEqual(len(near), kinds.CUT)
        self.assertEqual(kinds.kind_of(near), ('review', 'review'))
        self.assertEqual(kinds.kind_of('x' + near), ('other', None))       # one character more: past the cut

    def test_nothing_there(self):
        for task in ('', '   ', '\n\n', None, '!!!', '...'):
            with self.subTest(task=task):
                self.assertEqual(kinds.kind_of(task), ('other', None))

    def test_case_and_spacing_do_not_matter(self):
        self.assertEqual(kinds.kind_of('SECOND   OPINION on it'), ('review', 'second opinion'))
        self.assertEqual(kinds.kind_of('Look\tUp the flag'), ('find', 'look up'))

    def test_same_words_same_answer(self):
        for task, _, _ in CASES:
            self.assertEqual(kinds.kind_of(task), kinds.kind_of(task))
            self.assertEqual(kinds.kind_of(task), kinds.kind_of(str(task)))

    def test_it_says_it_is_rules_not_a_model(self):
        doc = ' '.join(kinds.__doc__.split())
        self.assertIn('rules, not a model', doc)


if __name__ == '__main__':
    unittest.main()
