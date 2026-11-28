import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# What opens the quick path that puts Claude Code's sign-in back, and what never does. Claude Code (2.1.329) writes its
# plaintext login file only in its fallback after a refused Keychain save; a chosen sign-out deletes the item, then
# removes that file, then clears the settings' account a moment later. So only a fresh write of that file by Claude
# Code is proof of the fault: the sign-in watch saw it written since the last look, or a look finds it written less
# than FAULT_SEEN_S ago. The file going away, a file left from an earlier pile-up, a write of Switchboard's own, and a
# loss soon after a put-back in an episode that never saw proof are not. And a login that sits only in the plaintext
# file is put back in the Keychain only after two looks, never at the first.
# The rule with no I/O, the sign-in watch's hint, then the account engine on its stand-ins (fakes_accounts) with a
# fake clock, through chosen sign-outs and sign-ins in Claude Code's order. Made-up *.example accounts.
import json
import os
import random
import shutil
import tempfile
import time
import unittest
from unittest import mock

from fakes_autoswitch import FakeClock
from fakes_accounts import FakeAnthropic, Rig, identity_block
from switchboard.accounts import api, autoloop, context, facts, live, putback
from switchboard.accounts import signin_watch as sw
from switchboard.accounts.api import PRESENT
from switchboard.accounts.putback import Seen, decide

NOW = 1794837600.0
X = 'uuid-x'
MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}


def gone(**kw):
    """An item with connector logins only; the settings name a saved account whose login is fine."""
    d = dict(live=PRESENT, login='no_login', named=X, saved='ok')
    d.update(kw)
    return Seen(**d)


def filed(**kw):
    """No item; the account login in Claude Code's plaintext file, proven to be the named account's."""
    d = dict(live=PRESENT, login='oauth', from_file=True, full=True, owner=True, file_there=True, named=X, saved='ok')
    d.update(kw)
    return Seen(**d)


def record(**kw):
    rec = {'missing': None, 'done': [], 'failed': [], 'fault_at': None, 'wrote_at': None, 'episode': None,
           'episodes': [], 'written': None}
    rec.update(kw)
    return rec


def episode(start, proof, writes=1, last=None):
    return {'start': start, 'writes': writes, 'last': start if last is None else last, 'said': False, 'proof': proof}


def proven(seen, hint=None, rec=None, **kw):
    """Whether this look's sightings leave fresh proof in the record, and the record."""
    rec = rec if rec is not None else record()
    putback._sightings(seen, hint, NOW, **kw)(rec)
    return putback.fault(rec, NOW), rec


class TheProofTable(unittest.TestCase):
    def test_a_write_of_the_file_seen_by_the_watch_is_proof_even_when_it_went_away_after(self):
        self.assertTrue(proven(gone(), {'login_file': 'gone', 'login_file_written': True})[0])
        self.assertTrue(proven(gone(), {'login_file': 'changed', 'login_file_written': True})[0])

    def test_a_caller_without_the_key_counts_a_file_that_appeared_or_changed(self):
        for word, want in (('appeared', True), ('changed', True), ('gone', False), (None, False)):
            self.assertEqual(proven(gone(), {'keychain': True, 'login_file': word})[0], want, word)

    def test_the_file_going_away_alone_is_not_proof(self):
        self.assertFalse(proven(gone(), {'login_file': 'gone', 'login_file_written': False})[0])
        self.assertFalse(proven(gone(file_there=False), {'login_file': 'gone'})[0])

    def test_a_file_found_written_lately_is_proof_and_one_left_from_before_is_not(self):
        self.assertTrue(proven(gone(file_there=True, file_fresh=True))[0])
        self.assertFalse(proven(gone(file_there=True))[0])
        self.assertTrue(proven(filed(file_fresh=True))[0])
        self.assertFalse(proven(filed())[0])

    def test_proof_lasts_ten_seconds(self):
        self.assertEqual(putback.FAULT_SEEN_S, 10.0)
        self.assertTrue(putback.fault({'fault_at': NOW - 9.99}, NOW))
        self.assertFalse(putback.fault({'fault_at': NOW - 10.0}, NOW))
        self.assertFalse(putback.fault({'fault_at': NOW + 1.0}, NOW))

    def test_a_loss_soon_after_our_write_is_proof_only_inside_an_episode_that_saw_proof(self):
        self.assertTrue(proven(gone(), rec=record(wrote_at=NOW - 3, episode=episode(NOW - 3, True)))[0])
        self.assertFalse(proven(gone(), rec=record(wrote_at=NOW - 3, episode=episode(NOW - 3, False)))[0])
        self.assertFalse(proven(gone(), rec=record(wrote_at=NOW - 10, episode=episode(NOW - 10, True,
                                                                                      last=NOW - 2)))[0])
        self.assertFalse(proven(gone(named=None), rec=record(wrote_at=NOW - 3, episode=episode(NOW - 3, True)))[0])

    def test_proof_seen_while_an_episode_is_open_marks_the_episode(self):
        _, rec = proven(gone(), {'login_file': 'appeared'}, rec=record(wrote_at=NOW - 3,
                                                                        episode=episode(NOW - 3, False)))
        self.assertTrue(rec['episode']['proof'])
        _, rec = proven(gone(), {'login_file': 'gone'}, rec=record(wrote_at=NOW - 3, episode=episode(NOW - 3, False)))
        self.assertFalse(rec['episode']['proof'])

    def test_an_episode_opened_while_proof_is_fresh_has_seen_proof(self):
        rec = record(fault_at=NOW - 2)
        self.assertTrue(putback._note_write(rec, NOW)['proof'])
        rec = record(fault_at=NOW - 12)
        self.assertFalse(putback._note_write(rec, NOW)['proof'])
        rec = record(fault_at=NOW - 12, episode=episode(NOW - 20, True, last=NOW - 5))
        self.assertTrue(putback._note_write(rec, NOW)['proof'])           # the open episode keeps what it saw

    def test_a_rewrite_of_the_file_by_switchboards_own_write_is_not_proof(self):
        ours = {'at': NOW - 1, 'account': X, 'how': 'over', 'sha': '0' * 64, 'file': [1, 2, 3, 4]}
        rec = record(wrote_at=NOW - 1, written=ours)
        hint = {'login_file': 'changed', 'login_file_written': True}
        self.assertFalse(proven(gone(), hint, rec=dict(rec), file_now=(1, 2, 3, 4))[0])     # still the file we left
        self.assertFalse(proven(gone(), hint, rec=dict(rec), file_now=None)[0])             # ours, then removed
        self.assertTrue(proven(gone(), hint, rec=dict(rec), file_now=(1, 2, 9, 4))[0])      # written since: not ours
        self.assertFalse(proven(gone(file_there=True, file_fresh=True), rec=dict(rec), file_now=(1, 2, 3, 4))[0])
        self.assertTrue(proven(gone(file_there=True, file_fresh=True), rec=dict(rec), file_now=(1, 2, 9, 4))[0])
        # the file holds the item's very bytes: one writer wrote both (a switch, or a put-back)
        self.assertFalse(proven(gone(), hint, file_is_item=True)[0])
        # long after a write of ours, a file written and gone again is Claude Code's
        old = record(wrote_at=NOW - 30, written=dict(ours, at=NOW - 30))
        self.assertTrue(proven(gone(), hint, rec=old, file_now=None)[0])


