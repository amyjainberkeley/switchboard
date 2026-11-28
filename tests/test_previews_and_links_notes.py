import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# Smaller rules of the previews, the link notes and the model weeks, one section each. Made-up *.example accounts and
# apps only.
import json
import os
import plistlib
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

import test_keep  # its sandbox: a throwaway home, the account engine's fake, the command line
from fakes_links import FakeChrome, Home
from fakes_artifacts import A, B, G, ID1, ID2, LINK, TINY_JPEG, publish
from test_app import CHROME, ChromePage
from test_server_robust import Sandbox
from test_previews_drawing import PreviewCase
from test_page_layout import BANNER, HOLDERS, LAB, NORTHWIND, PICTURE, STATE_EXTRA, own, stand_in_previews

from switchboard import (artifacts, browser, connect, core, demo, history, linkapp, menubar, previews,
                         server, session_hooks, term)


# ---------- a page file used again for another artifact never gives the older one a picture of the newer ----------

class AReusedPageFile(PreviewCase):
    """A publish names its local file; a session often writes the same file again for another page. The file then
    holds what the newer publish sent, so the older artifact keeps its title tile."""

    def test_an_older_artifact_is_not_drawn_from_a_file_written_again_for_a_newer_one(self):
        src = self.page('scratch/index.html', '<h1>The newer page</h1>')
        os.utime(src, (self.now - 5, self.now - 5))                     # written again for B, 5 s ago
        self.listed((ID1, src, self.now - 3600), (ID2, src, self.now - 4))    # A an hour ago, B now: one file
        self.assertEqual([x[0] for x in previews.todo(core.load(), self.now)], [ID2])
        self.assertEqual(previews.run_once()['made'], 1)
        self.assertEqual(previews.known(), {ID2})                       # A: its title, never B's picture

    def test_a_file_published_again_unchanged_is_drawn_for_each(self):
        src = self.page('scratch/index.html')
        os.utime(src, (self.now - 7200, self.now - 7200))               # written before both publishes
        self.listed((ID1, src, self.now - 3600), (ID2, src, self.now - 60))
        self.assertEqual(sorted(x[0] for x in previews.todo(core.load(), self.now)), sorted([ID1, ID2]))

    def test_a_page_changed_after_its_publish_waits_for_its_next_publish(self):
        src = self.page('plan.html')
        self.listed((ID1, src, self.now - 600))
        os.utime(src, (self.now - 60, self.now - 60))                   # edited after it was published
        self.assertEqual(previews.todo(core.load(), self.now), [])
        with core.locked() as st:
            st['artifacts'][0]['ft'] = self.now - 30                    # published again: what it holds now is the page
        self.assertEqual([x[0] for x in previews.todo(core.load(), self.now)], [ID1])


# ---------- slow draws on a busy Mac never end a page's picture for good ----------

class SlowDrawsOnABusyMac(PreviewCase):
    """Chrome runs at background priority, so on a busy Mac it can pass its deadline on any page. That is the Mac's
    doing, not the page's: such a draw is tried again an hour later and does not count toward TRIES. Failures that do
    count (a quiet Mac) wait a day after the third, not until the page is published again."""

    def test_draws_cut_off_on_a_busy_mac_come_once_it_is_quiet(self):
        own = previews.DEADLINE                                         # the product's own deadline, for the last draw
        self.patch(previews, 'DEADLINE', 1.0)
        self.chrome.mode('hang')
        self.listed((ID1, self.page(), self.now - 30))
        self.patch(browser, 'LOAD', lambda: 2.5)                        # busy, not crawling: the draws start
        t = self.now
        for _ in range(previews.TRIES):
            self.assertEqual(previews.run_once(now=t), {'made': 0, 'failed': 1, 'gone': 0})
            self.assertEqual(previews.run_once(now=t + 60)['failed'], 0)     # an hour's wait after each
            t += previews.RETRY_AFTER + 1
        self.chrome.mode('jpeg')
        self.patch(browser, 'LOAD', lambda: 0.3)                        # the afternoon is over
        # The last draw must come. On a Mac busy with other work the stand-in itself can take over a second to start,
        # past the short deadline that cut the draws above off on purpose, so it gets the product's own deadline back.
        self.patch(previews, 'DEADLINE', own)
        self.assertEqual(previews.run_once(now=t)['made'], 1)
        self.assertEqual(previews.known(), {ID1})

    def test_a_page_that_fails_three_times_on_a_quiet_mac_is_tried_again_a_day_later(self):
        self.chrome.mode('fail')
        self.listed((ID1, self.page(), self.now - 30))
        t = self.now
        for _ in range(previews.TRIES):
            self.assertEqual(previews.run_once(now=t)['failed'], 1)
            t += previews.RETRY_AFTER + 1
        self.chrome.mode('jpeg')
        self.assertEqual(previews.run_once(now=t)['made'], 0)           # three: not again the same day
        self.assertEqual(previews.run_once(now=t - previews.RETRY_AFTER - 1 + 86400 + 1)['made'], 1)


