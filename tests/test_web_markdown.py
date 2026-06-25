"""scry_web/static/markdown.js — the dependency-free Markdown renderer, exercised
through Node against the real source file. Tables, indented code fences, and HTML/
attribute escaping all regressed here, so they get direct coverage. Skips cleanly
when node isn't installed, keeping the suite hermetic and dependency-optional."""
import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
NODE = shutil.which("node")
DRIVER = os.path.join(HERE, "markdown_render.mjs")
BT = "`"  # backtick, kept out of the source so it can't confuse editors


@unittest.skipUnless(NODE, "node not installed — skipping JS markdown renderer tests")
class MarkdownRendererTest(unittest.TestCase):
    def render(self, *inputs):
        p = subprocess.run([NODE, DRIVER], input=json.dumps(list(inputs)),
                           capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    # -- tables ------------------------------------------------------------ #
    def test_table_renders_with_header_and_cells(self):
        (html,) = self.render("| A | B |\n|---|---|\n| 1 | 2 |")
        self.assertIn("<table>", html)
        self.assertIn("table-wrap", html)  # scroll wrapper keeps table semantics
        self.assertRegex(html, r"<th[^>]*>A</th>")
        self.assertRegex(html, r"<td[^>]*>1</td>")

    def test_table_alignment(self):
        (html,) = self.render("| L | C | R |\n|:--|:--:|--:|\n| a | b | c |")
        self.assertIn("text-align:center", html)
        self.assertIn("text-align:right", html)

    def test_table_after_text_line_with_no_blank(self):
        # The common "Here is the comparison:" + table (no blank line) shape.
        (html,) = self.render("Here is the comparison:\n| a | b |\n| --- | --- |\n| 1 | 2 |")
        self.assertIn("Here is the comparison", html)
        self.assertIn("<table>", html)
        self.assertRegex(html, r"<th[^>]*>a</th>")

    def test_pipe_inside_code_span_stays_one_cell(self):
        (html,) = self.render("| cmd | note |\n|---|---|\n| " + BT + "a|b" + BT + " | x |")
        self.assertIn("<code>a|b</code>", html)
        self.assertEqual(html.count("<td"), 2)  # not split into a phantom 3rd column

    def test_escaped_pipe_in_cell(self):
        (html,) = self.render("| A | B |\n|---|---|\n| a \\| b | c |")
        self.assertIn("a | b", html)

    def test_list_item_with_pipe_is_not_a_table(self):
        (html,) = self.render("- a | b\n|---|---|")
        self.assertIn("<li>", html)
        self.assertNotIn("<th", html)

    def test_pipeless_rule_stays_hr(self):
        (html,) = self.render("para\n\n---\n\nmore")
        self.assertIn("<hr>", html)
        self.assertNotIn("<table>", html)

    # -- fenced code ------------------------------------------------------- #
    def test_indented_fence_under_list_item(self):
        (html,) = self.render(
            "1. do this:\n    " + BT * 3 + "python\n    x = 1\n    y = 2\n    " + BT * 3)
        self.assertIn("<pre><code>", html)
        self.assertIn("x = 1", html)
        self.assertNotIn(BT * 3, html)  # the fence markers are consumed

    def test_plain_fence(self):
        (html,) = self.render(BT * 3 + "js\nconst a = 1;\n" + BT * 3)
        self.assertIn("<pre><code>", html)
        self.assertIn("const a = 1;", html)

    # -- escaping / XSS ---------------------------------------------------- #
    def test_html_and_quotes_escaped_in_text(self):
        (html,) = self.render('say "hi" & <tag>')
        self.assertIn("&quot;hi&quot;", html)
        self.assertIn("&amp;", html)
        self.assertIn("&lt;tag&gt;", html)

    def test_link_quote_cannot_break_out_of_href(self):
        (html,) = self.render('[click](https://e.com/" onmouseover=alert(1) x=")')
        self.assertIn("&quot;", html)               # the quote is neutralized
        self.assertNotIn('e.com/" onmouseover', html)  # no raw attribute breakout

    def test_link_ampersand_single_escaped(self):
        (html,) = self.render("[x](https://a.com/?a=1&b=2)")
        self.assertIn("a=1&amp;b=2", html)
        self.assertNotIn("&amp;amp;", html)  # not double-encoded

    def test_non_http_link_neutralized(self):
        (html,) = self.render("[x](javascript:alert(1))")
        self.assertIn('href="#"', html)

    # -- code-span sentinel (regression guard for the \0 placeholder) ------ #
    def test_bare_number_with_code_span_does_not_corrupt(self):
        (html,) = self.render("We have 3 options and " + BT + "code" + BT + " here.")
        self.assertIn("We have 3 options", html)   # the bare "3" stays text
        self.assertIn("<code>code</code>", html)


if __name__ == "__main__":
    unittest.main()
