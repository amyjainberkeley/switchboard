import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

# Chat search keeps key-like text out of its index, an index made by an older build is erased and made again, and
# erase() takes the index away. Every chat here is made up, in a throwaway folder; each secret-shaped string is
# built from parts, so none sits whole in this file.

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard.packs import memory as M

FOLDER = '/Users/x/code/orchard'
# key-like, made up: an Anthropic-shaped key, a bearer value, a password= value, a long key-like run
KEY = 'sk-' + 'ant-' + 'api03-' + 'Qv7Lm2' * 8
BEARER_VALUE = 'mF3kP9' + 'xQ2wL8' + 'vN4zR1'
PASSWORD_VALUE = 'Tr0ub4' + 'dorPlum' + '77x'
RUN = 'ghp' + '_' + 'A1b2C3d4E5' * 4
SECRETS = (KEY, BEARER_VALUE, PASSWORD_VALUE, RUN)
# words a person searches for that the same filter could take: kept
SNAKE = 'test_sign_in_after_2_tries_puts_the_login_back'


def tmp():
    return os.path.realpath(tempfile.mkdtemp(prefix='switchboard-memory-private-'))


def line(kind, text, minute, extra=None):
    d = {'type': kind, 'isSidechain': False, 'cwd': FOLDER, 'uuid': 'u-%s-%d' % (kind, minute),
         'timestamp': '2026-08-03T10:%02d:00.000Z' % minute}
    if kind == 'user':
        d['message'] = {'role': 'user', 'content': text}
    else:
        d['message'] = {'id': 'msg_%d' % minute, 'role': 'assistant', 'model': 'claude-opus-5-5',
                        'content': [{'type': 'text', 'text': text}]}
    d.update(extra or {})
    return json.dumps(d, separators=(',', ':')) + '\n'


def write_history(root):
    """One made-up chat whose prompt holds every secret beside ordinary words, and whose name holds one too."""
    d = os.path.join(root, M.project_folder_name(FOLDER))
    os.makedirs(d)
    prompt = ('the persimmon deploy fails with key %s and header Authorization: Bearer %s, also password=%s and '
              'the run %s; the token authentication step and %s are fine' % (KEY, BEARER_VALUE, PASSWORD_VALUE, RUN,
                                                                             SNAKE))
    with open(os.path.join(d, '7d0c9a52-1111-4222-8333-944455556666.jsonl'), 'w') as f:
        f.write(line('user', prompt, 1))
        f.write(line('assistant', 'The quince service needs a new key; the old one %s is set.' % KEY, 2))
        f.write(json.dumps({'type': 'custom-title', 'customTitle': 'orchard key %s' % KEY,
                            'sessionId': '7d0c9a52-1111-4222-8333-944455556666'}) + '\n')
    return d


def disk_bytes(index):
    """Every byte of the index and SQLite's files beside it."""
    out = b''
    for p in (index, index + '-journal', index + '-wal', index + '-shm'):
        if os.path.exists(p):
            with open(p, 'rb') as f:
                out += f.read()
    return out


def distinct(secret):
    """The part of a secret a search would look for: its longest word."""
    return max(secret.replace('-', ' ').replace('_', ' ').split(), key=len)


class SecretsStayOut(object):
    fts = None

    def setUp(self):
        self.root = os.path.join(tmp(), 'projects')
        write_history(self.root)
        self.index = os.path.join(tmp(), 'data', 'packs', M.INDEX_NAME)
        st = M.update_index(self.root, self.index, fts=self.fts)
        self.assertEqual(st['fts5'], self.fts)

    def test_no_secret_is_found_by_a_search(self):
        for s in SECRETS:
            self.assertEqual(M.search(distinct(s), 5, self.index), [], s[:6])

    def test_no_secret_is_in_the_bytes_on_disk(self):
        raw = disk_bytes(self.index)
        self.assertTrue(raw)
        for s in SECRETS:
            for form in (s, s.lower(), distinct(s), distinct(s).lower()):
                self.assertNotIn(form.encode(), raw, s[:6])

    def test_ordinary_words_beside_them_are_still_found(self):
        for q in ('persimmon', 'quince', 'deploy fails', 'authentication', 'orchard', 'sign in after tries'):
            got = M.search(q, 5, self.index)
            self.assertEqual(len(got), 1, q)
        self.assertEqual(len(M.search('login back', 5, self.index)), 1)
        self.assertIn(SNAKE.encode(), disk_bytes(self.index))

    def test_the_title_holds_no_secret(self):
        r = M.search('persimmon', 5, self.index)[0]
        self.assertNotIn(distinct(KEY), r['title'])
        self.assertTrue(r['title'].startswith('orchard key '))


