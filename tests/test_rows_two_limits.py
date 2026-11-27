import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# An account shows at most two limits, in the terminal and in the menu bar as on the page:
#
# - 5-hour only when there is a 5-hour number to show: the reading has a 5-hour window with a number, and it is not
#   too old to show. No 5-hour line for an account with no 5-hour limit, for a reading too old to show, or for one
#   never read. A 5-hour window that has not started (0%, no reset) is a number: it stays.
# - Weekly always: a dash when it is not known.
# - No line for a model's own week. A model week that auto-switch counts, at or over its switch line, is one short
#   red note at the end of the weekly line: its name and its percent (the highest, when several are). Never for a
#   model week auto-switch does not count, never under its line.
# - The switch line names only what the rows show: no model, and no 5-hour line when no account has a 5-hour limit.
#
# Made-up *.example accounts only. No network, no engine, no page.
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import core, demo, menubar, term

TZ = timezone(timedelta(hours=-8))                                      # Fri Nov 13 2026 is in winter time
NOW = datetime(2026, 11, 13, 15, 31, 25, tzinfo=TZ).timestamp()          # Fri 3:31 PM
SAT_NOON = '2026-11-14T19:59:59.651000+00:00'                            # 11:59:59.65 as Anthropic sends it: noon
ULTRA, TEAM, OLD, NEVER, FULL = ('ana@ultra.example', 'ana@team.example', 'ana@old.example', 'ana@never.example',
                                 'ana@full.example')


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ).isoformat()


def w(pct, resets=None):
    return {'pct': pct, 'resets': resets, 'pace': None, 'lasts': None}


def fable(pct, resets=SAT_NOON, name='Fable'):
    return {'name': name, 'pct': pct, 'resets': resets}


def acct(slot, email, session=None, weekly=None, scoped=(), **more):
    """One account as server.render hands it to the terminal and the menu bar."""
    return dict({'email': email, 'name': email, 'alias': '', 'slot': slot, 'plan': 'Ultra', 'org': '', 'active': False,
                 'needs_login': False, 'disabled': False, 'unmanaged': False, 'trusted': True, 'line': None,
                 'usage': {'session': session, 'weekly': weekly, 'scoped': list(scoped), 'spend': None},
                 'usage_at': NOW - 60}, **more)


def view(**more):
    """An Ultra account (no 5-hour limit, a Fable week under its line), a Team one whose five hours have not
    started, one whose reading is too old to show, one never read, and one whose counted Fable week is over its line."""
    return dict({'now': NOW, 'active': ULTRA, 'next': None, 'setup': {'engine': True, 'accounts': True},
                 'auto': {'available': True, 'enabled': True, 'on': True, 'session_at': 90.0, 'weekly_at': 95.0,
                          'model_at': 95.0, 'weekly_set': True, 'model': 'Fable', 'models': ['Fable']},
                 'accounts': [acct(1, ULTRA, weekly=w(27, SAT_NOON), scoped=[fable(22)], active=True),
                              acct(2, TEAM, session=w(0), weekly=w(17, at(16, 15)), plan='Team'),
                              acct(3, OLD, session=w(40, at(13, 18)), weekly=w(70, at(16, 12)), plan='Team',
                                   trusted=False, usage_at=NOW - 4 * 86400),
                              acct(4, NEVER, usage=None, usage_at=None),
                              acct(5, FULL, weekly=w(78, at(16, 13)), scoped=[fable(97, at(16, 13))])]}, **more)


def ultras_only(v):
    """The same view with only the accounts that have no 5-hour limit."""
    v['accounts'] = [a for a in v['accounts'] if a['email'] in (ULTRA, FULL)]
    return v


# ---------- the terminal ----------