class TheWatchHint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fresh-proof-watch-')
        self.kc = os.path.join(self.tmp, 'home', 'Library', 'Keychains')
        self.login = os.path.join(self.tmp, 'home', '.claude', '.credentials.json')
        self.settings = os.path.join(self.tmp, 'home', '.claude.json')
        os.makedirs(self.kc)
        os.makedirs(os.path.dirname(self.login))
        with open(self.settings, 'w') as f:
            f.write('{}')
        self.ch = sw.Changes(self.kc, self.login, self.settings, kqueue=None)

    def tearDown(self):
        self.ch.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text):
        tmp = self.login + '.new'
        with open(tmp, 'w') as f:
            f.write(text)
        os.replace(tmp, self.login)

    def test_a_hint_says_whether_the_file_was_written(self):
        self.assertEqual(sw.no_hint()['login_file_written'], False)
        self.write('{"a":1}')
        h = self.ch.sweep()
        self.assertEqual((h['login_file'], h['login_file_written']), ('appeared', True))
        self.write('{"a":2}')
        h = self.ch.sweep()
        self.assertEqual((h['login_file'], h['login_file_written']), ('changed', True))
        os.unlink(self.login)
        h = self.ch.sweep()
        self.assertEqual((h['login_file'], h['login_file_written']), ('gone', False))
        with open(self.settings, 'w') as f:
            f.write('{"oauthAccount":{}}')
        os.utime(self.settings, (time.time() + 5, time.time() + 5))
        h = self.ch.sweep()
        self.assertEqual((h['settings'], h['login_file_written']), (True, False))

    def test_merging_keeps_a_write_through_a_later_removal(self):
        a = {'keychain': False, 'login_file': 'changed', 'settings': False, 'tick': False, 'login_file_written': True}
        b = {'keychain': True, 'login_file': 'gone', 'settings': False, 'tick': False, 'login_file_written': False}
        m = sw.merge(a, b)
        self.assertEqual((m['login_file'], m['login_file_written'], m['keychain']), ('gone', True, True))
        self.assertTrue(sw.merge(b, a)['login_file_written'])
        self.assertFalse(sw.merge(b, dict(b))['login_file_written'])
        self.assertTrue(sw.merge(None, {'login_file': 'appeared'})['login_file_written'])     # a caller without it
        self.assertFalse(sw.merge(None, {'login_file': 'gone'})['login_file_written'])
        self.assertTrue(sw.merge({'login_file': 'changed'}, {'login_file': 'gone'})['login_file_written'])

    def test_the_record_keeps_it(self):
        lines = []
        rec = sw.Record(clock=sw.usage.Clock(), sink=lines.append)
        rec.add('change', hint=dict(sw.no_hint(), login_file='gone', login_file_written=True))
        self.assertTrue(json.loads(lines[-1])['hint']['login_file_written'])


class ThePromoteWaitsForASecondLook(unittest.TestCase):
    def test_never_at_the_first_look(self):
        v = decide(filed(), None, [], NOW)
        self.assertEqual((v.action, v.account, v.missing, v.watch), ('wait', X, (X, NOW), True))
        v = decide(filed(), None, [], NOW, fault=True)
        self.assertEqual((v.action, v.missing), ('wait', (X, NOW)))

    def test_with_proof_two_looks_half_a_second_apart(self):
        self.assertEqual(decide(filed(), (X, NOW - 0.4), [], NOW, fault=True).action, 'wait')
        v = decide(filed(), (X, NOW - putback.FAULT_AFTER_S), [], NOW, fault=True)
        self.assertEqual((v.action, v.missing), ('promote', (X, NOW - putback.FAULT_AFTER_S)))

    def test_without_proof_four_seconds(self):
        self.assertEqual(decide(filed(), (X, NOW - 3.9), [], NOW).action, 'wait')
        self.assertEqual(decide(filed(), (X, NOW - putback.PUT_BACK_AFTER_S), [], NOW).action, 'promote')

    def test_the_settings_must_name_the_same_account_at_both_looks(self):
        self.assertEqual(decide(filed(), ('uuid-y', NOW - 60), [], NOW).missing, (X, NOW))
        self.assertEqual(decide(filed(), ('uuid-y', NOW - 60), [], NOW).action, 'wait')
        self.assertEqual(decide(filed(), (X, NOW + 5), [], NOW).action, 'wait')     # a sighting dated past the clock

    def test_over_many_made_up_looks_a_promote_comes_only_after_its_wait(self):
        rng = random.Random(3606)
        promotes = 0
        for _ in range(20000):
            fault = rng.random() < 0.5
            seen = filed(owner=rng.random() < 0.8, older=rng.random() < 0.3, file_fresh=rng.random() < 0.3,
                         named=rng.choice((X, X, 'uuid-y', None)), part_way=rng.random() < 0.05)
            before = rng.choice((None, (X, NOW - rng.uniform(-1, 6)), ('uuid-y', NOW - rng.uniform(0, 6))))
            v = decide(seen, before, [], NOW, fault=fault)
            if v.action == 'promote':
                promotes += 1
                self.assertEqual(before[0], seen.named)
                self.assertGreaterEqual(NOW - before[1], putback.FAULT_AFTER_S if fault else putback.PUT_BACK_AFTER_S)
                self.assertEqual(v.missing, before)
        self.assertGreater(promotes, 1000)


# ---------- the account engine ----------

