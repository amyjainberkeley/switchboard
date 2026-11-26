import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# What looks like a secret, and files that usually hold one (switchboard/providers/redact.py, DESIGN section 11).
#
# Every value here is made up (tests/fixtures/providers/secrets_corpus.json): each secret line with the kind it must be
# counted as, and lines that must come back unchanged. Two slow inputs must finish inside the 2 s budget on Python 3.9.
import fnmatch
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard.providers import redact

MARK = '[secret removed]'
# The two words that end the first line of a key file, joined when the tests run: a scan for leaked keys reads files by
# shape and cannot know a sample from a real key, so no file here holds that line whole.
# For the same reason the samples file has the mark {+} inside every sample shaped like a real key or token: it
# stands for nothing and is taken out here.
PK = 'PRIVATE' + ' KEY'
with open(os.path.join(HERE, 'fixtures', 'providers', 'secrets_corpus.json')) as _f:
    CORPUS = json.loads(_f.read().replace('{PK}', PK).replace('{+}', ''))


class Corpus(unittest.TestCase):
    def test_every_secret_line_is_removed_with_its_kind(self):
        for case in CORPUS['secrets']:
            with self.subTest(line=case['line'][:60]):
                text, counts = redact.scrub(case['line'])
                self.assertIn(MARK, text)
                self.assertEqual(set(counts), {case['kind']}, counts)
                for gone in case['gone']:
                    self.assertNotIn(gone, text)
                for keep in case.get('keep', ()):
                    self.assertIn(keep, text)

    def test_every_line_that_must_stay_stays(self):
        for line in CORPUS['stay']:
            with self.subTest(line=line):
                self.assertEqual(redact.scrub(line), (line, {}))

    def test_all_of_it_at_once_counts_each(self):
        text = '\n'.join([c['line'] for c in CORPUS['secrets']] + CORPUS['stay'])
        out, counts = redact.scrub(text)
        want = {}
        for c in CORPUS['secrets']:
            want[c['kind']] = want.get(c['kind'], 0) + 1
        self.assertEqual(counts, want)
        for line in CORPUS['stay']:
            self.assertIn(line, out)

    def test_the_marker_itself_is_never_taken_for_a_secret(self):
        text, counts = redact.scrub('API_KEY=%s and token %s and password: %s' % (MARK, MARK, MARK))
        self.assertEqual(counts, {})
        self.assertEqual(text, 'API_KEY=%s and token %s and password: %s' % (MARK, MARK, MARK))

    def test_a_name_on_one_line_never_takes_the_next_line(self):
        text, counts = redact.scrub('the api key:\nis kept in the vault')
        self.assertEqual((text, counts), ('the api key:\nis kept in the vault', {}))

    def test_hex_beside_a_git_word_in_the_tool_call_stays(self):
        line = 'abcdef0123456789abcdef0123456789abcdef01'
        self.assertEqual(redact.scrub(line)[0], MARK)
        self.assertEqual(redact.scrub(line, context='git rev-parse HEAD~3')[0], line)


class KnownValues(unittest.TestCase):
    ENV = {'MY_SERVICE_TOKEN': 'plainwordsbutsecret', 'DATABASE_URL': 'postgres://db.example/made-up-db',
           'SHORT_KEY': 'abc', 'EDITOR': 'vim-made-up-editor', 'GH_PAT': 'pat-made-up-1234',
           'AUTHOR_NAME': 'Maya Reyes Example'}

    def test_env_secrets_takes_secret_names_of_eight_or_more(self):
        got = redact.env_secrets(self.ENV)
        self.assertEqual(set(got), {'plainwordsbutsecret', 'postgres://db.example/made-up-db', 'pat-made-up-1234'})
        self.assertEqual(redact.env_secrets(None), ())

    def test_a_value_from_the_environment_goes_wherever_it_appears(self):
        known = redact.env_secrets(self.ENV)
        text = 'it said plainwordsbutsecret twice: xplainwordsbutsecretx and pat-made-up-1234.'
        out, counts = redact.scrub(text, known)
        self.assertNotIn('plainwordsbutsecret', out)
        self.assertNotIn('pat-made-up-1234', out)
        self.assertEqual(counts, {'known_value': 3})
        self.assertIn('vim-made-up-editor', redact.scrub('vim-made-up-editor', known)[0])


