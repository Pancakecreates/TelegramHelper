import unittest

from src.core.text_sanitizer import sanitize_html


class TestTextSanitizer(unittest.TestCase):
    def test_erb_or_asp_tags(self):
        # Unsupported start tag <%= tag %> in LLM output
        raw = "1) <b>Где мы остановились</b> — <%= tag %>"
        result = sanitize_html(raw)
        self.assertIn("&lt;%= tag %&gt;", result)
        self.assertIn("<b>Где мы остановились</b>", result)

    def test_unclosed_tags(self):
        raw = "<b>unclosed bold"
        result = sanitize_html(raw)
        self.assertEqual(result, "<b>unclosed bold</b>")

    def test_orphan_closing_tag(self):
        raw = "hello </i> world"
        result = sanitize_html(raw)
        self.assertEqual(result, "hello  world")

    def test_code_fence(self):
        raw = "```python\nx = 1 < 2\n```"
        result = sanitize_html(raw)
        self.assertIn('<pre><code class="language-python">x = 1 &lt; 2</code></pre>', result)

    def test_a_tag_without_href(self):
        raw = "<a>link without href</a>"
        result = sanitize_html(raw)
        self.assertNotIn("<a>", result)

    def test_a_tag_with_href(self):
        raw = '<a href="https://example.com">link</a>'
        result = sanitize_html(raw)
        self.assertEqual(result, '<a href="https://example.com">link</a>')

    def test_nbsp_entity(self):
        raw = "word1&nbsp;word2"
        result = sanitize_html(raw)
        self.assertEqual(result, "word1 word2")

    def test_unescaped_angle_brackets(self):
        raw = "if 5 < 10 and 10 > 5"
        result = sanitize_html(raw)
        self.assertEqual(result, "if 5 &lt; 10 and 10 &gt; 5")


if __name__ == "__main__":
    unittest.main()
