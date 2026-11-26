import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# No command of the other assistants opens a login (DESIGN 2 R1, 17 T1).
#
# A made-up Mac whose Switchboard folder is <made-up home>/.switchboard, on purpose, holds every login file and folder
# the contract names (LOGIN_FILES under the home, LOGIN_DATA_FOLDERS under the data folder, a run's own
# .credentials.json, a window's <pid>.key, an auth.json deep in a folder), the files that can hold a key
# (NO_READ_FILES), the account list (ENGINE_DATA_FILES) and a run's window file. Then every command runs: providers
# (with --ask and --json), add, continue, back, hand, allow, stay and order. An audit hook, added once and on only
# while the commands run, records each open under the made-up Mac with the switchboard frame that asked for it:
# - an open of a login file, folder or name fails the test, whoever asked;
# - an open of the account list or a run's window file passes only from switchboard/accounts/, switchboard/core.py or
#   switchboard/resume.py; one of NO_READ_FILES only from switchboard/session_hooks.py;
# - no program called security is run;
# - a guard that saw nothing fails: at least one open is seen, and once the real Claude adapter is in the build, at
#   least one open of accounts.json (its seats come from the account code).
#
# Which kit: when every module the commands use imports (after the merge), the real one (default_kit) on a made-up
# Reach whose PATH and app folders are empty (no real program is found or run); until then, the commands' stand-in
# kit. The merge's step 4 is where this file proves the real code.
import dataclasses
import io
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

import switchboard  # noqa: E402
from fakes_provider_kit import CHAT_A, CHAT_B, NOW, FakeBridge, KitCase, chat  # noqa: E402
from fakes_providers import make_reach  # noqa: E402
from switchboard.providers import cli, contract  # noqa: E402
from switchboard.providers.contract import Hop  # noqa: E402

PACKAGE = os.path.dirname(os.path.abspath(switchboard.__file__))
ENGINE_FRAMES = (os.path.join(PACKAGE, 'accounts') + os.sep, os.path.join(PACKAGE, 'core.py'),
                 os.path.join(PACKAGE, 'resume.py'))
HOOK_FRAMES = (os.path.join(PACKAGE, 'session_hooks.py'),)
RUN_WINDOW = re.compile(r'(^|/)run/[^/]+/sessions/[^/]+\.json$')

_STATE = {'on': False, 'root': None, 'opens': [], 'runs': []}


def _frame():
    """The nearest frame in Switchboard's own code (not this test, not the standard library)."""
    f = sys._getframe(2)
    while f is not None:
        name = os.path.abspath(f.f_code.co_filename)
        if name.startswith(PACKAGE + os.sep):
            return name
        f = f.f_back
    return ''


def _hook(event, args):
    if not _STATE['on']:
        return
    if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
        try:
            path = os.path.abspath(os.fsdecode(args[0]))
        except (TypeError, ValueError):
            return
        if path.startswith(_STATE['root'] + os.sep):
            _STATE['opens'].append((path, _frame()))
    elif event in ('subprocess.Popen', 'os.exec', 'os.posix_spawn') and len(args) > 1:
        argv = args[1]
        first = argv[0] if isinstance(argv, (list, tuple)) and argv else argv
        _STATE['runs'].append(os.path.basename(os.fsdecode(first)) if first else '')


if not getattr(sys, '_switchboard_login_guard', False):     # one hook per process, whichever test file adds it
    sys.addaudithook(_hook)
    sys._switchboard_login_guard = True


def real_kit_or_none(reach):
    try:
        return cli.default_kit(reach)
    except ImportError:
        return None


