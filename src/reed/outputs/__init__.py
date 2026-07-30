"""Output formats — each consumes an :class:`Article <reed.models.Article>`."""

from .epub import generate_epub
from .markdown import generate_markdown

# generate_audiobook is NOT imported at module level — it pulls in
# torch, numpy, soundfile, and chatterbox-tts (heavy, ~4s+).
# Import it lazily inside the audiobook subcommand instead.

__all__ = [
    "generate_epub",
    "generate_markdown",
    "generate_audiobook",
    "generate_voice_preview",
]


def __getattr__(name: str):
    """Lazy-import the audiobook helpers so --help stays fast."""
    if name == "generate_audiobook":
        from .audiobook import generate_audiobook as _ga

        return _ga
    if name == "generate_voice_preview":
        from .audiobook import generate_voice_preview as _gvp

        return _gvp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
