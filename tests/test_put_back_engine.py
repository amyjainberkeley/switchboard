import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Putting Claude Code's sign-in back (putback.look) across the account engine, on its stand-ins only (fakes_accounts:
# an in-process Keychain, a made-up home, a made-up Anthropic) and a fake clock. Made-up *.example accounts.
#
# What Claude Code does and these tests act out: after a switch many Claude Code processes save the whole Keychain
# item at once; one failed save makes Claude Code delete the item, and another process saves it back holding only
# connector logins. Its settings still name the account. A chosen sign-out deletes the item and clears the settings'
# account.
import json
import os
import random
import shutil
import tempfile
import time
import unittest

from fakes_autoswitch import FakeClock
from fakes_accounts import Rig, identity_block
from switchboard.accounts import api, context, live, putback, roster, switcher, vault

MCP = {'mcpOAuth': {'docs-server': {'accessToken': 'mcp-made-up-1', 'expiresAt': 1}}}


class Base(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='put-back-engine-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, mono=self.clock.mono, sleep=self.clock.sleep)
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        putback.reset()
        self.rig.keep('uuid-a', 'ana@work.example', 'org-a')
        self.clock.advance(1.0)                 # two sign-ins never share an end stamp to the millisecond
        self.b_text = self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)   # Claude Code is on B
        self.saved_b = self.rig.vault('uuid-b')

    def tearDown(self):
        self.use.__exit__(None, None, None)
        putback.reset()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    # Claude Code's side

    def lose_login(self, siblings=True):
        """What many processes saving at once can leave: the item deleted, then saved again without the login."""
        self.rig.sec.drop(self.rig.service, self.rig.account)
        if siblings:
            self.rig.set_live(json.dumps(dict(MCP, discoveryState={'made-up': 1}), separators=(',', ':')))

    def sign_out(self):
        """/logout: the item deleted, then the settings' account cleared."""
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.set_config(None)

    # what happened

    def live_login(self):
        text = self.rig.live_text()
        return live.parse(text) if text else None

    def live_writes(self):
        return sum(1 for c in self.rig.kc.calls if c[0] == 'write' and c[1] == self.rig.service)

    def repairs(self):
        return [e for e in self.rig.events if e[2].get('h') == 'repair']

    def look(self, after=5.0):
        self.clock.advance(after)
        return putback.look(self.ctx)

    def secrets(self):
        out = set()
        for value in self.rig.sec.items().values():
            try:
                blob = json.loads(value)
            except ValueError:
                continue
            for o in ([blob.get('claudeAiOauth')] + [g for g in (blob.get('generations') or [])]):
                if isinstance(o, dict):
                    out.update(v for k, v in o.items() if k in ('accessToken', 'refreshToken') and v)
        return out