class TheStandInsSignInsEndInTheOrderTheyWereMade(unittest.TestCase):
    """The engine's tests below sign in to one account again and again, the clock moved an hour between. A real
    sign-in's end date (refreshTokenExpiresAt) is set fresh when it is made, a fixed span after that moment, and a
    renewal never moves it up (facts 'refresh.expiry_rule'), so a later sign-in ends later. The stand-in Anthropic must
    date them so, also when it moves a sign-in a day on to keep it apart from one made in the same moment: else the
    newest sign-in reads as the older one, and the engine rightly puts the kept one back over it."""

    def dates(self, gaps, **kw):
        clock = FakeClock(wall=NOW, mono=50000.0)
        server = FakeAnthropic(wall=clock.wall)
        out = []
        for gap in gaps:
            clock.advance(gap)
            out.append(live.parse(server.add('uuid-b', 'ana@home.example', 'org-b', **kw)).refresh_expires_ms)
        return out, clock, server

    def test_each_later_sign_in_ends_later_and_is_never_taken_for_the_same_one(self):
        rx, _, _ = self.dates((0.0, 0.3, putback.PUT_BACK_SPAN_S, 21.0, putback.PUT_BACK_SPAN_S, 0.0))
        for earlier, later in zip(rx, rx[1:]):
            self.assertGreater(later, earlier)
            self.assertFalse(facts.same_sign_in(earlier, later))

    def test_one_dated_as_made_days_before_keeps_its_own_date(self):
        _, clock, server = self.dates((0.0, 0.3))                       # the second is moved a day on
        clock.advance(3600.0)
        rx = live.parse(server.add('uuid-b', 'ana@home.example', 'org-b', rx_days=3)).refresh_expires_ms
        self.assertEqual(rx, int((clock.wall() + 3 * 86400) * 1000) + server.signins)


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='fresh-proof-')
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

    def write_file(self, text, age):
        """Claude Code's plaintext login file holding text, written `age` seconds ago by the fake clock."""
        with open(self.cred, 'w') as f:
            f.write(text)
        os.chmod(self.cred, 0o600)
        t = self.clock.wall() - age
        os.utime(self.cred, (t, t))

    def file_text(self):
        try:
            with open(self.cred) as f:
                return f.read()
        except FileNotFoundError:
            return None

    def watch(self, after=0.0, **hint):
        """The sign-in watch's look: its hint as the watch builds it."""
        self.clock.advance(after)
        h = dict(sw.no_hint())
        h.update(hint)
        if 'login_file_written' not in hint:
            h['login_file_written'] = h['login_file'] in ('appeared', 'changed')
        return api.watch_look(h)

    def lose_login(self):
        """What many processes saving at once can leave: the item deleted, then saved again without the login."""
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))

    def live_writes(self):
        return sum(1 for c in self.rig.kc.calls if c[0] in ('write', 'delete') and c[1] == self.rig.service)

    def has_login(self):
        text = self.rig.live_text()
        return bool(text) and live.parse(text).kind == 'oauth'

    def put_back_once(self):
        """A loss with no file and no proof, put back after PUT_BACK_AFTER_S: one write of ours."""
        self.lose_login()
        self.assertEqual(self.watch(0.2, keychain=True).action, 'wait')
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        return res

    def claude_code_routine(self, settings_after, new_login=False, connectors=False, look=None):
        """Claude Code's sign-out routine in its order (2.1.329): the item deleted, then its plaintext file removed
        (when there is one), then (a sign-in: connectors) the item saved back with only its connector state, then the
        settings' account cleared settings_after s after the item went; then (a sign-in: new_login) the new login and
        its settings. A look after every step, with the hint the sign-in watch would give, and one every 0.2 s between,
        for 20 s after the last step. Returns the looks' answers."""
        look = look or (lambda hint: api.watch_look(hint))
        steps = [(0.0, lambda: self.rig.sec.drop(self.rig.service, self.rig.account), {'keychain': True})]
        file_at = min(0.1, settings_after / 2.0)
        if os.path.exists(self.cred):
            steps.append((file_at, self.remove_file, {'login_file': 'gone'}))
        if connectors:
            steps.append((file_at + 0.001, lambda: self.rig.set_live(json.dumps(MCP, separators=(',', ':'))),
                          {'keychain': True}))
        steps.append((settings_after, lambda: self.rig.set_config(None), {'settings': True}))
        if new_login:
            def sign_in():
                self.new = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')
                blob = json.loads(self.rig.live_text() or '{}')
                blob.update(json.loads(self.new))
                self.rig.set_live(json.dumps(blob, separators=(',', ':')))
            steps.append((settings_after + 0.3, sign_in, {'keychain': True}))
            steps.append((settings_after + 0.31, lambda: self.rig.set_config(
                identity_block('uuid-b', 'ana@home.example', 'org-b')), {'settings': True}))
        start = self.clock.wall()
        end = steps[-1][0] + 20.0
        answers, t, last = [], 0.0, 0.0
        while t <= end:
            due = steps[0][0] if steps else None
            tick = last + 0.2
            if due is not None and due <= tick:
                at, act, hint = steps.pop(0)
                self.clock.advance(max(0.0, start + at - self.clock.wall()))
                act()
            else:
                hint = {}
                self.clock.advance(max(0.0, start + tick - self.clock.wall()))
            t = last = self.clock.wall() - start
            h = dict(sw.no_hint())
            h.update(hint)
            h['login_file_written'] = h['login_file'] in ('appeared', 'changed')
            answers.append(look(h))
        return answers

    def remove_file(self):
        if os.path.exists(self.cred):
            os.unlink(self.cred)

    def nothing_of_ours_left(self):
        """No write of ours is left in the item: the note of our last write lives its minute, and holds nothing the
        item holds now."""
        w = putback.load_record(self.ctx).get('written')
        if w is not None:
            self.assertIsNone(putback._ours(live.read_live(self.ctx), w['sha'], w.get('login_sha')))


class AChosenSignOutWithAnOldFileBesideAGoodItem(Base):
    """After a pile-up Claude Code's fallback file often stays for good beside a healthy item. A chosen sign-out
    removes it: the item goes, then the file, then the settings. Nothing of that is proof, so nothing is put back,
    whenever the settings change."""

    DELAYS = (0.005, 0.6, 1.5, 3.0)

    def old_file(self):
        self.write_file(self.b_text, age=3600.0)

    def test_a_chosen_sign_out_ends_signed_out(self):
        for delay in self.DELAYS:
            with self.subTest(settings_after=delay):
                self.old_file()
                self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))
                self.rig.set_live(self.b_text)
                putback.reset()
                answers = self.claude_code_routine(delay)
                self.assertFalse(self.has_login(), [a.action for a in answers])
                self.assertIsNone(self.rig.live_text())
                self.assertNotIn('promote', [a.action for a in answers])
                self.assertNotIn('put_back', [a.action for a in answers])
                self.nothing_of_ours_left()
                self.clock.advance(putback.PUT_BACK_SPAN_S)

    def test_the_reviewers_order_with_the_watchs_hint(self):
        # delete, looks at +0.1 s and +0.7 s with the file gone between, settings cleared at +0.8 s
        self.old_file()
        self.rig.sec.drop(self.rig.service, self.rig.account)
        first = self.watch(0.1, keychain=True)
        self.remove_file()
        second = self.watch(0.6, login_file='gone')
        self.rig.set_config(None)
        third = self.watch(0.1, settings=True)
        self.assertEqual([a.action for a in (first, second, third)], ['wait', 'wait', 'leave'],
                         [a.reason for a in (first, second, third)])
        self.assertIsNone(self.rig.live_text())
        self.assertEqual(self.live_writes(), 0)

    def test_a_sign_in_inside_claude_code_ends_with_the_new_login(self):
        for delay in self.DELAYS:
            with self.subTest(settings_after=delay):
                self.old_file()
                self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))
                self.rig.set_live(self.b_text)
                putback.reset()
                self.claude_code_routine(delay, new_login=True, connectors=True)
                self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'],
                                 json.loads(self.new)['claudeAiOauth'])
                self.nothing_of_ours_left()
                self.clock.advance(putback.PUT_BACK_SPAN_S)

    def test_the_loops_looks_with_no_hint_end_signed_out_too(self):
        for delay in self.DELAYS:
            with self.subTest(settings_after=delay):
                self.old_file()
                self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))
                self.rig.set_live(self.b_text)
                putback.reset()
                self.claude_code_routine(delay, look=lambda hint: api.put_back_pass())
                self.assertIsNone(self.rig.live_text())
                self.clock.advance(putback.PUT_BACK_SPAN_S)


class SoonAfterAPutBackOfOurs(Base):
    """She sees "Not logged in" in a chat after a put-back and signs out or in at once. The episode the put-back
    opened never saw proof, so the loss is not taken for the fault: the slow wait holds."""

    DELAYS = (0.005, 0.6, 1.5, 3.0)

    def test_a_chosen_sign_out_within_ten_seconds_ends_signed_out(self):
        for delay in self.DELAYS:
            with self.subTest(settings_after=delay):
                self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))
                self.rig.set_live(self.b_text)
                self.put_back_once()
                self.clock.advance(2.0)
                answers = self.claude_code_routine(delay)
                self.assertIsNone(self.rig.live_text(), [(a.action, a.reason) for a in answers])
                self.assertNotIn('put_back', [a.action for a in answers])
                self.clock.advance(putback.PUT_BACK_SPAN_S)

    def test_a_sign_in_within_ten_seconds_ends_with_the_new_login(self):
        for delay in self.DELAYS:
            with self.subTest(settings_after=delay):
                self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))
                self.rig.set_live(self.b_text)
                self.put_back_once()
                self.clock.advance(1.0)
                self.claude_code_routine(delay, new_login=True, connectors=True)
                self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'],
                                 json.loads(self.new)['claudeAiOauth'])
                self.clock.advance(putback.PUT_BACK_SPAN_S)

    def test_with_an_old_file_beside_the_rewrite_of_ours_is_no_proof_and_a_sign_out_ends_signed_out(self):
        for delay in self.DELAYS:
            with self.subTest(settings_after=delay):
                self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))
                self.write_file(json.dumps(MCP), age=3600.0)         # left by an earlier pile-up, without a login
                self.put_back_once()                                 # our write rewrites that file (fresh now)
                self.assertTrue(os.path.exists(self.cred))
                self.clock.advance(2.0)
                answers = self.claude_code_routine(delay)
                self.assertIsNone(self.rig.live_text(), [(a.action, a.reason) for a in answers])
                self.assertFalse([a for a in answers if a.action in ('put_back', 'promote')])
                self.assertFalse([a for a in answers if a.seen and a.seen.get('fault')])
                self.clock.advance(putback.PUT_BACK_SPAN_S)

    def test_after_a_repair_from_an_old_file_a_sign_out_within_ten_seconds_ends_signed_out(self):
        for delay in self.DELAYS:
            with self.subTest(settings_after=delay):
                self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))
                self.write_file(self.b_text, age=3600.0)
                self.rig.set_live(json.dumps(MCP, separators=(',', ':')))
                self.watch(0.2, keychain=True)
                self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')    # the repair
                self.assertIsNotNone(putback._hold_note(self.ctx))   # the file is kept as one of ours, for a while
                self.clock.advance(2.0)
                answers = self.claude_code_routine(delay)
                self.assertIsNone(self.rig.live_text(), [(a.action, a.reason) for a in answers])
                self.clock.advance(putback.PUT_BACK_SPAN_S)

    def test_with_proof_in_the_episode_a_loss_soon_after_is_put_back_quickly(self):
        self.lose_login()
        self.watch(0.1, login_file='appeared')                 # Claude Code wrote its file: proof
        res = self.watch(putback.FAULT_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.lose_login()
        self.assertEqual(self.watch(2.0, keychain=True).action, 'wait')
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'put_back')


