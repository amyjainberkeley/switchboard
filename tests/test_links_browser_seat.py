import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""Who holds the default-browser seat and other apps that also take clicked links. Every app
here is a made-up bundle in a throwaway home; the link app's program is a small script that answers --status."""
import os
import plistlib
import unittest

from fakes_links import Home

from switchboard import core, linkapp

LINKS_ID = linkapp.BUNDLE_ID


class Apps(Home):
    def setUp(self):
        super().setUp()
        self.apps = os.path.join(self.home, 'Applications')
        os.makedirs(self.apps)
        self.ran = os.path.join(self.tmp, 'ran.txt')

    def app(self, name, bundle_id, schemes=(), says=None, display=None):
        path = os.path.join(self.apps, name)
        os.makedirs(os.path.join(path, 'Contents', 'MacOS'))
        info = {'CFBundleIdentifier': bundle_id, 'CFBundleExecutable': 'run'}
        if display:
            info['CFBundleDisplayName'] = display
        if schemes:
            info['CFBundleURLTypes'] = [{'CFBundleURLName': 'Web', 'CFBundleURLSchemes': list(schemes)}]
        with open(os.path.join(path, 'Contents', 'Info.plist'), 'wb') as f:
            plistlib.dump(info, f)
        exe = os.path.join(path, 'Contents', 'MacOS', 'run')
        with open(exe, 'w') as f:
            f.write('#!/bin/sh\necho "%s $*" >> "%s"\n' % (name, self.ran) +
                    ('[ "$1" != --status ] || echo "%s"\n' % says if says is not None else ''))
        os.chmod(exe, 0o755)
        return path

    def runs(self):
        try:
            with open(self.ran) as f:
                return f.read().splitlines()
        except OSError:
            return []


class TheSeat(Apps):
    """The default browser is no longer the link app: say who has it, offer one button, never take it back."""

    def links_saying(self, holder_path):
        self.app('Switchboard Links.app', LINKS_ID, ('http', 'https'), says=holder_path)

    def test_links_open_as_their_owner_no_banner(self):
        self.links_saying(os.path.join(self.apps, 'Switchboard Links.app'))
        self.assertTrue(linkapp.status(fresh=True)['on'])
        self.assertIsNone(linkapp.banner())
        self.assertEqual(linkapp.seat()['holder']['kind'], 'links')

    def test_chrome_took_the_seat(self):
        chrome = self.app('Google Chrome.app', 'com.google.Chrome', ('http', 'https'))
        self.links_saying(chrome)
        st = linkapp.status(fresh=True)
        self.assertEqual((st['installed'], st['on']), (True, False))
        b = linkapp.banner()
        self.assertEqual(b['action'], 'make_default')
        self.assertEqual(b['text'], 'Google Chrome is the default browser, so links you click in other apps open there, '
                                    'not as their owner.')
        self.assertEqual(linkapp.seat()['holder']['kind'], 'browser')
        self.assertFalse(any('--make-default' in r for r in self.runs()), 'the seat is never taken back by itself')

    def test_another_link_handler_took_it_and_is_named(self):
        router = self.app('Link Router.app', 'com.example.linkrouter', ('http', 'https'), display='Link Router')
        self.links_saying(router)
        b = linkapp.banner()
        self.assertIn('Link Router takes clicked links now', b['text'])

    def test_nothing_said_when_the_link_app_was_never_built(self):
        self.assertFalse(linkapp.status(fresh=True)['installed'])
        self.assertIsNone(linkapp.banner())

    def test_an_answer_that_names_nothing_still_gets_a_plain_line(self):
        self.links_saying('')
        b = linkapp.banner()
        self.assertEqual(b['text'], 'Another app takes clicked links now. They open as their owner only if it sends '
                                    'them to Switchboard.')

    def test_links_never_loop_back_to_the_link_app(self):
        self.links_saying(os.path.join(self.apps, 'Switchboard Links.app'))
        os.makedirs(core.data_dir(), exist_ok=True)
        with open(core.data_path('previous-browser.txt'), 'w') as f:
            f.write(LINKS_ID)
        self.assertTrue(linkapp.seat(fresh=True)['loop'])
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, '..', 'router', 'SwitchboardLinks.swift')) as f:
            src = f.read()
        self.assertIn('where !id.isEmpty && id != me', src)       # the usual browser is never the link app itself
        self.assertIn('guard holder != me else { return nil }', src)


class OtherLinkHandlers(Apps):
    """Other apps that take clicked links are listed by what they declare, never stopped."""

    def test_only_apps_that_take_web_links_and_are_not_browsers_or_the_link_app(self):
        self.app('Link Router.app', 'com.example.linkrouter', ('http', 'https'), display='Link Router')
        self.app('Claude Accounts.app', 'com.example.accounts', ('HTTPS',))
        self.app('Google Chrome.app', 'com.google.Chrome', ('http', 'https'))
        self.app('Switchboard Links.app', LINKS_ID, ('http', 'https'), says=os.path.join(self.apps, 'Link Router.app'))
        self.app('Notes.app', 'com.example.notes', ('notes',))
        self.app('Broken.app', 'com.example.broken')
        linkapp.status(fresh=True)
        found = linkapp.other_link_handlers(folders=[self.apps])
        self.assertEqual([(x['name'], x['default']) for x in found], [('Claude Accounts', False), ('Link Router', True)])
        note = linkapp.handlers_note(found)
        self.assertTrue(note.startswith('Other apps here can also take clicked links: '))
        self.assertNotIn('Link Router', note)               # the app in the seat: the links line says what it does
        self.assertIn('Claude Accounts (~/Applications/Claude Accounts.app)', note)
        self.assertIn('Only one should', note)
        self.assertEqual([r for r in self.runs() if '--status' not in r], [], 'nothing else is ever run')

    def test_none_found_nothing_said(self):
        self.assertEqual(linkapp.other_link_handlers(folders=[self.apps]), [])
        self.assertIsNone(linkapp.handlers_note([]))


if __name__ == '__main__':
    unittest.main()
