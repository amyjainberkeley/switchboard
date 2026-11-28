import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The names, arguments and return shapes the page's takeaways call on the REAL router modules (tryit.on, off, state;
# cache_count.count with projects_folders; cache_setting.status), so a mismatch shows here and not at the gate.
import inspect
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))
sys.path.insert(0, HERE)

from switchboard.router import cache_count, cache_setting, tryit, wire  # noqa: E402

import fakes_provider_kit as kit  # noqa: E402
from test_router_wire import NEW, Home  # noqa: E402

NOW = kit.NOW
STATUS_KEYS = {'file', 'exists', 'value', 'state', 'set_by', 'version', 'new_enough', 'problem', 'lines', 'notes'}


class Faces(Home, kit.KitCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        wire._GATHER['thread'] = None

    def test_tryit_is_called_with_no_arguments(self):
        for fn in (tryit.on, tryit.off):
            params = inspect.signature(fn).parameters.values()
            self.assertTrue(all(p.default is not p.empty or p.kind == p.VAR_KEYWORD for p in params), fn)
        self.assertEqual(inspect.signature(tryit.state).parameters, {})

    def test_tryit_shapes(self):
        st = tryit.state()
        self.assertEqual(set(st), {'trying', 'was_on', 'was_trial'})
        self.assertIs(st['trying'], False)
        where = dict(claude_dir=self.claude, codex_home=self.codex, versions=NEW)
        for got in (tryit.on(kit=self.kit(), now=NOW, **where), tryit.off(kit=self.kit(), now=NOW, **where),
                    tryit.off()):
            self.assertIsInstance(got, list)
            self.assertTrue(got)
            self.assertTrue(all(isinstance(x, str) and '\n' not in x for x in got), got)

    def test_cache_count_as_the_page_calls_it(self):
        folders = cache_count.projects_folders()
        self.assertEqual(folders, [os.path.join(self.claude, 'projects')])     # the throwaway one, never the real
        folder = os.path.join(folders[0], 'made-up-project', 'subagents')
        os.makedirs(folder)
        with open(os.path.join(folder, 'agent-a1.jsonl'), 'w', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'assistant', 'timestamp': '2026-01-01T00:00:00Z'}) + '\n')
        got = cache_count.count(cache_count.projects_folders(), limit_s=2)
        self.assertIsInstance(got, dict)
        self.assertIsInstance(got['restores'], int)
        self.assertTrue(set(cache_count.RESULT_KEYS) <= set(got))

    def test_cache_setting_status_keys(self):
        got = cache_setting.status(claude_dir=self.claude, versions=NEW)
        self.assertEqual(set(got), STATUS_KEYS)
        self.assertIsInstance(got['lines'], list)
        self.assertIn(got['state'], ('unset', '5m', '1h', 'other', 'unknown'))


if __name__ == '__main__':
    unittest.main()
