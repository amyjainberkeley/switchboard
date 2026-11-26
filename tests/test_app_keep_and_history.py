import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Logins, history, the page, the terminal, the link app.
#
# Made-up accounts only (*.example). The engine, the automatic-switching host, notifications and the page are
# stand-ins; every folder is a throwaway one; nothing here reaches the network.
import io
import json
import os
import subprocess
import sys
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
sys.path.insert(0, ROOT)

from switchboard import __main__ as cli
from switchboard import core, engine, keep, server
from switchboard.accounts import api
from test_keep import A, B, ORG, Sandbox as KeepSandbox

TEAM = 'org-team'                       # A's email in a second organization: another account


def in_thread(target, *args, **kwargs):
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


# ---------- the person's auto-switch choice is never undone by the tick ----------

class AutoChoiceStaysYours(KeepSandbox):
    """keep.auto_by_itself runs in the page's tick; the person's on or off comes from a request (the page's switch,
    the menu bar's, `switchboard auto on|off`). Each test puts the other side exactly in the window it races."""

    def auto(self):
        return self.fake.auto.get('enabled')

    def test_a_tick_between_the_change_and_the_mark_does_not_turn_it_back_on(self):
        self.fake.hold(A)
        self.fake.hold(B)
        self.signed_in(A)
        self.look(1000.0)
        self.assertIs(self.auto(), True)                       # on by itself, two accounts
        rows = engine.listing()['rows']
        tick, real = [], self.host.ensure

        def ensure(now=None):                                  # 'auto' is written off: the page's tick runs now
            if not tick:
                tick.append(in_thread(server.keep_tick, {'rows': rows}, now=1060.0))
                tick[0].join(1.0)
            return real(now)
        self.host.ensure = ensure
        server.act('/api/auto', {'enabled': False})
        tick[0].join(10)
        self.assertIs(self.auto(), False)
        self.assertIs(core.settings().get('auto_set_by_you'), True)
        self.look(1120.0)
        self.assertIs(self.auto(), False)

    def test_off_while_the_tick_turns_it_on_by_itself_stays_off(self):
        self.fake.hold(A)
        self.fake.hold(B)
        self.signed_in(A)
        person, real = [], self.fake.set_auto

        def set_auto(**kw):                                    # the tick turns it on: the person's off comes now
            if kw.get('enabled') is True and not person:
                person.append(in_thread(server.act, '/api/auto', {'enabled': False}))
                person[0].join(1.0)
            return real(**kw)
        with mock.patch.object(api, 'set_auto', set_auto):
            self.look(1000.0)
        person[0].join(10)
        self.assertIs(self.auto(), False)
        self.look(1060.0)
        self.assertIs(self.auto(), False)

    def test_a_change_that_fails_part_way_is_still_yours(self):
        self.fake.hold(A)
        self.fake.hold(B)
        self.signed_in(A)
        self.look(1000.0)
        real = self.host.ensure

        def ensure(now=None):                                  # 'auto' is written, then starting or stopping fails
            raise RuntimeError('made-up')
        self.host.ensure = ensure
        with self.assertRaises(RuntimeError):
            server.act('/api/auto', {'enabled': False})
        self.host.ensure = real
        self.assertIs(core.settings().get('auto_set_by_you'), True)
        self.look(1060.0)
        self.assertIs(self.auto(), False)

    def test_switchboard_auto_off_with_the_page_down_marks_it_first_too(self):
        self.fake.hold(A)
        self.fake.hold(B)
        self.signed_in(A)
        self.look(1000.0)
        order = []
        real_mark, real_set = keep.set_by_you, engine.set_auto
        with mock.patch.object(keep, 'set_by_you', lambda: order.append('mark') or real_mark()), \
                mock.patch.object(engine, 'set_auto', lambda **kw: order.append('change') or real_set(**kw)):
            code, out = self.run_cli('auto', 'off')
        self.assertEqual(code, 0)
        self.assertEqual(order, ['mark', 'change'])
        self.assertIs(self.auto(), False)


# ---------- let go is by account (email and organization) ----------

