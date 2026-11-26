"""A login that can't be renewed is never copied aside, whatever its shape.

Claude Code's login item may hold a one-year token, an API key or an access token with no refresh token in more shapes
than the one Claude Code writes: the bare text, an object with no `claudeAiOauth`, the token nested anywhere in an
object. A switch away from any of them makes no recovery copy, puts the text in no other Keychain item or file, and
asks Anthropic nothing with it. The rule goes by what the text holds, not by its shape, and no wider: a text that
carries a refresh token is a sign-in and is copied aside as before, and so is a text with no token in it at all (an
emptied login, MCP logins only, anything else). A check over many made-up texts holds the rule to its definition.
Made-up tokens and accounts only; the Keychain and Anthropic are the suite's stand-ins."""
import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

import json
import os
import random
import unittest
from unittest import mock

from fakes_accounts import Rig
from switchboard.accounts import context, identity, live, net, switcher, vault

TOKEN = 'sk-ant-oat01-madeUpOneYearToken-0501-aaaaaaaaaaaa'
KEY = 'sk-ant-api03-madeUpConsoleKey-0502-bbbbbbbbbbbb'
REFRESH = 'sk-ant-ort01-madeUpRefreshToken-0503-cccccccccccc'

NEVER_COPIED = {
    'a bare token': TOKEN,
    'a bare token with a line end': TOKEN + '\n',
    'a bare API key': KEY,
    'an object with the token and no claudeAiOauth': json.dumps({'accessToken': TOKEN}),
    'an object with the key under its own name': json.dumps({'primaryApiKey': KEY}),
    'the token nested in an object': json.dumps({'saved': {'by': 'hand', 'value': [TOKEN]}}),
    'the token nested and written with an escape': json.dumps({'saved': {'value': TOKEN}}).replace('sk-ant',
                                                                                                    'sk\\u002dant'),
    'an emptied login beside a key': json.dumps({'claudeAiOauth': {'accessToken': '', 'refreshToken': ''},
                                                 'primaryApiKey': KEY}),
    'an access token that ends in a year, with a plan': json.dumps({'claudeAiOauth': {
        'accessToken': TOKEN, 'refreshToken': None, 'expiresAt': 99999999999999,
        'scopes': ['user:inference', 'user:profile'], 'subscriptionType': 'max'}}),
    'an empty refresh token': json.dumps({'claudeAiOauth': {'accessToken': TOKEN, 'refreshToken': ''}}),
}

COPIED_AS_BEFORE = {
    'a token and a refresh token with no claudeAiOauth': json.dumps({'accessToken': TOKEN, 'refreshToken': REFRESH}),
    'an emptied login': json.dumps({'claudeAiOauth': {'accessToken': '', 'refreshToken': ''}}),
    'MCP logins only': json.dumps({'mcpOAuth': {'made-up-server': {'accessToken': 'mcp-made-up-0504',
                                                                   'refreshToken': ''}}}),
    'a text that is no login': 'made-up text, no login in it',
}


class Case(unittest.TestCase):
    def setUp(self):
        self.start()

    def tearDown(self):
        self.stop()

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

    def each(self, cases):
        """(what, text) for each case, each on a made-up Mac of its own."""
        self.stop()
        for what, text in cases.items():
            self.start()
            yield what, text
            self.stop()
        self.start()

    def switch_away_from(self, text):
        self.rig.set_live(text)
        res = switcher.switch(self.ctx, 'uuid-a', kind='manual', source='cli', reason='test', interactive=True)
        self.assertTrue(res.ok, res.note)
        self.assertEqual(json.loads(self.rig.live_text())['claudeAiOauth']['accessToken'],
                         json.loads(self.rig.vault('uuid-a'))['claudeAiOauth']['accessToken'])
        return res

    def held_elsewhere(self, secret):
        """Every Keychain item but Claude Code's own, and every file under the made-up Mac, that holds `secret`."""
        out = []
        for (service, account), value in self.rig.sec.items().items():
            if (service, account) != (self.rig.service, self.rig.account) and secret in (value or ''):
                out.append((service, account))
        for folder, _, files in os.walk(self.rig.root):
            for name in files:
                with open(os.path.join(folder, name), 'rb') as f:
                    if secret.encode() in f.read():
                        out.append(name)
        return out