class TheTerminal(unittest.TestCase):
    def limits(self, v=None):
        return dict((a['email'], [(l['label'], l['pct']) for l in a['limits']])
                    for a in term.read(v or view(), tz=TZ)['accounts'])

    def blocks(self, v=None, colour=False):
        got = term.view(term.read(v or view(), tz=TZ), colour=colour)
        return dict((next(e for e in (ULTRA, TEAM, OLD, NEVER, FULL) if e in b.split('\n')[0]), b)
                    for b in '\n'.join(got).split('\n\n')[1:])

    def test_no_5_hour_line_without_a_5_hour_number(self):
        got = self.limits()
        self.assertEqual(got[ULTRA], [('weekly', 27)])                           # no 5-hour limit
        self.assertEqual(got[OLD], [('weekly', None)])                           # too old to show
        self.assertEqual(got[NEVER], [('weekly', None)])                         # never read
        v = view()
        v['accounts'][1]['usage']['session'] = w(None, at(13, 18))               # a window with no number
        self.assertEqual(self.limits(v)[TEAM], [('weekly', 17)])
        blocks = self.blocks()
        for email in (ULTRA, OLD, NEVER, FULL):
            self.assertNotIn('5-hour', blocks[email], email)
        self.assertRegex(blocks[OLD], r'(?m)^   weekly  ─{24}     —$')            # a dash, its age on the line above
        self.assertIn('checked 4 days ago', blocks[OLD].split('\n')[0])

    def test_a_5_hour_window_at_0_not_started_stays(self):
        self.assertEqual(self.limits()[TEAM], [('5-hour', 0), ('weekly', 17)])
        self.assertRegex(self.blocks()[TEAM], r'(?m)^   5-hour  ─{24}    0%  not started$')

    def test_no_line_for_a_models_week(self):
        for email, lims in self.limits().items():
            self.assertEqual([l for l, _ in lims if l not in ('5-hour', 'weekly')], [], email)
        self.assertNotRegex('\n'.join(self.blocks().values()), r'(?m)^\s+Fable\b')

    def test_a_counted_model_week_over_its_line_is_a_red_note_at_the_end_of_the_weekly_line(self):
        blocks = self.blocks()
        self.assertEqual(blocks[FULL].split('\n')[1],
                         '   weekly  ' + '━' * 19 + '─' * 5 + '   78%  resets Mon 1:00 PM · in 3 days'
                         ' · at this pace, full by Sat 7:17 PM  Fable 97%')
        self.assertNotIn('Fable', blocks[ULTRA])                                  # 22%, under its line: nothing
        coloured = self.blocks(colour=True)[FULL].split('\n')[1]
        self.assertTrue(coloured.endswith('full by Sat 7:17 PM  \x1b[91mFable 97%\x1b[0m'),
                        coloured)
        week = dict((a['email'], a['limits'][-1]) for a in term.read(view(), tz=TZ)['accounts'])
        self.assertEqual(week[FULL]['note'], {'label': 'Fable', 'pct': 97})
        self.assertFalse(week[FULL]['over'])                         # the weekly limit itself is under its line
        self.assertIsNone(week[ULTRA]['note'])

    def test_the_note_is_only_at_or_over_the_counted_line(self):
        def note(pct, models=('Fable',), model_at=95.0, name='Fable'):
            v = view()
            v['auto'].update(models=list(models), model_at=model_at)
            v['accounts'][4]['usage']['scoped'] = [fable(pct, at(16, 13), name)]
            return [a for a in term.read(v, tz=TZ)['accounts'] if a['email'] == FULL][0]['limits'][-1]['note']
        self.assertEqual(note(95), {'label': 'Fable', 'pct': 95})               # at its line
        self.assertEqual(note(94.6), {'label': 'Fable', 'pct': 95})             # rounds to it, as the % does
        self.assertIsNone(note(94.4))                                            # under it
        self.assertIsNone(note(99, models=()))                                   # a week auto-switch does not count
        self.assertIsNone(note(99, models=('Opus',)))
        self.assertEqual(note(99, name='Fable 5.2'), {'label': 'Fable 5.2', 'pct': 99})    # 'Fable' counts 'Fable 5.2'
        self.assertEqual(note(91, model_at=90.0), {'label': 'Fable', 'pct': 91})           # its own line, as set
        v = view()
        v['auto']['models'] = ['Fable', 'Opus']
        v['accounts'][4]['usage']['scoped'] = [fable(96, at(16, 13)), fable(99, at(16, 13), 'Opus'),
                                               fable(98, at(16, 13), 'Sonnet')]          # Sonnet's is not counted
        got = [a for a in term.read(v, tz=TZ)['accounts'] if a['email'] == FULL][0]['limits'][-1]['note']
        self.assertEqual(got, {'label': 'Opus', 'pct': 99})                       # the highest of those counted

    def test_on_a_narrow_terminal_the_note_stays_and_its_reset_says_less(self):
        got = term.view(term.read(view(), tz=TZ), width=60)
        self.assertLessEqual(max(len(x) for x in got), 60)
        self.assertIn('78%  full by Sat 7:17 PM  Fable 97%', '\n'.join(got))       # the pace words say less too
        self.assertIn('27%  resets Sat 12:00 PM · in 20 hours', '\n'.join(got))      # a line with no note keeps it all

    def test_the_note_can_be_turned_off_in_one_place(self):
        saved = term.MODEL_OVER_NOTE
        self.addCleanup(setattr, term, 'MODEL_OVER_NOTE', saved)
        term.MODEL_OVER_NOTE = False
        self.assertNotIn('Fable', self.blocks()[FULL])

    def test_the_header_names_only_what_the_rows_show(self):
        header = term.view(term.read(view(), tz=TZ))[0]
        self.assertEqual(header, 'Claude Code is on ana@ultra.example · auto-switch on at 90% 5-hour · 95% weekly')
        header = term.view(term.read(ultras_only(view()), tz=TZ))[0]
        self.assertEqual(header, 'Claude Code is on ana@ultra.example · auto-switch on at 95% weekly')
        # the 5-hour line of an account whose reading is too old still counts: the plan has a 5-hour limit
        v = view()
        v['accounts'] = [a for a in v['accounts'] if a['email'] in (ULTRA, OLD)]
        self.assertIn('at 90% 5-hour · 95% weekly', term.view(term.read(v, tz=TZ))[0])
        # an account Switchboard does not keep does not count
        v = ultras_only(view())
        v['accounts'].append(acct(6, TEAM, session=w(10, at(13, 18)), weekly=w(5, at(16, 15)), unmanaged=True))
        self.assertIn('auto-switch on at 95% weekly', term.view(term.read(v, tz=TZ))[0])

    def test_switchboard_auto_still_says_the_model_week_it_counts(self):
        """`switchboard auto` says the settings in words, as the page's Settings does: the model week stays there."""
        self.assertEqual(term.auto_line(view()['auto']),
                         'Auto-switch is on: at 90% 5-hour · 95% weekly, Fable too, using what refills first.')


