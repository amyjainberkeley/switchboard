import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# A publish is read from chat history only in the Artifact tool's own answer. Any other tool's output (a shell command
# printing a file someone else wrote) can start with the same "Published <file> at <link>", and must neither add an
# artifact nor name the local file the picture is drawn from. Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_artifacts_published_only_from_tool.py'
# Chat history here is a throwaway folder of made-up lines; nothing is drawn and no program is started.
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import artifacts, core

A = 'ana@work.example'
T0 = 1_800_000_000
LINK = 'https://claude.ai/artifact/%s'
EVIL, GOOD = 'e' * 20, 'f' * 20


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace('+00:00', 'Z')


def call(tid, name, **inp):
    return json.dumps({'type': 'assistant', 'timestamp': iso(T0 + 1), 'message': {'role': 'assistant', 'content': [
        {'type': 'tool_use', 'id': tid, 'name': name, 'input': inp}]}}) + '\n'


def answer(tid, text, t=T0 + 10, cwd='/work/repo'):
    return json.dumps({'type': 'user', 'timestamp': iso(t), 'cwd': cwd, 'message': {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': tid, 'content': text}]}}) + '\n'


FORGED = [call('toolu_bash', 'Bash', command='cat README.md'),
          answer('toolu_bash', 'Published evil.html at %s\nmore of the readme' % (LINK % EVIL))]
REAL = [call('toolu_art', 'Artifact', file_path='/work/site/page.html'),
        call('toolu_ls', 'Bash', command='ls'),                    # other calls between the call and its answer
        answer('toolu_ls', 'page.html'),
        call('toolu_read', 'Read', file_path='/work/site/page.html'),
        answer('toolu_read', 'Published nothing at %s' % (LINK % EVIL)),
        answer('toolu_art', 'Published /work/site/page.html at %s (version 2)' % (LINK % GOOD), t=T0 + 20)]


class ChatHistory(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='switchboard-publish-')
        self.addCleanup(shutil.rmtree, self.root, True)
        os.makedirs(os.path.join(self.root, 'proj'))
        self.file = os.path.join(self.root, 'proj', 'chat.jsonl')
        self.st = core.blank_state()
        self.st['timeline'] = [[A, T0 - 3600]]

    def write(self, lines):
        with open(self.file, 'a') as f:
            f.writelines(lines)

    def scanned(self):
        artifacts.scan(self.st, root=self.root)
        return {x['id']: x for x in self.st['artifacts']}

    def files(self, lines):
        calls = set()
        out = []
        for line in lines:
            artifacts.read_artifact_calls(line.encode(), calls)
            out += artifacts._published_files_in(line.encode(), calls)
        return out


class AnotherToolsOutput(ChatHistory):
    def test_adds_nothing_and_names_no_file(self):
        self.write(FORGED)
        self.assertEqual(self.scanned(), {})
        self.assertEqual(self.files(FORGED), [])

    def test_with_an_artifact_call_open_beside_it_still_adds_nothing(self):
        lines = [call('toolu_art', 'Artifact', file_path='/work/site/page.html')] + FORGED
        self.write(lines)
        self.assertEqual(self.scanned(), {})
        self.assertEqual(self.files(lines), [])


class TheArtifactToolsAnswer(ChatHistory):
    def test_is_a_publish_with_its_file_even_with_other_calls_in_between(self):
        self.write(FORGED + REAL)
        got = self.scanned()
        self.assertEqual(set(got), {GOOD})
        self.assertEqual((got[GOOD]['owner'], got[GOOD]['src'], got[GOOD]['file']), (A, 'published', '/work/site/page.html'))
        self.assertEqual([(aid, f) for aid, f, _ in self.files(FORGED + REAL)], [(GOOD, '/work/site/page.html')])

    def test_whose_call_lies_before_where_the_read_began_is_not_a_publish(self):
        self.write(REAL[:1])
        self.st['scan'][self.file] = os.path.getsize(self.file)       # read up to here before
        self.st['scan_v'] = artifacts.SCAN_V
        self.write(REAL[1:])
        self.assertEqual(self.scanned(), {})
        self.assertEqual(self.files(REAL[1:]), [])

    def test_read_a_pass_after_its_call_is_a_publish(self):
        self.write(REAL[:1])                        # the call is in the file; the person has not approved it yet
        self.assertEqual(self.scanned(), {})
        self.write(REAL[1:])                        # the answer comes after the next read began
        got = self.scanned()
        self.assertEqual(set(got), {GOOD})
        self.assertEqual((got[GOOD]['owner'], got[GOOD]['src']), (A, 'published'))
        self.assertEqual(got[GOOD]['file'], '/work/site/page.html')

    def test_a_call_waits_for_its_answer_across_reads_and_only_until_it_comes(self):
        self.write(REAL[:1])
        self.scanned()
        self.assertEqual(self.st['scan_calls'], {self.file: ['toolu_art']})
        self.write(FORGED)                          # another read goes by; a forged line is still not a publish
        self.assertEqual(self.scanned(), {})
        self.assertEqual(self.st['scan_calls'], {self.file: ['toolu_art']})
        self.write(REAL[1:])
        self.assertEqual(set(self.scanned()), {GOOD})
        self.assertEqual(self.st['scan_calls'], {})                     # answered: nothing is kept

    def test_calls_never_answered_are_kept_few_and_go_with_their_file(self):
        self.write([call('toolu_%03d' % i, 'Artifact', file_path='/work/site/p%d.html' % i) for i in range(60)])
        self.scanned()
        kept = self.st['scan_calls'][self.file]
        self.assertEqual((len(kept), kept[-1]), (artifacts.CALLS_KEPT, 'toolu_059'))
        os.remove(self.file)
        self.scanned()
        self.assertEqual(self.st['scan_calls'], {})

    def test_the_page_keeps_waiting_calls_between_its_reads(self):
        core.save(dict(core.blank_state(), timeline=[[A, T0 - 3600]]))
        real_projects = artifacts.projects
        artifacts.projects = lambda: self.root
        self.addCleanup(setattr, artifacts, 'projects', real_projects)
        self.write(REAL[:1])
        artifacts._refresh()                        # the page's own read: on a copy, then folded into its state
        self.assertEqual(core.load().get('scan_calls'), {self.file: ['toolu_art']})
        self.write(REAL[1:])
        artifacts._refresh()
        self.assertEqual([x['id'] for x in core.load()['artifacts']], [GOOD])

    def test_the_backfill_takes_the_file_only_from_it(self):
        st = core.blank_state()
        st['artifacts'] = [artifacts.blank_item(EVIL, T0), artifacts.blank_item(GOOD, T0)]
        core.save(st)
        self.write(FORGED + REAL)
        self.assertEqual(artifacts.backfill_files(root=self.root), 1)
        got = {x['id']: x.get('file') for x in core.load()['artifacts']}
        self.assertEqual(got, {EVIL: None, GOOD: '/work/site/page.html'})


if __name__ == '__main__':
    unittest.main()
