import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A login left only in Claude Code's plaintext file is put back in the Keychain after a second look ("promote"), and
# the wait before a put-back follows the evidence. What Claude Code does (2.1.329): when a save of its Keychain item is
# refused it writes the whole object to ~/.claude/.credentials.json and deletes the item; it then works from that file,
# and one more unlucky save removes the file too. A chosen sign-out never writes that file. So a fresh write of it (the
# sign-in watch saw it, or a look finds it written lately), or a put-back undone in an episode that saw one, is
# evidence of the fault: the login is put back after two sightings 0.5 s apart, as often as it is undone, within the
# limits of an episode.
# The rule with no I/O (one test per condition, each for its own stated reason, and a check over many made-up looks),
# then the account engine on its stand-ins (fakes_accounts) with a fake clock. Made-up *.example accounts.
import json
import os
import random
import re
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig
from switchboard import core, notify
from switchboard.accounts import api, autoloop, context, live, putback, vault
from switchboard.accounts.api import ABSENT, PRESENT, UNREADABLE
from switchboard.accounts.putback import Seen, decide

NOW = 1794837600.0
X = 'uuid-x'
MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}
NOTIFY_PUT_BACK = autoloop.notify_put_back          # the loop's own, before a test replaces it


def gone(**kw):
    """The shape the fault leaves: an item with connector logins only; the settings name a saved account whose login
    is fine."""
    d = dict(live=PRESENT, login='no_login', named=X, saved='ok')
    d.update(kw)
    return Seen(**d)


def filed(**kw):
    """Claude Code's fallback: no item, the account login in its plaintext file, proven to be the named account's."""
    d = dict(live=PRESENT, login='oauth', from_file=True, full=True, owner=True, file_there=True, named=X, saved='ok')
    d.update(kw)
    return Seen(**d)


def episode(start, writes, last=None):
    return {'start': start, 'writes': writes, 'last': start if last is None else last, 'said': False}


class ThePromoteRule(unittest.TestCase):
    def test_the_first_look_that_finds_it_waits_and_the_next_one_promotes(self):
        v = decide(filed(), None, [], NOW)
        self.assertEqual((v.action, v.account, v.watch, v.missing), ('wait', X, True, (X, NOW)))
        v = decide(filed(), (X, NOW - putback.PUT_BACK_AFTER_S), [], NOW)
        self.assertEqual((v.action, v.account, v.watch), ('promote', X, True))

    def test_a_keychain_that_did_not_answer_is_never_acted_on(self):
        self.assertEqual(decide(filed(live=UNREADABLE, login=None), None, [], NOW).action, 'leave')
        v = decide(filed(degraded=True), None, [], NOW)      # the file read only because the Keychain failed
        self.assertEqual(v.action, 'leave')
        self.assertIn('fallback copy', v.reason)

    def test_a_login_in_the_file_without_both_tokens_is_left(self):
        v = decide(filed(full=False), None, [], NOW)
        self.assertEqual(v.action, 'leave')
        self.assertIn('incomplete', v.reason)

    def test_settings_that_name_nobody_or_cannot_be_read_are_left(self):
        v = decide(filed(named=None), None, [], NOW)
        self.assertEqual(v.action, 'leave')
        self.assertIn('name no saved account', v.reason)
        self.assertIn("can't be read", decide(filed(config=UNREADABLE, named=None), None, [], NOW).reason)
        self.assertIn('no settings file', decide(filed(config=ABSENT, named=None), None, [], NOW).reason)

    def test_a_login_not_proven_to_be_the_named_accounts_newest_is_left_and_said(self):
        v = decide(filed(owner=False), None, [], NOW)
        self.assertEqual((v.action, v.key), ('leave', 'file_owner'))

    def test_an_older_generation_of_the_named_account_brings_its_usable_saved_copy_back(self):
        self.assertEqual(decide(filed(owner=False, older=True), (X, NOW - putback.PUT_BACK_AFTER_S), [], NOW).action,
                         'promote')
        for mark in ('refused', 'ended', 'conflict', 'unproven', 'claimed', 'renewing'):
            v = decide(filed(owner=False, older=True, **{mark: True}), None, [], NOW)
            self.assertEqual((v.action, v.key), ('leave', 'file_owner'), mark)
        for state in ('missing', 'unreadable', 'incomplete'):
            self.assertEqual(decide(filed(owner=False, older=True, saved=state), None, [], NOW).action, 'leave')

    def test_a_switch_part_way_or_a_sign_in_on_the_page_is_never_raced(self):
        v = decide(filed(part_way=True), None, [], NOW)
        self.assertEqual((v.action, v.watch), ('leave', True))
        self.assertIn('part-way', v.reason)
        v = decide(filed(signing_in=True), None, [], NOW)
        self.assertEqual(v.action, 'leave')
        self.assertIn('sign-in is under way', v.reason)

    def test_past_the_limits_claude_code_is_left_in_its_fallback(self):
        full = episode(NOW - 20, putback.EPISODE_WRITES)
        v = decide(filed(), None, [], NOW, episode=full, episodes=[NOW - 20])
        self.assertEqual((v.action, v.key), ('leave', 'file_limit'))
        self.assertEqual(decide(filed(), (X, NOW - putback.PUT_BACK_AFTER_S), [], NOW,
                                episode=episode(NOW - 20, putback.EPISODE_WRITES - 1), episodes=[NOW - 20]).action,
                         'promote')
        four = [NOW - 3000, NOW - 2000, NOW - 1000, NOW - 600]
        v = decide(filed(), None, [], NOW, episode=episode(NOW - 600, 1), episodes=four)
        self.assertEqual((v.action, v.key), ('leave', 'file_limit'))           # a fifth episode in the hour
        self.assertEqual(decide(filed(), (X, NOW - putback.PUT_BACK_AFTER_S), [], NOW, episode=episode(NOW - 600, 1),
                                episodes=four[1:]).action, 'promote')

    def test_a_file_that_holds_no_account_login_follows_the_put_back_rule(self):
        for kind in ('wiped', 'api_key', 'unparseable'):
            self.assertEqual(decide(filed(login=kind), (X, NOW - 60), [], NOW).action, 'leave', kind)
        self.assertEqual(decide(filed(login='no_login'), (X, NOW - 60), [], NOW).action, 'put_back')

    def test_the_put_back_rule_ignores_the_from_file_fields_without_an_account_login(self):
        self.assertEqual(decide(gone(from_file=True, owner=True, full=True), (X, NOW - 60), [], NOW).action,
                         'put_back')


