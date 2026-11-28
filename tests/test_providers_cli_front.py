import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The front door's commands, word for word with their exit codes, on a Kit of stand-ins (tests/fakes_provider_kit.py)
and the real routes, list of models, task file and kinds.

- `route` says the kind and the word that decided it (or that the person named it), the pick with its model and
  effort, the reason, and each provider passed with why; it starts nothing and writes nothing; --json says the same.
- `routes` shows the table kind by kind (a kind with no route follows the order) and which preset is on; set, reset
  and preset each answer in one line.
- `do`: not at a terminal it prints the line to run and starts nothing; at a terminal it asks the yes for this folder
  and provider where continue asks it (a never and a stay are honoured), writes the task file, records a task row
  and one History line, and ends in one exec whose argument list holds the model option when the app's version takes
  it, and no word of the task.
Every name, folder and word is made up.
"""
import errno
import json
import os
import stat
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

from fakes_provider_kit import NOW, KitCase, claude_limits, codex_found, codex_limits  # noqa: E402
from switchboard import core  # noqa: E402
from switchboard.providers import catalog, kinds, routes, run as provider_run, words  # noqa: E402

CANARY = 'QZCANARYQZ'
WEEK2 = NOW + 2 * 86400                     # when the made-up Codex refills (fakes_provider_kit.codex_limits)
HOUR = NOW + 3600                           # when the made-up Claude accounts refill (claude_limits)
TABLE_END = ('Presets: by-the-job. switchboard routes preset NAME turns one on, and switchboard routes preset off '
             'turns it off.')


def when(t):
    return words.when(t, NOW)


def remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def made_up_preset(test, rows):
    """A presets file of one made-up preset ('made-up'), in place of the shipped one for this test."""
    import shutil
    import tempfile
    from unittest import mock
    folder = tempfile.mkdtemp(prefix='sb-presets-')
    test.addCleanup(shutil.rmtree, folder, True)
    path = os.path.join(folder, 'route_presets.json')
    doc = {'v': 1, 'presets': {'made-up': {'rows': {
        k: {'choices': v, 'rests_on': 'A made-up line.', 'source': 'made up', 'read': '2026-11-25'}
        for k, v in rows.items()}}}}
    with open(path, 'w') as f:
        json.dump(doc, f)
    patcher = mock.patch.object(routes, 'PRESETS', path)
    patcher.start()
    test.addCleanup(patcher.stop)


def review_route():
    routes.set_kind('review', ['codex:gpt-6.2-astra:high', 'claude:sonnet'])


class Base(KitCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        for rel in (routes.ROUTES_FILE, catalog.OWN_FILE):
            path = core.data_path(rel)
            remove(path)
            self.addCleanup(remove, path)

    def assert_nothing_happened(self, r):
        self.assertEqual(r.calls.execs, [])
        self.assertEqual(r.said, [])
        self.assertEqual(r.tasks.rows, {})
        self.assertEqual(r.handoff.tasks_written, [])
        self.assertEqual(r.asked, [])


class Route(Base):
    def test_no_route_the_order_decides(self):
        r = self.rig()
        self.assertEqual(r('route', 'review', 'the', 'parser'), 0)
        self.assertEqual(r.lines(), [
            'This reads as a review, from the word "review". It goes to Claude, the first in your order: no route is '
            'set for reviews.',
            'Claude starts on its own default model.'])
        self.assert_nothing_happened(r)

    def test_the_first_choice_of_the_route(self):
        review_route()
        r = self.rig()
        self.assertEqual(r('route', '--', 'review the parser'), 0)
        self.assertEqual(r.lines(), [
            'This reads as a review, from the word "review". It goes to Codex, the first choice in your routes for '
            'reviews.',
            'It asks Codex for GPT-6.2 Astra at high effort.'])

    def test_a_later_choice_says_what_was_passed(self):
        review_route()
        r = self.rig()
        r.codex.limits_ = codex_limits(pct=99.0)
        self.assertEqual(r('route', 'review the parser'), 0)
        self.assertEqual(r.lines(), [
            'This reads as a review, from the word "review". It goes to Claude, the next choice in your routes for '
            'reviews.',
            'It asks Claude for Sonnet.',
            'Codex was passed: it is out until %s.' % when(WEEK2)])

    def test_the_kind_named(self):
        r = self.rig()
        self.assertEqual(r('route', '--as', 'find', 'review the parser'), 0)
        self.assertEqual(r.lines()[0], 'You named this a lookup. It goes to Claude, the first in your order: no '
                                       'route is set for lookups.')

    def test_a_phrase_is_words_and_no_second_bracket(self):
        r = self.rig()
        self.assertEqual(r('route', 'why does the cache never expire'), 0)
        self.assertEqual(r.lines()[0], 'This reads as a fix, from the words "why does". It goes to Claude, the first in '
                                       'your order: no route is set for fixes.')
        r = self.rig()
        r('route', 'where is the retry logic')
        self.assertTrue(r.lines()[0].startswith('This reads as a lookup, from the word "where". '), r.lines()[0])
        r = self.rig()
        r('route', 'draft the notes')
        self.assertTrue(r.lines()[0].startswith('This reads as a piece of writing, from the word "draft". '))
        for name, (one, many) in words.KIND_NAMES.items():
            self.assertNotIn('(', one + many, name)

    def test_no_word_of_the_table(self):
        r = self.rig()
        self.assertEqual(r('route', 'hello', 'there'), 0)
        self.assertEqual(r.lines()[0], 'No word of it says what kind of job it is, so it counts as another kind of job. '
                                       'It goes to Claude, the first in your order: no route is set for other jobs.')

    def test_a_provider_named_takes_the_routes_model_for_it(self):
        review_route()
        r = self.rig()
        self.assertEqual(r('route', '--on', 'codex', 'review it'), 0)
        self.assertEqual(r.lines(), [
            'This reads as a review, from the word "review". It goes to Codex, as you named it.',
            'It asks Codex for GPT-6.2 Astra at high effort.'])

    def test_a_provider_named_that_is_out(self):
        r = self.rig()
        r.codex.limits_ = codex_limits(pct=99.0)
        self.assertEqual(r('route', '--on', 'codex', 'fix it'), 0)
        self.assertEqual(r.lines()[1], 'Codex is out until %s; it starts all the same, since you named it.'
                         % when(WEEK2))

    def test_nobody_can_take_it(self):
        r = self.rig()
        r.claude.limits_ = claude_limits(under=0)
        r.codex.limits_ = codex_limits(pct=99.0)
        self.assertEqual(r('route', 'review it'), 1)
        self.assertEqual(r.lines(), [
            'This reads as a review, from the word "review".',
            'Nobody can take this task now. Nothing was started.',
            'Claude: every account is out until %s.' % when(HOUR),
            'Codex: it is out until %s.' % when(WEEK2),
            'The first one is back at %s.' % when(HOUR)])

    def test_a_preset_row_follows_the_persons_order(self):
        made_up_preset(self, {'find': ['codex:gpt-6-luna:medium', 'claude:haiku:medium']})
        routes.preset('made-up')
        r = self.rig()
        self.assertEqual(r('route', 'where is the retry logic'), 0)
        self.assertEqual(r.lines()[1], 'It asks Claude for Haiku at medium effort.')
        r = self.rig(order=['codex', 'claude'])
        self.assertEqual(r('route', 'where is the retry logic'), 0)
        self.assertEqual(r.lines()[1], 'It asks Codex for GPT-6 Luna at medium effort.')

    def test_a_never_is_passed_in_the_pick(self):
        routes.set_kind('fix', ['codex', 'claude'])
        r = self.rig()
        r.choices.set_consent(r.folder, 'codex', 'never', NOW)
        self.assertEqual(r('route', 'fix the parser'), 0)
        self.assertEqual(r.lines(), [
            'This reads as a fix, from the word "fix". It goes to Claude, the next choice in your routes for fixes.',
            'Claude starts on its own default model.',
            "Codex was passed: this folder's chats never go to it."])

    def test_a_stay_decides(self):
        r = self.rig()
        r.choices.set_stay(r.folder, 'codex', NOW)
        self.assertEqual(r('route', 'review it'), 0)
        self.assertEqual(r.lines()[0], 'This reads as a review, from the word "review". It goes to Codex: this folder '
                                       'stays on Codex.')

    def test_a_stay_on_one_whose_numbers_are_not_known(self):
        r = self.rig()
        r.codex.limits_ = codex_limits(read_ago=8 * 86400.0)       # older than its numbers are trusted
        r.choices.set_stay(r.folder, 'codex', NOW)
        self.assertEqual(r('route', 'review it'), 0)
        self.assertEqual(r.lines()[0], 'This reads as a review, from the word "review". It goes to Codex: this folder '
                                       "stays on Codex, and Codex's numbers are not known.")

    def test_a_provider_named_against_a_stay(self):
        r = self.rig()
        r.choices.set_stay(r.folder, 'codex', NOW)
        self.assertEqual(r('route', '--on', 'claude', 'review it'), 1)
        self.assertEqual(r.lines()[1], 'This folder stays on Codex. switchboard stay off lifts that.')

    def test_a_provider_named_that_is_not_added(self):
        r = self.rig(codex_added=False)
        self.assertEqual(r('route', '--on', 'codex', 'review it'), 1)
        self.assertEqual(r.lines()[1], 'Codex is not added. switchboard add codex adds it.')

    def test_json(self):
        review_route()
        r = self.rig()
        r.codex.limits_ = codex_limits(pct=99.0)
        self.assertEqual(r('route', '--json', 'review it'), 0)
        got = json.loads(r.text())
        self.assertEqual({k: got[k] for k in ('v', 'kind', 'by', 'word', 'provider', 'model', 'effort', 'reason',
                                              'stay', 'route')},
                         {'v': 1, 'kind': 'review', 'by': 'word', 'word': 'review', 'provider': 'claude',
                          'model': 'sonnet', 'effort': None, 'reason': 'route_next', 'stay': None,
                          'route': ['codex:gpt-6.2-astra:high', 'claude:sonnet']})
        self.assertEqual(got['passed'], [{'provider': 'codex', 'why': 'out', 'back_at': WEEK2}])
        self.assertEqual(got['says'][-1], 'Codex was passed: it is out until %s.' % when(WEEK2))

    def test_the_words_were_wrong(self):
        for args, first in ((('route',), words.HELP['route'].splitlines()[0]),
                            (('route', '--as', 'deploy', 'x'),
                             words.UNKNOWN_KIND.format(word='deploy', kinds=', '.join(kinds.KINDS))),
                            (('route', '--on', 'zeta', 'x'), words.UNKNOWN_PROVIDER.format(word='zeta',
                                                                                          ids='claude, codex')),
                            (('route', '--model', 'x', 'y'), words.HELP['route'].splitlines()[0]),
                            (('route', '--show', 'x'), words.HELP['route'].splitlines()[0])):
            with self.subTest(args=args):
                r = self.rig()
                self.assertEqual(r(*args), 2)
                self.assertEqual(r.lines()[0], first)
                self.assertIn(words.HELP['route'], r.text())


class Routes(Base):
    def rows(self, **set_):
        return ['  %-7s %s' % (k, set_.get(k, 'follows your order (Claude, then Codex)')) for k in kinds.KINDS]

    def test_the_table_of_a_fresh_install(self):
        r = self.rig()
        self.assertEqual(r('routes'), 0)
        self.assertEqual(r.lines(), ['Routes, kind by kind (no preset is on):'] + self.rows() + [TABLE_END])

    def test_set_then_the_table(self):
        r = self.rig()
        self.assertEqual(r('routes', 'set', 'Review', 'codex:gpt-6.2-astra:high', 'Claude:Sonnet'), 0)
        self.assertEqual(r.lines(), ['The route for review is now codex:gpt-6.2-astra:high, then claude:sonnet.'])
        r = self.rig()
        r('routes')
        self.assertEqual(r.lines()[1:-1], self.rows(review='codex:gpt-6.2-astra:high, then claude:sonnet'))

    def test_set_refused(self):
        r = self.rig()
        self.assertEqual(r('routes', 'set', 'review', 'codex:gpt-9'), 1)
        known = ', '.join(m.id for m in catalog.models('codex'))
        self.assertEqual(r.lines(), [words.UNKNOWN_MODEL.format(name='Codex', word='gpt-9', known=known)])
        r = self.rig()
        self.assertEqual(r('routes', 'set', 'deploy', 'codex'), 2)
        self.assertEqual(r.lines()[0], words.UNKNOWN_KIND.format(word='deploy', kinds=', '.join(kinds.KINDS)))
        r = self.rig()
        self.assertEqual(r('routes', 'set', 'review'), 2)
        self.assertEqual(r.lines()[0], words.ROUTES_SET_USAGE)
        self.assertEqual(dict(routes.load().routes), {})

    def test_reset_one_then_all(self):
        review_route()
        routes.set_kind('fix', ['codex'])
        r = self.rig()
        self.assertEqual(r('routes', 'reset', 'review'), 0)
        self.assertEqual(r.lines(), ['No route for review now: it follows your order (Claude, then Codex).'])
        r = self.rig()
        self.assertEqual(r('routes', 'reset'), 0)
        self.assertEqual(r.lines(), ['No routes now: every kind follows your order (Claude, then Codex).'])
        self.assertEqual(dict(routes.load().routes), {})

    def test_preset_on_then_off(self):
        r = self.rig()
        self.assertEqual(r('routes', 'preset', 'by-the-job'), 0)
        self.assertEqual(r.lines(), ['The preset by-the-job is on: it fills find and write, tried in your order '
                                     '(Claude, then Codex).'])
        r = self.rig()
        r('routes')
        self.assertEqual(r.lines(), ['Routes, kind by kind (the preset by-the-job is on):'] + self.rows(
            find='claude:haiku:medium, then codex:gpt-6-luna:medium (from the preset, in your order)',
            write='claude:sonnet:medium, then codex:gpt-6.2-sol:medium (from the preset, in your order)') + [TABLE_END])
        r = self.rig()
        self.assertEqual(r('routes', 'preset', 'off'), 0)
        self.assertEqual(r.lines(), ['No preset is on now: each route it set is back to what it was before, unless '
                                     'you changed it since.'])
        r = self.rig()
        self.assertEqual(r('routes', 'preset', 'off'), 0)
        self.assertEqual(r.lines(), ['No preset was on, so nothing changed.'])
        r = self.rig()
        self.assertEqual(r('routes', 'preset', 'fastest'), 1)
        self.assertEqual(r.lines(), [words.UNKNOWN_PRESET.format(word='fastest', names='by-the-job')])

    def test_a_preset_leaves_the_persons_own_route(self):
        routes.set_kind('find', ['codex'])
        r = self.rig()
        self.assertEqual(r('routes', 'preset', 'by-the-job'), 0)
        self.assertEqual(r.lines(), ['The preset by-the-job is on: it fills write, tried in your order (Claude, then '
                                     'Codex).',
                                     'Your own route for find stays.'])
        self.assertEqual(routes.load().get('find'), (routes.parse('codex'),))
        routes.set_kind('write', ['claude'])
        routes.preset(None)
        r = self.rig()
        r('routes', 'preset', 'by-the-job')
        self.assertEqual(r.lines(), ['The preset by-the-job is on, and fills no kind.',
                                     'Your own routes for find and write stay.'])

    def test_a_preset_row_is_shown_in_the_order_it_is_tried(self):
        made_up_preset(self, {'find': ['codex:gpt-6-luna:medium', 'claude:haiku:medium']})
        routes.preset('made-up')
        r = self.rig()
        r('routes')
        self.assertIn('  find    claude:haiku:medium, then codex:gpt-6-luna:medium (from the preset, in your order)',
                      r.lines())
        r = self.rig(order=['codex', 'claude'])
        r('routes')
        self.assertIn('  find    codex:gpt-6-luna:medium, then claude:haiku:medium (from the preset, in your order)',
                      r.lines())

    def test_json(self):
        review_route()
        r = self.rig()
        self.assertEqual(r('routes', '--json'), 0)
        self.assertEqual(json.loads(r.text()), {
            'v': 1, 'kinds': list(kinds.KINDS), 'order': ['claude', 'codex'], 'preset': None, 'from_preset': [],
            'presets': ['by-the-job'], 'routes': {'review': ['codex:gpt-6.2-astra:high', 'claude:sonnet']}})

    def test_words_it_does_not_take(self):
        for args in (('routes', 'list'), ('routes', 'preset'), ('routes', 'reset', 'a', 'b'), ('routes', '--all')):
            with self.subTest(args=args):
                r = self.rig()
                self.assertEqual(r(*args), 2)
                self.assertIn(words.HELP['routes'], r.text())


class AFileOfRoutesThatCannotBeUsed(Base):
    BAD = '{"v":1,"routes":{"fix":[{"provider":"x\\u001b[2J"}]}}'

    def setUp(self):
        super().setUp()
        path = core.data_path(routes.ROUTES_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(self.BAD)
        self.sentence = words.ROUTES_BAD.format(path=path)

    def test_routes_route_and_do_each_say_it_in_one_sentence(self):
        for args, tty in ((('routes',), False), (('routes', '--json'), False), (('route', 'fix it'), False),
                          (('do', 'fix it'), True), (('do', '--show', 'fix it'), True)):
            with self.subTest(args=args):
                r = self.rig(tty=tty)
                self.assertEqual(r(*args), 1)
                self.assertEqual(r.lines(), [self.sentence])
                self.assertNotIn('\x1b', r.text())
                self.assert_nothing_happened(r)

    def test_a_name_from_outside_is_printed_on_one_line(self):
        from switchboard.providers import cli
        r = self.rig()
        ctx = cli._Ctx(r.kit)
        self.assertEqual(ctx.name('x\x1b[2Jy'), 'Xy')


class TasksThatCannotBeTaken(Base):
    def test_characters_that_cannot_be_written_are_refused_first(self):
        task = 'fix the parser \udcff now'                    # a byte that is not UTF-8, as Python hands it over
        for args, tty in ((('do', task), True), (('do', task), False), (('do', '--show', task), True),
                          (('route', task), False)):
            with self.subTest(args=args, tty=tty):
                r = self.rig(tty=tty)
                self.assertEqual(r(*args), 1)
                self.assertEqual(r.lines(), [words.TASK_NOT_TEXT])
                self.assert_nothing_happened(r)
                self.assertEqual(r.choices.consent(r.folder, 'claude'), None)


class TasksTooLong(Base):
    def test_a_task_too_long_for_the_file_is_refused_not_cut(self):
        from switchboard.providers import handoff
        cap = sum(handoff.PERSON_NEWEST)
        self.assertEqual(cap, 24000)
        long_task = 'fix the parser ' + 'x' * 31000
        for args, tty in ((('do', long_task), True), (('do', long_task), False), (('route', long_task), False),
                          (('do', '--show', 'fix ' + 'é' * 12000), True)):        # 24,004 bytes
            with self.subTest(size=len(args[-1].encode()), tty=tty):
                r = self.rig(tty=tty)
                self.assertEqual(r(*args), 1)
                self.assertEqual(r.lines(), [words.TASK_TOO_LONG.format(
                    cap='24 KB', size=words.size(len(args[-1].encode('utf-8'))))])
                self.assert_nothing_happened(r)
        r = self.rig()
        self.assertEqual(r('route', 'fix ' + 'x' * (cap - 4)), 0)                 # just fits

    def test_the_sentence(self):
        self.assertEqual(words.TASK_TOO_LONG.format(cap='24 KB', size='31 KB'),
                         'A task may be up to 24 KB, and this one is 31 KB: put the detail in a file in this folder and '
                         'name that file in the task.')


class FoldersTooWide(Base):
    def test_do_refuses_the_home_folder_slash_and_a_folder_holding_the_home(self):
        r0 = self.rig()
        for folder in (r0.reach.home, os.sep, os.path.dirname(r0.reach.home)):
            for tty in (True, False):
                with self.subTest(folder=folder, tty=tty):
                    r = self.rig(tty=tty)
                    r.folder = os.path.realpath(folder)
                    self.allowed_all(r)
                    self.assertEqual(r('do', 'fix the parser'), 1)
                    self.assertEqual(r.lines(), [words.DO_TOO_WIDE.format(folder=os.path.realpath(folder))])
                    self.assertEqual((r.calls.execs, r.handoff.tasks_written, r.tasks.rows), ([], [], {}))

    def test_route_and_show_still_say_the_pick_there(self):
        r = self.rig(tty=True)
        r.folder = os.path.realpath(r.reach.home)
        self.assertEqual(r('do', '--show', 'fix the parser'), 0)
        self.assertEqual(r('route', 'fix the parser'), 0)

    def allowed_all(self, r):
        for pid in ('claude', 'codex'):
            r.choices.set_consent(r.folder, pid, 'yes', NOW - 3600)


class DoNotAtATerminal(Base):
    def test_it_prints_the_line_to_run_and_starts_nothing(self):
        r = self.rig()
        self.assertEqual(r('do', 'review the parser'), 0)
        self.assertEqual(r.lines(), ['To start this task, run this in a terminal:',
                                     "  switchboard do -- 'review the parser'"])
        self.assert_nothing_happened(r)

    def test_with_its_options(self):
        r = self.rig()
        self.assertEqual(r('do', '--as', 'review', '--on', 'codex', '--model', 'gpt-6-luna', '--effort', 'low', '--',
                           "fix Kim's parser"), 0)
        self.assertEqual(r.lines()[1], "  switchboard do --as review --on codex --model gpt-6-luna --effort low -- "
                                       "'fix Kim'\"'\"'s parser'")
        self.assert_nothing_happened(r)


class Do(Base):
    def rig(self, **kw):
        kw.setdefault('tty', True)
        return super().rig(**kw)

    def allowed(self, r, pid='codex'):
        r.choices.set_consent(r.folder, pid, 'yes', NOW - 3600)

    def question(self, to='Codex', vendor='OpenAI', jobs=True):
        return words.DO_QUESTION.format(to=to, vendor=vendor, jobs=words.DO_QUESTION_JOBS if jobs else '', work='',
                                        basename='site').rstrip('\n')

    def written(self, r):
        self.assertEqual(len(r.handoff.tasks_written), 1)
        return r.handoff.tasks_written[0]

    def test_end_to_end_on_the_stand_ins(self):
        review_route()
        r = self.rig(answers=['y'])
        self.assertEqual(r('do', 'review the parser', CANARY), 0)
        note = self.written(r)
        first = words.TASK_WORDS.format(hop=note.hop, path=note.path)
        prog = r.codex.found_.program.path
        # the question, where continue asks it, and the yes kept for this folder and Codex
        self.assertEqual(r.asked, [words.PROMPT])
        self.assertIn(self.question(), r.text())
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'yes')
        # the task file: beside the data folder, private, the person's words in it
        notes = provider_run.notes_root(r.reach)
        self.assertTrue(note.path.startswith(notes + os.sep))
        self.assertRegex(os.path.relpath(note.path, notes), r'^t-[0-9a-f]{8}/h-[0-9a-f]{8}\.md\Z')
        self.assertEqual(stat.S_IMODE(os.stat(note.path).st_mode), 0o600)
        with open(note.path, encoding='utf-8') as f:
            text = f.read()
        self.assertIn('> review the parser %s' % CANARY, text.splitlines())
        self.assertIn('This reads as a review, from the word "review".', text.splitlines())
        # one exec: the program, its folder, the model and effort, the fixed first message; no word of the task
        self.assertEqual(len(r.calls.execs), 1)
        path, argv, env, cwd = r.calls.execs[0]
        self.assertEqual((path, cwd), (prog, r.folder))
        self.assertEqual(argv, (prog, '-C', r.folder, '--model', 'gpt-6.2-astra', '-c', 'model_reasoning_effort="high"',
                                first))
        for word in argv + tuple(env.values()):
            self.assertNotIn(CANARY, word)
            self.assertNotIn('parser', word)
        # the task row and the History line: codes and counts, no word of the task
        (task_id, row), = r.tasks.rows.items()
        self.assertEqual(row['job'], {'kind': 'review', 'by': 'word', 'provider': 'codex', 'model': 'gpt-6.2-astra',
                                      'effort': 'high', 'reason': 'route', 'passed': [], 'file': note.path,
                                      'bytes': note.bytes, 'removed': 0, 'state': 'started', 'version': '0.169.1'})
        self.assertEqual(os.path.basename(os.path.dirname(note.path)), task_id)
        self.assertEqual([(t, k) for t, k, _ in r.said], [('Chats in site may go to Codex', 'setting'),
                                                           ('Started Codex on a new task in site (a review)', 'info')])
        self.assertNotIn(CANARY, repr(r.said) + repr(r.tasks.rows))
        # what the person read first
        lines = r.lines()
        self.assertEqual(lines[:2], [
            'This reads as a review, from the word "review". It goes to Codex, the first choice in your routes for '
            'reviews.',
            'It asks Codex for GPT-6.2 Astra at high effort.'])
        self.assertEqual(lines[-2:], [
            'Starting Codex 0.169.1 (ChatGPT.app) in this folder.',
            'The task file: %s (%s; 0 places that looked like a secret taken out of your words, best effort).'
            % (note.path, words.size(note.bytes))])

    def test_the_model_option_absent_when_the_version_does_not_take_it(self):
        review_route()
        for version, extra, said in (
                ('0.150.0', (), ['Switchboard does not pass a model to Codex 0.150.0: it starts on its own default.',
                                 'Switchboard does not pass an effort to Codex 0.150.0: it starts at its own.']),
                ('0.155.1', ('--model', 'gpt-6.2-astra'),
                 ['It asks Codex for GPT-6.2 Astra.',
                  'Switchboard does not pass an effort to Codex 0.155.1: it starts at its own.'])):
            with self.subTest(version=version):
                r = self.rig(codex_found_=codex_found(self.root, version=version, seen=version), answers=['y'])
                self.assertEqual(r('do', 'review the parser'), 0)
                note = self.written(r)
                argv = r.calls.execs[0][1]
                self.assertEqual(argv[3:-1], extra)
                lines = r.lines()
                self.assertEqual(lines[1:1 + len(said)], said)                  # said before the yes is asked
                self.assertLess(lines.index(said[-1]), lines.index(self.question().splitlines()[0]))
                self.assertNotIn('It asks Codex for GPT-6.2 Astra at high effort.', lines)
                with open(note.path, encoding='utf-8') as f:
                    self.assertIn('Asked for: high', f.read().splitlines())
                for cmd in (('route', 'review the parser'), ('do', '--show', 'review the parser')):
                    s = self.rig(codex_found_=codex_found(self.root, version=version, seen=version))
                    self.assertEqual(s(*cmd), 0)
                    self.assertEqual(s.lines()[1:], said, cmd)                 # route shows what do will do

    def test_a_version_that_could_not_be_read(self):
        review_route()
        r = self.rig(codex_found_=codex_found(self.root, version=None, seen=None))
        self.assertEqual(r('route', 'review the parser'), 0)
        self.assertEqual(r.lines()[1:], ['Switchboard passes no model or effort to Codex: its version could not be '
                                         'read.'])

    def test_a_yes_given_before_asks_nothing(self):
        r = self.rig()
        self.allowed(r, 'claude')
        self.assertEqual(r('do', 'add a footer'), 0)
        self.assertEqual(r.asked, [])
        self.assertEqual(r.calls.execs[0][1][1:-1], ())          # Claude, no route: its own default model

    def test_never_at_the_question(self):
        review_route()
        r = self.rig(answers=['never'])
        self.assertEqual(r('do', 'review the parser'), 1)
        self.assertEqual(r.lines()[-1], 'Nothing was sent.')
        self.assertEqual(r.choices.consent(r.folder, 'codex'), 'never')
        self.assertEqual([(t, k) for t, k, _ in r.said], [('Chats in site never go to Codex', 'setting')])
        self.assertEqual((r.calls.execs, r.handoff.tasks_written, r.tasks.rows), ([], [], {}))

    def test_no_at_the_question(self):
        review_route()
        r = self.rig(answers=['n'])
        self.assertEqual(r('do', 'review the parser'), 1)
        self.assertEqual(r.lines()[-1], 'Nothing was sent.')
        self.assertIsNone(r.choices.consent(r.folder, 'codex'))
        self.assertEqual((r.calls.execs, r.handoff.tasks_written, r.said), ([], [], []))

    def test_a_never_said_before_passes_to_the_next(self):
        review_route()
        r = self.rig(answers=['y'])
        r.choices.set_consent(r.folder, 'codex', 'never', NOW)
        self.assertEqual(r('do', 'review the parser'), 0)
        self.assertEqual(r.lines()[:3], [
            'This reads as a review, from the word "review". It goes to Claude, the next choice in your routes for '
            'reviews.',
            'It asks Claude for Sonnet.',
            "Codex was passed: this folder's chats never go to it."])
        self.assertEqual(r.calls.execs[0][1][:3], (r.claude.found_.program.path, '--model', 'sonnet'))

    def test_a_never_for_the_provider_named(self):
        r = self.rig()
        r.choices.set_consent(r.folder, 'codex', 'never', NOW)
        self.assertEqual(r('do', '--on', 'codex', 'review the parser'), 1)
        self.assertEqual(r.lines()[-1], words.NEVER.format(to='Codex', to_id='codex'))
        self.assert_nothing_happened(r)

    def test_a_stay_is_honoured(self):
        review_route()
        r = self.rig(answers=['y'])
        r.choices.set_stay(r.folder, 'claude', NOW)
        self.assertEqual(r('do', 'review the parser'), 0)
        self.assertEqual(r.lines()[0], 'This reads as a review, from the word "review". It goes to Claude: this '
                                       'folder stays on Claude.')
        argv = r.calls.execs[0][1]
        self.assertEqual(argv[:3], (r.claude.found_.program.path, '--model', 'sonnet'))
        self.assertIn(self.question(to='Claude', vendor='Anthropic', jobs=False), r.text())

    def test_named_codex_in_a_folder_that_stays_on_claude(self):
        r = self.rig()
        r.choices.set_stay(r.folder, 'claude', NOW)
        self.assertEqual(r('do', '--on', 'codex', 'review the parser'), 1)
        self.assertEqual(r.lines(), ['This reads as a review, from the word "review".',
                                     'This folder stays on Claude. switchboard stay off lifts that.'])
        self.assert_nothing_happened(r)

    def test_claudes_start_that_fails(self):
        def refuse(path, argv, env, cwd):
            raise OSError(errno.EACCES, 'Permission denied')
        r = self.rig(execve=refuse)
        self.allowed(r, 'claude')
        self.assertEqual(r('do', '--on', 'claude', 'add a footer'), 1)
        (task_id, row), = r.tasks.rows.items()
        self.assertEqual((row['job']['provider'], row['job']['state']), ('claude', 'failed'))
        self.assertEqual(r.said[-1][:2], ('Could not start Claude on a new task in site', 'problem'))
        lines = r.lines()
        self.assertEqual(lines[-2], 'Claude could not be started from here (Permission denied). To start it on the '
                                    'task, run this in a terminal:')
        self.assertTrue(lines[-1].startswith('  cd %s && ' % r.folder), lines[-1])
        self.assertIn(self.written(r).path, lines[-1])

    def test_nobody_can_take_it(self):
        r = self.rig()
        r.claude.limits_ = claude_limits(under=0)
        r.codex.limits_ = codex_limits(pct=99.0)
        self.assertEqual(r('do', 'review the parser'), 1)
        self.assertEqual(r.lines()[1:], [
            'Nobody can take this task now. Nothing was started.',
            'Claude: every account is out until %s.' % when(HOUR),
            'Codex: it is out until %s.' % when(WEEK2),
            'The first one is back at %s.' % when(HOUR)])
        self.assert_nothing_happened(r)

    def test_named_not_added(self):
        r = self.rig(codex_added=False)
        self.assertEqual(r('do', '--on', 'codex', 'review the parser'), 1)
        self.assertEqual(r.lines()[-1], 'Codex is not added. switchboard add codex adds it.')
        self.assert_nothing_happened(r)

    def test_named_on_codex(self):
        r = self.rig()
        self.allowed(r)
        self.assertEqual(r('do', '--on', 'codex', 'add a footer'), 0)
        self.assertEqual(r.lines()[0], 'This reads as a build job, from the word "add". It goes to Codex, as you named '
                                       'it.')
        self.assertEqual(r.calls.execs[0][1][1:3], ('-C', r.folder))

    def test_an_unknown_model_is_refused_with_the_known_ones(self):
        review_route()
        r = self.rig()
        self.assertEqual(r('do', '--model', 'gpt-9', 'review the parser'), 1)
        known = ', '.join(m.id for m in catalog.models('codex'))
        self.assertEqual(r.lines(), [words.UNKNOWN_MODEL.format(name='Codex', word='gpt-9', known=known)])
        self.assert_nothing_happened(r)

    def test_the_model_and_effort_given_replace_the_routes(self):
        review_route()
        r = self.rig()
        self.allowed(r)
        self.assertEqual(r('do', '--model', 'GPT-6 Luna', '--effort', 'low', 'review the parser'), 0)
        self.assertEqual(r.calls.execs[0][1][3:7], ('--model', 'gpt-6-luna', '-c', 'model_reasoning_effort="low"'))
        self.assertIn('It asks Codex for GPT-6 Luna at low effort.', r.lines())

    def test_too_old_is_refused(self):
        review_route()
        r = self.rig(codex_found_=codex_found(self.root, version='0.150.0', seen='0.169.1'))
        self.allowed(r)
        self.assertEqual(r('do', 'review the parser'), 1)
        self.assertEqual(r.lines(), [words.TOO_OLD.format(name='Codex', version='0.150.0', seen='0.169.1')])
        self.assert_nothing_happened(r)

    def test_an_exec_that_fails(self):
        def refuse(path, argv, env, cwd):
            raise OSError(errno.ENOENT, 'No such file or directory')
        review_route()
        r = self.rig(execve=refuse)
        self.allowed(r)
        self.assertEqual(r('do', 'review the parser'), 1)
        note = self.written(r)
        (task_id, row), = r.tasks.rows.items()
        self.assertEqual(row['job']['state'], 'failed')
        self.assertEqual(r.said[-1][:2], ('Could not start Codex on a new task in site', 'problem'))
        lines = r.lines()
        self.assertEqual(lines[-2], 'Codex could not be started from here (No such file or directory). To start it on '
                                    'the task, run this in a terminal:')
        self.assertTrue(lines[-1].startswith('  cd %s && ' % r.folder), lines[-1])
        self.assertIn(note.path, lines[-1])
        self.assertNotIn('parser', lines[-1])

    def test_a_key_in_the_shell_is_not_used(self):
        r = self.rig(env={'CODEX_API_KEY': 'made-up-key-0123456789'})
        self.allowed(r)
        self.assertEqual(r('do', '--on', 'codex', 'fix the parser'), 0)
        self.assertIn('Codex runs on your OpenAI sign-in here, not on the API key set in this shell.', r.lines())
        self.assertNotIn('CODEX_API_KEY', r.calls.execs[0][2])

    def test_show_is_route(self):
        review_route()
        r = self.rig()
        self.assertEqual(r('do', '--show', 'review the parser'), 0)
        s = self.rig(tty=False)
        s('route', 'review the parser')
        self.assertEqual(r.lines(), s.lines())
        self.assert_nothing_happened(r)

    def test_a_secret_in_the_task_stays_out_of_everything(self):
        r = self.rig(env={'MADE_UP_TOKEN': 'made-up-shell-value-0123456789'})
        self.allowed(r, 'claude')
        self.assertEqual(r('do', 'fix login with ghp_fake_made_up_0123456789abcdef and made-up-shell-value-0123456789'),
                         0)
        note = self.written(r)
        with open(note.path, encoding='utf-8') as f:
            text = f.read()
        self.assertNotIn('ghp_fake_made_up', text)
        self.assertNotIn('made-up-shell-value-0123456789', text)
        self.assertIn('(%s; 2 places that looked like a secret taken out of your words, best effort).'
                      % words.size(note.bytes), r.lines()[-1])



class TheDocs(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(HERE, '..', rel), encoding='utf-8') as f:
            return f.read()

    def section(self, text, heading):
        start = text.index('\n' + heading + '\n')
        end = text.find('\n## ', start + 1)
        return text[start:end if end > 0 else len(text)]

    def test_the_three_commands_are_in_the_docs_and_the_changelog(self):
        commands = self.section(self.read('docs/commands.md'), '## Other assistants')
        for cmd in ('do TASK', 'route TASK', 'routes'):
            self.assertIn('| `switchboard %s` |' % cmd, commands)
        how = ' '.join(self.section(self.read('docs/how-it-works.md'), '## The front door').split())
        for words_ in ('**The kinds.**', 'It is rules, not a model', '**The table.**', '**The pick.**',
                       '**The task file.**', '**What is never done.**', 'never a word of the task'):
            self.assertIn(words_, how)
        self.assertIn('**The front door**', self.read('CHANGELOG.md'))
        for text in (commands, how):
            self.assertNotIn('reviews on its Sonnet at high', text)
            self.assertNotIn('reviews to its Sonnet at high', text)
        self.assertIn('fills only kinds you have no route for', ' '.join(how.split()))
        self.assertIn('a task with words of two kinds takes the other one, and --as decides', how.replace('`', ''))

    def test_route_says_it_starts_no_app_and_no_more(self):       # it keeps a record of programs and takes a lock
        commands = self.section(self.read('docs/commands.md'), '## Other assistants')
        how = self.section(self.read('docs/how-it-works.md'), '## The front door')
        route_row = next(x for x in commands.splitlines() if x.startswith('| `switchboard route TASK` |'))
        do_row = next(x for x in commands.splitlines() if x.startswith('| `switchboard do TASK` |'))
        for text in (route_row, how, words.HELP['route'], words.HELP['do']):
            flat = ' '.join(text.split())
            self.assertNotIn('writes nothing', flat)
            self.assertNotIn('runs nothing', flat)
        self.assertIn('starts no app', ' '.join(words.HELP['route'].split()))
        self.assertIn('It starts no app.', route_row)
        self.assertIn('`--show` shows the pick and starts no app.', do_row)
        self.assertIn('`switchboard route` shows the same pick and starts no app.', how)
        self.assertIn('Not in a terminal it prints the line to run on standard output, with the task on one line and cut '
                      'at 4,000 characters, and starts nothing.', do_row)


if __name__ == '__main__':
    unittest.main()
