import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""A page published seconds ago opens as its publisher, whoever is in use now.

The session hooks note each publish in hooks/published.jsonl the moment Claude Code answers. Each open takes those
lines in before anything else (artifacts.go, which go_quietly and the page's Open call), so the page opens it in the
publisher's profile without waiting for a history scan, and owners.json names it at once: the link app's own fallback
(the page slow or busy) opens it there too, not in the profile of the account in use after a switch. A fake Chrome
stands in for the browser."""
import fcntl
import os
import time
import unittest
from unittest import mock

from fakes_artifacts import A, B, ID1, LINK, publish
from fakes_links import FakeChrome, Home

from switchboard import artifacts, core, session_hooks


class FreshPublish(Home):
    """A published a page, then Claude Code moved to B a minute later; the page's tick has not taken the line yet."""

    def setUp(self):
        super().setUp()
        self.chrome = FakeChrome(self, {'P1': 'Work', 'P2': 'Home'}, logins={'P1': A, 'P2': B}, owners={ID1: A},
                                 titles={ID1: 'Launch board'})
        self.now = time.time()
        self.addCleanup(artifacts._PUB.update, seen=None)
        artifacts._PUB['seen'] = None
        self.state(**{A: {'profile': 'P1', 'profile_src': 'you'}, B: {'profile': 'P2', 'profile_src': 'you'}})
        with core.locked() as st:
            st['timeline'], st['active'], st['scan_v'] = [[A, self.now - 7200], [B, self.now - 60]], B, \
                artifacts.SCAN_V
        artifacts.write_owner_map()                     # the map as the page wrote it before the publish
        t = self.now - 900
        session_hooks.note_published(session_hooks.published_line(publish(ID1), t), t)
        self.assertNotIn(ID1, artifacts.read_owner_map()['artifacts'])
        self.assertEqual(artifacts.read_owner_map()['fallback'], 'P2')     # the account in use's profile

    def test_the_page_opens_it_as_its_publisher_without_a_history_scan(self):
        self.chrome.osa = 'denied'                      # tabs can't be read: the first guess is what opens
        with mock.patch.object(artifacts, 'refresh', side_effect=AssertionError('no history scan needed')):
            r = artifacts.go(LINK % ID1)
        self.assertEqual((r['email'], r['profile']), (A, 'P1'))
        self.assertEqual(self.chrome.opened[-1][:2], ('P1', LINK % ID1))
        self.assertEqual(artifacts.read_owner_map()['artifacts'][ID1], {'o': A, 'p': 'P1', 's': 'published'})

    def test_a_busy_browser_still_puts_it_in_owners_json_for_the_link_app(self):
        self.patch(artifacts, 'GO_WAIT', 0.01)
        self.assertTrue(artifacts.GO_LOCK.acquire(timeout=5))      # another open or a check has the browser
        try:
            got = artifacts.go_quietly(LINK % ID1)
        finally:
            artifacts.GO_LOCK.release()
        self.assertEqual((got['by'], got['email'], got['profile']), ('map', A, 'P1'))
        m = artifacts.read_owner_map()
        self.assertEqual(m['artifacts'][ID1], {'o': A, 'p': 'P1', 's': 'published'})
        self.assertEqual(artifacts.map_lookup(m, ID1)['profile'], 'P1')      # the link app's next look: P1, not P2

    def test_a_busy_state_is_never_waited_for_before_an_open(self):
        took = []
        self.patch(artifacts, 'take_published_now', lambda: took.append(1) or 0)
        with core.locked() as st:                       # the page as it is once its tick took the line
            artifacts.take_published(st)
        self.chrome.osa = 'denied'                      # the open reads no tab, so it never needs the state's lock
        fd = os.open(core.data_path('.lock'), os.O_WRONLY | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)                  # another thread or process is saving the state right now
        r = artifacts.go(LINK % ID1)
        self.assertEqual((r['email'], r['profile']), (A, 'P1'))
        self.assertEqual(took, [])                      # the record waits for the page's next pass
        fcntl.flock(fd, fcntl.LOCK_UN)
        artifacts.go(LINK % ID1)
        self.assertEqual(took, [1])

    def test_an_open_with_nothing_new_in_the_record_writes_nothing(self):
        self.assertEqual(artifacts.take_published_now(), 1)
        with mock.patch.object(artifacts, 'locked', side_effect=AssertionError('nothing new: nothing written')):
            self.assertEqual(artifacts.take_published_now(), 0)     # what each open now runs first


if __name__ == '__main__':
    unittest.main()
