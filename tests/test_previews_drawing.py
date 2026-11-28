import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""Behind the page: previews, a picture of what each artifact holds.

For an artifact whose publish named a local file that is still there, the page's worker draws that file once in
headless Chrome (a fresh profile, removed after; the file:// page at 1200 x 750; about 480 px wide; one at a time, at
background priority, a 30 s deadline) and keeps the JPEG in previews/<id>.jpg, owner-only, written whole. Capped, the
oldest going first; a picture goes when its artifact is no longer listed. The page gets `preview: true|false` per
file in /api/artifacts and the picture at GET /api/preview/<id>, for a listed id only, never used as a path.

Chrome here is a stand-in (tests/fakes_artifacts.py) that writes a small JPEG, a wide PNG or JPEG, nothing, or
hangs; macOS's own sips makes a wide one small, as it does for real Chrome's half-scale picture."""
import glob
import http.client
import json
import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from fakes_links import Home
from fakes_artifacts import A, B, ID1, ID2, ID3, ID4, LINK, TINY_JPEG, StandInChrome, publish

from switchboard import artifacts, browser, core, previews, server, session_hooks
import pagekey_help  # noqa: E402  (the page key, as Switchboard's own programs send it)

IDS = ['%s%02dAbCdEfGhIjKlMnOp' % (c, i) for i, c in enumerate('QRSTUVWXYZ')]


class PreviewCase(Home):
    """A fresh home with a stand-in Chrome in its Applications folder, pages to draw, and pictures turned on."""

    def setUp(self):
        super().setUp()
        self.patch(browser, 'app_dirs', lambda: [os.path.join(self.home, 'Applications')])
        browser.APPS.clear()
        self.patch(previews, 'WORK', {'running': False, 'again': False, 'at': -1e9, 'forced': True, 'tried': {}})
        self.patch(browser, 'LOAD', lambda: 0.1)                     # a quiet Mac
        self.patch(previews, 'DEADLINE', 120.0)     # a test of what is drawn, not of the Mac's load (its own test below)
        self.chrome = StandInChrome(self.home)
        self.pages = os.path.join(self.tmp, 'pages')
        os.makedirs(self.pages)
        self.now = time.time()

    def page(self, name='index.html', body='<h1>Plan</h1>'):
        p = os.path.join(self.pages, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            f.write('<!doctype html><title>Plan</title>' + body)
        os.utime(p, (self.now - 7200, self.now - 7200))     # a page's file is written before it is published
        return p

    def listed(self, *items, files_v=True):
        """Save a state that lists these artifacts: (id, the file its publish named or None, that publish's time)."""
        st = core.blank_state()
        for aid, f, ft in items:
            it = artifacts.blank_item(aid, ft or self.now - 600)
            it.update(owner=A, src='published', ot=ft or self.now - 600)
            if f:
                it.update(file=f, ft=ft)
            st['artifacts'].append(it)
        st['scan_v'] = artifacts.SCAN_V
        if files_v:
            st['files_v'] = artifacts.FILES_V
        core.save(st)

    def picture(self, aid, made=None, data=TINY_JPEG):
        previews.keep(aid, data)
        if made is not None:
            os.utime(previews.path_of(aid), (made, made))


class APictureOfThePage(PreviewCase):
    def test_drawn_once_from_the_published_file_in_a_profile_of_its_own(self):
        src = self.page('launch/index.html')
        self.listed((ID1, src, self.now - 30))
        self.assertEqual(previews.run_once(), {'made': 1, 'failed': 0, 'gone': 0})
        pic = previews.path_of(ID1)
        with open(pic, 'rb') as f:
            self.assertEqual(f.read(), TINY_JPEG)
        self.assertEqual(stat.S_IMODE(os.stat(pic).st_mode), 0o600)                # only you can read it
        self.assertEqual(stat.S_IMODE(os.stat(previews.folder()).st_mode), 0o700)
        args = self.chrome.calls()[-1]['args']
        for flag in ('--headless=new', '--window-size=1200,750', '--force-device-scale-factor=0.5',
                     '--use-mock-keychain', '--disable-extensions', '--no-first-run', '--disable-sync',
                     '--deny-permission-prompts'):
            self.assertIn(flag, args)
        self.assertEqual(args[-1], pathlib.Path(src).as_uri())                      # the file:// page
        profile = next(a.split('=', 1)[1] for a in args if a.startswith('--user-data-dir='))
        self.assertNotIn(os.path.join('Library', 'Application Support'), profile)   # never one of your profiles
        self.assertFalse(os.path.exists(profile))                                   # and removed after
        rules = next(a for a in args if a.startswith('--host-resolver-rules='))
        self.assertIn('MAP * ~NOTFOUND', rules)
        self.assertIn('EXCLUDE cdnjs.cloudflare.com', rules)
        self.assertEqual(previews.known(), {ID1})
        files = [x for fo in artifacts.folders(core.load(), {})['folders'] for x in fo['files']]
        self.assertEqual([(x['id'], x['preview']) for x in files], [(ID1, True)])
        self.assertEqual(previews.run_once()['made'], 0)                    # once, until it is published again
        self.assertEqual(len(self.chrome.calls()), 1)
        with core.locked() as st:
            st['artifacts'][0]['ft'] = time.time() + 5                      # published again: drawn again
        self.assertEqual(previews.run_once()['made'], 1)

    def test_a_wide_picture_is_made_about_480_px_wide_and_a_png_a_jpeg(self):
        subprocess.run(['/usr/bin/sips', '-s', 'format', 'jpeg', os.path.join(self.chrome.dir, 'wide.png'), '--out',
                        os.path.join(self.chrome.dir, 'wide.jpg')], capture_output=True, timeout=60, check=True)
        for mode, aid in (('wide-png', ID1), ('wide-jpeg', ID2)):
            with self.subTest(mode=mode):
                self.chrome.mode(mode)
                self.listed((aid, self.page('%s.html' % mode), self.now - 30))
                self.assertEqual(previews.run_once()['made'], 1)
                with open(previews.path_of(aid), 'rb') as f:
                    data = f.read()
                self.assertEqual(previews.jpeg_size(data), (480, 300))

    def test_nothing_or_junk_from_the_browser_is_no_picture(self):
        for mode in ('nothing', 'junk', 'fail'):
            with self.subTest(mode=mode):
                self.chrome.mode(mode)
                self.listed((ID1, self.page(), self.now - 30))
                previews.WORK['tried'].clear()
                self.assertEqual(previews.run_once()['made'], 0)
                self.assertEqual(previews.known(), set())


class WhereThePicturesLive(PreviewCase):
    def test_the_cap_takes_the_oldest_first(self):
        self.listed(*[(aid, None, None) for aid in IDS[:6]])
        for i, aid in enumerate(IDS[:6]):
            self.picture(aid, made=self.now - 600 + 60 * i)
        self.patch(previews, 'MAX_FILES', 4)
        self.assertEqual(previews.tidy(), 2)
        self.assertEqual(previews.known(), set(IDS[2:6]))
        self.patch(previews, 'MAX_BYTES', 3 * len(TINY_JPEG) + 10)
        self.assertEqual(previews.tidy(), 1)
        self.assertEqual(previews.known(), set(IDS[3:6]))

    def test_a_picture_goes_with_its_artifact_and_stays_while_it_is_listed(self):
        self.listed((ID1, None, None), (ID2, None, None))
        for aid in (ID1, ID2, ID3):
            self.picture(aid)
        with core.locked() as st:
            st['artifacts'][1]['owner'] = B                 # an account since removed: its artifact is still listed
        self.assertEqual(previews.tidy(), 1)
        self.assertEqual(previews.known(), {ID1, ID2})
        core.save(core.blank_state())                      # a list that holds nothing never takes them all
        self.assertEqual(previews.tidy(), 0)
        with open(core.data_path('state.json'), 'w') as f:
            f.write('{broken')
        self.assertEqual(previews.tidy(), 0)
        self.assertEqual(previews.known(), {ID1, ID2})

    def test_a_write_cut_short_leaves_nothing_after_an_hour(self):
        self.listed((ID1, None, None))
        self.picture(ID1)
        left = os.path.join(previews.folder(), '.%s.jpg.0a1b2c3d.tmp' % ID1)
        with open(left, 'wb') as f:
            f.write(b'half')
        os.utime(left, (self.now - 7200, self.now - 7200))
        previews.tidy()
        self.assertFalse(os.path.exists(left))
        self.assertEqual(previews.known(), {ID1})


class HostileIds(PreviewCase):
    def test_an_id_is_never_a_path(self):
        self.listed((ID1, None, None), (ID2, None, None), (ID3, None, None))
        self.picture(ID1)
        self.assertEqual(previews.picture(ID1), TINY_JPEG)
        outside = os.path.join(self.tmp, 'secret.jpg')
        with open(outside, 'wb') as f:
            f.write(TINY_JPEG)
        for bad in ('../secret', '../../../../' + outside.lstrip('/'), outside, '/etc/passwd', 'a' * 129, 'a' * 10000,
                    '', 'short', ID1 + '/../' + ID1, ID1 + '.jpg', '%2e%2e%2fsecret%2e%2e%2f%2e%2e', 'Ä' * 20,
                    ID1 + '\x00', ID1 + '\n', None, 5, [ID1]):
            with self.subTest(bad=repr(bad)[:40]):
                self.assertIsNone(previews.picture(bad))
                with self.assertRaises(ValueError):
                    previews.path_of(bad)
        self.assertIsNone(previews.picture(ID4))           # a good id the page does not list
        self.picture(ID4)
        self.assertIsNone(previews.picture(ID4))           # still not listed, even with a picture
        os.symlink(outside, previews.path_of(ID2))         # a link put in the folder is not followed
        self.assertIsNone(previews.picture(ID2))
        with open(previews.path_of(ID3), 'wb') as f:
            f.write(b'GIF89a not a jpeg at all')
        self.assertIsNone(previews.picture(ID3))           # only a whole JPEG is answered


class TheRoute(PreviewCase):
    def setUp(self):
        super().setUp()
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.port = self.httpd.server_address[1]
        env = mock.patch.dict(os.environ, {'SWITCHBOARD_PORT': str(self.port)})
        env.start()
        self.addCleanup(env.stop)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def get(self, path, **headers):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=20)
        try:
            c.putrequest('GET', path, skip_host=True, skip_accept_encoding=True)
            c.putheader('Host', headers.pop('host', '127.0.0.1:%d' % self.port))
            for k, v in pagekey_help.with_key(headers).items():
                c.putheader(k.replace('_', '-'), v)
            c.endheaders()
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def test_a_listed_id_gets_its_picture_and_nothing_else_gets_anything(self):
        self.listed((ID1, None, None), (ID2, None, None))
        self.picture(ID1)
        code, headers, body = self.get('/api/preview/' + ID1)
        self.assertEqual((code, headers['Content-Type'], body), (200, 'image/jpeg', TINY_JPEG))
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        code, _, _ = self.get('/api/preview/' + ID1, Sec_Fetch_Site='same-origin')
        self.assertEqual(code, 200)
        for path in ('/api/preview/' + ID2, '/api/preview/' + ID3, '/api/preview/', '/api/preview/../../etc/passwd',
                     '/api/preview/..%2F..%2Fsecret', '/api/preview//etc/passwd', '/api/preview/' + 'a' * 10000,
                     '/api/preview/%s%%00' % ID1, '/api/preview/%s/x' % ID1, '/api/preview/%s.jpg' % ID1):
            with self.subTest(path=path[:50]):
                code, _, body = self.get(path)
                self.assertEqual(code, 404)
                self.assertNotIn(b'\xff\xd8', body)
        for headers in ({'host': 'evil.example:%d' % self.port}, {'Origin': 'https://evil.example'},
                        {'Sec_Fetch_Site': 'cross-site'}, {'Sec_Fetch_Site': 'same-site'}):
            with self.subTest(headers=headers):
                code, _, body = self.get('/api/preview/' + ID1, **headers)
                self.assertEqual(code, 403)
                self.assertNotIn(b'\xff\xd8', body)

    def test_the_demo_has_no_pictures(self):
        self.listed((ID1, None, None))
        self.picture(ID1)
        self.patch(core, 'DEMO', True)
        self.assertEqual(previews.known(), set())
        self.assertEqual(self.get('/api/preview/' + ID1)[0], 404)

    def test_each_file_in_the_artifacts_says_whether_it_has_a_picture(self):
        self.listed((ID1, None, None), (ID2, None, None))
        self.picture(ID1)
        code, _, body = self.get('/api/artifacts')
        self.assertEqual(code, 200)
        files = dict((x['id'], x['preview']) for fo in json.loads(body)['folders'] for x in fo['files'])
        self.assertEqual(files, {ID1: True, ID2: False})