class TheWaitFollowsTheEvidence(unittest.TestCase):
    def test_with_evidence_two_sightings_half_a_second_apart_are_enough(self):
        self.assertEqual(putback.FAULT_AFTER_S, 0.5)
        v = decide(gone(), None, [], NOW, fault=True)
        self.assertEqual((v.action, v.missing, v.watch), ('wait', (X, NOW), True))      # never on one look
        self.assertEqual(decide(gone(), (X, NOW - 0.4), [], NOW, fault=True).action, 'wait')
        self.assertEqual(decide(gone(), (X, NOW - 0.5), [], NOW, fault=True).action, 'put_back')

    def test_without_evidence_round_35s_wait_stays(self):
        self.assertEqual(decide(gone(), (X, NOW - 0.5), [], NOW).action, 'wait')
        self.assertEqual(decide(gone(), (X, NOW - putback.PUT_BACK_AFTER_S), [], NOW).action, 'put_back')
        self.assertEqual(decide(gone(), (X, NOW - 5), [NOW - 30], NOW).action, 'wait')      # 15 s after one undone

    def test_with_evidence_it_is_put_back_as_often_as_it_is_undone_within_an_episode(self):
        done = [NOW - 50, NOW - 40, NOW - 30, NOW - 20, NOW - 10]       # past the four an hour of a loss without proof
        ep = episode(NOW - 50, 5, last=NOW - 1)
        v = decide(gone(), (X, NOW - 0.5), done, NOW, fault=True, episode=ep, episodes=[NOW - 50])
        self.assertEqual(v.action, 'put_back')

    def test_twelve_writes_in_an_episode_is_the_limit(self):
        ep = episode(NOW - 100, putback.EPISODE_WRITES, last=NOW - 1)
        v = decide(gone(), (X, NOW - 0.5), [], NOW, fault=True, episode=ep, episodes=[NOW - 100])
        self.assertEqual((v.action, v.key, v.reason), ('limit', 'limit', putback.LIMIT_WORDS))
        ep = episode(NOW - 100, putback.EPISODE_WRITES - 1, last=NOW - 1)
        self.assertEqual(decide(gone(), (X, NOW - 0.5), [], NOW, fault=True, episode=ep,
                                episodes=[NOW - 100]).action, 'put_back')

    def test_four_episodes_an_hour_is_the_limit(self):
        starts = [NOW - 3000, NOW - 2000, NOW - 1000, NOW - 400]
        v = decide(gone(), (X, NOW - 0.5), [], NOW, fault=True, episode=episode(NOW - 400, 3), episodes=starts)
        self.assertEqual(v.action, 'limit')
        self.assertEqual(decide(gone(), (X, NOW - 0.5), [], NOW, fault=True, episode=episode(NOW - 400, 3),
                                episodes=[t - 700 for t in starts]).action, 'put_back')    # the oldest past the hour

    def test_evidence_never_puts_back_over_a_sign_out_a_mark_or_a_login_in_the_file(self):
        self.assertEqual(decide(gone(named=None), (X, NOW - 1), [], NOW, fault=True).action, 'leave')
        self.assertEqual(decide(gone(refused=True), (X, NOW - 1), [], NOW, fault=True).action, 'leave')
        self.assertEqual(decide(gone(file_login=True), (X, NOW - 1), [], NOW, fault=True).action, 'leave')
        self.assertEqual(decide(gone(part_way=True), (X, NOW - 1), [], NOW, fault=True).action, 'leave')
        self.assertEqual(decide(gone(login='oauth'), (X, NOW - 1), [], NOW, fault=True).action, 'leave')

    def test_an_episode_ends_after_a_quiet_minute_or_five_minutes(self):
        self.assertTrue(putback.episode_open(episode(NOW - 100, 3, last=NOW - 59), NOW))
        self.assertFalse(putback.episode_open(episode(NOW - 100, 3, last=NOW - 60), NOW))
        self.assertFalse(putback.episode_open(episode(NOW - 300, 3, last=NOW - 1), NOW))
        self.assertFalse(putback.episode_open(episode(NOW + 30, 3, last=NOW + 30), NOW))   # dated past the clock
        self.assertFalse(putback.episode_open(None, NOW))
        self.assertEqual((putback.EPISODE_QUIET_S, putback.EPISODE_MAX_S), (60.0, 300.0))

    def test_evidence_is_ten_seconds_old_at_most(self):
        self.assertTrue(putback.fault({'fault_at': NOW - 9.9}, NOW))
        self.assertFalse(putback.fault({'fault_at': NOW - 10}, NOW))
        self.assertFalse(putback.fault({'fault_at': NOW + 5}, NOW))
        self.assertFalse(putback.fault({'fault_at': None}, NOW))