class NoShapeIsCopied(Case):
    def test_a_switch_away_copies_none_of_them_and_asks_nothing(self):
        for what, text in self.each(NEVER_COPIED):
            self.switch_away_from(text)
            self.assertEqual((self.stashed, vault.stashed(self.ctx)), ([], []), what)
            for secret in (TOKEN, KEY, text.strip()):
                self.assertEqual(self.held_elsewhere(secret), [], what)
                self.assertNotIn(secret, self.asked, what)


class ASignInOrNoTokenIsCopiedAsBefore(Case):
    def test_each_is_copied_aside_before_the_switch(self):
        for what, text in self.each(COPIED_AS_BEFORE):
            self.switch_away_from(text)
            self.assertEqual(self.stashed, [text], what)

    def test_a_sign_in_nobody_kept_is_copied_aside(self):
        x_text = self.rig.sign_in('uuid-x', 'x@example.com', 'org-x')
        self.rig.set_config(None)
        self.rig.server.script.append(('profile', net.NetError('timeout')))
        res = self.switch_away_from(x_text)
        self.assertIn('kept aside in a safety copy', res.note or '')
        self.assertEqual(self.stashed, [x_text])


def oracle(parts):
    """Whether a text built from these parts can be used but never renewed, from how it was built."""
    shape, access, refresh = parts
    usable = access in ('token', 'key') or (access == 'plain' and shape in ('oauth', 'top'))
    if shape == 'oauth':
        renews = refresh in ('ort', 'plain')
    elif shape == 'top':
        renews = refresh in ('ort', 'plain')
    else:
        renews = refresh == 'ort'
    return usable and not renews


def build(rnd, parts):
    shape, access, refresh = parts
    n = rnd.randint(1000, 9999)
    a = {'none': None, 'empty': '', 'token': 'sk-ant-oat01-madeUp%d-xxxxxxxxxxxxxxxx' % n,
         'key': 'sk-ant-api03-madeUp%d-yyyyyyyyyyyyyyyy' % n, 'plain': 'made-up-access-%d' % n}[access]
    r = {'none': None, 'empty': '', 'ort': 'sk-ant-ort01-madeUp%d-zzzzzzzzzzzz' % n,
         'plain': 'made-up-refresh-%d' % n}[refresh]
    if shape == 'bare':
        return '\n'.join(x for x in (a, r) if x) or 'made-up text %d' % n
    if shape == 'oauth':
        return json.dumps({'claudeAiOauth': {'accessToken': a, 'refreshToken': r}, 'mcpOAuth': {}})
    if shape == 'top':
        return json.dumps(dict((k, v) for k, v in (('accessToken', a), ('refreshToken', r)) if v is not None))
    return json.dumps({'kept': {'one': a, 'two': [r]}})


class TheRuleOverManyCases(unittest.TestCase):
    def test_the_rule_is_what_the_text_holds(self):
        rnd = random.Random(20261125)
        seen = {True: 0, False: 0}
        for case in range(6000):
            parts = (rnd.choice(('bare', 'oauth', 'top', 'nested')), rnd.choice(('none', 'empty', 'token', 'key',
                                                                                    'plain')),
                     rnd.choice(('none', 'empty', 'ort', 'plain')))
            if parts[0] == 'bare' and parts[1] == 'plain':
                continue                                # a bare made-up word is no token to anyone
            text = build(rnd, parts)
            login = live.parse(text)
            want = oracle(parts)
            self.assertEqual(live.unrenewable(login), want, (case, parts, text))
            if login.full_pair:                         # a sign-in, as before
                self.assertFalse(want, (case, parts))
            seen[want] += 1
        self.assertGreater(min(seen.values()), 1000)


if __name__ == '__main__':
    unittest.main()
