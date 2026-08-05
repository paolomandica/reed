from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ebooklib import epub

from reed.models import Article, ArticleMetadata, ContentSection, SectionType
from reed.outputs.epub import generate_epub


class EpubGenerationTests(unittest.TestCase):
    def _make_article(self) -> Article:
        return Article(
            metadata=ArticleMetadata(
                title="My Test Book",
                author="Jane Doe",
                date="2024-01-02",
                language="en",
            ),
            sections=[
                ContentSection(type=SectionType.HEADING, text="Chapter One", level=2),
                ContentSection(type=SectionType.PARAGRAPH, text="Hello world."),
            ],
        )

    def test_epub_round_trip_metadata_and_toc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "book.epub"
            generate_epub(self._make_article(), output)

            self.assertTrue(output.exists())
            self.assertTrue(output.read_bytes().startswith(b"PK"))

            book = epub.read_epub(str(output))
            self.assertEqual(book.get_metadata("DC", "title")[0][0], "My Test Book")
            self.assertEqual(book.get_metadata("DC", "language")[0][0], "en")
            creators = [item[0] for item in book.get_metadata("DC", "creator")]
            self.assertTrue(any("Jane Doe" in creator for creator in creators))

            self.assertEqual(len(book.toc), 1)
            self.assertEqual(book.toc[0].title, "Chapter One")
            self.assertEqual(book.toc[0].href, "content.xhtml")

            item_names = [item.get_name() for item in book.get_items()]
            self.assertIn("content.xhtml", item_names)
            self.assertIn("title_page.xhtml", item_names)

    def test_epub_escapes_article_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "escaped.epub"
            article = Article(
                metadata=ArticleMetadata(title="A & B <Book>", author="X"),
                sections=[
                    ContentSection(type=SectionType.PARAGRAPH, text="1 < 2 & 3")
                ],
            )
            generate_epub(article, output)

            book = epub.read_epub(str(output))
            items = {item.get_name(): item.get_content().decode("utf-8") for item in book.get_items()}
            self.assertIn("A &amp; B &lt;Book&gt;", items["title_page.xhtml"])
            self.assertIn("1 &lt; 2 &amp; 3", items["content.xhtml"])
