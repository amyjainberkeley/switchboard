import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
"""The /go page (web/go.html) as headless Chrome draws it, served by the real handler: the explanation for a
page whose owner has no profile signed in as it, the bookmark's question, and nothing from the address
ever read as HTML. Chrome missing: skipped."""
import html
import os
import re
import unittest
from urllib.parse import quote

import test_app  # its headless-Chrome helper

LINK = 'https://claude.ai/artifact/' + 'a' * 20


def text_of(dom, tag_id):
    m = re.search(r'<[a-z0-9]+ id="%s"[^>]*>(.*?)</' % tag_id, dom, re.S)
    return html.unescape(m.group(1)) if m else None


@unittest.skipUnless(os.path.exists(test_app.CHROME), 'needs Google Chrome')
class TheGoPage(test_app.ChromePage):
    def test_a_page_whose_owner_has_no_profile_explains_and_offers_set_up(self):
        inner = 'owner=%s&url=%s' % (quote('ana@gmail.example', safe=''), quote(LINK + '?a=1&b=2', safe=''))
        dom = self.dom('go#' + quote(inner, safe=''), budget=3000)       # exactly as artifacts.explain_no_profile writes it
        self.assertEqual(text_of(dom, 'h'), 'No Chrome profile is signed in as ana@gmail.example')
        self.assertIn('This page belongs to ana@gmail.example', text_of(dom, 'p'))
        self.assertRegex(dom, r'<button id="s">Set up</button>')            # shown (no hidden attribute)
        self.assertRegex(dom, r'<button id="b" hidden="">')
        self.assertNotIn('sign in to', (text_of(dom, 'p') or '').lower())

    def test_an_owner_name_from_the_address_is_only_ever_text(self):
        evil = '<img src=x onerror=alert(1)>'
        dom = self.dom('go#' + quote('owner=%s&url=%s' % (evil, LINK), safe=''), budget=3000)
        self.assertNotIn('<img', dom)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', dom)

    def test_a_visit_without_the_key_asks_first_and_a_strange_link_is_refused(self):
        dom = self.dom('go#' + quote(LINK, safe=''), budget=3000)
        self.assertEqual(text_of(dom, 'h'), 'Open this page as its owner?')
        self.assertEqual(text_of(dom, 'p'), LINK)
        dom = self.dom('go#' + quote('https://claude.ai.evil.example/artifact/' + 'a' * 20, safe=''), budget=3000)
        self.assertEqual(text_of(dom, 'h'), 'No claude.ai link here')


if __name__ == '__main__':
    unittest.main()