# ---------- a picture made while the page is open shows on it ----------

class TheStateSaysWhichHaveAPicture(Sandbox):
    """The page reads the folders again when /api/state's artifacts change. A picture comes seconds after its publish
    (and the first backfill's over minutes), so each artifact row says whether it has one."""

    def test_each_artifact_row_says_whether_it_has_a_picture(self):
        st = core.blank_state()
        for aid in (ID1, ID2):
            st['artifacts'].append(artifacts.blank_item(aid, time.time() - 600))
        core.save(st)
        self.assertEqual(dict((x['id'], x.get('preview')) for x in server.view()['artifacts']), {ID1: False, ID2: False})
        previews.keep(ID1, TINY_JPEG)
        self.assertEqual(dict((x['id'], x.get('preview')) for x in server.view()['artifacts']), {ID1: True, ID2: False})


# The page as it is, with /api/state's and /api/artifacts' word on one artifact's picture stood in for: none, then one.
# The demo moves every made-up time with the clock, which reads the folders again at each look; here the times hold
# still, as a real Mac's do between two publishes.
PICTURE_COMES = r"""
      let pic = false;
      const ID = %s, base = window.fetch, T = {};
      const said = (x) => { if (x && x.id === ID) x.preview = pic; };
      const still = (x) => { x.t = T[x.id] || (T[x.id] = x.t); said(x); };
      const json = (v) => new Response(JSON.stringify(v), {status: 200, headers: {'Content-Type': 'application/json'}});
      window.fetch = function (u, o) {
        const s = String(u);
        if (s === '/api/state') return base('/api/state').then((r) => r.json()).then((st) => { (st.artifacts || []).forEach(still); return json(st); });
        if (s === '/api/artifacts') return base('/api/artifacts').then((r) => r.json()).then((d) => { for (const f of d.folders) f.files.forEach(said); return json(d); });
        return base.apply(this, arguments);
      };
      document.dispatchEvent(new Event('visibilitychange')); await wait(1500);       // the times hold still from here
      location.hash = '#artifacts/work'; await wait(1200);
      const img = () => $('#files-list a.tile[data-id="' + ID + '"] img');
      const before = !!img();
      pic = true;                                             // its picture is made: the next look at the state says so
      document.dispatchEvent(new Event('visibilitychange')); await wait(2500);
      done({before, after: img() ? img().getAttribute('src') : null});
"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class APictureShowsOnAnOpenPage(ChromePage):
    def test_the_tile_gets_its_picture_without_a_reload(self):
        demo._ready()
        aid = own(NORTHWIND)[0]['id']
        stand_in_previews(self, set(), {aid: PICTURE})
        got = self.run_page(PICTURE_COMES % json.dumps(aid), budget=12000)
        self.assertEqual(got, {'before': False, 'after': '/api/preview/' + aid})


# ---------- a link passed on while no one knew who held the seat counts for no app ----------

ROUTER = {'name': 'Link Router', 'kind': 'handler', 'id': 'com.example.linkrouter', 'path': '/Applications/Link Router.app'}
OTHER = {'name': 'Other', 'kind': 'handler', 'id': 'com.example.other', 'path': '/Applications/Other.app'}


class Forwards(unittest.TestCase):
    """The page's links line says an app sends clicked links to the page only for the app seen doing it."""

    def setUp(self):
        for target, values in ((server.FORWARDED, {'at': None, 'by': None, 'read': False, 'saved': None, 'saved_by': None}),
                               (server.LINKS, {'holder': None})):
            p = mock.patch.dict(target, values)
            p.start()
            self.addCleanup(p.stop)
        core.set_setting(server.FORWARDED_KEY, None)
        self.addCleanup(core.set_setting, server.FORWARDED_KEY, None)


