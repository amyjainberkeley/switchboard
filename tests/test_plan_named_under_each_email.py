import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Under each email, the plan in the words people know it by ("Max 20x", "Team"), on a real install too. A login
# names its plan in lower case ('max', 'team') and says nothing of the tier; Claude Code's own settings, kept with
# each account, hold the tier and the kind of organization. Made-up accounts only.
import os
import shutil
import unittest

from fakes_accounts import Rig, identity_block
from fakes_accounts_api import install
from switchboard import core, engine, server, term
from switchboard.accounts import roster, vault

A, B = 'ana@work.example', 'ana@home.example'
OWN = "ana@work.example's Organization"


class WhatTheEngineKeeps(unittest.TestCase):
    """The tier and the kind of organization come from the settings block kept with the account: read, never
    written, and nothing when it does not say."""

    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx

    def tearDown(self):
        self.rig.close()

    def keep(self, uuid, email, org, **said):
        self.rig.sign_in(uuid, email, org)
        self.rig.set_config(dict(identity_block(uuid, email, org), **said))
        roster.add_current(self.ctx)
        return next(a for a in roster.accounts(self.ctx) if a.email == email)

    def test_an_account_says_its_tier_and_its_kind_of_organization(self):
        a = self.keep('uuid-a', 'a@example.com', 'org-a', organizationType='claude_max',
                      organizationRateLimitTier='default_claude_max_20x')
        self.assertEqual((a.plan, a.tier, a.org_type), ('max', 'default_claude_max_20x', 'claude_max'))
        b = self.keep('uuid-b', 'b@example.com', 'org-b', organizationType='claude_team',
                      organizationRateLimitTier='default_raven', userRateLimitTier='default_claude_max_5x')
        self.assertEqual((b.tier, b.org_type), ('default_raven', 'claude_team'))

    def test_settings_that_do_not_say_give_nothing(self):
        a = self.keep('uuid-a', 'a@example.com', 'org-a')
        self.assertEqual((a.plan, a.tier, a.org_type), ('max', None, None))

    def test_a_damaged_block_gives_nothing_and_never_raises(self):
        self.keep('uuid-a', 'a@example.com', 'org-a', organizationType='claude_max',
                  organizationRateLimitTier='default_claude_max_20x')
        for block in ([], 'x', 7, None, {'organizationType': 5, 'organizationRateLimitTier': {}},
                      {'organizationType': '  ', 'organizationRateLimitTier': ['default_claude_max_20x']}):
            with self.subTest(block=block):
                doc = vault.load(self.ctx)
                doc['accounts']['uuid-a']['identity'] = block
                a = vault.to_account(self.ctx, doc, 'uuid-a')
                self.assertEqual((a.plan, a.tier, a.org_type), ('max', None, None))


class WhatTheRowCarries(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(core.data_dir(), ignore_errors=True)
        os.makedirs(core.data_dir())
        if os.path.exists(core.claude_json()):
            os.remove(core.claude_json())
        self.fake = install(self)

    def test_each_row_has_the_tier_and_the_kind_of_organization(self):
        self.fake.hold(A, 'org-w', plan='max', org_name=OWN, tier='default_claude_max_20x', org_type='claude_max')
        self.fake.hold(B, 'org-h', plan='pro')
        rows = engine.listing()['rows']
        self.assertEqual([(r['plan'], r['tier'], r['org_type']) for r in rows],
                         [('max', 'default_claude_max_20x', 'claude_max'), ('pro', None, None)])
        self.assertEqual((engine._row()['tier'], engine._row()['org_type']), (None, None))

    def test_the_state_follows_the_rows(self):
        self.fake.hold(A, 'org-w', plan='max', org_name=OWN, tier='default_claude_max_20x', org_type='claude_max')
        st = core.blank_state()
        server.apply_rows(st, engine.listing()['rows'], trust_active=False)
        a = st['accounts'][A]
        self.assertEqual((a['tier'], a['org_type'], server.plan_line(a)),
                         ('default_claude_max_20x', 'claude_max', 'Max 20x · Personal'))
        # the account moved to another plan: the tier it had is not kept
        rec = self.fake.by_id[a['id']]
        rec.update(plan='pro', tier='default_claude_ai', org_type='claude_pro')
        server.apply_rows(st, engine.listing()['rows'], trust_active=False)
        self.assertEqual(server.plan_line(st['accounts'][A]), 'Pro · Personal')
        rec.update(tier=None, org_type=None)                 # a row that knows neither clears both
        server.apply_rows(st, engine.listing()['rows'], trust_active=False)
        self.assertEqual((st['accounts'][A]['tier'], st['accounts'][A]['org_type']), (None, None))


CASES = (
    # what a real login and its settings say                                               what is written
    (dict(plan='max', tier='default_claude_max_20x', org_type='claude_max', org=OWN), 'Max 20x · Personal'),
    (dict(plan='max', tier='default_claude_max_5x', org_type='claude_max', org=OWN), 'Max 5x · Personal'),
    (dict(plan='max', org=OWN), 'Max · Personal'),                       # no tier known: the plan, with its capital
    (dict(plan='team', tier='default_raven', org_type='claude_team', org='Northwind'), 'Team · Northwind'),
    (dict(plan='team', org='Northwind'), 'Team · Northwind'),
    (dict(plan='enterprise', org_type='claude_enterprise', org='Northwind'), 'Enterprise · Northwind'),
    (dict(plan='pro', tier='default_claude_ai', org_type='claude_pro', org=OWN), 'Pro · Personal'),
    (dict(plan='ultra', org=OWN), 'Max 20x · Personal'),                 # the newer name's own word
    (dict(plan='free'), 'Free'),
    (dict(plan='someday'), 'Someday'),                                   # a word not known yet still gets its capital
    (dict(plan=None, org_type='claude_team', org='Northwind'), 'Team · Northwind'),
    # words that are already a name are left as they are
    (dict(plan='Max 5x', org=OWN), 'Max 5x · Personal'),
    (dict(plan='Ultra'), 'Max 20x'),
    (dict(plan='Team Premium'), 'Team Premium'),
    (dict(plan='max 20x'), 'max 20x'),                                   # not one word: not a login's own word
    (dict(), ''),
)


class TheWords(unittest.TestCase):
    def test_the_page_names_the_plan_the_way_people_know_it(self):
        for a, words in CASES:
            with self.subTest(account=a):
                self.assertEqual(server.plan_line(a), words)

    def test_the_terminal_works_out_the_same_words(self):
        for a, words in CASES:
            with self.subTest(account=a):
                self.assertEqual(term.plan_words(a), words)


if __name__ == '__main__':
    unittest.main()
