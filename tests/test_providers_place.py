import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# How much room a provider has and which one is next (switchboard/providers/place.py, DESIGN 8.4 and 8.5): pure
# functions over made-up numbers. No file, no clock, no program, no words.
import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard.providers import contract as c
from switchboard.providers import place

NOW = 1795600000.0
H, D = 3600.0, 86400.0


def win(used, resets=None, line=90.0, label='5-hour'):
    return c.Window(label=label, used_pct=used, resets_at=resets, line=line)


def lim(*windows, read_at=NOW - 60, seat='a', reached=None, error=None, provider='claude'):
    return c.Limits(provider=provider, seat=seat, windows=tuple(windows), read_at=read_at, source='file',
                    reached=reached, error=error)


def room(*limits, fresh=H):
    return place.room_of('claude', 'Claude', tuple(limits), NOW, fresh)


class RoomOf(unittest.TestCase):
    def test_fresh_numbers_under_their_lines_have_room(self):
        r = room(lim(win(40, NOW + H), win(70, NOW + 3 * D, 95.0, 'weekly')))
        self.assertEqual((r.provider, r.state, r.back_at, r.why, r.read_at), ('claude', c.ROOM, None, '', NOW - 60))

    def test_a_window_at_its_line_is_full_and_the_seat_is_out_until_it_refills(self):
        r = room(lim(win(90.0, NOW + H), win(10, NOW + 3 * D, 95.0, 'weekly')))
        self.assertEqual((r.state, r.back_at, r.why), (c.OUT, NOW + H, ''))

    def test_two_full_windows_refill_at_the_later(self):
        r = room(lim(win(99, NOW + H), win(96, NOW + 3 * D, 95.0, 'weekly')))
        self.assertEqual((r.state, r.back_at), (c.OUT, NOW + 3 * D))

    def test_a_full_window_with_no_reset_is_out_with_no_time(self):
        r = room(lim(win(100, None)))
        self.assertEqual((r.state, r.back_at), (c.OUT, None))
        r = room(lim(win(100, None), win(97, NOW + D, 95.0, 'weekly')))
        self.assertEqual((r.state, r.back_at), (c.OUT, None))

    def test_the_apps_own_reached_word_makes_it_out_whatever_the_numbers(self):
        r = room(lim(win(20, NOW + H), win(30, NOW + 2 * D, 95.0, 'weekly'), reached='rate_limit_reached'))
        self.assertEqual((r.state, r.back_at), (c.OUT, NOW + 2 * D))
        r = room(lim(win(20, NOW + H), win(99, NOW + 2 * D, 95.0, 'weekly'), reached='rate_limit_reached'))
        self.assertEqual((r.state, r.back_at), (c.OUT, NOW + 2 * D))

    def test_stale_numbers_are_not_room(self):
        r = room(lim(win(10, NOW + H), read_at=NOW - 2 * H))
        self.assertEqual((r.state, r.read_at), (c.UNKNOWN, NOW - 2 * H))
        r = room(lim(win(10, NOW + D), read_at=NOW - 6 * D), fresh=7 * D)
        self.assertEqual(r.state, c.ROOM)               # Codex's saved numbers stay good for 7 days
        self.assertEqual(room(lim(win(10, NOW + H), read_at=None)).state, c.UNKNOWN)

    def test_stale_numbers_with_a_window_full_until_later_are_still_out(self):
        r = room(lim(win(99, NOW + D), read_at=NOW - 5 * H))
        self.assertEqual((r.state, r.back_at), (c.OUT, NOW + D))

    def test_a_refilled_window_is_not_known(self):
        self.assertEqual(room(lim(win(None, None))).state, c.UNKNOWN)
        self.assertEqual(room(lim(win(None, None), win(50, NOW + D, 95.0, 'weekly'))).state, c.ROOM)

    def test_a_window_whose_reset_has_passed_has_refilled(self):
        self.assertEqual(room(lim(win(100, NOW - 60))).state, c.UNKNOWN)
        self.assertEqual(room(lim(win(100, NOW - 60), win(50, NOW + D, 95.0, 'weekly'))).state, c.ROOM)

    def test_numbers_with_an_error_are_not_current(self):
        self.assertEqual(room(lim(win(10, NOW + H), error='The saved numbers could not be read.')).state, c.UNKNOWN)

    def test_no_numbers_at_all_is_unknown(self):
        self.assertEqual(room().state, c.UNKNOWN)
        self.assertEqual(place.room_of('codex', 'Codex', c.Unsupported('codex', 'limits', 'no'), NOW, H).state,
                         c.UNKNOWN)
        self.assertEqual(room(lim()).state, c.UNKNOWN)

    def test_a_window_with_no_line_is_full_at_one_hundred(self):
        self.assertEqual(room(lim(win(99.5, NOW + H, None))).state, c.ROOM)
        self.assertEqual(room(lim(win(100, NOW + H, None))).state, c.OUT)

    def test_one_seat_with_room_is_enough(self):
        r = room(lim(win(99, NOW + H), seat='a'), lim(win(10, NOW + H), seat='b', read_at=NOW - 30))
        self.assertEqual((r.state, r.read_at), (c.ROOM, NOW - 30))

    def test_every_seat_out_is_out_until_the_first_refills(self):
        r = room(lim(win(99, NOW + 3 * H), seat='a'), lim(win(99, NOW + H), seat='b'))
        self.assertEqual((r.state, r.back_at), (c.OUT, NOW + H))
        r = room(lim(win(99, NOW + 3 * H), seat='a'), lim(win(99, None), seat='b'))
        self.assertEqual((r.state, r.back_at), (c.OUT, None))

    def test_one_seat_out_and_one_not_known_is_unknown(self):
        r = room(lim(win(99, NOW + 3 * H), seat='a'), lim(win(10, NOW + H), seat='b', read_at=NOW - 9 * H))
        self.assertEqual(r.state, c.UNKNOWN)

    def test_why_is_always_left_empty(self):
        for limits in ((), (lim(win(10, NOW + H)),), (lim(win(99, NOW + H)),), (lim(win(None)),)):
            self.assertEqual(place.room_of('claude', 'Claude', limits, NOW, H).why, '')


