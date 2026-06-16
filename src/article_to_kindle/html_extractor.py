"""Extract article content from saved HTML files.

This is a refactored version of article_html_to_markdown.py, adapted to return
structured Article objects instead of raw Markdown.
"""

import re
import logging
from pathlib import Path

from bs4 import BeautifulSoup, Tag, NavigableString

from .models import Article, ArticleMetadata, ContentSection, SectionType

logger = logging.getLogger(__name__)

SEMANTIC_BLOCKS = {"p", "blockquote", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
GENERIC_BLOCKS = {"div", "section", "article"}
SKIP_TAGS = {
    "script", "style", "noscript", "svg", "button", "input", "textarea",
    "form", "footer", "header", "nav", "aside", "img", "figure", "video",
    "audio", "canvas",
}
CONTAINER_HINTS = [
    {"data-testid": "longformRichTextComponent"},
    {"data-testid": "twitterArticleRichTextView"},
    {"data-testid": "article"},
]
ROLE_HINTS = ["main", "article"]
CLASS_HINTS = ["longform", "article", "richtext", "content", "post", "story", "entry"]


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def cleanup_text(text: str) -> str:
    text = normalize_ws(text)
    return re.sub(r"\s+([,.;:!?])", r"\1", text)


def is_probably_ui_text(text: str) -> bool:
    if not text:
        return True
    lowered = text.lower()
    ui_phrases = {
        "reply", "repost", "like", "likes", "bookmarked", "share post",
        "view post analytics", "upgrade to premium",
        "want to publish your own article?", "premium", "analytics",
    }
    if lowered in ui_phrases:
        return True
    return bool(re.fullmatch(r"[\d.,]+\s*(replies|reply|reposts|likes|views|bookmarks?)", lowered))


def find_title(soup: BeautifulSoup) -> str | None:
    selectors = [
        {"data-testid": "twitter-article-title"},
        {"property": "og:title"},
        {"name": "twitter:title"},
    ]
    for attrs in selectors:
        node = soup.find(attrs=attrs)
        if not node:
            continue
        if node.name == "meta":
            title = cleanup_text(node.get("content", ""))
        else:
            title = cleanup_text(node.get_text(" ", strip=True))
        if title:
            return title
    for tag in ["h1", "title"]:
        node = soup.find(tag)
        if node:
            title = cleanup_text(node.get_text(" ", strip=True))
            if title:
                return title
    return None


def find_author(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Try to find the author name and handle from the HTML.

    Returns (display_name, handle).
    """
    # Try meta tags first
    selectors = [
        {"name": "author"},
        {"property": "article:author"},
        {"name": "twitter:creator"},
    ]
    for attrs in selectors:
        node = soup.find(attrs=attrs)
        if node and node.name == "meta":
            content = node.get("content", "").strip()
            if content:
                # Twitter creator is often @handle
                if content.startswith("@"):
                    return (content[1:], content[1:])
                return (content, None)

    # Try finding author display name and handle from the page
    author_div = soup.find(attrs={"data-testid": "User-Name"})
    if author_div:
        links = author_div.find_all("a")
        display_name = None
        handle = None
        for link in links:
            text = cleanup_text(link.get_text(" ", strip=True))
            href = link.get("href", "")
            if text and text.startswith("@"):
                handle = text.lstrip("@")
            elif text and "@" not in text and not display_name:
                display_name = text
        if display_name:
            return (display_name, handle)
        if handle:
            return (handle, handle)

    # Fallback: look for any @handle link in the page
    for a in soup.find_all("a", href=True):
        href = a["href"]
        match = re.match(r"^/([A-Za-z0-9_]+)/?$", href)
        if match:
            handle = match.group(1)
            if handle not in ("i", "home", "explore", "notifications", "messages",
                              "search", "settings", "logout", "signup", "login",
                              "compose", "articles", "status"):
                return (handle, handle)

    return (None, None)


def find_date(soup: BeautifulSoup) -> str | None:
    """Try to find the publication date from the HTML."""
    selectors = [
        {"name": "article:published_time"},
        {"property": "article:published_time"},
        {"name": "pubdate"},
        {"property": "og:article:published_time"},
    ]
    for attrs in selectors:
        node = soup.find(attrs=attrs)
        if node and node.name == "meta":
            date_str = node.get("content", "").strip()
            if date_str:
                return date_str
    # Try finding in a time element
    time_el = soup.find("time")
    if time_el:
        dt = time_el.get("datetime", "")
        if dt:
            return dt
    return None


def find_description(soup: BeautifulSoup) -> str | None:
    """Try to find a description/abstract from meta tags."""
    selectors = [
        {"name": "description"},
        {"property": "og:description"},
    ]
    for attrs in selectors:
        node = soup.find(attrs=attrs)
        if node and node.name == "meta":
            desc = node.get("content", "").strip()
            if desc:
                return desc
    return None


def score_candidate(node: Tag) -> int:
    score = 0
    text = cleanup_text(node.get_text(" ", strip=True))
    score += min(len(text), 4000) // 40
    for h in node.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if cleanup_text(h.get_text(" ", strip=True)):
            score += 25
    for p in node.find_all(["p", "blockquote"]):
        if cleanup_text(p.get_text(" ", strip=True)):
            score += 12
    classes = " ".join(node.get("class", []))
    attrs = " ".join(f"{k}={v}" for k, v in node.attrs.items())
    lowered = f"{classes} {attrs}".lower()
    if any(hint in lowered for hint in CLASS_HINTS):
        score += 40
    return score


def find_content_root(soup: BeautifulSoup) -> Tag:
    for attrs in CONTAINER_HINTS:
        node = soup.find(attrs=attrs)
        if node:
            return node
    for role in ROLE_HINTS:
        node = soup.find(attrs={"role": role})
        if node:
            return node
    for tag in ["article", "main"]:
        node = soup.find(tag)
        if node:
            return node
    candidates = [n for n in soup.find_all(["div", "section"]) if cleanup_text(n.get_text(" ", strip=True))]
    return max(candidates, key=score_candidate) if candidates else (soup.body or soup)


def has_nested_block(node: Tag) -> bool:
    for child in node.children:
        if not isinstance(child, Tag):
            continue
        if child.name in SKIP_TAGS:
            continue
        if child.name in SEMANTIC_BLOCKS or child.name in GENERIC_BLOCKS:
            if cleanup_text(child.get_text(" ", strip=True)):
                return True
        if has_nested_block(child):
            return True
    return False


def is_leaf_block(node: Tag) -> bool:
    if node.name in SEMANTIC_BLOCKS:
        return True
    if node.name in GENERIC_BLOCKS:
        return not has_nested_block(node)
    return False


def _node_to_section(node: Tag) -> ContentSection | None:
    """Convert an HTML node to a ContentSection."""
    if node.name in SKIP_TAGS:
        return None
    text = cleanup_text(node.get_text(" ", strip=True))
    if not text or is_probably_ui_text(text):
        return None

    if node.name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(node.name[1])
        return ContentSection(type=SectionType.HEADING, text=text, level=level)
    if node.name == "blockquote":
        return ContentSection(type=SectionType.BLOCKQUOTE, text=text)
    if node.name == "li":
        return ContentSection(type=SectionType.LIST_ITEM, text=text)
    return ContentSection(type=SectionType.PARAGRAPH, text=text)


def extract_sections(root: Tag, title: str | None = None) -> list[ContentSection]:
    """Extract structured content sections from an HTML content root."""
    sections: list[ContentSection] = []
    seen: set[str] = set()

    if title:
        sections.append(ContentSection(type=SectionType.HEADING, text=title, level=1))
        seen.add(cleanup_text(title).lower())

    for node in root.descendants:
        if isinstance(node, NavigableString) or not isinstance(node, Tag):
            continue
        if node.name in SKIP_TAGS or not is_leaf_block(node):
            continue
        section = _node_to_section(node)
        if not section:
            continue
        key = cleanup_text(section.text).lower()
        if not key or len(key) < 2 or key in seen:
            continue
        seen.add(key)
        sections.append(section)

    return sections


def extract_from_html(html_path: Path) -> Article:
    """Extract a complete Article from a saved HTML file.

    Args:
        html_path: Path to the HTML file.

    Returns:
        A structured Article with metadata and content sections.
    """
    html = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    # Strip unwanted tags
    for tag in soup.find_all(SKIP_TAGS):
        tag.decompose()

    title = find_title(soup) or html_path.stem
    author_name, author_handle = find_author(soup)
    author = author_name or "Unknown"
    handle = author_handle or ""
    date = find_date(soup)
    description = find_description(soup) or ""
    lang = (soup.html.get("lang") if soup.html else None) or "en"

    content_root = find_content_root(soup)
    sections = extract_sections(content_root, title=title)

    # If no explicit title was found and first section is a heading, use it
    if title == html_path.stem and sections:
        first = sections[0]
        if first.type == SectionType.HEADING:
            title = first.text

    metadata = ArticleMetadata(
        title=title,
        author=author,
        author_handle=handle,
        date=date,
        language=lang,
        description=description,
        url="",
    )

    logger.info(
        "Extracted from HTML: title=%r, author=%r, sections=%d",
        title,
        author,
        len(sections),
    )
    return Article(metadata=metadata, sections=sections)
