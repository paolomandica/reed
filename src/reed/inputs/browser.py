"""Download fully-rendered X.com article HTML via a headless browser.

X Articles (``x.com/<user>/article/<id>``) are JavaScript-rendered SPAs.
A plain HTTP request only captures an empty shell — the article body is
injected client-side after page load.  This module uses Playwright (Chromium)
to wait for the rendered DOM and return the complete HTML.

Usage::

    from reed.inputs.browser import download_article_html

    html = download_article_html("https://x.com/user/article/123")
    Path("article.html").write_text(html, encoding="utf-8")

Requirements
------------
Install the optional ``browser`` extra and the Chromium browser binary::

    pip install reed[browser]
    playwright install chromium
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from bs4 import BeautifulSoup

from .html_file import CONTAINER_HINTS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selectors / timing
# ---------------------------------------------------------------------------

# Content selectors tried in order (first match wins).
_CONTENT_SELECTORS: list[str] = [
    '[data-testid="article"]',
    '[data-testid="longformRichTextComponent"]',
    '[data-testid="twitterArticleRichTextView"]',
    "article",
    '[role="article"]',
    "main",
]

# How long to wait for the article container to appear (ms).
_CONTENT_TIMEOUT_MS = 15_000

# Extra settle time if no known selector matched (ms).
_FALLBACK_WAIT_MS = 5_000

# Default navigation timeout (ms).
_DEFAULT_TIMEOUT_MS = 60_000

# Realistic user-agent sent with every request.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_VIEWPORT = {"width": 1280, "height": 800}

# URL patterns for extracting an article id.
_X_ARTICLE_RE = re.compile(
    r"https?://(?:www\.)?(?:twitter\.com|x\.com)/"
    r"(?:(?P<user>[A-Za-z0-9_]+)/article/|i/articles?/)(?P<id>\d+)",
    re.IGNORECASE,
)
_X_STATUS_RE = re.compile(
    r"https?://(?:www\.)?(?:twitter\.com|x\.com)/"
    r"(?P<user>[A-Za-z0-9_]+)/status/(?P<id>\d+)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _import_playwright():
    """Import Playwright, raising a user-friendly error if unavailable."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        raise RuntimeError(
            "Playwright is required for browser-based article download.\n\n"
            "Install it with:\n"
            "    pip install reed[browser]\n"
            "    playwright install chromium\n"
        ) from None
    return sync_playwright


def derive_filename(url: str) -> str:
    """Derive a safe filename from an X.com article or status URL.

    Returns something like ``x_article_2077143118524417439.html`` or
    ``x_status_123456789.html``.
    """
    for pattern in (_X_ARTICLE_RE, _X_STATUS_RE):
        m = pattern.search(url)
        if m:
            tid = m.group("id")
            prefix = "x_article" if "article" in pattern.pattern else "x_status"
            return f"{prefix}_{tid}.html"
    # Best-effort: use the last path segment
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    safe = re.sub(r"[^\w.-]", "_", slug)
    return f"x_{safe}.html"


