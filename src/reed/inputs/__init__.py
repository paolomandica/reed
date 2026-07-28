"""Input sources — each produces an :class:`Article <reed.models.Article>`."""

from .html_file import extract_from_html
from .markdown_file import extract_from_markdown

__all__ = ["extract_from_html", "extract_from_markdown"]