class WhenAPictureIsNotMade(PreviewCase):
    def test_a_missing_file_a_folder_a_markdown_file_or_a_relative_path_is_no_picture(self):
        os.makedirs(os.path.join(self.pages, 'dir.html'))
        self.listed((ID1, os.path.join(self.pages, 'gone.html'), self.now - 30),
                    (ID2, os.path.join(self.pages, 'dir.html'), self.now - 30),
                    (ID3, self.page('notes.md'), self.now - 30),
                    (ID4, 'pages/x.html', self.now - 30))
        self.assertEqual(previews.run_once()['made'], 0)
        self.assertEqual(self.chrome.calls(), [])                          # the browser is never started for them

    def test_a_file_that_goes_while_it_is_drawn_has_no_picture(self):
        self.chrome.mode('vanish')
        self.listed((ID1, self.page(), self.now - 30))
        self.assertEqual(previews.run_once(), {'made': 0, 'failed': 1, 'gone': 0})
        self.assertEqual(previews.known(), set())

    def test_a_deadline_ends_the_browser_and_its_profile(self):
        self.patch(previews, 'DEADLINE', 2.0)
        self.chrome.mode('hang')
        self.listed((ID1, self.page(), self.now - 30))
        started, real = [], subprocess.Popen

        def popen(*a, **k):                                 # the browser's process, as the page started it
            p = real(*a, **k)
            started.append(p.pid)
            return p
        temp = lambda: set(glob.glob(os.path.join(tempfile.gettempdir(), previews.TEMP_PREFIX + '*')))  # noqa: E731
        before = temp()
        began = time.monotonic()
        with mock.patch.object(previews.subprocess, 'Popen', popen):
            self.assertEqual(previews.run_once(), {'made': 0, 'failed': 1, 'gone': 0})
        self.assertLess(time.monotonic() - began, 300)      # the stand-in hangs for 600 s: the deadline ended it
        self.assertEqual(temp() - before, set())                            # its profile went with it
        self.assertEqual(len(started), 1)
        with self.assertRaises(ProcessLookupError):
            os.kill(started[0], 0)                          # ended and waited for (signal 0 only asks)
        self.chrome.mode('jpeg')
        with mock.patch.object(previews.subprocess, 'Popen', popen):
            self.assertEqual(previews.run_once()['made'], 0)                 # not again within the hour
        self.assertEqual(len(started), 1)
        previews.DEADLINE = 120.0                           # (restored by the patch above)
        self.assertEqual(previews.run_once(now=time.time() + 3700)['made'], 1)    # an hour on, it is

    def test_a_profile_left_by_a_page_stopped_mid_picture_goes_an_hour_on(self):
        base = tempfile.gettempdir()
        old = tempfile.mkdtemp(prefix=previews.TEMP_PREFIX, dir=base)
        self.addCleanup(shutil.rmtree, old, True)
        os.makedirs(os.path.join(old, 'profile', 'Default'))
        os.utime(old, (self.now - 7200, self.now - 7200))
        fresh = tempfile.mkdtemp(prefix=previews.TEMP_PREFIX, dir=base)     # another picture under way: left alone
        self.addCleanup(shutil.rmtree, fresh, True)
        mine = tempfile.mkdtemp(prefix='not-ours-', dir=base)
        self.addCleanup(shutil.rmtree, mine, True)
        os.utime(mine, (self.now - 7200, self.now - 7200))
        self.listed()
        previews.run_once()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isdir(fresh))
        self.assertTrue(os.path.isdir(mine))

    def test_a_mac_that_crawls_waits_and_an_ordinary_busy_one_does_not(self):
        self.listed((ID1, self.page(), self.now - 30))
        self.patch(browser, 'LOAD', lambda: 7.0)
        self.assertEqual(previews.run_once()['made'], 0)
        self.assertEqual(self.chrome.calls(), [])
        self.patch(browser, 'LOAD', lambda: 3.0)            # many sessions at once: Chrome runs at background priority
        self.assertEqual(previews.run_once()['made'], 1)

    def test_no_browser_no_picture(self):
        shutil.rmtree(self.chrome.app)
        browser.APPS.clear()
        self.assertIsNone(previews.chrome())
        self.listed((ID1, self.page(), self.now - 30))
        self.assertFalse(previews.kick(new=True))
        self.assertEqual(previews.run_once()['made'], 0)

    def test_only_in_the_page_s_own_process(self):
        previews.WORK['forced'] = None
        self.listed((ID1, self.page(), self.now - 30))
        self.assertFalse(previews.enabled())                # a command in a terminal, or a test, draws nothing
        self.assertFalse(previews.kick(new=True))
        self.assertEqual(self.chrome.calls(), [])


