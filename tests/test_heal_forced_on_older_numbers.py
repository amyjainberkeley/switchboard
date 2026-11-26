import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# When Claude Code is stopped on the account in use (its login refused or ended, or Claude Code said it is at its
# limit) and no other account has numbers fresh enough to land on, the forced switch goes by what was last known
# instead of staying stopped. Nothing else about choosing changes: the random snapshots below prove every other
# decision is the one the policy made before.
import os
import random
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard.accounts import api, policy
from fakes_autoswitch import T0, account

H, D = 3600.0, 86400.0
NOW = T0


def win(name, pct, resets=None, age=0.0, source='endpoint'):
    return api.Window(name, pct, resets, NOW - age, source)


def reading(aid, five=None, week=None, age=0.0, frozen=False, five_reset=NOW + 3 * H, week_reset=NOW + 4 * D):
    ws = []
    if five is not None:
        ws.append(win('5-hour', five, five_reset, age))
    if week is not None:
        ws.append(win('weekly', week, week_reset, age))
    return api.Reading(aid, tuple(ws), NOW - age, 'endpoint', frozen, None)


ACCTS = (account('a', 1), account('b', 2), account('c', 3))


def snap(readings, active='a', accounts=ACCTS, settings=None, **kw):
    base = dict(busy=False, idle_for_s=1800.0, since_switch_s=4 * H, optional_recent=0, load_per_core=0.5,
                battery_pct=90.0, on_ac=True)
    base.update(kw)
    return policy.Snapshot(now=NOW, accounts=tuple(accounts), readings=readings,
                           settings=settings or policy.Settings(enabled=True), active=active, **base)


OLD = 2 * H                 # numbers this old are no landing place today (over FORCED_MAX_AGE)


class StoppedGoesByWhatWasLastKnown(unittest.TestCase):
    def test_a_limit_notice_with_only_old_numbers_moves_to_the_most_room_last_read(self):
        r = {'a': reading('a', five=40.0, week=30.0, age=OLD),           # its own numbers old too: it said "limit"
             'b': reading('b', five=70.0, week=50.0, age=OLD),
             'c': reading('c', five=20.0, week=30.0, age=3 * H)}
        d = policy.decide(snap(r, rate_limited=True))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'c'))
        self.assertIn('older numbers', d.reason)
        self.assertIn('c@acct.example', d.reason)
        self.assertTrue(policy.on_older_numbers(d, snap(r, rate_limited=True)))

    def test_a_refused_or_ended_login_moves_too(self):
        r = {'a': reading('a', five=10.0, week=10.0, age=OLD), 'b': reading('b', five=30.0, week=30.0, age=OLD)}
        accts = (account('a', 1, status='needs_login'), account('b', 2))
        d = policy.decide(snap(r, accounts=accts))
        self.assertEqual((d.action, d.target), ('switch', 'b'))
        d = policy.decide(snap(r, accounts=ACCTS[:2], live_refused=True))
        self.assertEqual((d.action, d.target), ('switch', 'b'))

    def test_the_newest_reading_wins_among_equal_room_and_never_read_comes_last(self):
        r = {'a': reading('a', five=40.0, week=30.0),
             'b': reading('b', five=20.0, week=20.0, age=3 * H),
             'c': reading('c', five=20.0, week=20.0, age=OLD)}
        self.assertEqual(policy.decide(snap(r, rate_limited=True)).target, 'c')
        r = {'a': reading('a', five=40.0, week=30.0), 'b': reading('b', five=85.0, week=20.0, age=OLD)}
        self.assertEqual(policy.decide(snap(r, rate_limited=True)).target, 'b')      # read once, beats never read
        r = {'a': reading('a', five=40.0, week=30.0)}                               # nothing read at all: still goes
        d = policy.decide(snap(r, rate_limited=True))
        self.assertEqual((d.action, d.target), ('switch', 'b'))
        self.assertIn('not known', d.reason)

    def test_an_account_known_to_be_over_its_line_is_never_the_landing_place(self):
        r = {'a': reading('a', five=40.0, week=30.0),
             'b': reading('b', five=92.0, week=20.0, age=OLD),       # over its 90 line when last read
             'c': reading('c', five=10.0, week=99.0, age=OLD)}       # over its 95 weekly line
        d = policy.decide(snap(r, rate_limited=True))
        self.assertEqual((d.action, d.hold), ('stay', 'no fresh numbers'))
        accts = (account('a', 1), account('b', 2, status='needs_login'), account('c', 3, status='claimed'))
        r = {'a': reading('a', five=40.0, week=30.0), 'b': reading('b', five=1.0, week=1.0, age=OLD),
             'c': reading('c', five=1.0, week=1.0, age=OLD)}
        d = policy.decide(snap(r, accounts=accts, rate_limited=True))
        self.assertEqual(d.action, 'stay')                            # not signed in, or in use by a terminal

    def test_it_does_not_act_in_the_normal_cases(self):
        # Over a line but not stopped: it waits for fresh numbers, as before.
        r = {'a': reading('a', five=95.0, week=30.0), 'b': reading('b', five=10.0, week=10.0, age=OLD)}
        d = policy.decide(snap(r, accounts=ACCTS[:2]))
        self.assertEqual((d.action, d.hold), ('stay', 'no fresh numbers'))
        # A window at 100% by the numbers alone is not "Claude Code said so": it waits for fresh numbers as before.
        r['a'] = reading('a', five=100.0, week=30.0)
        self.assertEqual(policy.decide(snap(r, accounts=ACCTS[:2])).hold, 'no fresh numbers')
        # Stopped with a fresh landing place: the fresh one, as before, never the older guess.
        r['b'] = reading('b', five=50.0, week=50.0)
        d = policy.decide(snap(r, rate_limited=True))
        self.assertEqual((d.action, d.target), ('switch', 'b'))
        self.assertNotIn('older numbers', d.reason)
        self.assertFalse(policy.on_older_numbers(d, snap(r, rate_limited=True)))
        # Nothing that keeps every switch back is passed over.
        r = {'a': reading('a', five=40.0, week=30.0), 'b': reading('b', five=10.0, week=10.0, age=OLD)}
        for kw in (dict(live_ok=False, live_why='timeout'), dict(settling=True), dict(offline=True)):
            self.assertEqual(policy.decide(snap(r, accounts=ACCTS[:2], rate_limited=True, **kw)).action, 'stay', kw)
        off = policy.Settings(enabled=False)
        self.assertEqual(policy.decide(snap(r, accounts=ACCTS[:2], rate_limited=True, settings=off)).action, 'stay')