# ---------- the menu bar ----------

class TheMenuBar(unittest.TestCase):
    def rows(self, v=None):
        return dict((r['email'], r) for r in menubar.rows(v or view(), TZ))

    def test_no_5_hour_row_without_a_5_hour_number(self):
        rows = self.rows()
        for email in (ULTRA, OLD, NEVER, FULL):
            self.assertIsNone(rows[email]['five'], email)
            labels = [op[5][0][0] for op in menubar.row_ops(rows[email]) if op[0] == 'text']
            self.assertEqual(labels[0::2], ['Weekly'], email)                      # one bar row: Weekly
        old = rows[OLD]
        self.assertEqual(menubar.pct_text(old['week']), '—')
        self.assertEqual(menubar.row_height(old), 54.0)                           # the email line and one bar row
        self.assertEqual(menubar.row_label(old), 'Use ana@old.example, weekly —')
        v = view()
        v['accounts'][1]['usage']['session'] = w(None, at(13, 18))                # a 5-hour window with no number
        self.assertIsNone(self.rows(v)[TEAM]['five'])

    def test_a_5_hour_row_at_0_not_started_stays(self):
        team = self.rows()[TEAM]
        self.assertEqual((team['five']['label'], menubar.pct_text(team['five'])), ('5-hour', '0%'))
        self.assertFalse(team['five']['started'])                                 # Anthropic's "not started"
        self.assertEqual(menubar.row_height(team), 72.0)

    def test_a_models_week_is_the_pages_to_say(self):
        rows = self.rows()
        full = [lim['label'] for lim in menubar.limits(next(a for a in view()['accounts'] if a['email'] == FULL),
                                                      view()['auto'])]
        self.assertEqual(full, ['Weekly', 'Fable'])                    # counted, at its line: the page says it
        self.assertNotIn('Fable', repr(menubar.row_ops(rows[FULL])))              # the menu bar's row does not

    def test_the_item_for_a_reading_not_known_is_the_s_dimmed(self):
        v = view()
        for a in v['accounts']:
            a['active'] = a['email'] == OLD
        v['active'] = OLD
        r = menubar.reading(v)
        self.assertEqual((r['dot'], r['dim']), (None, True))
        self.assertEqual(set(op[-1] for op in menubar.item_ops(r)), {'ink50'})
        self.assertEqual(r['label'], 'Switchboard: ana@old.example, weekly —')

    def test_the_auto_switch_item_names_only_what_the_rows_show(self):
        def words(v):
            return next(x for x in menubar.menu(v) if x[0] == 'auto')[2]
        self.assertEqual(words(view()), 'at 90% 5-hour · 95% weekly')
        self.assertEqual(words(ultras_only(view())), 'at 95% weekly')