class TheLostLogin(Base):
    def test_the_login_is_put_back_on_the_second_look_with_the_connector_logins_kept(self):
        self.lose_login()
        first = self.look(0)
        self.assertEqual((first.action, first.watch), ('wait', True))
        self.assertEqual(self.live_writes(), 0)
        res = self.look(putback.PUT_BACK_AFTER_S + 1)
        self.assertEqual((res.action, res.account), ('put_back', 'ana@home.example'), res.reason)
        now = self.live_login()
        self.assertEqual(now.refresh_token, live.parse(self.saved_b).refresh_token)
        self.assertEqual(json.loads(self.rig.live_text())['mcpOAuth'], MCP['mcpOAuth'])   # the item's own, as they were
        self.assertEqual(self.rig.vault('uuid-b'), self.saved_b)                         # the saved login unchanged
        self.assertEqual(len(self.repairs()), 1)
        text, kind, facts = self.repairs()[0]
        self.assertEqual(text, 'Claude Code lost its sign-in; Switchboard put ana@home.example back')
        self.assertEqual((facts.get('account'), facts.get('by')), ('ana@home.example', 'auto'))
        self.assertEqual(res.line, text)
        self.assertEqual(api.live().account_id, 'uuid-b')                           # the page sees it at once
        self.assertEqual(putback.last_put_back(self.ctx)['account'], 'ana@home.example')

    def test_with_no_item_at_all_the_saved_copy_is_written_whole_with_its_connector_logins(self):
        # A login saved here keeps no connector logins (vault.saved_text: a switch takes those from the live item), so
        # the saved copy written whole brings the account login back alone; a copy an older build kept with its
        # connector logins brings those back too.
        self.lose_login(siblings=False)
        self.look(0)
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(json.loads(self.rig.live_text()), json.loads(self.saved_b))     # no live object: written whole
        older = live.dump(dict(json.loads(self.saved_b), **MCP))
        self.rig.sec.put('Switchboard login', 'uuid-b', older)
        self.lose_login(siblings=False)
        self.look(0)
        res = self.look(putback.PUT_BACK_WAITS[1] + 1)
        self.assertEqual(res.action, 'put_back', res.reason)
        blob = json.loads(self.rig.live_text())
        self.assertEqual(blob['claudeAiOauth'], json.loads(self.saved_b)['claudeAiOauth'])
        self.assertEqual(blob['mcpOAuth'], MCP['mcpOAuth'])     # no live object: nothing to keep, as a switch does it

    def test_an_item_without_a_login_keeps_its_own_connector_state_as_it_is(self):
        self.lose_login(siblings=False)
        self.rig.set_live(json.dumps({'discoveryState': {'made-up': 1}}, separators=(',', ':')))
        self.look(0)
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        blob = json.loads(self.rig.live_text())
        self.assertEqual(blob['claudeAiOauth'], json.loads(self.saved_b)['claudeAiOauth'])
        self.assertNotIn('mcpOAuth', blob)            # the item's machine-wide parts as they are now: none

    def test_after_a_put_back_the_next_looks_change_nothing_and_say_nothing(self):
        self.lose_login()
        self.look(0)
        self.look()
        writes, events = self.live_writes(), len(self.rig.events)
        for _ in range(20):
            res = self.look()
            self.assertEqual(res.action, 'leave')
            self.assertIsNone(res.line)
        self.assertEqual((self.live_writes(), len(self.rig.events)), (writes, events))

    def test_a_login_present_is_one_keychain_read_per_look(self):
        before = len(self.rig.kc.calls)
        res = self.look()
        self.assertEqual((res.action, res.watch), ('leave', False))
        self.assertEqual(self.rig.kc.calls[before:], [('read', self.rig.service, self.rig.account)])

    def test_no_token_reaches_a_log_line_a_history_line_or_what_the_loop_is_told(self):
        lines = []
        self.lose_login()
        for _ in range(3):
            res = self.look()
            lines.append(res.line or '')
            lines.append(res.reason or '')
        texts = lines + list(self.rig.logs) + [e[0] for e in self.rig.events] + \
            [json.dumps(e[2]) for e in self.rig.events]
        self.assertTrue(self.secrets())
        for secret in self.secrets() | {'mcp-made-up-1', 'sk-ant-'}:
            for t in texts:
                self.assertNotIn(secret, t)


