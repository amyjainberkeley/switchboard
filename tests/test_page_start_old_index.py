import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# At the page's start, an index of past chats that an older version made is erased (it may hold key-like text this
# version keeps out), and search builds it again later. A current index is left; a failure there never stops the page.
# Every chat is made up, in a throwaway folder; key-shaped strings are built from parts (test_packs_memory_private).
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import server, watch
from switchboard.packs import memory as M
from test_packs_memory_private import KEY, tmp, write_history
from test_watch_serve import Serve


def index_of(schema):
    """An index at the data folder's own place, as a version of that schema number wrote it."""
    root = os.path.join(tmp(), 'projects')
    write_history(root)
    with mock.patch.object(M, 'SCHEMA', schema), mock.patch.object(M, 'private_text', lambda t: t):
        M.update_index(root, M.index_path())
    return M.index_path()


class TheStep(Serve):
    def test_an_older_index_is_erased_and_said_once_in_the_log(self):
        path = index_of(1)
        with open(path, 'rb') as f:
            self.assertIn(KEY.encode(), f.read())           # what an older version kept, word for word
        server.drop_old_chat_index()
        self.assertFalse(os.path.exists(path))
        said = [x for x in self.log_lines() if 'Erased the search index of past chats' in x]
        self.assertEqual(len(said), 1, said)

    def test_a_current_index_is_left(self):
        path = index_of(M.SCHEMA)
        before = os.stat(path).st_mtime_ns
        server.drop_old_chat_index()
        self.assertTrue(os.path.exists(path))
        self.assertEqual(os.stat(path).st_mtime_ns, before)
        self.assertFalse([x for x in self.log_lines() if 'Erased the search index' in x])

    def test_no_index_is_nothing(self):
        server.drop_old_chat_index()
        self.assertFalse(os.path.exists(M.index_path()))

    def test_a_failure_never_stops_the_page(self):
        def boom(index=None):
            raise RuntimeError('made up')
        with mock.patch.object(M, 'drop_old_index', boom):
            server.drop_old_chat_index()                    # does not raise
        self.assertTrue([x for x in self.log_lines() if 'was not looked at' in x])

    def test_the_page_s_start_takes_the_step(self):
        took = []
        self.patch(server, 'drop_old_chat_index', lambda: took.append(1))
        os.environ['SWITCHBOARD_PORT'] = str(self.free_port())
        with mock.patch.object(watch, 'background', lambda: False):
            server.serve()
        self.assertEqual(took, [1])
        self.assertEqual(self.looped, [1])


if __name__ == '__main__':
    unittest.main()
