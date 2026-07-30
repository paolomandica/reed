"""Extract structured article content from common Markdown documents.

The parser accepts both Markdown produced by :mod:`reed.outputs.markdown`
and ordinary article Markdown. ``markdown-it-py`` provides the block
structure; this keeps Markdown syntax such as links and code fences from
leaking into audiobook narration.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from markdown_it.token import Token
from markdownify import markdownify

from ..models import Article, ArticleMetadata, ContentSection, SectionType

logger = logging.getLogger(__name__)

_LABEL_RE = re.compile(r"^(title|author|by|year|date|source)\s*:\s*(.+)$", re.I)
_HANDLE_RE = re.compile(r"\s*\(@(?P<handle>[^)]+)\)\s*$")
_BYLINE_RE = re.compile(r"^by\s+(.+)$", re.I)
_EMBEDDED_HTML_SKIP_TAGS = {
    "aside", "audio", "button", "canvas", "embed", "footer", "form", "iframe", "input",
    "nav", "noscript", "object", "option", "script", "select", "style", "svg",
    "template", "textarea", "video",
}


@dataclass(frozen=True)
class _MarkdownBlock:
    """A prose or structural block extracted from the Markdown token stream."""

    kind: str
    text: str = ""
    level: int = 0


def _markdown_parser() -> MarkdownIt:
    """Return the CommonMark parser with tables enabled for omission."""
    return MarkdownIt("commonmark", {"html": True}).enable("table")


def _normalise_whitespace(text: str) -> str:
    return " ".join(text.split())


def _html_fragment_to_markdown(html: str) -> str:
    """Sanitize an embedded HTML block and normalize it through Markdown."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_EMBEDDED_HTML_SKIP_TAGS):
        tag.decompose()
    return markdownify(str(soup), heading_style="ATX")


def _inline_html_to_text(html: str) -> str:
    """Keep useful inline HTML semantics without exposing tags to TTS."""
    soup = BeautifulSoup(html, "html.parser")
    image = soup.find("img")
    if image:
        return image.get("alt", "")
    if soup.find("br"):
        return " "
    return ""


def _inline_to_text(token: Token) -> str:
    """Turn inline tokens into speakable prose.

    Formatting and URLs are represented by structural tokens and are therefore
    omitted. Link labels and image alt text are ordinary child tokens and are
    retained. Inline code is deliberately ignored, just like fenced code.
    """
    pieces: list[str] = []
    for child in token.children or []:
        if child.type in {"text", "text_special"}:
            pieces.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            pieces.append(" ")
        elif child.type == "image":
            # markdown-it stores parsed alt text in the image's children.
            pieces.append(_inline_to_text(child))
        elif child.type == "html_inline":
            pieces.append(_inline_html_to_text(child.content))
        # link/emphasis/strong tokens are structural; code is intentionally
        # non-prose for this audiobook-oriented parser.
    return _normalise_whitespace("".join(pieces))


def _blocks_from_tokens(
    tokens: list[Token], parser: MarkdownIt
) -> list[_MarkdownBlock]:
    """Extract ordered prose blocks while retaining metadata boundaries."""
    blocks: list[_MarkdownBlock] = []
    blockquote_depth = 0
    list_item_depth = 0
    table_depth = 0

    for index, token in enumerate(tokens):
        if token.type == "blockquote_open":
            blockquote_depth += 1
            continue
        if token.type == "blockquote_close":
            blockquote_depth -= 1
            continue
        if token.type == "list_item_open":
            list_item_depth += 1
            continue
        if token.type == "list_item_close":
            list_item_depth -= 1
            continue
        if token.type == "table_open":
            table_depth += 1
            blocks.append(_MarkdownBlock("non_prose"))
            continue
        if token.type == "table_close":
            table_depth -= 1
            continue
        if table_depth:
            continue

        if token.type == "hr":
            blocks.append(_MarkdownBlock("rule"))
            continue
        if token.type == "html_block":
            normalized = _html_fragment_to_markdown(token.content)
            if normalized.strip():
                blocks.extend(_blocks_from_tokens(parser.parse(normalized), parser))
            continue
        if token.type in {"fence", "code_block"}:
            blocks.append(_MarkdownBlock("non_prose"))
            continue
        if token.type not in {"heading_open", "paragraph_open"}:
            continue

        inline = tokens[index + 1] if index + 1 < len(tokens) else None
        if inline is None or inline.type != "inline":
            continue
        text = _inline_to_text(inline)
        if not text:
            continue

        # Markdown soft-wraps adjacent metadata lines into one paragraph.
        # Split only a paragraph made entirely of recognised metadata fields;
        # ordinary multi-line prose remains a single paragraph.
        metadata_lines = [line.strip() for line in inline.content.splitlines() if line.strip()]
        if (
            token.type == "paragraph_open"
            and len(metadata_lines) > 1
            and all(_BYLINE_RE.match(line) or _LABEL_RE.match(line) for line in metadata_lines)
        ):
            blocks.extend(_MarkdownBlock("paragraph", line) for line in metadata_lines)
            continue

        if token.type == "heading_open":
            blocks.append(
                _MarkdownBlock("heading", text, level=int(token.tag.removeprefix("h")))
            )
        elif blockquote_depth:
            blocks.append(_MarkdownBlock("blockquote", text))
        elif list_item_depth:
            blocks.append(_MarkdownBlock("list_item", text))
        else:
            blocks.append(_MarkdownBlock("paragraph", text))

    return blocks