class NeverOverASignOutOrASignIn(Base):
    def test_a_chosen_sign_out_is_never_undone_however_many_looks_follow(self):
        self.sign_out()
        for _ in range(200):
            res = self.look()
            self.assertEqual(res.action, 'leave')
        self.assertEqual(self.live_writes(), 0)
        self.assertIsNone(self.rig.live_text())
        self.assertEqual(self.repairs(), [])

    def test_a_chosen_sign_out_with_an_unreadable_record_is_not_called_a_lost_sign_in(self):
        with open(self.ctx.path(putback.STATE), 'w') as f:
            f.write('not a record')
        self.sign_out()
        for _ in range(5):
            res = self.look()
            self.assertEqual(res.action, 'leave')
            self.assertIsNone(res.line)
        self.assertFalse([t for t in self.rig.logs if 'lost its sign-in' in t or 'None' in t], self.rig.logs)
        self.assertEqual(self.live_writes(), 0)

    def test_a_lost_login_with_an_unreadable_record_is_left_and_said_once_with_the_whole_email(self):
        os.makedirs(self.ctx.path(putback.STATE))     # there, and can't be read as a file
        self.lose_login()
        res = [self.look() for _ in range(4)]
        self.assertEqual([r.action for r in res], ['leave'] * 4)
        said = [r.line for r in res if r.line]
        self.assertEqual(len(said), 1, said)
        self.assertIn('ana@home.example', said[0])
        self.assertIn(putback.STATE, said[0])
        self.assertEqual(self.live_writes(), 0)

    def test_a_record_that_is_not_one_is_started_again_once_and_the_login_put_back(self):
        with open(self.ctx.path(putback.STATE), 'w') as f:
            f.write('not a record')
        self.lose_login()
        self.assertEqual(self.look(0).action, 'wait')
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        with open(self.ctx.path(putback.STATE)) as f:
            self.assertEqual(len(json.load(f)['done']), 1)
        self.assertEqual(len([t for t in self.rig.logs if 'could not be understood' in t]), 1)

    def test_a_sign_out_whose_settings_change_a_moment_after_the_item_goes_is_not_undone(self):
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.look(0)                                   # the item is gone; the settings still name B
        self.clock.advance(1.0)
        self.rig.set_config(None)                      # the sign-out's next step
        for _ in range(50):
            self.look()
        self.assertEqual(self.live_writes(), 0)

    def test_a_sign_in_under_way_in_claude_code_is_never_raced(self):
        self.lose_login()
        self.look(0)
        self.look(2.0)                                 # missing for 2 s: not yet
        new = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')     # Claude Code writes its new login
        self.rig.set_live(new)
        for _ in range(10):
            self.look()
        self.assertEqual(self.live_writes(), 0)
        self.assertEqual(self.rig.live_text(), new)

    def test_a_login_that_appears_between_the_decision_and_the_write_is_left_alone(self):
        self.lose_login()
        self.look(0)
        new = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')
        reads = []

        def on_call(verb, service, account):
            if verb == 'read' and service == vault.SERVICE_CURRENT and account == 'uuid-b':
                reads.append(1)
                if len(reads) == 2:                    # the second read of the saved login is under the locks
                    self.rig.set_live(new)
        self.rig.kc.on_call = on_call
        res = self.look()
        self.assertEqual(len(reads), 2, res)
        self.assertNotEqual(res.action, 'put_back')
        self.assertEqual(self.live_writes(), 0)
        self.assertEqual(self.rig.live_text(), new)

    def test_a_login_that_appears_just_before_the_last_read_is_left_alone(self):
        self.lose_login()
        self.look(0)
        new = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')
        live_reads = []

        def on_call(verb, service, account):
            if verb == 'read' and service == self.rig.service:
                live_reads.append(1)
                if len(live_reads) == 3:              # outside the locks, under them, then last before the write
                    self.rig.set_live(new)
        self.rig.kc.on_call = on_call
        res = self.look()
        self.assertGreaterEqual(len(live_reads), 3, res)
        self.assertEqual(self.live_writes(), 0)
        self.assertEqual(self.rig.live_text(), new)

    def test_a_sign_in_under_way_on_the_page_is_never_raced(self):
        folder = os.path.join(self.rig.data, 'logins', 'made-up-sign-in')
        os.makedirs(folder)
        os.utime(folder, (self.clock.wall(), self.clock.wall()))
        self.lose_login()
        for _ in range(5):
            self.assertEqual(self.look().action, 'leave')
        self.assertEqual(self.live_writes(), 0)
        open(os.path.join(folder, 'over'), 'w').close()      # handed over, cancelled or timed out
        self.look()
        self.assertEqual(self.look().action, 'put_back')

    def test_a_switch_part_way_is_left_to_settle_first(self):
        switcher._write_journal(self.ctx, {'id': 'made-up', 'step': 'writing', 'from_id': 'uuid-a', 'to_id': 'uuid-b'})
        self.lose_login()
        for _ in range(3):
            res = self.look()
            self.assertEqual((res.action, res.watch), ('leave', True))
        self.assertEqual(self.live_writes(), 0)

    def test_a_login_in_claude_codes_login_file_is_never_written_over(self):
        path = os.path.join(self.rig.home, '.claude', '.credentials.json')
        with open(path, 'w') as f:
            f.write(self.rig.server.add('uuid-b', 'ana@home.example', 'org-b'))
        self.lose_login()
        res = [self.look() for _ in range(4)]
        self.assertEqual([r.action for r in res], ['leave'] * 4)
        self.assertEqual(len([r for r in res if r.line]), 1)
        self.assertEqual(self.live_writes(), 0)