class LetGoIsPerAccount(KeepSandbox):
    def setUp(self):
        super().setUp()
        core.set_setting('auto_set_by_you', True)              # these tests are about keeping

    def signed_in_to(self, email, org):
        with open(core.claude_json(), 'w') as f:
            json.dump({'oauthAccount': {'emailAddress': email, 'organizationUuid': org}}, f)

    def remove_on_the_page(self, email):
        self.assertEqual(server.act('/api/remove', {'email': email})['state'], 'confirm')
        server.act('/api/remove', {'email': email, 'confirm': True})

    def kept_twice_over(self, t):
        """Two looks, far enough apart to keep a new login. Returns how many logins the engine was asked to keep."""
        self.fake.calls[:] = []
        self.look(t)
        self.look(t + 6)
        return self.adds()

    def test_removing_one_organization_on_the_page_lets_go_only_that_one(self):
        self.fake.hold(A)
        self.signed_in(A)
        self.look(900.0)
        self.remove_on_the_page(A)
        self.signed_in_to(A, TEAM)                             # the same email in another organization
        self.assertEqual(self.kept_twice_over(1000.0), 1)
        self.signed_in(A)                                      # the one removed: never by itself
        self.assertEqual(self.kept_twice_over(1100.0), 0)
        self.assertEqual(core.settings()['let_go'], [[A, ORG[A]]])

    def test_switchboard_remove_lets_go_only_that_account(self):
        self.fake.hold(A)
        self.signed_in(A)
        self.assertEqual(self.run_cli('remove', A, '--yes')[0], 0)
        self.signed_in_to(A, TEAM)
        self.assertEqual(self.kept_twice_over(1000.0), 1)

    def test_removing_everything_lets_go_each_account_not_each_email(self):
        self.fake.hold(A)
        self.signed_in(A)
        self.look(900.0)
        server.act('/api/purge', {'typed': 'remove everything'})
        self.signed_in_to(A, TEAM)
        self.assertEqual(self.kept_twice_over(1000.0), 1)

    def test_adding_one_organization_on_purpose_leaves_the_other_let_go(self):
        self.fake.hold(A)
        self.signed_in(A)
        self.look(900.0)
        self.remove_on_the_page(A)
        self.signed_in_to(A, TEAM)
        code, out = self.run_cli('add')                        # this one, on purpose
        self.assertEqual(code, 0)
        self.signed_in(A)                                      # back on the one removed, never added again
        self.assertEqual(self.kept_twice_over(1000.0), 0)

    def test_an_email_let_go_without_its_organization_covers_every_organization(self):
        core.set_setting('let_go', [A])
        self.signed_in_to(A, TEAM)
        self.assertEqual(self.kept_twice_over(1000.0), 0)
        self.assertTrue(keep.is_let_go((A, ORG[A])))
        keep.forget((A, TEAM))                                 # an add on purpose: its email goes from the list
        self.assertEqual(core.settings()['let_go'], [])


# ---------- a login is kept once, whoever keeps it ----------

class KeptOnce(KeepSandbox):
    def setUp(self):
        super().setUp()
        core.set_setting('auto_set_by_you', True)

    def test_a_page_add_since_the_rows_were_read_is_not_kept_again(self):
        self.fake.hold(A)
        self.signed_in(B)
        self.look(1000.0)                                      # B seen once
        looked = server.tick()                                 # the next tick reads the rows: B not held yet
        server.act('/api/engine', {'add': True})               # the page's Add keeps B meanwhile
        server.keep_tick(looked, now=1006.0)                   # the look, on the rows read before the Add
        self.assertEqual(self.kept_events(), ['Kept maya@ivy.example · 2 accounts'])
        self.assertEqual(self.notes(), [])

    def test_a_page_add_while_the_look_keeps_it_waits_for_it(self):
        self.fake.hold(A)
        self.signed_in(B)
        self.look(1000.0)
        page, real = [], engine.add_current

        def add_current():                                     # the look is keeping B: the page's Add comes now
            if not page:
                page.append(in_thread(server.act, '/api/engine', {'add': True}))
                page[0].join(1.0)
            return real()
        with mock.patch.object(engine, 'add_current', add_current):
            self.look(1006.0)
        page[0].join(10)
        self.assertEqual(self.kept_events(), ['Kept maya@ivy.example · 2 accounts'])


