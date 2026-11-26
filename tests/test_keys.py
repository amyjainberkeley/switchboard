import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Tests for switchboard/keys.py: lanes in the Keychain.
#
# The Keychain here is a fake: no test runs /usr/bin/security or touches this Mac's real Keychain
# (tests/_isolate.py refuses the command outright). Every key is made up.
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from switchboard import core, gateway, keys as K

# Built from pieces, so no key-shaped text sits in this file.
KEY_A = 'sk-ant-' + 'api03-made-up-key-0001'
KEY_B = 'sk-or-' + 'v1-made-up-key-0002'
OAUTH = K.OAUTH_TOKEN_PREFIX + '01-made-up-login-token-0003'


class FakeKeychain(object):
    """Answers the three security commands keys.py uses, like /usr/bin/security would; records every call."""

    def __init__(self, cut=0, fail_delete=False):
        self.items = {}
        self.calls = []
        self.cut = cut                  # simulate a Keychain that drops characters
        self.cut_once = False           # ... on the next add only
        self.refuse_add = False         # simulate an add that fails and leaves the item as it was
        self.fail_delete = fail_delete

    def __call__(self, argv, stdin=None):
        self.calls.append((list(argv), stdin))
        assert argv[0] == K.SECURITY
        if argv[1:] == ['-i']:
            if self.refuse_add:
                return 1, ''
            for line in (stdin or '').splitlines():
                words = line.split()
                if words and words[0] == 'add-generic-password':
                    opts = dict(zip(words[1::1], words[2::1]))
                    value = opts['-w']
                    self.items[(opts['-s'], opts['-a'])] = value[:-self.cut] if self.cut else value
            if self.cut_once:
                self.cut, self.cut_once = 0, False
            return 0, 'security> '
        opts = dict(zip(argv[2::1], argv[3::1]))
        item = (opts.get('-s'), opts.get('-a'))
        if argv[1] == 'find-generic-password':
            if item not in self.items:
                return K.NOT_FOUND, ''
            return 0, (self.items[item] + '\n') if '-w' in argv else 'attributes\n'
        if argv[1] == 'delete-generic-password':
            if self.fail_delete:
                return 1, ''
            if self.items.pop(item, None) is None:
                return K.NOT_FOUND, ''
            return 0, ''
        raise AssertionError('unexpected command %r' % argv)


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        patch = mock.patch.dict(os.environ, {'SWITCHBOARD_HOME': os.path.join(self.home, 'data')})
        patch.start()
        self.addCleanup(patch.stop)
        self.kc = FakeKeychain()
        self.keys = K.Keys(runner=self.kc)

    def everything_written(self):
        out = ''
        for folder, _, names in os.walk(self.home):
            for name in names:
                with open(os.path.join(folder, name), 'rb') as f:
                    out += f.read().decode('utf-8', 'replace')
        return out