class TheSettingsRightAfterASwitch(Base):
    """A Claude Code session open during a switch can write the earlier account's name back into the settings. Right
    after a switch away from the account the settings name, that name is not taken as the account to put back."""

    def switch_to_a(self):
        res = switcher.switch(self.ctx, 'uuid-a', source='auto', kind='forced')
        self.assertTrue(res.ok)
        roster.forget_live()

    def test_settings_naming_the_account_just_left_never_get_that_accounts_login(self):
        self.switch_to_a()
        self.rig.set_config(identity_block('uuid-b', 'ana@home.example', 'org-b'))    # the earlier name, written back
        writes = self.live_writes()                    # the switch's own
        self.lose_login()
        res = [self.look() for _ in range(40)]
        self.assertEqual(set(r.action for r in res), {'leave'})
        said = [r.line for r in res if r.line]
        self.assertEqual(len(said), 1, said)
        self.assertIn('ana@home.example', said[0])
        self.assertEqual(self.live_writes(), writes)
        self.assertEqual(self.repairs(), [])
        self.assertEqual(self.look(switcher_window()).action, 'leave')   # the same loss, however long it lasts
        self.assertEqual(self.live_writes(), writes)

    def test_settings_naming_the_account_switched_to_get_it_back(self):
        self.switch_to_a()
        self.lose_login()
        self.look(0)
        res = self.look()
        self.assertEqual((res.action, res.account), ('put_back', 'ana@work.example'), res.reason)
        self.assertEqual(self.live_login().refresh_token, live.parse(self.rig.vault('uuid-a')).refresh_token)

    def test_a_loss_long_after_the_switch_goes_by_the_settings(self):
        self.switch_to_a()
        self.rig.sign_in('uuid-b', 'ana@home.example', 'org-b')     # later, a /login of B in Claude Code
        self.clock.advance(switcher_window())
        roster.add_current(self.ctx)                                # and the page's Add keeps it
        self.lose_login()
        self.look(0)
        res = self.look()
        self.assertEqual((res.action, res.account), ('put_back', 'ana@home.example'), res.reason)


def switcher_window():
    return putback.SWITCHED_AWAY_S + 1.0


class WhenTheSavedLoginCannotBeUsed(Base):
    def check_refused(self, words):
        self.lose_login()
        res = [self.look() for _ in range(6)]
        self.assertEqual([r.action for r in res], ['leave'] * 6)
        said = [r.line for r in res if r.line]
        self.assertEqual(len(said), 1, said)
        self.assertIn('ana@home.example', said[0])
        self.assertIn(words, said[0])
        self.assertEqual(self.live_writes(), 0)
        self.assertEqual(self.repairs(), [])
        self.assertIn(said[0], self.rig.logs)
        return said[0]

    def mark(self, **fields):
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-b'].update(fields)

    def test_refused(self):
        self.mark(strike={'fp': live.parse(self.saved_b).fingerprint, 'at': self.clock.wall()})
        self.check_refused('refused by Anthropic')

    def test_ended(self):
        self.mark(rx_ms=int((self.clock.wall() - 60) * 1000))
        self.check_refused('end date')

    def test_missing(self):
        self.rig.sec.drop(vault.SERVICE_CURRENT, 'uuid-b')
        self.check_refused('is not in the Keychain')

    def test_unreadable(self):
        self.rig.kc.rule('read', service=vault.SERVICE_CURRENT, account='uuid-b', why='locked', times=-1)
        self.check_refused("can't be read")

    def test_in_conflict(self):
        self.mark(conflict={'profile_uuid': 'uuid-z', 'profile_email': 'zed@else.example', 'at': self.clock.wall()})
        self.check_refused('another account')

    def test_held_by_a_run_as(self):
        with vault.changing(self.ctx) as doc:
            doc['claims']['uuid-b'] = {'pid': os.getpid(), 'start': 'self-start'}
        self.check_refused('switchboard run')

    def test_once_the_mark_is_gone_it_is_put_back(self):
        self.mark(conflict={'profile_uuid': 'uuid-z', 'at': self.clock.wall()})
        self.check_refused('another account')
        with vault.changing(self.ctx) as doc:
            doc['accounts']['uuid-b'].pop('conflict', None)
        self.assertEqual(self.look().action, 'put_back')