class SecretFiles(unittest.TestCase):
    def test_each_name_in_the_list(self):
        for path in ('.env', '/Users/maya/site/.env', '.env.local', 'config/.env.production', 'server.pem',
                     'tls.key', 'cert.p12', 'cert.pfx', 'release.keystore', '~/.ssh/id_rsa', 'id_rsa.pub',
                     'id_ed25519', 'id_ed25519.pub', '.netrc', '/Users/maya/.npmrc', '.pypirc', 'credentials',
                     'credentials.json', 'auth.json', '.credentials.json', 'secrets.yaml', 'secrets.json',
                     '/Users/maya/.ssh/config', '~/.aws/config', '/Users/maya/.gnupg/pubring.kbx',
                     '/Users/maya/.codex/config.toml', '/Users/maya/.switchboard/state.json', 'SERVER.PEM',
                     "'/Users/maya/my site/.env'"):
            with self.subTest(path=path):
                self.assertTrue(redact.secret_file(path))

    def test_ordinary_files_are_not(self):
        for path in ('README.md', '/Users/maya/site/src/env.ts', 'environment.py', 'keys.md', 'monkey.py',
                     'my.env.d/readme', 'author.json', 'credential-helper.md', '/Users/maya/site/package.json',
                     '', 'secrets', 'a.keystore.md'):
            with self.subTest(path=path):
                self.assertFalse(redact.secret_file(path))

    def test_the_one_pattern_says_what_each_name_in_the_list_says(self):
        # secret_file() asks one pattern made of every name, once a word (sixteen calls a word took a second of one
        # long chat's read). It must answer as fnmatch does name by name, for every kind of base name.
        bases = ['.env', '.env.local', '.envrc', 'x.env', 'server.pem', 'pem', '.pem', 'a.pem.txt', 'tls.key',
                 'key', 'cert.p12', 'cert.pfx', 'release.keystore', 'a.keystore.md', 'id_rsa', 'id_rsa.pub',
                 'my_id_rsa', 'id_ed25519', 'id_ed25519.pub', '.netrc', 'netrc', '.npmrc', '.pypirc',
                 'credentials', 'credentials.json', 'credential-helper.md', 'auth.json', 'author.json',
                 '.credentials.json', 'secrets.yaml', 'secrets', 'secrets.', 'README.md', 'line one\n.env',
                 '.env\n', 'SERVER.PEM', 'x' * 5000, 'x' * 5000 + '.pem', '']
        for base in bases:
            low = base.lower()
            with self.subTest(base=base[:40]):
                self.assertEqual(bool(redact._SECRET_NAME_RE.match(low)),
                                 any(fnmatch.fnmatchcase(low, p) for p in redact._SECRET_NAMES))
        self.assertGreaterEqual(len(redact._SECRET_NAMES), 16)

    def test_command_files_names_the_words_of_a_command(self):
        self.assertEqual(redact.command_files('cat .env'), ('.env',))
        self.assertEqual(redact.command_files('cat ~/.aws/credentials | head'), ('~/.aws/credentials',))
        self.assertEqual(redact.command_files('grep -r TOKEN src/ < .env.local'), ('.env.local',))
        self.assertEqual(redact.command_files('cp .netrc /tmp/x && echo ok'), ('.netrc',))
        self.assertEqual(redact.command_files('python3 app.py --config=secrets.yaml'), ('secrets.yaml',))
        self.assertEqual(redact.command_files('bash -lc "cat id_rsa"'), ('id_rsa',))
        self.assertEqual(redact.command_files('ls -la src'), ())
        self.assertEqual(redact.command_files('echo "unterminated'), ())
        self.assertEqual(redact.command_files(''), ())

    def test_a_listing_of_the_environment_is_known_by_its_one_word(self):
        for cmd, word in (('env', 'env'), ('printenv', 'printenv'), ('  set ', 'set'), ('/usr/bin/env', 'env')):
            self.assertEqual(redact.environment_listing(cmd), word)
        for cmd in ('env FOO=1 make', 'printenv HOME', 'set -e', 'envsubst', 'echo env', ''):
            self.assertIsNone(redact.environment_listing(cmd))

    def test_the_words_that_replace_them(self):
        self.assertEqual(redact.file_left_out('/Users/maya/site/.env'),
                         '[the contents of .env were left out: such files usually hold secrets]')
        self.assertEqual(redact.listing_left_out('printenv'),
                         '[the output of printenv was left out: it lists the environment]')


