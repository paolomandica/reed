"""Output formats — each consumes an :class:`Article <reed.models.Article>`."""

from .epub import generate_epub
from .markdown import generate_markdown

# generate_audiobook is NOT imported at module level — it pulls in
# torch, numpy, soundfile, and chatterbox-tts (heavy, ~4s+).
# Import it lazily inside the audiobook subcommand instead.

__all__ = ["generate_epub", "generate_markdown", "generate_audiobook"]


def __getattr__(name: str):
    """Lazy-import generate_audiobook so --help stays fast."""
    if name == "generate_audiobook":
        from .audiobook import generate_audiobook as _ga

        return _ga
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