class TheLimitAndTheSpacing(Base):
    def until_put_back(self, looks=200):
        """Look every 5 s until a put-back; (the verdict, seconds from the first look that saw it missing)."""
        first = None
        for _ in range(looks):
            res = self.look()
            if res.action in ('wait', 'put_back') and first is None:
                first = self.clock.wall()
            if res.action != 'wait':
                return res, (self.clock.wall() - first) if first is not None else None
        self.fail('no put-back within %d looks' % looks)

    def test_a_put_back_undone_at_once_waits_longer_each_time_and_the_login_stays_back(self):
        waits = []
        for n in range(2):
            self.lose_login()
            res, waited = self.until_put_back()
            self.assertEqual(res.action, 'put_back', res.reason)
            waits.append(waited)
            self.clock.advance(2.0)                     # processes that read while it was gone save it again
        self.lose_login()
        res, waited = self.until_put_back()
        self.assertEqual(res.action, 'put_back')
        waits.append(waited)
        for _ in range(30):
            self.assertEqual(self.look().action, 'leave')
        self.assertEqual(self.live_writes(), 3)
        self.assertEqual(len(self.repairs()), 3)
        for got, want in zip(waits, putback.PUT_BACK_WAITS):
            self.assertGreaterEqual(got, want)
            self.assertLess(got, want + 5.0 + 0.01)
        self.assertEqual(putback.PUT_BACK_WAITS, (putback.PUT_BACK_AFTER_S, 15.0, 60.0, 300.0))

    def test_four_put_backs_then_one_line_then_nothing_until_the_hour_has_passed(self):
        for _ in range(putback.PUT_BACK_MAX):
            self.lose_login()
            res, _ = self.until_put_back()
            self.assertEqual(res.action, 'put_back', res.reason)
        self.lose_login()
        res, _ = self.until_put_back()
        self.assertEqual((res.action, res.line), ('limit', putback.LIMIT_WORDS))
        self.assertEqual(putback.LIMIT_WORDS, 'Claude Code keeps losing its sign-in. Switchboard has stopped putting '
                                              'it back for now. Run /login in Claude Code once.')
        problems = [e for e in self.rig.events if e[0] == putback.LIMIT_WORDS]
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][2].get('h'), 'problem')
        writes = self.live_writes()
        for _ in range(100):
            res = self.look(20)
            self.assertIsNone(res.line)
        self.assertEqual(self.live_writes(), writes)
        self.assertEqual(len([e for e in self.rig.events if e[0] == putback.LIMIT_WORDS]), 1)
        res = self.look(putback.PUT_BACK_SPAN_S)        # still missing all along: the first look once the hour is over
        self.assertEqual(res.action, 'put_back')

    def test_the_limit_reached_again_after_a_put_back_lands_is_said_again(self):
        for _ in range(putback.PUT_BACK_MAX):
            self.lose_login()
            self.assertEqual(self.until_put_back()[0].action, 'put_back')
        self.lose_login()
        self.assertEqual(self.until_put_back()[0].action, 'limit')
        for _ in range(1000):                          # the first put-back leaves the hour: one more may land
            res = self.look()
            if res.action not in ('limit', 'wait'):
                break
        self.assertEqual(res.action, 'put_back', res.reason)
        self.lose_login()
        res = self.until_put_back()
        self.assertEqual((res[0].action, res[0].line), ('limit', putback.LIMIT_WORDS))
        self.assertEqual(len([e for e in self.rig.events if e[0] == putback.LIMIT_WORDS]), 2)

    def test_two_callers_share_one_limit_and_one_first_sighting(self):
        self.lose_login()
        self.look(0)                                   # one process sees it missing
        putback.reset()                                # another process, with nothing in memory
        self.assertEqual(self.look().action, 'put_back')
        for n in range(putback.PUT_BACK_MAX - 1):
            self.lose_login()
            for i in range(200):
                putback.reset()                        # every look by another process
                res = self.look()
                if res.action != 'wait':
                    break
            self.assertEqual(res.action, 'put_back', res.reason)
        self.lose_login()
        for i in range(200):
            putback.reset()
            res = self.look()
            if res.action != 'wait':
                break
        self.assertEqual(res.action, 'limit')
        self.assertEqual(self.live_writes(), putback.PUT_BACK_MAX)