class AForwardNeedsAKnownHolder(Forwards):
    def test_a_link_passed_on_before_the_first_look_counts_for_no_app(self):
        server.note_forwarded(now=1000.0)                   # the page has just started: its first look is under way
        self.assertFalse(server.passes_links(OTHER))        # another app that takes the seat later inherits nothing
        self.assertFalse(server.passes_links(ROUTER))
        self.assertIsNone(core.settings().get(server.FORWARDED_KEY))
        server.LINKS['holder'] = dict(ROUTER)               # the look is done: the next click is noted for its app
        server.note_forwarded(now=1010.0)
        self.assertTrue(server.passes_links(ROUTER))
        self.assertFalse(server.passes_links(OTHER))
        self.assertEqual(core.settings()[server.FORWARDED_KEY], {'at': 1010, 'by': ROUTER['id']})

    def test_switchboard_links_or_a_browser_in_the_seat_notes_nothing(self):
        for holder in ({'name': 'Switchboard Links', 'kind': 'links', 'id': linkapp.BUNDLE_ID, 'path': '/x'},
                       {'name': 'Safari', 'kind': 'browser', 'id': 'com.apple.Safari', 'path': '/y'},
                       dict(ROUTER, id=None)):
            with self.subTest(holder=holder['name'], id=holder['id']):
                server.LINKS['holder'] = holder
                server.note_forwarded(now=1000.0)
                self.assertIsNone(server.forwarded())

    def test_a_note_that_names_no_app_counts_for_none(self):
        core.set_setting(server.FORWARDED_KEY, {'at': 1000, 'by': None})     # what the build before this kept
        self.assertFalse(server.passes_links(OTHER))
        self.assertFalse(server.passes_links(ROUTER))


# ---------- the note under the Bookmarklet about other apps that take clicked links ----------

FALSE_WORDS = ("don't open as their owner", 'don’t open as their owner', 'do not open as their owner',
               'no longer open as their owner')


def handler(name, bid, default=False, folder='~/Applications'):
    return {'name': name, 'path': os.path.join(os.path.expanduser(folder), name + '.app'), 'id': bid,
            'default': default}


class TheOtherAppsNote(unittest.TestCase):
    """Settings › Links, under the Bookmarklet: other apps that also take clicked links. The links line above it says
    what the app in the seat does, so the note never names that app, never says links don't open as their owner, and
    never tells you to remove an app that sends them to the page. With Switchboard Links the default: nothing."""

    def test_a_link_router_in_the_seat_and_nothing_else(self):
        found = [handler('Link Router', ROUTER['id'], default=True)]
        self.assertIsNone(linkapp.handlers_note(found, skip={ROUTER['id']}))

    def test_switchboard_links_the_default(self):
        found = [handler('Link Router', ROUTER['id']), handler('Other', OTHER['id'])]
        self.assertIsNone(linkapp.handlers_note(found, on=True))

    def test_an_app_seen_sending_links_here_is_never_one_to_remove(self):
        found = [handler('Link Router', ROUTER['id'])]                      # a browser has the seat now
        self.assertIsNone(linkapp.handlers_note(found, skip={ROUTER['id']}))

    def test_another_app_beside_a_link_router(self):
        found = [handler('Claude Accounts', 'com.example.accounts'), handler('Link Router', ROUTER['id'], default=True)]
        note = linkapp.handlers_note(found, skip={ROUTER['id']})
        self.assertEqual(note, 'Other apps here can also take clicked links: Claude Accounts (~/Applications/Claude '
                               'Accounts.app). Only one should; quit or remove the ones you no longer use.')
        for words in FALSE_WORDS:
            self.assertNotIn(words, note)


