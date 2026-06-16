"""Generate Kindle-compatible EPUB files using EbookLib."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ebooklib import epub

from .models import Article, ContentSection, SectionType
from .css import KINDLE_CSS

logger = logging.getLogger(__name__)


def _section_to_html(section: ContentSection) -> str:
    """Convert a ContentSection to its HTML representation."""
    text = _escape_html(section.text)

    if section.type == SectionType.TITLE:
        return f"<h1>{text}</h1>"
    elif section.type == SectionType.HEADING:
        if section.level <= 1:
            return f"<h1>{text}</h1>"
        elif section.level == 2:
            return f"<h2>{text}</h2>"
        else:
            return f"<h3>{text}</h3>"
    elif section.type == SectionType.PARAGRAPH:
        return f"<p>{text}</p>"
    elif section.type == SectionType.BLOCKQUOTE:
        return f"<blockquote>{text}</blockquote>"
    elif section.type == SectionType.LIST_ITEM:
        return f"<li>{text}</li>"
    else:
        return f"<p>{text}</p>"


def _escape_html(text: str) -> str:
    """Escape text for safe HTML embedding."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _build_content_body(sections: list[ContentSection]) -> str:
    """Build the main content XHTML body from sections."""
    parts: list[str] = []

    # Group consecutive list items
    i = 0
    while i < len(sections):
        section = sections[i]

        if section.type == SectionType.LIST_ITEM:
            parts.append("<ul>")
            while i < len(sections) and sections[i].type == SectionType.LIST_ITEM:
                parts.append(_section_to_html(sections[i]))
                i += 1
            parts.append("</ul>")
        else:
            parts.append(_section_to_html(section))
            i += 1

    return "\n".join(parts)


def _make_title_page(article: Article) -> str:
    """Generate the title page XHTML content."""
    meta = article.metadata
    parts = ['<div class="title-page">']
    parts.append(f"<h1>{_escape_html(meta.title)}</h1>")
    parts.append(f'<p class="author">{_escape_html(meta.to_epub_author())}</p>')
    if meta.date:
        try:
            dt = datetime.fromisoformat(meta.date)
            formatted = dt.strftime("%B %d, %Y")
        except (ValueError, TypeError):
            formatted = meta.date
        parts.append(f'<p class="date">{_escape_html(formatted)}</p>')
    if meta.url:
        parts.append(f'<p class="source">Source: {_escape_html(meta.url)}</p>')
    parts.append("</div>")
    return "\n".join(parts)


def generate_epub(article: Article, output_path: Path) -> Path:
    """Generate a Kindle-compatible EPUB from an Article.

    Args:
        article: The structured article with metadata and content.
        output_path: Where to write the .epub file.

    Returns:
        The path to the generated EPUB file.
    """
    book = epub.EpubBook()

    # --- Metadata ---
    book.set_identifier(f"urn:uuid:{article.uid}")
    book.set_title(article.metadata.title)
    book.set_language(article.metadata.language)
    book.add_author(article.metadata.to_epub_author())

    if article.metadata.date:
        book.add_metadata("DC", "date", article.metadata.date)
    if article.metadata.description:
        book.add_metadata("DC", "description", article.metadata.description)
    book.add_metadata("DC", "publisher", "article-to-kindle")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    book.add_metadata(
        None, "meta", now, {"property": "dcterms:modified"}
    )

    # --- CSS ---
    css_item = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=KINDLE_CSS.encode("utf-8"),
    )
    book.add_item(css_item)

    # --- Title Page ---
    title_page = epub.EpubHtml(
        title="Title Page",
        file_name="title_page.xhtml",
        lang=article.metadata.language,
    )
    title_page.content = _make_title_page(article)
    title_page.add_item(css_item)
    book.add_item(title_page)

    # --- Content ---
    body_html = _build_content_body(article.sections) if article.sections else (
        f"<h1>{_escape_html(article.metadata.title)}</h1>\n"
        f"<p>{_escape_html(article.metadata.description or '')}</p>"
    )
    content_chapter = epub.EpubHtml(
        title=article.metadata.title,
        file_name="content.xhtml",
        lang=article.metadata.language,
    )
    content_chapter.content = body_html
    content_chapter.add_item(css_item)
    book.add_item(content_chapter)

    # --- TOC ---
    toc_entries = []
    for section in article.sections:
        if section.type in (SectionType.HEADING, SectionType.TITLE):
            toc_entries.append(
                epub.Link("content.xhtml", section.text, f"toc-{section.text[:20]}")
            )

    book.toc = toc_entries if toc_entries else [epub.Link("content.xhtml", article.metadata.title, "content")]

    # --- Navigation ---
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # --- Spine ---
    spine_items = ["nav", title_page]
    if article.sections:
        spine_items.append(content_chapter)
    book.spine = spine_items

    # --- Write ---
    epub.write_epub(str(output_path), book)

    logger.info("EPUB written to %s", output_path)
    return output_path