class TestSecretsStayOutFts5(SecretsStayOut, unittest.TestCase):
    fts = True

    def setUp(self):
        if not M.fts5_available():
            self.skipTest('this Python has no FTS5')
        SecretsStayOut.setUp(self)


class TestSecretsStayOutPlain(SecretsStayOut, unittest.TestCase):
    fts = False


class TestWhatIsHidden(unittest.TestCase):
    def test_values_after_a_colon_or_equals_are_hidden_even_when_word_shaped(self):
        for text in ('password=correcthorsebatterystaple', 'token: correcthorsebatterystaple',
                     'secret="%s"' % SNAKE):
            self.assertIn('[hidden]', M.private_text(text), text)

    def test_long_names_and_words_after_a_space_are_kept(self):
        for text in ('the token authentication step', 'a secret configuration file', SNAKE,
                     'self.%s()' % SNAKE, 'BEARER_TOKEN_FOR_THE_STAGING_SERVER_NUMBER_2'):
            self.assertEqual(M.private_text(text), text)

    def test_the_quick_look_passes_by_only_text_redact_would_leave(self):
        import random
        from switchboard import core
        rnd = random.Random(7)
        bits = ['the', 'Token', 'PASSWORD', 'ſecret', 'Bearer', 'apiKey', 'api_key', ':', '=', ' ', '"', '/', '.',
                'sk-ant-', 'eyJ', 'x' * 39, 'Ab1' * 5, 'q7' * 6, '-', '_', '+', 'é', '\n', '@', '://', 'db', 'u',
                '-----BEGIN ', 'PRIVATE KEY-----', 'AKIA', 'ASIA', 'QZ7X' * 4, 'secret_access_key', 'DB_PASSWD',
                'pwd', 'monkey', 'sk-', 'ghp_', 'github_pat_', 'xoxb-', 'AIza', 'sk_live_', 'pk_live_', 'Ab12' * 9]
        for _ in range(4000):
            t = ''.join(rnd.choice(bits) for _ in range(rnd.randint(1, 12)))
            if not M._may_hide(t):
                self.assertEqual(M._hide_all(t), t, repr(t))
                self.assertEqual(core.redact(t), t, repr(t))
        self.assertFalse(M._may_hide('fix the login loop in server.py, line 120'))

    def test_the_quick_look_sees_each_shape_alone(self):
        for t in ('-----BEGIN ' + 'PRIVATE KEY----- MIIB' + 'q' * 10, 'AKIA' + 'QZ7X' * 4, 'ASIA' + 'W9P2' * 4,
                  'ghp_' + 'Ab12' * 5, 'github_pat_' + 'Kl12' * 6, 'xoxb-' + '1234567890-AbCdEfGhIj',
                  'AIza' + 'Sy' + 'A1b2C3d4E5' * 3 + 'xyz', 'rk_live_' + 'Cd34' * 4, 'sk-' + 'Ab12' * 6,
                  'postgres://app:' + 'S3cretPass' + '@db.example', 'DB_PWD=' + 'ora' + 'nge8sky',
                  'SMTP_PASSWD=' + 'pl' + 'um7tree', 'STRIPE_KEY=' + 'abc' + '12345xyz',
                  'eyJ' + 'abcdef1.' + 'mnopqr2.' + 'stuvwx3', 'Zq9' * 14, 'sk-ant-' + 'Qv7Lm2Qv',
                  'hf_' + 'AbCdEfGhIj' * 3 + 'Kl', 'glpat-' + 'Ab12Cd34Ef56Gh78Ij90', 'npm_' + 'Ab12' * 8,
                  'Authorization: Basic ' + 'dXNlcjpw' + 'YXNzd29yZDEyMw==', 'pw: ' + 'Sup3rSecret!',
                  'CREDENTIALS=' + 'abcdefgh12345', 'passphrase=' + 'correct-horse'):
            self.assertNotEqual(M._hide_all(t), t, t[:12])
            self.assertTrue(M._may_hide(t), t[:12])

    def test_key_shaped_strings_are_hidden(self):
        for said, s in (('key ' + KEY, KEY), ('Bearer ' + BEARER_VALUE, BEARER_VALUE),
                        ('password=' + PASSWORD_VALUE, PASSWORD_VALUE), (RUN, RUN)):
            self.assertNotIn(s, M.private_text('see %s here' % said))


