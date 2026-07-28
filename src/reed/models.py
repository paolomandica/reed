"""Data models for article content and metadata."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import uuid4


class SectionType(Enum):
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    BLOCKQUOTE = "blockquote"
    LIST_ITEM = "list_item"


@dataclass
class ContentSection:
    """A single content section (heading, paragraph, etc.)."""

    type: SectionType
    text: str
    level: int = 0  # heading level (1-6), 0 for non-headings


@dataclass
class ArticleMetadata:
    """Metadata for an article."""

    title: str
    author: str
    author_handle: str = ""
    date: str | None = None  # ISO 8601 date string
    language: str = "en"
    description: str = ""
    url: str = ""

    def to_epub_author(self) -> str:
        """Return the author string for EPUB metadata."""
        if self.author_handle:
            return f"{self.author} (@{self.author_handle})"
        return self.author


@dataclass
class Article:
    """A complete article with metadata and content."""

    metadata: ArticleMetadata
    sections: list[ContentSection] = field(default_factory=list)
    html_body: str = ""
    uid: str = field(default_factory=lambda: str(uuid4()))

    @property
    def title(self) -> str:
        return self.metadata.title

    @property
    def author(self) -> str:
        return self.metadata.author

    def output_filename(self) -> str:
        """Generate a safe output filename from the title."""
        import re

        slug = self.metadata.title.lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"\s+", "-", slug)
        return f"{slug}.epub"
