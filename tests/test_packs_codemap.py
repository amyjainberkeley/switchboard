import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

# Tests for switchboard/packs/codemap.py. Made-up home folders only; the fake `graphify` and
# `codegraph` files are never run (a test checks nothing is started at all).

# A fresh checkout has no bytecode folders for switchboard/packs, and tests/_isolate.py refuses any write outside
# the temp folders, so these imports write no bytecode.
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard.packs import codemap as M
from switchboard.packs import common as C
from switchboard.packs import houserules as H

SKILL = b'---\nname: graphify\ndescription: "made-up copy for tests"\n---\n\n# /graphify\n'


class Mac(object):
    """A made-up home with a Claude Code folder, a PATH folder and a project."""

    def __init__(self, graphify_skill=False, graphify_cmd=False, codegraph_cmd=False, codegraph_mcp=False,
                 codegraph_block=False, graphify_map=False, codegraph_map=False, instructions=None):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix='switchboard-codemap-'))
        self.home = os.path.join(self.root, 'home')
        self.cd = os.path.join(self.home, '.claude')
        self.bin = os.path.join(self.root, 'bin')
        self.project = os.path.join(self.root, 'project')
        self.state = os.path.join(self.root, 'data', 'packs', 'state.json')
        for d in (self.cd, self.bin, self.project):
            os.makedirs(d)
        if graphify_skill:
            os.makedirs(os.path.join(self.cd, 'skills', 'graphify'))
            self._w(os.path.join(self.cd, 'skills', 'graphify', 'SKILL.md'), SKILL)
        if graphify_cmd:
            self._exe(os.path.join(self.home, '.local', 'bin', 'graphify'))
        if codegraph_cmd:
            self._exe(os.path.join(self.bin, 'codegraph'))
        if codegraph_mcp:
            self._w(os.path.join(self.cd, '.claude.json'), json.dumps(
                {'numStartups': 3, 'mcpServers': {'codegraph': {'type': 'stdio', 'command': 'codegraph',
                                                                'args': ['serve', '--mcp']}}}).encode())
        text = instructions or b''
        if codegraph_block:
            text += b'<!-- CODEGRAPH_START -->\n## CodeGraph\nmade-up\n<!-- CODEGRAPH_END -->\n'
        if text:
            self._w(os.path.join(self.cd, 'CLAUDE.md'), text)
        if graphify_map:
            os.makedirs(os.path.join(self.project, 'graphify-out'))
            self._w(os.path.join(self.project, 'graphify-out', 'graph.json'), b'{}')
        if codegraph_map:
            os.makedirs(os.path.join(self.project, '.codegraph'))

    def _w(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)

    def _exe(self, path):
        self._w(path, b'#!/bin/sh\nexit 99\n')
        os.chmod(path, 0o755)

    def detect(self):
        return M.detect(self.cd, self.project, self.home, self.bin)

    def plan(self, on):
        return M.plan(on, self.cd, self.state, self.project, self.home, self.bin)

    def read(self):
        return C.read_bytes(os.path.join(self.cd, 'CLAUDE.md'))


class TestDetect(unittest.TestCase):
    def test_nothing_here(self):
        f = Mac().detect()
        self.assertFalse(f['graphify']['present'])
        self.assertFalse(f['codegraph']['present'])

    def test_graphify_skill_command_and_map(self):
        m = Mac(graphify_skill=True, graphify_cmd=True, graphify_map=True)
        g = m.detect()['graphify']
        self.assertTrue(g['present'])
        self.assertTrue(g['skill'].endswith('skills/graphify/SKILL.md'))
        self.assertEqual(g['command'], os.path.join(m.home, '.local', 'bin', 'graphify'))
        self.assertTrue(g['project_map'].endswith('graphify-out/graph.json'))

    def test_a_skill_with_another_name_is_not_graphify(self):
        m = Mac()
        m._w(os.path.join(m.cd, 'skills', 'graphify', 'SKILL.md'), b'---\nname: something-else\n---\n')
        self.assertFalse(m.detect()['graphify']['present'])

    def test_codegraph_command_mcp_block_and_map(self):
        m = Mac(codegraph_cmd=True, codegraph_mcp=True, codegraph_block=True, codegraph_map=True)
        c = m.detect()['codegraph']
        self.assertEqual(c['command'], os.path.join(m.bin, 'codegraph'))
        self.assertTrue(c['mcp'].endswith('.claude.json'))
        self.assertTrue(c['own_block'])
        self.assertTrue(c['project_map'].endswith('.codegraph'))

    def test_detect_runs_nothing(self):
        m = Mac(graphify_cmd=True, codegraph_cmd=True)
        boom = mock.Mock(side_effect=AssertionError('started a process'))
        with mock.patch('subprocess.Popen', boom), mock.patch('os.system', boom), \
                mock.patch('os.posix_spawn', boom):
            m.detect()
            m.plan(True)
        boom.assert_not_called()