class TheEngineSeesTheFilesAge(Base):
    def test_a_file_written_lately_is_fresh_and_an_old_one_is_not(self):
        self.lose_login()
        for age, want in ((0.0, True), (9.9, True), (10.1, False), (3600.0, False)):
            self.write_file(json.dumps(MCP), age)
            seen = putback.see(self.ctx, self.clock.wall())[0]
            self.assertTrue(seen.file_there)
            self.assertEqual(seen.file_fresh, want, age)

    def test_a_look_that_finds_a_fresh_file_notes_proof_and_an_old_one_does_not(self):
        self.lose_login()
        self.write_file(json.dumps(MCP), 3600.0)
        self.assertFalse(self.watch(0.2).seen['fault'])
        self.write_file(json.dumps(MCP), 1.0)
        self.assertTrue(self.watch(0.2).seen['fault'])

    def test_the_watch_seeing_the_file_go_notes_no_proof_and_a_write_does(self):
        self.lose_login()
        self.assertFalse(self.watch(0.2, login_file='gone').seen['fault'])
        self.assertTrue(self.watch(0.2, login_file='gone', login_file_written=True).seen['fault'])


class ThePromoteRemovesOnlyTheSameFile(Base):
    """Claude Code's plaintext file goes after a promote only when it is still the very file the write left: its
    stamp (device, inode, modification time, size). A process whose save was refused writes the same object to the
    file again and deletes the item a moment later; removing that fresh file would leave no login anywhere. And the
    file the write left does not go at once: the processes that asked while the item was gone still read it, so a
    later look takes it away (putback.HOLD_TIDY_S after the write)."""

    def fallback(self):
        text = self.rig.live_text()
        self.write_file(text, age=0.0)
        self.rig.sec.drop(self.rig.service, self.rig.account)
        return text

    def test_a_file_rewritten_with_the_same_bytes_after_the_write_stays(self):
        text = self.fallback()
        self.assertEqual(self.watch(login_file='appeared').action, 'wait')
        state = {'wrote': False, 'done': False}

        def saver(verb, service, account):
            if service != self.rig.service:
                return
            if verb == 'write':
                state['wrote'] = True
            elif verb == 'read' and state['wrote'] and not state['done']:
                state['done'] = True                  # the read-back: meanwhile a refused save writes the file again
                before = os.stat(self.cred).st_mtime_ns
                with open(self.cred, 'w') as f:       # in place: the same inode, the same size, the same bytes
                    f.write(text)
                os.utime(self.cred, ns=(before + 1000000, before + 1000000))
        self.rig.kc.on_call = saver
        res = self.watch(putback.FAULT_AFTER_S)
        self.rig.kc.on_call = None
        self.assertEqual(res.action, 'promote', res.reason)
        self.assertTrue(state['done'])
        self.assertTrue(os.path.exists(self.cred))                      # a file rewritten since stays
        self.watch(putback.HOLD_TIDY_S + 1.0)                           # and no later look takes it for ours
        self.assertTrue(os.path.exists(self.cred))
        self.rig.sec.drop(self.rig.service, self.rig.account)          # that save's own delete of the item
        got = live.read_live(self.ctx, fresh=True)
        self.assertEqual((got.read.kind, got.read.provenance), (PRESENT, 'file'))
        self.assertEqual(got.login.refresh_token, live.parse(text).refresh_token)   # a login is still readable

    def test_the_file_the_write_left_is_removed_by_a_later_look(self):
        text = self.fallback()
        self.watch(login_file='appeared')
        st = os.stat(self.cred)
        before = [st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size]
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'promote')
        with open(self.cred) as f:
            self.assertEqual(f.read(), text)                            # still there, untouched: it is still read
        self.assertEqual(putback.load_record(self.ctx)['written']['file'], before)
        self.watch(putback.HOLD_TIDY_S - 1.0)
        self.assertTrue(os.path.exists(self.cred))
        self.watch(1.5)
        self.assertFalse(os.path.exists(self.cred))                     # nothing of ours left in a file

    def test_a_file_replaced_by_another_with_the_same_bytes_stays(self):
        text = self.fallback()
        self.watch(login_file='appeared')
        state = {'wrote': False}

        def saver(verb, service, account):
            if service == self.rig.service and verb == 'write':
                state['wrote'] = True
            elif service == self.rig.service and verb == 'read' and state['wrote']:
                state['wrote'] = False
                tmp = self.cred + '.new'
                with open(tmp, 'w') as f:
                    f.write(text)
                os.replace(tmp, self.cred)            # a new inode
        self.rig.kc.on_call = saver
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'promote')
        self.rig.kc.on_call = None
        self.assertTrue(os.path.exists(self.cred))


if __name__ == '__main__':
    unittest.main()


