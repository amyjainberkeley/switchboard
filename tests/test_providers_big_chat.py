import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A 120 MB made-up Claude Code chat (compactions, large tool results, one line over the 4 MiB line cap), built once
# for this file in a temp folder and removed after it: read within ReadLimits in under 10 seconds, with what it keeps
# bounded (the number of events and the size of each one's text). Nothing here reads a real chat.
import json
import os
import shutil
import tempfile
import time
import unittest

import fakes_provider_chats as F
from fakes_providers import make_reach
from switchboard.providers import claude_chats, contract
from switchboard.providers.contract import ChatRef, ReadLimits

SIZE = 120 * 1000 * 1000
BLOB = 180000               # one tool result's characters


def build(path):
    """Write the chat line by line (never all of it in memory): rounds of a request, a tool call and a 180 KB
    result, and a reply; a compaction every 20 MB; one 5 MB result line. Returns the number of compactions and of
    rounds."""
    c = F.ClaudeChat(version='2.1.329')
    blob = ('made-up tool output line\n' * 8200)[:BLOB]
    written, compactions, k = 0, 0, 0
    next_compaction = 20 * 1000 * 1000
    with open(path, 'w', encoding='utf-8') as f:
        def put(d):
            nonlocal written
            s = json.dumps(d, separators=(',', ':')) + '\n'
            f.write(s)
            written += len(s)
        while written < SIZE:
            k += 1
            c.rows = []
            c.person('request number %d' % k)
            tid = c.tool_use('Bash', {'command': 'cat build-%d.log' % k, 'description': 'read the log'})
            c.tool_result(tid, blob if k != 300 else 'z' * (5 << 20), is_error=False)
            c.say('reply number %d' % k)
            if written > next_compaction:
                c.boundary()
                c.compact_summary('summary %d' % compactions)
                compactions += 1
                next_compaction += 20 * 1000 * 1000
            for d in c.rows:
                put(d)
    return compactions, k


class BigChat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix='big-chat-')
        cls.addClassCleanup(shutil.rmtree, cls.root, True)
        cls.path = os.path.join(cls.root, F.CHAT + '.jsonl')
        start = time.monotonic()
        cls.compactions, cls.rounds = build(cls.path)
        cls.built_s = time.monotonic() - start
        cls.reach, _ = make_reach(cls.root, env={'HOME': os.path.join(cls.root, 'home')})
        cls.ref = ChatRef(provider='claude', id=F.CHAT, folder=F.FOLDER, path=cls.path)

    def bounded(self, t):
        self.assertLess(len(t.events), 2000)
        for e in t.events:
            cap = {contract.TOOL_CALL: contract.KEEP_TOOL_INPUT, contract.TOOL_RESULT: contract.KEEP_TOOL_RESULT,
                   contract.PLAN: contract.KEEP_TOOL_INPUT}.get(e.kind, contract.KEEP_PERSON)
            self.assertLessEqual(len(e.text), cap)

    def test_the_file_is_over_120_mb_with_compactions(self):
        self.assertGreaterEqual(os.path.getsize(self.path), SIZE)
        self.assertGreaterEqual(self.compactions, 5)

    def test_read_from_the_newest_summary_in_under_ten_seconds(self):
        start = time.monotonic()
        t = claude_chats.read(self.reach, self.ref)
        took = time.monotonic() - start
        self.assertLess(took, 10.0)
        self.bounded(t)
        self.assertEqual(t.info.stopped, 'boundary')
        self.assertEqual(len([e for e in t.events if e.kind == contract.SUMMARY]), 1)
        people = [e for e in t.events if e.kind == contract.PERSON]
        self.assertEqual(people[-1].text, 'request number %d' % self.rounds)
        self.assertEqual(sum(e.before_boundary for e in people), 5)
        self.assertIsNone(t.info.degraded)
        self.assertLess(t.info.bytes_read, 30 * 1000 * 1000)

    def test_read_without_the_summary_stops_at_the_byte_cap(self):
        start = time.monotonic()
        t = claude_chats.read(self.reach, self.ref, ReadLimits(since_boundary=False))
        took = time.monotonic() - start
        self.assertLess(took, 10.0)
        self.bounded(t)
        self.assertIn(t.info.stopped, ('bytes', 'time'))
        self.assertTrue(t.info.from_end)
        self.assertLessEqual(t.info.bytes_read, 64 << 20)
        if t.info.stopped == 'bytes':
            self.assertGreaterEqual(t.info.bytes_read, (64 << 20) - (1 << 20))
        results = [e for e in t.events if e.kind == contract.TOOL_RESULT]
        self.assertTrue(all(len(e.text) == contract.KEEP_TOOL_RESULT and e.full_len == BLOB for e in results))

    def test_the_line_over_the_cap_is_skipped_and_counted(self):
        t = claude_chats.read(self.reach, self.ref, ReadLimits(since_boundary=False, max_bytes=1 << 40,
                                                                max_seconds=30.0))
        self.assertEqual(t.info.skipped_large, 1)
        self.assertEqual(t.info.stopped, 'start')
        self.assertEqual(len([e for e in t.events if e.kind == contract.SUMMARY]), self.compactions)


if __name__ == '__main__':
    unittest.main()