class TestBody(unittest.TestCase):
    def test_lines_follow_what_was_found(self):
        self.assertIsNone(M.body(Mac().detect()))
        g = M.body(Mac(graphify_skill=True).detect())
        self.assertIn(M.GRAPHIFY_LINE, g)
        self.assertNotIn(M.CODEGRAPH_LINE, g)
        both = M.body(Mac(graphify_cmd=True, codegraph_cmd=True).detect())
        self.assertIn(M.GRAPHIFY_LINE, both)
        self.assertIn(M.CODEGRAPH_LINE, both)

    def test_codegraph_left_out_when_its_own_block_is_there(self):
        b = M.body(Mac(graphify_cmd=True, codegraph_cmd=True, codegraph_block=True).detect())
        self.assertIn(M.GRAPHIFY_LINE, b)
        self.assertNotIn(M.CODEGRAPH_LINE, b)
        self.assertIsNone(M.body(Mac(codegraph_cmd=True, codegraph_block=True).detect()))

    def test_small(self):
        b = M.body(Mac(graphify_cmd=True, codegraph_cmd=True).detect())
        text = '\n'.join((M.START,) + b + (M.END,)) + '\n'
        self.assertLessEqual(len(b) + 2, 8)
        self.assertLess(C.est_tokens(text), 250)


class TestPlan(unittest.TestCase):
    def test_nothing_found_is_blocked_and_lists_the_documented_commands(self):
        p = Mac().plan(True)
        self.assertIn('No code map tool', p['blocked'])
        self.assertIsNone(p['op'])
        runs = [s['run'] for s in p['add_codegraph']]
        self.assertEqual(runs, ['curl -fsSL https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh | sh',
                                'codegraph install', 'cd <project folder> && codegraph init'])
        self.assertEqual(p['add_codegraph'][0]['or'], 'npm i -g @colbymchenry/codegraph')
        with self.assertRaises(C.Refused):
            M.apply(p)

    def test_codegraph_with_its_own_section_and_nothing_else(self):
        p = Mac(codegraph_cmd=True, codegraph_block=True).plan(True)
        self.assertIsNone(p['blocked'])
        self.assertIsNone(p['op'])
        self.assertIn('nothing to add', p['says'])

    def test_codegraph_present_offers_removal_not_install(self):
        p = Mac(codegraph_cmd=True).plan(True)
        self.assertEqual(p['add_codegraph'], [])
        self.assertEqual([s['run'] for s in p['remove_codegraph']],
                         ['codegraph uninstall', 'cd <project folder> && codegraph uninit'])

    def test_on_off_round_trip_exact(self):
        old = b'# Mine\nUse tabs.\n'
        m = Mac(graphify_skill=True, graphify_cmd=True, instructions=old)
        p = m.plan(True)
        self.assertEqual(p['op'], 'add')
        M.apply(p)
        new = m.read()
        where, span = C.locate(new, M.START, M.END)
        self.assertEqual(where, 'one')
        self.assertEqual(C.without_block(new, span), old)
        self.assertIn(b'graphify query', new)
        M.undo(m.cd, m.state, m.home, m.bin)
        self.assertEqual(m.read(), old)

    def test_new_tool_found_later_updates_only_inside_the_markers(self):
        m = Mac(graphify_cmd=True, instructions=b'# Mine\n')
        M.apply(m.plan(True))
        os.makedirs(os.path.join(m.home, '.local', 'bin'), exist_ok=True)
        m._exe(os.path.join(m.bin, 'codegraph'))
        p = m.plan(True)
        self.assertEqual(p['op'], 'replace')
        M.apply(p)
        new = m.read()
        self.assertIn(b'codegraph_explore', new)
        _, span = C.locate(new, M.START, M.END)
        self.assertEqual(C.without_block(new, span), b'# Mine\n')

    def test_off_after_the_tool_went_away_still_takes_it_out(self):
        m = Mac(graphify_cmd=True)
        M.apply(m.plan(True))
        os.unlink(os.path.join(m.home, '.local', 'bin', 'graphify'))
        p = m.plan(False)
        self.assertEqual(p['op'], 'delete-file')
        M.apply(p)
        self.assertIsNone(m.read())

    def test_edit_inside_refuses(self):
        m = Mac(graphify_cmd=True, instructions=b'# Mine\n')
        M.apply(m.plan(True))
        m._w(os.path.join(m.cd, 'CLAUDE.md'), m.read().replace(b'work as usual', b'do as you like'))
        self.assertIn('edited inside', m.plan(False)['blocked'])

    def test_with_house_rules_in_the_same_file(self):
        old = b'# Mine\n'
        m = Mac(graphify_cmd=True, instructions=old)
        C.apply_block(H.plan(True, m.cd, m.state))
        M.apply(m.plan(True))
        C.apply_block(H.plan(False, m.cd, m.state))
        M.apply(m.plan(False))
        self.assertEqual(m.read(), old)

    def test_note_when_graphify_is_off_the_path(self):
        p = Mac(graphify_cmd=True).plan(True)
        self.assertFalse(p['found']['graphify']['on_path'])
        self.assertTrue(any('not on this PATH' in n for n in p['notes']))

    def test_note_when_the_project_has_no_map(self):
        p = Mac(graphify_cmd=True).plan(True)
        self.assertTrue(any('no map yet' in n for n in p['notes']))


if __name__ == '__main__':
    unittest.main()
