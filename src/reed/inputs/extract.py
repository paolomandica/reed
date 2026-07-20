"""Parse downloaded tweet/thread text into structured content sections.

Handles plain text from APIs (FxTwitter, Syndication) — detects headings,
paragraphs, and other structural elements without needing HTML.
"""

import re
import logging

from ..models import Article, ArticleMetadata, ContentSection, SectionType
from .download import DownloadedContent

logger = logging.getLogger(__name__)

# Max length for a line to be considered a heading
MAX_HEADING_LENGTH = 100

# Lines matching these patterns are likely UI text, not headings
_UI_HEADING_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^(replies?|reposts?|likes?|bookmarks?|views?)\s*\d*$",
        r"^\d+\s+(replies?|reposts?|likes?|bookmarks?|views?)$",
        r"^(reply|repost|like|bookmark|share|view)\b",
        r"^want to publish your own article",
        r"^upgrade to premium",
        r"^trending",
        r"^what.s happening",
        r"^who to follow",
        r"^relevant people",
    ]
]


def _looks_like_heading(line: str) -> bool:
    """Heuristic: does this line look like a section heading?"""
    stripped = line.strip()
    if not stripped:
        return False
    if len(stripped) > MAX_HEADING_LENGTH:
        return False
    # Skip lines that are obviously UI text
    for pattern in _UI_HEADING_PATTERNS:
        if pattern.match(stripped):
            return False
    # Headings typically don't end with sentence punctuation
    if stripped[-1] in {".", ",", ";", ":"}:
        # Allow colon-only endings (common in headings)
        if stripped[-1] == ":":
            return True
        return False
    # Short lines surrounded by blank space are likely headings
    # Check if it's a single sentence-like line
    words = stripped.split()
    if len(words) <= 15:
        return True
    return False


def _detect_heading_level(line: str, prev_headings: dict[str, int]) -> int:
    """Assign a heading level (1-3) based on context and heuristics.

    First heading in the document gets h1 (title-level).
    Subsequent headings get h2 unless they appear to be sub-headings.
    """
    stripped = line.strip().lower().rstrip(":")

    if not prev_headings:
        return 1  # First heading is the main title

    # If we've seen very similar text, it's likely at the same level
    for h_text, h_level in prev_headings.items():
        if stripped == h_text:
            return h_level

    # Default: headings are h2 (sections), unless they seem like sub-sections
    # For now, treat all detected headings as h2 after the first one
    return 2


def extract_content(downloaded: DownloadedContent) -> Article:
    """Parse downloaded content into a structured Article.

    Handles both regular tweets/threads and X Article metadata.
    """
    metadata = ArticleMetadata(
        title=downloaded.title or "Untitled",
        author=downloaded.author_name,
        author_handle=downloaded.author_handle,
        date=downloaded.created_at,
        language=downloaded.language or "en",
        description=downloaded.description or "",
        url=downloaded.source_url or "",
    )

    sections: list[ContentSection] = []
    text = downloaded.text

    if not text:
        # X Article with only metadata (no body downloaded)
        if downloaded.title:
            sections.append(ContentSection(type=SectionType.TITLE, text=downloaded.title, level=1))
            if downloaded.description:
                sections.append(
                    ContentSection(type=SectionType.PARAGRAPH, text=downloaded.description)
                )
        return Article(metadata=metadata, sections=sections)

    # Split text into paragraphs (separated by blank lines)
    raw_blocks = re.split(r"\n\s*\n", text.strip())
    prev_headings: dict[str, int] = {}

    for block in raw_blocks:
        lines = block.strip().split("\n")
        if not lines:
            continue

        # Check if first line looks like a heading
        first_line = lines[0].strip()

        if len(lines) == 1 and _looks_like_heading(first_line):
            level = _detect_heading_level(first_line, prev_headings)
            clean = first_line.rstrip(":")
            sections.append(ContentSection(type=SectionType.HEADING, text=clean, level=level))
            prev_headings[clean.lower().rstrip(":")] = level
            continue

        # Check if it's a blockquote (lines starting with >)
        if all(l.strip().startswith(">") for l in lines if l.strip()):
            clean_lines = [l.strip().lstrip(">").strip() for l in lines if l.strip()]
            text_block = " ".join(clean_lines)
            sections.append(ContentSection(type=SectionType.BLOCKQUOTE, text=text_block))
            continue

        # Check if it's a list (lines starting with - or *)
        if all(
            re.match(r"^\s*[-*]\s", l) for l in lines if l.strip()
        ):
            for line in lines:
                stripped = line.strip()
                if stripped:
                    clean = re.sub(r"^\s*[-*]\s+", "", stripped)
                    sections.append(ContentSection(type=SectionType.LIST_ITEM, text=clean))
            continue

        # Otherwise, it's a paragraph
        clean_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped:
                clean_lines.append(stripped)
        if clean_lines:
            para_text = " ".join(clean_lines)
            sections.append(ContentSection(type=SectionType.PARAGRAPH, text=para_text))

    # If we didn't find a heading as the first section, prepend the title
    if sections and sections[0].type != SectionType.HEADING:
        if metadata.title and metadata.title != "Untitled":
            sections.insert(
                0, ContentSection(type=SectionType.HEADING, text=metadata.title, level=1)
            )

    # If still no title, use first heading or first few words
    if metadata.title == "Untitled" and sections:
        first = sections[0]
        if first.type == SectionType.HEADING:
            metadata.title = first.text
        else:
            # Use first 10 words of first paragraph as title
            words = first.text.split()[:10]
            metadata.title = " ".join(words)

    return Article(metadata=metadata, sections=sections)
