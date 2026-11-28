import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# What this tree ships. Handing work to another app, and the one "continue" after a move at a limit, are done inside
# Claude Code by the plugin. router/ready.py says whether that part of the plugin is in. While it is not, both are
# plain switches that start nothing, off until set, and every place a person reads about a hand-over says it is not
# in this version. Turning ready.HAND_READY on without changing those places makes this file fail.
import os
import shutil
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from switchboard import core  # noqa: E402
from switchboard.providers import contract  # noqa: E402
from switchboard.providers.job import RouteChoice  # noqa: E402
from switchboard.providers.routes import Table  # noqa: E402
from switchboard.router import hand_words, plan, ready, resolve, settings  # noqa: E402

NOW = 1795000000.0
EARLY = 'early: tested with stand-in programs, not yet with a real Codex'
NOT_YET = 'Handing work to the other app is not in this version'


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class Shipped(unittest.TestCase):
    def setUp(self):
        p = core.data_path('router')
        if os.path.isdir(p):
            shutil.rmtree(p)

    def test_one_fact_decides_which_switches_follow_on(self):
        self.assertIs(settings.HAND_READY, ready.HAND_READY)
        self.assertEqual(settings.FOLLOW_ON, ('hand.on', 'carry_on') if ready.HAND_READY else ())

    def test_routing_on_brings_the_hand_and_the_continue_only_where_they_are_in(self):
        got = settings.set_switch('on', True)
        self.assertEqual((got['hand']['on'], got['carry_on']), (ready.HAND_READY, ready.HAND_READY))
        self.assertIs(got['hand']['write'], False)
        self.assertEqual(settings.read(), got)

    def test_the_rule_hands_nothing_where_the_hand_is_not_in(self):
        settings.set_switch('on', True)
        table = Table(routes={'review': (RouteChoice('codex'),)})
        rooms = {p: contract.Room(p, contract.ROOM, read_at=NOW) for p in ('claude', 'codex')}
        got = plan.build(NOW, table, ['claude', 'codex'], rooms, {}, {}, {}, settings.read(), 'switchboard')
        self.assertIs(got['hand']['on'], ready.HAND_READY)
        self.assertIs(got['carry_on'], ready.HAND_READY)
        answer = resolve.resolve(got, 'review', 'claude', NOW + 1, 'helper')
        if ready.HAND_READY:
            self.assertEqual((answer['do'], answer.get('to')), ('hand', 'codex'), answer)
        else:
            self.assertEqual(answer['do'], 'nothing', answer)
            self.assertIn(('codex', 'hand_off'), [tuple(x) for x in answer['passed']])

    def test_every_place_a_person_reads_says_which(self):
        # The first run is not one of these places: it names no feature that is not in this version, and no app that
        # is not on this Mac (its words are wire.turn_on's; tests/test_router_first_run_seam.py).
        self.assertEqual(hand_words.FIRST_RUN, [])
        commands = read('docs', 'commands.md')
        rows = [x for x in commands.splitlines() if x.startswith('| `switchboard routing` |')]
        self.assertEqual(len(rows), 1, 'docs/commands.md has one row for `switchboard routing`')
        how = read('docs', 'how-it-works.md')
        self.assertIn('### Routing', how)
        skill = read('switchboard', 'skill', 'reference', 'routing.md')
        for name, text in (('docs/commands.md', rows[0]), ('docs/how-it-works.md', how),
                           ('the skill', skill)):
            with self.subTest(place=name):
                if ready.HAND_READY:
                    self.assertNotIn(NOT_YET, text)
                    self.assertIn(EARLY, text)
                else:
                    self.assertIn(NOT_YET, text)
                    self.assertNotIn(EARLY, text)

    def test_the_plugin_offers_no_helper_while_the_hand_is_not_in(self):
        # A file in the plugin's agents/ folder is listed to Claude in every session that loads the plugin (Claude
        # Code's own `plugin details` counts it). The hand-over's helper, a helper with a shell, belongs there only
        # with the plugin code that starts and guards it.
        agents = os.path.join(ROOT, 'switchboard', 'mods', 'switchboard-router', 'agents')
        kept = os.path.join(ROOT, 'switchboard', 'router', 'agents', 'switchboard-hand.md')
        if ready.HAND_READY:
            self.assertTrue(os.path.isfile(os.path.join(agents, 'switchboard-hand.md')))
        else:
            self.assertFalse(os.path.exists(agents), os.listdir(agents) if os.path.isdir(agents) else agents)
            self.assertTrue(os.path.isfile(kept))

    def test_the_command_says_which(self):
        from switchboard.router import terminal
        out = terminal.show(NOW)
        self.assertEqual(hand_words.CODEX_SIGN_IN in out, ready.HAND_READY)
        self.assertEqual(hand_words.HAND_NOT_YET in out, not ready.HAND_READY)


if __name__ == '__main__':
    unittest.main()