class Adding(Base):
    def test_the_key_goes_only_through_stdin_never_an_argument(self):
        self.keys.add('main', KEY_A)
        for argv, stdin in self.kc.calls:
            self.assertFalse(any(KEY_A in a for a in argv), argv)
        adds = [(argv, stdin) for argv, stdin in self.kc.calls if argv == [K.SECURITY, '-i']]
        self.assertEqual(len(adds), 1)
        self.assertEqual(adds[0][1], 'add-generic-password -U -s switchboard-gateway -a main -w %s\n' % KEY_A)

    def test_every_command_names_switchboards_own_service_and_nothing_else(self):
        self.keys.add('main', KEY_A)
        self.keys._secret('main')
        self.keys.remove('main')
        for argv, stdin in self.kc.calls:
            text = ' '.join(argv) + ' ' + (stdin or '')
            self.assertIn('-s switchboard-gateway', text)
            self.assertNotIn('Claude Code', text)

    def test_it_reads_the_key_back_and_keeps_it_only_when_exact(self):
        self.kc.cut = 1
        with self.assertRaises(core.SwitchError) as e:
            self.keys.add('main', KEY_A)
        self.assertIn('did not keep the key exactly', str(e.exception))
        self.assertEqual(self.kc.items, {})                 # removed again
        self.assertEqual(self.keys.list(), [])

    def test_a_failed_replacement_keeps_the_working_key(self):
        self.keys.add('main', KEY_A)
        self.kc.refuse_add = True                           # the Keychain refuses the new key
        with self.assertRaises(core.SwitchError):
            self.keys.add('main', KEY_A.replace('0001', '0009'))
        self.kc.refuse_add = False
        self.assertEqual(self.keys._secret('main'), KEY_A)   # never deleted
        self.assertEqual(self.keys.list()[0]['last4'], KEY_A[-4:])

    def test_a_mangled_replacement_puts_the_working_key_back(self):
        self.keys.add('main', KEY_A)
        self.kc.cut, self.kc.cut_once = 1, True             # the new key is stored short
        with self.assertRaises(core.SwitchError):
            self.keys.add('main', KEY_A.replace('0001', '0009'))
        self.assertEqual(self.keys._secret('main'), KEY_A)   # the old key was written back
        self.assertEqual(self.keys.list()[0]['last4'], KEY_A[-4:])

    def test_list_shows_names_and_last_four_only(self):
        self.keys.add('main', KEY_A)
        self.keys.add('spare', KEY_B, kind='openrouter')
        rows = self.keys.list()
        self.assertEqual([r['name'] for r in rows], ['main', 'spare'])
        self.assertEqual([r['last4'] for r in rows], [KEY_A[-4:], KEY_B[-4:]])
        self.assertNotIn(KEY_A[:-4], json.dumps(rows))
        self.assertNotIn(KEY_B[:-4], json.dumps(rows))

    def test_kinds_get_their_documented_url_and_header(self):
        self.keys.add('main', KEY_A)
        self.keys.add('spare', KEY_B, kind='openrouter')
        self.keys.add('own', KEY_B, kind='custom', base_url='https://llm.example.com/anthropic/', auth='x-api-key')
        rows = {r['name']: r for r in self.keys.list()}
        self.assertEqual((rows['main']['base_url'], rows['main']['auth']), ('https://api.anthropic.com', 'x-api-key'))
        self.assertEqual((rows['spare']['base_url'], rows['spare']['auth']), ('https://openrouter.ai/api', 'bearer'))
        self.assertEqual(rows['own']['base_url'], 'https://llm.example.com/anthropic')

    def test_an_openrouter_lane_carries_its_name_map_visibly(self):
        self.keys.add('spare', KEY_B, kind='openrouter')
        self.assertEqual(self.keys.list()[0]['names'], gateway.OPENROUTER_NAMES)
        self.keys.add('spare', KEY_B, kind='openrouter', names={'claude-opus-5-5': 'anthropic/claude-opus-5.5'})
        self.assertEqual(self.keys.list()[0]['names'], {'claude-opus-5-5': 'anthropic/claude-opus-5.5'})

    def test_adding_the_same_name_replaces_it_in_place(self):
        self.keys.add('a', KEY_A)
        self.keys.add('b', KEY_B, kind='openrouter')
        self.keys.add('a', KEY_A.replace('0001', '0009'))
        self.assertEqual([(r['name'], r['last4']) for r in self.keys.list()], [('a', '0009'), ('b', KEY_B[-4:])])

    def test_from_prompt_asks_without_the_command_line(self):
        asked = []
        self.keys.add_from_prompt('main', ask=lambda prompt: asked.append(prompt) or KEY_A + '\n')
        self.assertEqual(len(asked), 1)
        self.assertNotIn(KEY_A, asked[0])
        self.assertEqual(self.keys._secret('main'), KEY_A)

    def test_the_default_prompt_is_getpass(self):
        import inspect
        self.assertIn('getpass.getpass', inspect.getsource(K.Keys.add_from_prompt))


class Refusing(Base):
    def assert_refused(self, words, *args, **kw):
        with self.assertRaises(core.SwitchError) as e:
            self.keys.add(*args, **kw)
        self.assertIn(words, str(e.exception))
        for secret in (KEY_A, KEY_B, OAUTH):
            self.assertNotIn(secret, str(e.exception))
        self.assertEqual(self.kc.calls, [])

    def test_a_subscription_login_token_is_not_a_key(self):
        self.assert_refused('subscription login token', 'main', OAUTH, kind='custom', base_url='https://x.example.com')
        self.assert_refused('subscription login token', 'main', OAUTH)

    def test_an_anthropic_lane_needs_an_api_key(self):
        self.assert_refused('sk-ant-api', 'main', KEY_B)

    def test_odd_keys(self):
        self.assert_refused('does not look like an API key', 'main', 'short', kind='openrouter')
        self.assert_refused('does not look like an API key', 'main', KEY_B + ' x', kind='openrouter')
        self.assert_refused('does not look like an API key', 'main', '-' + KEY_B, kind='openrouter')
        self.assert_refused('does not look like an API key', 'main', 'k' * 513, kind='openrouter')

    def test_a_trailing_newline_is_never_accepted(self):
        self.assert_refused('lane name', 'main\n', KEY_A)
        self.assert_refused('does not look like an API key', 'main', KEY_A + '\n')
        self.assert_refused('plain model names', 'spare', KEY_B, kind='openrouter',
                            names={'claude-opus-5-5\n': 'anthropic/claude-opus-5.5'})

    def test_odd_names(self):
        for name in ('', '-x', 'a b', 'a/b', 'x' * 33, 'é', 'a\nb'):
            self.assert_refused('lane name', name, KEY_A)

    def test_base_urls(self):
        self.assert_refused('base URL', 'own', KEY_B, kind='custom', base_url='http://llm.example.com')
        self.assert_refused('base URL', 'own', KEY_B, kind='custom', base_url='https://user:pw@example.com')
        self.assert_refused('needs its base URL', 'own', KEY_B, kind='custom')
        self.keys.add('own', KEY_B, kind='custom', base_url='http://127.0.0.1:9')     # plain http to this Mac only

    def test_name_maps_point_only_at_claude(self):
        self.assert_refused('only point at Claude', 'spare', KEY_B, kind='openrouter',
                            names={'claude-opus-5-5': 'other/model-1'})
        self.assert_refused('plain model names', 'spare', KEY_B, kind='openrouter', names={'claude opus': 'claude'})

    def test_unknown_kind(self):
        self.assert_refused('anthropic, openrouter or custom', 'main', KEY_A, kind='mystery')