class TheTries(Base):
    def test_two_writes_refused_then_one_lands_one_put_back_counted_one_line(self):
        self.lose_login()
        self.look(0)
        self.rig.kc.rule('write', service=self.rig.service, why='unknown', times=2)
        res = self.look()
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(self.live_writes(), 3)
        self.assertEqual(len(putback.load_record(self.ctx)['done']), 1)
        self.assertEqual(len(self.repairs()), 1)
        self.assertEqual(self.live_login().refresh_token, live.parse(self.saved_b).refresh_token)

    def test_tries_stop_at_once_when_a_login_is_there_after_a_refused_write(self):
        self.lose_login()
        self.look(0)
        self.rig.kc.rule('write', service=self.rig.service, why='unknown', times=1)
        new = self.rig.server.add('uuid-b', 'ana@home.example', 'org-b')
        real = self.rig.kc.write

        def write(service, account, value, interactive=False):
            got = real(service, account, value, interactive)
            if service == self.rig.service and not got.ok:
                self.rig.set_live(new)                 # Claude Code saved its own login meanwhile
            return got
        self.rig.kc.write = write
        res = self.look()
        self.assertNotEqual(res.action, 'put_back')
        self.assertEqual(self.live_writes(), 1)
        self.assertEqual(self.rig.live_text(), new)
        self.assertEqual(putback.load_record(self.ctx)['done'], [])

    def test_every_write_refused_counts_nothing_says_one_line_once_and_the_next_look_tries_again(self):
        self.lose_login()
        self.look(0)
        self.rig.kc.rule('write', service=self.rig.service, why='unknown', times=putback.TRIES)
        self.clock.advance(5.0)
        t0 = self.clock.mono()
        first = putback.look(self.ctx)
        self.assertLessEqual(self.clock.mono() - t0, putback.TRIES_WITHIN_S)
        self.assertEqual(first.action, 'failed')
        self.assertEqual(self.live_writes(), putback.TRIES)
        self.assertIsNotNone(first.line)
        self.assertNotIn('sk-ant-', first.line)
        self.assertEqual(putback.load_record(self.ctx)['done'], [])
        self.assertEqual(self.repairs(), [])
        self.rig.kc.rule('write', service=self.rig.service, why='unknown', times=putback.TRIES)
        second = self.look()
        self.assertEqual(second.action, 'failed')
        self.assertIsNone(second.line)                 # said once
        for _ in range(10):
            res = self.look()
            if res.action != 'wait':
                break
        self.assertEqual(res.action, 'put_back')
        self.assertEqual(len(putback.load_record(self.ctx)['done']), 1)
        self.assertEqual(len(self.repairs()), 1)

    def test_writes_that_never_land_are_tried_less_often_each_time_and_said_once(self):
        self.lose_login()
        self.look(0)
        self.rig.kc.rule('write', service=self.rig.service, why='denied', times=-1)
        res = [self.look() for _ in range(60)]         # five minutes of looks, 5 s apart
        failed = [r for r in res if r.action == 'failed']
        self.assertEqual(len(failed), len(putback.PUT_BACK_WAITS))     # tries at 0, 4, 15 and 60 s after a failure
        self.assertEqual(self.live_writes(), len(failed) * putback.TRIES)
        self.assertEqual(len([r for r in res if r.line]), 1)
        self.assertTrue(all(r.watch for r in res))
        self.assertEqual(putback.load_record(self.ctx)['done'], [])
        del self.rig.kc.rules[:]                       # the Keychain takes writes again
        for _ in range(70):
            res = self.look()
            if res.action != 'wait':
                break
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(putback.load_record(self.ctx)['failed'], [])