# ---------- settings.json written by two processes at once ----------

class SettingsAcrossProcesses(KeepSandbox):
    def test_a_terminal_and_the_page_writing_at_once_both_stay(self):
        # The page (this process) has read settings.json and is about to write it back when a command in a terminal
        # (another process) writes another setting.
        code = ('import sys; sys.path.insert(0, sys.argv[1]); from switchboard import core; print("ready", flush=True); '
                'core.set_setting("let_go", [["ana@harbor.example", ""]])')
        child, real = [], core.write_json

        def write_json(path, data, mode=0o600):
            if path == core.data_path('settings.json') and not child:
                p = subprocess.Popen([sys.executable, '-I', '-B', '-c', code, ROOT], stdout=subprocess.PIPE,
                                     env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                child.append(p)
                self.assertEqual(p.stdout.readline().strip(), b'ready')
                try:
                    p.wait(timeout=2)                          # before: nothing held it back, so it wrote first
                except subprocess.TimeoutExpired:
                    pass                                       # now: it waits for this write
            return real(path, data, mode)
        with mock.patch.object(core, 'write_json', write_json):
            core.set_setting('auto_on_said', 123.0)
        self.assertEqual(child[0].wait(timeout=30), 0)
        child[0].stdout.close()
        s = core.settings()
        self.assertEqual((s.get('auto_on_said'), s.get('let_go')), (123.0, [['ana@harbor.example', '']]))

    def test_the_bookmark_key_is_made_once(self):
        k = core.key()
        self.assertTrue(k)
        self.assertEqual(core.key(), k)
        self.assertEqual(core.settings()['key'], k)


# ---------- what the history keeps ----------

# Made up, and on purpose not in any provider's exact shape (length, checksum), so no secret scanner takes them for
# real ones; each still has the shape the history masks.
SECRETS = (
    'eyJhbGciOi.eyJzdWIiOiIx.SflKxwRJSMeKKF2QT4',                      # a JWT with short parts
    'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJtYXlhIn0.mAdEuPsIgNaTuRe_0123456789-abcdefghijklmnopq',
    'TWFkZVVwS2V5Rm9y/VGVzdHMwMTIzNDU2/Tm90UmVhbA0123',                # base64 with /
    'AbCdEfGhIjKlMnOpQrSt+UvWxYz0123456789/abcdEFGH==',               # base64 with + / and padding
    'sk-proj-abc123def456ghi789',                                      # sk- of any length
    'sk_made_up_51H8abc',
    'key-3ax6xnjp29jd',
    'gh' 'p_abcdefghijklmnopqrst0123',
    'gi' 'thub_pat_11ABCDEFG0abcdefghijkl_mnopq',
    'glpat-abcd1234efgh',
    'xo' 'xb-123456789012-abcdefghijkl',
    'AK' 'IAMADEUP7KEY',
    'AI' 'zaSyD-1bcdefghij0123',
    'hf_abcdefghij1234567890',
    'npm_abcdefghij1234567890ABCDEFGHIJ',
)
SAID_AS_IT_IS = (
    'Kept maya@ivy.example · 2 accounts',
    'maya@ivy.example reached its 5-hour switch line: 91%',
    'Mapped /Users/maya/Documents/GitHub/northwind-platform-v2/services.',
    'Adds the House rules block (12 lines) at the top of /Users/maya/.claude/CLAUDE.md.',
    'Added the key console (…9f2c)',
    'Learned that "Q4 pricing review" opens as maya@northwind.example',
    'https://claude.ai/artifact/0f1e2d3c4b5a69788796a5b4c3d2e1f0',
    'organization 8c1b2f3e-1234-4cde-9abc-0123456789ab',
    'Engine 1.1.0, history.jsonl.1, claude-mem on, key-value, risk-free, sk-learn',
)


def history_module():
    from switchboard import history
    return history


from test_history import Sandbox as HistorySandbox


class WhatTheHistoryKeeps(HistorySandbox):
    def test_every_shape_of_key_or_token_is_masked(self):
        h = history_module()
        for secret in SECRETS:
            with self.subTest(secret=secret):
                with core.locked() as st:
                    core.event(st, 'The engine said: %s refused' % secret, 'warn')
                line = h.read(1)[0]
                self.assertRegex(line['text'], r'^The engine said: [A-Za-z_-]{0,11}… refused$')
                self.assertNotIn(secret[-10:], json.dumps(line, ensure_ascii=False))

    def test_switchboards_own_words_are_kept_as_they_are(self):
        h = history_module()
        for text in SAID_AS_IT_IS:
            with self.subTest(text=text):
                self.assertEqual(h.clean(text), text)

    def test_an_artifacts_title_is_cut_to_60_characters(self):
        from switchboard import artifacts, browser
        self.patch(browser, 'profiles', lambda: {'Profile 1': 'Work'})
        with core.locked() as st:
            core.ensure_account(st, 'maya@northwind.example')
        title = 'Q4 pricing review: ' + ', '.join(['every region, every plan, every discount we gave'] * 3)
        artifacts.learn_ok('0f1e2d3c4b5a69788796a5b4c3d2e1f0', 'Profile 1', 'maya@northwind.example', title, [])
        said = core.load()['events'][-1]['text']
        self.assertEqual(said, 'Learned that "%s…" opens as maya@northwind.example' % title[:59])
        kept = history_module().read(1)[0]
        self.assertEqual((kept['text'], kept['reason']), (said, 'opens "%s…"' % title[:59]))
        self.assertEqual(core.load()['artifacts'][0]['title'], title)      # the folders still show all of it


# ---------- switchboard log -f misses nothing ----------

class FollowMissesNothing(HistorySandbox):
    def follow_with(self, before_following):
        """`switchboard log -f --json`, where `before_following` runs after the last lines were read and before the
        follow looks at the file: one pass of the follow, then it stops. Returns the text of each line printed."""
        h = history_module()
        real, stop = h.follow, threading.Event()

        def follow(write, **kw):
            before_following()
            kw.update(stop=stop, every=0, sleep=lambda s: stop.set())
            return real(write, **kw)
        self.patch(h, 'follow', follow)
        out = io.StringIO()
        self.assertEqual(h.main(['-f', '--json'], out), 0)
        return [json.loads(line)['text'] for line in out.getvalue().splitlines()]

    def say(self, text):
        history_module().record('setting', text, by='you')

    def test_a_line_written_between_the_read_and_the_follow_is_printed(self):
        self.say('Notifications: on')
        self.say('Notifications: off')
        got = self.follow_with(lambda: self.say('Keep the Mac awake: on'))
        self.assertEqual(got, ['Notifications: on', 'Notifications: off', 'Keep the Mac awake: on'])

    def test_a_line_still_being_written_when_read_is_printed_once_it_is_whole(self):
        h = history_module()
        self.say('Notifications: on')
        line = json.dumps({'t': 1.0e9, 'kind': 'setting', 'text': 'Notifications: off', 'by': 'you'}).encode()
        with open(h.path(), 'ab') as f:
            f.write(line[:20])                                  # half of it, as a writer is part-way through

        def finish():
            with open(h.path(), 'ab') as f:
                f.write(line[20:] + b'\n')
        self.assertEqual(self.follow_with(finish), ['Notifications: on', 'Notifications: off'])

    def test_a_rotation_between_the_read_and_the_follow_loses_nothing(self):
        h = history_module()
        self.patch(h, 'MAX', 600)
        self.say('Notifications: on')

        def busy():
            for i in range(8):
                self.say('line %d' % i)
        got = self.follow_with(busy)
        self.assertTrue(os.path.exists(h.path() + '.1'))
        self.assertEqual(got, ['Notifications: on'] + ['line %d' % i for i in range(8)])


# ---------- Chrome setup says Signed in when it was shown, and only then ----------

from test_history import L, N, row

PAGE_ID = '0f1e2d3c4b5a69788796a5b4c3d2e1f0'


class ChromeSetupSaysWhatWasShown(HistorySandbox):
    ORG = 'org-northwind-0001-2222'

    def setUp(self):
        super().setUp()
        from switchboard import browser
        self.patch(browser, 'HINTS', {'at': 0, 'key': None, 'value': {}})
        self.opened = []
        self.patch(browser, 'open_url', lambda profile, url, background=False: self.opened.append((profile, url)))

    def chrome(self, profiles):
        """Google Chrome in the throwaway home: its app, and its profiles in Local State (test_history's helper)."""
        from switchboard import browser
        d = os.path.join(browser.support(), browser.BROWSERS['chrome']['dir'])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'Local State'), 'w') as f:
            json.dump({'profile': {'info_cache': dict((p, {'name': n}) for p, n in profiles.items())}}, f)
        for p in profiles:
            os.makedirs(os.path.join(d, p, 'Extensions'), exist_ok=True)
        os.makedirs(os.path.join(self.home, 'Applications', 'Google Chrome.app'), exist_ok=True)
        browser.APPS.clear()
        self.addCleanup(browser.APPS.clear)
        return d

    def signed_in(self, email):
        return dict((a['email'], a['chrome']) for a in server.view()['accounts'])[email]['signed_in']

    def page_loaded(self, profile, email):
        from switchboard import artifacts
        artifacts.learn_ok(PAGE_ID, profile, email, 'Lab notes', [])

    def test_a_profile_you_picked_says_signed_in_once_a_page_of_its_loaded_there(self):
        self.chrome({'Profile 1': 'Work', 'Claude Ivy': 'Claude Ivy'})
        self.rows = [row(1, N, True), row(2, L, False)]
        server.tick()
        server.act('/api/profile', {'email': L, 'profile': 'Claude Ivy'})      # picked on the page
        self.assertIsNone(self.signed_in(L))                                   # not shown yet
        self.page_loaded('Claude Ivy', L)                                      # a page of L's loaded there (Check)
        self.assertIs(self.signed_in(L), True)
        self.assertEqual(core.load()['accounts'][L]['profile_src'], 'you')    # still the person's own choice

    def test_a_new_profile_switchboard_made_says_it_too(self):
        d = self.chrome({'Profile 1': 'Work'})
        self.rows = [row(1, N, True)]
        server.tick()
        made = server.act('/api/profile', {'email': N, 'profile': '+new'})['profile_name']
        self.assertEqual(self.opened, [(made, 'https://claude.ai/login')])
        os.makedirs(os.path.join(d, made))                                     # Chrome made it; signed in there
        self.assertIsNone(self.signed_in(N))
        self.page_loaded(made, N)
        self.assertIs(self.signed_in(N), True)

    def test_a_profile_that_changed_hands_says_signed_in_only_once_shown_again(self):
        self.chrome({'Profile 1': 'Work', 'Claude Ivy': 'Claude Ivy'})
        self.rows = [row(1, N, True), row(2, L, False)]
        server.tick()
        server.act('/api/profile', {'email': L, 'profile': 'Claude Ivy'})
        self.page_loaded('Claude Ivy', L)
        server.act('/api/profile', {'email': N, 'profile': 'Claude Ivy'})      # moved to another account
        self.assertIsNone(self.signed_in(N))
        server.act('/api/profile', {'email': L, 'profile': 'Claude Ivy'})      # and back
        self.assertIsNone(self.signed_in(L))
        self.page_loaded('Claude Ivy', L)
        self.assertIs(self.signed_in(L), True)
        from switchboard import artifacts
        with core.locked() as st:                                              # then seen signed out there
            artifacts.mark_signed_out(st, [('Claude Ivy', L)])
        self.assertIs(self.signed_in(L), False)

    def test_a_profile_matched_from_its_claude_ai_data_is_a_guess_until_a_page_loads(self):
        d = self.chrome({'Profile 1': 'Work'})
        store = os.path.join(d, 'Profile 1', 'Local Storage', 'leveldb')
        os.makedirs(store)
        with open(os.path.join(store, '000003.log'), 'wb') as f:              # what claude.ai left there once
            f.write(b'_https://claude.ai\x00\x01sidebar-open:' + self.ORG.encode())
        self.assertFalse(os.path.exists(os.path.join(d, 'Profile 1', 'Cookies')))     # and no sign-in at all
        self.rows = [row(1, N, True, org_id=self.ORG)]
        server.tick()
        a = core.load()['accounts'][N]
        self.assertEqual((a['profile'], a['profile_src']), ('Profile 1', 'hint'))
        self.assertIsNone(self.signed_in(N))
        self.page_loaded('Profile 1', N)
        self.assertIs(self.signed_in(N), True)


