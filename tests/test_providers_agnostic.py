import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The shared code of switchboard/providers knows no provider by name: each per-provider fact is on the provider's own
# adapter, and the shared modules ask the adapter.
#
# - No shared module holds a provider id in quotes ('claude', "codex"), in code, a comment or a docstring. The shared
#   modules are every module of the package but the registry (__init__.py, the one list of adapters), the adapters
#   (claude.py, codex.py), their facts tables (claude_facts.py, codex_facts.py, and chat_facts.py, the readers' one
#   table) and their chat readers (claude_chats.py, codex_chats.py). Nor does a shared module import one of those
#   adapter modules (chat_facts, the readers' shared table, may be read).
# - Each fact the shared modules used to keep by id is the adapter's own now, with the same words as before, so the
#   commands say exactly what they said.
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from switchboard import providers  # noqa: E402
from switchboard.providers import contract, words  # noqa: E402

PACKAGE = os.path.join(HERE, '..', 'switchboard', 'providers')
ADAPTER_SIDE = ('__init__.py', 'claude.py', 'codex.py', 'claude_facts.py', 'codex_facts.py', 'chat_facts.py',
                'claude_chats.py', 'codex_chats.py')


def shared_modules():
    return sorted(n for n in os.listdir(PACKAGE) if n.endswith('.py') and n not in ADAPTER_SIDE)


class SharedCodeNamesNoProvider(unittest.TestCase):
    def test_the_list_of_shared_modules_is_the_package_less_the_adapter_side(self):
        names = shared_modules()
        for must in ('cli.py', 'handoff.py', 'words.py', 'choices.py', 'place.py', 'tasks.py', 'contract.py',
                     'run.py'):
            self.assertIn(must, names)
        for side in ADAPTER_SIDE:
            self.assertTrue(os.path.isfile(os.path.join(PACKAGE, side)), side)

    def test_no_shared_module_holds_a_provider_id_in_quotes(self):
        ids = providers.ids()
        self.assertGreaterEqual(len(ids), 2)
        quoted = re.compile(r'''(['"])(%s)\1''' % '|'.join(map(re.escape, ids)))
        found = []
        for name in shared_modules():
            with open(os.path.join(PACKAGE, name), encoding='utf-8') as f:
                for n, line in enumerate(f, 1):
                    if quoted.search(line):
                        found.append('%s:%d: %s' % (name, n, line.strip()))
        self.assertEqual(found, [])

    def test_no_shared_module_imports_an_adapter_module(self):
        adapter = re.compile(r'^\s*(from\s+\.(claude|codex)\w*\s+import|from\s+\.\s+import\s+.*\b(claude|codex)\w*)')
        found = []
        for name in shared_modules():
            with open(os.path.join(PACKAGE, name), encoding='utf-8') as f:
                for n, line in enumerate(f, 1):
                    if adapter.search(line):
                        found.append('%s:%d: %s' % (name, n, line.strip()))
        self.assertEqual(found, [])


class EachAdapterHoldsItsOwnFacts(unittest.TestCase):
    """The words the shared modules used to keep by id, now on each adapter, unchanged."""

    def cls(self, pid):
        got = providers.adapter_class(pid)
        self.assertIsNotNone(got, pid)
        return got

    def test_the_contract_declares_each_fact_with_a_default_that_names_no_provider(self):
        p = contract.Provider
        self.assertIsNone(p.app_name)
        self.assertEqual(p.sign_in, '')
        self.assertEqual(p.billing_prefixes, ())
        self.assertEqual(dict(p.cannot), {})
        self.assertEqual(p.job_question, '')
        self.assertEqual(p.own_rules, '')
        self.assertIs(p.account_list, False)
        self.assertEqual((p.session_env, p.rule_files, p.rule_dirs), ('', (), ()))
        self.assertEqual(p.plan_phrase, '')

    def test_claude(self):
        c = self.cls(providers.ids()[0])
        self.assertEqual((c.name, c.app_name), ('Claude', 'Claude Code'))
        self.assertEqual(c.sign_in, 'run /login in Claude Code')
        self.assertEqual(c.billing_prefixes, ('ANTHROPIC_', 'CLAUDE_CODE_'))
        self.assertEqual(dict(c.cannot), {contract.JOB: words.NO_CLAUDE_JOB, contract.START_APP: words.NO_CLAUDE_LINK})
        self.assertEqual(c.job_question, '')
        self.assertEqual(c.own_rules, '~/.claude/CLAUDE.md, memory, skills, hooks')
        self.assertIs(c.account_list, True)
        self.assertEqual(c.session_env, 'CLAUDE_CODE_SESSION_ID')
        self.assertEqual(c.rule_files, ('CLAUDE.md', 'CLAUDE.local.md', os.path.join('.claude', 'CLAUDE.md')))
        self.assertEqual(c.rule_dirs, (os.path.join('.claude', 'rules'),))
        self.assertEqual(c.plan_phrase, 'Claude plan')

    def test_codex(self):
        c = self.cls(providers.ids()[1])
        self.assertEqual((c.name, c.app_name), ('Codex', None))
        self.assertEqual(c.sign_in, 'run codex login, or open the ChatGPT app')
        self.assertEqual(c.billing_prefixes, ('OPENAI_', 'CODEX_'))
        self.assertEqual(dict(c.cannot), {})
        self.assertEqual(c.job_question, words.HAND_TERMS)
        self.assertEqual(c.own_rules, '')
        self.assertIs(c.account_list, False)
        self.assertEqual((c.session_env, c.rule_files, c.rule_dirs), ('', ('AGENTS.md', 'AGENTS.override.md'), ()))
        self.assertEqual(c.plan_phrase, 'ChatGPT plan')

    def test_every_billing_prefix_starts_a_name_in_billing_env(self):
        for pid in providers.ids():
            for prefix in self.cls(pid).billing_prefixes:
                self.assertTrue(any(n.startswith(prefix) for n in contract.BILLING_ENV), (pid, prefix))

    def test_one_provider_is_served_by_the_account_list_and_it_is_the_first(self):
        served = [pid for pid in providers.ids() if self.cls(pid).account_list]
        self.assertEqual(served, [providers.ids()[0]])

    def test_the_notes_rule_files_are_every_adapters_in_the_registrys_order(self):
        from switchboard.providers import handoff
        self.assertEqual(handoff.rule_names(), (('CLAUDE.md', 'CLAUDE.local.md', os.path.join('.claude', 'CLAUDE.md'),
                                                 'AGENTS.md', 'AGENTS.override.md'), (os.path.join('.claude', 'rules'),)))

    def test_the_tables_keyed_by_id_are_gone_from_words(self):
        for gone in ('APP_NAMES', 'SIGN_IN', 'PROVIDER_NAMES', 'PLAN_OF'):
            self.assertFalse(hasattr(words, gone), gone)


if __name__ == '__main__':
    unittest.main()
