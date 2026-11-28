import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A plain login file of ours counts its HOLD_TIDY_S from the moment Claude Code's Keychain item was last known absent:
# a look that reads "no such item" while that file is there moves its note's 'wrote' to now (the processes that asked
# at that look read the file for 30 s more). The note is rewritten at most once a second and without waiting for the
# disk (it only moves a time later), and only while it is still the note that look read: another process that put a
# new file of ours in place meanwhile keeps its note, and so its file.
# The account engine on its stand-ins (r17_engine_fakes) with a fake clock; made-up *.example accounts.
import json
import os
import unittest
from unittest import mock

from hold_fakes import Base
from switchboard.accounts import putback


class ItemGoneAgain(Base):
    def promoted_then_gone(self):
        """A promote kept Claude Code's plain login file as one of ours; two seconds later the item went again."""
        self.cc_writes()
        self.hold('appeared')
        self.cc_deletes_item()
        self.assertEqual(self.look(after=0.2, keychain=True).action, 'wait')
        self.assertEqual(self.look(after=putback.PUT_BACK_AFTER_S).action, 'promote')
        self.assertTrue(self.ours())
        self.clock.advance(2.0)
        self.cc_deletes_item()

    def note_writes(self):
        """A list that gets (wrote, sync) of each write of signin-hold.json from now on."""
        writes = []
        real = putback._hold_write_note

        def write(ctx, note, sync=True):
            writes.append((note.get('wrote'), sync))
            return real(ctx, note, sync)
        patcher = mock.patch.object(putback, '_hold_write_note', write)
        patcher.start()
        self.addCleanup(patcher.stop)
        return writes

    def another_process_puts_a_new_file_of_ours(self):
        """What another process's hand-over or replacement leaves: a new file of ours and the note naming it."""
        tmp = self.cred + '.made-up'
        with open(tmp, 'w') as f:
            f.write(self.file_text() + ' ')
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.cred)
        st = os.stat(self.cred)
        note = dict(self.note(), stamp=[st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size], prev=None, tmp=None,
                    state='settled', at=self.clock.wall(), wrote=self.clock.wall())
        with open(self.ctx.path(putback.HOLD_FILE), 'w') as f:
            json.dump(note, f)
        return note['stamp']


class TheNoteIsRewritten(ItemGoneAgain):
    def test_at_most_once_a_second_and_without_waiting_for_the_disk(self):
        self.promoted_then_gone()
        writes = self.note_writes()
        first = self.clock.wall() + 0.2
        for _ in range(3):
            self.assertEqual(self.look(after=0.2, keychain=True).action, 'wait')
        self.assertEqual(len(writes), 1, writes)
        self.assertAlmostEqual(writes[0][0], first, places=3)       # its time started again at the first look
        self.assertIs(writes[0][1], False)
        self.assertAlmostEqual(self.note()['wrote'], first, places=3)

    def test_never_over_a_note_another_process_wrote_in_between(self):
        self.promoted_then_gone()
        self.clock.advance(1.5)
        real = putback._hold_absent
        state = {}

        def absent(ctx, note):
            if note is not None and not state:
                state['stamp'] = self.another_process_puts_a_new_file_of_ours()
            return real(ctx, note)
        with mock.patch.object(putback, '_hold_absent', absent):
            self.look(after=0.1, keychain=True)
        self.assertIn('stamp', state)                               # it landed between the look's read and write
        self.assertEqual(self.note()['stamp'], state['stamp'])
        self.assertTrue(self.ours())                                # so that file is still known as ours


if __name__ == '__main__':
    unittest.main()
