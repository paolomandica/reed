"""Output formats — each consumes an :class:`Article <reed.models.Article>`."""

from .epub import generate_epub
from .audiobook import generate_audiobook

__all__ = ["generate_epub", "generate_audiobook"]
