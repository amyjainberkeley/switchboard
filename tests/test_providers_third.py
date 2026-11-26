import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# Any assistant is one adapter: a made-up third one, Tern (tests/fakes_tern.py), written on the test side and put in
# the registry the way the package lists its own, works everywhere the two real ones do, with no shared module edited
# for it.
#
# - `switchboard providers` lists it with its own program name, plan, window and state; `providers.view` carries it.
# - place.pick chooses it when Claude is out, and `continue` (with and without --to) gets as far as its own start.
# - Its own facts are what the person reads: how it signs in, what it cannot do, its rule file and where its memory
#   lives in the note.
# - The terminal group, the menu bar rows and the page's group show it (the page in headless Chrome, under the lock).
import dataclasses
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, '..')))

import fakes_tern  # noqa: E402
import test_app  # noqa: E402  (its headless-Chrome helper; it has no tests of its own here)
from fakes_provider_kit import (NOW, Rig, chat as kit_chat, claude_found, claude_limits, claude_seats,  # noqa: E402
                                read_json)
from fakes_providers import make_reach, stand_in  # noqa: E402
from switchboard import core, demo, menubar, term  # noqa: E402
from switchboard.providers import choices, cli, contract, handoff, place, view, words  # noqa: E402
from switchboard.providers.contract import Room, Transcript, Event, ReadInfo  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix='sb-third-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        shutil.rmtree(os.path.join(core.data_dir(), contract.DATA_DIR), True)     # each test's choices start empty
        reg = fakes_tern.registered()
        self.Tern = reg.__enter__()
        self.addCleanup(reg.__exit__, None, None, None)
        self.Tern.program_path = stand_in(os.path.join(self.root, 'bin'), 'fake-tern', 'import sys\nsys.exit(3)\n')
        self.folder = os.path.join(self.root, 'site')
        os.makedirs(self.folder)
        self.reach, self.calls = make_reach(self.root, wall=lambda: NOW)

    def kit(self):
        """The real Kit (the registry's adapters, the real choices, place and ledger) on a made-up Mac."""
        self.out = io.StringIO()
        return dataclasses.replace(cli.default_kit(self.reach), out=self.out, cwd=lambda: self.folder,
                                   say=lambda *a, **k: None)


class OnTheBoard(Base):
    def test_the_registry_knows_it_and_switchboard_add_adds_it(self):
        self.assertIn('tern', cli._Ctx(self.kit()).ids())
        self.assertEqual(cli.main(['add', 'tern'], self.kit()), 0)
        self.assertIn('Added Tern. Your order: Claude, then Tern.', self.out.getvalue())
        self.assertIn('Switchboard will run Tern Code 1.2.0 (your PATH).', self.out.getvalue())
        self.assertEqual(choices.order(), ['claude', 'tern'])

    def test_switchboard_providers_lists_it(self):
        choices.add('tern', NOW - 3600)
        self.assertEqual(cli.main(['providers'], self.kit()), 0)
        lines = self.out.getvalue().splitlines()
        tern = [ln for ln in lines if ln.startswith('Tern ')]
        self.assertEqual(len(tern), 1, lines)
        self.assertIn('has room · Tern Team · 3-day 30%, refills', tern[0])
        self.assertIn('Tern Code 1.2.0 (your PATH)', self.out.getvalue())
        self.assertIn('Order: Claude, then Tern.', lines)

    def test_the_view_carries_it(self):
        choices.add('tern', NOW - 3600)
        board = view.view(self.reach)
        self.assertEqual(board['order'], ['claude', 'tern'])
        tern = next(p for p in board['providers'] if p['id'] == 'tern')
        self.assertEqual((tern['name'], tern['app'], tern['state'], tern['version'], tern['plan']),
                         ('Tern', 'Tern Code', contract.ROOM, '1.2.0', 'Tern Team'))
        self.assertIs(tern['account_list'], False)
        self.assertEqual([w['label'] for w in tern['seats'][0]['windows']], ['3-day'])
        self.assertEqual(tern['seats'][0]['windows'][0]['used_pct'], 30.0)
        self.assertEqual(self.calls.runs, [])                   # the board started nothing

    def test_on_the_mac_and_not_added_it_is_offered(self):
        board = view.view(self.reach)
        self.assertEqual([p['id'] for p in board['providers']], ['claude'])
        self.assertEqual(board['add'], [{'id': 'tern', 'name': 'Tern', 'app': 'Tern Code', 'version': '1.2.0'}])


