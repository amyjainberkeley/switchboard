"""Many made-up cases from a fixed seed: one sign-in whose stamp moves at each renewal never costs an account its login.

Each case keeps 2 to 5 accounts whose sign-ins end at random stamps (some seconds apart, some days apart), signed in
in the order of their stamps, the last one in use. Then a random run of events: Claude Code renews the live login
(its stamp moves by a random amount in [-30 s, +30 s] under Claude Code's min rule), a keep pass, an upkeep pass, a
switch to another account, a new sign-in of the account in use, Claude Code's settings naming another kept account
(an open session writes the earlier name back), and a first sign-in of an account not kept yet (its stamp often a
little under a kept account's, and its settings sometimes naming that account). After every event:
(a) no token POST carried a refresh token of the account in use, or one Claude Code had already used up;
(b) after a keep pass, the saved copy of the account in use is the live login;
(c) no account is marked refused;
(d) no slot holds another account's login.
With every move at zero only exact stamps are equal: the existing keep, refresh and switch test files check those
decisions, unchanged.
"""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import random
import time
import unittest

from fakes_accounts import identity_block
from same_signin_fakes import DAY_MS, email_of, keep_at, new_rig, org_of, refresh_token, renew, sign_in_at
from switchboard.accounts import capture, live, switcher, tokens
from switchboard.accounts.api import AccountsError

SEED = 7741
LATEST_MS = 30 * DAY_MS
CASES = 1000
EVENTS = 8
TAG = 'sk-ant-ort01-'


def owner_of(token):
    """The made-up account a refresh token was issued to (the stand-in puts the account id's first 8 characters in
    every token it issues; the ids here are 8 characters long)."""
    return token[len(TAG):len(TAG) + 8] if token and token.startswith(TAG) else None