def random_snapshot(rng):
    n = rng.randint(1, 4)
    statuses = ('ok', 'ok', 'ok', 'expiring', 'needs_login', 'claimed', 'unreadable', 'conflict')
    accts = tuple(account('x%d' % i, i + 1, status=rng.choice(statuses)) for i in range(n))
    model = rng.choice((None, None, 'Fable'))
    ids = [a.id for a in accts]
    rules = {}
    ranks = {}
    if rng.random() < 0.5:                                              # half with rules set: kept out, used last
        rules = dict((x, r) for x, r in ((x, rng.choice(('in', 'in', 'last', 'out'))) for x in ids) if r != 'in')
    if rng.random() < 0.4:                                              # and ranks
        ranks = dict((x, rng.randint(1, 3)) for x in ids if rng.random() < 0.7)
    settings = policy.Settings(enabled=rng.random() > 0.05, session_at=rng.choice((90.0, 80.0, 99.9)),
                               weekly_at=rng.choice((95.0, 60.0)), model_at=95.0, model=model,
                               strategy=rng.choice(policy.STRATEGIES), rules=tuple(sorted(rules.items())),
                               ranks=tuple(sorted(ranks.items())))
    readings = {}
    for a in accts:
        if rng.random() < 0.15:
            continue                                                    # never read
        ws = []
        for name in ('5-hour', 'weekly', 'model:Fable'):
            if rng.random() < 0.15:
                continue
            pct = rng.choice((None, 0.0, 100.0, 89.9, 90.0, 94.9, 95.0, rng.uniform(0, 100)))
            age = rng.choice((0.0, 60.0, 899.0, 900.0, 901.0, 2 * H, 30 * H, -600.0, rng.uniform(0, 3 * H)))
            reset = rng.choice((None, NOW - 60, NOW + rng.uniform(60, 7 * D)))
            ws.append(api.Window(name, pct, reset, NOW - age, rng.choice(('endpoint', 'status_line'))))
        readings[a.id] = api.Reading(a.id, tuple(ws), NOW - rng.uniform(0, 3 * H), 'endpoint', rng.random() < 0.2)
    active = rng.choice([a.id for a in accts] + [None, 'gone'])
    # accounts a forced switch left: a minute, two hours or a day ago (read since, or not)
    left = tuple((a.id, NOW - rng.choice((60.0, 2 * H, 30 * H))) for a in accts if rng.random() < 0.3)
    return policy.Snapshot(
        now=NOW, accounts=accts, readings=readings, settings=settings, active=active,
        live_ok=rng.random() > 0.1, live_why=None, disagree=rng.random() < 0.1, busy=rng.random() < 0.3,
        idle_for_s=rng.choice((None, 0.0, 400.0)), since_switch_s=rng.choice((None, 60.0, 5 * H)),
        optional_recent=rng.choice((0, 3)), load_per_core=rng.choice((None, 1.0, 9.0)),
        battery_pct=rng.choice((None, 10.0, 90.0)), on_ac=rng.choice((None, True, False)),
        settling=rng.random() < 0.05, offline=rng.random() < 0.05, rate_limited=rng.random() < 0.35,
        claimed=frozenset(a.id for a in accts if a.status == 'claimed'), no_optional=None,
        live_refused=rng.random() < 0.2, left_forced=left)