class AnItemWithoutALoginBesideTheFile(Base):
    """Claude Code reads its Keychain item first, and an item that is there without a login wins over the plaintext
    file: Claude Code is signed out while its login sits in that file. No sign-out leaves this (it removes the file).
    When the file's login is proven the named account's, it is written into the item with the item's own machine-wide
    parts (an ordinary write), on the put-back's own timing, and the file is then kept as the promote keeps it (with
    the item's text, for the processes still reading it) until the sign-in hold takes it away."""

    ITEM_MCP = {'mcpOAuth': {'item-server': {'accessToken': 'mcp-made-up-item', 'expiresAt': 2}}}

    def state(self, text=None, age=3600.0):
        """The item without a login (its own connector state) and the file holding text (B's login by default)."""
        text = self.b_text if text is None else text
        self.write_file(text, age)
        self.rig.set_live(json.dumps(dict(self.ITEM_MCP, discoveryState={'made-up': 7}), separators=(',', ':')))
        return text

    def test_the_files_login_goes_in_with_the_items_own_machine_wide_parts(self):
        text = self.state()
        first = self.watch(0.2, keychain=True)
        self.assertEqual(first.action, 'wait', first.reason)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S - 1.0).action, 'wait')
        res = self.watch(1.0)
        self.assertEqual((res.action, res.account), ('put_back', 'ana@home.example'), res.reason)
        blob = json.loads(self.rig.live_text())
        self.assertEqual(blob['claudeAiOauth'], json.loads(text)['claudeAiOauth'])
        self.assertEqual(blob['mcpOAuth'], self.ITEM_MCP['mcpOAuth'])          # the item's, not the file's
        self.assertEqual(self.rig.kc.created, [])                               # an ordinary write over the item
        self.assertEqual(self.file_text(), self.rig.live_text())                # kept as the promote keeps it, with
        self.assertIsNotNone(putback._hold_note(self.ctx))                      # the item's text, as one of ours
        self.assertEqual(putback.load_record(self.ctx)['written']['how'], 'over')
        self.assertEqual(len(putback.load_record(self.ctx)['done']), 1)         # one write of the episode
        self.assertEqual(self.told, [res])                                      # Claude Code was signed out
        self.assertTrue([t for t in self.rig.logs if 'plain login file' in t and 'ana@home.example' in t],
                        self.rig.logs[-3:])

    def test_with_proof_two_looks_half_a_second_apart(self):
        self.state(age=0.5)
        self.assertEqual(self.watch(0.1, keychain=True).action, 'wait')
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'put_back')
        self.assertTrue(self.has_login())

    def test_a_login_in_the_file_not_proven_the_accounts_stays_as_it_is_and_is_said_once(self):
        self.state(self.rig.vault('uuid-a'))                        # A's login; the settings name B
        res = [self.watch(5.0) for _ in range(5)]
        self.assertEqual([r.action for r in res], ['leave'] * 5, [r.reason for r in res])
        self.assertEqual(len([r for r in res if r.line]), 1)
        self.assertEqual(self.live_writes(), 0)
        self.assertTrue(os.path.exists(self.cred))

    def test_an_older_generation_in_the_file_brings_the_newer_saved_copy_with_the_items_parts(self):
        old = self.b_text
        self.clock.advance(600)
        newer = self.rig.server.refresh_for(old)
        self.rig.set_live(newer)
        self.assertIn('Saved the newer login', api.keep_pass())
        self.state(old)
        self.watch(0.2, keychain=True)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        blob = json.loads(self.rig.live_text())
        self.assertEqual(blob['claudeAiOauth'], json.loads(newer)['claudeAiOauth'])
        self.assertEqual(blob['mcpOAuth'], self.ITEM_MCP['mcpOAuth'])
        self.assertEqual(self.file_text(), self.rig.live_text())   # never the used-up login it held

    def test_a_file_that_changes_before_the_write_is_not_written_from_its_old_bytes(self):
        self.state()
        self.watch(0.2, keychain=True)
        newer = self.rig.server.refresh_for(self.b_text)

        def claude_code(verb, service, account):
            if verb == 'read' and service == self.rig.service:
                self.write_file(newer, age=0.0)         # Claude Code wrote its file again meanwhile
        self.rig.kc.on_call = claude_code
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.rig.kc.on_call = None
        self.assertNotEqual(json.loads(self.rig.live_text()).get('claudeAiOauth'), json.loads(self.b_text)
                            ['claudeAiOauth'], res.reason)

    def test_a_chosen_sign_out_is_never_undone_from_the_file(self):
        self.state()
        self.watch(0.2, keychain=True)
        self.rig.set_config(None)
        for _ in range(10):
            self.assertEqual(self.watch(5.0).action, 'leave')
        self.assertEqual(self.live_writes(), 0)


class FailedWritesAreSpaced(unittest.TestCase):
    """A look whose writes all failed is noted; the next try waits by the count of such looks in a row: with proof
    0.5, 1, 2, 4, 8, 15, 30, then 60 s; without, 4, 15, 60, 300 s. The same for a promote."""

    def test_with_proof_the_ladder(self):
        self.assertEqual(putback.FAULT_FAIL_WAITS, (0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0))
        for n, wait in enumerate(putback.FAULT_FAIL_WAITS + (60.0,), start=1):
            failed = [NOW - 500 + i for i in range(n - 1)] + [NOW - wait + 0.01]
            failed = failed[-2 * putback.PUT_BACK_MAX:]
            v = decide(gone(), (X, NOW - 600), [], NOW, failed, fault=True)
            self.assertEqual(v.action, 'wait', (n, wait))
            failed[-1] = NOW - wait
            self.assertEqual(decide(gone(), (X, NOW - 600), [], NOW, failed, fault=True).action, 'put_back', n)

    def test_without_proof_round_35s_spacing(self):
        for n, wait in enumerate(putback.PUT_BACK_WAITS, start=1):
            failed = [NOW - 3000 + i for i in range(n - 1)] + [NOW - wait + 0.01]
            self.assertEqual(decide(gone(), (X, NOW - 3500), [], NOW, failed).action, 'wait', n)
            failed[-1] = NOW - wait
            self.assertEqual(decide(gone(), (X, NOW - 3500), [], NOW, failed).action, 'put_back', n)

    def test_a_promote_is_spaced_the_same_way(self):
        self.assertEqual(decide(filed(), (X, NOW - 60), [], NOW, [NOW - 0.4], fault=True).action, 'wait')
        self.assertEqual(decide(filed(), (X, NOW - 60), [], NOW, [NOW - 0.5], fault=True).action, 'promote')
        self.assertEqual(decide(filed(), (X, NOW - 60), [], NOW, [NOW - 3.9]).action, 'wait')
        self.assertEqual(decide(filed(), (X, NOW - 60), [], NOW, [NOW - 4.0]).action, 'promote')
        two = [NOW - 100, NOW - 14.9]
        self.assertEqual(decide(filed(), (X, NOW - 600), [], NOW, two).action, 'wait')
        self.assertEqual(decide(filed(), (X, NOW - 600), [], NOW, [NOW - 100, NOW - 15]).action, 'promote')


