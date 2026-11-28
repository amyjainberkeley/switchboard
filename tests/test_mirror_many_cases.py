import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The standing copy of the sign-in over many made-up cases (a fixed seed). Each case is a made-up Mac with two kept
# accounts and Claude Code on one, and a random run of what happens there: Claude Code renews its login or saves its
# other parts, a process removes the plain login file, a refused save moves the login to the file and deletes the item
# (Claude Code's fallback), a save by a process whose read found nothing puts the item back and removes the file, the
# item is lost, a switch, the setting turned off and on, Anthropic's refusal struck on the login, and at the end, now
# and then, a chosen sign-out. After every change the sign-in watch tells the hold and looks, as it does. The rules
# checked after each look:
#   (a) a plain login file of ours is owner-only and never holds a login that is blank or without a token, nor one
#       that is struck, unless the item was absent less than HOLD_TIDY_S before (the hold's own rule: the processes
#       told "no such item" then read it; its access token still works);
#   (b) with the setting on, after a look that read an item holding a full, unstruck login of the account the settings
#       name, the plain login file holds exactly the item's text, unless it is Claude Code's own (its fallback, younger
#       than HOLD_TIDY_S, or a login newer than the item's or not told apart by age), or ours with the item's very
#       login and other parts written less than MIRROR_PARTS_S before (a change of those alone waits that long), or no
#       file while REMAKE_BURST copies were already made beside that very login in the last REMAKE_GAP_S;
#   (c) with the setting off, a file of ours beside such an item is one the item was absent less than HOLD_TIDY_S
#       before (the hold's own rule);
#   (d) a chosen sign-out is never undone: from the moment the settings name nobody, no item comes back and no file of
#       ours is there, for TAKE_BACK_S and more; and before that moment nothing is written back without proof of a
#       fault (Claude Code's own fallback write less than FAULT_SEEN_S before: then a write that meets the sign-out
#       is taken back), never before SIGN_OUT_S with the setting on, nor before PUT_BACK_AFTER_S with it off;
#   (e) nothing is lost: some Keychain item or Claude Code's plain login file still holds a refresh token Anthropic
#       takes for the account's sign-in (the fake server's latest).
# The account engine on its stand-ins (fakes_accounts) with a fake clock; made-up *.example accounts. Every time the
# rules compare is read on the engine's own clock (ctx.wall()): the stand-in Anthropic adds each token round trip's
# few milliseconds to it (FakeAnthropic.TRIP_S), so the test's clock alone runs behind what the engine wrote down.
import json
import os
import random
import unittest

from hold_fakes import Base
from switchboard.accounts import live, putback, switcher, vault

SEED = 3737
CASES = 1500
STEPS = 10