# ---------- the welcome's first name ----------

class WelcomeName(HistorySandbox):
    def test_the_first_name_whatever_the_full_name_looks_like(self):
        import collections
        import pwd
        real = pwd.getpwuid(os.getuid())
        for gecos, name in (('Chen, Maya', 'Maya'), ('Chen,Maya', 'Maya'), ('Dr. Maya Chen', 'Maya'),
                            ('Prof. Dr. Maya Chen', 'Maya'), ('Chen, Dr. Maya', 'Maya'), ('Maya Chen, PhD', 'Maya'),
                            ('King, Jr.', 'King'), ('​Maya Chen', 'Maya'), ('‮Maya Chen', 'Maya'),
                            ('Ma​ya⁦ Chen', 'Maya'), ('﻿Maya', 'Maya'), ('​‮', None),
                            ('Dr.', None),
                            # plain names and the usual GECOS shapes
                            ('Maya Reyes', 'Maya'), ('Maya Reyes,Room 4,555-0100,', 'Maya'),
                            ('  Jean-Luc  Picard', 'Jean-Luc'), ('', None), (',,,', None), ('Ma\x1bya\x07 R', 'Maya')):
            with self.subTest(gecos=gecos):
                fake = collections.namedtuple('P', 'pw_name pw_gecos pw_dir pw_uid')('maya', gecos, real.pw_dir,
                                                                                    real.pw_uid)
                self.patch(server, 'getpwuid', lambda uid, fake=fake: fake)
                server.FIRST_NAME.clear()
                self.assertEqual(server.first_name(), name)


