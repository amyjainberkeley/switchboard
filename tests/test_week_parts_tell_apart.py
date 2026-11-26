import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)
# The week's three parts are three different things: workflows, helpers, and you in chat. Wherever the page stacks
# them (the hours, the this-week and last-week bars, a project's bar), a reader has to see three parts, not one bar
# that fades. So they are not three steps of one grey: workflows are ink, helpers a grey, and you wear the page's one
# accent. This file reads the page's own colours for them, in each theme, and measures them. No browser.
# Run with:
#   SWITCHBOARD_HOME="$(mktemp -d)/data" taskpolicy -b python3 -m unittest discover -s tests -p 'test_week_parts_tell_apart.py'
import itertools
import math
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

PAGE = os.path.join(ROOT, 'switchboard', 'web', 'index.html')
PARTS = (('wf', 'workflows'), ('help', 'helpers'), ('you', 'you'))     # baseline up, as the page stacks them

# How far apart two colours look: the distance between them in OKLab (Björn Ottosson, 2020), times 100. About 2 is
# the smallest step an eye sees side by side; today's page asks for 20 between any two parts.
# The same distance as a colour-blind eye sees it: both colours first go through the matrices of Machado, Oliveira and
# Fernandes (2009) at full severity, for the two red-green kinds (protan, deutan) and the blue-yellow one (tritan).
SIGHTS = {'protan': ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
          'deutan': ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
          'tritan': ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900))}
FAR = 20            # any two parts, plain sight
FAR_RED_GREEN = 15  # any two parts, either red-green kind of colour blindness
FAR_BLUE_YELLOW = 10
SHOWS = 3.0         # a part against its page (the contrast ratio that marks need)
NO_COLOUR = 0.03    # under this much chroma a colour reads as a grey


def linear(colour):
    """'#27272a' as three linear-light numbers."""
    h = colour.strip().lstrip('#')
    return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)))


def oklab(r, g, b):
    l, m, s = (x ** (1 / 3) for x in (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
                                      0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
                                      0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b))
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s, 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def seen(colour, sight=None):
    rgb = linear(colour)
    if sight:
        rgb = tuple(max(0.0, min(1.0, sum(k * x for k, x in zip(row, rgb)))) for row in SIGHTS[sight])
    return oklab(*rgb)


def apart(a, b, sight=None):
    return 100 * math.dist(seen(a, sight), seen(b, sight))


def contrast(a, b):
    la, lb = (0.2126 * r + 0.7152 * g + 0.0722 * bb for r, g, bb in (linear(a), linear(b)))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def chroma(colour):
    return math.hypot(*seen(colour)[1:])


def hexed(rgb):
    """'rgb(39, 39, 42)', as a browser says a colour, as '#27272a'."""
    r, g, b = (int(round(float(x))) for x in re.findall(r'[\d.]+', rgb)[:3])
    return '#%02x%02x%02x' % (r, g, b)


def themes():
    """The page's colours as it states them: light, dark as the Mac picks it, dark as a person picks it."""
    with open(PAGE, encoding='utf-8') as f:
        css = f.read()

    def block(start):
        i = css.index(start) + len(start)
        return dict((k, v.strip()) for k, v in re.findall(r'--([a-z0-9-]+):\s*([^;]+);', css[i:css.index('}', i)]))
    return {'light': block('\n:root {'), 'dark': block(':root:not([data-theme="light"]) {'), 'dark, picked': block(':root[data-theme="dark"] {')}


class TheThreeParts(unittest.TestCase):
    def setUp(self):
        self.themes = themes()

    def each(self):
        for name, t in self.themes.items():
            yield name, t, [(word, t['c-' + key]) for key, word in PARTS]

    def test_both_dark_themes_draw_the_same_parts(self):
        mac, picked = self.themes['dark'], self.themes['dark, picked']
        for k in ('bg', 'accent', 'c-wf', 'c-help', 'c-you', 'c-one'):
            self.assertEqual(mac[k], picked[k], k)
        self.assertNotEqual(self.themes['light']['c-wf'], mac['c-wf'])

    def test_each_part_shows_on_its_page(self):
        for name, t, parts in self.each():
            for word, colour in parts + [('a bar that is not split', t['c-one'])]:
                self.assertGreaterEqual(contrast(colour, t['bg']), SHOWS, (name, word, colour))

    def test_any_two_parts_are_far_apart(self):
        # as steps of one grey they were 12 apart in the light theme: one bar that fades, not three parts
        for name, t, parts in self.each():
            for (wa, a), (wb, b) in itertools.combinations(parts, 2):
                self.assertGreaterEqual(apart(a, b), FAR, (name, wa, wb, round(apart(a, b), 1)))

    def test_and_far_apart_to_a_colour_blind_eye(self):
        for name, t, parts in self.each():
            for (wa, a), (wb, b) in itertools.combinations(parts, 2):
                for sight, least in (('protan', FAR_RED_GREEN), ('deutan', FAR_RED_GREEN), ('tritan', FAR_BLUE_YELLOW)):
                    self.assertGreaterEqual(apart(a, b, sight), least, (name, wa, wb, sight, round(apart(a, b, sight), 1)))

    def test_you_wear_the_pages_one_accent(self):
        for name, t, parts in self.each():
            self.assertEqual(t['c-you'], t['accent'], name)

    def test_workflows_and_helpers_carry_no_colour(self):
        # so the accent stays the only colour in the week, and it means one thing: you
        for name, t, parts in self.each():
            for key in ('c-wf', 'c-help', 'c-one'):
                self.assertLess(chroma(t[key]), NO_COLOUR, (name, key, t[key]))
            self.assertGreater(chroma(t['c-you']), 0.1, name)
            # workflows are the ink: the strongest of the three on the page, helpers the quietest
            self.assertGreater(contrast(t['c-wf'], t['bg']), contrast(t['c-you'], t['bg']), name)
            self.assertGreater(contrast(t['c-you'], t['bg']), contrast(t['c-help'], t['bg']), name)

    def test_a_bar_that_is_not_split_looks_like_none_of_the_three(self):
        # "What agents did" and "Models" draw one plain bar a row: it must not read as the helpers' grey
        for name, t, parts in self.each():
            for word, colour in parts:
                self.assertGreaterEqual(apart(t['c-one'], colour), 15, (name, word, round(apart(t['c-one'], colour), 1)))


if __name__ == '__main__':
    unittest.main()
