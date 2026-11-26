"""A token or key written with \\u escapes is still a token or key, in text that is not a JSON object too.

Claude Code's login item may hold text written by hand that is not a JSON object: bare text, an object with a stray
comma, single quotes, a JSON list. When the token in it is written with \\u escapes (sk\\u002dant-...), it is still a
login that can't be renewed: a switch away makes no recovery copy of it, puts it in no other Keychain item or file,
and asks Anthropic nothing with it. The same text with a refresh token in it, written the same way, is a sign-in and
is copied aside as before. Made-up tokens and accounts only; the Keychain and Anthropic are the suite's stand-ins."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import unittest
from unittest import mock

from fakes_accounts import Rig
from switchboard.accounts import context, identity, live, switcher, vault

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0801-aaaaaaaaaaaa'
KEY = 'sk-ant-api03-madeUpConsoleKey-0802-bbbbbbbbbbbb'
REFRESH = 'sk-ant-ort01-madeUpRefreshToken-0803-cccccccccccc'


def escaped(secret, how='\\u002d'):
    return secret.replace('sk-ant', 'sk' + how + 'ant')


NEVER_COPIED = {
    'an object with a stray comma': '{"x":"%s",}' % escaped(TOKEN),
    'bare text': escaped(TOKEN),
    'bare text, upper-case escape': escaped(TOKEN, '\\u002D'),
    'single quotes': "{'x':'%s'}" % escaped(TOKEN),
    'a JSON list': json.dumps([TOKEN]).replace('sk-ant', 'sk\\u002dant'),
    'a key in an object with a stray comma': '{"apiKey":"%s",}' % escaped(KEY),
    'every letter escaped': ''.join('\\u%04x' % ord(c) for c in TOKEN),
}

COPIED_AS_BEFORE = {
    'a token and a refresh token, both escaped': '{"a":"%s","r":"%s",}' % (escaped(TOKEN), escaped(REFRESH)),
    'an escape that is no token': '{"x":"made\\u002dup text",}',
    'a key named twice, with a refresh token beside the token':
        '{"x":"%s","x":"b","r":"%s"}' % (escaped(TOKEN), escaped(REFRESH)),
    'a key named twice and no token': '{"x":"made\\u002dup","x":"b"}',
}

# A JSON object may name one key twice. A reader keeps the last value, and the earlier one stays in the text, which
# is what a copy would hold. A token written with escapes under the earlier one is in no parsed value at all.
OAUTH = '{"claudeAiOauth":%s}'
HIDDEN_BY_A_KEY_NAMED_TWICE = {
    'a key named twice, the token first': '{"x":"%s","x":"b"}' % escaped(TOKEN),
    'a key named twice, every letter escaped': '{"x":"%s","x":"b"}' % ''.join('\\u%04x' % ord(c) for c in TOKEN),
    'the login object twice, an emptied one last': '{"claudeAiOauth":%s,"claudeAiOauth":%s}' % (
        '{"accessToken":"%s","refreshToken":""}' % escaped(TOKEN), '{"accessToken":"","refreshToken":""}'),
    'accessToken twice inside the login object': OAUTH % (
        '{"accessToken":"%s","accessToken":"","refreshToken":""}' % escaped(TOKEN)),
    'accessToken twice at the top': '{"accessToken":"%s","accessToken":0}' % escaped(TOKEN),
    'a key under a name used twice': '{"k":"%s","k":""}' % escaped(KEY),
}


class Case(unittest.TestCase):
    def start(self):
        self.rig = Rig()
        self.ctx = self.rig.ctx
        self.use = context.use(self.ctx)
        self.use.__enter__()
        self.rig.two_accounts()                                     # Claude Code is on B
        self.asked, self.stashed = [], []
        real_lookup, real_stash = identity.lookup_profile, vault.stash

        def lookup(ctx, access_token, *args, **kwargs):
            self.asked.append(access_token)
            return real_lookup(ctx, access_token, *args, **kwargs)

        def stash(ctx, key, text, *args, **kwargs):
            self.stashed.append(text)
            return real_stash(ctx, key, text, *args, **kwargs)
        self.patches = [mock.patch.object(identity, 'lookup_profile', lookup), mock.patch.object(vault, 'stash', stash)]
        for patch in self.patches:
            patch.start()

    def stop(self):
        for patch in self.patches:
            patch.stop()
        self.use.__exit__(None, None, None)
        self.rig.close()

    def switch_away_from(self, text):
        self.rig.set_live(text)
        res = switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli', reason='test', interactive=True)
        self.assertTrue(res.ok, res.note)
        return res

    def held_elsewhere(self, secret):
        """Every Keychain item but Claude Code's own, and every file under the made-up Mac, that holds `secret`."""
        out = [k for k, v in self.rig.sec.items().items()
               if k != (self.rig.service, self.rig.account) and secret in (v or '')]
        for folder, _, files in os.walk(self.rig.root):
            for name in files:
                with open(os.path.join(folder, name), 'rb') as f:
                    if secret.encode() in f.read():
                        out.append(name)
        return out


class NoEscapedShapeIsCopied(Case):
    def never_copied(self, shapes):
        for what, text in shapes.items():
            with self.subTest(what):
                self.start()
                try:
                    self.assertTrue(live.unrenewable(live.parse(text)), what)
                    self.switch_away_from(text)
                    self.assertEqual((self.stashed, vault.stashed(self.ctx)), ([], []), what)
                    for secret in (text, TOKEN, KEY, text[:40], escaped(TOKEN), escaped(KEY)):
                        self.assertEqual(self.held_elsewhere(secret), [], what)
                    self.assertEqual(self.asked, [], what)
                finally:
                    self.stop()

    def test_a_switch_away_copies_none_of_them_and_asks_nothing(self):
        self.never_copied(NEVER_COPIED)

    def test_nor_a_token_under_a_key_that_is_named_twice(self):
        self.never_copied(HIDDEN_BY_A_KEY_NAMED_TWICE)


class ASignInWrittenTheSameWayIsCopiedAsBefore(Case):
    def test_each_is_copied_aside_before_the_switch(self):
        for what, text in COPIED_AS_BEFORE.items():
            with self.subTest(what):
                self.start()
                try:
                    self.assertFalse(live.unrenewable(live.parse(text)), what)
                    self.switch_away_from(text)
                    self.assertEqual(self.stashed, [text], what)
                finally:
                    self.stop()


if __name__ == '__main__':
    unittest.main()
