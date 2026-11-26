import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The rule for putting Claude Code's sign-in back (putback.decide), with no Keychain and no files: one test per
# condition, each refusing for its own stated reason, and a check over many made-up sequences from a fixed seed that
# the rule never breaks.
import random
import unittest

from switchboard.accounts import putback
from switchboard.accounts.api import ABSENT, PRESENT, UNREADABLE
from switchboard.accounts.putback import Seen, decide

NOW = 1794837600.0
X = 'uuid-x'


def gone(**kw):
    """A login lost after a switch: the item holds connector logins only, the settings name a saved account whose saved
    login is fine."""
    d = dict(live=PRESENT, login='no_login', named=X, saved='ok')
    d.update(kw)
    return Seen(**d)


class TheRule(unittest.TestCase):
    def ready(self, seen, before=None, done=()):
        """The verdict on a second look PUT_BACK_AFTER_S after the first."""
        return decide(seen, before or (X, NOW - putback.PUT_BACK_AFTER_S), list(done), NOW)

    def test_a_missing_login_seen_twice_far_enough_apart_is_put_back(self):
        for seen in (gone(), gone(live=ABSENT, login=None), gone(login='empty')):
            v = self.ready(seen)
            self.assertEqual((v.action, v.account), ('put_back', X), seen)

    def test_the_first_look_only_waits_and_asks_to_look_again_soon(self):
        v = decide(gone(), None, [], NOW)
        self.assertEqual((v.action, v.missing, v.watch), ('wait', (X, NOW), True))
        v = decide(gone(), (X, NOW - putback.PUT_BACK_AFTER_S + 0.5), [], NOW)
        self.assertEqual(v.action, 'wait')
        self.assertEqual(v.missing, (X, NOW - putback.PUT_BACK_AFTER_S + 0.5))   # the first sighting is kept

    def test_a_first_sighting_of_another_account_starts_the_wait_again(self):
        v = decide(gone(), ('uuid-other', NOW - 60), [], NOW)
        self.assertEqual((v.action, v.missing), ('wait', (X, NOW)))

    def test_a_first_sighting_dated_past_the_clock_starts_the_wait_again(self):
        v = decide(gone(), (X, NOW + 30), [], NOW)
        self.assertEqual((v.action, v.missing), ('wait', (X, NOW)))

    def test_an_account_login_is_never_written_over(self):
        for kind in ('oauth', 'wiped', 'api_key', 'unparseable'):
            v = self.ready(gone(login=kind))
            self.assertEqual(v.action, 'leave', kind)
            self.assertIsNone(v.missing)
        self.assertIn('emptied', self.ready(gone(login='wiped')).reason)
        self.assertIn('API key', self.ready(gone(login='api_key')).reason)

    def test_a_read_that_failed_or_a_fallback_copy_is_never_acted_on(self):
        v = self.ready(gone(live=UNREADABLE, login=None))
        self.assertEqual(v.action, 'leave')
        self.assertIn("can't be read", v.reason)
        self.assertEqual(v.missing, (X, NOW - putback.PUT_BACK_AFTER_S))     # a failed read does not reset the wait
        v = self.ready(gone(degraded=True))
        self.assertEqual(v.action, 'leave')
        self.assertIn('fallback copy', v.reason)

    def test_settings_that_name_nobody_are_a_chosen_sign_out(self):
        v = self.ready(gone(named=None))
        self.assertEqual(v.action, 'leave')
        self.assertIn('name no saved account', v.reason)
        self.assertIsNone(v.missing)
        self.assertFalse(v.watch)

    def test_settings_that_cannot_be_read_or_are_not_there(self):
        for kind, words in ((UNREADABLE, "settings can't be read"), (ABSENT, 'no settings file')):
            v = self.ready(gone(config=kind))
            self.assertEqual(v.action, 'leave')
            self.assertIn(words, v.reason)

    def test_a_login_in_claude_codes_login_file_is_left_to_it(self):
        v = self.ready(gone(file_login=True))
        self.assertEqual((v.action, v.key), ('leave', 'file'))
        self.assertIn('login file', v.reason)

    def test_a_switch_part_way_waits_and_looks_again(self):
        v = self.ready(gone(part_way=True))
        self.assertEqual((v.action, v.watch), ('leave', True))
        self.assertIn('switch is part-way', v.reason)

    def test_a_sign_in_under_way_on_the_page_is_never_raced(self):
        v = self.ready(gone(signing_in=True))
        self.assertEqual(v.action, 'leave')
        self.assertIn('sign-in is under way', v.reason)

    def test_each_mark_on_the_saved_login_refuses_for_its_own_reason(self):
        cases = {'missing': (dict(saved='missing'), 'is not in the Keychain'),
                 'unreadable': (dict(saved='unreadable'), "can't be read"),
                 'incomplete': (dict(saved='incomplete'), 'is incomplete'),
                 'refused': (dict(refused=True), 'refused by Anthropic'),
                 'ended': (dict(ended=True), 'end date'),
                 'conflict': (dict(conflict=True), 'another account'),
                 'unproven': (dict(unproven=True), 'not checked with Anthropic'),
                 'claimed': (dict(claimed=True), 'switchboard run'),
                 'renewing': (dict(renewing=True), 'renewed')}
        for key, (kw, words) in cases.items():
            v = self.ready(gone(**kw))
            self.assertEqual((v.action, v.key, v.account), ('leave', key, X), key)
            self.assertIn(words, v.reason, key)
            self.assertEqual(v.missing[0], X)
            self.assertFalse(v.watch, key)

    def test_four_in_an_hour_then_it_stops_until_the_hour_has_passed(self):
        long_ago = (X, NOW - 1000)
        done = [NOW - 3000, NOW - 2000, NOW - 1000, NOW - 10]
        v = self.ready(gone(), before=long_ago, done=done)
        self.assertEqual((v.action, v.key), ('limit', 'limit'))
        self.assertIn('Run /login in Claude Code once', v.reason)
        self.assertEqual(self.ready(gone(), before=long_ago, done=done[:3]).action, 'put_back')
        later = [t - 600 for t in done]                                 # the oldest is now past the hour
        self.assertEqual(self.ready(gone(), before=long_ago, done=later).action, 'put_back')

    def test_each_put_back_within_the_hour_waits_longer_than_the_one_before(self):
        self.assertEqual(putback.PUT_BACK_WAITS, (putback.PUT_BACK_AFTER_S, 15.0, 60.0, 300.0))
        done = []
        for wait in putback.PUT_BACK_WAITS:
            self.assertEqual(decide(gone(), (X, NOW - wait + 0.5), done, NOW).action, 'wait', (wait, done))
            self.assertEqual(decide(gone(), (X, NOW - wait), done, NOW).action, 'put_back', (wait, done))
            done = done + [NOW - 30]
        old = [NOW - putback.PUT_BACK_SPAN_S - 1] * 3                   # put-backs past the hour count for nothing
        self.assertEqual(decide(gone(), (X, NOW - putback.PUT_BACK_AFTER_S), old, NOW).action, 'put_back')

    def test_put_backs_dated_past_the_clock_still_count(self):
        v = self.ready(gone(), before=(X, NOW - 1000), done=[NOW + 100] * putback.PUT_BACK_MAX)
        self.assertEqual(v.action, 'limit')