class NeverBroken(unittest.TestCase):
    """Many made-up looks from a fixed seed: a promote comes only when every condition of item 4 holds, and a put-back
    with evidence only after two sightings FAULT_AFTER_S apart and within the limits."""

    def test_the_rule_over_many_made_up_looks(self):
        rng = random.Random(3604)
        kinds = ('oauth', 'oauth', 'wiped', 'api_key', 'no_login', 'empty', None)
        for _ in range(20000):
            live_kind = rng.choice((PRESENT, PRESENT, PRESENT, ABSENT, UNREADABLE))
            login = rng.choice(kinds) if live_kind == PRESENT else None
            seen = Seen(live=live_kind, degraded=rng.random() < 0.1, login=login, file_login=rng.random() < 0.1,
                        config=rng.choice((PRESENT, PRESENT, PRESENT, ABSENT, UNREADABLE)),
                        named=rng.choice((X, X, 'uuid-y', None)),
                        saved=rng.choice(('ok', 'ok', 'missing', 'unreadable', 'incomplete')),
                        refused=rng.random() < 0.1, part_way=rng.random() < 0.1, signing_in=rng.random() < 0.1,
                        from_file=live_kind == PRESENT and rng.random() < 0.5, full=rng.random() < 0.8,
                        owner=rng.random() < 0.7, older=rng.random() < 0.3, file_there=rng.random() < 0.5,
                        ended=rng.random() < 0.1, conflict=rng.random() < 0.1)
            before = rng.choice((None, (X, NOW - rng.uniform(-2, 30)), ('uuid-y', NOW - rng.uniform(0, 30))))
            done = [NOW - rng.uniform(-50, 4000) for _ in range(rng.randint(0, 8))]
            starts = [NOW - rng.uniform(-50, 4000) for _ in range(rng.randint(0, 6))]
            ep = rng.choice((None, episode(NOW - rng.uniform(-10, 400), rng.randint(1, 14),
                                           last=NOW - rng.uniform(-5, 90))))
            fault = rng.random() < 0.5
            v = decide(seen, before, done, NOW, fault=fault, episode=ep, episodes=starts)
            if v.action == 'promote':
                self.assertTrue(seen.from_file and seen.login == 'oauth' and seen.full, seen)
                self.assertTrue(seen.owner or (seen.older and seen.saved == 'ok' and not (
                    seen.refused or seen.ended or seen.conflict)), seen)
                self.assertEqual((seen.live, seen.degraded, seen.config), (PRESENT, False, PRESENT), seen)
                self.assertFalse(seen.part_way or seen.signing_in, seen)
                self.assertEqual(v.account, seen.named)
                self.assertTrue(putback.room(ep, starts, NOW))
            if v.action == 'put_back':
                self.assertTrue(putback.is_missing(seen), seen)
                self.assertFalse(seen.from_file and seen.login == 'oauth')
                self.assertFalse(seen.degraded or seen.part_way or seen.signing_in, seen)
                if seen.file_login:                     # only beside a login in the file proven the named account's
                    self.assertTrue(seen.full and (seen.owner or (seen.older and seen.saved == 'ok')), seen)
                self.assertEqual(v.account, seen.named)
                self.assertEqual(before[0], seen.named)
                self.assertGreaterEqual(NOW - before[1], putback.FAULT_AFTER_S)
                if fault:
                    self.assertTrue(putback.room(ep, starts, NOW))
                else:
                    self.assertGreaterEqual(NOW - before[1], putback.PUT_BACK_AFTER_S)
                    self.assertLess(sum(1 for t in done if NOW - t < putback.PUT_BACK_SPAN_S), putback.PUT_BACK_MAX)