class ManyCases(unittest.TestCase):
    def test_no_case_breaks_a_rule(self):
        rnd = random.Random(SEED)
        started = time.time()
        counts = {}
        for n in range(CASES):
            self.one_case(rnd, n, counts)
        took = time.time() - started
        self.assertGreater(counts.get('renew', 0), CASES)
        self.assertGreater(counts.get('switch', 0), CASES // 4)
        self.assertGreater(counts.get('saved_by_keep', 0), CASES // 4)
        self.assertGreater(counts.get('idle_renewed', 0), CASES // 8)
        print('\n%d cases, %s, %.0f s' % (CASES, sorted(counts.items()), took))

    def one_case(self, rnd, n, counts):
        rig, clock = new_rig()
        try:
            self.run_case(rig, clock, rnd, n, counts)
        finally:
            end = time.time() + 20
            while tokens._INFLIGHT and time.time() < end:
                time.sleep(0.05)
            rig.close()

    def run_case(self, rig, clock, rnd, n, counts):
        k = rnd.randint(2, 5)
        ids = ['acct%04d' % i for i in range(k)]
        # every stamp lies before now + LATEST_MS, the latest sign-in last; the gaps between sign-ins are seconds (one
        # sitting) or days
        stamps, cur = [], int(time.time() * 1000) + LATEST_MS - rnd.randint(60, 3 * 86400) * 1000
        for _ in ids:
            stamps.insert(0, cur)
            cur -= rnd.randint(1, 90) * 1000 + rnd.randint(0, 999) if rnd.random() < 0.6 else \
                rnd.randint(1, 4) * DAY_MS + rnd.randint(0, DAY_MS)
        for aid, rx in zip(ids, stamps):                # signed in in the order of their stamps
            keep_at(rig, aid, rx)
            clock.advance(rnd.randint(1, 30))
        in_use = ids[-1]
        raw = []
        rig.server.on_post = raw.append
        used_by_claude_code = set()
        story = ['case %d: %s' % (n, ', '.join('%s@%+.1fs' % (a, (s - stamps[0]) / 1000.0)
                                                for a, s in zip(ids, stamps)))]

        def check(event, seen, was_in_use):
            what = '\n'.join(story)
            for rt in raw[seen:]:
                self.assertNotEqual(owner_of(rt), was_in_use, 'a POST with the in-use account\'s token after %s\n%s'
                                    % (event, what))
                self.assertNotIn(rt, used_by_claude_code, 'a POST with a used-up token after %s\n%s' % (event, what))
            doc = rig.doc()
            for aid, a in doc['accounts'].items():
                self.assertNotIn('strike', a, '%s marked refused after %s\n%s' % (aid, event, what))
                text = rig.vault(aid)
                self.assertIsNotNone(text, what)
                self.assertEqual(owner_of(refresh_token(text)), aid[:8],
                                 '%s holds another account\'s login after %s\n%s' % (aid, event, what))
            self.assertEqual(owner_of(refresh_token(rig.live_text())), in_use[:8], what)

        for _ in range(EVENTS):
            seen, was_in_use = len(raw), in_use
            event = rnd.choice(('renew', 'renew', 'keep', 'keep', 'upkeep', 'switch', 'sign_in', 'stale_label',
                                'first_sign_in'))
            counts[event] = counts.get(event, 0) + 1
            if event == 'renew':
                move = rnd.randint(-30000, 30000)
                old, _ = renew(rig, move, clock, step_s=rnd.randint(30, 3 * 3600), uuid=in_use)
                used_by_claude_code.add(refresh_token(old))
                story.append('renew %s by %+d ms' % (in_use, move))
                seen = len(raw)                         # Claude Code's own renewal is not ours
            elif event == 'keep':
                note = capture.keep_pass(rig.ctx, force=True)
                story.append('keep: %s' % note)
                if note and note.startswith('Saved the newer login Claude Code made'):
                    counts['saved_by_keep'] = counts.get('saved_by_keep', 0) + 1
                self.assertEqual(rig.vault(in_use), rig.live_text(),
                                 'after a keep pass the saved copy is not the live login\n' + '\n'.join(story))
            elif event == 'upkeep':
                clock.advance(6.6 * 3600)
                # Claude Code in use renews about every 8 hours, and always before it works with an expired token
                if rnd.random() < 0.5 or live.parse(rig.live_text()).expired(clock.wall()):
                    old, _ = renew(rig, rnd.randint(-30000, 30000), clock, step_s=1, uuid=in_use)
                    used_by_claude_code.add(refresh_token(old))
                    seen = len(raw)
                notes = tokens.upkeep_pass(rig.ctx, budget_s=120)
                counts['idle_renewed'] = counts.get('idle_renewed', 0) + sum(1 for x in notes if 'Renewed' in x)
                story.append('upkeep: %s' % notes)
            elif event == 'switch':
                target = rnd.choice(sorted(a for a in rig.doc()['accounts'] if a != in_use))
                try:
                    res = switcher.switch(rig.ctx, target, source='cli')
                except AccountsError as e:
                    story.append('switch to %s refused: %s' % (target, e))
                    self.fail('a switch was refused\n' + '\n'.join(story))
                self.assertTrue(res.ok, '\n'.join(story + [repr(res)]))
                story.append('switch %s -> %s' % (in_use, target))
                in_use = target
            elif event == 'sign_in':
                clock.advance(rnd.randint(60, 3600))
                sign_in_at(rig, in_use, int(clock.wall() * 1000) + 30 * DAY_MS)
                story.append('new sign-in of %s' % in_use)
            elif event == 'stale_label':
                others = sorted(a for a in rig.doc()['accounts'] if a != in_use)
                named = rnd.choice(others)
                rig.set_config(identity_block(named, email_of(named), org_of(named)))
                story.append('the settings name %s' % named)
            else:
                # a sign-in in Claude Code replaces its login: what it renewed and nobody kept is gone with it, and its
                # used-up saved copy would then be refused (a loss no rule here can prevent). The keep pass that comes
                # first models Switchboard having seen the renewal before the person signed in elsewhere.
                story.append('keep: %s' % capture.keep_pass(rig.ctx, force=True))
                doc = rig.doc()
                kept = sorted(doc['accounts'])
                new = 'nw%06d' % len(story)
                if rnd.random() < 0.6:
                    near = rnd.choice(kept)
                    rx = doc['accounts'][near]['rx_ms'] - rnd.randint(1, 120000)
                else:
                    near, rx = None, int(clock.wall() * 1000) + 30 * DAY_MS
                clock.advance(rnd.randint(1, 60))
                sign_in_at(rig, new, rx)
                story.append('first sign-in of %s@%+.1fs' % (new, (rx - stamps[0]) / 1000.0))
                if near is not None and rnd.random() < 0.5:
                    rig.set_config(identity_block(near, email_of(near), org_of(near)))
                    story.append('the settings name %s' % near)
                in_use = new
            check(event, seen, was_in_use)


if __name__ == '__main__':
    unittest.main()
