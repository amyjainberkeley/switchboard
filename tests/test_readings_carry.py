import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# One Mac's readings on another: `switchboard readings give` prints each account's limits and when each was read, as
# JSON, with nothing secret; `switchboard readings take` shows, for accounts this Mac keeps, the readings newer than its
# own, for at most the time given (10 minutes unless said, an hour at most), marked as from another Mac on every
# surface, with their real age, and gone after. Automatic switching never reads them.
import io
import json
import os
import stat
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

from switchboard import __main__ as cli
from switchboard import core, engine, readings, report, server, term
from switchboard.accounts import api, context, usage, vault
from fakes_accounts import Rig

A, B, C = 'maya@northwind.example', 'maya@fernhill.example', 'sam@fernhill.example'
Z = '%Y-%m-%dT%H:%M:%SZ'


def iso(t):
    return time.strftime(Z, time.gmtime(t))


class Base(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.addCleanup(self.rig.close)
        self.ids = {}
        for uuid, email, org in (('uuid-a', A, 'org-n'), ('uuid-b', B, 'org-f')):
            self.rig.keep(uuid, email, org)
            self.ids[email] = vault.account_key(self.rig.doc(), uuid, org)
        use = context.use(self.rig.ctx)
        use.__enter__()
        self.addCleanup(use.__exit__, None, None, None)
        for path in (usage.store_path(), readings.store_path()):
            self.addCleanup(lambda p=path: os.path.exists(p) and os.unlink(p))
        self.now = time.time()

    def own(self, email, at, five=None, week=None, resets=None):
        """This Mac's own reading of an account, read at `at`."""
        store = usage.load_store()
        acct = store['accounts'].setdefault(self.ids[email], {})
        w = acct.setdefault('w', {})
        for name, pct in (('5-hour', five), ('weekly', week)):
            if pct is not None:
                w[name] = {'pct': pct, 'resets_at': resets or self.now + 3 * 3600, 'at': at, 'src': 'endpoint'}
        acct['ep'] = {'ok_at': at}
        usage.write_atomic(usage.store_path(), store)

    def given(self, *accounts):
        """A document as `readings give` prints it on the other Mac."""
        return {'schema': 1, 'given_at': iso(self.now), 'accounts': list(accounts)}

    def item(self, email, at, five=40.0, week=70.0, models=()):
        def lim(pct):
            return {'used_percent': pct, 'refills_at': iso(self.now + 2 * 3600), 'read_at': iso(at)}
        return {'email': email, 'checked_at': iso(at),
                'limits': {'five_hour': lim(five), 'week': lim(week),
                           'models': [dict({'name': n}, **lim(p)) for n, p in models]}}

    def run_cli(self, *args, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, 'stdout', out), mock.patch.object(sys, 'stderr', err):
            if stdin is not None:
                fake = mock.Mock()
                fake.buffer = io.BytesIO(stdin)
                with mock.patch.object(sys, 'stdin', fake):
                    code = cli.main(list(args))
            else:
                code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()


class Give(Base):
    def test_a_small_json_document_schema_first_with_each_limit_and_when_it_was_read(self):
        self.own(A, self.now - 120, five=62.5, week=70)
        code, out, err = self.run_cli('readings', 'give')
        self.assertEqual((code, err), (0, ''))
        self.assertTrue(out.startswith('{\n "schema": 1,'), out[:30])
        doc = json.loads(out)
        self.assertEqual(set(doc), {'schema', 'given_at', 'accounts'})
        self.assertEqual([a['email'] for a in doc['accounts']], [A, B])
        a, b = doc['accounts']
        self.assertEqual(set(a), {'email', 'checked_at', 'limits'})
        self.assertEqual(set(a['limits']), {'five_hour', 'week', 'models'})
        self.assertEqual(a['limits']['five_hour']['used_percent'], 62.5)
        self.assertEqual(a['limits']['five_hour']['read_at'], iso(self.now - 120))
        self.assertRegex(a['limits']['week']['refills_at'], r'Z$')
        self.assertEqual(b['limits']['five_hour'], {'used_percent': None, 'refills_at': None, 'read_at': None})
        for secret in ('uuid-a', 'org-n', 'sk-ant', 'accessToken', self.ids[A]):
            self.assertNotIn(secret, out)

    def test_it_gives_only_what_this_mac_read_itself(self):
        readings.take(self.given(self.item(A, self.now - 30)), now=self.now)
        doc = readings.give(now=self.now)
        self.assertIsNone(doc['accounts'][0]['limits']['five_hour']['used_percent'])


class Take(Base):
    def test_newer_only_for_accounts_kept_here(self):
        self.own(A, self.now - 600, five=10, week=20)
        self.own(B, self.now - 30, five=11, week=21)
        said = dict(readings.take(self.given(self.item(A, self.now - 60), self.item(B, self.now - 120),
                                             self.item(C, self.now - 10)), now=self.now))
        self.assertTrue(said[A].startswith('shown here until '), said)
        self.assertEqual(said[B], 'not taken: its own numbers here are newer')
        self.assertEqual(said[C], 'not kept here')
        got = api.readings()
        self.assertTrue(got[self.ids[A]].elsewhere)
        self.assertEqual(dict((w.name, w.pct) for w in got[self.ids[A]].windows), {'5-hour': 40.0, 'weekly': 70.0})
        self.assertEqual(got[self.ids[A]].read_at, core.parse_iso(iso(self.now - 60)))        # its real age, never "now"
        self.assertFalse(got[self.ids[B]].elsewhere)

    def test_never_past_the_time_given_and_this_macs_own_newer_reading_wins(self):
        readings.take(self.given(self.item(A, self.now - 60)), for_s=120, now=self.now)
        own = usage.readings(now=self.now)
        self.assertTrue(readings.shown(own, now=self.now + 119)[self.ids[A]].elsewhere)
        self.assertNotIn(self.ids[A], readings.shown(own, now=self.now + 120))
        self.own(A, self.now - 10, five=5, week=6)
        self.assertFalse(readings.shown(usage.readings(now=self.now), now=self.now)[self.ids[A]].elsewhere)

    def test_ten_minutes_unless_said_and_an_hour_at_most(self):
        self.assertEqual(readings.FOR_DEFAULT, 600)
        for words, seconds in (('10m', 600), ('1h', 3600), ('90s', 90), ('30', 1800), ('60m', 3600)):
            self.assertEqual(readings.duration(words), seconds)
        for words in ('2h', '61m', '3601s', '0', '-5m', 'soon', ''):
            with self.subTest(words=words), self.assertRaises(readings.Words):
                readings.duration(words)
        code, _, err = self.run_cli('readings', 'take', '-', '--for', '2h', stdin=b'{}')
        self.assertEqual(code, 2)
        self.assertIn('an hour at most', err)

    def test_a_reading_dated_ahead_of_this_clock_is_not_taken(self):
        said = dict(readings.take(self.given(self.item(A, self.now + 600)), now=self.now))
        self.assertEqual(said[A], "not taken: its numbers are dated ahead of this Mac's clock")
        self.assertNotIn(self.ids[A], api.readings())

    def test_a_clock_there_that_runs_ahead_never_makes_its_numbers_younger(self):
        ahead = 240.0
        doc = self.given(self.item(A, self.now - 60 + ahead))
        doc['given_at'] = iso(self.now + ahead)
        readings.take(doc, now=self.now)
        self.assertEqual(api.readings()[self.ids[A]].read_at, core.parse_iso(iso(self.now - 60 + ahead)) - (
            core.parse_iso(iso(self.now + ahead)) - self.now))
        self.assertLessEqual(api.readings()[self.ids[A]].read_at, self.now - 59)
        behind = self.given(self.item(B, self.now - 600))
        behind['given_at'] = iso(self.now - 300)
        readings.take(behind, now=self.now)
        self.assertEqual(api.readings()[self.ids[B]].read_at, core.parse_iso(iso(self.now - 600)))

    def test_the_kept_file_is_owner_only(self):
        readings.take(self.given(self.item(A, self.now - 60)), now=self.now)
        self.assertEqual(stat.S_IMODE(os.stat(readings.store_path()).st_mode), 0o600)

    def test_not_a_document_or_a_newer_one(self):
        for doc in (None, [], {'schema': 1}, {'accounts': []}):
            with self.subTest(doc=doc), self.assertRaises(core.SwitchError) as got:
                readings.take(doc, now=self.now)
            self.assertIn("isn't what `switchboard readings give` prints", str(got.exception))
        with self.assertRaises(core.SwitchError) as got:
            readings.take({'schema': 2, 'accounts': []}, now=self.now)
        self.assertIn('newer Switchboard', str(got.exception))

    def test_through_main_from_standard_input_and_a_file(self):
        doc = json.dumps(self.given(self.item(A, self.now - 60))).encode()
        code, out, err = self.run_cli('readings', 'take', '-', stdin=doc)
        self.assertEqual(code, 0, err)
        self.assertIn('%s: shown here until ' % A, out)
        path = os.path.join(self.rig.root, 'given.json')
        with open(path, 'wb') as f:
            f.write(doc)
        self.assertEqual(self.run_cli('readings', 'take', path, '--for', '5m')[0], 0)
        code, _, err = self.run_cli('readings', 'take', '--soon')
        self.assertEqual(code, 2)
        self.assertIn('Unknown word: --soon.', err)
        code, out, _ = self.run_cli('readings')
        self.assertEqual(code, 2)
        self.assertIn('switchboard readings give', out)
        self.assertIn('switchboard readings take [-|FILE] [--for 10m]', out)


class Marked(Base):
    def test_status_the_terminal_and_the_page_state_say_from_another_mac(self):
        self.own(B, self.now - 30, five=11, week=21)
        readings.take(self.given(self.item(A, self.now - 180)), now=self.now)
        view = cli.local_view()
        by = dict((a['email'], a) for a in view['accounts'])
        self.assertTrue(by[A]['elsewhere'])
        self.assertFalse(by[B]['elsewhere'])
        rows = dict((r['email'], r) for r in term.read(view)['accounts'])
        self.assertEqual(rows[A]['numbers'], 'from another Mac')     # the list says where, never how old
        self.assertEqual(rows[B]['numbers'], '')
        line = next(x for x in term.view(term.read(view)) if x[:1].isdigit() and A in x)
        self.assertTrue(line.endswith('from another Mac'), line)
        doc = report.document(view, core.VERSION)
        js = dict((a['email'], a) for a in doc['accounts'])
        self.assertEqual((js[A]['from_another_mac'], js[B]['from_another_mac']), (True, False))
        self.assertEqual(js[A]['checked_at'], iso(self.now - 180))
        self.assertEqual(js[A]['limits']['five_hour']['used_percent'], 40)

    def test_the_page_says_it_in_the_same_words(self):
        with open(os.path.join(HERE, '..', 'switchboard', 'web', 'index.html'), encoding='utf-8') as f:
            page = f.read()
        self.assertIn("(a.elsewhere ? 'from another Mac, ' : 'checked ') + x", page)
        self.assertIn('isOld(a) || a.elsewhere ? ageWords(a)', page)

    def test_gone_from_the_page_state_after_its_time_even_with_no_numbers_here(self):
        readings.take(self.given(self.item(A, self.now - 60)), for_s=60, now=self.now)
        st = core.blank_state()
        server.apply_rows(st, [dict(r, active=False) for r in engine.rows()])
        self.assertTrue(st['accounts'][A]['elsewhere'])
        self.assertIsNotNone(st['accounts'][A]['usage'])
        with mock.patch.object(readings.time, 'time', return_value=self.now + 61):
            server.apply_rows(st, [dict(r, active=False) for r in engine.rows()])
        self.assertFalse(st['accounts'][A]['elsewhere'])
        self.assertIsNone(st['accounts'][A]['usage'])


class NeverDecided(Base):
    def test_automatic_switching_reads_only_this_macs_own(self):
        self.own(A, self.now - 600, five=99, week=99)
        readings.take(self.given(self.item(A, self.now - 60, five=1, week=1)), now=self.now)
        self.assertTrue(api.readings()[self.ids[A]].elsewhere)
        decided_on = usage.readings(now=self.now)[self.ids[A]]
        self.assertFalse(decided_on.elsewhere)
        self.assertEqual(dict((w.name, w.pct) for w in decided_on.windows), {'5-hour': 99, 'weekly': 99})
        with open(os.path.join(HERE, '..', 'switchboard', 'accounts', 'autoloop.py'), encoding='utf-8') as f:
            loop = f.read()
        self.assertNotIn('api.readings', loop)
        self.assertNotIn('readings.shown', loop)


if __name__ == '__main__':
    unittest.main()