class NoLoginFile(KitCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.root, 'home')
        self.data = os.path.join(self.home, '.switchboard')            # on purpose: the home's own data folder
        for rel in contract.LOGIN_FILES + contract.NO_READ_FILES:
            self.put(os.path.join(self.home, rel))
        for rel in contract.LOGIN_DATA_FOLDERS:
            self.put(os.path.join(self.data, rel, 'account-1.json'))
        for rel in contract.ENGINE_DATA_FILES:
            self.put(os.path.join(self.data, rel), '{"v": 1, "accounts": []}')
        self.put(os.path.join(self.data, 'run', '1', '.credentials.json'))
        self.put(os.path.join(self.data, 'run', '1', 'sessions', '4242.json'), '{}')
        self.put(os.path.join(self.home, '.claude', 'sessions', '4242.0a1b-2c3d.key'))
        self.put(os.path.join(self.home, 'site', 'deep', 'auth.json'))
        patch = mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': self.data, 'HOME': self.home,
                                             'CLAUDE_CONFIG_DIR': os.path.join(self.home, '.claude')})
        patch.start()
        self.addCleanup(patch.stop)

    def put(self, path, text='{"made-up": true}'):
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path, 'w') as f:
            f.write(text)

    def is_login(self, path):
        base = os.path.basename(path)
        if base in contract.LOGIN_BASENAMES or contract.LOGIN_NAME_RE.match(base):
            return True
        if os.path.relpath(path, self.home) in contract.LOGIN_FILES:
            return True
        return any(path.startswith(os.path.join(self.data, d) + os.sep) for d in contract.LOGIN_DATA_FOLDERS)

    def every_command(self, rig_or_kit):
        """Each command once, as a person would type them, and as an assistant's shell tool would."""
        call = rig_or_kit
        for args in (['providers'], ['providers', '--json'], ['providers', '--ask'], ['add', 'codex'], ['order'],
                     ['order', 'codex', 'claude'], ['order', 'claude'], ['allow', 'codex'], ['continue'],
                     ['continue', '--to', 'codex', '--latest'], ['back'], ['back', '--show'],
                     ['hand', 'codex', '--', 'review the footer'], ['stay', 'claude'], ['stay', 'off']):
            call(*args)

    def check(self):
        opens = _STATE['opens']
        bad = []
        for path, frame in opens:
            rel = os.path.relpath(path, self.data) if path.startswith(self.data + os.sep) else None
            if self.is_login(path):
                bad.append(('login', path, frame))
            elif rel in contract.ENGINE_DATA_FILES or (rel and RUN_WINDOW.search(rel)):
                if not frame.startswith(ENGINE_FRAMES):
                    bad.append(('account list or window file', path, frame))
            elif os.path.relpath(path, self.home) in contract.NO_READ_FILES and not frame.startswith(HOOK_FRAMES):
                bad.append(('settings that can hold a key', path, frame))
        self.assertEqual(bad, [])
        self.assertNotIn('security', _STATE['runs'])
        self.assertTrue(opens, 'the guard saw no open at all: it proves nothing')

    def run_guarded(self, fn):
        _STATE.update(on=True, root=self.root, opens=[], runs=[])
        try:
            fn()
        finally:
            _STATE['on'] = False
        self.check()

    def test_the_guard_itself_sees_a_login_open(self):
        def opens_one():
            with open(os.path.join(self.home, '.codex', 'auth.json')):
                pass
        _STATE.update(on=True, root=self.root, opens=[], runs=[])
        try:
            opens_one()
        finally:
            _STATE['on'] = False
        self.assertTrue(any(self.is_login(p) for p, _ in _STATE['opens']))
        with self.assertRaises(AssertionError):
            self.check()

    def test_every_command_on_the_kit_this_build_has(self):
        reach, _ = make_reach(self.root, env={'HOME': self.home, 'PATH': ''}, wall=lambda: NOW)
        kit = real_kit_or_none(reach)
        if kit is not None:
            folder = os.path.join(self.home, 'site')
            kit = dataclasses.replace(kit, out=io.StringIO(), cwd=lambda: folder,
                                      ask=lambda prompt: self.fail('asked without a terminal: %r' % prompt))
            self.run_guarded(lambda: self.every_command(lambda *a: cli.main(list(a), kit=kit)))
            from switchboard.providers import adapter_class
            if adapter_class('claude') is not None:
                engine = [p for p, _ in _STATE['opens'] if os.path.basename(p) in contract.ENGINE_DATA_FILES]
                self.assertTrue(engine, 'the Claude adapter is in, yet no accounts.json open was seen')
            return
        self.run_guarded(self.stand_in_run)

    def stand_in_run(self):
        """Before the merge: the commands on the stand-in kit, at a terminal (every question answered yes) and not."""
        for tty in (True, False):
            r = self.rig(tty=tty, answers=['y'] * 20, codex_added=False,
                         claude_chats=(lambda f: chat('claude', f, cid=CHAT_A, ago=4000.0, live=True),),
                         codex_chats=(lambda f: chat('codex', f, cid=CHAT_B, ago=60.0, started_ago=3500.0),))
            r.claude.bridge = FakeBridge(can=True)
            r.codex.job_answer = 'made-up answer'
            r.tasks.add_hop(Hop(id='h-0000000a', task='t-0000000a', kind='continue', at=NOW - 3600, folder=r.folder,
                                from_provider='claude', from_chat=CHAT_A, to_provider='codex', to_chat=CHAT_B,
                                state='started'))
            self.every_command(r)


if __name__ == '__main__':
    unittest.main()
