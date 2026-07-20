"""Extract article content from Markdown files produced by reed.

Handles the Markdown format that ``generate_markdown`` produces, so users
can round-trip: download → edit → regenerate EPUB/audiobook.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..models import Article, ArticleMetadata, ContentSection, SectionType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns for the metadata header
# ---------------------------------------------------------------------------

_AUTHOR_RE = re.compile(
    r"^\*By\s+(.+?)(?:\s*\(@([^)]+)\))?\s*(?:[—\-]\s*(.+?))?\*$"
)
_SOURCE_RE = re.compile(r"^Source:\s+(.+)$")


def _parse_metadata_header(lines: list[str]) -> tuple[ArticleMetadata, int]:
    """Parse the reed metadata block from the top of a Markdown file.

    Returns:
        ``(metadata, content_start_index)`` — *content_start_index* is the
        line number (0-based) where body content begins.
    """
    title = ""
    author = "Unknown"
    author_handle = ""
    date = ""
    url = ""
    description = ""

    i = 0

    # First H1 is the title
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("# ") and not line.startswith("## "):
            title = line[2:].strip()
            i += 1
            break
        i += 1

    # Parse remaining metadata lines until --- separator or content
    while i < len(lines):
        line = lines[i].strip()

        # Separator ends the metadata block
        if line == "---":
            i += 1
            break

        # Author line: *By Author (@handle) — Date*
        author_match = _AUTHOR_RE.match(line)
        if author_match:
            author = author_match.group(1).strip() or author
            author_handle = (author_match.group(2) or "").strip()
            date_str = (author_match.group(3) or "").strip()
            if date_str:
                date = date_str
            i += 1
            continue

        # Source line
        source_match = _SOURCE_RE.match(line)
        if source_match:
            url = source_match.group(1).strip()
            i += 1
            continue

        # Empty lines — skip
        if not line:
            i += 1
            continue

        # If we encounter content before seeing ---, break and treat
        # everything from here as body content
        if line.startswith(("## ", "### ", "#### ", "> ", "- ", "* ")):
            break

        # Any other non-empty line — stop metadata scanning and treat
        # this line (and everything after) as body content
        break

    # If we never found a title, use the filename later
    metadata = ArticleMetadata(
        title=title,
        author=author,
        author_handle=author_handle,
        date=date if date else None,
        language="en",
        description=description,
        url=url,
    )

    return metadata, i


# ---------------------------------------------------------------------------
# Content section parsing
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$")
_UNORDERED_LIST_RE = re.compile(r"^[-*]\s+(.+)$")


def _parse_content_sections(lines: list[str], start: int) -> list[ContentSection]:
    """Parse body content from *lines* starting at index *start*.

    Consecutive blockquote lines are merged; consecutive list items are kept
    as individual sections.
    """
    sections: list[ContentSection] = []

    i = start
    while i < len(lines):
        line = lines[i]

        # Skip blank lines
        if not line.strip():
            i += 1
            continue

        # Heading
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            if text:
                sections.append(
                    ContentSection(type=SectionType.HEADING, text=text, level=level)
                )
            i += 1
            continue

        # Blockquote — merge consecutive lines
        bq_match = _BLOCKQUOTE_RE.match(line)
        if bq_match:
            bq_lines: list[str] = []
            while i < len(lines):
                bq_line_match = _BLOCKQUOTE_RE.match(lines[i])
                if not bq_line_match:
                    break
                bq_lines.append(bq_line_match.group(1).strip())
                i += 1
            text = " ".join(bq_lines)
            if text:
                sections.append(
                    ContentSection(type=SectionType.BLOCKQUOTE, text=text)
                )
            continue

        # List item (unordered)
        ul_match = _UNORDERED_LIST_RE.match(line)
        if ul_match:
            text = ul_match.group(1).strip()
            if text:
                sections.append(
                    ContentSection(type=SectionType.LIST_ITEM, text=text)
                )
            i += 1
            continue

        # Paragraph — accumulate consecutive non-blank, non-special lines
        para_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            cur = lines[i].strip()
            # Stop at structural elements
            if (
                _HEADING_RE.match(cur)
                or _BLOCKQUOTE_RE.match(cur)
                or _UNORDERED_LIST_RE.match(cur)
                or cur == "---"
            ):
                break
            para_lines.append(cur)
            i += 1

        text = " ".join(para_lines)
        if text:
            sections.append(ContentSection(type=SectionType.PARAGRAPH, text=text))

    return sections


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_from_markdown(md_path: Path) -> Article:
    """Parse a Markdown file (as produced by ``generate_markdown``) into an Article.

    Args:
        md_path: Path to the ``.md`` file.

    Returns:
        A structured ``Article`` ready for EPUB, audiobook, or re-export.
    """
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    metadata, content_start = _parse_metadata_header(lines)

    # If no title found, use the filename stem
    if not metadata.title:
        metadata.title = md_path.stem

    sections = _parse_content_sections(lines, content_start)

    # If no sections were parsed, try a simpler fallback: treat everything
    # after the metadata block as a single body paragraph.
    if not sections:
        body_lines = [l.strip() for l in lines[content_start:] if l.strip()]
        if body_lines:
            body_text = " ".join(body_lines)
            sections.append(
                ContentSection(type=SectionType.PARAGRAPH, text=body_text)
            )

    logger.info(
        "Extracted from Markdown: title=%r, author=%r, sections=%d",
        metadata.title,
        metadata.author,
        len(sections),
    )

    return Article(metadata=metadata, sections=sections)