class CarriedOnInIt(Base):
    def rig(self, tern_used=30.0, signed_in=True, consent='yes'):
        self.Tern.used, self.Tern.signed_in = tern_used, signed_in
        r = Rig(self.root, added={'tern': NOW - 86400})
        r.place = place                                         # the real one chooses
        r.claude = r.provider('claude', found=claude_found(self.root), seats=claude_seats(),
                              limits=claude_limits(under=0), chats=[kit_chat('claude', r.folder)])
        r.tern = r.add_provider(fakes_tern.TernProvider(r.reach))
        if consent:
            r.choices.set_consent(r.folder, 'tern', consent, NOW - 60)
        return r

    def test_place_pick_chooses_it_when_claude_is_out(self):
        rooms = {'claude': Room('claude', contract.OUT, back_at=NOW + 3600),
                 'tern': Room('tern', contract.ROOM, read_at=NOW - 60)}
        self.assertEqual(place.pick(['claude', 'tern'], 'claude', rooms), ('tern', 'room'))
        self.assertEqual(place.pick(['claude', 'tern'], 'claude', rooms, to='tern'), ('tern', 'room'))

    def test_continue_gets_as_far_as_its_own_start(self):
        r = self.rig()
        self.assertEqual(r('continue'), 0, r.text())
        self.assertEqual(len(self.Tern.started), 1, r.text())
        folder, how, path = self.Tern.started[0]
        self.assertEqual((folder, how), (r.folder, contract.PRINT))     # no terminal here: the line to run
        self.assertIn('Tern: has room (Tern Team, 3-day 30%, read 1 min ago).', r.lines())
        self.assertIn('To carry on in Tern, run this in a terminal:', r.lines())

    def test_continue_to_it_by_name(self):
        r = self.rig()
        self.assertEqual(r('continue', '--to', 'tern'), 0, r.text())
        self.assertEqual(len(self.Tern.started), 1)

    def test_its_own_sign_in_words_when_it_is_signed_out(self):
        r = self.rig(signed_in=False)
        self.assertEqual(r('continue', '--to', 'tern'), 1)
        self.assertEqual(r.lines()[-1], 'Tern is not signed in here. Sign in once in Tern itself: run tern auth in '
                                        'Tern Code.')
        self.assertEqual(self.Tern.started, [])

    def test_its_own_sentence_for_a_job(self):
        r = self.rig()
        self.assertEqual(r('hand', 'tern', '--', 'look at the tests'), 1)
        self.assertEqual(r.lines()[-1], 'Tern takes no jobs from Switchboard.')

    def test_no_question_of_another_providers_before_its_job(self):
        self.assertEqual(self.Tern.job_question, '')


class InTheNote(Base):
    def test_its_rule_file_is_listed_and_its_memory_named(self):
        files, _ = handoff.rule_names()
        self.assertIn('TERN.md', files)
        with open(os.path.join(self.folder, 'TERN.md'), 'w') as f:
            f.write('made-up rules\n')
        found = [os.path.basename(p) for p, _ in handoff._rule_files(self.folder, False, self.reach.home)]
        self.assertIn('TERN.md', found)
        chat = fakes_tern.chat(self.folder)
        t = Transcript(chat, (Event(contract.PERSON, 'made-up request for Tern'),), ReadInfo(reader='tern'))
        stand = handoff.where_things_stand(self.folder, view.quiet(self.reach))
        note = handoff.make(t, hop='h-0000000d', task='t-0000000d', to_name='Claude', from_name='Tern',
                            why='The person asked to carry this chat on in Claude.', stand=stand, reach=self.reach)
        with open(note.path, encoding='utf-8') as f:
            text = f.read()
        self.assertIn("- Tern's own memory and the person's global rules (for Tern: ~/.tern/RULES.md), which the next "
                      "assistant does not read unless told to.", text)