def candidates(s):
    """The accounts a forced switch may go to: signed in, not the one in use, not claimed, never kept out."""
    return [policy.view(a, s.readings.get(a.id), s.settings, s.now) for a in s.accounts
            if policy._eligible(a, s) and policy.rule_of(s.settings, a.id) != 'out']


def fresh_of(s):
    return [v for v in candidates(s) if v.known and v.age is not None and v.age <= policy.FORCED_MAX_AGE]


def stopped_and_nothing_fresh(s):
    """The only case the change may decide differently: the account in use is truly stopped (and not kept out of
    auto-switch), and no account it could go to that was read fresh is a landing place or an escape."""
    by = dict((a.id, a) for a in s.accounts)
    act = by.get(s.active)
    if act is None or policy.rule_of(s.settings, act.id) == 'out':
        return False
    stopped = act.status == 'needs_login' or s.live_refused or s.rate_limited
    blocked = policy._blocked(policy.view(act, s.readings.get(act.id), s.settings, s.now), s, act)
    fits = [v for v in fresh_of(s) if v.room >= policy.LAND_MARGIN or
            (blocked and v.raw_room is not None and v.raw_room >= policy.ESCAPE_ROOM)]
    return stopped and not fits


class SomeReadFreshAndNoneFits(unittest.TestCase):
    """Some accounts were read just now and none of them is a landing place or an escape: the ones that could not be
    read are judged by what was last known of them. An account read just now is never chosen this way."""

    def test_the_others_are_judged_by_what_was_last_known(self):
        r = {'a': reading('a', five=40.0, week=30.0), 'b': reading('b', five=100.0, week=100.0),
             'c': reading('c', five=10.0, week=10.0, age=OLD)}
        d = policy.decide(snap(r, rate_limited=True))
        self.assertEqual((d.action, d.kind, d.target), ('switch', 'forced', 'c'))
        self.assertIn('b@acct.example has no room', d.reason)
        self.assertIn("no other account's numbers could be read just now, so it moved on older numbers", d.reason)
        self.assertIn('c@acct.example had', d.reason)
        self.assertTrue(policy.on_older_numbers(d, snap(r, rate_limited=True)))

    def test_one_read_just_now_is_never_chosen_this_way(self):
        accts = ACCTS + (account('d', 4),)
        r = {'a': reading('a', five=40.0, week=30.0), 'b': reading('b', five=99.0, week=99.0),
             'c': reading('c', five=97.0, week=10.0), 'd': reading('d', five=95.0, week=95.0, age=OLD)}
        d = policy.decide(snap(r, accounts=accts, rate_limited=True))
        self.assertEqual((d.action, d.target), ('stay', None))          # d was over its line when last read
        r['d'] = reading('d', five=30.0, week=20.0, age=OLD)
        d = policy.decide(snap(r, accounts=accts, rate_limited=True))
        self.assertEqual(d.target, 'd')
        self.assertIn('b@acct.example and c@acct.example have no room', d.reason)

    def test_not_stopped_it_still_waits_for_fresh_numbers(self):
        r = {'a': reading('a', five=95.0, week=30.0), 'b': reading('b', five=100.0, week=100.0),
             'c': reading('c', five=10.0, week=10.0, age=OLD)}
        d = policy.decide(snap(r))
        self.assertEqual((d.action, d.hold), ('stay', 'no fresh numbers'))

    def test_a_fresh_escape_still_comes_first(self):
        r = {'a': reading('a', five=100.0, week=30.0), 'b': reading('b', five=92.0, week=50.0),
             'c': reading('c', five=10.0, week=10.0, age=OLD)}
        d = policy.decide(snap(r, rate_limited=True))
        self.assertEqual(d.target, 'b')
        self.assertNotIn('older numbers', d.reason)