# ---------- the page offers Open only for a link the server opens ----------

import test_app  # its headless-Chrome helper

PASTED = (
    ('https://claude.ai/artifact/' + PAGE_ID, 'https://claude.ai/artifact/' + PAGE_ID),
    ('https://claude.ai/code/artifact/' + PAGE_ID, 'https://claude.ai/code/artifact/' + PAGE_ID),
    ('see https://claude.ai/artifact/%s for the numbers' % PAGE_ID, 'https://claude.ai/artifact/' + PAGE_ID),
    ('https://evil.example/claude.ai/artifact/' + PAGE_ID, None),        # a look-alike
    ('https://claude.ai.evil.example/artifact/' + PAGE_ID, None),
    ('HTTPS://CLAUDE.AI/artifact/' + PAGE_ID, None),                     # the server's test is exact
    ('https://claude.ai/artifact/short', None),
    ('claude.ai/artifact/' + PAGE_ID, None),
)


class PastedLinkRule(unittest.TestCase):
    def test_each_case_is_what_the_server_does(self):
        from switchboard import artifacts
        for text, want in PASTED:
            with self.subTest(text=text):
                url = want or text
                self.assertEqual(url.startswith('https://claude.ai/') and bool(artifacts.art_id(url)), want is not None)


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class PastedLinkInThePage(test_app.ChromePage):
    def test_open_is_offered_only_for_a_link_the_server_opens(self):
        got = self.run_page("""
      await wait(1500);
      const f = $('#files-find'), out = [];
      const type = async (v) => { f.value = v; f.dispatchEvent(new Event('input', {bubbles: true})); await wait(250); };
      for (const v of %s) {
        await type(v);
        const b = $('#files-list [data-k="open:pasted"]');
        out.push(b ? b.dataset.url : null);
      }
      done(out);
""" % json.dumps([text for text, _ in PASTED]))
        self.assertEqual(got, [want for _, want in PASTED])


