import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The commands' stand-ins match the real modules (lane D second pass, D8). REAL below is copied from the lead's list of
# the other lanes' real public functions (names and arguments, from their branch heads on Mon Nov 23 2026: lane A
# aac80f8, lane E c89c089); the lead checks it against the real modules at the merge.
#
# - Each stand-in in tests/fakes_provider_kit.py (FakeHandoff, FakeTasks, FakePlace, FakeChoices, FakeRun, FakeBridge)
#   has each function of its module with the same arguments in the same order, the same keyword names, the same kinds
#   and the same defaults; the four modules the commands use whole have every function. A stand-in holds no public
#   function the real module lacks (but the few named test helpers).
# - Every function cli.py calls through its Kit (kit.handoff.X, kit.tasks.X, ...) and on an adapter's bridge is in the
#   list. The reason codes cli.py answers are place.REASONS, every one.
# - The real modules, when this build has them (after the merge), match the same list.
# - What the commands call returns what the real one does: each prune an int, put_note a bool, try_lock a context
#   that gives a bool.
import importlib
import inspect
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

import fakes_provider_kit as kit  # noqa: E402
from switchboard.providers import cli, contract  # noqa: E402

REAL = {
    'handoff': {
        'fact': '(value, cap=200)',
        'where_things_stand': '(folder, reach)',
        'make': '(transcript, *, hop, task, to_name, from_name, why, stand, earlier=(), budget=BUDGET, reach)',
        'reusable': '(hop, chat, reader)',
        'answer_path': '(task, hop, reach)',
        'back_note': '(handoff, *, other_name, start, end)',
        'prune': '(reach, now)',
        'write_task': '(text, *, hop, task, to_name, kind_line, asked=(), folder, reach)',   # switchboard do's
    },
    'tasks': {
        'new_hop_id': '()',
        'new_task_id': '()',
        'task_for': '(folder, provider, chat_id)',
        'add_hop': '(hop)',
        'update_hop': '(hop_id, **changes)',
        'hops': '(folder=None, limit=50)',
        'open_continue': '(folder, to_chat=None)',
        'continued_from': '(folder, from_chat)',
        'prune': '(now)',
        'add_task': '(task_id, folder, job, at)',                  # switchboard do's task row, and its state
        'update_task': '(task_id, **changes)',
    },
    'place': {
        'room_of': '(provider, name, limits, now, fresh_s)',
        'pick': '(order, current, rooms, to=None)',
    },
    'choices': {
        'added': '()',
        'order': '()',
        'add': '(pid, now)',
        'remove': '(pid)',
        'set_order': '(ids)',
        'consent': '(folder, pid)',
        'set_consent': '(folder, pid, state, now)',
        'consent_at': '(folder, pid)',
        'allow': '(folder, pid, now)',
        'stays': '(folder)',
        'set_stay': '(folder, pid, now)',
        'ask_ok': '(pid)',
        'set_ask_ok': '(now, pid)',
        'hand_ack': '(pid)',
        'set_hand_ack': '(now, pid)',
        'notice_said': '(pid)',
        'set_notice_said': '(now, pid)',
        'open_with': '(pid)',
        'set_open_with': '(pid, how)',
        'handoff_budget': '()',
    },
    'run': {
        'child_env': '(env, job=False)',
        'shell_line': '(argv, cwd, env=None)',
        'version_key': '(text)',
        'default_reach': '()',
        'run_program': '(argv, stdin=None, timeout=30.0, env=None, cwd=None)',
        'open_pipe': '(argv, env=None, cwd=None)',
        'exec_program': '(path, argv, env, cwd)',
        'open_link': '(url, app=None)',
        'try_lock': '(name)',
        'notes_root': '(reach)',
    },
    'bridge': {
        'hooks_can_deliver': '()',
        'put_note': '(session_id, text, path, hop, reach=None)',
        'chat_in_use': '(cid, path)',
    },
}
# place.REASONS at lane A's aac80f8, in its order.
REASONS = ('room', 'unknown', 'out', 'all_out', 'not_added', 'not_installed', 'signed_out', 'is_source', 'no_other')
WHOLE = ('handoff', 'tasks', 'place', 'choices')        # the stand-ins of these carry every function
HELPERS = {'tasks': {'get'}}                            # a test's own reader of the stand-in ledger
STAND_INS = {'handoff': kit.FakeHandoff, 'tasks': kit.FakeTasks, 'place': kit.FakePlace, 'choices': kit.FakeChoices,
             'run': kit.FakeRun, 'bridge': kit.FakeBridge}