class TheTriesThroughTheKeychainWrapper(unittest.TestCase):
    """The real Keychain wrapper over the suite's stand-in tool: a refused write makes the item rest 30 s, and a plain
    read during the rest answers "unreadable" without asking. The tries must read through it all the same."""

    def setUp(self):
        self._old_home = os.environ.get('SWITCHBOARD_HOME')
        self.tmp = tempfile.mkdtemp(prefix='put-back-wrapper-')
        os.environ['SWITCHBOARD_HOME'] = os.path.join(self.tmp, 'data')
        os.makedirs(os.environ['SWITCHBOARD_HOME'])
        self.clock = FakeClock(wall=time.time(), mono=time.monotonic())
        self.rig = Rig(wall=self.clock.wall, sleep=self.clock.sleep, real_keychain=True)
        self.use = context.use(self.rig.ctx)
        self.use.__enter__()
        putback.reset()

    def tearDown(self):
        self.use.__exit__(None, None, None)
        putback.reset()
        self.rig.close()
        if self._old_home is None:
            os.environ.pop('SWITCHBOARD_HOME', None)
        else:
            os.environ['SWITCHBOARD_HOME'] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_two_writes_answered_45_then_one_that_lands(self):
        self.rig.keep('uuid-b', 'ana@home.example', 'org-b', siblings=MCP)
        saved = self.rig.vault('uuid-b')
        self.rig.sec.drop(self.rig.service, self.rig.account)
        self.rig.sec.put(self.rig.service, self.rig.account, json.dumps(MCP, separators=(',', ':')))
        self.assertEqual(putback.look(self.rig.ctx).action, 'wait')
        self.clock.advance(putback.PUT_BACK_AFTER_S + 1)
        self.rig.sec.rule(verb='add', service=self.rig.service, exit=45, times=2,
                          stderr='security: SecKeychainItemCreateFromContent: The specified item already exists in '
                                 'the keychain.\n')
        res = putback.look(self.rig.ctx)
        self.assertEqual(res.action, 'put_back', res.reason)
        self.assertEqual(len([c for c in self.rig.sec.calls('add') if c['service'] == self.rig.service]), 3)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth'], json.loads(saved)['claudeAiOauth'])
        self.assertEqual(len(putback.load_record(self.rig.ctx)['done']), 1)
        self.assertEqual(len([e for e in self.rig.events if e[2].get('h') == 'repair']), 1)


