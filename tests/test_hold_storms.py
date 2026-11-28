import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The fault in its true order, with the stand-in Claude Code savers of test_savers_many_cases (each reads
# the item, else the plain login file, else nothing, changes its part and saves the whole object; a refused save
# writes the file and deletes the item; a save that landed after it read "no such item" removes the file), on three
# orders of that test's fixed stream in which a saver read "no item and no file" (and so saved an item without the
# login). The same orders run twice: as that test runs them, and with the sign-in watch's call of the hold after
# every step (the watch tells the hold at once, in its own loop). With the hold, no saver reads "no item and no file"
# in those orders; that test's own checks (a) to (f) still hold, and no file of ours is left at the end.
# Stand-ins only (r17_engine_fakes, an in-process Keychain and a made-up Anthropic); made-up *.example accounts.
import os
import random
import shutil
import tempfile
import unittest
from unittest import mock

import test_savers_many_cases as many
from hold_fakes import stamp_of
from switchboard.accounts import api, autoloop, putback

# Orders of test_savers_many_cases' stream (its SEED) in which a saver read "no item and no file" while the settings
# named an account without the hold, and none did with it: of its first 7,000 orders, 17 have such a read without the
# hold and 8 with it. In the 9 the hold mends (these three among them) another saver removed the file within the
# hold's three seconds of Claude Code's write. In the 8 others a saver removed it later than that (that test's steps
# are 0.01 to 6 s apart; in pile-ups on the real Keychain tool the removal came within 0.67 s of the write), where the
# hold does not reach by design.
ORDERS = (498, 3717, 4229)


class CountingSaver(many.Saver):
    """test_savers_many_cases' saver; its fallback write is dated by the world's clock (a file Claude Code writes is
    dated now; that test's files carry the real time, days from its clock), in both arms alike."""

    def act(self):
        step = self.step
        did = super().act()
        if step == 'file' and os.path.exists(self.w.cred):
            t = self.w.clock.wall()
            os.utime(self.w.cred, (t, t))
        return did

    def take(self, text):
        if not text and self.w.named() is not None:
            self.w.empty_reads += 1             # its fresh read found no item and no file, while signed in
        super().take(text)


class HoldWorld(many.World):
    """test_savers_many_cases' world; with hold=True the sign-in watch's hold is told of every change of the plain
    login file and the settings at once (MiniWatch's rule), and the watch's own hint never reports the hold's file."""

    def __init__(self, rng, tmp, hold):
        super().__init__(rng, tmp)
        self.savers = [CountingSaver(self, s.n, s.honors) for s in self.savers]
        self.hold_on = hold
        self.empty_reads = 0
        self.file_seen = stamp_of(self.cred)
        self.settings_seen = self.config_text()

    def config_text(self):
        try:
            with open(self.rig.config_path(), 'rb') as f:
                return f.read()
        except FileNotFoundError:
            return None

    def file_is_holds(self):
        note = putback._hold_note(self.ctx)
        st = stamp_of(self.cred)
        return st is not None and note is not None and note['stamp'] == st

    def tell(self):
        """What the watch does at once when the plain login file or the settings changed."""
        if not self.hold_on:
            return
        now = self.config_text()
        if now != self.settings_seen:
            self.settings_seen = now
            api.watch_hold('settings')
        f = stamp_of(self.cred)
        old, self.file_seen = self.file_seen, f
        ev = 'appeared' if old is None and f is not None else 'gone' if old is not None and f is None else \
            'changed' if old != f else None
        if ev is not None and api.watch_hold(ev).get('did') == 'held':
            self.file_seen = stamp_of(self.cred)        # the watch sees its hold's own link at once
            api.watch_hold('appeared')

    def hint(self):
        h = super().hint()
        if self.hold_on and h.get('login_file') in ('appeared', 'changed') and self.file_is_holds():
            h['login_file'] = None              # the hold's own file: the watch drops it from the look's hint
        h['login_file_written'] = h.get('login_file') in ('appeared', 'changed')
        return h

    def look(self):
        self.tell()
        res = super().look()
        self.tell()
        return res


def run_order(case, hold):
    """test_savers_many_cases' case `case`, step for step, with or without the hold: {'empty_reads', 'broken',
    'ours_left'}."""
    rng = random.Random(many.SEED * 100003 + case)
    tmp = tempfile.mkdtemp(prefix='hold-storm-')
    w = HoldWorld(rng, tmp, hold)
    try:
        for _ in range(rng.randint(15, 45)):
            r = rng.random()
            idle = [s for s in w.savers if s.step is None]
            busy = [s for s in w.savers if s.step is not None]
            if r < 0.30 and idle:
                rng.choice(idle).start(renew=rng.random() < 0.15)
            elif r < 0.60 and busy:
                rng.choice(busy).act()
            elif r < 0.85:
                w.look()
            elif r < 0.88 and not w.signed_out and not busy:
                w.sign_out()
            elif r < 0.92 and not busy:
                w.sign_in()
            elif r < 0.94 and not w.signed_out and not busy:
                w.switch()
            elif r < 0.97 and not busy:
                w.lost()
            w.tell()
            w.clock.advance(rng.choice((0.01, 0.05, 0.2, 0.6, 2.0, 6.0)))
            w.check()
            if w.broken:
                break
        if not w.broken:
            for s in w.savers:
                steps = 0
                while s.step is not None and steps < 20:
                    s.act()
                    w.tell()
                    steps += 1
                    w.clock.advance(0.05)
            for i in range(90):
                w.clock.advance(0.5 if i < 8 else 5.0)
                w.look()
                w.check()
                if i > 10 and w.settled() and not w.file_is_holds():     # a file kept for a while goes by a later look
                    break
        said = w.secrets_said()
        if said:
            w.fail('(f) a token in %r' % said[0][:80])
        return {'empty_reads': w.empty_reads, 'broken': list(w.broken), 'ours_left': w.file_is_holds()}
    finally:
        w.close()
        shutil.rmtree(tmp, ignore_errors=True)


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        patcher = mock.patch.object(autoloop, 'notify_put_back', lambda res: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        putback.reset()
        putback._KEPT.clear()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home


class TheFaultsTrueOrder(Base):
    def test_in_three_orders_where_a_saver_read_nothing_with_the_hold_none_does(self):
        for case in ORDERS:
            without = run_order(case, hold=False)
            self.assertGreater(without['empty_reads'], 0, 'case %d: the order this test was chosen for' % case)
            self.assertEqual(without['broken'], [], case)
            putback.reset()
            with_hold = run_order(case, hold=True)
            self.assertEqual(with_hold['empty_reads'], 0, 'case %d' % case)
            self.assertEqual(with_hold['broken'], [], case)
            self.assertFalse(with_hold['ours_left'], 'case %d: a file of ours is left' % case)
            putback.reset()


if __name__ == '__main__':
    unittest.main()