class NothingElseChanges(unittest.TestCase):
    def test_random_snapshots_decide_as_before_unless_stopped_with_nothing_fresh(self):
        rng = random.Random(3417)
        moved = same_in_case = onto_last = by_rank = with_rules = some_fresh = left_out = 0
        for i in range(40000):
            s = random_snapshot(rng)
            got = policy.decide(s)
            with mock.patch.object(policy, '_on_older_numbers', lambda *a, **k: None):
                before = policy.decide(s)
            fresh_ids = set(v.id for v in fresh_of(s))
            left_out += stopped_and_nothing_fresh(s) and any(
                policy._left_before_read(s, v) for v in candidates(s) if not v.over and v.id not in fresh_ids)
            if got == before:
                same_in_case += stopped_and_nothing_fresh(s)
                continue
            self.assertTrue(stopped_and_nothing_fresh(s), (i, s, before, got))
            self.assertEqual((before.action, before.kind, before.hold), ('stay', 'forced', 'no fresh numbers'), i)
            self.assertEqual((got.action, got.kind), ('switch', 'forced'), i)
            target = dict((a.id, a) for a in s.accounts)[got.target]
            self.assertTrue(policy._eligible(target, s), i)
            self.assertNotEqual(policy.rule_of(s.settings, target.id), 'out', i)        # never onto a kept-out one
            tv = policy.view(target, s.readings.get(target.id), s.settings, s.now)
            self.assertFalse(tv.over, i)
            self.assertFalse(policy._left_before_read(s, tv), i)   # never back to one left before it was read
            self.assertTrue(policy.on_older_numbers(got, s), i)
            self.assertNotIn(target.id, fresh_ids, i)                  # one read fresh is never chosen this way
            some_fresh += bool(fresh_ids)
            ok = [v for v in candidates(s) if not v.over and v.id not in fresh_ids and
                  not policy._left_before_read(s, v)]
            last = policy.rule_of(s.settings, target.id) == 'last'
            if last:                                                    # used last only when nothing else qualifies
                self.assertEqual([v.id for v in ok if policy.rule_of(s.settings, v.id) != 'last'], [], i)
                onto_last += 1
            group = [v for v in ok if (policy.rule_of(s.settings, v.id) == 'last') == last]
            self.assertEqual(policy._rank_key(tv), min(policy._rank_key(v) for v in group), i)   # ranks respected
            by_rank += len(set(policy._rank_key(v) for v in group)) > 1
            with_rules += bool(s.settings.rules)
            moved += 1
        self.assertGreater(moved, 300)          # the streams really reach the new case
        self.assertGreater(same_in_case, 100)   # and the case where nothing qualifies still stays
        self.assertGreater(onto_last, 20)       # with rules and ranks set, and unset
        self.assertGreater(by_rank, 20)
        self.assertGreater(with_rules, 100)
        self.assertGreater(moved - with_rules, 100)
        self.assertGreater(some_fresh, 20)      # and the case with some read fresh, none of them fitting
        self.assertGreater(left_out, 100)       # and the case where an account a forced switch left is passed over