class ManyMadeUpInterleavings(Base):
    """From a fixed seed: the item erased, the item saved without a login, a chosen sign-out, a sign-in in Claude Code,
    a switch, a look, in any order and at any pace. After every step: (a) the put-back never wrote over an account
    login; (b) after a chosen sign-out nothing is written until someone signs in; (c) what it writes is always the
    saved login of the account the settings name; (d) never more than four writes in any hour; (e) no token appears in
    a log line, a History line or what the loop is told."""

    SEQUENCES = 150
    STEPS = 24
    SEED = 4242
    KINDS = ('erase', 'no_login', 'sign_out', 'sign_in', 'switch', 'look', 'look', 'look')   # one drawn per step
    PACES = (0.5, 2.0, 4.0, 5.0, 5.0, 30.0, 400.0)    # seconds between two steps, one drawn per step
    BUSIEST_AT_LEAST = 0                            # writes the busiest hour must reach, so (d) is put to the test

    def test_the_rules_hold_over_many_interleavings(self):
        rng = random.Random(self.SEED)
        busiest = [0]
        accounts = (('uuid-a', 'ana@work.example', 'org-a'), ('uuid-b', 'ana@home.example', 'org-b'))
        told = []
        looked = {'in': False}
        writes = []
        state = {'signed_out': False}
        problems = []

        def on_call(verb, service, account):
            if verb != 'write' or service != self.rig.service or not looked['in']:
                return
            now_text = self.rig.live_text()
            if now_text and live.parse(now_text).kind not in putback.NO_LOGIN:
                problems.append('(a) wrote over a login')
            if state['signed_out']:
                problems.append('(b) wrote after a chosen sign-out')
            block = self.rig.config().get('oauthAccount') or {}
            writes.append((self.clock.wall(), block.get('accountUuid')))
            hour = len([t for t, _ in writes if self.clock.wall() - t < putback.PUT_BACK_SPAN_S])
            busiest[0] = max(busiest[0], hour)
            if hour > putback.PUT_BACK_MAX:
                problems.append('(d) more than four writes in an hour')

        self.rig.kc.on_call = on_call
        for n in range(self.SEQUENCES):
            for _ in range(self.STEPS):
                step = rng.choice(self.KINDS)
                if step == 'erase':
                    self.rig.sec.drop(self.rig.service, self.rig.account)
                elif step == 'no_login':
                    self.rig.set_live(json.dumps(MCP))
                elif step == 'sign_out':
                    self.sign_out()
                    state['signed_out'] = True
                elif step == 'sign_in':
                    uuid, email, org = rng.choice(accounts)
                    self.rig.sign_in(uuid, email, org)
                    state['signed_out'] = False
                elif step == 'switch':
                    target = rng.choice(accounts)[0]
                    try:
                        switcher.switch(self.ctx, target, kind='manual', source='page')
                        state['signed_out'] = False
                    except api.AccountsError:
                        pass
                    roster.forget_live()
                else:
                    looked['in'] = True
                    before = len(writes)
                    try:
                        res = putback.look(self.ctx)
                    finally:
                        looked['in'] = False
                    told.extend(x for x in (res.line, res.reason) if x)
                    if res.action == 'put_back':
                        block = self.rig.config().get('oauthAccount') or {}
                        aid = block.get('accountUuid')
                        got = json.loads(self.rig.live_text())['claudeAiOauth']
                        if got != json.loads(self.rig.vault(aid))['claudeAiOauth']:
                            problems.append('(c) wrote something other than the saved login of %s' % aid)
                        if writes[-1][1] != aid:
                            problems.append('(c) the settings named another account at the write')
                    elif len(writes) > before and res.action != 'failed':
                        problems.append('a look that did not put back wrote (%s)' % res.action)
                self.clock.advance(rng.choice(self.PACES))
                self.assertEqual(problems, [], 'sequence %d' % n)
        texts = told + list(self.rig.logs) + [e[0] for e in self.rig.events] + \
            [json.dumps(e[2]) for e in self.rig.events]
        for t in texts:
            self.assertNotIn('sk-ant-', t)                 # every made-up token starts so, access and refresh alike
            self.assertNotIn('mcp-made-up', t)
        self.assertGreater(len(writes), 10, 'the sequences made too few put-backs to prove anything')
        self.assertGreaterEqual(busiest[0], self.BUSIEST_AT_LEAST, 'no hour came near the limit')


class ManyQuickInterleavings(ManyMadeUpInterleavings):
    """The same rules with mostly looks and losses, never more than two minutes apart, so a lost login comes back
    again and again within one hour and the busiest hour reaches the limit: (d) can fail here."""

    SEED = 9090
    KINDS = ('erase', 'no_login', 'erase', 'sign_out', 'sign_in', 'switch') + ('look',) * 10
    PACES = (0.5, 2.0, 5.0, 5.0, 15.0, 60.0, 120.0)
    BUSIEST_AT_LEAST = putback.PUT_BACK_MAX


if __name__ == '__main__':
    unittest.main()
