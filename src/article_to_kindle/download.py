"""Download X.com (Twitter) article content via public APIs.

Primary: FxTwitter API (no auth, returns thread data)
Fallback: Twitter Syndication API (no auth, single tweet)
"""

import re
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

# --- URL parsing ---

X_URL_PATTERNS = [
    re.compile(
        r"https?://(?:www\.)?(?:twitter\.com|x\.com)/(?P<user>[A-Za-z0-9_]+)/status/(?P<id>\d+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"https?://(?:www\.)?(?:twitter\.com|x\.com)/i/articles?/(?P<id>\d+)",
        re.IGNORECASE,
    ),
]

FX_TWITTER_API = "https://api.fxtwitter.com/{user}/status/{id}"
SYNDICATION_API = "https://cdn.syndication.twimg.com/tweet-result?id={id}&token=x"


@dataclass
class DownloadedContent:
    """Content downloaded from X.com."""

    text: str  # Full article/thread text
    author_name: str
    author_handle: str
    created_at: str | None  # ISO 8601
    title: str | None = None
    language: str | None = None
    description: str | None = None
    source_url: str | None = None
    is_article: bool = False  # True if it's an X Article (long-form)


def parse_x_url(url: str) -> tuple[str | None, str | None, str | None]:
    """Parse an X.com URL into (username, status_id, article_id).

    Returns (username, status_id, article_id). One or more may be None.
    """
    for pattern in X_URL_PATTERNS:
        match = pattern.search(url)
        if match:
            groups = match.groupdict()
            user = groups.get("user")
            tid = groups.get("id")
            if "article" in pattern.pattern:
                return (None, None, tid)
            return (user, tid, None)
    return (None, None, None)


def _parse_twitter_date(date_str: str) -> str | None:
    """Parse a Twitter date string to ISO 8601."""
    if not date_str:
        return None
    try:
        # Format: "Wed Jun 10 12:29:25 +0000 2026"
        dt = datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
        return dt.isoformat()
    except ValueError:
        pass
    try:
        # Already ISO 8601
        datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return date_str
    except ValueError:
        pass
    return None


def _fetch_fxtwitter(user: str, status_id: str) -> dict | None:
    """Fetch tweet data from FxTwitter API."""
    url = FX_TWITTER_API.format(user=user, id=status_id)
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == 200:
            return data
        logger.warning("FxTwitter API returned code %s: %s", data.get("code"), data.get("message"))
        return None
    except httpx.HTTPError as e:
        logger.warning("FxTwitter API request failed: %s", e)
        return None


def _fetch_syndication(status_id: str) -> dict | None:
    """Fetch tweet data from Twitter Syndication API."""
    url = SYNDICATION_API.format(id=status_id)
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        return data
    except httpx.HTTPError as e:
        logger.warning("Syndication API request failed: %s", e)
        return None


def _extract_content_fxtwitter(data: dict, source_url: str) -> DownloadedContent | None:
    """Extract DownloadedContent from FxTwitter API response."""
    tweet = data.get("tweet", {})
    author = tweet.get("author", {})
    article = tweet.get("article")

    author_name = author.get("name", "Unknown")
    author_handle = author.get("screen_name", "")
    created_at = _parse_twitter_date(tweet.get("created_at", ""))

    text = tweet.get("text", "") or ""
    raw_text = tweet.get("raw_text", {}) or {}
    if not text and raw_text.get("text"):
        text = raw_text["text"]

    # Check if this is an X Article (long-form post)
    is_article = bool(article and isinstance(article, dict) and article.get("title"))
    title = None
    description = None

    if is_article:
        title = article.get("title", "")
        description = article.get("preview_text", "")

    # If it's an article, the tweet text is just a t.co link
    if is_article and text.startswith("https://t.co/"):
        text = ""

    # Build full text from thread if available
    thread = data.get("thread", [])
    if thread and not is_article:
        parts = [text] if text else []
        for t in thread:
            t_text = t.get("text", "") or t.get("raw_text", {}).get("text", "")
            if t_text and not t_text.startswith("https://t.co/"):
                parts.append(t_text)
        if parts:
            text = "\n\n".join(parts)

    if not text and not is_article:
        logger.warning("No text content found in FxTwitter response")
        return None

    lang = tweet.get("lang")
    if lang == "zxx":  # "zxx" = no linguistic content
        lang = None

    return DownloadedContent(
        text=text,
        author_name=author_name,
        author_handle=author_handle,
        created_at=created_at,
        title=title,
        language=lang,
        description=description,
        source_url=source_url,
        is_article=is_article,
    )


def _extract_content_syndication(data: dict, source_url: str) -> DownloadedContent | None:
    """Extract DownloadedContent from Syndication API response."""
    user = data.get("user", {})
    article = data.get("article", {})

    author_name = user.get("name", "Unknown")
    author_handle = user.get("screen_name", "")
    created_at = _parse_twitter_date(data.get("created_at", ""))

    text = data.get("text", "") or ""
    is_article = bool(article and isinstance(article, dict) and article.get("title"))

    title = None
    description = None
    if is_article:
        title = article.get("title", "")
        description = article.get("preview_text", "")
        text = ""  # Article body not in syndication API

    if not text and not is_article:
        logger.warning("No text content found in Syndication response")
        return None

    return DownloadedContent(
        text=text,
        author_name=author_name,
        author_handle=author_handle,
        created_at=created_at,
        title=title,
        language=data.get("lang"),
        description=description,
        source_url=source_url,
        is_article=is_article,
    )


def download_tweet(url: str) -> DownloadedContent:
    """Download article content from an X.com URL.

    Tries FxTwitter API first, falls back to Syndication API.

    Raises ValueError if the URL can't be parsed or no content is found.
    """
    user, status_id, article_id = parse_x_url(url)

    if article_id:
        raise ValueError(
            "X Article URLs (x.com/i/articles/...) are not directly supported. "
            "Use the tweet URL that links to the article instead, "
            "or download the page as HTML and use --html."
        )

    if not status_id or not user:
        raise ValueError(f"Could not parse X.com URL: {url}")

    source_url = f"https://x.com/{user}/status/{status_id}"

    # Try FxTwitter first
    data = _fetch_fxtwitter(user, status_id)
    if data:
        content = _extract_content_fxtwitter(data, source_url)
        if content:
            if content.is_article and not content.text:
                raise ValueError(
                    f"This is an X Article: \"{content.title}\". "
                    "X Article bodies cannot be downloaded without a browser. "
                    "Save the article page as HTML and use --html, or install "
                    "playwright for automated browser support."
                )
            return content

    # Fall back to Syndication API
    data = _fetch_syndication(status_id)
    if data:
        content = _extract_content_syndication(data, source_url)
        if content:
            if content.is_article and not content.text:
                raise ValueError(
                    f"This is an X Article: \"{content.title}\". "
                    "X Article bodies cannot be downloaded without a browser. "
                    "Save the article page as HTML and use --html, or install "
                    "playwright for automated browser support."
                )
            return content

    raise ValueError(
        f"Failed to fetch content from {url}. "
        "The tweet may be private, deleted, or rate-limited. "
        "Try downloading the page as HTML and use --html."
    )
