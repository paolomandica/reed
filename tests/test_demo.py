from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from reed.cli import _sample_article_path, main
from reed.inputs import extract_from_markdown
from reed.models import SectionType


class SampleArticleTests(unittest.TestCase):
    def test_bundled_sample_resolves_and_parses(self) -> None:
        sample = _sample_article_path()

        self.assertTrue(sample.is_file(), sample)
        self.assertEqual(sample.name, "reed-demo.md")

        article = extract_from_markdown(sample)
        self.assertEqual(
            article.title, "How reed turns a saved article into an audiobook"
        )
        self.assertEqual(article.author, "Paolo Mandica")

        types = [section.type for section in article.sections]
        self.assertIn(SectionType.HEADING, types)
        self.assertIn(SectionType.BLOCKQUOTE, types)
        self.assertIn(SectionType.LIST_ITEM, types)
        headings = [
            section.text
            for section in article.sections
            if section.type == SectionType.HEADING
        ]
        self.assertGreaterEqual(len(headings), 3)


class DemoCommandTests(unittest.TestCase):
    def test_demo_no_audiobook_generates_epub_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory) / "out"
            result = CliRunner().invoke(
                main, ["demo", "--no-audiobook", "--output-dir", str(out_dir)]
            )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("Audiobook: skipped", result.output)

            epub = out_dir / "how-reed-turns-a-saved-article-into-an-audiobook.epub"
            md = epub.with_suffix(".md")
            self.assertTrue(epub.is_file())
            self.assertTrue(md.is_file())
            self.assertTrue(epub.read_bytes().startswith(b"PK"))
            self.assertIn(
                "How reed turns a saved article into an audiobook",
                md.read_text(encoding="utf-8"),
            )
            self.assertFalse(epub.with_suffix(".m4b").exists())

    def test_demo_rejects_invalid_speed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = CliRunner().invoke(
                main,
                [
                    "demo",
                    "--speed",
                    "3.0",
                    "--output-dir",
                    str(Path(directory) / "out"),
                ],
            )

        self.assertEqual(result.exit_code, 1)
        self.assertIn("--speed must be between 0.5 and 2.0", result.output)


if __name__ == "__main__":
    unittest.main()