class TheNoteTheServerKeeps(Sandbox):
    """The background look gives the note what it needs: the seat's app, the app seen sending links here, and whether
    Switchboard Links has the seat."""

    def setUp(self):
        super().setUp()
        for target, values in ((server.FORWARDED, {'at': None, 'by': None, 'read': False, 'saved': None, 'saved_by': None}),
                               (server.LINKS, {'banner': None, 'handlers': None, 'holder': None})):
            p = mock.patch.dict(target, values)
            p.start()
            self.addCleanup(p.stop)
        self.apps = os.path.join(self.home, 'Applications')
        self.router = self.app('Link Router.app', ROUTER['id'], 'Link Router')
        self.links_app = self.app('Switchboard Links.app', linkapp.BUNDLE_ID, 'Switchboard Links')
        self.chrome = self.app('Google Chrome.app', 'com.google.Chrome', 'Google Chrome')
        real = linkapp.other_link_handlers
        self.patch(linkapp, 'other_link_handlers', lambda fresh=False, folders=None: real(folders=[self.apps]))

    def app(self, name, bid, display):
        path = os.path.join(self.apps, name)
        os.makedirs(os.path.join(path, 'Contents'))
        with open(os.path.join(path, 'Contents', 'Info.plist'), 'wb') as f:
            plistlib.dump({'CFBundleIdentifier': bid, 'CFBundleDisplayName': display,
                           'CFBundleURLTypes': [{'CFBundleURLSchemes': ['http', 'https']}]}, f)
        return path

    def look(self, holder):
        value = {'installed': True, 'on': holder == self.links_app,
                 'browser': os.path.splitext(os.path.basename(holder))[0]}
        self.patch(linkapp, 'status', lambda fresh=False: value)
        self.patch(linkapp, 'CACHE', {'at': time.time(), 'value': value, 'path': holder})
        with server.LINKS_LOCK:
            server.LINKS.update(loop=True, at=None, running=False)
        self.assertTrue(server.refresh_link_status(force=True))
        end = time.monotonic() + 10
        while server.LINKS['running'] and time.monotonic() < end:
            time.sleep(0.02)
        return server.link_extras()['handlers']

    def test_a_link_router_switchboard_links_and_chrome_in_the_seat(self):
        self.assertIsNone(self.look(self.router))           # a Link Router has the seat: the links line says so
        server.note_forwarded()                             # it sends a clicked link here
        self.assertIsNone(self.look(self.router))
        self.assertIsNone(self.look(self.links_app))        # Switchboard Links is the default: nothing to say
        self.assertIsNone(self.look(self.chrome))           # Chrome took it: a Link Router is one that sends links
        other = self.app('Claude Accounts.app', 'com.example.accounts', 'Claude Accounts')
        self.assertEqual(self.look(self.router), 'Other apps here can also take clicked links: Claude Accounts (%s). '
                         'Only one should; quit or remove the ones you no longer use.' % other.replace(self.home, '~'))


# ---------- `switchboard links` in a terminal says what the page says ----------

CHROME_SEAT = {'path': '/Applications/Google Chrome.app', 'id': 'com.google.Chrome', 'name': 'Google Chrome',
               'kind': 'browser'}
TO_DEFAULT = ' Make Switchboard Links the default with: switchboard links on\n'


class TheTerminalSaysWhatThePageSays(test_keep.Sandbox):
    """With an app other than a browser in the seat, `switchboard links` says what the page's links line says (the
    banner's words, linkapp.banner): a Link Router, which sends clicked links to the page, never reads "off"."""

    def setUp(self):
        super().setUp()
        self.holder = dict(ROUTER)
        for target, name, value in (
                (linkapp, 'status', lambda fresh=False: {'installed': True, 'on': False, 'browser': self.holder['name']}),
                (linkapp, 'seat', lambda fresh=False, look=True: {'holder': dict(self.holder), 'loop': False})):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.dict(server.FORWARDED, {'at': None, 'by': None, 'read': False, 'saved': None, 'saved_by': None})
        p.start()
        self.addCleanup(p.stop)

    def test_a_link_router_sends_links_to_the_page(self):
        core.set_setting(server.FORWARDED_KEY, {'at': round(time.time()) - 60, 'by': ROUTER['id']})    # the page saw it
        code, out = self.run_cli('links')
        self.assertEqual(code, 0)
        self.assertEqual(out, 'Link Router takes clicked links and sends them to Switchboard: they open as their owner '
                              'while the page runs. Switchboard Links also works when the page is closed.' + TO_DEFAULT)
        for words in FALSE_WORDS + (': off',):
            self.assertNotIn(words, out)

    def test_an_app_not_seen_sending_links_on(self):
        self.assertEqual(self.run_cli('links'), (0, 'Link Router takes clicked links now. They open as their owner only '
                                                    'if it sends them to Switchboard.' + TO_DEFAULT))

    def test_a_browser_in_the_seat_says_links_are_off(self):
        self.holder = dict(CHROME_SEAT)
        self.assertEqual(self.run_cli('links'), (0, 'Links open as their owner: off (Google Chrome is the default '
                                                    'browser). Turn it on with: switchboard links on\n'))


# ---------- a switch the page noticed late counts from when History says it happened ----------

