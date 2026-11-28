import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# On a busy Mac (load per core above keychain.LOAD_HIGH, the line autoloop.LOAD_HIGH draws for optional switches)
# Switchboard's routine Keychain calls are spaced out, so it is not one more process queuing on `security` while
# Claude Code's own reads of its item, limited to 2 s, run out. The sign-in watch's looks for a change of the Keychain
# or the settings alone come CAP_GAP_S apart from the first, not MIN_GAP_S; the plain login file kept for Claude Code
# waits for a later look when only the item's other parts changed. What is urgent keeps its pace: a login file that
# appeared, changed or went, an answer that asks for the fast pace (a loss being put back), a renewal's follow.
# The watch on a fake clock with made-up changes (signin_watch_fakes), and the account engine on its stand-ins
# (fakes_accounts); made-up *.example accounts.
import json
import unittest

from hold_fakes import Base
from signin_watch_fakes import Answer, T0_MONO, make
from switchboard.accounts import autoloop, keychain, policy, putback
from switchboard.accounts import signin_watch as sw

T = T0_MONO


def per_minute(times, start, end):
    """The most looks that started in any 60 s between start and end."""
    times = sorted(t for t in times if start <= t <= end)
    best, j = 0, 0
    for i, t in enumerate(times):
        while times[j] <= t - 60.0:
            j += 1
        best = max(best, i - j + 1)
    return best


class TheLine(unittest.TestCase):
    def test_is_the_loops_own(self):
        self.assertEqual(keychain.LOAD_HIGH, autoloop.LOAD_HIGH)
        self.assertEqual(keychain.LOAD_HIGH, policy.LOAD_HIGH)

    def test_busy_mac_reads_the_load_per_core(self):
        old = keychain.LOAD
        try:
            for load, busy in ((0.0, False), (keychain.LOAD_HIGH, False), (keychain.LOAD_HIGH + 0.1, True),
                               (None, False), (float('nan'), False), (True, False)):
                keychain.LOAD = lambda: load
                self.assertEqual(keychain.busy_mac(), busy, load)

            def boom():
                raise OSError('no load')
            keychain.LOAD = boom
            self.assertFalse(keychain.busy_mac())
        finally:
            keychain.LOAD = old

    def test_the_watch_the_process_runs_is_given_it(self):
        import inspect
        self.assertIn('busy=keychain.busy_mac', inspect.getsource(sw.build))


class TheWatchsPace(unittest.TestCase):
    def storm(self, busy, **hint):
        """A change every 0.5 s for two minutes; returns the times looks started after the first."""
        w, sim, looks, _, _, _ = make()
        w.busy = (lambda: busy)
        for i in range(240):
            sim.change(T + 1.0 + i * 0.5, **(hint or {'keychain': True}))
        w.run(until=T + 125.0)
        return [t for t, _ in looks.calls if t > T]

    def test_keychain_changes_on_a_busy_mac_give_one_look_every_cap_gap_s(self):
        quiet, busy = self.storm(False), self.storm(True)
        self.assertGreaterEqual(per_minute(quiet, T + 1, T + 125), 30)          # the watch's own cap: 40
        self.assertLessEqual(per_minute(busy, T + 1, T + 125), int(60 / sw.CAP_GAP_S) + 1)
        gaps = [b - a for a, b in zip(busy, busy[1:])]
        self.assertGreaterEqual(min(gaps), sw.CAP_GAP_S - 1e-6)

    def test_settings_changes_too(self):
        busy = self.storm(True, settings=True)
        self.assertLessEqual(per_minute(busy, T + 1, T + 125), int(60 / sw.CAP_GAP_S) + 1)

    def first_look_after(self, busy, *changes):
        w, sim, looks, _, _, _ = make()
        w.busy = (lambda: busy)
        for at, hint in changes:
            sim.change(T + at, **hint)
        w.run(until=T + 8.0)
        return [t - T for t, _ in looks.calls if t > T][0]

    def test_a_login_file_that_appeared_keeps_its_pace(self):
        changes = ((1.0, {'keychain': True}), (1.6, {'login_file': 'appeared', 'login_file_written': True}))
        self.assertAlmostEqual(self.first_look_after(True, *changes), 1.6, places=6)
        self.assertAlmostEqual(self.first_look_after(True, *changes[:1]), sw.CAP_GAP_S, places=6)

    def test_a_login_file_that_went_keeps_the_quiet_pace(self):
        # the Keychain change alone would wait CAP_GAP_S; the login file's going is looked at by the quiet pace
        changes = ((1.0, {'keychain': True}), (1.4, {'login_file': 'gone'}))
        self.assertLessEqual(self.first_look_after(True, *changes), 1.4 + sw.SETTLE_S + 1e-6)

    def test_an_answer_that_asks_for_the_fast_pace_keeps_it(self):
        counts = []
        for busy in (False, True):
            w, sim, looks, _, _, _ = make(answer=lambda hint: Answer('wait', watch=True, next_s=0.5))
            w.busy = (lambda: busy)
            sim.change(T + 1.0, login_file='appeared', login_file_written=True)
            w.run(until=T + 20.0)
            counts.append(len([t for t, _ in looks.calls if t > T]))
        self.assertEqual(counts[0], counts[1])
        self.assertGreater(counts[1], 15)

    def test_without_a_load_source_the_pace_is_as_before(self):
        w, sim, looks, _, _, _ = make()
        self.assertIsNone(w.busy)
        for i in range(20):
            sim.change(T + 1.0 + i * 0.5, keychain=True)
        w.run(until=T + 15.0)
        made = [t for t, _ in looks.calls if t > T]
        self.assertLessEqual(min(b - a for a, b in zip(made, made[1:])), sw.MIN_GAP_S + 0.2)