class WritesThatKeepFailing(Base):
    def refuse_every_write(self):
        self.rig.kc.rule('write', service=self.rig.service, why='denied', times=-1)

    def fails(self, looks, gap, **hint):
        """[(when the look started, its answer)] for looks `gap` s apart (each try's own sleeps come on top)."""
        out = []
        for _ in range(looks):
            self.clock.advance(gap)
            started = self.clock.wall()
            out.append((started, self.watch(0.0, **hint)))
        return out

    def test_with_proof_the_tries_follow_the_ladder(self):
        self.lose_login()
        self.refuse_every_write()
        t0 = self.clock.wall()
        looks = self.fails(520, 0.25, login_file='changed')            # Claude Code keeps writing its file
        failed = [t for t, r in looks if r.action == 'failed']
        self.assertLessEqual(self.clock.wall() - t0, 400)
        gaps = [b - a for a, b in zip(failed, failed[1:])]
        for k, gap in enumerate(gaps):
            want = putback.FAULT_FAIL_WAITS[min(k, len(putback.FAULT_FAIL_WAITS) - 1)]
            self.assertGreaterEqual(gap, want - 1e-6, (k, gaps))
        self.assertGreaterEqual(len(failed), 9, gaps)
        self.assertEqual(self.live_writes(), len(failed) * putback.TRIES)
        self.assertEqual(putback.load_record(self.ctx)['done'], [])

    def test_a_promote_refused_every_time_is_tried_less_often_each_time(self):
        self.write_file(self.b_text, age=3600.0)
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.refuse_every_write()
        looks = self.fails(120, 1.0)
        failed = [t for t, r in looks if r.action == 'failed']
        self.assertEqual(len(failed), len(putback.PUT_BACK_WAITS), [r.action for _, r in looks])   # 4, 4, 15, 60 s
        self.assertLessEqual(self.live_writes(), len(failed) * putback.TRIES)
        self.assertTrue(putback.load_record(self.ctx)['failed'])
        self.assertTrue(os.path.exists(self.cred))
        self.assertEqual(len([r for _, r in looks if r.line]), 1)       # said once

    def test_a_repair_from_the_file_refused_is_noted_too(self):
        self.write_file(self.b_text, age=3600.0)
        self.rig.set_live(json.dumps(MCP, separators=(',', ':')))
        self.refuse_every_write()
        self.watch(0.2)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'failed')
        self.assertEqual(len(putback.load_record(self.ctx)['failed']), 1)
        self.assertEqual(self.watch(1.0).action, 'wait')

    def failed_a_few_times(self):
        self.lose_login()
        self.refuse_every_write()
        self.watch(0.2)
        for wait in putback.PUT_BACK_WAITS[:2]:
            self.assertEqual(self.watch(wait).action, 'failed')
        self.assertEqual(len(putback.load_record(self.ctx)['failed']), 2)
        del self.rig.kc.rules[:]

    def test_a_landed_write_clears_them(self):
        self.failed_a_few_times()
        self.assertEqual(self.watch(putback.PUT_BACK_WAITS[1]).action, 'put_back')
        self.assertEqual(putback.load_record(self.ctx)['failed'], [])

    def test_a_login_seen_there_clears_them(self):
        self.failed_a_few_times()
        self.rig.set_live(self.b_text)
        self.assertEqual(self.watch(1.0).action, 'leave')
        self.assertEqual(putback.load_record(self.ctx)['failed'], [])

    def test_a_verdict_that_names_no_account_clears_them(self):
        self.failed_a_few_times()
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_config(None)                                      # a chosen sign-out ended the episode
        self.assertEqual(self.watch(1.0).action, 'leave')
        self.assertEqual(putback.load_record(self.ctx)['failed'], [])
        self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))   # later: signed in, lost again
        self.rig.set_live(self.b_text)
        self.watch(600.0)
        self.clock.advance(putback.PUT_BACK_SPAN_S)
        self.lose_login()
        self.watch(0.2)
        self.assertEqual(self.watch(putback.PUT_BACK_AFTER_S).action, 'put_back')    # not spaced by old failures

    def test_a_save_lock_held_past_the_budget_is_a_wait_not_a_failed_write(self):
        self.lose_login()
        self.watch(0.2)
        lock = os.path.join(self.rig.home, '.claude', '.storage-write.lock')
        os.mkdir(lock)
        try:
            res = [self.watch(putback.PUT_BACK_AFTER_S) for _ in range(putback.BUSY_LOOKS)]
        finally:
            os.rmdir(lock)
        self.assertEqual([r.action for r in res], ['wait'] * putback.BUSY_LOOKS, [r.reason for r in res])
        self.assertIn('saving its login', res[0].reason)
        self.assertTrue(all(r.watch for r in res))
        self.assertEqual(putback.load_record(self.ctx)['failed'], [])
        held = [t for t in self.rig.logs if 'save lock' in t and 'looks in a row' in t]
        self.assertEqual(len(held), 1, self.rig.logs[-5:])
        self.assertEqual(self.live_writes(), 0)
        self.assertEqual(self.watch(1.0).action, 'put_back')            # free again: at once


class TheWords(Base):
    def test_the_promote_says_what_was_found_and_done(self):
        self.assertEqual(putback.PROMOTE_LINE, 'Claude Code kept its sign-in only in its plain login file. Switchboard '
                                               'put it back in the Keychain.')

    def test_a_held_save_lock_is_named_with_the_home_folder_as_a_tilde(self):
        self.lose_login()
        self.watch(0.2)
        lock = os.path.join(self.rig.home, '.claude', '.storage-write.lock')
        os.mkdir(lock)
        try:
            for _ in range(putback.BUSY_LOOKS):
                self.watch(putback.PUT_BACK_AFTER_S)
        finally:
            os.rmdir(lock)
        held = [t for t in self.rig.logs if 'looks in a row' in t]
        self.assertEqual(len(held), 1, self.rig.logs[-4:])
        self.assertIn("Claude Code's save lock (~/.claude/.storage-write.lock) was held", held[0])
        self.assertNotIn(self.rig.home, held[0])

    def test_a_login_left_in_the_plain_file_right_after_a_switch_has_its_own_line(self):
        from switchboard.accounts import switcher
        self.clock.advance(600)
        self.rig.set_live(self.rig.server.refresh_for(self.b_text))     # B renewed and kept: the file's is older
        self.assertIn('Saved the newer login', api.keep_pass())
        self.assertTrue(switcher.switch(self.ctx, 'uuid-a', source='auto', kind='forced').ok)
        self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))    # the earlier name, written back
        self.write_file(self.b_text, age=0.0)
        self.rig.sec.drop(self.rig.service, self.rig.account)
        said = [r.line for r in (self.watch(1.0) for _ in range(3)) if r.line]
        self.assertEqual(said, ["Claude Code keeps its sign-in only in its plain login file, and its settings name "
                                "ana@home.example, the account Switchboard just switched away from; Switchboard left "
                                "it as it is"])