def extract_text_from_html(html: str) -> str:
    """Extract readable article text from rendered HTML.

    Uses the same content-root detection as ``extract_from_html`` so the
    extracted text matches what the EPUB pipeline would see.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Try the same container hints used by html_file.py
    root = None
    for attrs in CONTAINER_HINTS:
        root = soup.find(attrs=attrs)
        if root:
            break
    if root is None:
        for role in ("main", "article"):
            root = soup.find(attrs={"role": role})
            if root:
                break
    if root is None:
        for tag in ("article", "main"):
            root = soup.find(tag)
            if root:
                break
    if root is None:
        root = soup.body or soup

    # Remove noisy elements
    for tag_name in ("script", "style", "noscript", "svg", "nav", "footer"):
        for el in root.find_all(tag_name):
            el.decompose()

    text = root.get_text("\n", strip=True)
    # Collapse blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Core download
# ---------------------------------------------------------------------------


def download_article_html(
    url: str,
    *,
    headed: bool = False,
    timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    storage_state: str | Path | None = None,
) -> str:
    """Download the fully-rendered HTML of an X.com article.

    Launches a headless (or headed) Chromium browser via Playwright,
    navigates to *url*, waits for article content to appear, then returns
    ``page.content()``.

    Args:
        url: An X.com article URL (e.g. ``https://x.com/user/article/123``).
        headed: If ``True``, run the browser visibly (useful for debugging
            or manual login).
        timeout_ms: Navigation timeout in milliseconds.
        storage_state: Optional path to a Playwright ``storage_state`` JSON
            file (cookies / localStorage from a prior login session).  Use
            :func:`save_auth` to create one.

    Returns:
        The full rendered HTML as a UTF-8 string.

    Raises:
        RuntimeError: If Playwright is not installed, or if the page load
            times out / returns empty content.
    """
    sync_playwright = _import_playwright()

    state_path: str | None = None
    if storage_state is not None:
        state_path = str(storage_state)

    logger.info(
        "Launching browser (headed=%s, timeout=%dms) for %s",
        headed,
        timeout_ms,
        url,
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport=_VIEWPORT,
            storage_state=state_path,
        )
        page = context.new_page()

        try:
            # -- navigate --------------------------------------------------------
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            # -- wait for article content ----------------------------------------
            _wait_for_article_content(page)

            # -- get the rendered HTML -------------------------------------------
            html = page.content()

            if not html or len(html) < 500:
                raise RuntimeError(
                    f"Downloaded HTML is suspiciously small ({len(html)} bytes). "
                    "The page may not have loaded correctly."
                )

            logger.info("Downloaded %d bytes of rendered HTML.", len(html))
            return html

        finally:
            context.close()
            browser.close()


def _wait_for_article_content(page) -> None:
    """Wait for the article body to appear in the DOM.

    Tries known content selectors first, falls back to a short fixed wait
    plus a scroll-to-bottom to trigger lazy rendering.
    """
    for selector in _CONTENT_SELECTORS:
        try:
            page.wait_for_selector(selector, timeout=_CONTENT_TIMEOUT_MS)
            logger.info("Article content found via selector: %s", selector)
            # Small extra settle for sub-elements
            page.wait_for_timeout(1500)
            return
        except Exception:
            continue

    # Fallback: fixed wait + scroll to trigger any lazy-loaded content
    logger.warning(
        "No known article selector matched — using fallback wait + scroll."
    )
    page.wait_for_timeout(_FALLBACK_WAIT_MS)
    _scroll_to_bottom(page)
    page.wait_for_timeout(2000)


def _scroll_to_bottom(page) -> None:
    """Scroll to the bottom of the page to trigger lazy rendering."""
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def save_auth(output_path: str | Path) -> None:
    """Open a headed browser at ``x.com/login`` for manual login, then save
    the session cookies / storage to *output_path* as a Playwright
    ``storage_state`` JSON file.

    The user can then pass this file to ``download_article_html(storage_state=...)``
    or the ``reed fetch --auth`` / ``reed epub --auth`` CLI options.
    """
    sync_playwright = _import_playwright()
    output = Path(output_path)

    print(f"Opening browser at https://x.com/login ...")
    print("Log in manually, then close the browser window (or press Ctrl+C).")
    print(f"Session cookies will be saved to: {output}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=_USER_AGENT,
            viewport=_VIEWPORT,
        )
        page = context.new_page()
        page.goto("https://x.com/login", wait_until="domcontentloaded")

        # Wait for the user to log in manually, then press Enter.
        try:
            input("Log in to X.com in the browser window, then press Enter here...")
        except (KeyboardInterrupt, EOFError):
            print()  # newline after ^C

        storage = context.storage_state()
        output.write_text(_json_dumps(storage), encoding="utf-8")
        print(f"✓ Auth session saved to: {output}")
        context.close()
        browser.close()


def _json_dumps(obj) -> str:
    """Serialize *obj* to pretty-printed JSON (stdlib import)."""
    import json

    return json.dumps(obj, indent=2, ensure_ascii=False)