# ---------- the account engine ----------

class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='fault-mode-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        putback._KEPT.clear()
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)                 # two sign-ins never share an end stamp to the millisecond
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)   # Claude Code is on B
        self.saved_b = self.rig.vault('uuid-b')
        self.told = []
        patcher = mock.patch.object(autoloop, 'notify_put_back', self.told.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.use.__exit__(None, None, None)
        putback.reset()
        putback._KEPT.clear()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def cred(self):
        return os.path.join(self.rig.home, '.claude', '.credentials.json')

    def fallback(self, text=None):
        """Claude Code's fallback after a refused save: its whole object to the plaintext file, then the item
        deleted."""
        text = self.rig.live_text() if text is None else text
        with open(self.cred, 'w') as f:
            f.write(text)
        os.chmod(self.cred, 0o600)
        os.utime(self.cred, (self.clock.wall(), self.clock.wall()))      # written now, by the fake clock
        self.rig.sec.drop(self.rig.service, self.rig.account)
        return text

    def lose_login(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))

    def watch(self, after=0.0, **hint):
        self.clock.advance(after)
        h = {'keychain': True, 'login_file': None, 'settings': False, 'tick': False}
        h.update(hint)
        return api.watch_look(h)

    def live_writes(self):
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)

    def repairs(self):
        return [e for e in self.rig.events if e[2].get('h') == 'repair']

    def has_login(self):
        text = self.rig.live_text()
        return bool(text) and live.parse(text).kind == 'oauth'