class Case(Base):
    MIRROR = True

    # what is there
    def setting(self, on):
        with open(self.ctx.path('settings.json'), 'w') as f:
            json.dump({'auto_policy': {'keep_signin_file': on}}, f)

    def named(self):
        return (self.rig.config().get('oauthAccount') or {}).get('accountUuid')

    def aid(self, uuid):
        return [k for k in self.rig.doc()['accounts'] if k.startswith(uuid)][0]

    def struck(self, text):
        fp = live.parse(text).fingerprint if text else None
        return bool(fp) and any((a.get('strike') or {}).get('fp') == fp for a in self.rig.doc()['accounts'].values())

    def any_valid(self, uuid):
        tok = self.rig.server.valid_refresh(uuid)
        if not tok:
            return True
        if any(tok in v for v in self.rig.sec.items().values()):
            return True
        text = self.file_text()
        return bool(text and tok in text)

    # the watch
    def watch_look(self, after, **hint):
        self.clock.advance(after)
        if 'keychain' not in hint:
            hint['keychain'] = True
        return self.look(**hint)

    def check(self, why, on, signed_out_at=None):
        """The rules above, after a look that read the item."""
        item, text = self.rig.live_text(), self.file_text()
        ours = self.ours()
        tag = '%s (%s)' % (why, self.trace)
        if ours:
            self.assertEqual(os.stat(self.cred).st_mode & 0o777, 0o600, tag)
            filed = live.parse(text)
            self.assertTrue(filed.full_pair, '(a) %s' % tag)
            if self.struck(text):
                note = self.note() or {}
                since = note.get('wrote') if note.get('wrote') is not None else note.get('at')
                self.assertTrue(item is None or (since is not None and
                                                 self.ctx.wall() - since < putback.HOLD_TIDY_S + 1.0), '(a) %s' % tag)
        if signed_out_at is not None:
            self.assertIsNone(item, '(d) the item came back after a sign-out: %s' % tag)
            self.assertFalse(ours, '(d) a file of ours after a sign-out: %s' % tag)
            return
        login = live.parse(item) if item else None
        named = self.named()
        usable = login is not None and login.full_pair and not self.struck(item) and named is not None
        if usable:
            doc = self.rig.doc()
            usable = putback._mirror_owner(login, doc, self.rig.config().get('oauthAccount')) is not None
        if on and usable:
            if text != item and ours:
                st = os.stat(self.cred)
                young = self.ctx.wall() - st.st_mtime_ns / 1e9 < putback.MIRROR_PARTS_S + 1.0
                self.assertTrue(putback._same_login(live.parse(text), login) and young,
                                '(b) a file of ours that is not the item: %s' % tag)
            elif text != item:
                key = putback._login_key(login)
                burst = [t for t, k in putback._REMADE.get(self.ctx.data_dir) or []
                         if k == key and 0 <= self.ctx.wall() - t < putback.REMAKE_GAP_S]
                self.assertTrue(text is not None or len(burst) >= putback.REMAKE_BURST,
                                '(b) no file beside a usable item: %s' % tag)
                if text is not None:
                    st = os.stat(self.cred)
                    young = self.ctx.wall() - st.st_mtime_ns / 1e9 < putback.HOLD_TIDY_S
                    filed = live.parse(text)
                    other = filed.kind == 'oauth' and not putback._one_account(
                        login, filed, self.rig.doc(), self.rig.config().get('oauthAccount'))
                    cmp = live.compare(filed, login) if filed.kind == 'oauth' else 'older'
                    self.assertTrue(young or other or cmp not in ('older', 'same'),
                                    '(b) an old file of Claude Code\'s not adopted: %s' % tag)
        if not on and usable and ours:
            note = self.note() or {}
            since = note.get('wrote') if note.get('wrote') is not None else note.get('at')
            self.assertTrue(since is not None and self.ctx.wall() - since < putback.HOLD_TIDY_S + 1.0,
                            '(c) %s' % tag)

    # one case
    def run_case(self, rnd, n):
        self.trace = []
        on = True
        if rnd.random() < 0.1:
            on = False
            self.setting(False)
        self.watch_look(0.2)
        self.check('start', on)
        for _ in range(STEPS):
            ev = rnd.choice(('renew', 'renew', 'parts', 'removes', 'fallback', 'lands', 'lost', 'switch', 'setting',
                             'strike', 'quiet'))
            self.trace.append(ev)
            if ev == 'renew' and self.rig.live_text():
                self.clock.advance(rnd.uniform(1.0, 9000.0))
                self.rig.claude_renews()
                self.watch_look(rnd.uniform(0.15, 1.0))
            elif ev == 'parts' and self.rig.live_text():
                blob = json.loads(self.rig.live_text())
                blob['mcpOAuth'] = {'docs-server': {'accessToken': 'mcp-made-up-%d' % rnd.randrange(10 ** 6),
                                                    'expiresAt': rnd.randrange(10 ** 6)}}
                self.rig.set_live(json.dumps(blob, separators=(',', ':')))
                self.watch_look(rnd.uniform(0.15, 1.0))
            elif ev == 'removes' and self.file_text() is not None:
                self.cc_removes()
                self.hold('gone')
                self.watch_look(rnd.uniform(0.15, 1.0), login_file='gone')
            elif ev == 'fallback' and self.rig.live_text() and live.parse(self.rig.live_text()).full_pair:
                renewed = self.rig.server.refresh_for(self.rig.live_text())
                had = self.file_text() is not None
                self.cc_writes(renewed)
                self.hold('changed' if had else 'appeared')
                self.clock.advance(rnd.uniform(0.05, 0.4))
                self.cc_deletes_item()
                self.wait_out(rnd, on, login_file='changed' if had else 'appeared', login_file_written=True)
            elif ev == 'lands' and self.file_text() is not None and self.rig.live_text() is None:
                text = self.file_text()
                if live.parse(text).full_pair:
                    self.rig.set_live(text)
                    self.cc_removes()
                    self.hold('gone')
                    self.watch_look(rnd.uniform(0.15, 1.0), login_file='gone')
            elif ev == 'lost' and self.rig.live_text():
                self.cc_deletes_item()
                if rnd.random() < 0.5 and self.file_text() is not None:
                    self.cc_removes()
                    self.hold('gone')
                self.wait_out(rnd, on)
            elif ev == 'switch' and self.rig.live_text() and live.parse(self.rig.live_text()).full_pair:
                target = self.aid('uuid-a') if self.named() == 'uuid-b' else self.aid('uuid-b')
                try:
                    switcher.switch(self.ctx, target)
                except Exception:  # noqa: BLE001 - a refused switch is an outcome like any other
                    pass
                self.hold('settings')
                self.watch_look(rnd.uniform(0.15, 1.0), settings=True)
            elif ev == 'setting':
                on = not on
                self.setting(on)
                self.watch_look(rnd.uniform(0.15, 1.0))
            elif ev == 'strike' and self.rig.live_text() and rnd.random() < 0.3:
                fp = live.parse(self.rig.live_text()).fingerprint
                if fp:
                    with vault.changing(self.ctx) as doc:
                        aid = self.aid(self.named()) if self.named() else None
                        if aid:
                            doc['accounts'][aid]['strike'] = {'fp': fp, 'at': self.ctx.wall()}
                self.watch_look(rnd.uniform(0.15, 1.0))
            else:
                self.watch_look(rnd.uniform(1.0, 400.0))
            self.check(ev, on)
            if self.named():
                self.assertTrue(self.any_valid(self.named()), '(e) %s' % self.trace)
        if rnd.random() < 0.3 and self.named():
            self.sign_out(rnd, on)
        return self.trace

    def wait_out(self, rnd, on, **hint):
        """Looks at the watch's pace until the loss is answered (or 40 s), checking the rules at each."""
        res = self.watch_look(rnd.uniform(0.15, 0.5), **hint)
        t = 0.0
        while res.action == 'wait' and t < 40.0:
            step = res.next_s or 1.0
            t += step
            res = self.watch_look(step)
            self.check('waiting', on)

    def sign_out(self, rnd, on):
        """Claude Code's sign-out: the item and the plain login file deleted (a copy of ours is left behind now and
        then, as when that delete failed), then the settings cleared before the put-back's wait is over."""
        self.trace.append('sign_out')
        self.cc_deletes_item()
        if self.file_text() is not None and (not self.ours() or rnd.random() < 0.8):
            self.cc_removes()
            self.hold('gone')
        start = self.ctx.wall()
        after = rnd.uniform(0.2, (putback.SIGN_OUT_S if on else putback.PUT_BACK_AFTER_S) - 0.5)
        t = 0.0
        res = self.watch_look(0.15)
        while True:
            if res.action in ('put_back', 'promote'):
                proof = putback.fault(putback.load_record(self.ctx), self.ctx.wall())
                self.assertTrue(proof, '(d) written back without proof %.1f s into a sign-out: %s'
                                % (self.ctx.wall() - start, self.trace))
            if t + 1.0 >= after:
                break
            res = self.watch_look(1.0)
            t += 1.0
        self.clock.advance(after - t)
        self.settings_name(None)
        self.hold('settings')
        res = self.watch_look(0.15, settings=True)
        at = self.ctx.wall()
        for _ in range(int(putback.TAKE_BACK_S / 5) + 3):
            self.check('signed out', on, signed_out_at=at)
            res = self.watch_look(5.0)
        self.check('signed out', on, signed_out_at=at)


class ManyCases(unittest.TestCase):
    def test_the_rules_hold(self):
        rnd = random.Random(SEED)
        counts = {}
        for n in range(CASES):
            case = Case('run_case')                     # not a test of its own: each case runs from here
            case.setUp()
            try:
                trace = case.run_case(random.Random(rnd.random()), n)
                for ev in trace:
                    counts[ev] = counts.get(ev, 0) + 1
            except AssertionError as e:
                raise AssertionError('case %d: %s' % (n, e))
            finally:
                case.tearDown()
        self.assertGreater(counts.get('sign_out', 0), CASES // 10)
        self.assertGreater(counts.get('fallback', 0), CASES // 2)


if __name__ == '__main__':
    unittest.main()
