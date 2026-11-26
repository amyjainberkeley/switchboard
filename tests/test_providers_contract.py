import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The shared contract every adapter keeps (switchboard/providers/contract.py, DESIGN 5), proven for every adapter in
# the registry whose module this build carries, and for the commands' stand-in adapter (tests/fakes_provider_kit.py):
#
# - its id matches ID_RE and is the registry's; `can` is a subset of CAPABILITIES;
# - every method outside `can` answers Unsupported without raising; every method inside answers its contract type
#   (or raises ProviderError, the one failure the person can act on);
# - start() and resume() never run anything: the Reach's run, pipe, execve and open_url stay untouched;
# - an adapter module that fails to load gives None and a LOAD_ERRORS entry (its error's type name only), and the
#   other adapter still loads;
# - repr() of a Reach, Launch, Ran, Answer and Event never shows the environment, a chat's words or a program's output;
# - every id in ADAPTERS imports on this Python, once its module is in the build (before the merge the commands' lane
#   has neither adapter: that test says so by skipping each missing one).
#
# The Reach is made up: its home and app folders are empty temp folders, so no adapter finds a real program, and
# tests/_isolate.py refuses to run one called claude or codex anyway. The real adapters are given a found stand-in on
# a made-up PATH (fake-claude; fake-codex, as tests/fakes_codex.py writes it), its version asked once beforehand, so
# start, resume and job reach the type check and the "nothing ran" check instead of ending at "not on this Mac".
import importlib.abc
import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

import switchboard.providers as providers  # noqa: E402
from fakes_provider_kit import NOW, FakeProvider, chat, claude_found, codex_found  # noqa: E402
from fakes_providers import make_reach, stand_in  # noqa: E402
from switchboard.providers import claude_facts, codex_facts, contract  # noqa: E402
from switchboard.providers.contract import (Answer, ChatRef, Event, Found, Handoff, Launch, Limits,  # noqa: E402
                                            ProviderError, Ran, Reach, Seat, Transcript, Unsupported)

MARK = 'canary-0f1e2d3c-made-up'
PACKAGE = os.path.dirname(providers.__file__)


def recording_reach(root, **kw):
    """A made-up Reach whose run, pipe, execve and open_url only note that they were called."""
    reach, _ = make_reach(root, **kw)
    used = []

    def note(name):
        def f(*a, **k):
            used.append(name)
            raise AssertionError('%s was called' % name)
        return f
    import dataclasses
    return dataclasses.replace(reach, run=note('run'), pipe=note('pipe'), execve=note('execve'),
                               open_url=note('open_url')), used


def a_handoff(folder):
    path = os.path.join(folder, 'h-0000000a.md')
    return Handoff(hop='h-0000000a', path=path, meta_path=path[:-3] + '.json', bytes=10, sha256='0' * 64)


