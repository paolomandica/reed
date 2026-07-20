"""Input sources — each produces an :class:`Article <reed.models.Article>`."""

from .html_file import extract_from_html
from .download import download_tweet
from .extract import extract_content


def extract_from_url(url: str):
    """Download and parse an X.com article from a URL.

    Convenience wrapper that calls :func:`download_tweet` then
    :func:`extract_content`, returning a fully-parsed
    :class:`~reed.models.Article`.

    Args:
        url: An X.com tweet/thread URL.

    Returns:
        A structured ``Article`` ready for any output format.

    Raises:
        ValueError: If the URL cannot be parsed or the content
            cannot be fetched.
    """
    downloaded = download_tweet(url)
    return extract_content(downloaded)


__all__ = ["extract_from_url", "extract_from_html"]
