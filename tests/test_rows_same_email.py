import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Two accounts with one email: one person can be in two organizations under one email. Each is named by its email and
# its organization ("maya@twin.example (Northwind)") on every surface that names it: the page's state (and so the
# page, the menu bar and notifications), the terminal, History's lines. A command given that email says it fits two
# accounts and how to name one. `switchboard status --json` keeps the bare email, and the organization in its field.
#
# When all emails differ, nothing changes anywhere: not a word, not a space. A test over the demo's accounts, and a
# check over many made-up lists, pin that.
#
# Made-up *.example accounts only. The engine is tests/fakes_accounts_api.py.
import io
import os
import random
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import core, demo, history, menubar, names, notify, report, server, term
from test_cli_commands import Cli

TWIN, OTHER = 'maya@twin.example', 'bo@other.example'
NORTH, OWN = 'maya@twin.example (Northwind)', 'maya@twin.example (Personal)'


def row(number, email, org_name, org_id, **more):
    """A row as engine.listing gives it."""
    return dict({'id': 'acct-%d' % number, 'number': number, 'email': email, 'org_id': org_id, 'org_name': org_name,
                 'plan': 'team', 'tier': None, 'org_type': None, 'alias': None, 'status': 'ok', 'active': False,
                 'unmanaged': False, 'usage': None, 'usage_at': None, 'checked_at': None, 'source': 'none',
                 'frozen': False, 'usage_error': None, 'models_seen': [], 'refresh_expires_at': None,
                 'emptied': False}, **more)


def twin_rows():
    now = time.time()
    use = {'session': None, 'weekly': {'pct': 30.0, 'resets': server.iso(now + 3 * 86400), 'pace': None, 'lasts': None},
           'scoped': [], 'spend': None}
    return [row(1, TWIN, 'Northwind', 'org-n', active=True, usage=use, usage_at=now - 30),
            row(2, 'Maya@Twin.example', "maya@twin.example's Organization", 'org-p', usage=use, usage_at=now - 30),
            row(3, OTHER, 'Other Co', 'org-o', usage=use, usage_at=now - 30)]


FACTS = {'profiles': {}, 'browser': {'name': '', 'present': False, 'tabs_blocked': False},
         'engine': {'installed': True, 'claude': True}, 'links': {'installed': False, 'on': False},
         'auto': {'available': True, 'enabled': False, 'session_at': 90, 'weekly_at': 95},
         'setup_hidden': True, 'demo': False}


def rendered(rows):
    st = core.blank_state()
    server.apply_rows(st, rows)
    return st, server.render(st, dict(FACTS, now=time.time()))


class TheNames(unittest.TestCase):
    def test_two_accounts_with_one_email_are_named_by_their_organization(self):
        self.assertEqual(names.keys(twin_rows()), [NORTH, OWN, OTHER])

    def test_an_organization_not_known_or_said_twice_is_its_number(self):
        rows = [row(1, TWIN, None, 'a'), row(2, TWIN, 'Acme', 'b'), row(3, TWIN, 'Acme', 'c')]
        self.assertEqual(names.keys(rows), [TWIN + ' (#1)', TWIN + ' (Acme #2)', TWIN + ' (Acme #3)'])
        self.assertEqual(names.email_of(TWIN + ' (Acme #2)'), TWIN)
        self.assertEqual(names.email_of(TWIN), TWIN)

    def test_when_all_emails_differ_every_name_is_the_email_over_many_made_up_lists(self):
        rnd = random.Random(20261125)
        for _ in range(2000):
            n = rnd.randint(0, 8)
            emails = rnd.sample(['a%d@x%d.example' % (i, i % 3) for i in range(40)], n)
            rows = [row(i + 1, rnd.choice([e, e.upper()]), rnd.choice([None, '', 'Acme', "e's Organization"]), 'o')
                    for i, e in enumerate(emails)]
            self.assertEqual(names.keys(rows), [e.lower() for e in emails])

    def test_the_command_says_it_fits_two_and_how_to_name_one(self):
        self.assertEqual(names.ambiguous(TWIN, [(1, 'Northwind'), (2, 'Personal')]),
                         'maya@twin.example fits two accounts: 1 (Northwind) and 2 (Personal). Give one a short name '
                         'and use that: switchboard alias 2 SHORT. Its number works for now; it changes when the list '
                         'changes.')


