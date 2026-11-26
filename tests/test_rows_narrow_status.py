import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard status` on a narrow terminal: the pace words, a spend line and an account named with its organization
# never push an email off its line. An email is always shown whole; at 80 columns every line fits.
#
# Made-up *.example accounts only. No network, no engine, no page.
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import term

TZ = timezone(timedelta(hours=-8))
NOW = datetime(2026, 11, 13, 15, 31, 25, tzinfo=TZ).timestamp()          # Fri 3:31 PM
MON_NOON = datetime(2026, 11, 16, 12, 0, tzinfo=TZ).timestamp()
LONG = 'margarita.reyes-oyelaran@northwind-holdings.example'
TWIN_N, TWIN_P = 'maya@twin.example (Northwind Holdings)', 'maya@twin.example (Personal)'


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def w(pct, resets=MON_NOON):
    return {'pct': pct, 'resets': iso(resets), 'pace': None, 'lasts': None}


def acct(slot, email, weekly, session=None, cost=None, **more):
    return dict({'email': email, 'name': email, 'alias': '', 'slot': slot, 'plan': 'Team',
                 'org': 'Northwind Holdings', 'active': False, 'needs_login': False, 'disabled': False,
                 'unmanaged': False, 'trusted': True, 'checked_at': NOW - 60, 'usage_at': NOW - 60,
                 'usage': {'session': session, 'weekly': weekly, 'scoped': [], 'spend': cost}}, **more)


def view():
    cost = {'pct': 70, 'used': 1400.0, 'limit': 2000.0, 'currency': 'USD', 'resets': iso(NOW + 18 * 86400)}
    twin = acct(2, TWIN_N, w(80), cost=cost)            # ahead of its week, a spend line, and a model week over
    twin['usage']['scoped'] = [{'name': 'Fable', 'pct': 97, 'resets': iso(MON_NOON)}]
    return {'now': NOW, 'active': LONG, 'next': None,
            'auto': {'enabled': True, 'on': True, 'session_at': 90.0, 'weekly_at': 95.0, 'model_at': 95.0,
                     'models': ['Fable']},
            'accounts': [acct(1, LONG, w(88), session=w(40, NOW + 7200), active=True), twin, acct(3, TWIN_P, w(20))]}


class At80Columns(unittest.TestCase):
    def test_every_email_whole_and_every_line_fits(self):
        got = term.view(term.read(view(), tz=TZ), width=80)
        text = '\n'.join(got)
        for email in (LONG, TWIN_N, TWIN_P):
            self.assertTrue(any(email in x for x in got), email)          # whole, on one line
        self.assertLessEqual(max(len(x) for x in got), 80, text)
        self.assertIn('full by', text)                                    # the pace words still said
        self.assertIn('Fable 97%', text)                                  # and the note
        self.assertIn('$1,400 of $2,000', text)

    def test_the_header_names_the_account_whole(self):
        got = term.view(term.read(view(), tz=TZ), width=80)
        self.assertEqual(got[0], 'Claude Code is on ' + LONG)


if __name__ == '__main__':
    unittest.main()