# Well-known credential shapes a prompt can hold, each built from parts (none sits whole in this file).
SHAPES = (
    ('a private key block', 'here -----BEGIN ' + 'RSA PRIVATE' + ' KEY----- MIIEow' + 'IBAAKCAQEA' * 3 +
     ' Zq9 -----END RSA PRIVATE KEY----- thanks', ('MIIEow', 'IBAAKCAQEA', 'Zq9'), ('here ', ' thanks')),
    ('a PGP private key block', 'mine: -----BEGIN PGP ' + 'PRIVATE KEY BLOCK----- lQdGBF/' + 'abc+def' * 3 +
     ' -----END PGP PRIVATE KEY BLOCK----- done', ('lQdGBF', 'abc+def'), ('mine: ', ' done')),
    ('an OpenSSH key cut short', 'key: -----BEGIN ' + 'OPENSSH PRIVATE' + ' KEY----- b3BlbnNz' + 'aC1rZXk' * 4,
     ('b3BlbnNz', 'aC1rZXk'), ()),
    ('an AWS access key id', 'use AKIA' + 'QZ7X' * 4 + ' and ASIA' + 'W9P2' * 4 + ' now', ('QZ7X', 'W9P2'),
     ('use ', ' now')),
    ('an AWS secret', 'aws_secret_access_key = ' + ('wJa' + 'lrXUtn/K7MDENG+bPxRfiCY' + 'Ab12Cd34Ef9z0Yq8Wx7v')[:40],
     ('wJalrXUtn', 'bPxRfiCY'), ('aws_secret_access_key',)),
    ('a secret access key in JSON', '"SecretAccessKey": "' + ('Zm9v' + 'YmFy/9+Qw' * 5)[:40] + '"', ('Zm9vYmFy',), ()),
    ('an .env line', 'STRIPE_SECRET_KEY=' + 'abc' + '12345xyz', ('abc12345xyz',), ('STRIPE_SECRET_KEY',)),
    ('a YAML password', 'DB_PASSWORD: ' + 'hun' + 'ter22!x', ('hunter22',), ('DB_PASSWORD',)),
    ('a JSON key', '{"apiKey": "' + 'AbCd' + '1234efgh' + '"}', ('AbCd1234efgh',), ('apiKey',)),
    ('passwd and pwd', 'SMTP_PASSWD=' + 'pl' + 'um7tree and db_pwd=' + 'ora' + 'nge8sky', ('plum7tree', 'orange8sky'),
     ('SMTP_PASSWD', 'db_pwd')),
    ('a password in a URL', 'postgres://app:' + 'S3cr' + 'etPass' + '@db.example:5432/main', ('S3cretPass',),
     ('postgres://app:', '@db.example:5432/main')),
    ('sk-', 'key sk-' + 'proj-' + 'Ab12' * 6, ('Ab12Ab12',), ()),
    ('GitHub', 'ghp_' + 'Ab12' * 5 + ' gho_' + 'Cd34' * 5 + ' ghu_' + 'Ef56' * 5 + ' ghs_' + 'Gh78' * 5 + ' ghr_' +
     'Ij90' * 5 + ' github_pat_' + 'Kl12' * 6, ('Ab12', 'Cd34', 'Ef56', 'Gh78', 'Ij90', 'Kl12'), ()),
    ('Slack', 'xoxb-' + '1234567890-' + 'AbCdEfGhIj' + ' xoxp-' + '22334455-' + 'KlMnOpQr' + ' xoxa-' + '9z8y7x6w5v' +
     ' xoxr-' + 'u4t3s2r1q0' + ' xoxs-' + 'p9o8n7m6l5', ('AbCdEfGhIj', 'KlMnOpQr', '9z8y7x6w5v', 'u4t3s2r1q0',
                                                        'p9o8n7m6l5'), ()),
    ('Google', 'AIza' + 'Sy' + 'A1b2C3d4E5' * 3 + 'xyz', ('A1b2C3d4E5',), ()),
    ('a passphrase', 'passphrase=' + 'correct-horse-' + 'battery-staple', ('horse', 'staple'), ('passphrase=',)),
    ('pw', 'pw: ' + 'Sup3r' + 'Secret!', ('Sup3rSecret',), ('pw: ',)),
    ('credentials', 'CREDENTIALS=' + 'abcdefgh' + '12345', ('abcdefgh12345',), ('CREDENTIALS=',)),
    ('Hugging Face', 'hf_' + 'AbCdEfGhIj' * 3 + 'Kl', ('AbCdEfGhIj',), ()),
    ('GitLab', 'glpat-' + 'Ab12Cd34Ef' + '56Gh78Ij90', ('Ab12Cd34Ef',), ()),
    ('npm', 'npm_' + 'Ab12' * 8, ('Ab12Ab12',), ()),
    ('Basic authorization', 'Authorization: Basic ' + 'dXNlcjpw' + 'YXNzd29yZDEyMw==', ('dXNlcjpw',),
     ('Authorization: Basic ',)),
    ('a URL password with a slash', 'https://admin:' + 's3cr/etPw' + '@host.example/x', ('s3cr', 'etPw'),
     ('https://admin:', '@host.example/x')),
    ('a URL password with an @', 'https://admin:' + 'p@ss' + '@host.example/x', ('p@ss', 'ss@'),
     ('https://admin:', '@host.example/x')),
    ('an AWS id with a letter after it', 'AKIA' + 'QZ7X' * 4 + 'x', ('QZ7X',), ()),
    ('a mixed-case key of 40 or more', 'see ' + 'Ab1Cd2' * 7 + ' here', ('Ab1Cd2Ab1Cd2',), ('see ', ' here')),
    ('Stripe', 'sk_live_' + 'Ab12' * 4 + ' rk_live_' + 'Cd34' * 4 + ' pk_live_' + 'Ef56' * 4, ('Ab12', 'Cd34', 'Ef56'),
     ()),
)