# ---------- another program on the page's port is a page that is not running ----------

import socketserver

NOT_HTTP = b'SSH-2.0-OpenSSH_9.6\r\n'
CUT_OFF = b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n{"accounts": [{"email": '
NOT_JSON = b'HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello'


class Answers(socketserver.ThreadingTCPServer):
    """A program on 127.0.0.1 that answers every connection with the same bytes, then hangs up."""
    daemon_threads = True

    def __init__(self, said):
        self.said = said
        socketserver.ThreadingTCPServer.__init__(self, ('127.0.0.1', 0), AnswersHandler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


class AnswersHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        try:
            self.request.recv(65536)
        except OSError:
            pass
        self.request.sendall(self.server.said)


class AnotherProgramOnThePort(KeepSandbox):
    def answering(self, said):
        program = Answers(said)
        self.addCleanup(program.server_close)
        self.addCleanup(program.shutdown)
        os.environ['SWITCHBOARD_PORT'] = str(program.server_address[1])

    def test_the_helpers_say_the_page_is_not_running(self):
        for name, said in (('not HTTP', NOT_HTTP), ('cut off', CUT_OFF), ('not JSON', NOT_JSON)):
            with self.subTest(name):
                self.answering(said)
                self.assertFalse(cli.page_running())
                self.assertIsNone(cli.page_state())
                with self.assertRaises(OSError):
                    cli.ask_page('/api/auto', {'enabled': True}, timeout=5)

    def test_page_status_switch_and_auto_end_without_a_traceback(self):
        self.fake.hold(A)
        self.fake.hold(B)
        self.signed_in(A)
        self.answering(NOT_HTTP)
        code, out = self.run_cli('page')                        # it did nothing, so it exits 1
        self.assertEqual((code, out), (1, 'The page is not running. Start it with `switchboard serve`, or run the '
                                          'installer again.\n'))
        code, out = self.run_cli('status')
        self.assertEqual(code, 0)
        self.assertIn('maya@ivy.example', out)
        code, out = self.run_cli('2')                           # the switch is made here, as with the page down
        self.assertEqual(code, 0)
        self.assertEqual([c[2:4] for c in self.fake.calls if c[0] == 'switch'], [('manual', 'cli')])
        code, out = self.run_cli('auto', 'on')
        self.assertEqual(code, 0)
        self.assertIn(cli.NOT_RUNNING_HERE, out)
        self.assertEqual((self.fake.auto.get('enabled'), core.settings().get('auto_set_by_you')), (True, True))
        code, out = self.run_cli('doctor')                      # it asks whether the page runs, auto-switch on
        self.assertIn(' --  Page at http://localhost:%s\n' % os.environ['SWITCHBOARD_PORT'], out)
        self.assertIn('It runs inside the page: start it with `switchboard serve`.', out)


# ---------- the link app asks the page directly, on the page's own port ----------

class LinkAppAsksThePageDirectly(unittest.TestCase):
    """The link app is Swift, and the suite never builds it (router/build.sh --no-register builds it in a throwaway
    folder). Its source is read here."""

    def source(self):
        with open(os.path.join(ROOT, 'router', 'SwitchboardLinks.swift'), encoding='utf-8') as f:
            return f.read()

    def test_never_through_a_proxy(self):
        src = self.source()
        self.assertFalse('URLSession.shared' in src, 'URLSession.shared follows the system proxy')
        self.assertIn('let config = URLSessionConfiguration.ephemeral\n    config.connectionProxyDictionary = [:]', src)
        self.assertIn('direct.dataTask(with: request)', src)

    def test_the_pages_port_as_switchboard_finds_it(self):
        src = self.source()
        body = src[src.index('func pagePort() -> Int {'):src.index('func pageURL() -> URL {')]
        where = [body.index(x) for x in ('environment["SWITCHBOARD_PORT"]', 'setting("port")', 'settingsPort]',
                                         'return 7799')]
        self.assertEqual(where, sorted(where))                 # SWITCHBOARD_PORT, the page's own, settings.json, 7799
        self.assertIn('dataDir + "/settings.json"', body)
        self.assertIn('let dataDir = (environment["SWITCHBOARD_HOME"]', src)    # the data folder core.data_dir finds
        self.assertIn('var request = URLRequest(url: pageURL(), timeoutInterval: 2.5)', src)


if __name__ == '__main__':
    unittest.main()
