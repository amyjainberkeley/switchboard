"""A one-year token or an API key kept as an account (accounts/bearer.py): kept in the Keychain only, told apart by
its shape, never put on by auto-switch, switched to by hand inside the switch transaction, and forgotten on removal.
Made-up tokens and accounts only; the Keychain and Anthropic are the suite's stand-ins."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import unittest

from fakes_accounts import Rig
from switchboard.accounts import api, bearer, context, live, roster, switcher, vault
from switchboard.accounts.api import AccountsError

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0001-aaaaaaaaaaaa'
TOKEN2 = 'sk-ant-oat01-madeUpOneYearToken-0002-bbbbbbbbbbbb'
KEY = 'sk-ant-api03-madeUpConsoleKey-0003-cccccccccccccc'
SECRETS = (TOKEN, TOKEN2, KEY)


class Case(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.a_text, self.b_text = self.rig.two_accounts()          # Claude Code is on B
        self.asked = len(self.rig.server.requests)

    def tearDown(self):
        switcher._HOOK = None
        self.rig.close()

    def records(self):
        return bearer.records(self.rig.doc())

    def assert_no_secret_anywhere(self):
        """No made-up token or key in a file under the made-up Mac (but Claude Code's own login file), a log line,
        an event, or any Keychain item but its own and Claude Code's login."""
        for folder, _, names in os.walk(self.rig.root):
            for name in names:
                path = os.path.join(folder, name)
                if name == '.credentials.json':
                    continue
                with open(path, 'rb') as f:
                    data = f.read()
                for s in SECRETS:
                    self.assertNotIn(s.encode(), data, path)
        for line in self.rig.logs + [json.dumps(e) for e in self.rig.events]:
            for s in SECRETS:
                self.assertNotIn(s, line)
        for (service, account), value in self.rig.sec.items().items():
            if service in bearer.SERVICES.values() or (service, account) == (self.rig.service, self.rig.account):
                continue
            for s in SECRETS:
                self.assertNotIn(s, value, service)

    def assert_nothing_asked(self):
        self.assertEqual(len(self.rig.server.requests), self.asked)


class Adding(Case):
    def test_a_token_for_a_kept_account_is_kept_in_the_keychain_and_nowhere_else(self):
        rec, what = bearer.add(self.ctx, TOKEN + '\n', for_id='uuid-a')
        self.assertEqual(what, 'added')
        self.assertEqual((rec['kind'], rec['for'], rec['label'], rec['rule'], rec['last4']),
                         ('token', 'uuid-a', None, 'out', TOKEN[-4:]))
        self.assertTrue(rec['id'].startswith('token-'))
        self.assertEqual(self.rig.sec.get('Switchboard token', rec['id']), TOKEN)
        self.assertEqual(list(self.records()), [rec['id']])
        self.assertEqual(self.rig.events[-1][0], 'Kept a one-year token for a@example.com')
        self.assert_no_secret_anywhere()
        self.assert_nothing_asked()

    def test_an_api_key_with_a_label_of_its_own(self):
        rec, what = bearer.add(self.ctx, KEY, label='Build-Box', alias='bb')
        self.assertEqual((what, rec['kind'], rec['label'], rec['alias'], rec['for']), ('added', 'key', 'build-box',
                                                                                         'bb', None))
        self.assertEqual(self.rig.sec.get('Switchboard API key', rec['id']), KEY)
        self.assertEqual(bearer.find_id(self.rig.doc(), 'BB'), rec['id'])
        self.assertEqual(bearer.find_id(self.rig.doc(), 'build-box'), rec['id'])
        self.assert_no_secret_anywhere()
        self.assert_nothing_asked()

    def test_anything_else_is_refused_and_never_repeated(self):
        for text in ('', 'hello', 'sk-ant-ort01-madeUpRefreshToken-0004-dddd', self.a_text, 'sk-ant-oat01-short',
                     'sk-ant-api03-has a space in it 0000000000', 'sk-ant-oat01-' + 'x' * 600):
            with self.subTest(text=text[:16]), self.assertRaises(AccountsError) as e:
                bearer.add(self.ctx, text, label='x')
            self.assertEqual(str(e.exception), bearer.REFUSED)
        self.assertEqual(self.records(), {})
        self.assertEqual(self.rig.kc.count('write', 'Switchboard token') + self.rig.kc.count('write', 'Switchboard API '
                                                                                                       'key'), 0)

    def test_the_shapes(self):
        self.assertEqual([bearer.kind_of(x) for x in (TOKEN, KEY, ' %s\n' % TOKEN, 'sk-ant-ort01-' + 'a' * 30)],
                         ['token', 'key', 'token', None])

    def test_a_key_is_not_for_an_account_and_each_needs_a_name(self):
        with self.assertRaises(AccountsError) as e:
            bearer.add(self.ctx, KEY, for_id='uuid-a')
        self.assertEqual(str(e.exception), bearer.KEY_FOR)
        with self.assertRaises(AccountsError) as e:
            bearer.add(self.ctx, TOKEN)
        self.assertEqual(str(e.exception), bearer.NAME_IT)
        for word in ('12', '-x', 'two words', 'x' * 41):
            with self.subTest(word=word), self.assertRaises(AccountsError) as e:
                bearer.add(self.ctx, TOKEN, label=word)
            self.assertEqual(str(e.exception), bearer.NAME_WORDS)
        self.assertEqual(self.records(), {})

    def test_a_name_already_taken_is_refused(self):
        bearer.add(self.ctx, KEY, label='ci')
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-a']['alias'] = 'work'
        for kw, who in (({'label': 'ci'}, 'the name of ci'), ({'label': 'work'}, 'the name of a@example.com'),
                        ({'label': 'b@example.com'}, None), ({'for_id': 'uuid-a', 'alias': 'ci'}, 'the name of ci')):
            with self.subTest(kw=kw), self.assertRaises(AccountsError) as e:
                bearer.add(self.ctx, TOKEN, **kw)
            if who:
                self.assertIn(who, str(e.exception))
        self.assertEqual(len(self.records()), 1)

    def test_safe_to_run_twice_and_a_new_token_for_an_account_takes_the_old_ones_place(self):
        rec, _ = bearer.add(self.ctx, TOKEN, for_id='uuid-a')
        again, what = bearer.add(self.ctx, TOKEN, for_id='uuid-a')
        self.assertEqual((what, again['id']), ('same', rec['id']))
        new, what = bearer.add(self.ctx, TOKEN2, for_id='uuid-a')
        self.assertEqual((what, new['id']), ('replaced', rec['id']))
        self.assertEqual(self.rig.sec.get('Switchboard token', rec['id']), TOKEN2)
        self.assertEqual(len(self.records()), 1)

    def test_a_keychain_that_does_not_keep_it_keeps_nothing(self):
        for why in ('denied', 'silent', 'timeout'):
            with self.subTest(why=why):
                self.rig.kc.rule('write', 'Switchboard token', why=why)
                with self.assertRaises(AccountsError):
                    bearer.add(self.ctx, TOKEN, for_id='uuid-a')
                self.assertEqual(self.records(), {})
                self.assertEqual([k for k in self.rig.sec.items() if k[0] == 'Switchboard token'], [])

    def test_a_list_that_cannot_be_written_takes_the_item_out_again(self):
        from unittest import mock
        with mock.patch.object(vault, 'save', side_effect=OSError(28, 'full')), self.assertRaises(AccountsError):
            bearer.add(self.ctx, TOKEN, for_id='uuid-a')
        self.assertEqual([k for k in self.rig.sec.items() if k[0] == 'Switchboard token'], [])


class Rules(Case):
    def test_each_starts_kept_out_a_token_may_be_used_last_a_key_never(self):
        tok, _ = bearer.add(self.ctx, TOKEN, label='spare')
        key, _ = bearer.add(self.ctx, KEY, label='ci')
        self.assertEqual([bearer.rule_of(self.records()[x['id']]) for x in (tok, key)], ['out', 'out'])
        bearer.set_rule(self.ctx, tok['id'], 'last')
        self.assertEqual(bearer.rule_of(self.records()[tok['id']]), 'last')
        with self.assertRaises(AccountsError) as e:
            bearer.set_rule(self.ctx, tok['id'], 'in')
        self.assertEqual(str(e.exception), bearer.refusal('token', 'in'))
        for rule in ('in', 'last'):
            with self.subTest(rule=rule), self.assertRaises(AccountsError) as e:
                bearer.set_rule(self.ctx, key['id'], rule)
            self.assertEqual(str(e.exception), 'An API key is billed per token by Anthropic, so auto-switch never puts '
                                               'Claude Code on one: it stays kept out.')
        bearer.set_rule(self.ctx, key['id'], 'out')
        self.assertEqual(bearer.rule_of(self.records()[key['id']]), 'out')

    def test_a_hand_edited_list_never_gives_a_key_a_rule(self):
        key, _ = bearer.add(self.ctx, KEY, label='ci')
        for rule in ('in', 'last', 'LAST', None, 7):
            with self.subTest(rule=rule):
                with vault.changing(self.ctx) as doc:
                    doc['bearers'][key['id']]['rule'] = rule
                self.assertEqual(bearer.rule_of(self.records()[key['id']]), 'out')
                self.assertEqual(bearer.view(self.rig.doc())[0]['rule'], 'out')

    def test_auto_switch_never_sees_one_as_an_account(self):
        bearer.add(self.ctx, TOKEN, label='spare')
        bearer.add(self.ctx, KEY, label='ci')
        with context.use(self.ctx):
            self.assertEqual(sorted(a.id for a in api.accounts()), ['uuid-a', 'uuid-b'])


class RulesHeldEverywhere(Case):
    def test_a_hand_edited_auto_switch_settings_file_never_reaches_a_key_or_a_token(self):
        from switchboard import core
        from switchboard.accounts import autoloop
        key, _ = bearer.add(self.ctx, KEY, label='ci')
        tok, _ = bearer.add(self.ctx, TOKEN, label='spare')
        before = core.settings().get(autoloop.POLICY_KEY)
        core.set_setting(autoloop.POLICY_KEY, {'rules': {key['id']: 'last', tok['id']: 'last'}})
        try:
            with context.use(self.ctx):
                snap = autoloop.gather()
                self.assertEqual(sorted(a.id for a in snap.accounts), ['uuid-a', 'uuid-b'])
                for bid in (key['id'], tok['id']):
                    with self.subTest(bid=bid), self.assertRaises(AccountsError):
                        api.set_auto(rule=(bid, 'last'))
            self.assertEqual([x['rule'] for x in bearer.view(self.rig.doc())], ['out', 'out'])
        finally:
            core.set_setting(autoloop.POLICY_KEY, before)

    def test_many_made_up_records_never_give_a_key_a_rule_and_never_change_the_accounts(self):
        import random
        rnd = random.Random(20261125)
        plain = vault.list_accounts(self.ctx)
        junk = ['in', 'last', 'out', 'LAST', '', None, 0, 1, True, [], {}, 'use']
        for n in range(2000):
            recs = {}
            for i in range(rnd.randint(0, 4)):
                kind = rnd.choice(['token', 'key', 'other', None])
                bid = rnd.choice(['%s-%04d' % (kind, i), 'uuid-a', 'x%d' % i])
                recs[bid] = {'id': bid if rnd.random() < 0.9 else 'else', 'kind': kind, 'rule': rnd.choice(junk),
                             'for': rnd.choice([None, 'uuid-a', 'gone', 3]), 'label': rnd.choice([None, 'w%d' % i, 7, ['x']]),
                             'alias': rnd.choice([None, 'a%d' % i, {}]), 'last4': rnd.choice([None, 'abcd', 5]),
                             'added_at': rnd.choice([None, 1.7e9, 'x', -1]), 'fp': rnd.choice([None, 'f%d' % i])}
            doc = vault.blank()
            doc['accounts'] = self.rig.doc()['accounts']
            doc['bearers'] = recs if rnd.random() < 0.95 else rnd.choice([[], 'x', None])
            for rec in bearer.records(doc).values():
                rule = bearer.rule_of(rec)
                self.assertIn(rule, bearer.RULES[rec['kind']], (n, rec))
                if rec['kind'] == 'key':
                    self.assertEqual(rule, 'out')
            for rec in bearer.records(doc).values():
                bearer.find_id(doc, 'w1')
            for row in bearer.view(doc):
                self.assertTrue(isinstance(row['name'], str) and isinstance(row['last4'], str))
                self.assertTrue(row['rule'] == 'out' or (row['kind'] == 'token' and row['rule'] == 'last'))
                self.assertNotIn('fp', row)
            with vault.account_lock(self.ctx):
                vault.save(self.ctx, doc)
            self.assertEqual([a.id for a in vault.list_accounts(self.ctx)], [a.id for a in plain])

    def test_the_uninstallers_all_takes_every_one_out(self):
        tok, _ = bearer.add(self.ctx, TOKEN, for_id='uuid-a')
        key, _ = bearer.add(self.ctx, KEY, label='ci')
        with context.use(self.ctx):
            said = api.remove_all()
        self.assertIn('One-year tokens and API keys kept as accounts: 2 removed.', said)
        self.assertEqual(self.records(), {})
        self.assertEqual([k for k in self.rig.sec.items() if k[0] in bearer.SERVICES.values()], [])


class Removing(Case):
    def test_removing_forgets_it_and_its_item(self):
        rec, _ = bearer.add(self.ctx, KEY, label='ci')
        with context.use(self.ctx):
            self.assertEqual(api.remove_token('ci'), 'ci')
        self.assertEqual(self.records(), {})
        self.assertIsNone(self.rig.sec.get('Switchboard API key', rec['id']))

    def test_a_keychain_that_refuses_keeps_the_record(self):
        rec, _ = bearer.add(self.ctx, KEY, label='ci')
        self.rig.kc.rule('delete', 'Switchboard API key', why='denied')
        with self.assertRaises(AccountsError):
            bearer.remove(self.ctx, rec['id'])
        self.assertIn(rec['id'], self.records())

    def test_removing_a_kept_account_forgets_its_token(self):
        rec, _ = bearer.add(self.ctx, TOKEN, for_id='uuid-a')
        other, _ = bearer.add(self.ctx, KEY, label='ci')
        with context.use(self.ctx):
            api.remove('a@example.com')
        self.assertEqual(list(self.records()), [other['id']])
        self.assertIsNone(self.rig.sec.get('Switchboard token', rec['id']))


class Switching(Case):
    def token_for_a(self):
        rec, _ = bearer.add(self.ctx, TOKEN, for_id='uuid-a', alias='a-token')
        return rec

    def label(self):
        return self.rig.config().get('oauthAccount')

    def test_onto_a_token_and_back(self):
        rec = self.token_for_a()
        res = switcher.switch(self.ctx, rec['id'], source='cli')
        self.assertTrue(res.ok)
        got = live.parse(self.rig.live_text())
        self.assertEqual((got.access_token, got.has_refresh, got.expires_ms, got.scopes),
                         (TOKEN, False, None, ('user:inference',)))
        self.assertEqual(self.label()['accountUuid'], 'uuid-a')
        self.assertEqual(self.rig.vault('uuid-b'), self.b_text)          # the sign-in left is kept whole
        self.assertEqual(vault.stashed(self.ctx), [])
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
        self.assertEqual(self.rig.events[-1][0], 'Switched Claude Code to the one-year token for a@example.com. On a '
                                                 'one-year token, claude.ai connectors and Remote Control do not '
                                                 'work; model requests do.')
        self.assertEqual(res.note, bearer.AFTER_SWITCH['token'])
        roster.forget_live(self.ctx)
        lv = roster.live_view(self.ctx)
        self.assertEqual((lv.state, lv.account_id, lv.bearer), ('other', None, rec['id']))
        res = switcher.switch(self.ctx, 'uuid-b', source='cli')
        self.assertTrue(res.ok)
        self.assertEqual(self.rig.live_text(), self.b_text)              # the sign-in, whole, as it was kept
        self.assertEqual(self.label()['accountUuid'], 'uuid-b')
        self.assertEqual(vault.stashed(self.ctx), [])                     # the token is in its own item only
        self.assertEqual(res.from_id, rec['id'])
        self.assert_no_secret_anywhere()
        self.assert_nothing_asked()                                       # the token was never sent anywhere

    def test_a_token_of_its_own_takes_the_account_label_out_and_back(self):
        rec, _ = bearer.add(self.ctx, TOKEN, label='spare')
        self.assertTrue(switcher.switch(self.ctx, rec['id'], source='cli').ok)
        self.assertIsNone(self.label())
        self.assertTrue(switcher.switch(self.ctx, 'uuid-a', source='cli').ok)
        self.assertEqual(self.label()['accountUuid'], 'uuid-a')
        self.assertEqual(live.parse(self.rig.live_text()).fingerprint, live.parse(self.a_text).fingerprint)

    def test_switching_to_the_token_in_use_changes_nothing(self):
        rec = self.token_for_a()
        switcher.switch(self.ctx, rec['id'], source='cli')
        writes = self.rig.kc.count('write', self.rig.service)
        res = switcher.switch(self.ctx, rec['id'], source='cli')
        self.assertTrue(res.ok)
        self.assertEqual(self.rig.kc.count('write', self.rig.service), writes)

    def test_an_api_key_is_never_put_in_the_login(self):
        rec, _ = bearer.add(self.ctx, KEY, label='ci')
        with self.assertRaises(AccountsError) as e:
            switcher.switch(self.ctx, rec['id'], source='cli')
        self.assertEqual(str(e.exception), bearer.KEY_SWITCH % 'ci')
        self.assertEqual(self.rig.live_text(), self.b_text)

    def test_a_token_gone_from_the_keychain_changes_nothing(self):
        rec = self.token_for_a()
        self.rig.sec.drop('Switchboard token', rec['id'])
        with self.assertRaises(AccountsError) as e:
            switcher.switch(self.ctx, rec['id'], source='cli')
        self.assertEqual(e.exception.kind, 'dead_login')
        self.assertEqual(self.rig.live_text(), self.b_text)

    def test_a_failure_at_each_step_is_undone(self):
        rec = self.token_for_a()
        label_before = self.label()
        # the login item refuses the write: nothing changed
        self.rig.kc.rule('write', self.rig.service, why='denied')
        with self.assertRaises(AccountsError):
            switcher.switch(self.ctx, rec['id'], source='cli')
        self.assertEqual((self.rig.live_text(), self.label()), (self.b_text, label_before))
        # the login item takes it but reads back different: put back
        self.rig.kc.rule('write', self.rig.service, why='silent')
        res = switcher.switch(self.ctx, rec['id'], source='cli')
        self.assertFalse(res.ok)
        self.assertEqual((self.rig.live_text(), self.label()), (self.b_text, label_before))
        # Claude Code's settings can't be written: the login goes back
        from unittest import mock
        from switchboard.accounts import ccfiles
        with mock.patch.object(ccfiles, 'write_identity', side_effect=ccfiles.CCFileError('changed', 'no')):
            res = switcher.switch(self.ctx, rec['id'], source='cli')
        self.assertTrue(res.rolled_back)
        self.assertEqual((self.rig.live_text(), self.label()), (self.b_text, label_before))
        self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
        self.assert_no_secret_anywhere()          # (a safety copy of the sign-in in use stays, as for any undone switch)

    def test_a_switch_cut_off_onto_a_token_is_finished_or_undone_from_what_the_login_holds(self):
        rec = self.token_for_a()
        for step, ends_on_token in (('live_saved', False), ('credential_written', True)):
            with self.subTest(step=step):
                def stop(name, step=step):
                    if name == step:
                        raise KeyboardInterrupt()
                switcher._HOOK = stop
                with self.assertRaises(KeyboardInterrupt):
                    switcher.switch(self.ctx, rec['id'], source='cli')
                switcher._HOOK = None
                self.assertTrue(os.path.exists(switcher.journal_path(self.ctx)))
                self.assertTrue(switcher.resolve_journal(self.ctx))
                self.assertFalse(os.path.exists(switcher.journal_path(self.ctx)))
                on = live.parse(self.rig.live_text())
                self.assertEqual(on.access_token == TOKEN, ends_on_token)
                self.assertEqual(self.label()['accountUuid'], 'uuid-a' if ends_on_token else 'uuid-b')
                if ends_on_token:
                    switcher.switch(self.ctx, 'uuid-b', source='cli')
                self.assertEqual(self.rig.live_text(), self.b_text)

    def test_auto_switch_stays_while_claude_code_is_on_a_token(self):
        from switchboard.accounts import autoloop
        rec = self.token_for_a()
        switcher.switch(self.ctx, rec['id'], source='cli')
        roster.forget_live(self.ctx)
        active, ok, why, _, _, refused = autoloop.interpret_live(roster.live_view(self.ctx), None, 'uuid-b')
        self.assertEqual((active, ok, refused), (None, False, False))
        self.assertIn('one-year token', why)

    def test_the_usage_reader_never_gets_the_token(self):
        rec = self.token_for_a()
        switcher.switch(self.ctx, rec['id'], source='cli')
        roster.forget_live(self.ctx)
        for aid in ('uuid-a', 'uuid-b'):
            got = roster.access_token(self.ctx, aid)
            self.assertNotEqual(got.value, TOKEN)


if __name__ == '__main__':
    unittest.main()
