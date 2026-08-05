from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from reed.inputs import extract_from_html
from reed.models import SectionType


class SubstackStyleArticleTests(unittest.TestCase):
    """A Substack-like page: JSON-LD metadata, byline, and an article body."""

    HTML = """<!doctype html>
<html lang="en">
<head>
<meta property="og:title" content="The Aged Mother by Matsuo Basho">
<meta property="article:published_time" content="2024-01-15">
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "NewsArticle", "headline": "The Aged Mother by Matsuo Basho", "author": {"@type": "Person", "name": "Matsuo Basho"}, "datePublished": "2024-01-15"}
</script>
</head>
<body>
<header><nav><a href="/archive">Archive</a></nav></header>
<main>
  <div class="post">
    <h1>The Aged Mother by Matsuo Basho</h1>
    <div class="byline">Matsuo Basho · Jan 15, 2024</div>
    <article class="post-body">
      <p>An old mother climbs the mountain path with her son, and the evening snow begins to fall around them.</p>
      <h2>The Parable</h2>
      <p>The son carries his mother on his back, remembering all the years she carried him.</p>
    </article>
  </div>
</main>
<footer><p>© 2024 Example</p></footer>
</body>
</html>"""

    def test_metadata_and_body_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "substack.html"
            path.write_text(self.HTML, encoding="utf-8")
            article = extract_from_html(path)

        self.assertEqual(article.title, "The Aged Mother by Matsuo Basho")
        self.assertEqual(article.author, "Matsuo Basho")
        self.assertEqual(article.metadata.date, "2024-01-15")

        texts = [section.text for section in article.sections]
        self.assertIn("The Parable", texts)
        self.assertTrue(any(section.type == SectionType.PARAGRAPH for section in article.sections))

        body_lower = article.html_body.lower()
        self.assertNotIn("archive", body_lower)
        self.assertNotIn("© 2024", body_lower)


class GenericBlogTests(unittest.TestCase):
    """A typical personal blog: meta author/date and an <article> body."""

    HTML = """<!doctype html>
<html>
<head>
<title>How I Learned to Ship Software</title>
<meta name="author" content="Jane Doe">
<meta name="article:published_time" content="2023-06-01">
</head>
<body>
<div class="site-header">Home · About · Archive</div>
<main class="content">
  <article>
    <h1>How I Learned to Ship Software</h1>
    <p class="byline">By Jane Doe on June 1, 2023</p>
    <p>Shipping software is mostly about shipping small things regularly and learning from what breaks.</p>
    <h2>Lessons learned</h2>
    <ul>
      <li>Ship early.</li>
      <li>Ship often.</li>
    </ul>
  </article>
</main>
</body>
</html>"""

    def test_meta_author_date_and_article_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "blog.html"
            path.write_text(self.HTML, encoding="utf-8")
            article = extract_from_html(path)

        self.assertEqual(article.title, "How I Learned to Ship Software")
        self.assertEqual(article.author, "Jane Doe")
        self.assertEqual(article.metadata.date, "2023-06-01")

        texts = [section.text for section in article.sections]
        self.assertIn("Lessons learned", texts)
        self.assertTrue(any("Ship early" in text for text in texts))
        self.assertNotIn("By Jane Doe", texts)
        self.assertNotIn("site-header", article.html_body.lower())


class NewsStyleArticleTests(unittest.TestCase):
    """A news page whose author comes from JSON-LD and date from visible text."""

    HTML = """<!doctype html>
<html>
<head>
<title>City Council Approves Budget</title>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "BlogPosting", "headline": "City Council Approves Budget", "author": {"@type": "Person", "name": "Alex Rivera"}, "datePublished": "2024-03-10"}
</script>
</head>
<body>
<div id="site-nav">Sections · Politics · Local</div>
<article>
  <h1>City Council Approves Budget</h1>
  <p class="byline">By Alex Rivera | March 10, 2024</p>
  <p>The council voted to approve next year's budget after a final round of amendments.</p>
  <p>The plan includes new funding for transit and public libraries across the district.</p>
</article>
</body>
</html>"""

    def test_json_ld_author_and_visible_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "news.html"
            path.write_text(self.HTML, encoding="utf-8")
            article = extract_from_html(path)

        self.assertEqual(article.title, "City Council Approves Budget")
        self.assertEqual(article.author, "Alex Rivera")
        self.assertIsNotNone(article.metadata.date)
        self.assertIn("2024", article.metadata.date)

        texts = [section.text for section in article.sections]
        self.assertTrue(any("transit" in text for text in texts))
        self.assertNotIn("By Alex Rivera", texts)
        self.assertNotIn("site-nav", article.html_body.lower())
