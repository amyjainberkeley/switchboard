"""Claude Code's settings naming another account than the one its login is.

A Claude Code session that was open during a switch can write the earlier account's name back into the settings a
moment later. The login says whose it is, so the account in use is still read, and the settings are made to name it
again by the pass that keeps logins, with a line in History."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import threading
import time
import unittest

from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, capture, ccfiles, context, net, roster, switcher, tokens, vault

FIXED = 'Claude Code is on b@example.com (its settings named another account; fixed)'


class Clock:
    def __init__(self):
        self.jump = 0.0

    def mono(self):
        return time.monotonic() + self.jump


class Case(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.rig = Rig(mono=self.clock.mono)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.a_text, self.b_text = self.rig.two_accounts()          # Claude Code is on B; A is an idle saved login
        capture.reset()
        tokens.WAKE.reset()
        roster._FORCED.clear()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        end = time.time() + 20
        while tokens._INFLIGHT and time.time() < end:
            time.sleep(0.05)
        self.rig.close()

    def name_a(self, **other):
        """The settings name A, as a late write by an open session leaves them; the login stays B's."""
        self.rig.set_config(identity_block('uuid-a', 'a@example.com', 'org-a'), **other)
        roster.forget_live()

    def named(self):
        return self.rig.config()['oauthAccount']['accountUuid']

    def access(self, text):
        return json.loads(text)['claudeAiOauth']['accessToken']

    def fixed_lines(self):
        return [e for e in self.rig.events if e[0] == FIXED]

    def wait(self):
        self.clock.jump += capture.LABEL_BACK_AFTER_S + 1

    def two_passes(self, force=True):
        """The pass that first sees the other name, and the one after the wait. Returns what each said."""
        first = capture.keep_pass(self.ctx, force=force)
        self.wait()
        return first, capture.keep_pass(self.ctx, force=force)


class TheAccountInUseIsStillRead(Case):
    def test_its_own_token_is_handed_over_for_its_own_numbers(self):
        self.name_a()
        self.assertTrue(api.live().disagree)
        got = api.access_token('uuid-b')
        self.assertEqual((got.kind, got.value), (api.PRESENT, self.access(self.b_text)))

    def test_the_account_the_settings_name_never_gets_the_login_in_use(self):
        self.name_a()
        got = api.access_token('uuid-a')
        self.assertEqual((got.kind, got.value), (api.PRESENT, self.access(self.rig.vault('uuid-a'))))
        self.assertNotEqual(got.value, self.access(self.b_text))

    def test_nothing_is_spent_to_read_it(self):
        self.name_a()
        before = len(self.rig.server.requests)
        self.assertEqual(api.access_token('uuid-b').kind, api.PRESENT)
        self.assertEqual(len(self.rig.server.requests), before)

    def test_an_account_first_kept_on_its_label_alone_is_not_read_while_the_label_names_another(self):
        with vault.changing(self.ctx) as doc:                          # its login was never checked with Anthropic
            doc['accounts']['uuid-b']['proof'] = {'kind': 'label', 'at': 1.0}
        self.name_a()
        got = api.access_token('uuid-b')
        self.assertEqual((got.kind, got.why), (api.UNREADABLE, 'disagree'))
        self.rig.set_config(identity_block('uuid-b', 'b@example.com', 'org-b'))
        roster.forget_live()
        self.assertEqual(api.access_token('uuid-b').kind, api.PRESENT)     # its own label: read as before


