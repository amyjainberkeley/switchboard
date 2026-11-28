import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard status --json`: every account and its limits as one JSON document, for scripts and status bars. It is
# a contract: `schema` first, whole emails, times in UTC ending in Z, null for what is not known (a reading too old
# to show, as the terminal shows a dash), null for a limit the account does not have. It only looks: a login Claude
# Code is on that Switchboard does not hold is shown, never kept, and nothing but the document reaches standard
# output. The same document comes from the running page and without it. `switchboard owner LINK --json` too.
# The engine is tests/fakes_accounts_api.py; the page, when it runs, is test_cli_with_page's stand-in.
import json
import os
import sys
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import artifacts, browser, core, engine
from switchboard.accounts import api
from fakes_accounts_api import account_id
from test_cli_commands import Cli, A, B, C
from test_cli_with_page import FakePage

H = 3600.0
Z = '%Y-%m-%dT%H:%M:%SZ'

# The shape, pinned. A change that removes or renames one of these keys must raise "schema" in switchboard/report.py
# (and say so in docs/commands.md); adding a key does not, and adds it here.
TOP_KEYS = {'schema', 'version', 'read_at', 'in_use', 'next', 'auto_switch', 'accounts', 'this_folder'}
AUTO_KEYS = {'on', 'order', 'five_hour_line', 'week_line', 'model_line', 'models'}
ACCOUNT_KEYS = {'number', 'email', 'short_name', 'plan', 'organization', 'in_use', 'next', 'needs_sign_in',
                'in_switchboard', 'sign_in_ends_by', 'limits', 'checked_at', 'from_another_mac', 'kind', 'label',
                'added_at', 'one_year_added_at'}
LIMITS_KEYS = {'five_hour', 'week', 'models', 'spend'}
LIMIT_KEYS = {'used_percent', 'refills_at'}
WEEK_KEYS = LIMIT_KEYS | {'pace'}
PACE_KEYS = {'even_percent', 'ahead', 'full_at'}
OWNER_KEYS = {'schema', 'link', 'owner', 'how', 'open_with'}


def utc(t):
    """What the document writes for a time: UTC, to the nearest second, ending in Z."""
    return datetime.fromtimestamp(int(t + 0.5), timezone.utc).strftime(Z)


def on_the_hour(t):
    return round(t / H) * H


class StatusJson(Cli):
    def doc(self, *more):
        """`switchboard status --json`: exit 0, nothing on stderr, and standard output is one JSON document."""
        code, out, err = self.run_cli('status', '--json', *more)
        self.assertEqual((code, err), (0, ''), out)
        return json.loads(out), out

    def three(self):
        """Three accounts in the person's order; Claude Code on the first. Returns their ids."""
        ids = (self.fake.hold(A, 'org-w', org_name='Northwind', alias='work'), self.fake.hold(B), self.fake.hold(C))
        self.fake.sign_in(A, 'org-w')
        return ids

    def auto_running(self):
        """Auto-switch on, and its loop running and beating: what the page sees while it switches."""
        self.fake.auto['enabled'] = True
        real = engine.HOST.status
        p = mock.patch.object(engine.HOST, 'status', lambda wanted=None: dict(real(wanted), running=True, on=True))
        p.start()
        self.addCleanup(p.stop)

    def account(self, doc, email):
        return next(a for a in doc['accounts'] if a['email'] == email)


class TheDocument(StatusJson):
    def test_it_parses_has_schema_1_and_the_accounts_in_order_by_whole_email(self):
        self.three()
        doc, out = self.doc()
        self.assertEqual(doc['schema'], 1)
        self.assertTrue(out.startswith('{\n "schema": 1,'), out[:40])      # schema first, json.dumps(indent=1)
        self.assertEqual(doc['version'], core.VERSION)
        self.assertEqual([(a['number'], a['email']) for a in doc['accounts']], [(1, A), (2, B), (3, C)])
        self.assertEqual(doc['in_use'], A)
        self.assertEqual([a['in_use'] for a in doc['accounts']], [True, False, False])
        self.assertTrue(all(a['in_switchboard'] for a in doc['accounts']))
        work = self.account(doc, A)
        self.assertEqual((work['short_name'], work['plan'], work['organization']), ('work', 'Max 20x', 'Northwind'))
        self.assertIsNone(self.account(doc, B)['short_name'])
        self.assertRegex(doc['read_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')

    def test_numbers_their_refills_and_when_each_was_checked(self):
        self.three()
        now = time.time()
        # the week refills exactly 50 hours from now, not on the hour: its pace (where an even week is now, 118 of 168
        # hours, 70%) must not depend on the minute the test runs at (rounded to the hour it read 71 at some minutes)
        five, week, fable = on_the_hour(now + 2 * H), int(now) + 50 * H, on_the_hour(now + 50 * H)
        self.fake.reading(A, five=(41.4, five), week=(75.5, week), models={'Fable': (89, fable)}, at=now - 30)
        doc, _ = self.doc()
        lim = self.account(doc, A)['limits']
        self.assertEqual(lim['five_hour'], {'used_percent': 41, 'refills_at': utc(five)})
        pace = lim['week'].pop('pace')
        self.assertEqual(lim['week'], {'used_percent': 76, 'refills_at': utc(week)})     # halves go up, as on the page
        self.assertEqual((pace['even_percent'], pace['ahead']), (70, True))             # 76% used, 118 hours in
        self.assertEqual(lim['models'], [{'name': 'Fable', 'used_percent': 89, 'refills_at': utc(fable)}])
        self.assertEqual(self.account(doc, A)['checked_at'], utc(now - 30))

    def test_a_reading_too_old_to_show_has_null_percents_never_a_number(self):
        self.three()
        now = time.time()
        self.fake.reading(B, five=(12, now + H), week=(30, now + 9 * H), at=now - 2 * H)     # over an hour old
        self.fake.reading(A, five=(20, now + H), week=(40, now + 9 * H), at=now - 60)
        doc, _ = self.doc()
        blank = {'used_percent': None, 'refills_at': None}
        self.assertEqual(self.account(doc, B)['limits'], {'five_hour': blank, 'week': dict(blank, pace=None),
                                                          'models': [], 'spend': None})
        self.assertEqual(self.account(doc, B)['checked_at'], utc(now - 2 * H))       # how old it is, still said
        self.assertEqual(self.account(doc, A)['limits']['week']['used_percent'], 40)
        never = self.account(doc, C)                                                 # never read: not known either
        self.assertEqual((never['limits']['five_hour'], never['checked_at']), (blank, None))

    def test_a_plan_with_no_five_hour_limit_has_five_hour_null(self):
        self.three()
        now = time.time()
        self.fake.reading(A, week=(55, now + 9 * H), at=now - 60)
        doc, _ = self.doc()
        lim = self.account(doc, A)['limits']
        self.assertIsNone(lim['five_hour'])
        self.assertEqual(lim['week']['used_percent'], 55)

    def test_a_sign_in_needed_and_when_a_sign_in_ends(self):
        ends = time.time() + 5 * 24 * H
        self.fake.hold(A, 'org-w', refresh_expires_at=ends)
        self.fake.hold(B, status='needs_login')
        self.fake.sign_in(A, 'org-w')
        doc, _ = self.doc()
        self.assertEqual([a['needs_sign_in'] for a in doc['accounts']], [False, True])
        self.assertEqual(self.account(doc, A)['sign_in_ends_by'], utc(ends))
        self.assertIsNone(self.account(doc, B)['sign_in_ends_by'])

    def test_the_next_account_is_marked_only_while_auto_switch_runs(self):
        self.three()
        now = time.time()
        for who, week in ((A, 50), (B, 20), (C, 60)):
            self.fake.reading(who, five=(10, now + H), week=(week, now + (9 if who == B else 30) * H), at=now - 30)
        doc, _ = self.doc()                                                 # auto-switch off
        self.assertFalse(doc['auto_switch']['on'])
        self.assertIsNone(doc['next'])
        self.assertEqual([a['next'] for a in doc['accounts']], [False, False, False])
        self.auto_running()
        doc, _ = self.doc()
        self.assertTrue(doc['auto_switch']['on'])
        self.assertEqual(doc['auto_switch'], {'on': True, 'order': 'refills-first', 'five_hour_line': 90,
                                              'week_line': 95, 'model_line': 95, 'models': []})
        self.assertEqual(doc['next'], B)
        self.assertEqual([a['next'] for a in doc['accounts']], [False, True, False])

    def test_a_shape_pin_of_every_key(self):
        self.three()
        self.fake.reading(A, five=(10, time.time() + H), week=(20, time.time() + 9 * H), models={'Fable': (5, None)})
        doc, _ = self.doc()
        self.assertEqual(set(doc), TOP_KEYS)
        self.assertEqual(list(doc)[0], 'schema')
        self.assertEqual(set(doc['auto_switch']), AUTO_KEYS)
        for a in doc['accounts']:
            self.assertEqual(set(a), ACCOUNT_KEYS)
            self.assertEqual(set(a['limits']), LIMITS_KEYS)
            self.assertEqual(set(a['limits']['five_hour']), LIMIT_KEYS)
            self.assertEqual(set(a['limits']['week']), WEEK_KEYS)
        self.assertEqual(set(self.account(doc, A)['limits']['week']['pace']), PACE_KEYS)
        self.assertEqual(set(self.account(doc, A)['limits']['models'][0]), LIMIT_KEYS | {'name'})


class ItOnlyLooks(StatusJson):
    def snapshot(self):
        """Every file in the data folder and Claude Code's config, with its bytes."""
        out = {}
        for root in (os.environ['SWITCHBOARD_HOME'], os.path.dirname(core.claude_json())):
            for folder, _, names in os.walk(root):
                for n in names:
                    path = os.path.join(folder, n)
                    with open(path, 'rb') as f:
                        out[path] = f.read()
        return out

    def test_a_login_not_held_is_shown_never_kept_and_plain_status_keeps_it(self):
        self.fake.hold(A, 'org-w')
        self.fake.sign_in(C, 'org-ivy')
        before, saved = self.snapshot(), engine.saved_identities()
        doc, out = self.doc()
        self.assertNotIn('Kept', out)
        self.assertNotIn('Reading the accounts', out)
        self.assertEqual(engine.saved_identities(), saved)
        self.assertNotIn((C, 'org-ivy'), engine.saved_identities())
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn(('add_current',), self.fake.calls)
        self.assertEqual(doc['in_use'], C)
        on = self.account(doc, C)
        self.assertEqual((on['in_use'], on['in_switchboard'], on['number']), (True, False, None))
        self.assertTrue(self.account(doc, A)['in_switchboard'])
        code, out, _ = self.run_cli('status')                       # the same state, without --json: kept
        self.assertEqual(code, 0)
        self.assertIn('Kept %s' % C, out)
        self.assertIn((C, 'org-ivy'), engine.saved_identities())

    def test_no_login_token_fingerprint_or_id_is_in_it(self):
        ids = self.three()
        self.fake.hold('dee@lab.example', 'org-lab', status='needs_login')
        self.fake.sign_in('eve@away.example', 'org-away')            # Claude Code on a login not held
        self.fake.reading(A, five=(10, time.time() + H), week=(20, time.time() + 9 * H))
        _, out = self.doc()
        secrets = [v for r in self.fake.saved for v in (r['id'], r['fingerprint'], r['org_id']) if v]
        secrets += [account_id('eve@away.example', 'org-away'), 'org-away']
        secrets += [v for v in self.fake.block().values() if v and '@' not in v]     # the identity block's ids
        # 4 held: 4 ids, 4 fingerprints, 2 organizations; the login not held: its id and organization, as the fake
        # names them and as Claude Code's config does
        self.assertEqual(len(secrets), 14, secrets)
        for value in secrets:
            with self.subTest(value=value):
                self.assertNotIn(value, out)
        for word in ('acct-', 'fp-', 'org-', 'token', 'fingerprint', 'organization_id', 'account_id'):
            self.assertNotIn(word, out)
        self.assertTrue(ids)


class PageOrNot(StatusJson):
    def test_the_same_document_with_the_page_running_and_without(self):
        self.three()
        now = time.time()
        self.fake.reading(A, five=(30, now + H), week=(60, now + 9 * H), at=now - 30)
        self.fake.reading(B, week=(10, now + 30 * H), at=now - 2 * H)
        view = cli.local_view()                     # the page's own state, as the running page would answer
        without, _ = self.doc()
        page = FakePage(self, {('GET', '/api/state'): (200, view)})
        with mock.patch.object(cli, 'local_view', lambda: self.fail('the running page was not asked')):
            got, _ = self.doc()
        self.assertEqual(page.posted, [])
        self.assertEqual(got['read_at'], utc(view['now']))
        self.assertEqual(dict(got, read_at=None), dict(without, read_at=None))

    def test_the_demo_gives_its_made_up_accounts(self):
        core.DEMO = True
        doc, _ = self.doc()
        self.assertEqual(doc['schema'], 1)
        self.assertEqual([a['email'] for a in doc['accounts']],
                         ['maya@northwind.example', 'maya.reyes@mail.example', 'maya@fernhill.example',
                          'm.reyes@harbor.example', 'maya@ivy.example'])
        self.assertEqual(doc['in_use'], 'maya@northwind.example')
        personal = self.account(doc, 'maya.reyes@mail.example')
        self.assertIsNone(personal['limits']['five_hour'])                  # its plan has no 5-hour limit
        self.assertTrue(personal['needs_sign_in'])
        self.assertEqual(self.account(doc, 'maya@ivy.example')['limits']['models'][0]['name'], 'Fable')
        self.assertEqual(self.account(doc, 'maya@northwind.example')['plan'], 'Team')

    def test_another_providers_rows_are_not_accounts_here(self):
        # The document is the Claude accounts, as before the list had other providers' rows: a running page's state
        # carries them ('provider_rows'), and none becomes an account or the next one.
        self.three()
        from test_one_list import codex_row
        view = dict(cli.local_view(), provider_rows=[codex_row(email='ana@made-up.example', next=True)])
        FakePage(self, {('GET', '/api/state'): (200, view)})
        doc, _ = self.doc()
        self.assertNotIn('ana@made-up.example', [a['email'] for a in doc['accounts']])
        self.assertEqual(len(doc['accounts']), len(view['accounts']))
        self.assertNotEqual(doc['next'], 'ana@made-up.example')

    def test_with_another_providers_row_next_the_next_is_auto_switchs_own(self):
        # A page that marked another provider's row next sends 'next' None and keeps automatic switching's own next
        # account in 'claude_next': the document names that account, as scripts read it.
        self.three()
        now = time.time()
        for who, week in ((A, 50), (B, 20), (C, 60)):
            self.fake.reading(who, five=(10, now + H), week=(week, now + (9 if who == B else 30) * H), at=now - 30)
        self.auto_running()
        from test_one_list import codex_row
        view = dict(cli.local_view(), next=None, claude_next=B,
                    provider_rows=[codex_row(email='ana@made-up.example', next=True)])
        FakePage(self, {('GET', '/api/state'): (200, view)})
        doc, _ = self.doc()
        self.assertEqual(doc['next'], B)
        self.assertEqual([a['next'] for a in doc['accounts']], [False, True, False])

    def test_a_reading_too_old_to_show_still_knows_the_plan_has_no_five_hour_limit(self):
        self.three()
        now = time.time()
        self.fake.reading(B, week=(30, now + 9 * H), at=now - 26 * H)             # a day old, no 5-hour window
        doc, _ = self.doc()
        lim = self.account(doc, B)['limits']
        self.assertIsNone(lim['five_hour'])
        self.assertEqual(lim['week'], {'used_percent': None, 'refills_at': None, 'pace': None})

    def test_a_reading_too_old_to_show_with_a_five_hour_window_keeps_both_nulls(self):
        self.three()
        now = time.time()
        self.fake.reading(B, five=(12, now + H), week=(30, now + 9 * H), at=now - 26 * H)
        doc, _ = self.doc()
        self.assertEqual(self.account(doc, B)['limits']['five_hour'], {'used_percent': None, 'refills_at': None})


class HelpAndErrors(StatusJson):
    def test_help_after_it_prints_the_commands_lines(self):
        for args in (('status', '--json', '--help'), ('status', '--help'), ('status', '-h', '--json')):
            with self.subTest(args=args):
                code, out, err = self.run_cli(*args)
                self.assertEqual((code, err), (0, ''))
                self.assertEqual(out, cli.command_help('status') + '\n')
                self.assertTrue(out.startswith('switchboard status [--json]\n'), out)
                self.assertIn('--json', out)

    def test_accounts_that_cannot_be_read_say_so_on_stderr_as_usage_does(self):
        self.three()
        self.fake.fail['accounts'] = api.AccountsError('keychain_locked', "macOS's Keychain is locked: unlock the Mac.")
        code, out, _ = self.run_cli('status', '--json')
        self.assertEqual(code, "refused: macOS's Keychain is locked: unlock the Mac.")
        self.assertEqual(out, '')


class OwnerJson(StatusJson):
    ID = 'JsonArtifactId00000001'

    def setUp(self):
        super().setUp()
        p = mock.patch.object(browser, 'profiles', lambda: {})
        p.start()
        self.addCleanup(p.stop)

    def test_an_owner_known_and_how(self):
        self.three()
        with core.locked() as st:
            st['artifacts'].append(dict(artifacts.blank_item(self.ID, time.time()), owner=A, src='seen'))
        link = 'https://claude.ai/artifact/%s' % self.ID
        code, out, err = self.run_cli('owner', link, '--json')
        self.assertEqual((code, err), (0, ''), out)
        doc = json.loads(out)
        self.assertEqual(set(doc), OWNER_KEYS)
        self.assertEqual(doc, {'schema': 1, 'link': link, 'owner': A, 'how': 'seen',
                               'open_with': 'switchboard open %s' % link})

    def test_an_owner_not_known_exits_1_with_owner_null(self):
        link = 'https://claude.ai/artifact/%s' % self.ID
        code, out, _ = self.run_cli('owner', '--json', link)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out), {'schema': 1, 'link': link, 'owner': None, 'how': None,
                                           'open_with': 'switchboard open %s' % link})

    def test_owner_help_names_json(self):
        code, out, _ = self.run_cli('owner', '--json', '--help')
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('switchboard owner LINK [--json]\n'), out)


if __name__ == '__main__':
    unittest.main()