class TheCopysFollow(Base):
    MIRROR = True

    def busy(self, on):
        keychain.LOAD = (lambda: keychain.LOAD_HIGH + 1.0) if on else (lambda: 0.0)

    def calls(self, since):
        return [c for c in self.rig.kc.calls[since:] if c[1] == self.rig.service]

    def test_a_change_of_the_other_parts_alone_waits_on_a_busy_mac(self):
        self.look(keychain=True)
        self.busy(True)
        blob = json.loads(self.rig.live_text())
        blob['mcpOAuth'] = {'docs-server': {'accessToken': 'mcp-made-up-9', 'expiresAt': 9}}
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        before = len(self.rig.kc.calls)
        text = self.file_text()
        self.look(after=1.0, keychain=True)
        self.assertEqual(self.file_text(), text)
        self.assertEqual(len(self.calls(before)), 1)          # the look's own read, nothing under the save lock
        self.busy(False)
        # the copy's age is its real modification time against the fake clock: the margin covers the real seconds
        self.look(after=putback.MIRROR_PARTS_S + 600.0, keychain=True)
        self.assertEqual(self.file_text(), self.rig.live_text())
        blob['mcpOAuth'] = {'docs-server': {'accessToken': 'mcp-made-up-10', 'expiresAt': 10}}
        self.rig.set_live(json.dumps(blob, separators=(',', ':')))
        self.busy(True)
        text = self.file_text()
        self.look(after=putback.MIRROR_PARTS_S, keychain=True)     # an old copy, a busy Mac: still a later look
        self.assertEqual(self.file_text(), text)

    def test_an_old_file_of_claude_codes_judged_once_is_not_read_for_again(self):
        # Claude Code's file holds a newer login than the item: never adopted. Judging it takes Claude Code's save
        # lock and a fresh read; the judgement stands until the file or the item's login changes
        self.clock.advance(600.0)
        newer = self.rig.server.refresh_for(self.rig.live_text())
        self.cc_writes(newer, age=putback.HOLD_TIDY_S + 1)
        self.look(keychain=True)                              # judged: one read of its own, one under the lock
        before = len(self.rig.kc.calls)
        for _ in range(5):
            self.look(after=1.0, keychain=True)
        self.assertEqual(len(self.calls(before)), 5)          # the looks' own reads, nothing more
        self.assertEqual(self.file_text(), newer)
        self.assertFalse(self.ours())

    def test_a_renewal_is_followed_at_once_on_a_busy_mac(self):
        self.look(keychain=True)
        self.busy(True)
        self.clock.advance(600.0)
        renewed = self.rig.claude_renews()
        self.look(after=0.2, keychain=True)
        self.assertEqual(self.file_text(), renewed)

    def test_a_removal_is_written_again_at_once_on_a_busy_mac(self):
        self.look(keychain=True)
        self.busy(True)
        self.cc_removes()
        self.hold('gone')
        self.look(after=0.2, login_file='gone')
        self.assertEqual(self.file_text(), self.rig.live_text())

    def test_a_loss_is_put_back_on_the_same_schedule_on_a_busy_mac(self):
        self.look(keychain=True)
        self.busy(True)
        self.cc_deletes_item()
        res, t = self.look(after=0.15, keychain=True), 0.15
        while res.action == 'wait' and t < 30:
            t += res.next_s or 1.0
            res = self.look(after=res.next_s or 1.0)
        self.assertEqual(res.action, 'promote', res.reason)
        self.assertLess(t, putback.SIGN_OUT_S + putback.PUT_BACK_AFTER_S + 0.5)


if __name__ == '__main__':
    unittest.main()