class TheSettingsArePutBack(Case):
    def test_once_they_have_named_the_other_account_for_a_moment_and_history_says_so(self):
        capture.keep_pass(self.ctx)
        self.name_a()
        self.assertEqual(self.two_passes(force=False), (None, FIXED))  # no force: the changed name is the signal
        self.assertEqual(self.named(), 'uuid-b')
        self.assertEqual(self.rig.config()['oauthAccount']['organizationUuid'], 'org-b')
        roster.forget_live()
        self.assertFalse(api.live().disagree)
        (line,) = self.fixed_lines()
        self.assertEqual((line[1], line[2].get('h'), line[2].get('account'), line[2].get('by')),
                         ('info', 'repair', 'b@example.com', 'auto'))

    def test_not_at_the_first_sight_of_it(self):
        self.name_a()
        self.assertIsNone(capture.keep_pass(self.ctx, force=True))
        self.clock.jump += capture.LABEL_BACK_AFTER_S - 5
        self.assertIsNone(capture.keep_pass(self.ctx, force=True))
        self.assertEqual(self.named(), 'uuid-a')
        self.assertEqual(self.fixed_lines(), [])

    def test_a_sign_in_under_way_is_never_written_over(self):
        self.name_a()                                                  # Claude Code wrote its settings first
        self.assertIsNone(capture.keep_pass(self.ctx, force=True))
        self.rig.set_live(self.a_text)                                 # and its login a moment later
        roster.forget_live()
        self.wait()
        capture.keep_pass(self.ctx, force=True)
        self.assertEqual(self.named(), 'uuid-a')
        self.assertEqual(self.fixed_lines(), [])

    def test_a_name_that_changed_again_waits_again(self):
        self.name_a()
        capture.keep_pass(self.ctx, force=True)
        self.wait()
        self.rig.set_config(identity_block('uuid-z', 'z@example.com', 'org-z'))
        roster.forget_live()
        self.assertIsNone(capture.keep_pass(self.ctx, force=True))
        self.assertEqual(self.named(), 'uuid-z')
        self.wait()
        self.assertEqual(capture.keep_pass(self.ctx, force=True), FIXED)
        self.assertEqual(self.named(), 'uuid-b')

    def test_a_late_write_after_two_quick_switches(self):
        api.switch('uuid-a')
        api.switch('uuid-b')
        self.assertEqual(self.named(), 'uuid-b')
        capture.reset()
        capture.keep_pass(self.ctx)
        self.name_a()                                                  # the open session's write lands now
        self.assertEqual(api.access_token('uuid-b').kind, api.PRESENT)
        self.assertEqual(self.two_passes(force=False), (None, FIXED))
        self.assertEqual(self.named(), 'uuid-b')
        self.assertEqual(api.live().account_id, 'uuid-b')

    def test_every_other_setting_is_left_as_it_was(self):
        self.name_a(numStartups=7, projects={'/tmp/x': {'allowedTools': ['Read']}})
        self.two_passes()
        cfg = self.rig.config()
        self.assertEqual(self.named(), 'uuid-b')
        self.assertEqual((cfg['numStartups'], cfg['projects']), (7, {'/tmp/x': {'allowedTools': ['Read']}}))

    def test_settings_that_already_name_it_are_not_written(self):
        capture.keep_pass(self.ctx, force=True)
        before = open(self.rig.config_path(), 'rb').read()
        for _ in range(3):
            self.two_passes()
        self.assertEqual(open(self.rig.config_path(), 'rb').read(), before)
        self.assertEqual(self.fixed_lines(), [])

    def test_a_name_no_account_here_has_is_put_back_too(self):
        self.rig.set_config(identity_block('uuid-z', 'z@example.com', 'org-z'))
        roster.forget_live()
        self.assertEqual(self.two_passes(), (None, FIXED))
        self.assertEqual(self.named(), 'uuid-b')

    def test_an_older_copy_of_the_same_sign_in_still_says_whose_it_is(self):
        self.rig.claude_renews()
        capture.keep_pass(self.ctx, force=True)                        # the newer login is saved
        self.rig.set_live(self.b_text)                                 # Claude Code holds the earlier one again
        self.name_a()
        self.assertEqual(self.two_passes(), (None, FIXED))
        self.assertEqual(self.named(), 'uuid-b')

    def test_settings_that_name_no_account_are_left_alone(self):
        self.rig.set_config(None)
        roster.forget_live()
        self.assertEqual(self.two_passes(), (None, None))
        self.assertNotIn('oauthAccount', self.rig.config())

    def test_a_login_no_account_here_has_never_moves_the_settings(self):
        self.rig.set_live(self.rig.server.add('uuid-c', 'c@example.com', 'org-c'))
        self.name_a()
        self.two_passes()
        self.assertEqual(self.named(), 'uuid-a')
        self.assertEqual(self.fixed_lines(), [])