class _Any:
    def __eq__(self, other):
        return True

    def __repr__(self):
        return 'BUDGET'


def shape(sig: inspect.Signature):
    """(name, kind, default) of each argument, annotations left out."""
    return tuple((p.name, p.kind, p.default) for p in sig.parameters.values())


def listed(text):
    ns = {'BUDGET': _Any()}
    exec('def f%s: pass' % text, ns)
    return shape(inspect.signature(ns['f']))


def public(obj):
    return {n for n in dir(obj) if not n.startswith('_') and callable(getattr(obj, n))}


class TheStandIns(unittest.TestCase):
    def stand_in(self, mod):
        cls = STAND_INS[mod]
        return cls()

    def test_each_function_has_the_real_arguments(self):
        for mod, funcs in REAL.items():
            obj = self.stand_in(mod)
            for name, text in funcs.items():
                if mod not in WHOLE and not hasattr(obj, name):
                    continue
                with self.subTest(module=mod, function=name):
                    self.assertTrue(hasattr(obj, name), '%s stand-in has no %s' % (mod, name))
                    self.assertEqual(shape(inspect.signature(getattr(obj, name))), listed(text))

    def test_no_stand_in_has_a_function_the_real_one_lacks(self):
        for mod in REAL:
            obj = self.stand_in(mod)
            extra = public(obj) - set(REAL[mod]) - HELPERS.get(mod, set())
            with self.subTest(module=mod):
                self.assertEqual(extra, set())

    def test_the_check_catches_a_drift(self):            # a check that passes everything proves nothing
        self.assertNotEqual(listed('(folder, pid)'), listed('(pid, folder)'))
        self.assertNotEqual(listed("(pid='codex')"), listed('()'))
        self.assertNotEqual(listed('(reach, now)'), listed('(reach, *, now)'))
        self.assertNotEqual(listed('(now, pid=None)'), listed("(now, pid='codex')"))

    def test_what_they_return(self):
        root = os.path.realpath(__import__('tempfile').mkdtemp(prefix='sb-kit-'))
        self.addCleanup(__import__('shutil').rmtree, root, True)
        rig = kit.Rig(root)
        self.assertIsInstance(rig.handoff.prune(rig.reach, kit.NOW), int)
        self.assertIsInstance(rig.tasks.prune(kit.NOW), int)
        self.assertIsInstance(kit.FakeBridge().put_note(kit.CHAT_A, 'x', '/made-up/n.md', 'h-0000000a'), bool)
        with rig.run.try_lock('made-up.lock') as held:
            self.assertIsInstance(held, bool)
        self.assertRegex(rig.tasks.new_task_id(), r'^t-[0-9a-f]{8}\Z')
        self.assertRegex(rig.tasks.new_hop_id(), r'^h-[0-9a-f]{8}\Z')