class Budget(unittest.TestCase):
    def test_four_mebibytes_of_begin_markers_finish_in_time(self):
        text = ('-----BEGIN RSA %s-----\n' % PK) * ((4 << 20) // 32)
        t0 = time.monotonic()
        out, counts = redact.scrub(text, deadline=t0 + 2.0)
        took = time.monotonic() - t0
        self.assertLess(took, 2.5)
        self.assertNotIn('BEGIN RSA ' + PK, out)

    def test_four_mebibytes_of_one_key_prefix_finish_in_time(self):
        for prefix in ('sk-ant-', 'ghp_', 'AKIA', 'eyJ', 'xoxb-', 'a=', 'token ', 'Bearer '):
            with self.subTest(prefix=prefix):
                text = prefix * ((4 << 20) // len(prefix))
                t0 = time.monotonic()
                out, counts = redact.scrub(text, deadline=t0 + 2.0)
                self.assertLess(time.monotonic() - t0, 2.5)

    def test_past_the_budget_the_rest_is_left_out_and_counted(self):
        ticks = iter([0.0, 0.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0])
        text = ('first line is fine\n' * 5000) + 'sk-ant-api03-EXAMPLEexample0123456789abcdef\n' + ('more\n' * 5000)
        out, counts = redact.scrub(text, deadline=1.0, clock=lambda: next(ticks, 9.0))
        self.assertIn('[left out: too long to check for secrets]', out)
        self.assertEqual(counts.get('too_long'), 1)
        self.assertNotIn('EXAMPLEexample0123456789abcdef', out)
        self.assertTrue(out.startswith('first line is fine\n'))

    def test_a_deadline_already_gone_leaves_the_whole_piece_out(self):
        out, counts = redact.scrub('sk-ant-api03-EXAMPLEexample0123456789abcdef', deadline=1.0, clock=lambda: 2.0)
        self.assertEqual(out, '[left out: too long to check for secrets]')
        self.assertEqual(counts, {'too_long': 1})



class ShellVariablesAndPgpKeys(unittest.TestCase):
    def test_a_value_is_kept_as_a_shell_variable_only_when_it_is_one_whole(self):
        for line in ('PASSWORD=$HOME', 'API_TOKEN=${MADE_UP_TOKEN}', 'SECRET_KEY=$SECRET_KEY',
                     'export DB_PASSWORD="${db_password}"'):
            with self.subTest(line=line):
                self.assertEqual(redact.scrub(line), (line, {}))
        for line, gone in (('PASSWORD=$ecret99xx', '$ecret99xx'), ('API_TOKEN=$madeUp-Value-42', 'madeUp'),
                           ('SESSION_KEY=${A}made-up-tail', 'made-up-tail'), ('TOKEN=$(cat made-up)', 'cat')):
            with self.subTest(line=line):
                text, counts = redact.scrub(line)
                self.assertNotIn(gone, text)
                self.assertEqual(counts, {'named_value': 1})

    def test_a_pgp_private_key_block_goes_with_its_header(self):
        block = ('-----BEGIN PGP %s BLOCK-----\n\nlQOYBGMadeUpMadeUp\n=ab12\n'
                 '-----END PGP %s BLOCK-----') % (PK, PK)
        text, counts = redact.scrub('before\n' + block + '\nafter')
        self.assertEqual(text, 'before\n' + MARK + '\nafter')
        self.assertEqual(counts, {'private_key': 1})
        text, counts = redact.scrub('-----BEGIN PGP %s BLOCK-----\nlQOYBGMadeUpMadeUp\n=ab12' % PK)
        self.assertNotIn('BEGIN PGP', text)
        self.assertNotIn('lQOYBGMadeUp', text)
        public = '-----BEGIN PGP PUBLIC KEY BLOCK-----'
        self.assertEqual(redact.scrub(public), (public, {}))


if __name__ == '__main__':
    unittest.main()
