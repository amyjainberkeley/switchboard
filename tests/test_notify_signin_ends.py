import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Notifications for a sign-in about to end (server.tell, notify.send_together): once when it is 3 days from its end
# and once when it is 1 day from it ("Sign-in for … ends Wed 1:15 AM. Open Switchboard and press Sign in to all.", the
# account in use too), and once when it has ended ("Sign-in for … ended. Open Switchboard and press Sign in to all.").
# Each is said once per account, per end date and per stage, remembered in notify.json like every other
# note: a restart (a fresh process, the same notify.json) or a page left open never says it again, while a new sign-in
# has a new date, so its notes come again. Several due in one tick are one note, so the limit of three notes in five
# minutes never sees a burst. Only while Notifications are on, and never in the demo.
#
# Before this, an account that came to need a sign-in sent no notification at all (the ended note fails on the base).
# The engine is a stand-in (test_app's Sandbox); no notification is shown (NOTIFY_RUN is a fake). Made-up accounts.
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import core, notify, server, term
import test_app  # its Sandbox and its rows (its own tests are not run from here)

D, H = 86400, 3600
W = term.SIGNIN_WINDOW          # a sign-in can end any time from this long before its date
TODO = 'Open Switchboard and press Sign in to all.'
A, B, C, E = 'ana@work.example', 'ana@home.example', 'bo@lab.example', 'cy@side.example'