class TheStandInAdapterLaunchesAsTheRealOne(unittest.TestCase):
    """FakeProvider builds the key press's launches the way CodexProvider does (the argument list, the environment,
    the folder and the line to run), so the tests of the key press that use it cannot drift from the real one."""

    def setUp(self):
        from fakes_codex import Rig
        self.rig = Rig(self)
        self.rig.app('ChatGPT.app', '0.169.1')
        self.real = self.rig.provider
        self.fake = kit.FakeProvider(self.rig.reach, 'codex', found=self.real.detect())
        self.folder = os.path.join(self.rig.root, 'site')
        os.makedirs(self.folder)

    def same(self, a, b):
        self.assertEqual((a.provider, a.how, a.program.path, a.argv, dict(a.env), a.cwd, a.line, a.first_words),
                         (b.provider, b.how, b.program.path, b.argv, dict(b.env), b.cwd, b.line, b.first_words))

    def test_open_here(self):
        for how in (contract.IN_TERMINAL, contract.PRINT):
            with self.subTest(how=how):
                self.same(self.fake.open_here(self.folder, how), self.real.open_here(self.folder, how))

    def test_start_task(self):
        path = os.path.join(self.rig.root, 'task.md')
        for how, extra in ((contract.IN_TERMINAL, ()), (contract.PRINT, ('-m', 'gpt-made-up'))):
            with self.subTest(how=how):
                self.same(self.fake.start_task(self.folder, 'h-0000000a', path, how, extra),
                          self.real.start_task(self.folder, 'h-0000000a', path, how, extra))

    def test_start_in_a_terminal_up_to_its_first_message(self):
        h = contract.Handoff(hop='h-0000000a', path=os.path.join(self.rig.root, 'note.md'),
                             meta_path=os.path.join(self.rig.root, 'note.json'), bytes=1, sha256='0' * 64)
        a, b = self.fake.start(self.folder, h, contract.IN_TERMINAL), self.real.start(self.folder, h,
                                                                                       contract.IN_TERMINAL)
        self.assertEqual((a.argv[:-1], dict(a.env), a.cwd), (b.argv[:-1], dict(b.env), b.cwd))
        for launch in (a, b):
            self.assertIn('h-0000000a', launch.argv[-1])
            self.assertIn(h.path, launch.argv[-1])


class WhatTheCommandsCall(unittest.TestCase):
    def source(self):
        with open(cli.__file__, encoding='utf-8') as f:
            return f.read()

    def test_every_call_through_the_kit_is_in_the_list(self):
        calls = set(re.findall(r'kit\.(handoff|tasks|place|choices|run)\.(\w+)', self.source()))
        self.assertGreater(len(calls), 20)
        for mod, name in sorted(calls):
            with self.subTest(module=mod, function=name):
                self.assertIn(name, REAL[mod])

    def test_every_call_on_the_bridge_is_in_the_list(self):
        calls = set(re.findall(r'\bbridge\.(\w+)\(', self.source()))
        self.assertEqual(calls, {'hooks_can_deliver', 'put_note'})

    def test_the_reason_codes_are_place_reasons(self):
        handled = set(re.findall(r"reason == '(\w+)'", self.source()))
        self.assertTrue(handled <= set(REASONS), handled - set(REASONS))
        self.assertEqual(set(cli.PICK_REASONS), set(REASONS))


class TheRealModules(unittest.TestCase):
    """After the merge the real modules are here; each function they have matches the list."""

    def test_each_real_function_that_is_here(self):
        checked = 0
        for mod, funcs in REAL.items():
            try:
                real = importlib.import_module('switchboard.providers.' + mod)
            except ImportError:
                continue
            for name, text in funcs.items():
                fn = getattr(real, name, None)
                if fn is None:
                    continue
                with self.subTest(module=mod, function=name):
                    self.assertEqual(shape(inspect.signature(fn)), listed(text))
                    checked += 1
        self.assertGreater(checked, 0)          # run.py's upper part is in every build

    def test_the_real_reasons_when_here(self):
        try:
            place = importlib.import_module('switchboard.providers.place')
        except ImportError:
            self.skipTest('place.py is not in this build yet')
        self.assertEqual(tuple(place.REASONS), REASONS)


if __name__ == '__main__':
    unittest.main()
