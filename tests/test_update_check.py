import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# `switchboard update --check [--json]`: one look at the place an update would install from, and nothing else. It
# changes nothing. Exit 0 when this is the newest, 10 when a newer one exists, 1 when it could not look, 2 for words it
# does not take. The web is a stand-in that records each request; Homebrew is a stand-in script on PATH.
import io
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import core, newer
from switchboard.accounts import net

WEB = 'https://example.invalid/sb/main/install.sh'
ASKED = 'https://example.invalid/sb/main/switchboard/core.py'
KEYS = ['schema', 'checked_at', 'installed_from', 'this_version', 'newest_version', 'newer', 'problem']


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def write_exe(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(text)
    os.chmod(path, 0o755)


class Web(object):
    """The network, standing in: each request is written down, and the answer is the one given."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def request(self, method, url, headers=None, body=None, timeout_s=10.0):
        self.calls.append({'method': method, 'url': url, 'headers': dict(headers or {}), 'body': body})
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='switchboard-check-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = os.path.join(self.tmp, 'data')
        self.home = os.path.join(self.tmp, 'home')
        os.makedirs(self.home)
        self.bin = os.path.join(self.tmp, 'bin')
        os.makedirs(self.bin)
        env = mock.patch.dict(os.environ, {'HOME': self.home, 'SWITCHBOARD_HOME': self.data,
                                           'SWITCHBOARD_PORT': str(free_port()),
                                           'PATH': self.bin + os.pathsep + '/usr/bin:/bin'})
        env.start()
        self.addCleanup(env.stop)
        self.copy(os.path.join(self.data, 'app'), '0.2.0')
        self.web = Web(net.NetError('dns', False, 'no such host'))
        p = mock.patch.object(newer, 'HTTP', self.web)
        p.start()
        self.addCleanup(p.stop)

    def copy(self, folder, version):
        os.makedirs(os.path.join(folder, 'switchboard'), exist_ok=True)
        with open(os.path.join(folder, 'switchboard', 'core.py'), 'w') as f:
            f.write("import os\n\nVERSION = '%s'\n" % version)

    def source(self, text):
        with open(os.path.join(self.data, 'app', 'source'), 'w') as f:
            f.write(text + '\n')

    def files(self):
        out = {}
        for folder, _, names in os.walk(self.data):
            for n in names:
                p = os.path.join(folder, n)
                st = os.stat(p)
                out[p] = (st.st_size, st.st_mtime_ns)
        return out

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        before = self.files()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(['update'] + list(args))
        self.assertEqual(self.files(), before)                  # it changes nothing
        return code, out.getvalue(), err.getvalue()

    def check(self, *more):
        code, out, err = self.run_cli('--check', *more)
        return code, out

    def doc(self):
        code, out, err = self.run_cli('--check', '--json')
        d = json.loads(out)                                     # standard output holds the document and nothing else
        self.assertEqual(list(d), KEYS)
        self.assertEqual(d['schema'], 1)
        self.assertRegex(d['checked_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        return code, d


class FromAFolder(Case):
    def setUp(self):
        super().setUp()
        self.clone = os.path.join(self.tmp, 'clone')
        self.source(self.clone)

    def test_a_newer_version_exits_10(self):
        self.copy(self.clone, '0.10.0')
        self.assertEqual(self.check(), (10, 'Switchboard 0.10.0 is out (this is 0.2.0). Install it with: '
                                            'switchboard update\n'))
        self.assertEqual(self.web.calls, [])                    # a folder is read, the web is never asked

    def test_the_newest_exits_0(self):
        self.copy(self.clone, '0.2.0')
        self.assertEqual(self.check(), (0, 'Switchboard 0.2.0 is the newest.\n'))

    def test_a_copy_ahead_of_its_source_is_not_told_to_go_back(self):
        self.copy(self.clone, '0.1.9')
        self.assertEqual(self.check(), (0, 'This is Switchboard 0.2.0, ahead of the 0.1.9 it can update from.\n'))

    def test_a_folder_that_is_gone_exits_1(self):
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(out, 'Could not look for a newer version: %s has no copy of Switchboard to read.\n'
                         % self.clone)

    def test_the_json_document(self):
        self.copy(self.clone, '0.3.0')
        code, d = self.doc()
        self.assertEqual(code, 10)
        self.assertEqual({k: d[k] for k in KEYS[2:]},
                         {'installed_from': 'folder', 'this_version': '0.2.0', 'newest_version': '0.3.0',
                          'newer': True, 'problem': None})


class FromTheWeb(Case):
    def setUp(self):
        super().setUp()
        self.source(WEB)

    def test_it_asks_for_the_version_file_beside_the_installer_with_the_agent_name_alone(self):
        self.web.answer = (200, {}, b"import os\nVERSION = '0.3.0'\n")
        self.assertEqual(self.check()[0], 10)
        self.assertEqual(self.web.calls, [{'method': 'GET', 'url': ASKED, 'body': None,
                                           'headers': {'User-Agent': 'Switchboard/%s' % core.VERSION}}])

    def test_the_newest_exits_0(self):
        self.web.answer = (200, {}, b"VERSION = '0.2.0'\n")
        self.assertEqual(self.check(), (0, 'Switchboard 0.2.0 is the newest.\n'))

    def test_no_answer_exits_1_with_a_sentence(self):
        for answer, why in ((net.NetError('timeout', False, 'x'), 'example.invalid did not answer in time'),
                            (net.NetError('dns', False, 'x'), 'could not find example.invalid'),
                            (net.NetError('connect', False, 'x'), 'could not connect to example.invalid'),
                            (net.NetError('network', True, 'x'), 'the connection to example.invalid failed'),
                            ((404, {}, b'Not Found'), 'example.invalid answered 404'),
                            ((200, {}, b'<html>'), 'example.invalid sent no version Switchboard could read')):
            self.web.answer = answer
            self.assertEqual(self.check(), (1, 'Could not look for a newer version: %s.\n' % why), why)
            code, d = self.doc()
            self.assertEqual((code, d['newer'], d['newest_version'], d['problem']),
                             (1, None, None, 'Could not look for a newer version: %s.' % why))

    def test_an_address_it_does_not_know_how_to_read_is_never_asked(self):
        self.source('https://example.invalid/sb/main/setup')
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertEqual(self.web.calls, [])


class WithHomebrew(Case):
    def setUp(self):
        super().setUp()
        self.source('homebrew:' + os.path.join(self.tmp, 'cellar'))
        self.calls = os.path.join(self.tmp, 'brew.calls')

    def brew(self, version, code=0):
        answer = json.dumps({'formulae': [], 'casks': [{'token': 'switchboard', 'version': version,
                                                        'installed': '0.2.0'}]})
        write_exe(os.path.join(self.bin, 'brew'),
                  '#!/bin/sh\nprintf "%%s|%%s|%%s\\n" "$*" "$HOMEBREW_NO_AUTO_UPDATE" "$HOMEBREW_NO_ANALYTICS" '
                  '>> "%s"\ncat <<"EOF"\n%s\nEOF\nexit %d\n' % (self.calls, answer, code))

    def asked(self):
        with open(self.calls) as f:
            return f.read().splitlines()

    def test_homebrew_is_asked_and_nothing_else(self):
        self.brew('0.3.0')
        self.assertEqual(self.check(), (10, 'Switchboard 0.3.0 is out (this is 0.2.0). Install it with: '
                                            'switchboard update\n'))
        self.assertEqual(self.asked(), ['info --cask --json=v2 switchboard|1|1'])
        self.assertEqual(self.web.calls, [])

    def test_the_newest_exits_0_and_a_cask_version_with_a_build_is_read(self):
        self.brew('0.2.0,abc1234')
        self.assertEqual(self.check(), (0, 'Switchboard 0.2.0 is the newest.\n'))

    def test_homebrew_failing_exits_1(self):
        self.brew('0.3.0', code=1)
        self.assertEqual(self.check(), (1, 'Could not look for a newer version: Homebrew did not answer.\n'))

    def test_no_brew_exits_1(self):
        with mock.patch.object(cli, 'brew_path', lambda: None):
            self.assertEqual(self.check(), (1, 'Could not look for a newer version: brew is not on this PATH.\n'))

    def test_the_json_document(self):
        self.brew('0.3.0')
        code, d = self.doc()
        self.assertEqual((code, d['installed_from'], d['newest_version'], d['newer']), (10, 'homebrew', '0.3.0', True))


class Words(Case):
    def test_without_a_source_exits_1(self):
        code, out = self.check()
        self.assertEqual((code, out), (1, 'Could not look for a newer version: Switchboard does not know where it '
                                          'was installed from.\n'))
        code, d = self.doc()
        self.assertEqual((code, d['installed_from'], d['this_version']), (1, None, '0.2.0'))

    def test_words_it_does_not_take(self):
        usage = 'switchboard update --check [--json]\n'
        for words in (['--check', '--port', '7801'], ['--check', '--check'], ['--check', '--menu-app'],
                      ['--check', 'now']):
            self.assertEqual(self.run_cli(*words)[:2], (2, usage), words)
        self.assertEqual(self.run_cli('--json')[:2],
                         (2, 'switchboard update [--link-app] [--menu-app | --no-menu-app] [--port N]\n'
                          'switchboard update --check [--json]\n'))

    def test_with_json_a_word_it_does_not_take_is_still_a_document(self):
        code, out, err = self.run_cli('--check', '--json', '--now')
        d = json.loads(out)
        self.assertEqual((code, d['problem'], d['newer']), (2, 'Unknown word: --now. switchboard update --check '
                                                               '[--json]', None))

    def test_the_help_names_it_right_after_the_port_line(self):
        lines = cli.command_help('update').splitlines()
        self.assertTrue(lines[-2].startswith('switchboard update --port N'), lines)
        self.assertTrue(re.match(r'switchboard update --check\s{2,}\S', lines[-1]), lines)


class Numbers(unittest.TestCase):
    def test_versions_compare_as_numbers(self):
        self.assertEqual(newer.compare('0.10.0', '0.9.9'), 1)
        self.assertEqual(newer.compare('0.2', '0.2.0'), 0)
        self.assertEqual(newer.compare('1.0.0', '1.0.1'), -1)
        for bad in ('', 'v1', '1.x', '1..2', None, '1.2.3.4.5'):
            self.assertIsNone(newer.compare(bad, '1.0'), bad)


if __name__ == '__main__':
    unittest.main()
