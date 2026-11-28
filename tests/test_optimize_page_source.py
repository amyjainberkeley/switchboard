import _isolate  # noqa: F401  (first: it gives switchboard a throwaway data folder)

# The page's takeaway code, read as text (no browser): it asks /api/optimize with the week, puts every takeaway's words
# in as text, adds no colour of its own, and stays in the week section. tests/test_page_optimize.py drives it in Chrome.
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..'))
sys.path.insert(0, ROOT)

PAGE = os.path.join(ROOT, 'switchboard', 'web', 'index.html')
START = "// Takeaways on the week's numbers (/api/optimize)"


def read():
    with open(PAGE, encoding='utf-8') as f:
        return f.read()


class TheCode(unittest.TestCase):
    def setUp(self):
        self.html = read()
        a = self.html.index(START)
        self.js = self.html[a:self.html.index('function chartIndex(ev)', a)]

    def test_asked_with_the_week(self):
        self.assertIn("getJSON('/api/models'), getJSON('/api/optimize')]", self.html)
        self.assertIn("if (o && Array.isArray(o.takeaways)) optData = o.takeaways;", self.html)
        self.assertIn("post('/api/optimize', {id, do: how})", self.js)

    def test_words_go_in_as_text(self):
        self.assertNotIn('innerHTML', self.js)
        self.assertNotIn('insertAdjacentHTML', self.js)
        self.assertIn('d.textContent = text', self.js)

    def test_only_in_the_week_section(self):
        for q in re.findall(r"closest\('([^']+)'\)", self.js):
            self.assertIn(q, ('#week .opt', '#opt-pop'))
        for q in re.findall(r"querySelector(?:All)?\('([^']+)'", self.js):
            self.assertTrue(q.startswith(('#week', '#wk-', '#opt-pop')) or q == 'b', q)   # 'b': the legend row's share

    def test_no_colour_of_its_own(self):
        rules = [ln for ln in self.html.splitlines() if ln.startswith('.opt') or ln.startswith('.tip.opt-pop')]
        self.assertEqual(len(rules), 4)
        for ln in rules:
            self.assertNotRegex(ln, r'#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(')

    def test_the_press_says_undo_after(self):
        self.assertIn("undo ? 'Undo' : t.change.label", self.js)
        self.assertIn("e.key === 'Escape'", self.js)
        self.assertIn("e.key === 'Enter'", self.js)

    def test_the_press_is_reached_by_keyboard(self):
        # Enter on a number moves focus to its press, a press puts focus on the new press (Undo), focus leaving both
        # the number and the takeaway closes it, and Escape gives focus back to the number without opening it again
        self.assertIn("openOpt(el); optFocus(); }", self.js)
        self.assertIn("if (!optFocus()) el.focus();", self.js)
        self.assertIn("document.addEventListener('focusout'", self.js)
        self.assertIn("optSkip = true; back.focus(); optSkip = false;", self.js)
        self.assertIn("if (el && !optSkip) openOpt(el);", self.js)

    def test_the_presss_other_lines_go_under_the_first(self):
        self.assertIn("more: Array.isArray(d.more) ? d.more : []", self.js)
        self.assertIn("optSaid.more) || []) pop.appendChild(add('div', 't-x', m));", self.js)

    def test_a_count_reads_as_times_and_a_window_only_when_named(self):
        # a share reads "N%" "of <window>"; a count (unit 'times') reads "N times", and with no window no line under it
        self.assertNotIn("t.measured.value + '%'", self.js)
        self.assertNotIn("'of ' + t.measured.basis", self.js)
        self.assertIn("times = m.unit === 'times'", self.js)
        self.assertIn("times ? m.value + ' times' : m.value + '%'", self.js)
        self.assertIn("if (m.basis) pop.appendChild(add('div', 't-h', (times ? 'in ' : 'of ') + m.basis));", self.js)

    def test_the_number_says_what_it_is(self):
        self.assertNotIn('aria-haspopup', self.js)                             # says nothing on a bare span
        self.assertIn("el.setAttribute('role', 'button')", self.js)
        self.assertIn("el.setAttribute('aria-describedby', 'opt-pop')", self.js)


if __name__ == '__main__':
    unittest.main()