class ItStaysOutOfTheWay(Case):
    def test_while_a_switch_is_running_the_settings_are_left_to_it(self):
        self.name_a()
        capture.keep_pass(self.ctx, force=True)
        self.wait()
        held, done = threading.Event(), threading.Event()

        def a_switch():
            with vault.switch_lock(self.ctx, timeout=5.0):
                held.set()
                done.wait(20)
        t = threading.Thread(target=a_switch)
        t.start()
        self.addCleanup(t.join, 20)
        self.addCleanup(done.set)
        self.assertTrue(held.wait(10))
        self.assertIsNone(capture.keep_pass(self.ctx, force=True))
        self.assertEqual(self.named(), 'uuid-a')
        done.set()
        t.join(20)
        self.assertEqual(capture.keep_pass(self.ctx, force=True), FIXED)   # and a later pass does it
        self.assertEqual(self.named(), 'uuid-b')

    def test_a_switch_that_was_cut_off_is_settled_first(self):
        self.name_a()
        switcher._write_journal(self.ctx, {'id': 'cut-off', 'step': 'begun'})
        self.assertEqual(self.two_passes(), (None, None))
        self.assertEqual(self.named(), 'uuid-a')

    def test_an_account_kept_on_its_label_alone_or_marked_as_another_s_is_never_written_for(self):
        for mark in ({'proof': {'kind': 'label', 'at': 1.0}},
                     {'proof': {'kind': 'profile', 'at': 1.0}, 'conflict': {'profile_uuid': 'uuid-z', 'at': 1.0}}):
            with vault.changing(self.ctx) as doc:
                doc['accounts']['uuid-b'].pop('conflict', None)
                doc['accounts']['uuid-b'].update(mark)
            capture.reset()
            self.name_a()
            self.rig.server.script.append(('profile', net.NetError('dns', False)))     # Anthropic can't be asked now
            self.two_passes()
            self.assertEqual(self.named(), 'uuid-a', mark)
        self.assertEqual(self.fixed_lines(), [])

    def test_a_saved_name_that_is_not_the_account_s_own_is_never_written(self):
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-b']['identity'] = identity_block('uuid-q', 'q@example.com', 'org-q')
        self.name_a()
        self.assertEqual(self.two_passes(), (None, None))
        self.assertEqual(self.named(), 'uuid-a')
        self.assertEqual(self.fixed_lines(), [])

    def test_a_login_that_changed_under_the_pass_is_looked_at_again_not_written_for(self):
        self.name_a()
        capture.keep_pass(self.ctx, force=True)
        self.wait()
        real = capture._locked_live
        self.addCleanup(setattr, capture, '_locked_live', real)
        capture._locked_live = lambda ctx, login: False
        self.assertIsNone(capture.keep_pass(self.ctx, force=True))
        self.assertEqual(self.named(), 'uuid-a')

    def test_a_write_that_could_not_be_made_says_nothing_and_is_made_at_a_later_look(self):
        self.name_a()
        capture.keep_pass(self.ctx)
        self.wait()
        real = ccfiles.write_identity
        self.addCleanup(setattr, ccfiles, 'write_identity', real)

        def refuse(env, home, block, expect_uuid=None):
            raise ccfiles.CCFileError('changed', 'the settings file did not keep it')
        ccfiles.write_identity = refuse
        self.assertIsNone(capture.keep_pass(self.ctx))
        reads = self.rig.kc.count('read', self.rig.service)
        for _ in range(4):
            self.assertIsNone(capture.keep_pass(self.ctx))             # no look at the Keychain on every pass
        self.assertEqual(self.rig.kc.count('read', self.rig.service), reads)
        self.assertEqual((self.named(), self.fixed_lines()), ('uuid-a', []))
        ccfiles.write_identity = real
        self.clock.jump += capture.REREAD_S + 1
        self.assertEqual(capture.keep_pass(self.ctx), FIXED)
        self.assertEqual(self.named(), 'uuid-b')

    def test_something_that_keeps_writing_another_name_wins_after_a_few_times_and_one_line_says_so(self):
        for _ in range(capture.LABEL_BACK_MAX):
            self.name_a()
            self.assertEqual(self.two_passes(), (None, FIXED))
        self.name_a()
        _, said = self.two_passes()
        self.assertIn('keep naming another account', said)
        self.assertIn('b@example.com', said)
        self.assertEqual(self.named(), 'uuid-a')
        self.assertIsNone(capture.keep_pass(self.ctx, force=True))      # said once
        self.assertEqual(len(self.fixed_lines()), capture.LABEL_BACK_MAX)
        self.assertEqual(api.access_token('uuid-b').kind, api.PRESENT)  # and its numbers are still read
        self.clock.jump += capture.LABEL_BACK_SPAN_S + 1
        self.assertEqual(capture.keep_pass(self.ctx, force=True), FIXED)
        self.assertEqual(self.named(), 'uuid-b')

    def test_a_newer_login_by_claude_code_is_still_saved_in_the_same_pass(self):
        rotated = self.rig.claude_renews()
        self.name_a()
        self.two_passes()
        self.assertEqual(self.rig.vault('uuid-b'), rotated)
        self.assertEqual(self.named(), 'uuid-b')
        self.assertEqual(len(self.fixed_lines()), 1)


if __name__ == '__main__':
    unittest.main()