class NamedByTheNextOne(Base):
    def test_a_note_from_it_names_it_as_its_adapter_does(self):
        from switchboard.providers import claude, codex
        from switchboard.providers.contract import Handoff
        meta = os.path.join(self.root, 'h-0000000e.json')
        with open(meta, 'w') as f:
            json.dump({'from': {'provider': 'tern'}}, f)
        note = Handoff(hop='h-0000000e', path=os.path.join(self.root, 'h-0000000e.md'), meta_path=meta, bytes=1,
                       sha256='0')
        with mock.patch.object(self.Tern, 'name', 'Tern Bird'):       # a name that is not its id with a capital
            self.assertEqual(claude.ClaudeProvider(self.reach)._other_name(note), 'Tern Bird')
            self.assertEqual(codex.CodexProvider(self.reach)._from_name(note), 'Tern Bird')


class OnEverySurface(Base):
    def board(self):
        choices.add('tern', NOW - 3600)
        return view.view(self.reach)

    def test_the_terminal_group(self):
        got = term.providers_lines(self.board(), NOW)
        self.assertEqual([x for x in got if x.startswith('  Tern')],
                         ['  Tern    has room · Tern Team · 3-day 30%%, refills %s' % words.when(NOW + 86400, NOW)])

    def test_the_menu_bar_rows(self):
        self.assertIn(('line', 'Tern: has room · Tern Team · 3-day 30%%, refills %s' % words.when(NOW + 86400, NOW)),
                      menubar.provider_items(self.board(), NOW))

    def test_doctor_has_its_line(self):
        from switchboard import __main__ as main
        said, board = [], self.board()
        with mock.patch.object(view, 'view', lambda *a, **k: board):
            main._doctor_providers(lambda good, text, fix='': said.append((good, text)))
        self.assertIn((True, 'Tern 1.2.0: has room · Tern Team · 3-day 30%%, refills %s'
                       % words.when(NOW + 86400, NOW)), said)


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class OnThePage(test_app.ChromePage):
    def test_the_page_group_draws_it_in_its_own_window_words(self):
        root = os.path.realpath(tempfile.mkdtemp(prefix='sb-third-page-'))
        self.addCleanup(shutil.rmtree, root, True)
        shutil.rmtree(os.path.join(core.data_dir(), contract.DATA_DIR), True)
        reach, _ = make_reach(root)
        with fakes_tern.registered() as tern:
            tern.program_path = stand_in(os.path.join(root, 'bin'), 'fake-tern', 'import sys\nsys.exit(3)\n')
            choices.add('tern', time.time() - 3600)
            board = view.view(reach)
        demo._ready()
        demo.FACTS['providers'] = board
        got = self.run_page('''
          const r = document.querySelector('#providers .pv-row[data-provider="tern"]');
          done(r ? {name: r.querySelector('.em').textContent, sub: r.querySelector('.sub').textContent,
                    label: r.querySelector('.pv-lims .ml').textContent, pct: r.querySelector('.pv-lims .pct').textContent,
                    state: r.querySelector('.pv-state').textContent,
                    order: [...document.querySelectorAll('#providers .pv-row')].map((x) => x.dataset.provider)} : null);
        ''')
        self.assertEqual(got, {'name': 'Tern', 'sub': 'Tern Team', 'label': '3-day', 'pct': '30%', 'state': 'Has room',
                               'order': ['claude', 'tern']})


if __name__ == '__main__':
    unittest.main()
