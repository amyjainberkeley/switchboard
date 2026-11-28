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
        # A newer Switchboard than the copy under test can be running on this Mac: its sign-in watch rewrites its
        # heartbeat every few seconds, and its self-check keeps a last good settings file and mends what it wrote.
        for name in ('signin-watch.json', 'signin-watch.lease', 'signin-watch.log', 'signin-watch.log.1',
                     'signin-watch.out', 'signin-record.jsonl', 'signin-record.jsonl.1', 'signin-hold.json',
                     'page-watch.json', 'page-starts.json', 'page-stopped.json', 'page-port.json', 'page-tries.json',
                     'page-tries.lock', 'menubar-tries.json', 'watch-spells.json', 'settings.json.last-good',
                     'repairs.json', 'repairs.lock', 'statusline-install.json', 'notify-hooks-install.json',
                     'session-hooks-install.json', 'settings.json.damaged-1764140000',
                     'accounts.json.damaged-1764140000', 'state.json.damaged-1764140000'):
            with self.subTest(name=name):
                self.assertEqual(_isolate.changes(snap({name: (10, 1.0)}), snap({name: (20, 2.0)})), [])
                self.assertEqual(_isolate.changes(snap({}), snap({'.%s.a1b2c3.tmp' % name: (5, 3.0)})), [])
        for temp in ('.signin-watch.json.a1b2c3.tmp', '.signin-hold.json.a1b2c3.tmp', 'signin-record.jsonl.a1b2c3',
                     'signin-watch.log.a1b2c3', '.settings.json.damaged-1764140000'):
            with self.subTest(temp=temp):
                self.assertEqual(_isolate.changes(snap({}), snap({temp: (5, 3.0)})), [])

    def test_what_a_newer_running_copy_s_sign_in_watch_writes_is_not_the_tests_doing(self):
        # A newer Switchboard than the copy under test can be the one running on this Mac: its sign-in watch
        # rewrites its heartbeat every few seconds, with its lease, log, record and hold.
        for name in ('signin-watch.json', 'signin-watch.lease', 'signin-watch.log', 'signin-watch.log.1',
                     'signin-watch.out', 'signin-record.jsonl', 'signin-record.jsonl.1', 'signin-hold.json'):
            with self.subTest(name=name):
                self.assertEqual(_isolate.changes(snap({name: (10, 1.0)}), snap({name: (20, 2.0)})), [])
        for temp in ('.signin-watch.json.a1b2c3.tmp', 'signin-watch.log.1.tmp', 'signin-record.jsonl.a1b2c3',
                     '.signin-hold.json.a1b2c3.tmp'):
            with self.subTest(temp=temp):
                self.assertEqual(_isolate.changes(snap({}), snap({temp: (5, 3.0)})), [], temp)

    def test_a_file_only_a_person_writes_still_counts(self):
        # Quit in the menu bar, and the settings file itself, are a person's doing: a test that writes them is caught.
        for name in ('menubar-quit.json', 'settings.json'):
            with self.subTest(name=name):
                got = _isolate.changes(snap({}), snap({name: (5, 3.0)}))
                self.assertEqual(got, ['~/.switchboard/%s: absent -> (5, 3.0)' % name])

    def test_any_other_file_written_meanwhile_still_is(self):
        got = _isolate.changes(snap({}), snap({'made-up.json': (5, 3.0)}))
        self.assertEqual(got, ['~/.switchboard/made-up.json: absent -> (5, 3.0)'])


if __name__ == '__main__':
    unittest.main()
