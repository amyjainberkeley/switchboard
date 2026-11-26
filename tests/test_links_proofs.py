import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""Link proofs that expire. An account's profile is proved by a page load and dated; a sign-in page
or another account's page puts it in doubt; a quiet daily check proves it again; an account with no profile signed
in as it says so and offers Set up. A fake Chrome stands in for the browser."""
import time
import unittest

from fakes_links import A, B, C, G, ID1, ID2, ID3, LINK, FakeChrome, Home

from switchboard import artifacts, browser, core, linkapp

DAY = 86400


def acct(email):
    return core.load()['accounts'][email]


def state_of(email, names=None, now=None):
    return artifacts.link_state(acct(email), names if names is not None else browser.profiles(), now)[0]


class SignedOutThenSignedInAsAnother(Home):
    """A proved on P1; P1 signed out; then re-signed as B."""

    def test_a_profile_signed_out_then_signed_in_as_another_account(self):
        chrome = FakeChrome(self, {'P1': 'Work', 'P9': 'Spare'}, logins={'P1': A, 'P9': 'someone@else.example'},
                            owners={ID1: A, ID2: B}, titles={ID1: 'Budget', ID2: 'Roadmap'})
        self.state(active=A, **{A: {'profile': 'P1', 'profile_src': 'you'}, B: {}})
        self.page(ID1, owner=A, src='published')
        self.page(ID2, owner=B, src='published')
        r = artifacts.go(LINK % ID1)
        self.assertEqual((r['email'], r['profile'], r['checked']), (A, 'P1', True))
        self.assertEqual(state_of(A), 'proved')

        chrome.logins['P1'] = None                          # signed out (a cookie wipe, a session that expired)
        r = artifacts.go(LINK % ID1)
        self.assertTrue(r.get('signin'))
        self.assertEqual(artifacts.link_state(acct(A), browser.profiles())[:3:2], ('suspect', 'signin'))
        self.assertEqual(artifacts.link_rows(core.load(), browser.profiles())[A]['action']['do'], 'signin')

        chrome.logins['P1'] = B                             # signed in again, as B this time
        r = artifacts.go(LINK % ID2)
        self.assertEqual((r['email'], r['profile']), (B, 'P1'))
        self.assertEqual((acct(B)['profile'], state_of(B)), ('P1', 'proved'))
        with self.assertRaises(artifacts.NoProfile) as said:
            artifacts.go(LINK % ID1)                         # A's page: nowhere to open it now
        self.assertIn(A, str(said.exception))
        self.assertNotEqual(state_of(A), 'proved')
        self.assertFalse(any(artifacts.link_state(a, browser.profiles())[0] == 'proved' and a.get('profile') == 'P1'
                             for e, a in core.load()['accounts'].items() if e != B), 'two accounts proved on P1')
        row = artifacts.link_rows(core.load(), browser.profiles())[A]
        self.assertTrue(row['line'].startswith('No Chrome profile is signed in as this account'))
        self.assertEqual(row['action']['do'], 'setup')
        # A's page was only ever tried in P1, the one profile that could have opened it: no tab flashing through
        # every other account's profile, and only Switchboard's own tabs were closed.
        self.assertEqual([p for p, u, _ in chrome.opened if u == LINK % ID1], ['P1', 'P1', 'P1'])


class ProofsExpire(Home):
    def test_a_proof_is_dated_and_old_after_14_days(self):
        now = time.time()
        a = {'profile': 'P1', 'profile_src': 'checked', 'proof': {'profile': 'P1', 'at': now - 13 * DAY}}
        self.assertEqual(artifacts.link_state(a, {'P1': 'Work'}, now)[0], 'proved')
        a['proof']['at'] = now - 15 * DAY
        self.assertEqual(artifacts.link_state(a, {'P1': 'Work'}, now)[:2], ('old', now - 15 * DAY))
        self.assertTrue(artifacts.opens_in(a, {'P1': 'Work'}, now))      # still where its pages open
        a['proof']['at'] = now + 3600                                    # a clock set back: the date isn't trusted
        self.assertEqual(artifacts.link_state(a, {'P1': 'Work'}, now)[:2], ('proved', None))

    def test_a_proof_only_counts_for_the_profile_it_was_made_in(self):
        now = time.time()
        a = {'profile': 'P2', 'profile_src': 'you', 'proof': {'profile': 'P1', 'at': now}}
        self.assertEqual(artifacts.link_state(a, {'P1': 'W', 'P2': 'H'}, now)[0], 'unproved')
        a['doubt'] = {'profile': 'P1', 'at': now, 'why': 'signin'}
        self.assertEqual(artifacts.link_state(a, {'P1': 'W', 'P2': 'H'}, now)[0], 'unproved')

    def test_a_checked_proof_with_no_date_is_proved_with_no_date(self):
        self.assertEqual(artifacts.link_state({'profile': 'P1', 'profile_src': 'checked'}, {'P1': 'W'}), ('proved', None, None))
        self.assertEqual(artifacts.link_state({'profile': 'P1', 'profile_src': 'hint'}, {'P1': 'W'})[0], 'unproved')

    def test_a_missing_profile_folder_is_said_but_an_unreadable_list_is_not_taken_for_missing(self):
        a = {'profile': 'P7', 'profile_src': 'checked'}
        self.assertEqual(artifacts.link_state(a, {'P1': 'Work'})[0], 'missing')
        self.assertIsNone(artifacts.opens_in(a, {'P1': 'Work'}))
        self.assertEqual(artifacts.link_state(a, {})[0], 'proved')       # Local State couldn't be read: not known
        self.assertEqual(artifacts.link_state({}, {'P1': 'Work'})[0], 'none')

    def test_a_signed_out_profile_is_suspect_until_a_page_loads_there_again(self):
        now = time.time()
        a = {'profile': 'P1', 'profile_src': 'checked', 'proof': {'profile': 'P1', 'at': now - 60}, 'signed_out': now}
        self.assertEqual(artifacts.link_state(a, {'P1': 'W'}, now)[:3:2], ('suspect', 'signin'))
        self.assertEqual(artifacts.opens_in(a, {'P1': 'W'}, now), 'P1')     # one sign-in there fixes it
        a['proof']['at'] = now + 1
        self.assertEqual(artifacts.link_state(a, {'P1': 'W'}, now + 2)[0], 'proved')


class OneProfileOneAccount(Home):
    def test_a_page_of_another_account_in_a_proved_profile_puts_it_in_doubt(self):
        FakeChrome(self, {'P1': 'Work'}, logins={'P1': B}, owners={ID2: B})
        self.state(**{A: {'profile': 'P1', 'profile_src': 'checked', 'proof': {'profile': 'P1', 'at': time.time() - 99}},
                      B: {'profile': 'P1', 'profile_src': 'you'}})
        self.page(ID2, owner=B, src='listed')
        artifacts.learn_ok(ID2, 'P1', B, 'Roadmap', [])
        self.assertEqual(state_of(B), 'proved')
        self.assertEqual(artifacts.link_state(acct(A), browser.profiles())[:3:2], ('suspect', 'other_account'))
        self.assertEqual(acct(A)['doubt']['by'], B)
        row = artifacts.link_rows(core.load(), browser.profiles())[A]
        self.assertIn('is signed in as %s now' % B, row['line'])

    def test_an_account_whose_old_link_was_never_proved_moves_to_where_its_pages_load(self):
        FakeChrome(self, {'P1': 'Work', 'P2': 'Home'})
        self.state(**{A: {'profile': 'P1', 'profile_src': 'hint'}})
        self.page(ID1, owner=A, src='seen', profile='P2')
        artifacts.learn_ok(ID1, 'P2', A, 'Budget', [])
        self.assertEqual((acct(A)['profile'], state_of(A)), ('P2', 'proved'))
        said = [e['text'] for e in core.load()['events']]
        self.assertTrue(any('opens in the Google Chrome profile "Home" now (not "Work")' in t for t in said), said)

    def test_a_solid_link_stays_where_the_person_put_it(self):
        FakeChrome(self, {'P1': 'Work', 'P2': 'Home'})
        self.state(**{A: {'profile': 'P1', 'profile_src': 'you'}})
        artifacts.learn_ok(ID1, 'P2', A, 'Budget', [])
        self.assertEqual(acct(A)['profile'], 'P1')
        self.assertEqual(acct(A)['signed_in_profile'], 'P2')


class Candidates(Home):
    def test_suspect_links_are_tried_last_for_a_page_nobody_owns_yet(self):
        FakeChrome(self, {'P1': 'Work', 'P2': 'Home', 'P3': 'Side'})
        now = time.time()
        self.state(active=A, **{
            A: {'profile': 'P1', 'profile_src': 'checked', 'doubt': {'profile': 'P1', 'at': now, 'why': 'wrong'}},
            B: {'profile': 'P2', 'profile_src': 'checked', 'proof': {'profile': 'P2', 'at': now}},
            C: {'profile': 'P3', 'profile_src': 'hint'}})
        st = core.load()
        self.assertEqual([p for p, _ in artifacts.candidates(st, None)], ['P2', 'P3', 'P1'])

    def test_a_page_proved_to_be_its_owners_skips_other_accounts_proved_profiles(self):
        FakeChrome(self, {'P1': 'Work', 'P2': 'Home', 'P3': 'Side'})
        now = time.time()
        self.state(**{A: {}, B: {'profile': 'P2', 'profile_src': 'checked', 'proof': {'profile': 'P2', 'at': now}},
                      C: {'profile': 'P3', 'profile_src': 'hint'}})
        it = self.page(ID1, owner=A, src='seen')
        self.assertEqual([p for p, _ in artifacts.candidates(core.load(), it)], ['P3'])
        it2 = self.page(ID2, owner=A, src='published')      # history only: other accounts' profiles last, still tried
        self.assertEqual([p for p, _ in artifacts.candidates(core.load(), it2)], ['P3', 'P2'])


class TheDailyReproof(Home):
    def setUp(self):
        super().setUp()
        self.chrome = FakeChrome(self, {'P1': 'Work', 'P2': 'Home'}, logins={'P1': A, 'P2': B},
                                 owners={ID1: A, ID2: A, ID3: B}, titles={ID1: 'Budget', ID2: 'Plan', ID3: 'Road'})
        now = time.time()
        self.state(active=A, **{A: {'profile': 'P1', 'profile_src': 'checked', 'proof': {'profile': 'P1', 'at': now - 3 * DAY}},
                                B: {'profile': 'P2', 'profile_src': 'checked', 'proof': {'profile': 'P2', 'at': now - 9 * DAY}}})
        self.page(ID1, owner=A, src='seen', profile='P1')
        self.page(ID2, owner=A, src='published')
        self.page(ID3, owner=B, src='seen', profile='P2')

    def test_due_once_a_day_oldest_proof_first_and_a_recheck_first_of_all(self):
        now = time.time()
        self.assertEqual(artifacts.reprove_due(core.load(), browser.profiles(), now), [B, A])
        with core.locked() as st:
            st['accounts'][B]['reproof_at'] = now - 3600
        self.assertEqual(artifacts.reprove_due(core.load(), browser.profiles(), now), [A])
        with core.locked() as st:
            st['accounts'][B]['recheck'] = now - 10
            st['accounts'][B]['reproof_at'] = now - 400
        self.assertEqual(artifacts.reprove_due(core.load(), browser.profiles(), now), [B, A])

    def test_a_quiet_check_opens_one_page_in_the_background_reads_it_and_closes_it(self):
        self.assertEqual(artifacts.prove_one(A), 'ok')
        self.assertEqual(self.chrome.opened, [('P1', LINK % ID1, True)])
        self.assertEqual(len(self.chrome.closed), 1)
        self.assertEqual(self.chrome.tabs, [])
        a = acct(A)
        self.assertGreater(a['proof']['at'], time.time() - 60)
        self.assertGreater(a['reproof_at'], time.time() - 60)

    def test_one_deleted_page_cannot_condemn_a_working_profile(self):
        self.chrome.owners[ID1] = 'deleted'                 # the newest page is gone; the second one still opens
        self.assertEqual(artifacts.prove_one(A), 'ok')
        self.assertEqual([u for _, u, _ in self.chrome.opened], [LINK % ID1, LINK % ID2])

    def test_a_profile_now_signed_in_as_another_account_is_put_in_doubt(self):
        self.chrome.logins['P1'] = B
        self.assertEqual(artifacts.prove_one(A), 'wrong')
        self.assertEqual(artifacts.link_state(acct(A), browser.profiles())[:3:2], ('suspect', 'wrong'))
        self.assertIn('no longer opens the pages of %s' % A, core.load()['events'][-1]['text'])
        self.assertEqual(len(self.chrome.closed), 2)

    def test_a_signed_out_profile_is_marked(self):
        self.chrome.logins['P1'] = None
        self.assertEqual(artifacts.prove_one(A), 'signin')
        self.assertEqual(state_of(A), 'suspect')
        self.assertTrue(acct(A).get('signed_out'))

    def test_never_with_chrome_closed_tabs_unreadable_or_the_mac_busy(self):
        self.assertIsNone(artifacts.reprove_ready())
        self.chrome.load = 2.0
        self.assertEqual(artifacts.reprove_ready(), 'busy Mac')
        self.chrome.load = 0.2
        self.chrome.running = False
        self.assertEqual(artifacts.reprove_ready(), 'browser closed')
        self.assertIsNone(artifacts.reprove_tick())
        self.assertEqual(self.chrome.opened, [])            # the browser is never started for a check
        self.chrome.running = True
        self.chrome.consent = -1743
        browser.CONSENT.update(state=None, at=-1e9)
        self.assertEqual(artifacts.reprove_ready(), 'tabs not readable')

    def test_a_look_that_settles_nothing_is_tried_again_in_an_hour(self):
        self.chrome.delay = 100.0
        self.assertEqual(artifacts.prove_one(A), 'unchecked')
        left = artifacts.REPROVE_EVERY - (time.time() - acct(A)['reproof_at'])
        self.assertAlmostEqual(left, 3600, delta=120)

    def test_background_work_runs_only_in_the_page_and_never_in_tests_unless_asked(self):
        self.assertFalse(artifacts.background_on())
        linkapp.PAGE['up'] = True
        self.assertFalse(artifacts.background_on())          # in tests, the page's mark alone is not enough
        self.assertFalse(artifacts.start_reprove())
        artifacts.REPROVE['forced'] = True
        self.assertTrue(artifacts.start_reprove())
        for _ in range(200):
            if not artifacts.REPROVE['running']:
                break
            time.sleep(0.02)
        self.assertEqual(self.chrome.opened[0], ('P2', LINK % ID3, True))     # B: the oldest proof
        self.assertFalse(artifacts.start_reprove())          # and not again within 10 minutes


class SetUp(Home):
    def test_set_up_makes_a_profile_named_for_the_account_and_opens_the_sign_in_there(self):
        chrome = FakeChrome(self, {'Profile 4': 'Work'})
        self.state(**{G: {}, A: {}})
        r = artifacts.setup_profile(G)
        self.assertEqual(r['profile'], 'Claude gmail')
        self.assertEqual(chrome.opened, [('Claude gmail', 'https://claude.ai/login', False)])
        a = acct(G)
        self.assertEqual((a['profile'], a['profile_src'], state_of(G)), ('Claude gmail', 'you', 'unproved'))
        self.assertIn('sign in to claude.ai there once', core.load()['events'][-1]['text'])

    def test_the_name_tells_accounts_apart(self):
        self.assertEqual(artifacts.profile_label('maya@uni.example'), 'uni')
        self.assertEqual(artifacts.profile_label('maya@mail.example', {'maya@mail.example': {}, 'bo@mail.example': {}}),
                         'maya')
        self.assertEqual(artifacts.profile_label('maya@mail.example', {'maya@mail.example': {'alias': 'personal'}}),
                         'personal')
        FakeChrome(self, {'Claude gmail': 'Claude gmail'})
        self.state(**{G: {}})
        self.assertEqual(artifacts.setup_profile(G)['profile'], 'Claude gmail 2')

    def test_set_up_needs_a_browser_and_a_known_account(self):
        self.state(**{G: {}})
        with self.assertRaises(core.SwitchError):
            artifacts.setup_profile(G)                        # no Chrome in this home
        FakeChrome(self, {'P1': 'Work'})
        with self.assertRaises(core.SwitchError):
            artifacts.setup_profile('nobody@example.com')


class TodaysGmail(Home):
    """gmail linked to "Profile 4" as checked, but Profile 4's claude.ai is another account now."""

    def setUp(self):
        super().setUp()
        self.uni = 'maya@uni.example'
        self.chrome = FakeChrome(self, {'Profile 4': 'Work', 'Claude Uni': 'Claude Uni'},
                                 logins={'Profile 4': self.uni, 'Claude Uni': self.uni},
                                 owners={ID1: G, ID2: G}, extensions=('Claude Uni',))
        self.state(active=self.uni, **{G: {'profile': 'Profile 4', 'profile_src': 'checked'},
                                            self.uni: {'profile': 'Claude Uni', 'profile_src': 'checked'}})
        self.page(ID1, owner=G, src='published')
        self.page(ID2, owner=G, src='listed')

    def test_the_stale_proof_is_found_out_by_the_daily_check_and_the_row_says_so(self):
        self.assertEqual(state_of(G), 'proved')               # the stale 'checked' (no date): due at once
        self.assertEqual(artifacts.reprove_due(core.load(), browser.profiles())[0], G)
        self.assertEqual(artifacts.prove_one(G), 'wrong')
        row = artifacts.link_rows(core.load(), browser.profiles())[G]
        self.assertEqual(row['line'], 'No Chrome profile is signed in as this account. The profile "Work" no longer '
                                      'opens its pages.')
        self.assertEqual(row['action'], {'do': 'setup', 'label': 'Set up', 'profile_name': 'Claude gmail'})
        self.assertEqual(row['chrome']['state'], 'no_profile')
        self.assertEqual(artifacts.switch_note(core.load(), G),
                         "No Google Chrome profile is signed in as %s, so its pages won't open: Set up on "
                         "Switchboard's page." % G)

    def test_with_tabs_unreadable_a_known_owner_without_a_profile_is_explained_not_guessed(self):
        self.assertEqual(artifacts.prove_one(G), 'wrong')
        self.chrome.consent = -1744                        # macOS hasn't been asked yet: tabs can't be read
        browser.CONSENT.update(state=None, at=-1e9)
        before = len(self.chrome.opened)
        with self.assertRaises(artifacts.NoProfile):
            artifacts.go(LINK % ID1)
        self.assertEqual(len(self.chrome.opened), before)  # no guess opened in another account's profile
        r = artifacts.go_quietly(LINK % ID1)
        self.assertEqual(r['by'], 'explain')

    def test_a_click_on_gmails_page_explains_and_offers_set_up_instead_of_page_not_found(self):
        self.assertEqual(artifacts.prove_one(G), 'wrong')
        r = artifacts.go_quietly(LINK % ID1)
        self.assertEqual((r['by'], r['email'], r['no_profile']), ('explain', G, True))
        page = self.chrome.opened[-1]
        self.assertEqual(page[0], None)
        self.assertTrue(page[1].startswith('http://127.0.0.1:7799/go#owner%3D'))
        from urllib.parse import parse_qs, unquote
        said = parse_qs(unquote(page[1].split('#', 1)[1]))          # as go.html reads it: decode, then its two values
        self.assertEqual((said['owner'], said['url']), ([G], [LINK % ID1]))
        r = artifacts.explain_no_profile(G, LINK % ID1 + '?a=1&b=2')
        said = parse_qs(unquote(self.chrome.opened[-1][1].split('#', 1)[1]))
        self.assertEqual(said['url'], [LINK % ID1 + '?a=1&b=2'])     # a link with & in it arrives whole