class TheBackfill(PreviewCase):
    def test_pages_known_from_before_get_their_files_from_history_once(self):
        src1, src3 = self.page('one.html'), self.page('rel/three.html')
        self.listed((ID1, None, None), (ID2, None, None), (ID3, None, None), files_v=False)
        proj = os.path.join(core.projects(), 'p')
        os.makedirs(proj)
        when = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(self.now - 3600))
        with open(os.path.join(proj, 's.jsonl'), 'w') as f:
            for text, cwd in (('Published %s at %s (version 2)' % (src1, LINK % ID1), '/x'),
                              ('Published /gone/two.html at %s' % (LINK % ID2), '/x'),
                              ('Published rel/three.html at %s' % (LINK % ID3), self.pages)):
                f.write(json.dumps({'type': 'assistant', 'message': {'content': [
                    {'type': 'tool_use', 'id': 't', 'name': 'Artifact'}]}}) + '\n')     # the call each answer belongs to
                f.write(json.dumps({'type': 'user', 'timestamp': when, 'cwd': cwd, 'message': {'content': [
                    {'type': 'tool_result', 'tool_use_id': 't', 'content': text}]}}) + '\n')
        self.assertEqual(previews.run_once()['made'], 2)
        st = core.load()
        self.assertEqual(st['files_v'], artifacts.FILES_V)
        self.assertEqual(dict((x['id'], x.get('file')) for x in st['artifacts']),
                         {ID1: src1, ID2: '/gone/two.html', ID3: src3})
        self.assertEqual(previews.known(), {ID1, ID3})      # the one whose file is gone has none
        with mock.patch.object(artifacts, 'backfill_files', side_effect=AssertionError('only once')):
            previews.run_once()


class FromPublishToPicture(PreviewCase):
    def test_a_publish_is_drawn_soon_after_its_line_arrives(self):
        self.addCleanup(artifacts._PUB.update, seen=None)
        artifacts._PUB['seen'] = None
        st = core.blank_state()
        core.ensure_account(st, A).update(connected=True, slot=1)
        st['active'], st['timeline'], st['files_v'] = A, [[A, self.now - 7200]], artifacts.FILES_V
        core.save(st)
        src = self.page('launch/index.html')
        out = __import__('io').StringIO()
        session_hooks.hook(stdin=__import__('io').BytesIO(json.dumps(publish(ID1, path=src)).encode()), env={},
                           now=time.time(), out=out)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(artifacts.published_tick(), 1)     # the page's next pass: owner, then the picture started
        end = time.monotonic() + 150
        while (previews.WORK['running'] or not previews.known()) and time.monotonic() < end:
            time.sleep(0.05)
        self.assertEqual(previews.known(), {ID1})
        self.assertEqual(artifacts.find(core.load(), ID1)['owner'], A)
        self.assertEqual(previews.picture(ID1), TINY_JPEG)


if __name__ == '__main__':
    unittest.main()