class EverySurface(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = False

    def test_the_state_keeps_both_and_the_page_names_each(self):
        st, v = rendered(twin_rows())
        self.assertEqual(sorted(k for k, a in st['accounts'].items() if a.get('connected')), sorted([NORTH, OWN, OTHER]))
        self.assertEqual([a['email'] for a in v['accounts']], [NORTH, OWN, OTHER])
        self.assertEqual(v['active'], NORTH)
        self.assertEqual((st['accounts'][NORTH]['id'], st['accounts'][OWN]['id']), ('acct-1', 'acct-2'))

    def test_the_terminal_and_the_menu_bar(self):
        _, v = rendered(twin_rows())
        lines = term.view(term.read(v))
        self.assertEqual(lines[0].split(' · ')[0], 'Claude Code is on ' + NORTH)
        self.assertTrue(any(x.startswith('1  ' + NORTH + '  ') for x in lines), lines)
        self.assertTrue(any(x.startswith('2  ' + OWN + '  ') for x in lines), lines)
        self.assertEqual([r['email'] for r in menubar.rows(v)], [NORTH, OWN, OTHER])

    def test_notifications_and_history_lines(self):
        st, _ = rendered(twin_rows())
        before = {'accounts': dict((k, {'usage': {'weekly': {'pct': 10, 'resets': a['usage']['weekly']['resets']}}})
                                   for k, a in st['accounts'].items())}
        after = {'accounts': dict((k, {'usage': {'weekly': {'pct': 96, 'resets': a['usage']['weekly']['resets']}}})
                                  for k, a in st['accounts'].items())}
        notes = [n for n in notify.from_states(before, after, now=time.time()) if n]
        self.assertIn(OWN + ' is near its limit', [n.title for n in notes])
        history.note_tick(st, before, after, {'session_line': 90, 'weekly_line': 95})
        texts = [e['text'] for e in st['events']]
        self.assertIn(OWN + ' reached its weekly switch line: 96%', texts)

    def test_status_json_keeps_the_email_and_says_the_organization(self):
        _, v = rendered(twin_rows())
        doc = report.document(v, '0.0.0')
        self.assertEqual([(a['email'], a['organization']) for a in doc['accounts']],
                         [(TWIN, 'Northwind'), (TWIN, 'Personal'), (OTHER, 'Other Co')])
        self.assertEqual(doc['in_use'], TWIN)

    def test_a_switch_the_engine_recorded_is_found_by_its_name(self):
        rec = {'kind': 'switch', 'to': TWIN, 'account': TWIN, 'by': 'you', 't': time.time()}
        with mock.patch.object(history, 'newest', lambda n: [rec]):
            self.assertIs(history.recorded_switch(OWN, since=time.time() - 60), rec)

    def test_the_page_and_the_menu_can_send_either_name(self):
        accounts = {NORTH: {}, OWN: {}, OTHER: {}}
        self.assertEqual(names.key_of(accounts, 'maya@twin.example (northwind)'), NORTH)
        self.assertEqual(names.key_of(accounts, 'Bo@Other.example'), OTHER)


class TheEngine(unittest.TestCase):
    """The engine's own words: a target that fits two accounts, and the switch line it writes to History."""

    DOC = {'order': ['a1', 'a2', 'a3'],
           'accounts': {'a1': {'email': TWIN, 'org_name': 'Northwind', 'org_id': 'org-n'},
                        'a2': {'email': TWIN, 'org_name': "maya@twin.example's Organization", 'org_id': 'org-p'},
                        'a3': {'email': OTHER, 'org_name': 'Other Co', 'org_id': 'org-o'}}}

    def test_a_target_that_fits_two_says_how_to_name_one(self):
        from switchboard.accounts import roster
        from switchboard.accounts.api import AccountsError
        with self.assertRaises(AccountsError) as got:
            roster.find_id(None, TWIN, self.DOC)
        self.assertEqual(got.exception.kind, 'ambiguous')                      # the kind scripts read stays
        self.assertEqual(str(got.exception), 'maya@twin.example fits two accounts: 1 (Northwind) and 2 (Personal). '
                                             'Give one a short name and use that: switchboard alias 2 SHORT. Its '
                                             'number works for now; it changes when the list changes.')
        self.assertEqual(roster.find_id(None, '2', self.DOC), 'a2')

    def test_the_name_every_surface_shows_finds_it(self):
        from switchboard.accounts import roster
        self.assertEqual(roster.find_id(None, 'maya@twin.example (Personal)', self.DOC), 'a2')
        self.assertEqual(roster.find_id(None, 'Maya@Twin.example (northwind)', self.DOC), 'a1')

    def test_the_engines_lines_name_it_with_its_organization(self):
        self.assertEqual(names.in_doc(self.DOC, 'a2'), OWN)
        self.assertEqual(names.in_doc(self.DOC, 'a3'), OTHER)
        one = {'order': ['a1'], 'accounts': {'a1': self.DOC['accounts']['a1']}}
        self.assertEqual(names.in_doc(one, 'a1'), TWIN)


class NothingChangesWhenEmailsDiffer(unittest.TestCase):
    """The demo's accounts all have their own email: every surface is as it was."""

    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def test_the_demos_names_are_its_emails(self):
        v = demo.view()
        emails = [a['email'] for a in v['accounts']]
        rows = [row(a['slot'], a['email'], a['org'], 'o-%d' % a['slot']) for a in v['accounts']]
        self.assertEqual(names.keys(rows), emails)
        st = core.blank_state()
        server.apply_rows(st, rows)
        self.assertEqual(sorted(st['accounts']), sorted(emails))
        text = '\n'.join(term.view(term.read(v)) + [r['email'] for r in menubar.rows(v)])
        for e in emails:
            self.assertNotIn(e + ' (', text)
        self.assertEqual([a['email'] for a in report.document(v, '0')['accounts']], emails)


class TheCommand(Cli):
    def test_an_email_that_fits_two_accounts_says_so_and_how_to_name_one(self):
        self.fake.hold(TWIN, 'org-n', org_name='Northwind')
        self.fake.hold(TWIN, 'org-p', org_name="maya@twin.example's Organization")
        self.fake.hold(OTHER, 'org-o', org_name='Other Co')
        self.fake.sign_in(OTHER, 'org-o')
        for args in ((TWIN,), ('use', TWIN), ('switch', TWIN)):
            with self.subTest(args=args):
                code, out, err = self.run_cli(*args)
                self.assertEqual(code, 'refused: maya@twin.example fits two accounts: 1 (Northwind) and 2 (Personal). '
                                       'Give one a short name and use that: switchboard alias 2 SHORT. Its number '
                                       'works for now; it changes when the list changes.')
                self.assertNotIn('ambiguous', out + err)

    def test_its_number_still_names_one(self):
        self.fake.hold(TWIN, 'org-n', org_name='Northwind')
        self.fake.hold(TWIN, 'org-p', org_name="maya@twin.example's Organization")
        self.fake.sign_in(TWIN, 'org-n')
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            view = cli.local_view()
        self.assertEqual(cli.resolve('2', view)['email'], OWN)
        self.assertEqual(cli.resolve(OWN, view)['email'], OWN)


if __name__ == '__main__':
    unittest.main()
