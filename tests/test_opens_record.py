import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The record of opens, and a proof that can grow old.

Every link opened for a person adds one line to <data>/opens.jsonl (owner-only, appended whole, moved aside past
its size like History's record): when, the artifact's id cut to 8 characters, its owner or none, the profile tried,
the verdict (ok, wrong, signin, trouble, unchecked), how many profiles were tried, and by what (page, map, poster,
explain). Never the whole id, a link, a title or anything a page showed, and nothing for the quiet check. An
account whose link was proved before proofs were dated gets a dated proof at its first quiet check that loads its
page, and the page says "last checked <date>" beside each link with a dated proof. A fake Chrome stands in for the
browser."""
import json
import os
import stat
import time
import unittest
from unittest import mock

from fakes_links import A, B, ID1, ID2, ID3, LINK, FakeChrome, Home

from switchboard import artifacts, browser, core, linkapp

DAY = 86400
KEYS = ['by', 'id', 'owner', 'profile', 't', 'tries', 'verdict']


def lines():
    try:
        with open(artifacts.opens_path()) as f:
            return [json.loads(x) for x in f.read().splitlines()]
    except FileNotFoundError:
        return []


class Record(Home):
    def setUp(self):
        super().setUp()
        self.chrome = FakeChrome(self, {'P1': 'Work', 'P2': 'Home'}, logins={'P1': A, 'P2': B},
                                 owners={ID1: A, ID2: B, ID3: A}, titles={ID1: 'Budget', ID2: 'Roadmap'})
        self.state(active=B, **{A: {'profile': 'P1', 'profile_src': 'you'}, B: {'profile': 'P2', 'profile_src': 'you'}})

    def check_line(self, x):
        self.assertEqual(sorted(x), KEYS)
        self.assertEqual(len(x['id']), 8)
        raw = json.dumps(x)
        for full in (ID1, ID2, ID3, 'claude.ai', 'Budget', 'Roadmap'):
            self.assertNotIn(full, raw)


class EachOpenIsOneLine(Record):
    def test_an_open_that_loads_first_time(self):
        self.page(ID1, owner=A, src='published')
        r = artifacts.go(LINK % ID1)
        self.assertEqual((r['email'], r['profile'], r['checked']), (A, 'P1', True))
        got = lines()
        self.assertEqual(len(got), 1)
        self.check_line(got[0])
        self.assertEqual({k: got[0][k] for k in KEYS if k != 't'},
                         {'by': 'page', 'id': ID1[:8], 'owner': A, 'profile': 'P1', 'tries': 1, 'verdict': 'ok'})
        self.assertAlmostEqual(got[0]['t'], time.time(), delta=60)
        self.assertEqual(stat.S_IMODE(os.stat(artifacts.opens_path()).st_mode), 0o600)

    def test_a_page_whose_owner_was_wrong_says_how_many_profiles_it_took(self):
        self.page(ID1, owner=B, src='published')            # history guessed B: P2 says "Page not found" first
        r = artifacts.go(LINK % ID1)
        self.assertEqual((r['email'], r['profile']), (A, 'P1'))
        got = lines()[-1]
        self.assertEqual((got['verdict'], got['tries'], got['profile'], got['owner']), ('ok', 2, 'P1', A))

    def test_tabs_not_readable_is_unchecked(self):
        self.chrome.osa = 'denied'
        self.page(ID1, owner=A, src='published')
        r = artifacts.go(LINK % ID1)
        self.assertFalse(r['checked'])
        got = lines()[-1]
        self.assertEqual((got['by'], got['verdict'], got['tries'], got['profile']), ('page', 'unchecked', 1, 'P1'))

    def test_a_signed_out_profile_is_signin(self):
        self.chrome.logins['P1'] = None
        self.page(ID1, owner=A, src='seen', profile='P1')
        r = artifacts.go(LINK % ID1)
        self.assertTrue(r.get('signin'))
        got = lines()[-1]
        self.assertEqual((got['verdict'], got['profile'], got['owner']), ('signin', 'P1', A))

    def test_claudes_own_error_page_is_trouble(self):
        self.chrome.outage = True
        self.page(ID1, owner=A, src='published')
        artifacts.go(LINK % ID1)
        self.assertEqual(lines()[-1]['verdict'], 'trouble')

    def test_no_profile_could_open_it_is_wrong(self):
        self.chrome.owners[ID3] = 'nobody@else.example'
        self.page(ID3, owner=None, src=None)
        with mock.patch.object(artifacts, 'refresh', lambda: None):
            with self.assertRaises(core.SwitchError):
                artifacts.go(LINK % ID3)
        got = lines()[-1]
        self.assertEqual((got['by'], got['verdict'], got['owner']), ('page', 'wrong', None))
        self.assertGreaterEqual(got['tries'], 2)

    def test_an_error_that_is_not_page_not_found_is_never_wrong(self):
        self.page(ID1, owner=A, src='published')
        with mock.patch.object(artifacts, 'learn_ok', side_effect=core.SwitchError('the state could not be saved')):
            with self.assertRaises(core.SwitchError):
                artifacts.go(LINK % ID1)                    # its page loaded; writing down what it proved failed
        self.assertEqual(lines(), [])

    def test_the_quiet_check_writes_no_line(self):
        self.page(ID1, owner=A, src='published')
        artifacts.go(LINK % ID1, quiet=True)
        self.assertEqual(artifacts.prove_one(A), 'ok')
        self.assertEqual(lines(), [])


class TheOtherWays(Record):
    def test_from_the_owner_map(self):
        self.page(ID1, owner=A, src='seen', profile='P1')
        artifacts.write_owner_map()
        got = artifacts.open_from_map(LINK % ID1)
        self.assertEqual(got['profile'], 'P1')
        x = lines()[-1]
        self.check_line(x)
        self.assertEqual((x['by'], x['verdict'], x['tries'], x['owner'], x['profile']),
                         ('map', 'unchecked', 1, A, 'P1'))

    def test_a_busy_browser_opens_from_the_map_and_says_so(self):
        self.page(ID1, owner=A, src='seen', profile='P1')
        artifacts.write_owner_map()
        self.patch(artifacts, 'GO_WAIT', 0.01)
        self.assertTrue(artifacts.GO_LOCK.acquire(timeout=5))
        try:
            artifacts.go_quietly(LINK % ID1)
        finally:
            artifacts.GO_LOCK.release()
        self.assertEqual([x['by'] for x in lines()], ['map'])

    def test_after_the_poster_opened_it(self):
        self.page(ID1, owner=A, src='seen', profile='P1')
        artifacts.write_owner_map()
        r = artifacts.go_quietly(LINK % ID1, sent_at=time.time() - 30, fallback_after=1.5)
        self.assertEqual(r['by'], 'poster')
        x = lines()[-1]
        self.check_line(x)
        self.assertEqual((x['by'], x['verdict'], x['owner'], x['profile']), ('poster', 'unchecked', A, 'P1'))
        self.assertEqual(self.chrome.opened, [])            # the link app opened it; the page opened nothing

    def test_an_owner_with_no_profile_gets_the_explanation(self):
        with core.locked() as st:
            st['accounts'][A].update(profile=None, profile_src=None)
        self.page(ID1, owner=A, src='seen')
        self.patch(linkapp, 'page_port', lambda: 7965)
        r = artifacts.go_quietly(LINK % ID1)
        self.assertTrue(r['no_profile'])
        self.assertEqual(len(lines()), 1)                   # one line for one click
        x = lines()[-1]
        self.check_line(x)
        self.assertEqual((x['by'], x['verdict'], x['tries'], x['owner'], x['profile']),
                         ('explain', 'unchecked', 0, A, None))


class TheRecordItself(Record):
    def test_it_moves_aside_past_its_size_and_never_raises(self):
        self.patch(artifacts, 'OPENS_MAX', 200)
        for i in range(4):
            artifacts.note_open(ID1, A, 'P1', 'ok', 1, 'page')
        self.assertTrue(os.path.exists(artifacts.opens_path() + '.1'))
        self.assertLessEqual(len(lines()), 2)
        self.assertEqual(stat.S_IMODE(os.stat(artifacts.opens_path() + '.1').st_mode), 0o600)
        x = artifacts.note_open(ID1, 7, None, 'odd', True, 'elsewhere')       # what it is given is cleaned
        self.assertEqual((x['owner'], x['verdict'], x['tries'], x['by']), (None, 'unchecked', 0, 'page'))
        with mock.patch('os.open', side_effect=OSError('read-only')):
            self.assertIsNone(artifacts.note_open(ID1, A, 'P1', 'ok', 1, 'page'))

    def test_the_demo_writes_none(self):
        self.patch(core, 'DEMO', True)
        self.assertIsNone(artifacts.note_open(ID1, A, 'P1', 'ok', 1, 'page'))
        self.assertFalse(os.path.exists(artifacts.opens_path()))


class AProofGrowsOld(Home):
    def test_a_link_proved_before_proofs_were_dated_gets_a_date_at_its_first_quiet_check(self):
        FakeChrome(self, {'P1': 'Work'}, logins={'P1': A}, owners={ID1: A}, titles={ID1: 'Budget'})
        self.state(active=A, **{A: {'profile': 'P1', 'profile_src': 'checked'}})
        self.page(ID1, owner=A, src='seen', profile='P1')
        names = browser.profiles()
        self.assertEqual(artifacts.link_state(core.load()['accounts'][A], names), ('proved', None, None))
        self.assertIsNone(artifacts.link_rows(core.load(), names, extensions={})[A]['checked'])
        self.assertEqual(artifacts.prove_one(A), 'ok')
        a = core.load()['accounts'][A]
        state, at, _ = artifacts.link_state(a, names)
        self.assertEqual(state, 'proved')
        self.assertAlmostEqual(at, time.time(), delta=60)
        self.assertEqual(artifacts.link_state(a, names, now=at + (artifacts.PROOF_DAYS + 1) * DAY)[0], 'old')
        row = artifacts.link_rows(core.load(), names, extensions={})[A]
        self.assertEqual(row['checked'], 'last checked %s' % time.strftime('%b %-d', time.localtime(at)))
        old = artifacts.link_rows(core.load(), names, now=at + (artifacts.PROOF_DAYS + 1) * DAY, extensions={})[A]
        self.assertEqual((old['link'], old['checked']), ('old', row['checked']))

    def test_the_page_draws_it_beside_each_matched_link(self):
        with open(os.path.join(os.path.dirname(artifacts.__file__), 'web', 'index.html'), encoding='utf-8') as f:
            page = f.read()
        body = page[page.index('function pairsHTML()'):]
        body = body[:body.index('\n}\n')]
        self.assertIn('(a.link || {}).checked', body)
        self.assertIn('esc(a.link.checked)', body)
        self.assertIn("I.check + profilePick(a) + when", body)


if __name__ == '__main__':
    unittest.main()