class ASwitchNoticedLate(Home):
    """The page's timeline has each switch when the page noticed it: up to a pass of its loop late, more while the
    page was down. History has each switch the engine made at the moment it made it. A publish in between belongs to
    the account switched to; it must never get the old one as sure."""

    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.addCleanup(artifacts._PUB.update, seen=None)
        artifacts._PUB['seen'] = None

    def accounts(self, timeline):
        self.state(**{A: {}, B: {}, G: {}})
        with core.locked() as st:
            st['timeline'], st['active'], st['scan_v'] = timeline, timeline[-1][0], artifacts.SCAN_V

    def switched(self, to, t, frm=A):
        history.record('switch', 'Switched Claude Code to %s' % to, t=t, to=to, account=to, frm=frm, by='auto')

    def test_the_timeline_takes_the_time_history_has(self):
        sw = self.now - 1000
        self.accounts([[A, self.now - 7200], [B, sw + 200]])            # noticed 200 s late
        self.switched(B, sw)
        tl = artifacts.switch_timeline(core.load())
        self.assertEqual(tl, [[A, self.now - 7200], [B, sw]])
        self.assertEqual(core.owner_seg(dict(core.load(), timeline=tl), sw + 100), (B, True))

    def test_a_publish_in_between_gets_the_new_account_from_the_record_and_from_chat_history(self):
        sw = self.now - 1000
        self.accounts([[A, self.now - 7200], [B, sw + 200]])
        self.switched(B, sw)
        line = session_hooks.published_line(publish(ID1), sw + 100)
        session_hooks.note_published(line, sw + 100)
        artifacts.take_published_now()
        it = artifacts.find(core.load(), ID1)
        self.assertEqual((it['owner'], it['src']), (B, 'published'))
        proj = os.path.join(core.projects(), 'p')                      # the same kind of publish, in chat history
        os.makedirs(proj)
        when = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(sw + 100))
        with open(os.path.join(proj, 's.jsonl'), 'w') as f:
            f.write(json.dumps({'type': 'assistant', 'message': {'content': [
                {'type': 'tool_use', 'id': 't', 'name': 'Artifact'}]}}) + '\n')     # the call the answer belongs to
            f.write(json.dumps({'type': 'user', 'timestamp': when, 'cwd': '/x', 'message': {'content': [
                {'type': 'tool_result', 'tool_use_id': 't', 'content': 'Published /x/plan.html at %s' % (LINK % ID2)}]}})
                    + '\n')
        st = core.load()
        artifacts.scan(st)
        it = artifacts.find(st, ID2)
        self.assertEqual((it['owner'], it['src']), (B, 'published'))

    def test_switches_the_page_never_saw_between_two_it_did(self):
        self.accounts([[A, self.now - 7200], [B, self.now - 600]])
        self.switched(G, self.now - 3000)                               # to G and on to B, both before the page looked
        self.switched(B, self.now - 2000, frm=G)
        self.assertEqual(artifacts.switch_timeline(core.load()),
                         [[A, self.now - 7200], [G, self.now - 3000], [B, self.now - 2000]])

    def test_a_switch_history_has_that_the_page_saw_otherwise_changes_nothing(self):
        self.accounts([[A, self.now - 7200], [G, self.now - 600]])
        self.switched(B, self.now - 3000)                               # the page saw G next, not B: left as it saw it
        self.assertEqual(artifacts.switch_timeline(core.load()), [[A, self.now - 7200], [G, self.now - 600]])


# ---------- a Chrome profile Switchboard makes is named by the page's one short-name rule ----------

# A common setup's shape, made up: a gmail address with no alias, three aliased ones, and a second gmail address (ivy).
GMAIL, HARBOR, FERNHILL, GMAIL2, OUTLOOK = ('quinn.demo@gmail.example', 'quinn@harbor.example',
                                           'quinn@fernhill.example', 'quinn.b@gmail.example', 'quinn@outlook.example')
SHAPE = {GMAIL: None, HARBOR: 'harbor', FERNHILL: 'fernhill', GMAIL2: 'ivy', OUTLOOK: 'outlook'}


