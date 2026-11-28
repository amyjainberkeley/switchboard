import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Keeping Claude Code signed in, over thousands of made-up snapshots from a fixed seed (2 to 5 accounts, random
# statuses, numbers, ages, sign-in ends, both settings on and off, random `lost` and `ends_at`). Whatever the case:
# (a) no move ever lands on an account that needs a sign-in, is in conflict, or was left for a lost sign-in; a move that
#     keeps Claude Code signed in never lands on one whose own sign-in ends within the hour;
# (b) with `lost` and `ends_at` both None there is no such move, and the decision is the one with the setting off;
# (c) with the setting off, the new facts change nothing;
# (d) with automatic switching off, the only switches are these moves;
# (e) the reason names accounts by their whole email, and never a token or a fingerprint.
import dataclasses
import os
import random
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard.accounts import api, autoloop, policy, putback  # noqa: E402
from fakes_autoswitch import T0  # noqa: E402

SEED = 20261125
CASES = 8000
H, D = 3600.0, 86400.0
NOW = T0
STATUSES = ('ok', 'ok', 'ok', 'expiring', 'needs_login', 'conflict', 'unreadable', 'claimed')
LOST = [dict(putback.MARKS)[k] for k in autoloop.LOST_MARKS] + [
    'was put back 4 times within an hour and was lost again each time']
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


def made_up(rnd):
    n = rnd.randint(2, 5)
    accounts, readings = [], {}
    for i in range(n):
        aid = 'acct%d' % i
        end = rnd.choice([None, NOW + rnd.uniform(-H, 2 * H), NOW + rnd.uniform(2 * H, 30 * D)])
        accounts.append(api.Account(id=aid, email='person.%d@mail%d.example' % (i, rnd.randint(1, 3)), org_id=None,
                                    org_name=None, plan='Max', alias=None, order=i + 1, status=rnd.choice(STATUSES),
                                    refresh_expires_at=end, added_at=NOW - D,
                                    fingerprint='sk-ant-ort01-made-up-%d' % i if rnd.random() < 0.1 else 'fp%05d' % i))
        if rnd.random() < 0.15:
            continue                                      # never read
        age = rnd.choice([0.0, rnd.uniform(0, 900), rnd.uniform(900, 12 * H)])
        ws = tuple(api.Window(name, rnd.choice([rnd.uniform(0, 100), 100.0, 0.0]), NOW + rnd.uniform(-H, 5 * D),
                              NOW - age, 'endpoint') for name in ('5-hour', 'weekly') if rnd.random() < 0.9)
        readings[aid] = api.Reading(aid, ws, NOW - age, 'endpoint', rnd.random() < 0.1, None)
    ids = [a.id for a in accounts]
    active = rnd.choice(ids + [None]) if rnd.random() < 0.1 else rnd.choice(ids)
    settings = policy.Settings(enabled=rnd.random() < 0.5, stay_signed_in=rnd.random() < 0.7,
                               strategy=rnd.choice(policy.STRATEGIES), session_at=rnd.choice([90.0, 80.0]),
                               weekly_at=rnd.choice([95.0, 85.0]))
    lost = rnd.choice(LOST) if rnd.random() < 0.35 else None
    ends = rnd.choice([None, NOW + rnd.uniform(-900, 900), NOW + rnd.uniform(900, 2 * H)]) \
        if rnd.random() < 0.6 else None

    def some():
        return frozenset(x for x in ids if rnd.random() < 0.15)
    return policy.Snapshot(
        now=NOW, accounts=tuple(accounts), readings=readings, settings=settings, active=active,
        live_ok=rnd.random() < (0.3 if lost else 0.85), live_why='made up', disagree=rnd.random() < 0.05,
        busy=rnd.random() < 0.3, idle_for_s=rnd.choice([None, 10.0, 1800.0]), since_switch_s=rnd.choice([None, 60.0,
                                                                                                          4 * H]),
        optional_recent=rnd.randint(0, 3), load_per_core=rnd.choice([None, 0.5, 6.0]), battery_pct=90.0, on_ac=True,
        settling=rnd.random() < 0.05, offline=rnd.random() < 0.05, rate_limited=rnd.random() < 0.05,
        claimed=frozenset(a.id for a in accounts if a.status == 'claimed'), live_refused=rnd.random() < 0.05,
        lost=lost, ends_at=ends, candidates_read=rnd.random() < 0.5, left=some(), moved_recently=some())


def plain(s):
    """The same snapshot with none of the new facts."""
    return dataclasses.replace(s, lost=None, ends_at=None, candidates_read=False, left=frozenset(),
                               moved_recently=frozenset())


def setting_off(s):
    return dataclasses.replace(s, settings=dataclasses.replace(s.settings, stay_signed_in=False))


class ManyCases(unittest.TestCase):
    def test_the_rules_hold_in_every_made_up_case(self):
        rnd = random.Random(SEED)
        counts = {'moves': 0, 'lost moves': 0, 'end moves': 0, 'moves with switching off': 0, 'old numbers': 0,
                  'stays for want of a target': 0}
        for n in range(CASES):
            s = made_up(rnd)
            d = policy.decide(s)
            why = policy.stay_why(s)
            by_id = dict((a.id, a) for a in s.accounts)
            where = 'case %d: %r' % (n, d)
            moving = d.action == 'switch' and why is not None
            if d.action == 'switch':
                t = by_id[d.target]
                # (a)
                self.assertIn(t.status, policy.OK_STATUSES, where)
                self.assertNotEqual(t.id, s.active, where)
                self.assertNotIn(t.id, s.claimed, where)
                if s.settings.stay_signed_in:
                    self.assertNotIn(t.id, s.left, where)
            if moving:
                counts['moves'] += 1
                counts['lost moves' if s.lost else 'end moves'] += 1
                counts['old numbers'] += 'numbers are' in d.reason
                self.assertEqual(d.kind, 'forced', where)
                self.assertFalse(policy.ends_soon(by_id[d.target], s.now), where)
                self.assertFalse(s.settling or s.offline, where)
                self.assertNotIn(s.active, s.moved_recently, where)
                note = policy.switch_note(d, s)
                self.assertIn(by_id[s.active].email, note, where)
                if policy._refused_first(s) is None:
                    self.assertIn('Switchboard moved Claude Code to %s' % by_id[d.target].email, note, where)
                    self.assertEqual(policy.switch_note(d, s), policy.stay_note(d, s), where)
                else:                                  # a refused sign-in, switching on: the forced switch's words
                    self.assertIn("login was refused", note, where)
            elif why is not None and d.action == 'stay' and d.hold == 'no other account':
                counts['stays for want of a target'] += 1
            # (b)
            if s.lost is None and s.ends_at is None:
                self.assertIsNone(why, where)
                if not s.left:
                    self.assertEqual(d, policy.decide(setting_off(s)), where)
            # (c)
            off = setting_off(s)
            self.assertEqual(policy.decide(off), policy.decide(plain(off)), where)
            self.assertIsNone(policy.stay_why(off), where)
            # (d)
            if not s.settings.enabled and d.action == 'switch':
                self.assertIsNotNone(why, where)
                counts['moves with switching off'] += 1
                self.assertIn('although automatic switching is off', policy.switch_note(d, s), where)
            # (e)
            for text in (d.reason, policy.switch_note(d, s) if d.action == 'switch' else ''):
                self.assertNotIn('sk-ant', text, where)
                self.assertNotRegex(text, r'fp\d{5}', where)
                emails = set(a.email for a in s.accounts)
                for found in EMAIL.findall(text):
                    self.assertIn(found, emails, where)
            if why is not None and not (s.settling or s.offline):
                self.assertIn(by_id[s.active].email, d.reason, where)
        for key, value in counts.items():                  # every kind of case was met, many times
            self.assertGreater(value, 50, (key, counts))


if __name__ == '__main__':
    unittest.main()
