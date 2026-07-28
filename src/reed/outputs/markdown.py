"""Generate Markdown files from structured articles."""

import logging
from datetime import datetime
from pathlib import Path

from markdownify import markdownify as md

from ..models import Article, ContentSection, SectionType

logger = logging.getLogger(__name__)


def _section_to_markdown(section: ContentSection) -> str:
    """Convert a single ContentSection to its Markdown representation."""
    text = section.text

    if section.type in (SectionType.TITLE, SectionType.HEADING):
        level = max(section.level, 1) if section.level else 1
        prefix = "#" * min(level, 6)
        return f"{prefix} {text}\n"

    elif section.type == SectionType.PARAGRAPH:
        return f"{text}\n\n"

    elif section.type == SectionType.BLOCKQUOTE:
        lines = text.split("\n")
        return "\n".join(f"> {line}" for line in lines) + "\n\n"

    elif section.type == SectionType.LIST_ITEM:
        return f"- {text}\n"

    else:
        return f"{text}\n\n"


def _sections_to_markdown(article: Article) -> str:
    """Build body Markdown from content sections (fallback when no html_body)."""
    parts: list[str] = []
    i = 0
    while i < len(article.sections):
        section = article.sections[i]

        # Skip a title/heading that duplicates the metadata header
        if i == 0 and section.type in (SectionType.TITLE, SectionType.HEADING):
            if section.text == article.metadata.title:
                i += 1
                continue

        if section.type == SectionType.LIST_ITEM:
            # Group consecutive list items together
            while i < len(article.sections) and article.sections[i].type == SectionType.LIST_ITEM:
                parts.append(_section_to_markdown(article.sections[i]))
                i += 1
        else:
            parts.append(_section_to_markdown(section))
            i += 1

    return "".join(parts)


def _build_metadata_header(article: Article) -> str:
    """Build the metadata / title block at the top of the Markdown file."""
    meta = article.metadata
    lines: list[str] = []

    # Title
    lines.append(f"# {meta.title}\n")

    # Author + date line
    byline_parts = [f"*By {meta.author}"]
    if meta.author_handle:
        byline_parts.append(f" (@{meta.author_handle})")
    if meta.date:
        try:
            dt = datetime.fromisoformat(meta.date)
            formatted = dt.strftime("%B %d, %Y")
        except (ValueError, TypeError):
            formatted = meta.date
        byline_parts.append(f" — {formatted}")
    byline_parts.append("*")
    lines.append("".join(byline_parts) + "\n")

    if meta.url:
        lines.append(f"\nSource: {meta.url}\n")

    # Separator
    lines.append("\n---\n")
    return "\n".join(lines)


def generate_markdown(article: Article, output_path: Path) -> Path:
    """Generate a Markdown file from an Article.

    Uses ``markdownify`` to convert the stored HTML body directly to
    Markdown, preserving structure (headings, lists, blockquotes, links,
    emphasis) without manual section-by-section reconstruction.

    Args:
        article: The structured article with metadata and content.
        output_path: Where to write the ``.md`` file.

    Returns:
        The path to the generated Markdown file.
    """
    parts: list[str] = []

    # Metadata header
    parts.append(_build_metadata_header(article))

    # Body — prefer markdownify on the raw HTML body when available,
    # fall back to section-by-section conversion for pasted text / .md files.
    if article.html_body:
        body_md = md(article.html_body, heading_style="ATX")
        parts.append(body_md)
    elif article.sections:
        parts.append(_sections_to_markdown(article))
    else:
        logger.warning("No html_body or sections available; Markdown output will be metadata-only.")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure .md extension
    if output_path.suffix != ".md":
        output_path = output_path.with_suffix(".md")

    markdown_text = "\n".join(parts)
    output_path.write_text(markdown_text, encoding="utf-8")

    logger.info("Markdown written to %s", output_path)
    return output_path