class ThePromote(Base):
    def test_a_login_left_only_in_the_file_is_back_in_the_keychain_at_the_second_look(self):
        text = self.fallback()
        self.assertEqual(self.watch(login_file='appeared').action, 'wait')
        res = self.watch(putback.FAULT_AFTER_S)
        self.assertEqual((res.action, res.account), ('promote', 'ana@home.example'), res.reason)
        self.assertEqual(self.rig.live_text(), text)                       # the file's own bytes, connectors included
        self.assertEqual(json.loads(self.rig.live_text())['mcpOAuth'], MCP['mcpOAuth'])
        self.assertEqual(self.rig.kc.created, [(self.rig.service, self.rig.account)])     # create only
        with open(self.cred) as f:                                         # the file stays as it is for now (a process
            self.assertEqual(f.read(), text)                               # told "no such item" may be on its way to it)
        self.assertEqual(self.rig.vault('uuid-b'), self.saved_b)          # the same generation: saved copy unchanged
        self.assertEqual(len(self.repairs()), 1)
        text_, kind, facts_ = self.repairs()[0]
        self.assertEqual(text_, putback.PROMOTE_LINE)
        self.assertEqual(putback.PROMOTE_LINE, 'Claude Code kept its sign-in only in its plain login file. '
                                               'Switchboard put it back in the Keychain.')
        self.assertEqual((kind, facts_.get('account'), facts_.get('by')), ('info', 'ana@home.example', 'auto'))
        self.assertEqual(self.told, [])                                    # no notification: nothing was lost
        self.assertEqual(res.next_s, putback.AFTER_WRITE_NEXT_S)
        self.assertEqual(api.last_put_back()['how'], 'promote')
        self.assertEqual(api.live().account_id, 'uuid-b')

    def test_a_newer_login_in_the_file_also_brings_the_saved_copy_up(self):
        self.clock.advance(600)
        newer = self.rig.server.refresh_for(self.rig.live_text())            # Claude Code renewed, then fell back
        self.fallback(newer)
        self.watch()
        res = self.watch(putback.FAULT_AFTER_S)
        self.assertEqual(res.action, 'promote', res.reason)
        self.assertEqual(self.rig.live_text(), newer)
        self.assertEqual(live.parse(self.rig.vault('uuid-b')).refresh_token, live.parse(newer).refresh_token)

    def test_said_once_per_episode(self):
        for n in range(3):
            self.fallback()
            self.assertEqual(self.watch(2.0).action, 'wait')
            self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'promote')
            self.assertEqual(self.watch(0.5).action, 'leave')
        self.assertEqual(len(self.repairs()), 1)
        self.clock.advance(putback.EPISODE_MAX_S)                            # a new episode: said again
        self.fallback()
        self.assertEqual(self.watch().action, 'wait')
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'promote')
        self.assertEqual(len(self.repairs()), 2)

    def test_an_item_made_first_by_another_process_is_never_written_over(self):
        self.fallback()
        other = self.rig.server.refresh_for(self.b_text)
        self.watch()

        def first(verb, service, account):
            if verb == 'write' and service == self.rig.service and self.rig.live_text() is None:
                self.rig.set_live(other)                    # a Claude Code save lands just before ours
        self.rig.kc.on_call = first
        res = self.watch(putback.FAULT_AFTER_S)
        self.rig.kc.on_call = None
        # no write over it; the renewal it holds is a round trip newer than the saved login, so the keep step saves it
        self.assertEqual(res.action, 'kept', res.reason)
        self.assertEqual(self.rig.live_text(), other)
        self.assertTrue(os.path.exists(self.cred))                          # Claude Code's file is Claude Code's
        self.assertEqual(self.rig.vault('uuid-b'), vault.bound_text(other))

    def test_a_file_that_changes_before_the_locked_read_is_not_promoted_from_the_old_bytes(self):
        self.fallback()
        real = putback.see
        newer = self.rig.server.refresh_for(self.b_text)

        def see(ctx, now, interactive=False, **kw):
            got = real(ctx, now, interactive, **kw)
            if interactive:                                 # the look under the locks: Claude Code wrote meanwhile
                with open(self.cred, 'w') as f:
                    f.write(newer)
            return got
        with mock.patch.object(putback, 'see', see):
            res = self.watch()
        self.assertNotEqual(res.action, 'promote')
        self.assertIsNone(self.rig.live_text())

    def test_another_accounts_login_in_the_file_is_left_and_said_once(self):
        self.fallback(self.rig.vault('uuid-a'))                            # the settings still name B
        res = [self.watch(1.0) for _ in range(4)]
        self.assertEqual([r.action for r in res], ['leave'] * 4)
        said = [r.line for r in res if r.line]
        self.assertEqual(len(said), 1, said)
        self.assertIn("can't tell that it is the newest login of ana@home.example", said[0])
        self.assertEqual(self.live_writes(), 0)

    def test_a_login_in_the_file_older_than_the_saved_copy_brings_the_saved_copy_back_not_the_file(self):
        old = self.b_text
        self.clock.advance(600)
        newer = self.rig.server.refresh_for(old)
        self.rig.set_live(newer)
        self.assertIn('Saved the newer login', api.keep_pass())               # the saved copy is the newer one
        stale = json.dumps(dict(json.loads(old), discoveryState={'made-up': 4}), separators=(',', ':'))
        self.fallback(stale)                                # a process wrote what it read before the renewal
        self.watch()
        res = self.watch(putback.FAULT_AFTER_S)
        self.assertEqual(res.action, 'promote', res.reason)
        written = json.loads(self.rig.live_text())
        self.assertEqual(written['claudeAiOauth'], json.loads(newer)['claudeAiOauth'])     # never the used-up one
        self.assertEqual(written['mcpOAuth'], MCP['mcpOAuth'])
        self.assertEqual(self.rig.kc.created, [(self.rig.service, self.rig.account)])
        with open(self.cred) as f:                              # the file no longer holds the used-up login: a
            self.assertEqual(json.loads(f.read()), written)     # process on its way to it finds the one now in the item
        self.watch(putback.HOLD_TIDY_S)                         # and a later look takes the file away
        self.assertFalse(os.path.exists(self.cred))
        self.assertEqual(live.parse(self.rig.vault('uuid-b')).refresh_token, live.parse(newer).refresh_token)

    def test_an_older_login_in_the_file_with_a_saved_copy_that_cannot_be_used_is_left(self):
        old = self.b_text
        self.clock.advance(600)
        self.rig.set_live(self.rig.server.refresh_for(old))
        api.keep_pass()
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-b']['conflict'] = {'profile_uuid': 'uuid-z', 'at': self.clock.wall()}
        self.fallback(old)
        res = [self.watch(1.0) for _ in range(3)]
        self.assertEqual([r.action for r in res], ['leave'] * 3)
        self.assertEqual(self.live_writes(), 0)

    def test_settings_that_name_nobody_are_left(self):
        self.fallback()
        self.rig.set_config(None)
        self.assertEqual(self.watch().action, 'leave')
        self.assertEqual(self.live_writes(), 0)

    def test_a_switch_part_way_or_a_page_sign_in_waits(self):
        self.fallback()
        with mock.patch.object(putback.switcher, 'read_journal', lambda ctx: (api.Read(PRESENT), {})):
            self.assertEqual(self.watch().action, 'leave')
        with mock.patch.object(putback, '_signing_in', lambda ctx, now: True):
            self.assertEqual(self.watch().action, 'leave')
        self.assertEqual(self.live_writes(), 0)
        self.assertEqual(self.watch().action, 'wait')
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'promote')

    def test_while_claude_code_holds_its_save_lock_nothing_is_written(self):
        self.fallback()
        self.watch()
        lock = os.path.join(self.rig.home, '.claude', '.storage-write.lock')
        os.mkdir(lock)
        try:
            res = self.watch(putback.FAULT_AFTER_S)
        finally:
            os.rmdir(lock)
        self.assertEqual(res.action, 'wait')                                # a held lock is not a failed write
        self.assertIn('saving its login', res.reason)
        self.assertEqual(res.next_s, putback.FAULT_AFTER_S)
        self.assertEqual(self.live_writes(), 0)
        self.assertEqual(self.watch(1.0).action, 'promote')

    def test_past_the_limits_it_is_left_in_the_file_and_said_once(self):
        for n in range(putback.EPISODE_WRITES):
            self.fallback()
            self.assertEqual(self.watch(1.0).action, 'wait', n)
            self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'promote', n)
        writes = self.live_writes()
        self.fallback()
        res = [self.watch(1.0) for _ in range(5)]
        self.assertEqual([r.action for r in res], ['leave'] * 5)
        said = [r.line for r in res if r.line]
        self.assertEqual(len(said), 1, said)
        self.assertIn('leaves it in the file for now', said[0])
        self.assertEqual(self.live_writes(), writes)
        self.assertTrue(os.path.exists(self.cred))