def _clean_title(text: str) -> str:
    """Remove the conventional ``Title:`` label without changing real titles."""
    match = _LABEL_RE.match(text)
    if match and match.group(1).lower() == "title":
        return match.group(2).strip()
    return text.strip()


def _normalise_date(value: str) -> str:
    """Return ISO dates emitted by reed, while preserving unknown date text."""
    value = value.strip()
    try:
        return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def _parse_author(value: str) -> tuple[str, str, str]:
    """Return author, handle, and optional date from a byline value."""
    value = value.strip()
    date = ""
    for separator in (" — ", " – ", " - "):
        if separator in value:
            value, date = (part.strip() for part in value.split(separator, 1))
            break

    handle_match = _HANDLE_RE.search(value)
    handle = handle_match.group("handle").strip() if handle_match else ""
    if handle_match:
        value = value[: handle_match.start()].strip()
    return value, handle, date


def _consume_metadata(blocks: list[_MarkdownBlock]) -> tuple[ArticleMetadata, int]:
    """Read a leading title/metadata prologue and return the body offset.

    Metadata is intentionally accepted only after the document's leading H1.
    This avoids treating a later ``## Author: ...`` article heading as front
    matter. A horizontal rule ends a reed-style metadata block.
    """
    title = ""
    author = "Unknown"
    author_handle = ""
    date: str | None = None
    url = ""
    index = 0

    if blocks and blocks[0].kind == "heading" and blocks[0].level == 1:
        title = _clean_title(blocks[0].text)
        index = 1
    else:
        return ArticleMetadata(title=title, author=author), index

    while index < len(blocks):
        block = blocks[index]
        if block.kind == "rule":
            index += 1
            break
        if block.kind not in {"heading", "paragraph"}:
            break

        byline_match = _BYLINE_RE.match(block.text)
        label_match = _LABEL_RE.match(block.text)
        if byline_match:
            parsed_author, parsed_handle, parsed_date = _parse_author(
                byline_match.group(1)
            )
            if parsed_author:
                author = parsed_author
            if parsed_handle:
                author_handle = parsed_handle
            if parsed_date:
                date = _normalise_date(parsed_date)
        elif label_match:
            label = label_match.group(1).lower()
            value = label_match.group(2).strip()
            if label in {"author", "by"}:
                parsed_author, parsed_handle, parsed_date = _parse_author(value)
                if parsed_author:
                    author = parsed_author
                if parsed_handle:
                    author_handle = parsed_handle
                if parsed_date:
                    date = _normalise_date(parsed_date)
            elif label in {"year", "date"}:
                date = _normalise_date(value)
            elif label == "source":
                url = value
            else:
                break
        else:
            break
        index += 1

    return (
        ArticleMetadata(
            title=title,
            author=author,
            author_handle=author_handle,
            date=date,
            language="en",
            url=url,
        ),
        index,
    )


def _sections_from_blocks(blocks: list[_MarkdownBlock]) -> list[ContentSection]:
    sections: list[ContentSection] = []
    section_types = {
        "heading": SectionType.HEADING,
        "paragraph": SectionType.PARAGRAPH,
        "blockquote": SectionType.BLOCKQUOTE,
        "list_item": SectionType.LIST_ITEM,
    }
    for block in blocks:
        section_type = section_types.get(block.kind)
        if section_type is not None and block.text:
            sections.append(
                ContentSection(type=section_type, text=block.text, level=block.level)
            )
    return sections


def extract_from_markdown(md_path: Path) -> Article:
    """Parse a Markdown article into structured metadata and speakable sections."""
    text = md_path.read_text(encoding="utf-8")
    parser = _markdown_parser()
    blocks = _blocks_from_tokens(parser.parse(text), parser)
    metadata, content_start = _consume_metadata(blocks)

    if not metadata.title:
        metadata.title = md_path.stem

    sections = _sections_from_blocks(blocks[content_start:])

    logger.info(
        "Extracted from Markdown: title=%r, author=%r, sections=%d",
        metadata.title,
        metadata.author,
        len(sections),
    )
    return Article(metadata=metadata, sections=sections)