# ---------- the demo ----------

class TheDemo(unittest.TestCase):
    """The demo shows each kind of row: no 5-hour limit, a 5-hour number, the note, and a reading too old to show."""

    def setUp(self):
        self.addCleanup(setattr, core, 'DEMO', core.DEMO)
        core.DEMO = True
        demo.STATE.clear()
        demo.FACTS.clear()
        self.addCleanup(demo.STATE.clear)
        self.addCleanup(demo.FACTS.clear)

    def test_each_kind_of_row_is_there(self):
        v = demo.view()
        rows = dict((a['email'], a) for a in term.read(v)['accounts'])
        lines = dict((e, [(l['label'], l['pct'] is not None) for l in r['limits']]) for e, r in rows.items())
        mail = next(a for a in v['accounts'] if a['email'] == 'maya.reyes@mail.example')
        self.assertIsNone(mail['usage']['session'])                                       # no 5-hour limit,
        self.assertEqual(lines['maya.reyes@mail.example'], [('weekly', False)])           # and too old to show
        self.assertIn('checked 1 day ago', rows['maya.reyes@mail.example']['age'])
        self.assertEqual(lines['maya@northwind.example'], [('5-hour', True), ('weekly', True)])   # a 5-hour number
        self.assertEqual(rows['m.reyes@harbor.example']['limits'][-1]['note'], {'label': 'Fable', 'pct': 97})
        self.assertEqual([e for e, r in rows.items() if r['limits'][-1].get('note')], ['m.reyes@harbor.example'])

    def test_the_plans_order_and_next_are_as_they_were(self):
        v = demo.view()
        self.assertEqual(v['next'], 'maya@ivy.example')
        self.assertEqual([o['email'] for o in v['plan']['order']],
                         ['maya@northwind.example', 'maya@ivy.example', 'maya@fernhill.example', 'm.reyes@harbor.example',
                          'maya.reyes@mail.example'])
        self.assertEqual(next(o for o in v['plan']['order'] if o['email'] == 'm.reyes@harbor.example')['usable'], False)


if __name__ == '__main__':
    unittest.main()
