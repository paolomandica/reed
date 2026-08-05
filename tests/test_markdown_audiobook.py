from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from reed.inputs import extract_from_html
from reed.inputs.html_file import is_prose_preformatted_text
from reed.inputs.markdown_file import extract_from_markdown
from reed.models import Article, ArticleMetadata, ContentSection, SectionType
from reed.outputs.audiobook import (
    _concat_audio,
    article_text_for_tts,
    narration_segments_for_tts,
)
from reed.outputs.markdown import generate_markdown

ROOT = Path(__file__).resolve().parents[1]


class MarkdownParsingTests(unittest.TestCase):
    def test_included_book_metadata_is_not_body_or_narration(self) -> None:
        article = extract_from_markdown(ROOT / "tests" / "fixtures" / "the-man.md")

        self.assertEqual(article.title, "The Man in the Brown Suit")
        self.assertEqual(article.author, "Agatha Christie")
        self.assertEqual(article.metadata.date, "1924")
        self.assertEqual(article.sections[0].text, "Prologue")
        self.assertNotIn("Author: Agatha Christie", [s.text for s in article.sections])
        self.assertNotIn("Year: 1924", [s.text for s in article.sections])

        narration = article_text_for_tts(article, 500)
        self.assertEqual(narration[:3], [
            "The Man in the Brown Suit.",
            "By Agatha Christie.",
            "Prologue.",
        ])
        self.assertNotIn("1924", " ".join(narration))
        self.assertNotIn("-------", " ".join(narration))

    def test_common_markdown_is_rendered_as_readable_prose(self) -> None:
        markdown = '''Readable Title
==============

Author: Ada Lovelace (@ada)
Date: 1843
Source: https://example.test/article

---

## Chapter One

Some **bold** [linked words](https://example.test/link), ![a useful image](image.jpg), and `inline code`.

1. First item
   1. Nested item
2. Second item

> A quoted thought
> continues here.

```python
print("skip this")
```

| Column | Value |
| --- | --- |
| skip | this |
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "article.md"
            path.write_text(markdown, encoding="utf-8")
            article = extract_from_markdown(path)

        self.assertEqual(article.title, "Readable Title")
        self.assertEqual(article.author, "Ada Lovelace")
        self.assertEqual(article.metadata.author_handle, "ada")
        self.assertEqual(article.metadata.date, "1843")
        self.assertEqual(article.metadata.url, "https://example.test/article")
        self.assertEqual(article.sections[0].text, "Chapter One")
        self.assertEqual(article.sections[1].text, "Some bold linked words, a useful image, and .")
        self.assertEqual(
            [section.text for section in article.sections if section.type == SectionType.LIST_ITEM],
            ["First item", "Nested item", "Second item"],
        )
        self.assertIn(
            "A quoted thought continues here.",
            [section.text for section in article.sections if section.type == SectionType.BLOCKQUOTE],
        )
        all_text = " ".join(section.text for section in article.sections)
        self.assertNotIn("skip this", all_text)
        self.assertNotIn("Column", all_text)
        self.assertNotIn("https://example.test/link", all_text)

    def test_embedded_html_is_normalized_before_section_parsing(self) -> None:
        markdown = '''# HTML in Markdown

<section>
  <h2>Embedded heading</h2>
  <p>Embedded <strong>prose</strong> survives.</p>
  <ul><li>First item</li><li>Second item</li></ul>
  <img src="diagram.png" alt="A useful diagram">
  <script>alert("never narrate")</script>
  <nav>Navigation noise</nav>
</section>