class InsideThePersonsRules(unittest.TestCase):
    """The move on older numbers keeps every rule a person set for an account."""

    def test_never_onto_an_account_kept_out(self):
        r = {'a': reading('a', five=40.0, week=30.0), 'b': reading('b', five=1.0, week=1.0, age=OLD),
             'c': reading('c', five=60.0, week=60.0, age=OLD)}
        rules = policy.Settings(enabled=True, rules=(('b', 'out'),))
        d = policy.decide(snap(r, rate_limited=True, settings=rules))
        self.assertEqual((d.action, d.target), ('switch', 'c'))
        rules = policy.Settings(enabled=True, rules=(('b', 'out'), ('c', 'out')))
        d = policy.decide(snap(r, rate_limited=True, settings=rules))
        self.assertEqual((d.action, d.hold), ('stay', 'no other account'))

    def test_on_an_account_kept_out_it_stays(self):
        r = {'a': reading('a', five=40.0, week=30.0), 'b': reading('b', five=1.0, week=1.0, age=OLD)}
        rules = policy.Settings(enabled=True, rules=(('a', 'out'),))
        d = policy.decide(snap(r, accounts=ACCTS[:2], rate_limited=True, settings=rules))
        self.assertEqual((d.action, d.hold), ('stay', policy.KEPT_OUT))

    def test_an_account_used_last_only_when_no_other_qualifies(self):
        r = {'a': reading('a', five=40.0, week=30.0), 'b': reading('b', five=1.0, week=1.0, age=OLD),
             'c': reading('c', five=60.0, week=60.0, age=OLD)}
        rules = policy.Settings(enabled=True, rules=(('b', 'last'),))
        self.assertEqual(policy.decide(snap(r, rate_limited=True, settings=rules)).target, 'c')
        r['c'] = reading('c', five=95.0, week=60.0, age=OLD)              # c over its line when last read
        d = policy.decide(snap(r, rate_limited=True, settings=rules))
        self.assertEqual((d.action, d.target), ('switch', 'b'))
        self.assertIn('used last', d.reason)

    def test_a_lower_rank_first(self):
        r = {'a': reading('a', five=40.0, week=30.0), 'b': reading('b', five=1.0, week=1.0, age=OLD),
             'c': reading('c', five=60.0, week=60.0, age=OLD)}
        ranks = policy.Settings(enabled=True, ranks=(('c', 1),))
        d = policy.decide(snap(r, rate_limited=True, settings=ranks))
        self.assertEqual(d.target, 'c')
        self.assertIn('rank 1', d.reason)
        ranks = policy.Settings(enabled=True, ranks=(('b', 2), ('c', 2)))       # one rank alike: as with none
        self.assertEqual(policy.decide(snap(r, rate_limited=True, settings=ranks)).target, 'b')