class RemovingAndOrdering(Base):
    def test_remove_deletes_item_and_record(self):
        self.keys.add('a', KEY_A)
        self.keys._secret('a')
        self.keys.remove('a')
        self.assertEqual(self.kc.items, {})
        self.assertEqual(self.keys.list(), [])
        with self.assertRaises(core.SwitchError):
            self.keys._secret('a')                          # nothing cached after a removal

    def test_remove_unknown(self):
        with self.assertRaises(core.SwitchError):
            self.keys.remove('nope')

    def test_a_keychain_that_will_not_delete_keeps_the_lane(self):
        self.keys.add('a', KEY_A)
        self.kc.fail_delete = True
        with self.assertRaises(core.SwitchError):
            self.keys.remove('a')
        self.assertEqual([r['name'] for r in self.keys.list()], ['a'])

    def test_an_item_already_gone_still_removes_the_lane(self):
        self.keys.add('a', KEY_A)
        self.kc.items.clear()
        self.keys.remove('a')
        self.assertEqual(self.keys.list(), [])

    def test_reorder(self):
        self.keys.add('a', KEY_A)
        self.keys.add('b', KEY_B, kind='openrouter')
        self.keys.reorder(['b', 'a'])
        self.assertEqual([r['name'] for r in self.keys.list()], ['b', 'a'])
        for bad in (['b'], ['a', 'a'], ['a', 'b', 'c'], 'ab'):
            with self.assertRaises(core.SwitchError):
                self.keys.reorder(bad)


class TheKeyStaysHidden(Base):
    def test_never_written_to_a_file_or_logged(self):
        core.log('a line before')
        self.keys.add('a', KEY_A)
        self.keys.add('b', KEY_B, kind='openrouter')
        self.keys._secret('a')
        self.keys.reorder(['b', 'a'])
        self.keys.remove('b')
        for secret in (KEY_A, KEY_B):
            self.assertNotIn(secret, self.everything_written())

    def test_no_public_function_returns_it(self):
        results = [self.keys.add('a', KEY_A), self.keys.add('b', KEY_B, kind='openrouter'), self.keys.list(),
                   self.keys.present('a'), self.keys.reorder(['b', 'a']), self.keys.remove('b'), self.keys.list()]
        text = repr(results)
        self.assertNotIn(KEY_A, text)
        self.assertNotIn(KEY_B, text)
        public = [n for n in dir(K.Keys) if not n.startswith('_')]
        self.assertEqual(sorted(public), ['add', 'add_from_prompt', 'list', 'present', 'remove', 'reorder'])
        self.assertIs(results[3], True)                    # present() answers yes or no, from the item's attributes

    def test_errors_never_carry_what_the_keychain_printed(self):
        def loud(argv, stdin=None):
            return 1, 'security: ' + (stdin or '')         # a Keychain that echoes its input on failure
        store = K.Keys(runner=loud)
        with self.assertRaises(core.SwitchError) as e:
            store.add('a', KEY_A)
        self.assertNotIn(KEY_A, str(e.exception))

    def test_the_record_file_is_private(self):
        self.keys.add('a', KEY_A)
        mode = stat.S_IMODE(os.stat(core.data_path('gateway-lanes.json')).st_mode)
        self.assertEqual(mode, 0o600)

    def test_the_secret_is_cached_briefly(self):
        self.keys.add('a', KEY_A)
        reads = len(self.kc.calls)
        self.assertEqual(self.keys._secret('a'), KEY_A)
        self.assertEqual(self.keys._secret('a'), KEY_A)
        self.assertEqual(len(self.kc.calls), reads + 1)

    def test_the_default_runner_never_puts_input_in_argv(self):
        import inspect
        source = inspect.getsource(K.Keys._keychain_add)
        self.assertIn("[SECURITY, '-i']", source)


if __name__ == '__main__':
    unittest.main()