# Ordinary long names a person searches for, which hold digits or sit after "key"
NAMES = ('test_login_flow_works_with_oauth2_and_utf8_cookies_enabled',
         'react-native-community-async-storage-v2-migration-plan', 'Primary key: customer_id_and_order_id',
         'sorted(rows, key=operator.itemgetter)')


class TestCredentialShapes(unittest.TestCase):
    def test_each_shape_is_hidden_and_the_words_around_it_stay(self):
        for name, text, gone, kept in SHAPES:
            got = M.private_text(text)
            for g in gone:
                self.assertNotIn(g, got, name)
            for k in kept:
                self.assertIn(k, got, name)
            self.assertIn('[hidden]', got, name)

    def test_ordinary_text_beside_those_names_is_kept(self):
        for text in ('the key: remember to commit first', 'tokenizer = AutoTokenizer.from_pretrained(name)',
                     'max_tokens=4096', 'if token == expected_token:', 'a monkey: bananas', 'sort_keys=True',
                     'see https://example.org/docs and mail me@example.org', 'the sk-learn docs',
                     'password = os.environ["DB_PASSWORD"]', 'API_KEY=${API_KEY}',
                     'https://registry.example:443/@scope/pkg', NAMES[0], NAMES[1], NAMES[2], NAMES[3],
                     'rewrite-the-onboarding-flow-for-ios17-and-android14-users', '--api-key-file=/etc/foo/bar.txt',
                     'cache_key=user_profile_settings_page'):
            self.assertEqual(M.private_text(text), text)

    def test_ordinary_long_names_are_found_by_search(self):
        root = os.path.join(tmp(), 'projects')
        d = os.path.join(root, M.project_folder_name(FOLDER))
        os.makedirs(d)
        with open(os.path.join(d, '5e6f7a8b-1111-4222-8333-944455556666.jsonl'), 'w') as f:
            for i, n in enumerate(NAMES):
                f.write(line('user', 'look at %s please' % n, i + 1))
        index = os.path.join(tmp(), 'data', 'packs', M.INDEX_NAME)
        M.update_index(root, index)
        for q in ('oauth2 cookies enabled', 'async storage migration', 'customer_id_and_order_id',
                  'operator itemgetter'):
            self.assertEqual(len(M.search(q, 5, index)), 1, q)
        raw = disk_bytes(index)
        for n in NAMES:
            self.assertIn(n.encode(), raw)

    def test_a_key_cut_at_the_size_cap_leaves_no_piece(self):
        key = 'Qv7Lm2' * 7 + 'Zz'
        root = os.path.join(tmp(), 'projects')
        d = os.path.join(root, M.project_folder_name(FOLDER))
        os.makedirs(d)
        with open(os.path.join(d, '6f7a8b9c-1111-4222-8333-944455556666.jsonl'), 'w') as f:
            f.write(line('user', 'x' * (M.MAX_TEXT - 20) + ' ' + key, 1))
            f.write(line('assistant', 'the persimmon is ripe', 2))
        index = os.path.join(tmp(), 'data', 'packs', M.INDEX_NAME)
        M.update_index(root, index)
        raw = disk_bytes(index).lower()
        self.assertNotIn(b'qv7lm2', raw)

    def test_a_first_prompt_that_is_only_a_key_is_in_no_table_and_no_file(self):
        import sqlite3
        key = 'xoxb-' + '1234567890-' + 'AbCdEfGhIjKl'
        for fts in (True, False) if M.fts5_available() else (False,):
            root = os.path.join(tmp(), 'projects')
            d = os.path.join(root, M.project_folder_name(FOLDER))
            os.makedirs(d)
            with open(os.path.join(d, '1a2b3c4d-1111-4222-8333-944455556666.jsonl'), 'w') as f:
                f.write(line('user', key, 1))
                f.write(line('assistant', 'Noted, the persimmon bot can post now.', 2))
            index = os.path.join(tmp(), 'data', 'packs', M.INDEX_NAME)
            M.update_index(root, index, fts=fts)
            r = M.search('persimmon', 5, index)[0]
            self.assertEqual(r['title'], '[hidden]')
            self.assertNotIn('AbCdEfGhIjKl', ' '.join(r['snippets']))
            con = sqlite3.connect(index)
            try:
                for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
                    for row in con.execute('SELECT * FROM "%s"' % table):
                        self.assertNotIn('AbCdEfGhIj', repr(row), table)
                        self.assertNotIn('abcdefghij', repr(row).lower(), table)
            finally:
                con.close()
            raw = disk_bytes(index).lower()
            self.assertNotIn(b'abcdefghij', raw)
            self.assertNotIn(b'1234567890', raw)