class NeverBroken(unittest.TestCase):
    """Thousands of made-up looks from a fixed seed: whatever a look sees, a put-back comes only when every condition
    holds, for the account the settings name, after two sightings far enough apart, and never past the limit."""

    def test_the_rule_over_many_made_up_looks(self):
        rng = random.Random(5150)
        kinds = ('oauth', 'wiped', 'api_key', 'unparseable', 'no_login', 'empty', None)
        for _ in range(20000):
            live = rng.choice((PRESENT, PRESENT, ABSENT, UNREADABLE))
            seen = Seen(live=live, degraded=rng.random() < 0.1,
                        login=rng.choice(kinds) if live == PRESENT else None, file_login=rng.random() < 0.1,
                        config=rng.choice((PRESENT, PRESENT, PRESENT, ABSENT, UNREADABLE)),
                        named=rng.choice((X, X, 'uuid-y', None)),
                        saved=rng.choice(('ok', 'ok', 'ok', 'missing', 'unreadable', 'incomplete')),
                        refused=rng.random() < 0.1, ended=rng.random() < 0.1, conflict=rng.random() < 0.1,
                        unproven=rng.random() < 0.1, claimed=rng.random() < 0.1, renewing=rng.random() < 0.1,
                        part_way=rng.random() < 0.1, signing_in=rng.random() < 0.1)
            before = rng.choice((None, (X, NOW - rng.uniform(-10, 400)), ('uuid-y', NOW - rng.uniform(0, 400))))
            done = [NOW - rng.uniform(-50, 2 * putback.PUT_BACK_SPAN_S) for _ in range(rng.randint(0, 6))]
            v = decide(seen, before, done, NOW)
            if v.action != 'put_back':
                continue
            self.assertTrue(putback.is_missing(seen), seen)
            self.assertFalse(seen.degraded or seen.file_login or seen.part_way or seen.signing_in, seen)
            self.assertEqual((seen.config, seen.saved), (PRESENT, 'ok'), seen)
            self.assertFalse(any((seen.refused, seen.ended, seen.conflict, seen.unproven, seen.claimed,
                                  seen.renewing)), seen)
            self.assertEqual(v.account, seen.named)
            self.assertIsNotNone(before)
            self.assertEqual(before[0], seen.named)
            self.assertGreaterEqual(NOW - before[1], putback.wait_for(done, NOW))
            self.assertGreaterEqual(NOW - before[1], putback.PUT_BACK_AFTER_S)
            self.assertLess(sum(1 for t in done if NOW - t < putback.PUT_BACK_SPAN_S), putback.PUT_BACK_MAX)


if __name__ == '__main__':
    unittest.main()