class SignInNotes(test_app.Sandbox):
    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.lines = server.plan_settings(self.auto)

    def view(self, *accounts):
        """The tick's view of the state (server.changes_view) after rows of (email, sign-in end, needs a sign-in);
        the first is the account in use."""
        st = core.blank_state()
        rows = [test_app.row(i + 1, email, i == 0, 20, refresh_expires_at=at,
                             status='needs_login' if need else 'ok') for i, (email, at, need) in enumerate(accounts)]
        server.apply_rows(st, rows)
        return server.changes_view(st)

    def tell(self, before, after, now):
        self.shown.clear()
        server.tell(before, after, self.lines, now)
        return [(c[8], c[9]) for c in self.shown]          # (title, body) of each note shown

    def when(self, at, now):
        return term.signin_words(at, now)[len('Sign-in ends '):]

    def test_once_at_3_days_and_once_at_1_day(self):
        at = self.now + W + 2.5 * D                                               # the earliest end 2.5 days away
        v = self.view((A, self.now + 20 * D, False), (B, at, False))
        self.assertEqual(self.tell(v, v, self.now), [
            ('Switchboard', ('Sign-in for %s ends %s. ' + TODO) % (B, self.when(at, self.now)))])
        self.assertEqual(self.tell(v, v, self.now + 60), [])                       # a page left open: not again
        self.assertEqual(self.tell(v, v, self.now + D), [])                        # still the 3-day stage
        later = at - W - 20 * H                                                   # 1 day from the earliest end
        self.assertEqual(self.tell(v, v, later), [
            ('Switchboard', ('Sign-in for %s ends %s. ' + TODO) % (B, self.when(at, later)))])
        self.assertEqual(self.tell(v, v, later + 600), [])

    def test_measured_from_the_earliest_it_can_end_not_its_date(self):
        # the date is the latest a sign-in ends; it can end from 3 days before (Claude Code's own rule)
        v = self.view((A, self.now + 20 * D, False), (B, self.now + 4 * D, False))     # its date 4 days off
        self.assertEqual(len(self.tell(v, v, self.now)), 1)                        # due already
        with open(notify.state_path()) as f:
            self.assertIn('signin:%s:1' % B, json.load(f)['seen'])                 # the 1-day note: 1 day from earliest
        far = self.view((A, self.now + 20 * D, False), (C, self.now + W + 3 * D + 600, False))
        self.assertEqual(self.tell(far, far, self.now), [])

    def test_nothing_further_off_than_3_days(self):
        v = self.view((A, self.now + 20 * D, False), (B, self.now + W + 3 * D + 600, False), (C, None, False))
        self.assertEqual(self.tell(v, v, self.now), [])

    def test_not_again_after_a_restart(self):
        at = self.now + W + 2 * D
        v = self.view((A, self.now + 20 * D, False), (B, at, False))
        self.assertEqual(len(self.tell(v, v, self.now)), 1)
        # a fresh process with the same notify.json (the same data folder): nothing is said again
        shown = self.in_a_fresh_process(v, self.now + 120)
        self.assertEqual(shown, [])
        then = at - W - 12 * H                                                    # its 1-day note: said once
        self.assertEqual(self.in_a_fresh_process(v, then), [[
            'Switchboard', ('Sign-in for %s ends %s. ' + TODO) % (B, self.when(at, then))]])

    def in_a_fresh_process(self, v, now):
        tmp = tempfile.mkdtemp(prefix='switchboard-notes-')
        out, view = os.path.join(tmp, 'shown.jsonl'), os.path.join(tmp, 'view.json')
        with open(view, 'w') as f:
            json.dump({'view': v, 'lines': self.lines, 'now': now}, f)
        script = ('import json, sys; sys.path.insert(0, sys.argv[1]); from switchboard import server; '
                  'server.NOTIFY_RUN = lambda cmd: open(sys.argv[3], "a").write(json.dumps(cmd[8:10]) + "\\n") and 0; '
                  'x = json.load(open(sys.argv[2])); server.tell(x["view"], x["view"], x["lines"], x["now"])')
        subprocess.run([sys.executable, '-I', '-B', '-c', script, ROOT, view, out], check=True, timeout=120,
                       env=dict((k, os.environ[k]) for k in ('HOME', 'SWITCHBOARD_HOME', 'CLAUDE_CONFIG_DIR', 'PATH')
                                if k in os.environ))
        if not os.path.exists(out):
            return []
        with open(out) as f:
            return [json.loads(x) for x in f if x.strip()]

    def test_a_new_sign_in_has_a_new_date_so_its_notes_come_again(self):
        at = self.now + 2 * D
        v = self.view((A, self.now + 20 * D, False), (B, at, False))
        self.assertEqual(len(self.tell(v, v, self.now)), 1)
        new = at + 30 * D                                                         # signed in again, a date further on
        then = new - 2.5 * D
        v2 = self.view((A, self.now + 50 * D, False), (B, new, False))
        self.assertEqual(self.tell(v2, v2, then), [
            ('Switchboard', ('Sign-in for %s ends %s. ' + TODO) % (B, self.when(new, then)))])

    def test_the_account_in_use_is_told_like_any_other_with_no_login_line(self):
        at = self.now + 20 * H
        v = self.view((A, at, False), (B, self.now + 20 * D, False))
        self.assertEqual(self.tell(v, v, self.now), [
            ('Switchboard', ('Sign-in for %s ends %s. ' + TODO) % (A, self.when(at, self.now)))])

    def test_several_due_in_one_tick_are_one_note(self):
        ats = {A: self.now + 2.5 * D, B: self.now + 2 * D, C: self.now + 2.2 * D, E: self.now + 10 * H}
        v = self.view(*[(e, at, False) for e, at in ats.items()])
        got = self.tell(v, v, self.now)
        self.assertEqual(len(got), 1)
        # soonest first, at most three named, then how many more, and what to do last
        self.assertEqual(got[0], ('Switchboard', 'Sign-ins end soon: %s %s; %s %s; %s %s; and 1 more. Open Switchboard '
                                                 'and press Sign in to all.'
                                  % (E, self.when(ats[E], self.now), B, self.when(ats[B], self.now), C,
                                     self.when(ats[C], self.now))))
        self.assertEqual(self.tell(v, v, self.now + 60), [])                       # each remembered
        with open(notify.state_path()) as f:
            self.assertEqual(len(json.load(f)['recent']), 1)                       # one note toward the limit

    def test_five_long_emails_in_one_note_keep_what_to_do_whole(self):
        long = ['%s.with-a-long-name@an-organization-with-a-long-name-%d.example' % (n, i)
                for i, n in enumerate(('maya.reyes', 'ana.lee', 'bo.chen', 'cy.diaz', 'di.evans'))]
        v = self.view(*[(e, self.now + (10 + 5 * i) * H, False) for i, e in enumerate(long)])   # the first in use
        got = self.tell(v, v, self.now)
        self.assertEqual(len(got), 1)
        body = got[0][1]
        self.assertLessEqual(len(body), server.NOTE_ROOM)                         # never cut by notify's own limit
        self.assertNotIn('…', body)
        self.assertTrue(body.endswith('Open Switchboard and press Sign in to all.'), body)
        self.assertNotIn('/login', body)
        named = [e for e in long if e in body]
        self.assertEqual(named, long[:len(named)])                                 # soonest first
        self.assertTrue(1 <= len(named) <= 3, body)
        self.assertIn('and %d more' % (5 - len(named)), body)

    def test_none_for_an_account_that_already_needed_a_sign_in_at_the_first_look(self):
        at = self.now - 3 * D
        after = self.view((A, self.now + 20 * D, False), (B, at, True))
        self.assertEqual(self.tell({'active': None, 'accounts': {}}, after, self.now), [])   # no state yet
        older = self.view((A, self.now + 20 * D, False), (B, None, True))       # an older build's: no date kept
        self.assertEqual(self.tell(older, after, self.now), [])
        refused = self.view((A, self.now + 20 * D, False), (B, self.now + D, True))   # refused before its date
        self.assertEqual(self.tell(refused, after, self.now), [])                  # told then: not again

    def test_once_when_it_has_ended(self):
        at = self.now - 60
        before = self.view((A, self.now + 20 * D, False), (B, at, False))       # the tick before: not refused yet
        after = self.view((A, self.now + 20 * D, False), (B, at, True))
        self.assertEqual(self.tell(before, after, self.now),
                         [('Switchboard', 'Sign-in for %s ended. Open Switchboard and press Sign in to all.' % B)])
        self.assertEqual(self.tell(after, after, self.now + 60), [])
        self.assertEqual(self.tell(after, after, self.now + 9 * D), [])          # long after, when notify.json forgot it
        self.assertEqual(self.tell(before, after, self.now + 120), [])           # said once per end date

    def test_a_sign_in_needed_for_another_reason_keeps_todays_silence(self):
        before = self.view((A, self.now + 20 * D, False), (B, self.now + 2 * D, False))
        self.tell(before, before, self.now)                                        # its 3-day note, said
        after = self.view((A, self.now + 20 * D, False), (B, self.now + 2 * D, True))   # refused before its date
        self.assertEqual(self.tell(before, after, self.now + 60), [])
        self.assertEqual(self.tell(after, after, self.now + 1.5 * D), [])          # and no 1-day note for it

    def test_none_while_notifications_are_off_or_in_the_demo(self):
        v = self.view((A, self.now + 20 * D, False), (B, self.now + 2 * D, False))
        core.set_setting('notify', False)
        self.assertEqual(self.tell(v, v, self.now), [])
        core.set_setting('notify', True)
        core.DEMO = True
        self.assertEqual(self.tell(v, v, self.now), [])
        core.DEMO = False
        self.assertEqual(len(self.tell(v, v, self.now)), 1)                       # nothing was remembered meanwhile


if __name__ == '__main__':
    unittest.main()
