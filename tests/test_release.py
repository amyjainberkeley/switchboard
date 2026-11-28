import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# What the release no longer does, said plainly. This file's other tests live on, renamed by the public line, in
# tests/test_app_setup_and_more.py (its sandbox is used here); these two were dropped there and are still true of the
# program: installing the engine from the page is gone, and every request only the old engine answered gets one plain
# refusal (server.RETIRED). The engine is tests/fakes_accounts_api.py, in accounts.api's place in this process.
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, engine, server  # noqa: E402
from test_app_setup_and_more import A, B, EngineSandbox  # noqa: E402  (a sandbox only: it has no tests of its own)


class WhatIsGone(EngineSandbox):
    def test_installing_from_the_page_is_gone(self):
        self.assertFalse(hasattr(engine, 'install'))
        self.assertFalse(hasattr(engine, 'install_command'))
        server.act('/api/engine', {'install': True})               # an old page's button: does nothing
        self.assertEqual(self.changes(), [])

    def test_what_only_the_old_engine_did_is_refused_plainly(self):
        for path, body in (('/api/line', {'email': A, 'pct': 85}), ('/api/rank', {'email': B, 'tier': 3}),
                           ('/api/token', {'token': 'sk-ant-oat01-' + 'q' * 60}), ('/api/folder', {'path': '/tmp'}),
                           ('/api/backup', {'confirm': True}), ('/api/restore', {'text': '{}'}),
                           ('/api/clear', {'id': 'c1'}), ('/api/setting', {'key': 'ui.theme', 'value': 'dark'}),
                           ('/api/hold', {'email': A, 'on': True}), ('/api/repair', {}), ('/api/kept', {'id': 'k'})):
            with self.subTest(path=path):
                with self.assertRaises(core.SwitchError) as cm:
                    server.act(path, body)
                self.assertIn('does not do that any more', str(cm.exception))
        self.assertEqual(self.changes(), [])


if __name__ == '__main__':
    unittest.main()