Inline <em>markup</em> with <img src="map.png" alt="a map"> stays readable.
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "article.md"
            path.write_text(markdown, encoding="utf-8")
            article = extract_from_markdown(path)

        self.assertEqual([(section.type, section.text) for section in article.sections], [
            (SectionType.HEADING, "Embedded heading"),
            (SectionType.PARAGRAPH, "Embedded prose survives."),
            (SectionType.LIST_ITEM, "First item"),
            (SectionType.LIST_ITEM, "Second item"),
            (SectionType.PARAGRAPH, "A useful diagram"),
            (SectionType.PARAGRAPH, "Inline markup with a map stays readable."),
        ])
        narration = " ".join(article_text_for_tts(article, 500))
        self.assertNotIn("alert", narration)
        self.assertNotIn("Navigation noise", narration)
        self.assertNotIn("<", narration)

    def test_reed_markdown_round_trip_preserves_metadata(self) -> None:
        source = Article(
            metadata=ArticleMetadata(
                title="Round Trip",
                author="Ada Lovelace",
                author_handle="ada",
                date="1843-01-01",
                url="https://example.test/round-trip",
            ),
            sections=[ContentSection(SectionType.HEADING, "Introduction", 2)],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = generate_markdown(source, Path(directory) / "article.md")
            article = extract_from_markdown(path)

        self.assertEqual(article.title, "Round Trip")
        self.assertEqual(article.author, "Ada Lovelace")
        self.assertEqual(article.metadata.author_handle, "ada")
        self.assertEqual(article.metadata.date, "1843-01-01")
        self.assertEqual(article.metadata.url, "https://example.test/round-trip")
        self.assertEqual([(s.type, s.text) for s in article.sections], [
            (SectionType.HEADING, "Introduction"),
        ])

    def test_missing_title_uses_the_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "untitled-article.md"
            path.write_text("Plain body text.", encoding="utf-8")
            article = extract_from_markdown(path)

        self.assertEqual(article.title, "untitled-article")
        self.assertEqual(article.sections[0].text, "Plain body text.")


class HtmlArticleExtractionTests(unittest.TestCase):
    def test_prose_pre_article_round_trips_without_page_chrome(self) -> None:
        html = """<!doctype html>
<title>Distribution changes - &lt;antirez&gt;</title>
<div id="container"><div id="content">
  <section id="newslist"><article><h2>Distribution changes</h2></article></section>
  <topcomment><article class="comment">
    <span class="info"><a href="/user/antirez">antirez</a> 7 days ago. 42 views.</span>
    <pre>Software distribution is changing rapidly as development tools become more capable. Teams can test branches earlier and adapt them for their specific systems. This paragraph intentionally has enough natural language to be prose rather than source code.</pre>
  </article></topcomment>
  <div id="disqus_thread_outdiv"><a class="dsq-brlink">blog comments powered by Disqus</a></div>
</div></div>"""
        with tempfile.TemporaryDirectory() as directory:
            html_path = Path(directory) / "article.html"
            markdown_path = Path(directory) / "article.md"
            html_path.write_text(html, encoding="utf-8")
            article = extract_from_html(html_path)
            generate_markdown(article, markdown_path)
            roundtrip = extract_from_markdown(markdown_path)

        self.assertEqual(article.title, "Distribution changes - <antirez>")
        self.assertEqual(article.author, "antirez")
        self.assertNotEqual(article.title, "antirez site header")
        self.assertEqual(article.sections[-1].type, SectionType.PARAGRAPH)
        self.assertIn("Software distribution is changing", article.sections[-1].text)
        self.assertNotIn("views", article.html_body.lower())
        self.assertNotIn("disqus", article.html_body.lower())
        self.assertEqual(roundtrip.title, article.title)
        self.assertEqual(roundtrip.author, article.author)
        self.assertEqual(len(roundtrip.sections), 1)
        self.assertIn("Software distribution is changing", roundtrip.sections[0].text)

    def test_json_ld_author_precedes_profile_url_metadata(self) -> None:
        html = """<!doctype html>
<title>Structured author</title>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "NewsArticle", "author": {"@type": "Person", "name": "Ada Lovelace"}}
</script>
<article><p>A sufficiently long article paragraph contains several ordinary sentences. It exists so the content extractor can select a real article body without reading page metadata as prose.</p></article>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "article.html"
            path.write_text(html, encoding="utf-8")
            article = extract_from_html(path)

        self.assertEqual(article.author, "Ada Lovelace")

    def test_code_like_preformatted_text_remains_non_prose(self) -> None:
        code = """def render(value):
    result = value + 1
    return result

for item in values:
    print(render(item))
"""
        prose = """This is a long enough natural-language paragraph to be treated as prose. It has two complete sentences and does not contain source code syntax or programming control flow."""

        self.assertFalse(is_prose_preformatted_text(code))
        self.assertTrue(is_prose_preformatted_text(prose))


class NarrationSegmentationTests(unittest.TestCase):
    def test_title_author_and_duplicate_heading_are_handled_once(self) -> None:
        article = Article(
            metadata=ArticleMetadata(title="My Story", author="Ada"),
            sections=[
                ContentSection(SectionType.HEADING, "My Story", 1),
                ContentSection(SectionType.HEADING, "Part One", 2),
                ContentSection(SectionType.PARAGRAPH, "A paragraph."),
                ContentSection(SectionType.LIST_ITEM, "A list entry"),
            ],
        )

        segments = narration_segments_for_tts(article, 500)
        self.assertEqual([segment.text for segment in segments], [
            "My Story.",
            "By Ada.",
            "Part One.",
            "A paragraph.",
            "A list entry",
        ])
        self.assertEqual([segment.pause_after_ms for segment in segments], [
            1000, 1000, 1000, 500, 250,
        ])

    def test_long_units_respect_the_character_limit_without_internal_pause(self) -> None:
        article = Article(
            metadata=ArticleMetadata(title="T", author="Unknown"),
            sections=[ContentSection(
                SectionType.PARAGRAPH,
                "One short sentence. Another short sentence. A veryveryveryveryveryverylongword.",
            )],
        )

        segments = narration_segments_for_tts(article, 20)
        self.assertTrue(all(len(segment.text) <= 20 for segment in segments))
        self.assertEqual(segments[0].text, "T.")
        self.assertEqual(segments[0].pause_after_ms, 1000)
        self.assertEqual([segment.pause_after_ms for segment in segments[1:-1]], [0] * (len(segments) - 2))
        self.assertEqual(segments[-1].pause_after_ms, 500)

    def test_concat_honors_per_segment_pauses(self) -> None:
        sample_rate, audio = _concat_audio([
            (10, np.array([1.0, 1.0]), 100),
            (10, np.array([2.0, 2.0]), 0),
        ])

        self.assertEqual(sample_rate, 10)
        self.assertEqual(audio.tolist(), [1.0, 1.0, 0.0, 2.0, 2.0])


if __name__ == "__main__":
    unittest.main()