class TheLoopMovesAndSaysSo(unittest.TestCase):
    """The loop end to end: every read refused, the account in use stopped, older numbers with room elsewhere."""

    def setUp(self):
        from fakes_autoswitch import FakeClock, FakeEngine, FakeNet, FakeTokens, Home
        self.home = Home('run')
        self.home.setUp()
        self.clock = FakeClock()
        self.accts = [account('a1', 1), account('a2', 2), account('a3', 3)]
        self.engine = FakeEngine(self.accts, 'a1', self.clock)
        self.net = FakeNet(self.clock)
        self.tokens = FakeTokens()
        from fakes_autoswitch import set_policy
        set_policy()

    def tearDown(self):
        self.home.tearDown()

    def loop(self, **kw):
        from fakes_autoswitch import make_loop
        return make_loop(self.engine, self.clock, self.net, self.tokens, **kw)

    def answer(self, a1, a2, a3):
        for tok, a in (('tok-a1', a1), ('tok-a2', a2), ('tok-a3', a3)):
            self.net.set(tok, a)

    def log_text(self):
        from switchboard import core
        from switchboard.accounts import autoloop
        try:
            with open(core.data_path(autoloop.LOG)) as f:
                return f.read()
        except OSError:
            return ''

    def test_a_stopped_account_moves_on_older_numbers_when_every_read_is_refused(self):
        from fakes_autoswitch import usage_body
        good = lambda five, week: (200, {}, usage_body(five=five, week=week, five_reset=T0 + 6 * H,      # noqa: E731
                                                       week_reset=T0 + 4 * D))
        self.answer(good(20.0, 30.0), good(60.0, 50.0), good(30.0, 20.0))
        loop = self.loop()
        self.assertIsNone(loop.run_pass().switched)                 # all fine: nothing moves
        self.clock.advance(2 * H)                                   # two hours on, Anthropic refuses every read
        refused = (429, {'Retry-After': '3600'}, {'error': {'type': 'rate_limit_error'}})
        self.answer(refused, refused, refused)
        self.engine.accts[0] = account('a1', 1, status='needs_login')   # and the login in use was refused
        res = loop.run_pass()
        self.assertIsNotNone(res.switched)
        sw = self.engine.switches[0]
        self.assertEqual((sw['to'], sw['kind'], sw['source']), ('a3', 'forced', 'auto'))
        self.assertIn('moved on older numbers', sw['reason'])       # what History says (the switch's reason)
        self.assertIn('send your message again', sw['reason'])

    def test_the_account_it_moved_to_is_read_the_moment_its_wait_ends(self):
        from fakes_autoswitch import usage_body
        from switchboard.accounts import usage
        good = lambda five, week: (200, {}, usage_body(five=five, week=week, five_reset=T0 + 6 * H,      # noqa: E731
                                                       week_reset=T0 + 4 * D))
        self.answer(good(20.0, 30.0), good(60.0, 50.0), good(30.0, 20.0))
        loop = self.loop()
        loop.run_pass()
        self.clock.advance(2 * H)
        refused = (429, {'Retry-After': '600'}, {'error': {'type': 'rate_limit_error'}})
        self.answer(refused, refused, refused)
        self.engine.accts[0] = account('a1', 1, status='needs_login')
        self.assertIsNotNone(loop.run_pass().switched)
        self.assertIn('the account it moved to is read the moment its wait for a read ends', self.log_text())
        self.assertTrue(loop.poller.plans['a3'].wake)
        sent = self.net.count('tok-a3')
        self.clock.advance(700)                                     # the wait is over; Anthropic answers again
        self.answer(refused, refused, good(35.0, 25.0))
        loop.run_pass()
        self.assertGreater(self.net.count('tok-a3'), sent)
        r = usage.readings(now=self.clock.wall())['a3']
        self.assertTrue(all(abs(self.clock.wall() - (w.read_at or r.read_at)) < 120 for w in r.windows))

    def test_with_no_wait_the_account_it_moved_to_is_read_at_once(self):
        from fakes_autoswitch import usage_body
        from switchboard.accounts import usage
        self.answer(*[(200, {}, usage_body(five=10.0, week=10.0, five_reset=T0 + 6 * H, week_reset=T0 + 4 * D))] * 3)
        loop = self.loop()
        sent = self.net.count('tok-a2')
        snap = policy.Snapshot(now=self.clock.wall(), accounts=tuple(self.accts), readings={},
                               settings=policy.Settings(enabled=True), active='a2')
        loop._read_landing('a2', usage.Context(active='a1', order=('a1', 'a2', 'a3')), snap)
        self.assertEqual(self.net.count('tok-a2'), sent + 1)
        self.assertIn('the account it moved to was read at once', self.log_text())

    def test_a_full_disk_begins_no_switch_even_for_a_stopped_account_and_says_so_once(self):
        # The switch's own floor (switcher._check_disk, 50 MB) is what lets it promise its undo, so under it no switch
        # is begun at all, blocked or not, and no record of one is written: History says so once for the spell.
        from fakes_autoswitch import usage_body
        from switchboard import core
        from switchboard.accounts import autoloop, switcher
        good = lambda five, week: (200, {}, usage_body(five=five, week=week, five_reset=T0 + 6 * H,      # noqa: E731
                                                       week_reset=T0 + 4 * D))
        self.answer(good(95.0, 30.0), good(10.0, 20.0), good(10.0, 20.0))     # over its line, still working
        first = self.loop(disk_free=lambda: 10 * 1024 * 1024)
        self.assertIsNone(first.run_pass().switched)
        self.assertIn('disk almost full: no switch begins under 50 MB free', self.log_text())
        self.assertEqual(self.engine.switches, [])
        self.clock.advance(120)
        self.answer(good(100.0, 30.0), good(10.0, 20.0), good(10.0, 20.0))    # at 100%: Claude Code can't work
        second = self.loop(disk_free=lambda: 10 * 1024 * 1024)
        self.assertIsNone(second.run_pass().switched)
        self.assertEqual(self.engine.switches, [])
        self.assertFalse(os.path.exists(core.data_path(switcher.JOURNAL)))
        self.assertEqual(first.notes + second.notes, [(autoloop.DISK_FULL_WORDS, 'problem')])


if __name__ == '__main__':
    unittest.main()
