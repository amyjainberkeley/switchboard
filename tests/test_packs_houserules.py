import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
import os
import stat
import sys
import tempfile
import unittest

# Tests for switchboard/packs/houserules.py and the marker-block edit in switchboard/packs/common.py.
# Every test works in its own throwaway Claude Code folder and Switchboard record.

# A fresh checkout has no bytecode folders for switchboard/packs, and tests/_isolate.py refuses any write outside
# the temp folders, so these imports write no bytecode.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard.packs import common as C
from switchboard.packs import houserules as H

OTHER = {'id': 'other-block', 'name': 'Other block', 'start': '<!-- other: start -->', 'end': '<!-- other: end -->',
         'body': ('## Other', 'one line')}


class Box(object):
    def __init__(self, content=None):
        self.root = tempfile.mkdtemp(prefix='switchboard-houserules-')
        self.cd = os.path.realpath(os.path.join(self.root, 'claude'))
        os.makedirs(self.cd)
        self.state = os.path.join(self.root, 'data', 'packs', 'state.json')
        self.file = os.path.join(self.cd, 'CLAUDE.md')
        if content is not None:
            self.write(content)

    def write(self, data):
        with open(self.file, 'wb') as f:
            f.write(data)

    def read(self):
        return C.read_bytes(self.file)

    def plan(self, on, spec=None):
        if spec is None:
            return H.plan(on, self.cd, self.state)
        return C.plan_block(spec, on, self.cd, self.state)

    def on(self, spec=None):
        return C.apply_block(self.plan(True, spec))

    def off(self, spec=None):
        return C.apply_block(self.plan(False, spec))


BLOCK = H.text().encode('utf-8')