class ThePutBackWithEvidence(Base):
    def test_the_file_appearing_makes_the_put_back_come_after_half_a_second(self):
        self.lose_login()
        first = self.watch(login_file='appeared')
        self.assertEqual((first.action, first.next_s), ('wait', putback.FAULT_AFTER_S))
        res = self.watch(putback.FAULT_AFTER_S, keychain=False)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertTrue(self.has_login())
        self.assertEqual(self.told, [res])

    def test_without_evidence_it_waits_four_seconds(self):
        self.lose_login()
        first = self.watch()
        self.assertEqual((first.action, first.next_s), ('wait', putback.PUT_BACK_AFTER_S))
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'wait')
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')

    def test_a_put_back_undone_is_made_again_at_once_up_to_twelve_then_the_limit_line_once(self):
        landed = []
        for n in range(putback.EPISODE_WRITES):
            self.lose_login()
            res = self.watch(2.0, login_file='appeared' if n == 0 else None)
            if res.action == 'wait':
                res = self.watch(putback.FAULT_AFTER_S)
            self.assertEqual(res.action, 'put_back', (n, res.reason))
            landed.append(res)
        self.assertEqual(len({r.at for r in landed}), 1)                    # one episode: one notification
        self.lose_login()
        res = [self.watch(1.0) for _ in range(5)]
        self.assertEqual([r.action for r in res], ['limit'] * 5)
        self.assertEqual([r.line for r in res if r.line], [putback.LIMIT_WORDS])
        self.assertEqual(self.live_writes(), putback.EPISODE_WRITES)

    def test_one_notification_per_episode_from_the_watch_and_the_loop_in_the_same_minute(self):
        shown = []
        core.set_setting('notify', True)
        with mock.patch.object(autoloop, 'notify_put_back', NOTIFY_PUT_BACK), \
                mock.patch.object(notify, '_show', lambda title, body, run: shown.append(body) or 0):
            self.lose_login()
            self.watch(login_file='appeared')
            first = self.watch(putback.FAULT_AFTER_S)
            self.assertEqual(first.action, 'put_back')
            self.clock.advance(5.0)
            self.lose_login()                                              # undone again; the loop's pass finds it
            api.put_back_pass()
            self.clock.advance(putback.FAULT_AFTER_S)
            second = api.put_back_pass()
            self.assertEqual(second.action, 'put_back', second.reason)
            autoloop.notify_put_back(second)                               # what the loop's stage does with it
        self.assertEqual(first.at, second.at)
        self.assertEqual(len(shown), 1, shown)

    def test_next_s_after_a_put_back_follows_the_timed_looks_then_none(self):
        self.lose_login()
        self.watch(login_file='appeared')
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).next_s, putback.AFTER_WRITE_NEXT_S)
        self.assertEqual(self.watch(10.0).next_s, 5.0)                      # the last timed look, 15 s after
        self.assertIsNone(self.watch(putback.AFTER_WRITE_S).next_s)

    def test_seen_holds_plain_facts_only(self):
        self.lose_login()
        res = self.watch(login_file='appeared')
        self.assertEqual(res.seen, {'live': 'present', 'login': 'no_login', 'source': 'keychain', 'login_file': False,
                                    'named': True, 'saved': 'ok', 'action': 'wait', 'fault': True, 'live_fp': None,
                                    'saved_fp': live.parse(self.saved_b).fingerprint})
        res = self.watch(putback.FAULT_AFTER_S)
        self.assertEqual(res.seen['action'], 'put_back')
        for r in (res, self.watch(1.0)):
            for k, v in r.seen.items():
                self.assertRegex(k, r'^[a-z_]{1,32}$')
                self.assertTrue(v is None or isinstance(v, (bool, int, float)) or re.match(r'^[a-z_]+$', v) or
                                re.match(r'^[0-9a-f]{16}$', v), (k, v))
        flat = json.dumps(res.seen)
        for secret in ('sk-ant', '@', 'uuid-'):
            self.assertNotIn(secret, flat)


class TheKeepStep(Base):
    def test_a_renewal_by_claude_code_is_kept_at_the_next_look_and_asked_once(self):
        self.clock.advance(600)
        newer = self.rig.claude_renews()
        res = self.watch()
        self.assertEqual(res.action, 'kept', res.reason)
        self.assertEqual(self.rig.vault('uuid-b'), vault.bound_text(newer))        # its account's parts (e82dd95)
        self.assertEqual(res.seen['named'], True)
        self.assertEqual(self.watch(1.0).action, 'leave')

    def test_a_login_that_is_the_saved_copy_asks_nothing(self):
        with mock.patch.object(putback.capture, 'keep_pass', side_effect=AssertionError('asked')):
            self.assertEqual(self.watch().action, 'leave')


class WithAutomaticSwitchingOff(Base):
    def test_the_promote_and_the_put_back_still_work(self):
        api.set_auto(enabled=False)
        self.fallback()
        self.assertEqual(self.watch(login_file='appeared').action, 'wait')
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'promote')
        self.lose_login()
        self.watch(1.0)
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'put_back')
        self.assertTrue(self.has_login())


if __name__ == '__main__':
    unittest.main()
