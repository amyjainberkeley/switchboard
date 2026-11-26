import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# An account's own switch line (`switchboard auto line`) is its line everywhere: History says it reached its switch
# line at its own line, never at the shared one, and the notification "near its limit" comes at its own line less the
# landing room. An account with no line of its own is judged as before. Made-up accounts only.
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core, history, notify, server
from switchboard.accounts import autoloop, usage
from test_auto_check import CheckCase, ok

OWN, SHARED = 'own@acct.example', 'shared@acct.example'
H = 3600.0


def state(own_pct, shared_pct, now):
    """The tick's view: two accounts, each with a 5-hour window at that percent, refilling in 3 hours."""
    def acct(aid, pct):
        return {'id': aid, 'usage': {'session': {'pct': pct, 'resets_at': now + 3 * H},
                                     'weekly': {'pct': 10.0, 'resets_at': now + 90 * H}}}
    return {'active': SHARED, 'accounts': {OWN: acct('acc-own', own_pct), SHARED: acct('acc-shared', shared_pct)}}


def lines(own=None):
    out = {'session_line': 90, 'weekly_line': 95, 'landing_room': 3}
    if own:
        out['own_lines'] = {'acc-own': own}
    return out


class History(unittest.TestCase):
    def reached(self, before, after, ls, now):
        st = core.blank_state()
        history.note_tick(st, before, after, ls, now=now)
        return [e['text'] for e in st['events'] if 'reached its' in e['text']]

    def test_an_own_line_of_99_says_nothing_at_92(self):
        now = time.time()
        said = self.reached(state(80, 80, now), state(92, 92, now), lines({'session_at': 99.0}), now)
        self.assertEqual(said, ['%s reached its 5-hour switch line: 92%%' % SHARED])

    def test_an_own_line_of_60_is_said_at_60(self):
        now = time.time()
        said = self.reached(state(50, 50, now), state(65, 65, now), lines({'session_at': 60.0}), now)
        self.assertEqual(said, ['%s reached its 5-hour switch line: 65%%' % OWN])

    def test_with_no_own_line_it_is_as_before(self):
        now = time.time()
        said = self.reached(state(80, 80, now), state(92, 92, now), lines(), now)
        self.assertEqual(sorted(said), sorted('%s reached its 5-hour switch line: 92%%' % e for e in (OWN, SHARED)))


class Notifications(unittest.TestCase):
    def told(self, before, after, ls, now):
        sent = []
        with mock.patch.object(server, 'helper_settings', lambda: {'notify': True}), \
                mock.patch.object(notify, 'send', lambda note, **k: sent.append(note.key)), \
                mock.patch.object(server, 'signin_notes', lambda *a: []):
            server.tell(before, after, ls, now)
        return sorted(k for k in sent if k.startswith('near:'))

    def test_near_comes_at_each_accounts_own_line(self):
        now = time.time()
        # shared line 90 less 3: near at 87; the own line 60 less 3: near at 57
        self.assertEqual(self.told(state(50, 80, now), state(58, 80, now), lines({'session_at': 60.0}), now),
                         ['near:%s:five_hour' % OWN])
        self.assertEqual(self.told(state(80, 80, now), state(88, 88, now), lines({'session_at': 99.0}), now),
                         ['near:%s:five_hour' % SHARED])

    def test_with_no_own_line_it_is_as_before(self):
        now = time.time()
        self.assertEqual(self.told(state(80, 80, now), state(88, 88, now), lines(), now),
                         sorted('near:%s:five_hour' % e for e in (OWN, SHARED)))

    def test_a_switch_is_told_once(self):
        now = time.time()
        sent = []
        after = dict(state(50, 50, now), active=OWN)
        with mock.patch.object(server, 'helper_settings', lambda: {'notify': True}), \
                mock.patch.object(notify, 'send', lambda note, **k: sent.append(note.key)), \
                mock.patch.object(server, 'signin_notes', lambda *a: []):
            server.tell(state(50, 50, now), after, lines({'session_at': 60.0}), now)
        self.assertEqual([k for k in sent if k.startswith('switch:')], ['switch:%s>%s' % (SHARED, OWN)])


class ReadingPace(CheckCase):
    def lines_read_at(self):
        seen = []
        real = usage.Context

        def context(*a, **k):
            seen.append(dict(k.get('lines') or {}))
            return real(*a, **k)
        self.answers(ok(65.0, 30.0), ok(10.0, 20.0), ok(10.0, 20.0))
        with mock.patch.object(usage, 'Context', context):
            autoloop.Loop(deps=self.deps(), clock=self.clock).run_pass()
        return seen[0]

    def test_the_account_in_use_is_read_against_its_own_line(self):
        autoloop.save_settings(enabled=True)
        s = core.settings()                     # the store keeps it so; save_settings asks the real engine's list
        s['auto_policy'] = dict(s.get('auto_policy') or {}, own_lines={'a1': {'session_at': 60.0}})
        core.write_json(core.data_path('settings.json'), s)
        self.assertEqual(self.lines_read_at(), {'5-hour': 60.0, 'weekly': 95.0})

    def test_with_no_own_line_the_shared_ones(self):
        autoloop.save_settings(enabled=True)
        self.assertEqual(self.lines_read_at(), {'5-hour': 90.0, 'weekly': 95.0})


if __name__ == '__main__':
    unittest.main()