class ChromeProfileNames(Home):
    """Set up, the row menu's New profile and Add account each make a profile named "Claude " + the account's short
    name on the page: its alias; else its domain's first label when no other account without an alias has that label
    and no account's alias is that word; else the part before the @. A no-alias gmail account's is "Claude gmail"."""

    def setUp(self):
        super().setUp()
        self.chrome = FakeChrome(self, {'Profile 4': 'Work'})
        self.state(**dict((e, {'alias': alias}) for e, alias in SHAPE.items()))

    def test_the_rule_on_accounts_of_every_shape(self):
        accounts = core.load()['accounts']
        self.assertEqual([artifacts.profile_label(e, accounts) for e in SHAPE],
                         ['gmail', 'harbor', 'fernhill', 'ivy', 'outlook'])
        two = {'ana@mail.example': {}, 'bo@mail.example': {}}               # two without an alias share the label
        self.assertEqual([artifacts.profile_label(e, two) for e in two], ['ana', 'bo'])
        taken = {'ana@mail.example': {}, 'bo@home.example': {'alias': 'mail'}}     # an alias is that word
        self.assertEqual(artifacts.profile_label('ana@mail.example', taken), 'ana')

    def test_set_up_and_its_button(self):
        row = artifacts.link_rows(core.load(), {'Profile 4': 'Work'})[GMAIL]
        self.assertEqual(row['action']['profile_name'], 'Claude gmail')
        self.assertEqual(artifacts.setup_profile(GMAIL)['profile'], 'Claude gmail')

    def test_new_profile_in_the_row_menu(self):
        self.assertEqual(server.act('/api/profile', {'email': GMAIL, 'profile': '+new'}),
                         {'opened': True, 'profile_name': 'Claude gmail'})
        self.assertEqual(server.act('/api/profile', {'email': OUTLOOK, 'profile': '+new'})['profile_name'],
                         'Claude outlook')

    def test_add_account(self):
        started = []
        self.patch(connect, 'start', lambda email, profile: started.append((email, profile)) or 'k')
        for email in ('quinn@lab.example', 'quinn.c@gmail.example', ''):
            server.act('/api/connect', {'email': email, 'profile': '+new'})
        self.assertEqual(started, [('quinn@lab.example', 'Claude lab'),
                                   ('quinn.c@gmail.example', 'Claude quinnc'),      # gmail is the first account's
                                   ('', 'Claude account')])


# ---------- only the model automatic switching counts is drawn against its line ----------

def two_models(now, pct=96):
    """A made-up account whose reading has two models' own weeks, both past 95%."""
    resets = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now + 3 * 86400))
    return {'slot': 1, 'email': 'quinn@lab.example', 'usage_at': now - 60, 'trusted': True,
            'usage': {'session': {'pct': 10, 'resets': resets}, 'weekly': {'pct': 40, 'resets': resets},
                      'scoped': [{'name': 'Fable', 'pct': pct, 'resets': resets},
                                 {'name': 'Opus', 'pct': pct, 'resets': resets}], 'spend': None}}


class ModelWeeksInTheTerminalAndTheMenuBar(unittest.TestCase):
    """The terminal's list draws no model week at all: no line, no note (the page shows it). In the menu bar's limits
    the model automatic switching counts (the engine counts one: Fable here, or Fable 5.2 by the same name), at or
    over its line, is the weekly line's red note; another model's week is never said there. A state with no `models`
    at all (from before they were named) counts every model: the first of the highest is said."""

    def setUp(self):
        self.now = time.time()
        self.auto = {'enabled': True, 'on': True, 'session_at': 90.0, 'weekly_at': 95.0, 'model_at': 95.0,
                     'model': 'Fable', 'models': ['Fable']}

    def notes(self, auto):
        a = two_models(self.now)
        term_rows = term.read({'now': self.now, 'auto': auto, 'accounts': [a]})['accounts'][0]['limits']
        bar_rows = menubar.limits(a, auto)
        return ([x['label'] for x in term_rows], term_rows[-1].get('note'),
                [(x['label'], x['over']) for x in bar_rows if x['kind'] == 'model'])

    def test_only_the_counted_model_is_said(self):
        self.assertEqual(self.notes(self.auto), (['5-hour', 'weekly'], None, [('Fable', True)]))
        self.assertEqual(self.notes(dict(self.auto, models=['Fable 5.2'])),
                         (['5-hour', 'weekly'], None, [('Fable', True)]))

    def test_no_model_counted_none_is_said(self):
        self.assertEqual(self.notes(dict(self.auto, model=None, models=[])), (['5-hour', 'weekly'], None, []))

    def test_a_state_with_no_models_counts_every_model(self):
        auto = dict(self.auto)
        del auto['models'], auto['model']
        self.assertEqual(self.notes(auto), (['5-hour', 'weekly'], None, [('Fable', True)]))