class AWriteThatRacedASignOutIsTakenBack(Base):
    """A put-back, a promote or a repair from the file that lands while Claude Code signs out (its settings then name
    nobody) is taken back: an item we created is deleted, one we wrote over gets back what was there. Within four
    bounds, each of which alone keeps the item untouched: our own write, within TAKE_BACK_S, settings that are there
    and name nobody, and the item still holding byte for byte what we wrote."""

    def clear_settings_at_the_read_back(self):
        """Claude Code's sign-out clears its settings while our write is read back (in the same hold of the lock)."""
        state = {'wrote': False}

        def at(verb, service, account):
            if service != self.rig.service:
                return
            if verb == 'write':
                state['wrote'] = True
            elif verb == 'read' and state['wrote']:
                state['wrote'] = False
                self.rig.set_config(None)
        self.rig.kc.on_call = at

    def drop_item(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)

    def nothing_counted(self):
        rec = putback.load_record(self.ctx)
        self.assertEqual((rec['done'], rec['written']), ([], None))
        self.assertEqual([e for e in self.rig.events if e[2].get('h') == 'repair'], [])
        self.assertEqual(self.told, [])

    def taken_back_line(self, res, history=False):
        """history: a take-back at a later look of a put-back whose History line went out is said in History too
        (else it is said in the log only)."""
        self.assertEqual(res.action, 'leave', res.reason)
        self.assertEqual(res.line, 'Claude Code was signing out when Switchboard put back the login of '
                                   'ana@home.example; Switchboard took its write back')
        if history:
            self.assertIn(res.line, [e[0] for e in self.rig.events if e[2].get('h') == 'repair'])
        else:
            self.assertIn(res.line, self.rig.logs)

    def test_an_item_we_created_in_the_same_hold_is_deleted(self):
        self.drop_item()
        self.watch(0.2)
        self.clear_settings_at_the_read_back()
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.rig.kc.on_call = None
        self.taken_back_line(res)
        self.assertIsNone(self.rig.live_text())
        self.assertIn(('delete', self.rig.service, self.rig.account), self.rig.kc.calls)
        self.nothing_counted()

    def test_an_item_we_wrote_over_in_the_same_hold_gets_back_exactly_what_was_there(self):
        self.lose_login()
        before = self.rig.live_text()
        self.watch(0.2)
        self.clear_settings_at_the_read_back()
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.rig.kc.on_call = None
        self.taken_back_line(res)
        self.assertEqual(self.rig.live_text(), before)
        self.nothing_counted()

    def test_a_promote_in_the_same_hold_is_taken_back_and_the_file_left(self):
        self.write_file(self.b_text, age=3600.0)
        self.drop_item()
        self.watch(0.2)
        self.clear_settings_at_the_read_back()
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.rig.kc.on_call = None
        self.taken_back_line(res)
        self.assertIsNone(self.rig.live_text())
        self.assertTrue(os.path.exists(self.cred))                     # Claude Code's own, for its sign-out to remove
        self.nothing_counted()

    def test_a_repair_from_the_file_in_the_same_hold_gets_back_what_was_there(self):
        self.write_file(self.b_text, age=3600.0)
        self.rig.set_live(json.dumps(MCP, separators=(',', ':')))
        before = self.rig.live_text()
        self.watch(0.2)
        self.clear_settings_at_the_read_back()
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.rig.kc.on_call = None
        self.taken_back_line(res)
        self.assertEqual(self.rig.live_text(), before)
        self.nothing_counted()

    def landed(self, created):
        if created:
            self.drop_item()
        else:
            self.lose_login()
        self.watch(0.2)
        res = self.watch(putback.PUT_BACK_AFTER_S)
        self.assertEqual(res.action, 'put_back', res.reason)
        return self.rig.live_text()

    def test_a_later_look_deletes_an_item_we_created(self):
        self.landed(created=True)
        self.clock.advance(2.0)
        self.rig.set_config(None)
        res = self.watch(0.2, settings=True)
        self.taken_back_line(res, history=True)
        self.assertIsNone(self.rig.live_text())
        self.assertIsNone(putback.load_record(self.ctx)['written'])
        self.assertEqual(len([e for e in self.rig.events if e[2].get('h') == 'repair']), 2)   # the put-back's, ours

    def test_a_later_look_takes_only_the_login_out_of_an_item_we_wrote_over(self):
        ours = json.loads(self.landed(created=False))
        self.clock.advance(2.0)
        self.rig.set_config(None)
        self.taken_back_line(self.watch(0.2, settings=True), history=True)
        now = json.loads(self.rig.live_text())
        self.assertNotIn('claudeAiOauth', now)
        self.assertEqual(now, dict((k, v) for k, v in ours.items() if k != 'claudeAiOauth'))

    # each bound alone keeps the item untouched

    def test_bound_our_own_write_a_login_claude_code_wrote_is_never_removed(self):
        self.landed(created=True)
        new = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')
        self.rig.set_live(new)                                          # Claude Code's own login now
        self.rig.set_config(None)
        for _ in range(5):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), new)
        self.assertIsNotNone(putback.load_record(self.ctx)['written'])   # the note stays its minute, takes nothing

    def test_bound_within_sixty_seconds(self):
        self.assertEqual(putback.TAKE_BACK_S, 60.0)
        ours = self.landed(created=True)
        self.clock.advance(putback.TAKE_BACK_S)
        self.rig.set_config(None)
        for _ in range(5):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), ours)

    def test_bound_settings_that_name_someone_cannot_be_read_or_are_missing(self):
        ours = self.landed(created=True)
        for _ in range(3):
            self.watch(1.0)                                             # still named: nothing to take back
        self.assertEqual(self.rig.live_text(), ours)
        with open(self.rig.config_path(), 'w') as f:
            f.write('{not json')
        for _ in range(3):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), ours)
        os.unlink(self.rig.config_path())
        for _ in range(3):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), ours)
        self.assertIsNotNone(putback.load_record(self.ctx)['written'])

    def test_bound_byte_for_byte_a_renewal_after_our_write_is_never_touched(self):
        self.landed(created=False)
        renewed = self.rig.claude_renews()                              # Claude Code renewed: other bytes
        self.rig.set_config(None)
        for _ in range(5):
            self.watch(1.0)
        self.assertEqual(self.rig.live_text(), renewed)
        self.assertNotIn(('delete', self.rig.service, self.rig.account), self.rig.kc.calls)

    # the orders of a chosen sign-out that reach a write of ours

    def proof_episode(self):
        """A put-back in an episode that saw Claude Code write its plaintext file: a loss soon after is the fault."""
        self.lose_login()
        self.watch(0.1, login_file='appeared')
        self.assertEqual(self.watch(putback.FAULT_AFTER_S).action, 'put_back')
        self.clock.advance(1.0)

    def test_the_reviewers_order_ends_signed_out_whenever_the_settings_change(self):
        for settings_at in (0.8, 1.5, 3.0, 10.0):
            with self.subTest(settings_at=settings_at):
                self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))
                self.rig.set_live(self.b_text)
                self.proof_episode()
                t0 = self.clock.wall()
                self.drop_item()                                         # the sign-out deletes the item
                answers = [self.watch(0.1, keychain=True), self.watch(0.6)]
                # the quick path, from the copy of the sign-in the put-back left in the plain login file
                self.assertEqual(answers[-1].action, putback.PROMOTE, [a.reason for a in answers])
                while self.clock.wall() - t0 < settings_at - 0.2:
                    answers.append(self.watch(0.2))
                self.clock.advance(max(0.0, t0 + settings_at - self.clock.wall()))
                self.rig.set_config(None)                                # then the settings name nobody
                answers.append(self.watch(0.15, settings=True))
                for _ in range(10):
                    answers.append(self.watch(0.5))
                self.assertFalse(self.has_login(), [(a.action, a.reason) for a in answers])
                self.assertIsNone(self.rig.live_text())
                self.nothing_of_ours_left()
                self.clock.advance(putback.PUT_BACK_SPAN_S)

    def test_claude_codes_routine_inside_a_proof_episode_ends_signed_out(self):
        for delay in (0.005, 0.6, 1.5, 3.0, 10.0):
            with self.subTest(settings_after=delay):
                self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))
                self.rig.set_live(self.b_text)
                self.proof_episode()
                answers = self.claude_code_routine(delay)
                self.assertFalse(self.has_login(), [(a.action, a.reason) for a in answers])
                self.nothing_of_ours_left()
                self.clock.advance(putback.PUT_BACK_SPAN_S)

    def test_a_sign_in_inside_claude_code_inside_a_proof_episode_ends_with_the_new_login(self):
        for delay in (0.005, 0.6, 1.5, 3.0, 10.0):
            with self.subTest(settings_after=delay):
                self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))
                self.rig.set_live(self.b_text)
                self.proof_episode()
                self.claude_code_routine(delay, new_login=True, connectors=True)
                self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'],
                                 json.loads(self.new)['claudeAiOauth'])
                self.clock.advance(putback.PUT_BACK_SPAN_S)