class TestSize(unittest.TestCase):
    def test_at_most_40_lines_and_under_1500_tokens(self):
        s = H.size()
        self.assertLessEqual(s['lines'], 40)
        self.assertEqual(s['lines'], BLOCK.count(b'\n'))
        self.assertLess(s['tokens_estimate'], 1500)
        self.assertEqual(s['tokens_estimate'], -(-len(H.text()) // 3))
        self.assertEqual(s['how'], 'characters / 3, rounded up')

    def test_holds_the_five_ideas(self):
        t = H.text().lower()
        for words in (('assum', 'ask'), ('as small as the job allows',), ('change only what the task needs',),
                      ('run that check',), ('did not check',)):
            for w in words:
                self.assertIn(w, t)

    def test_markers_are_block_level_html_comments(self):
        for m in (H.START, H.END):
            self.assertTrue(m.startswith('<!--') and m.endswith('-->'))
            self.assertNotIn('\n', m)


class TestAddAndRemove(unittest.TestCase):
    def test_no_file_on_then_off_leaves_no_file(self):
        b = Box()
        p = b.plan(True)
        self.assertEqual(p['op'], 'add')
        self.assertIn('a new file', p['says'])
        C.apply_block(p)
        self.assertEqual(b.read(), BLOCK)
        p = b.plan(False)
        self.assertEqual(p['op'], 'delete-file')
        C.apply_block(p)
        self.assertIsNone(b.read())
        st, _ = C.load_state(b.state)
        self.assertEqual(st['blocks'], {})
        self.assertEqual(st['files'], {})

    def test_existing_file_kinds_round_trip_exactly(self):
        cases = {
            'plain': b'# Mine\n\nKeep tests short.\n',
            'no final newline': b'# Mine\nlast line',
            'crlf': b'# Mine\r\nKeep tests short.\r\n',
            'bom': C.BOM + b'# Mine\n',
            'empty': b'',
            'not utf-8': b'caf\xe9 \xff\xfe\n',
            'looks like markdown comment': b'<!-- my own note -->\n# Mine\n',
        }
        for name, old in cases.items():
            with self.subTest(name):
                b = Box(old)
                b.on()
                new = b.read()
                where, span = C.locate(new, H.START, H.END)
                self.assertEqual(where, 'one')
                self.assertEqual(C.without_block(new, span), old)       # not a byte of theirs changed
                nl = b'\r\n' if name == 'crlf' else b'\n'
                self.assertEqual(new[span[0]:span[1]], BLOCK.replace(b'\n', nl))
                self.assertEqual(span[0], 3 if name == 'bom' else 0)
                b.off()
                self.assertEqual(b.read(), old)

    def test_file_keeps_its_mode(self):
        b = Box(b'x\n')
        os.chmod(b.file, 0o640)
        b.on()
        self.assertEqual(stat.S_IMODE(os.stat(b.file).st_mode), 0o640)
        b.off()
        self.assertEqual(stat.S_IMODE(os.stat(b.file).st_mode), 0o640)

    def test_on_twice_changes_nothing(self):
        b = Box(b'# Mine\n')
        b.on()
        once = b.read()
        p = b.plan(True)
        self.assertIsNone(p['op'])
        self.assertIn('already on', p['says'])
        self.assertEqual(C.apply_block(p)['changed'], [])
        self.assertEqual(b.read(), once)


class TestPersonEdits(unittest.TestCase):
    def test_edits_around_the_block_stay_and_only_the_block_goes(self):
        old = b'# Mine\nKeep tests short.\n'
        b = Box(old)
        b.on()
        new = b.read()
        edited = b'Written above it.\n' + new.replace(b'Keep tests short.', b'Keep tests very short.') + b'tail\n'
        b.write(edited)
        b.off()
        self.assertEqual(b.read(), b'Written above it.\n# Mine\nKeep tests very short.\ntail\n')

    def test_file_made_for_the_block_stays_when_the_person_wrote_in_it(self):
        b = Box()
        b.on()
        b.write(b.read() + b'my own line\n')
        p = b.plan(False)
        self.assertEqual(p['op'], 'remove')
        C.apply_block(p)
        self.assertEqual(b.read(), b'my own line\n')

    def test_edit_inside_the_block_refuses_and_says_so(self):
        b = Box(b'# Mine\n')
        b.on()
        inside = b.read().replace(b'Claim no more than the checks showed.', b'Claim what you like.')
        b.write(inside)
        p = b.plan(False)
        self.assertIn('edited inside', p['blocked'])
        self.assertIn('lines 1-', p['blocked'])
        with self.assertRaises(C.Refused):
            C.apply_block(p)
        with self.assertRaises(C.Refused):
            H.undo(b.cd, b.state)
        self.assertEqual(b.read(), inside)
        # turning it on again leaves the person's version alone
        p = b.plan(True)
        self.assertIsNone(p['op'])
        self.assertIn('Your version stays', p['says'])

    def test_marker_line_changed_counts_as_inside(self):
        b = Box(b'# Mine\n')
        b.on()
        b.write(b.read().replace(H.END.encode(), H.END.encode() + b' extra'))
        p = b.plan(False)
        self.assertIsNotNone(p['blocked'])
        self.assertIn('not as one block', p['blocked'])

    def test_person_took_the_block_out_themselves(self):
        old = b'# Mine\n'
        b = Box(old)
        b.on()
        b.write(old)
        p = b.plan(False)
        self.assertEqual(p['op'], 'forget')
        self.assertEqual(C.apply_block(p)['changed'], [])
        self.assertEqual(b.read(), old)
        self.assertEqual(C.load_state(b.state)[0]['blocks'], {})

    def test_a_copy_of_the_block_is_refused(self):
        b = Box(b'# Mine\n')
        b.on()
        b.write(b.read() + BLOCK)
        p = b.plan(False)
        self.assertIn('2 start and 2 end', p['blocked'])

    def test_someone_elses_text_between_the_markers_is_left_alone(self):
        theirs = (H.START + '\nnot ours\n' + H.END + '\n# Mine\n').encode()
        b = Box(theirs)
        self.assertIn('no record', b.plan(True)['blocked'])
        self.assertIn('no record', b.plan(False)['blocked'])

    def test_exact_text_without_a_record_is_adopted_not_rewritten(self):
        b = Box(BLOCK + b'# Mine\n')
        p = b.plan(True)
        self.assertEqual(p['op'], 'adopt')
        self.assertEqual(C.apply_block(p)['changed'], [])
        b.off()
        self.assertEqual(b.read(), b'# Mine\n')


class TestSafety(unittest.TestCase):
    def test_file_changed_after_the_plan(self):
        b = Box(b'# Mine\n')
        p = b.plan(True)
        b.write(b'# Mine, changed\n')
        with self.assertRaises(C.Refused):
            C.apply_block(p)
        self.assertEqual(b.read(), b'# Mine, changed\n')

    def test_record_changed_after_the_plan(self):
        b = Box(b'# Mine\n')
        p = b.plan(True)
        b.on(OTHER)
        with self.assertRaises(C.Refused):
            C.apply_block(p)

    def test_linked_file_is_not_edited(self):
        b = Box()
        target = os.path.join(b.root, 'elsewhere.md')
        with open(target, 'wb') as f:
            f.write(b'# Linked\n')
        os.symlink(target, b.file)
        self.assertIn('is a link', b.plan(True)['blocked'])
        with open(target, 'rb') as f:
            self.assertEqual(f.read(), b'# Linked\n')

    def test_read_only_file_is_left_alone(self):
        b = Box(b'# Mine\n')
        os.chmod(b.file, 0o444)
        try:
            self.assertIn('read-only', b.plan(True)['blocked'])
        finally:
            os.chmod(b.file, 0o644)

    def test_no_claude_folder(self):
        root = tempfile.mkdtemp(prefix='switchboard-houserules-')
        p = H.plan(True, os.path.join(root, 'missing'), os.path.join(root, 'state.json'))
        self.assertIn('no Claude Code folder', p['blocked'])

    def test_unreadable_record_blocks(self):
        b = Box(b'# Mine\n')
        os.makedirs(os.path.dirname(b.state))
        with open(b.state, 'w') as f:
            f.write('{not json')
        self.assertIn("can't be read", b.plan(True)['blocked'])

    def test_record_is_owner_only(self):
        b = Box(b'# Mine\n')
        b.on()
        self.assertEqual(stat.S_IMODE(os.stat(b.state).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(b.state)).st_mode), 0o700)


class TestTwoBlocks(unittest.TestCase):
    def test_two_blocks_come_out_in_either_order(self):
        for first in ('house', 'other'):
            for old in (None, b'# Mine\n'):
                with self.subTest(first=first, old=old):
                    b = Box(old)
                    b.on()
                    b.on(OTHER)
                    if first == 'house':
                        b.off()
                        b.off(OTHER)
                    else:
                        b.off(OTHER)
                        b.off()
                    self.assertEqual(b.read(), old)
                    st, _ = C.load_state(b.state)
                    self.assertEqual((st['blocks'], st['files']), ({}, {}))


class TestTwoFolders(unittest.TestCase):
    def test_each_claude_folder_has_its_own_record(self):
        a, b = Box(b'# A\n'), Box(b'# B\n')
        state = a.state
        C.apply_block(H.plan(True, a.cd, state))
        C.apply_block(H.plan(True, b.cd, state))
        self.assertEqual(len(C.load_state(state)[0]['blocks']), 2)
        C.apply_block(H.plan(False, a.cd, state))
        C.apply_block(H.plan(False, b.cd, state))
        self.assertEqual((a.read(), b.read()), (b'# A\n', b'# B\n'))


class TestStatusAndCli(unittest.TestCase):
    def test_status(self):
        b = Box(b'# Mine\n')
        self.assertEqual(H.status(b.cd, b.state)['state'], 'off')
        b.on()
        s = H.status(b.cd, b.state)
        self.assertEqual(s['state'], 'on')
        self.assertLessEqual(s['lines'], 40)
        b.write(b.read().replace(b'plain lines', b'plain words'))
        self.assertEqual(H.status(b.cd, b.state)['state'], 'edited')

    def test_cli_show(self):
        import io
        out = io.StringIO()
        self.assertEqual(H.main(['show'], out), 0)
        self.assertEqual(out.getvalue(), H.text())


if __name__ == '__main__':
    unittest.main()
