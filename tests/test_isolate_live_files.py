import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The test fence (tests/_isolate.py) snapshots the real data folder before and after a test run. A Switchboard
# installed on this Mac rewrites some files there by itself while tests run; those are never counted as the tests'
# doing. The engine's note of putting a lost sign-in back, and the page's ask for a read, are two of them.
import unittest


def snap(data):
    return {'data': data}


class TheFilesTheInstalledCopyRewrites(unittest.TestCase):
    def test_a_put_back_note_or_a_read_request_written_meanwhile_is_not_the_tests_doing(self):
        for name in ('put-back.json', 'read-request.json'):
            with self.subTest(name=name):
                before, after = snap({name: (10, 1.0)}), snap({name: (20, 2.0)})
                self.assertEqual(_isolate.changes(before, after), [])
                temp = '.%s.a1b2c3.tmp' % name                  # its own temporary name while it is written
                self.assertEqual(_isolate.changes(snap({}), snap({temp: (5, 3.0)})), [], temp)

    def test_what_the_watch_and_the_self_check_write_by_themselves_is_not_the_tests_doing(self):
        for name in ('page-watch.json', 'page-starts.json', 'page-stopped.json', 'page-port.json', 'page-tries.json',
                     'page-tries.lock', 'menubar-tries.json', 'watch-spells.json', 'settings.json.last-good',
                     'repairs.json', 'repairs.lock', 'statusline-install.json', 'notify-hooks-install.json',
                     'session-hooks-install.json', 'settings.json.damaged-1764140000',
                     'accounts.json.damaged-1764140000', 'state.json.damaged-1764140000'):
            with self.subTest(name=name):
                self.assertEqual(_isolate.changes(snap({name: (10, 1.0)}), snap({name: (20, 2.0)})), [])
                self.assertEqual(_isolate.changes(snap({}), snap({'.%s.a1b2c3.tmp' % name: (5, 3.0)})), [])

    def test_a_file_only_a_person_writes_still_counts(self):
        # Quit in the menu bar is a person's action: a test that writes the quit mark is caught.
        got = _isolate.changes(snap({}), snap({'menubar-quit.json': (5, 3.0)}))
        self.assertEqual(got, ['~/.switchboard/menubar-quit.json: absent -> (5, 3.0)'])

    def test_any_other_file_written_meanwhile_still_is(self):
        got = _isolate.changes(snap({}), snap({'made-up.json': (5, 3.0)}))
        self.assertEqual(got, ['~/.switchboard/made-up.json: absent -> (5, 3.0)'])


if __name__ == '__main__':
    unittest.main()