class ManySignOutsAndSignIns(unittest.TestCase):
    """From a fixed seed: Claude Code's sign-out routine, or a sign-in inside it, at made-up paces (the settings
    cleared 5 ms to 12 s after the item went), or its settings file made anew (a small new file that names nobody,
    with no sign-out), after a put-back of ours in an episode with or without proof, maybe followed by a chat's save
    that keeps our login and adds its own part, beside an old plaintext file or none, with Switchboard looking at
    made-up times (the sign-in watch with its hint, or the loop with none). After every call: Switchboard removes a
    login from the item only by taking back its own write (putback._undo's bounds), never once the settings file was
    made anew. At the end: a sign-out leaves no login in the item, and the note of our last write (it lives its
    minute) holds nothing the item holds; a sign-in leaves the new login; a settings file made anew leaves the item's
    login as it was."""

    CASES = 2000
    SEED = 3637

    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        patcher = mock.patch.object(autoloop, 'notify_put_back', lambda res: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        putback.reset()
        putback._KEPT.clear()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home

    def run_case(self, rng):
        tmp = tempfile.mkdtemp(prefix='fresh-proof-many-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        clock = FakeClock(wall=NOW + rng.uniform(0, 10 ** 6), mono=50000.0)
        rig = Rig(wall=clock.wall, mono=clock.mono, sleep=clock.sleep)
        use = context.use(rig.ctx)
        use.__enter__()
        putback.reset()
        putback._KEPT.clear()
        broken, ours, took = [], [], []
        anew = {'on': False}
        try:
            rig.keep('uuid-a', 'ana@work.example', 'org-a')
            clock.advance(1.0)
            rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)
            rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'),
                           projects=dict(('/made-up/p%03d' % n, {'h': ['x' * 80] * 3}) for n in range(80)))
            cred = os.path.join(rig.home, '.claude', '.credentials.json')
            real_write, real_delete = rig.kc.write, rig.kc.delete

            def oauth(text):
                try:
                    return json.loads(text).get('claudeAiOauth') if text else None
                except ValueError:
                    return None

            def taking_back(value=None):
                """A take-back of a write of ours: the settings are the lived-in file and name nobody, and the item
                holds a text we wrote less than TAKE_BACK_S ago (a delete), or its account login (a write without
                one)."""
                cur, cfg = rig.live_text(), rig.config()
                if cur is None or 'oauthAccount' in cfg or not os.path.exists(rig.config_path()) or anew['on']:
                    return False
                recent = [t for t, at in ours if 0 <= clock.wall() - at < putback.TAKE_BACK_S]
                if value is None:
                    return cur in recent
                return (cur in recent or (oauth(cur) is not None and oauth(cur) in [oauth(t) for t in recent])) and \
                    live.parse(value).kind != 'oauth'

            def write(service, account, value, interactive=False, create_only=False):
                if service == rig.service:
                    cur = rig.live_text()
                    if cur and live.parse(cur).kind == 'oauth' and not taking_back(value):
                        broken.append('wrote over a login that is not its own write meeting a sign-out')
                    if taking_back(value):
                        took.append('write')
                done = real_write(service, account, value, interactive=interactive, create_only=create_only)
                if service == rig.service and done.ok:
                    ours.append((value, clock.wall()))
                return done

            def delete(service, account):
                if service == rig.service:
                    if not taking_back():
                        broken.append('removed a login that is not its own write meeting a sign-out')
                    took.append('delete')
                return real_delete(service, account)
            rig.kc.write, rig.kc.delete = write, delete

            snap = {'cred': None}

            def look():
                now_cred = None
                if os.path.exists(cred):
                    with open(cred) as f:
                        now_cred = f.read()
                before, snap['cred'] = snap['cred'], now_cred
                lf = 'appeared' if before is None and now_cred is not None else 'gone' if \
                    before is not None and now_cred is None else 'changed' if before != now_cred else None
                if rng.random() < 0.2:
                    return api.put_back_pass()
                return api.watch_look(dict(sw.no_hint(), keychain=True, login_file=lf,
                                           login_file_written=lf in ('appeared', 'changed')))

            def lose():
                rig.sec.drop(rig.service, rig.account)
                rig.set_live(json.dumps(dict(MCP, discoveryState={'lost': rng.randint(1, 9)}), separators=(',', ':')))

            # before: maybe a put-back of ours (in an episode with proof or without), maybe an old file beside
            if rng.random() < 0.7:
                lose()
                proof = rng.random() < 0.6
                if proof:
                    with open(cred, 'w') as f:
                        f.write(json.dumps(MCP))
                    look()
                    os.unlink(cred)
                for _ in range(40):
                    clock.advance(rng.choice((0.3, 0.6, 1.0, 2.0)))
                    if look().action == 'put_back':
                        break
                clock.advance(rng.uniform(0.0, 8.0))
                if rng.random() < 0.6:
                    lose()                                  # undone again: a quick put-back with proof may follow
                    for _ in range(rng.randint(0, 4)):
                        clock.advance(rng.choice((0.1, 0.3, 0.6)))
                        look()
            if rng.random() < 0.3 and live.parse(rig.live_text() or '').kind == 'oauth':
                with open(cred, 'w') as f:
                    f.write(rig.live_text())
                t = clock.wall() - 3600
                os.utime(cred, (t, t))
            if rng.random() < 0.4 and live.parse(rig.live_text() or '').kind == 'oauth':
                clock.advance(rng.uniform(0.05, 3.0))       # a chat saves its own part; its fresh read kept the login
                blob = json.loads(rig.live_text())
                blob['mcpOAuth'] = dict(blob.get('mcpOAuth') or {}, **{'chat-%d' % rng.randint(1, 9): {'n': 1}})
                rig.set_live(json.dumps(blob, separators=(',', ':')))
                if rng.random() < 0.5:
                    look()
            snap['cred'] = None
            if os.path.exists(cred):
                with open(cred) as f:
                    snap['cred'] = f.read()
            pick = rng.random()
            sign_in, made_anew = pick < 0.35, 0.35 <= pick < 0.55
            settings_at = rng.choice((0.005, 0.3, 0.6, 1.0, 1.5, 3.0, 6.0, 12.0))
            file_at = rng.uniform(0.0, min(0.7, settings_at))
            new = {}

            def store_new():
                new['text'] = rig.server.add('uuid-b', 'ana@home.example', 'org-b')
                blob = json.loads(rig.live_text() or '{}')
                blob.update(json.loads(new['text']))
                rig.set_live(json.dumps(blob, separators=(',', ':')))

            def make_anew():
                new['login'] = oauth(rig.live_text())
                anew['on'] = True
                with open(rig.config_path(), 'w') as f:
                    json.dump({'numStartups': 1}, f)
            if made_anew:
                steps = [(settings_at, make_anew)]
            else:
                steps = [(0.0, lambda: rig.sec.drop(rig.service, rig.account)),
                         (file_at, lambda: os.path.exists(cred) and os.unlink(cred))]
            if sign_in and rng.random() < 0.5:
                steps.append((file_at + 0.001, lambda: rig.set_live(json.dumps(MCP, separators=(',', ':')))))
            if not made_anew:
                steps.append((settings_at, lambda: rig.set_config(None)))
            if sign_in:
                at = settings_at + rng.uniform(0.05, 2.0)
                steps.append((at, store_new))
                steps.append((at + rng.uniform(0.0, 0.5), lambda: rig.set_config(
                    identity_block('uuid-b', 'ana@home.example', 'org-b'))))
            start, end, t = clock.wall(), steps[-1][0] + 6.0, 0.0
            while t <= end:                             # each turn runs a step or moves the clock on by a gap
                gap = rng.choice((0.1, 0.2, 0.5, 1.0))
                if steps and steps[0][0] <= t + gap:
                    at, act = steps.pop(0)
                    clock.advance(max(0.0, at - t))
                    t = max(t, at)
                    act()
                    if rng.random() < 0.7:
                        look()
                else:
                    clock.advance(gap)
                    t += gap
                    look()
                t = max(t, clock.wall() - start)            # a look's own tries move the clock too
            text = rig.live_text()
            if sign_in:
                if not text or json.loads(text).get('claudeAiOauth') != json.loads(new['text'])['claudeAiOauth']:
                    broken.append('a sign-in did not end with the new login')
            elif made_anew:
                if new['login'] is not None and oauth(text) != new['login']:
                    broken.append('a settings file made anew lost the login the item held')
            else:
                if text and live.parse(text).kind == 'oauth':
                    broken.append('a sign-out ended with a login in the item')
                w = putback.load_record(rig.ctx).get('written')
                if w is not None and putback._ours(live.read_live(rig.ctx), w['sha'], w.get('login_sha')):
                    broken.append('a note of ours still describes the item after a sign-out')
            return broken, ('sign_in' if sign_in else 'made_anew' if made_anew else 'sign_out'), bool(took)
        finally:
            use.__exit__(None, None, None)
            rig.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_chosen_sign_out_or_sign_in_always_wins(self):
        kinds, taken = {}, 0
        for case in range(self.CASES):
            broken, kind, took = self.run_case(random.Random(self.SEED * 100003 + case))
            self.assertEqual(broken, [], 'case %d' % case)
            kinds[kind] = kinds.get(kind, 0) + 1
            taken += took
        print('\n%d cases: %s; a write of ours taken back in %d' % (self.CASES, sorted(kinds.items()), taken))
        self.assertGreater(taken, self.CASES // 20)                     # the check reached the take-back