class PageRows(Home):
    def test_each_link_state_has_its_line(self):
        now = time.time()
        FakeChrome(self, {'P1': 'Work', 'P2': 'Home', 'P3': 'Side'}, extensions=('P1',))
        self.state(**{
            A: {'profile': 'P1', 'profile_src': 'checked', 'proof': {'profile': 'P1', 'at': now - 60}},
            B: {'profile': 'P2', 'profile_src': 'checked', 'proof': {'profile': 'P2', 'at': now - 20 * DAY}},
            C: {'profile': 'P3', 'profile_src': 'hint'},
            G: {'profile': 'P9', 'profile_src': 'you'}})
        rows = artifacts.link_rows(core.load(), browser.profiles(), now)
        self.assertTrue(rows[A]['line'].startswith('Opens in the Google Chrome profile "Work", proved '))
        self.assertIn('last proved', rows[B]['line'])
        self.assertIn('not proved yet', rows[C]['line'])
        self.assertIn('Its profile "P9" is gone', rows[G]['line'])
        self.assertEqual(rows[A]['chrome']['state'], 'ready')
        self.assertEqual(rows[B]['chrome']['state'], 'missing')
        self.assertEqual(rows[B]['chrome']['action']['label'], 'Add to this profile')
        self.assertEqual(rows[B]['chrome']['action']['url'], browser.CLAUDE_EXTENSION_PAGE)
        for r in rows.values():
            self.assertNotIn('sign in', r['line'].lower().replace('signed in', ''))

    def test_claude_in_chrome_per_profile_with_the_account_its_claude_ai_follows(self):
        now = time.time()
        FakeChrome(self, {'P1': 'Work', 'P2': 'Home', 'P3': 'Side'}, extensions=('P2',))
        self.state(**{A: {'profile': 'P1', 'profile_src': 'checked', 'proof': {'profile': 'P1', 'at': now}},
                      B: {'profile': 'P2', 'profile_src': 'you'}})
        rows = dict((r['profile'], r) for r in artifacts.chrome_profiles(core.load(), browser.profiles(), now=now))
        self.assertEqual((rows['P1']['extension'], rows['P1']['account']), (False, A))
        self.assertEqual(rows['P1']['action']['label'], 'Add to this profile')
        self.assertEqual((rows['P2']['extension'], rows['P2']['version'], rows['P2']['action']), (True, '1.2.3_0', None))
        self.assertIsNone(rows['P3']['account'])                  # nobody's page has loaded there: not known
        self.assertIn('not known yet', rows['P3']['line'])
        self.assertEqual([r['name'] for r in artifacts.chrome_profiles(core.load(), browser.profiles(), now=now)],
                         ['Home', 'Side', 'Work'])

    def test_add_to_this_profile_opens_the_store_page_in_that_profile_only(self):
        chrome = FakeChrome(self, {'P1': 'Work'})
        artifacts.add_extension('P1')
        self.assertEqual(chrome.opened, [('P1', browser.CLAUDE_EXTENSION_PAGE, False)])
        with self.assertRaises(core.SwitchError):
            artifacts.add_extension('P7')

    def test_claude_code_without_its_native_host_says_so_for_everyone(self):
        FakeChrome(self, {'P1': 'Work'}, extensions=('P1',))
        self.state(**{A: {'profile': 'P1', 'profile_src': 'checked'}})
        row = artifacts.link_rows(core.load(), browser.profiles(), host=False)[A]['chrome']
        self.assertEqual(row['state'], 'no_host')
        self.assertIn('/chrome', row['line'])


class SwitchNotes(Home):
    def test_a_switch_to_an_account_whose_profile_lacks_the_extension_says_so_once(self):
        FakeChrome(self, {'P1': 'Work', 'P2': 'Home'}, extensions=('P1',))
        self.state(**{A: {'profile': 'P1', 'profile_src': 'checked'}, B: {'profile': 'P2', 'profile_src': 'checked'}})
        self.assertIsNone(artifacts.switch_note(core.load(), A))
        self.assertEqual(artifacts.switch_note(core.load(), B),
                         'Claude in Chrome needs the extension in "Home": Add to this profile, on Switchboard\'s page.')

    def test_the_desktop_app_line_comes_once(self):
        self.assertEqual(artifacts.desktop_note(), artifacts.DESKTOP_NOTE)
        self.assertIsNone(artifacts.desktop_note())
        self.assertIsNone(artifacts.desktop_note())


if __name__ == '__main__':
    unittest.main()