def rooms(**states):
    return {pid: c.Room(provider=pid, state=s, back_at=NOW + H if s == c.OUT else None) for pid, s in states.items()}


class Pick(unittest.TestCase):
    ORDER = ['claude', 'codex']

    def test_the_other_with_room(self):
        self.assertEqual(place.pick(self.ORDER, 'claude', rooms(claude=c.OUT, codex=c.ROOM)), ('codex', 'room'))
        self.assertEqual(place.pick(self.ORDER, 'codex', rooms(claude=c.ROOM, codex=c.OUT)), ('claude', 'room'))

    def test_numbers_not_known_is_next_and_says_so(self):
        self.assertEqual(place.pick(self.ORDER, 'claude', rooms(claude=c.OUT, codex=c.UNKNOWN)),
                         ('codex', 'unknown'))

    def test_every_one_out(self):
        self.assertEqual(place.pick(self.ORDER, 'claude', rooms(claude=c.OUT, codex=c.OUT)), (None, 'all_out'))

    def test_room_anywhere_in_the_order_wins_over_unknown_earlier(self):
        order = ['claude', 'alpha', 'beta', 'gamma']
        got = place.pick(order, 'claude', rooms(claude=c.OUT, alpha=c.UNKNOWN, beta=c.OUT, gamma=c.ROOM))
        self.assertEqual(got, ('gamma', 'room'))
        got = place.pick(order, 'claude', rooms(claude=c.OUT, alpha=c.OUT, beta=c.UNKNOWN, gamma=c.UNKNOWN))
        self.assertEqual(got, ('beta', 'unknown'))

    def test_the_order_decides_between_two_with_room(self):
        order = ['codex', 'alpha', 'claude']
        got = place.pick(order, 'claude', rooms(claude=c.ROOM, alpha=c.ROOM, codex=c.ROOM))
        self.assertEqual(got, ('codex', 'room'))

    def test_not_added_not_installed_and_signed_out_are_passed_over(self):
        order = ['claude', 'alpha', 'beta', 'codex']
        got = place.pick(order, 'claude', rooms(claude=c.OUT, alpha=c.NOT_INSTALLED, beta=c.SIGNED_OUT,
                                                codex=c.ROOM))
        self.assertEqual(got, ('codex', 'room'))
        got = place.pick(order, 'claude', rooms(claude=c.OUT, alpha=c.NOT_INSTALLED, beta=c.SIGNED_OUT,
                                                codex=c.NOT_ADDED))
        self.assertEqual(got, (None, 'not_installed'))      # the first one in the order says why

    def test_nothing_else_in_the_order(self):
        self.assertEqual(place.pick(['claude'], 'claude', rooms(claude=c.OUT)), (None, 'no_other'))
        self.assertEqual(place.pick(['claude', 'codex'], 'claude', rooms(claude=c.OUT)), (None, 'no_other'))

    def test_a_named_target(self):
        self.assertEqual(place.pick(self.ORDER, 'claude', rooms(claude=c.ROOM, codex=c.OUT), to='codex'),
                         ('codex', 'out'))
        self.assertEqual(place.pick(self.ORDER, 'claude', rooms(claude=c.ROOM, codex=c.UNKNOWN), to='codex'),
                         ('codex', 'unknown'))
        self.assertEqual(place.pick(self.ORDER, 'claude', rooms(claude=c.ROOM, codex=c.ROOM), to='codex'),
                         ('codex', 'room'))

    def test_a_named_target_that_cannot_be(self):
        r = rooms(claude=c.ROOM, codex=c.ROOM)
        self.assertEqual(place.pick(self.ORDER, 'claude', r, to='claude'), (None, 'is_source'))
        for state, why in ((c.NOT_ADDED, 'not_added'), (c.NOT_INSTALLED, 'not_installed'),
                           (c.SIGNED_OUT, 'signed_out')):
            self.assertEqual(place.pick(self.ORDER, 'claude', rooms(claude=c.ROOM, codex=state), to='codex'),
                             (None, why))
        self.assertEqual(place.pick(self.ORDER, 'claude', rooms(claude=c.ROOM), to='codex'), (None, 'not_added'))

    def test_rooms_may_be_a_list(self):
        listed = list(rooms(claude=c.OUT, codex=c.ROOM).values())
        self.assertEqual(place.pick(self.ORDER, 'claude', listed), ('codex', 'room'))

    def test_every_reason_is_a_known_code(self):
        self.assertTrue({'room', 'unknown', 'out', 'all_out', 'not_added', 'not_installed', 'signed_out',
                         'is_source', 'no_other'} <= set(place.REASONS))


class Pure(unittest.TestCase):
    def test_it_imports_nothing_but_the_contract(self):
        with open(place.__file__) as f:
            tree = ast.parse(f.read())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update((0, a.name) for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.add((node.level, node.module))
        self.assertTrue(found <= {(0, 'typing'), (1, 'contract')}, found)


if __name__ == '__main__':
    unittest.main()