MODEL_NOTES = r"""
      await wait(800);
      const row = all('#acc .arow').find((b) => b.querySelector('.em').textContent === %s), note = row && row.querySelector('.c-week .mnote');
      done(all('#acc .arow').length && row ? {lines: row.querySelectorAll('[data-kind="model"]').length, note: note ? note.textContent : null,
        hover: row.querySelector('.c-week').title.split('\n').slice(1)} : null);
"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class ModelWeeksOnThePage(ChromePage):
    """(Each model week was a line under the weekly bar, red only when counted.) Now: no line; the counted one at or
    over its line is the red note; each is said on the weekly line's hover, the counted one with its switch line."""

    def notes(self, models):
        demo._ready()
        self.at = time.time()
        demo.STATE['accounts'][LAB]['usage']['scoped'] = two_models(self.at)['usage']['scoped']
        demo.FACTS['auto'].update(model=models[0] if models else None, models=models, model_at=95.0)
        return self.run_page(MODEL_NOTES % json.dumps(LAB))

    def test_a_second_model_is_said_plainly_on_the_hover(self):
        self.assertEqual(self.notes(['Fable']), {'lines': 0, 'note': 'Fable 96%', 'hover': [
            'Fable’s own week: 96% used, switches at 95%. Refills ' + self.refill(), 'Opus’s own week: 96% used. Refills ' + self.refill()]})

    def test_with_no_model_counted_none_is_the_note(self):
        self.assertEqual(self.notes([]), {'lines': 0, 'note': None, 'hover': [
            'Fable’s own week: 96% used. Refills ' + self.refill(), 'Opus’s own week: 96% used. Refills ' + self.refill()]})

    def refill(self):
        """The two weeks' own refill, as the page's clock() says it (they are not the weekly one's: LAB's week refills
        in 1.4 days, these in 3): a weekday and a time."""
        return demo_clock(int(self.at + 3 * 86400))           # two_models' reset, to the second


def demo_clock(t):
    """index.html's clock(): 'Thu 5:00 PM' for another day, in this Mac's time zone."""
    lt = time.localtime(round(t / 60) * 60)
    return '%s %d:%02d %s' % (time.strftime('%a', lt), lt.tm_hour % 12 or 12, lt.tm_min, 'AM' if lt.tm_hour < 12 else 'PM')


# ---------- Settings › Links says "An artifact always opens as its owner." only while that is true ----------

LINKS_NOTE = STATE_EXTRA + r"""
      await wait(400);
      location.hash = '#settings'; await wait(900);
      const out = [];
      for (const c of %s) {
        extra = {links: Object.assign({installed: true, on: false, browser: null, home: '', key: 'demo', banner: null, handlers: null,
                                       holder: null, forwards: false}, c)};
        await look(900);
        out.push({note: ($('#g-links .g-note') || {}).textContent || '',
                  line: ($('#g-links .needs[data-k="links-line"]') || {}).textContent || ''});
      }
      done(out);
"""


@unittest.skipUnless(os.path.exists(CHROME), 'needs Google Chrome')
class TheLinksNoteIsTrue(ChromePage):
    def test_the_note_shows_only_while_switchboard_links_is_the_default(self):
        got = self.run_page(LINKS_NOTE % json.dumps([
            {'on': True, 'browser': 'Switchboard Links'},
            {'browser': 'Google Chrome', 'banner': BANNER, 'holder': HOLDERS['chrome']},
            {'browser': 'Link Router', 'banner': BANNER, 'holder': HOLDERS['router'], 'forwards': True},
            {'browser': 'Safari', 'banner': None, 'holder': HOLDERS['safari']},            # `switchboard links off`
        ]), budget=15000)
        self.assertEqual(got[0], {'note': 'An artifact always opens as its owner.', 'line': ''})
        self.assertEqual(got[1]['note'], '')                 # Chrome has the seat: its line says links open there
        self.assertTrue(got[1]['line'].startswith('Google Chrome is the default browser'), got[1])
        self.assertEqual(got[2]['note'], '')                 # a Link Router: they open as their owner while it runs
        self.assertTrue(got[2]['line'].startswith('Link Router takes clicked links and sends them here'), got[2])
        self.assertEqual(got[3], {'note': '', 'line': ''})   # turned off on purpose: no line, and no claim


if __name__ == '__main__':
    unittest.main()