class TestOlderIndex(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(tmp(), 'projects')
        write_history(self.root)
        self.index = os.path.join(tmp(), 'data', 'packs', M.INDEX_NAME)
        # what an older build wrote: schema 1, every word as it was typed
        with mock.patch.object(M, 'SCHEMA', 1), mock.patch.object(M, 'private_text', lambda t: t):
            M.update_index(self.root, self.index)
        self.assertIn(KEY.encode(), disk_bytes(self.index))

    def test_the_next_update_replaces_it_and_its_old_bytes_are_gone(self):
        ino = os.stat(self.index).st_ino
        fd = os.open(self.index, os.O_RDONLY)       # a reader holding the old file open still finds no secret
        try:
            st = M.update_index(self.root, self.index)
            os.lseek(fd, 0, os.SEEK_SET)
            old = b''
            while True:
                chunk = os.read(fd, 1 << 16)
                if not chunk:
                    break
                old += chunk
        finally:
            os.close(fd)
        self.assertNotIn(KEY.encode(), old)
        self.assertEqual(old.strip(b'\0'), b'')     # written over with zeros before it was removed
        self.assertNotEqual(os.stat(self.index).st_ino, ino)
        self.assertNotIn(KEY.encode(), disk_bytes(self.index))
        self.assertEqual(st['chats'], 1)
        self.assertEqual(len(M.search('persimmon', 5, self.index)), 1)

    def test_a_search_or_a_status_look_erases_it_too(self):
        self.assertEqual(M.search('persimmon', 5, self.index), [])
        self.assertFalse(os.path.exists(self.index))
        with mock.patch.object(M, 'SCHEMA', 1), mock.patch.object(M, 'private_text', lambda t: t):
            M.update_index(self.root, self.index)
        self.assertFalse(M.search_status(self.index)['on'])
        self.assertFalse(os.path.exists(self.index))


class TestErase(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(tmp(), 'projects')
        self.chats = write_history(self.root)
        self.index = os.path.join(tmp(), 'data', 'packs', M.INDEX_NAME)
        M.update_index(self.root, self.index)
        for end in ('-wal', '-shm'):                # as SQLite leaves them beside a file in WAL mode
            with open(self.index + end, 'wb') as f:
                f.write(b'persimmon ' * 10)

    def test_erase_removes_the_three_files_and_search_builds_it_again(self):
        got = M.erase(self.index)
        self.assertEqual(sorted(got['removed']), sorted([self.index, self.index + '-shm', self.index + '-wal']))
        self.assertGreater(got['bytes'], 0)
        for end in ('', '-wal', '-shm', '-journal'):
            self.assertFalse(os.path.exists(self.index + end), end)
        self.assertTrue(os.listdir(self.chats))     # Claude Code's chat files are not touched
        self.assertEqual(M.search('persimmon', 5, self.index), [])
        self.assertFalse(M.search_status(self.index)['on'])
        out = io.StringIO()
        M.main(['search', 'persimmon', '--no-update', '--index', self.index], out)
        self.assertEqual(out.getvalue(), 'No chat matched.\n')
        M.update_index(self.root, self.index)
        self.assertEqual(len(M.search('persimmon', 5, self.index)), 1)

    def test_the_command_prints_one_line_and_exits_0(self):
        out = io.StringIO()
        self.assertEqual(M.main(['erase', '--index', self.index], out), 0)
        said = out.getvalue()
        self.assertEqual(said.count('\n'), 1, said)
        self.assertTrue(said.startswith('Erased the search index of your past chats ('), said)
        self.assertIn('Search builds it again the next time you use it.', said)
        self.assertFalse(os.path.exists(self.index))
        out = io.StringIO()
        self.assertEqual(M.main(['erase', '--index', self.index], out), 0)
        self.assertEqual(out.getvalue(), 'There is no search index of your past chats to erase. Search builds it '
                                         'the next time you use it.\n')

    def test_a_link_in_its_place_is_refused_and_nothing_is_written(self):
        M.erase(self.index)
        other = os.path.join(tmp(), 'other.txt')
        with open(other, 'wb') as f:
            f.write(b'keep me')
        os.symlink(other, self.index)
        out = io.StringIO()
        self.assertEqual(M.main(['erase', '--index', self.index], out), 1)
        said = out.getvalue()
        self.assertEqual(said.count('\n'), 1, said)
        self.assertTrue(said.startswith('The search index at %s is a link' % self.index), said)
        self.assertTrue(os.path.islink(self.index))
        with open(other, 'rb') as f:
            self.assertEqual(f.read(), b'keep me')
        self.assertFalse(M.drop_old_index(self.index))

    def test_a_file_that_cannot_be_removed_is_one_plain_line(self):
        out = io.StringIO()
        with mock.patch.object(M.os, 'unlink', side_effect=PermissionError('Operation not permitted')):
            self.assertEqual(M.main(['erase', '--index', self.index], out), 1)
        said = out.getvalue()
        self.assertEqual(said.count('\n'), 1, said)
        self.assertTrue(said.startswith('Could not remove the search index of your past chats ('), said)

class TestDropOldIndexAtStart(unittest.TestCase):
    """drop_old_index() is safe to call when the page starts: it never raises, never waits long, and erases only an
    index that says it is of another schema."""

    def setUp(self):
        self.index = os.path.join(tmp(), 'data', 'packs', M.INDEX_NAME)

    def old_index(self):
        root = os.path.join(tmp(), 'projects')
        write_history(root)
        with mock.patch.object(M, 'SCHEMA', 1), mock.patch.object(M, 'private_text', lambda t: t):
            M.update_index(root, self.index)

    def test_missing_file_or_folder(self):
        self.assertFalse(M.drop_old_index(self.index))
        self.assertFalse(M.drop_old_index(os.path.join(tmp(), 'no', 'such', M.INDEX_NAME)))

    def test_part_made_files_are_left(self):
        os.makedirs(os.path.dirname(self.index))
        for raw in (b'', b'SQLite format 3\x00 cut short', b'not a database at all'):
            with open(self.index, 'wb') as f:
                f.write(raw)
            self.assertFalse(M.drop_old_index(self.index), raw)
            with open(self.index, 'rb') as f:
                self.assertEqual(f.read(), raw)

    def test_a_first_build_under_way_is_left(self):
        import sqlite3
        os.makedirs(os.path.dirname(self.index))
        con = sqlite3.connect(self.index, isolation_level=None)
        try:
            con.execute('BEGIN')
            con.execute('CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)')
            con.execute("INSERT INTO meta VALUES ('schema', '1')")     # not committed yet: nobody else sees it
            self.assertFalse(M.drop_old_index(self.index))
            self.assertTrue(os.path.exists(self.index))
        finally:
            con.close()

    def test_a_busy_old_index_is_left_at_once_and_erased_when_free(self):
        import sqlite3
        self.old_index()
        con = sqlite3.connect(self.index, isolation_level=None)
        try:
            con.execute('BEGIN EXCLUSIVE')                              # another process writing it
            t = time.time()
            self.assertFalse(M.drop_old_index(self.index))
            self.assertLess(time.time() - t, 2.0)
            self.assertTrue(os.path.exists(self.index))
        finally:
            con.close()
        self.assertTrue(M.drop_old_index(self.index))
        self.assertFalse(os.path.exists(self.index))

    def test_an_index_of_this_build_stays(self):
        root = os.path.join(tmp(), 'projects')
        write_history(root)
        M.update_index(root, self.index)
        self.assertFalse(M.drop_old_index(self.index))
        self.assertTrue(os.path.exists(self.index))

    def test_a_file_that_cannot_be_removed_is_no_error(self):
        self.old_index()
        with mock.patch.object(M.os, 'unlink', side_effect=PermissionError('read-only folder')):
            self.assertFalse(M.drop_old_index(self.index))


class TestPageRebuildsAfterAnErase(unittest.TestCase):
    """A running page whose index was erased by the command (another process) starts the rebuild at its next
    search, not after its five-minute wait."""

    def setUp(self):
        from switchboard import memorypage
        self.mp = memorypage
        self.index = os.path.join(tmp(), 'data', 'packs', M.INDEX_NAME)
        self.started = []
        patches = [mock.patch.object(M, 'index_path', lambda: self.index),
                   mock.patch.object(memorypage.threading, 'Thread', self.fake_thread),
                   mock.patch.dict(memorypage.STATE, {'at': time.time() - 10, 'running': False, 'failed': False,
                                                      'chats': None})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def fake_thread(self, target=None, name=None, daemon=None):
        started = self.started

        class T(object):
            def start(self):
                started.append(name)
        return T()

    def test_a_missing_index_starts_the_rebuild_at_once(self):
        st = self.mp.ensure()
        self.assertEqual(self.started, ['switchboard-memory-index'])
        self.assertTrue(st['running'])

    def test_after_a_failure_older_than_the_short_wait(self):
        # an erase while a rebuild was writing fails that rebuild; the next search half a minute on starts again
        self.mp.STATE.update(failed=True, at=time.time() - self.mp.ASKED - 1)
        self.mp.ensure()
        self.assertEqual(self.started, ['switchboard-memory-index'])

    def test_not_while_the_index_is_there_or_the_last_run_failed(self):
        os.makedirs(os.path.dirname(self.index))
        open(self.index, 'wb').close()
        self.mp.ensure()
        self.assertEqual(self.started, [])
        os.unlink(self.index)
        self.mp.STATE['failed'] = True
        self.mp.ensure()
        self.assertEqual(self.started, [])


if __name__ == '__main__':
    unittest.main()