class Contract:
    """The checks for one adapter: self.make(reach) builds it. found: it has a program, so every start, resume and
    job must answer its type (a ProviderError there is a failure)."""
    found = False

    def make(self, reach):
        raise NotImplementedError

    def reach_kw(self):
        """The made-up Reach's own words (a PATH holding a stand-in program, for the real adapters)."""
        return {}

    def setUp(self):
        super().setUp()
        self.root = os.path.realpath(tempfile.mkdtemp(prefix='sb-providers-contract-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.folder = os.path.join(self.root, 'site')
        os.makedirs(self.folder)

    def calls(self, prov):
        """(what, the call, the contract type it answers) for every method."""
        c = chat(prov.id, self.folder)
        h = a_handoff(self.folder)
        out = os.path.join(self.root, 'answer.txt')
        return [
            (contract.DETECT, lambda: prov.detect(), Found),
            (contract.SEATS, lambda: prov.seats(), tuple),
            (contract.LIMITS, lambda: prov.limits(), tuple),
            (contract.ASK, lambda: prov.limits(ask=True), tuple),
            (contract.CHATS, lambda: prov.chats(self.folder), tuple),
            (contract.READ, lambda: prov.read(c), Transcript),
            (contract.START, lambda: prov.start(self.folder, h, contract.IN_TERMINAL), Launch),
            (contract.START, lambda: prov.start(self.folder, h, contract.PRINT), Launch),
            (contract.START_APP, lambda: prov.start(self.folder, h, contract.IN_APP), Launch),
            (contract.RESUME, lambda: prov.resume(c, h, contract.IN_TERMINAL), Launch),
            (contract.JOB, lambda: prov.job(self.folder, 'made-up job', out, 5.0), Answer),
            (contract.NOTE, lambda: prov.note(c), str),
        ]

    def test_its_names(self):
        prov = self.make(make_reach(self.root, **self.reach_kw())[0])
        self.assertRegex(prov.id, contract.ID_RE)
        self.assertIn(prov.id, providers.ids())
        self.assertTrue(prov.name and prov.vendor)
        self.assertIsInstance(prov.can, frozenset)
        self.assertLessEqual(prov.can, contract.CAPABILITIES)

    def test_every_method_answers_its_type_or_unsupported(self):
        prov = self.make(make_reach(self.root, **self.reach_kw())[0])
        for what, call, kind in self.calls(prov):
            with self.subTest(what=what):
                try:
                    got = call()
                except ProviderError as e:
                    self.assertIn(what, prov.can, 'a method outside can raised %r' % e)
                    if self.found and what in (contract.START, contract.START_APP, contract.RESUME, contract.JOB):
                        self.fail('%s raised %r although a program was found' % (what, e))
                    self.assertIn(e.kind, ProviderError.KINDS)
                    continue
                if what in prov.can:
                    self.assertIsInstance(got, (kind, Unsupported) if what == contract.START_APP else kind)
                    if kind is tuple:
                        inner = {contract.SEATS: Seat, contract.LIMITS: Limits, contract.ASK: Limits,
                                 contract.CHATS: ChatRef}[what]
                        self.assertTrue(all(isinstance(x, inner) for x in got), got)
                else:
                    self.assertIsInstance(got, Unsupported)
                    self.assertEqual((got.provider, got.what), (prov.id, what))
                    self.assertTrue(got.why)

    def test_start_and_resume_never_run_anything(self):
        reach, used = recording_reach(self.root, **self.reach_kw())
        prov = self.make(reach)
        h = a_handoff(self.folder)
        made = 0
        for how in contract.HOW:
            for call in (lambda: prov.start(self.folder, h, how), lambda: prov.resume(chat(prov.id, self.folder),
                                                                                       h, how)):
                try:
                    made += isinstance(call(), Launch)
                except ProviderError:
                    if self.found:
                        raise
        self.assertEqual(used, [])
        if self.found:
            self.assertGreaterEqual(made, 3)        # in a terminal and printed, at least: each a Launch


class TheStandInAdapter(Contract, unittest.TestCase):
    """The commands' tests stand on FakeProvider: it keeps the same contract as a real adapter."""

    def make(self, reach):
        prov = FakeProvider(reach, 'codex', found=codex_found(self.root))
        prov.chats_ = [chat('codex', self.folder)]
        return prov


class TheStandInClaude(Contract, unittest.TestCase):
    def make(self, reach):
        return FakeProvider(reach, 'claude', found=claude_found(self.root))


def _real(pid):
    cls = providers.adapter_class(pid)
    return cls


FAKE_CLAUDE = "import sys\nif sys.argv[1:] == ['--version']:\n    sys.stdout.write('2.1.330 (Claude Code)\\n')\n"


class Real(Contract):
    """A real adapter, with a stand-in program found on a made-up PATH; its version is asked once in setUp (as the
    commands' detect does before a start), so a start or resume after it runs nothing."""
    found = True
    pid = facts = None

    def setUp(self):
        if _real(self.pid) is None:
            self.skipTest('the %s adapter is not in this build yet (%s)' % (self.pid,
                                                                             providers.LOAD_ERRORS.get(self.pid)))
        super().setUp()
        self.bin = os.path.join(self.root, 'bin')
        for patch in (mock.patch.dict(self.facts, program_names=('fake-' + self.pid,), probe_seconds=60.0),
                      mock.patch.object(claude_facts, 'VERSION_PROBE_S', 60.0)):    # a busy Mac starts it slowly
            patch.start()
            self.addCleanup(patch.stop)
        self.program()
        found = self.make(make_reach(self.root, **self.reach_kw())[0]).detect()
        self.assertIsNotNone(found.program, found)

    def reach_kw(self):
        home = os.path.join(self.root, 'home')
        return {'path_dirs': (self.bin,), 'wall': lambda: NOW + 3600,
                'env': {'HOME': home, 'PATH': self.bin + ':/usr/bin:/bin'}}

    def make(self, reach):
        return _real(self.pid)(reach)


class RealClaude(Real, unittest.TestCase):
    pid, facts = 'claude', claude_facts.FACTS

    def program(self):
        stand_in(self.bin, 'fake-claude', FAKE_CLAUDE)


class RealCodex(Real, unittest.TestCase):
    pid, facts = 'codex', codex_facts.FACTS

    def program(self):
        from fakes_codex import FakeCodex
        FakeCodex(self.bin, '0.169.1', exec={'answer': 'A made-up answer.\n'})


class Boom(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """A module that fails as it loads (a syntax error on an older Python, say)."""

    def __init__(self, name):
        self.name = name

    def find_spec(self, name, path, target=None):
        return importlib.util.spec_from_loader(name, self) if name == self.name else None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        raise SyntaxError('made-up')


class Registry(unittest.TestCase):
    def setUp(self):
        saved = dict(providers.LOAD_ERRORS)
        self.addCleanup(lambda: (providers.LOAD_ERRORS.clear(), providers.LOAD_ERRORS.update(saved)))

    def stand_in_claude(self):
        mod = types.ModuleType('switchboard.providers.claude')

        class ClaudeProvider(FakeProvider):
            def __init__(self, reach):
                super().__init__(reach, 'claude')
        mod.ClaudeProvider = ClaudeProvider
        return mod

    def test_a_module_that_fails_to_load_stops_no_other(self):
        name = 'switchboard.providers.codex'
        boom = Boom(name)
        saved = {k: sys.modules.pop(k) for k in (name,) if k in sys.modules}
        self.addCleanup(sys.modules.update, saved)
        self.addCleanup(sys.modules.pop, name, None)
        sys.meta_path.insert(0, boom)
        self.addCleanup(sys.meta_path.remove, boom)
        with mock.patch.dict(sys.modules, {'switchboard.providers.claude': self.stand_in_claude()}):
            self.assertIsNone(providers.adapter_class('codex'))
            self.assertEqual(providers.LOAD_ERRORS['codex'], 'SyntaxError')
            root = tempfile.mkdtemp(prefix='sb-providers-contract-')
            self.addCleanup(shutil.rmtree, root, True)
            loaded = providers.load_all(make_reach(root)[0])
            self.assertEqual([p.id for p in loaded], ['claude'])
            with self.assertRaises(ProviderError) as e:
                providers.load('codex', make_reach(root)[0])
            self.assertEqual(e.exception.kind, 'not_found')

    def test_a_missing_module_is_named_by_its_error_type_only(self):
        with mock.patch.dict(sys.modules, {'switchboard.providers.codex': None}):
            self.assertIsNone(providers.adapter_class('codex'))
        self.assertEqual(providers.LOAD_ERRORS['codex'], 'ModuleNotFoundError')

    def test_a_module_without_its_class(self):
        with mock.patch.dict(sys.modules, {'switchboard.providers.codex': types.ModuleType('x')}):
            self.assertIsNone(providers.adapter_class('codex'))
        self.assertEqual(providers.LOAD_ERRORS['codex'], 'NoAdapter')

    def test_only_registry_ids_are_known(self):
        for word in ('claude', 'codex'):
            self.assertTrue(providers.known(word))
        for word in ('Codex', '../codex', 'codex ', '', None, 'contract', 'cli', 'words', 'gemini'):
            self.assertFalse(providers.known(word), word)

    def test_every_adapter_imports_on_this_python(self):
        for pid, module, cls in providers.ADAPTERS:
            with self.subTest(pid=pid):
                if not os.path.exists(os.path.join(PACKAGE, module + '.py')):
                    self.skipTest('%s.py is not in this build yet' % module)
                self.assertIsNotNone(providers.adapter_class(pid), providers.LOAD_ERRORS.get(pid))


class Repr(unittest.TestCase):
    def test_nothing_private_in_a_repr(self):
        root = tempfile.mkdtemp(prefix='sb-providers-contract-')
        self.addCleanup(shutil.rmtree, root, True)
        reach, _ = make_reach(root, env={'OPENAI_API_KEY': MARK, 'HOME': root})
        values = [
            reach,
            Launch('codex', contract.IN_TERMINAL, None, ('/made-up/fake-codex',), env={'X': MARK}),
            Ran(code=1, out=MARK, err=MARK),
            Answer('codex', True, text=MARK, path='/made-up/answer.txt'),
            Event(contract.PERSON, text=MARK),
            Transcript(chat('codex', root), (Event(contract.ASSISTANT, text=MARK),), contract.ReadInfo()),
        ]
        for v in values:
            with self.subTest(kind=type(v).__name__):
                self.assertNotIn(MARK, repr(v))
        self.assertIsInstance(values[0], Reach)
        self.assertGreater(NOW, 0)


if __name__ == '__main__':
    unittest.main()
