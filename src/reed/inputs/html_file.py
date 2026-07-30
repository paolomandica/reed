"""Extract article content from saved HTML files.

Handles X.com (Twitter) articles, Substack newsletters, and generic
long-form article pages through heuristics rather than site-specific
selectors where possible.
"""

import json
import re
import logging
from pathlib import Path

from bs4 import BeautifulSoup, Tag, NavigableString

from ..models import Article, ArticleMetadata, ContentSection, SectionType

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
BODY_CLASS_HINTS = ["body markup", "article-body", "post-body", "entry-content",
                    "article-content", "post-content"]

# Substack / generic date patterns for byline text
_DATE_PATTERNS = [
    re.compile(
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),  # ISO 8601
]


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def cleanup_text(text: str) -> str:
    text = normalize_ws(text)
    return re.sub(r"\s+([,.;:!?])", r"\1", text)


def is_prose_preformatted_text(text: str) -> bool:
    """Return whether a ``<pre>`` block is prose rather than source code."""
    normalized = cleanup_text(text)
    words = normalized.split()
    if len(words) < 20:
        return False

    code_markers = len(re.findall(
        r"(?:[{};]|\b(?:def|class|function|return|import|SELECT|FROM)\b|^\s*(?:#include|\$|>>>))",
        text,
        re.IGNORECASE | re.MULTILINE,
    ))
    sentence_endings = len(re.findall(r"[.!?](?:\s|$)", normalized))
    return sentence_endings >= 2 and code_markers * 3 < len(words)


def _normalise_prose_pre_blocks(root: Tag) -> None:
    """Convert prose-only legacy blocks to paragraphs for downstream use."""
    for pre in root.find_all("pre"):
        if is_prose_preformatted_text(pre.get_text("\n", strip=True)):
            pre.name = "p"
            pre.attrs = {}

    # Older essay sites frequently put the complete article in a font tag.
    # Convert only outer, article-length font containers; nested font tags are
    # typically footnote styling and remain part of the prose.
    for font in root.find_all("font"):
        if _has_ancestor(font, "font"):
            continue
        if is_prose_preformatted_text(font.get_text(" ", strip=True)):
            font.name = "p"
            font.attrs = {}


def _has_ancestor(node: Tag, tag_name: str | set[str]) -> bool:
    """Check if *node* has an ancestor with one of the given tag names."""
    names = {tag_name} if isinstance(tag_name, str) else tag_name
    parent = node.parent
    while parent:
        if hasattr(parent, "name") and parent.name in names:
            return True
        parent = parent.parent
    return False


def is_probably_ui_text(text: str) -> bool:
    if not text:
        return True
    lowered = text.lower().strip()

    # Exact-match UI phrases (mostly X.com)
    exact_matches = {
        "reply", "repost", "like", "likes", "bookmarked", "share post",
        "view post analytics", "upgrade to premium",
        "want to publish your own article?", "premium", "analytics",
        # Substack
        "share", "subscribe", "ready for more",
        "discussion about this post",
        "sic transit imperium",  # author sign-off
    }
    if lowered.rstrip(".,;:!?") in exact_matches:
        return True

    # Engagement-stat patterns: "1,565", "262 Restacks", "Like (106)"
    if re.fullmatch(r"[\d.,]+\s*(replies|reply|reposts|likes|views|bookmarks|restacks?)?", lowered):
        return True
    if re.fullmatch(r"like\s*\(\s*\d+\s*\)", lowered):
        return True
    if re.fullmatch(r"[\d.,]+\s+restacks?", lowered):
        return True

    # Date-only lines: "Jul 14, 2026", "Jun 18, 2025 • Johann Kurtz"
    for pat in _DATE_PATTERNS:
        if pat.fullmatch(lowered):
            return True
    # "Jun 18, 2025 • Johann Kurtz" style
    for pat in _DATE_PATTERNS:
        if pat.match(lowered):
            remainder = lowered[pat.match(lowered).end():].strip().lstrip("•-–—").strip()
            if len(remainder.split()) <= 3:
                return True

    # Substack CTA / promo blurbs
    cta_prefixes = (
        "if you enjoyed this piece",
        "if you find my work valuable",
        "become a free or paid subscriber",
        "all support is appreciated",
        "get my bestselling book",
    )
    if lowered.startswith(cta_prefixes):
        return True

    return False


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


def _structured_author(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Extract an Article/NewsArticle author from JSON-LD metadata."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if isinstance(node, dict) and isinstance(node.get("@graph"), list):
                nodes.extend(node["@graph"])
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type", "")
            types = set(node_type if isinstance(node_type, list) else [node_type])
            if not types & {"Article", "NewsArticle", "BlogPosting"}:
                continue
            author = node.get("author")
            authors = author if isinstance(author, list) else [author]
            for candidate in authors:
                if isinstance(candidate, dict):
                    name = cleanup_text(str(candidate.get("name", "")))
                    if name:
                        return name, None
                elif isinstance(candidate, str) and not re.match(r"https?://", candidate):
                    return cleanup_text(candidate), None
    return None, None


def find_author(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Try to find the author name and handle from the HTML.

    Returns (display_name, handle).

    Handles X.com (data-testid, @handle links) and Substack
    (profile-link aria-labels, byline text).
    """
    # 1. Structured metadata carries the display name on many news sites.
    structured_author = _structured_author(soup)
    if structured_author[0]:
        return structured_author

    # 2. Meta tags (works for both X.com and generic)
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
                if content.startswith("@"):
                    return (content[1:], content[1:])
                # Some publishers place a contributor-profile URL here;
                # continue to the visible byline instead of narrating it.
                if re.match(r"https?://", content, re.IGNORECASE):
                    continue
                return (content, None)

    # 2. X.com: data-testid="User-Name" block
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

    # 3. Substack / generic: profile-link with aria-label
    for a in soup.find_all("a", attrs={"aria-label": True}):
        label = a.get("aria-label", "")
        match = re.match(r"view\s+(.+?)['’]s?\s+profile", label, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            href = a.get("href", "")
            handle = None
            if "/@" in href:
                handle = href.split("/@")[-1].rstrip("/")
            return (name, handle)

    # 4. Substack: look for a byline div containing a name and date
    for el in soup.find_all(["div", "span"], class_=lambda c: c and "byline" in " ".join(c).lower()):
        text = cleanup_text(el.get_text(" ", strip=True))
        if text:
            # The first part before a date is typically the author name
            for pat in _DATE_PATTERNS:
                m = pat.search(text)
                if m:
                    name_part = text[:m.start()].strip().rstrip(",").strip()
                    if name_part and len(name_part.split()) <= 4:
                        return (name_part, None)

    # 5. Generic fallback: profile paths and @handle links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        profile_match = re.match(r"^/user/([A-Za-z0-9_]+)/?$", href)
        if profile_match:
            handle = profile_match.group(1)
            return (handle, handle)
        match = re.match(r"^/([A-Za-z0-9_]+)/?$", href)
        if match:
            handle = match.group(1)
            if handle not in ("i", "home", "explore", "notifications", "messages",
                              "search", "settings", "logout", "signup", "login",
                              "compose", "articles", "status", "about", "contact",
                              "archive", "podcast", "subscribe", "account", "notes"):
                return (handle, handle)

    return (None, None)


def find_date(soup: BeautifulSoup) -> str | None:
    """Try to find the publication date from the HTML."""
    # 1. Meta tags
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

    # 2. <time> element
    time_el = soup.find("time")
    if time_el:
        dt = time_el.get("datetime", "")
        if dt:
            return dt

    # 3. Substack / generic: parse a date from header text
    header = soup.find(attrs={"role": "region", "aria-label": "Post header"})
    if header:
        header_text = cleanup_text(header.get_text(" ", strip=True))
        for pat in _DATE_PATTERNS:
            m = pat.search(header_text)
            if m:
                return m.group(0)

    # 4. Generic: search any visible text for a date
    for pat in _DATE_PATTERNS:
        text_nodes = soup.find_all(string=lambda t: t and pat.search(t.strip()))
        if text_nodes:
            return pat.search(text_nodes[0].strip()).group(0)

    return None


def find_description(soup: BeautifulSoup) -> str | None:
    """Try to find a description/abstract from meta tags or subtitle."""
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

    # Substack: subtitle <h3> inside the post header
    header = soup.find(attrs={"role": "region", "aria-label": "Post header"})
    if header:
        subtitle = header.find("h3")
        if subtitle:
            text = cleanup_text(subtitle.get_text(" ", strip=True))
            if text:
                return text

    # Generic: look for elements with class "subtitle", "deck", "description"
    for cls in ["subtitle", "deck", "description", "abstract"]:
        el = soup.find(class_=lambda c: c and cls in " ".join(c).lower())
        if el:
            text = cleanup_text(el.get_text(" ", strip=True))
            if text:
                return text

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
    """Find the broad content container (article, main, or best candidate)."""
    for attrs in CONTAINER_HINTS:
        node = soup.find(attrs=attrs)
        if node:
            return node
    for role in ROLE_HINTS:
        node = soup.find(attrs={"role": role})
        if node:
            return node
    candidates = [
        node
        for node in soup.find_all(["article", "main", "div", "section"])
        if cleanup_text(node.get_text(" ", strip=True))
    ]
    return max(candidates, key=score_candidate) if candidates else (soup.body or soup)


def find_body_root(content_root: Tag) -> Tag:
    """Narrow from the broad content container to the actual body text div.

    On Substack the content root is the full post container (header +
    body + comments + footer).  We want just the ``div.body.markup``
    (or similar) that holds the article prose.
    """
    # 1. Substack / generic: div with "body" + "markup" classes
    for hint in BODY_CLASS_HINTS:
        parts = hint.split()
        found = content_root.find(
            class_=lambda c: c and all(p in " ".join(c).lower() for p in parts)
        )
        if found and cleanup_text(found.get_text(" ", strip=True)):
            return found

    # 2. X.com: the data-testid-based containers are already the body
    for attrs in CONTAINER_HINTS:
        node = content_root.find(attrs=attrs)
        if node:
            return node

    # 3. Legacy essay sites often use a large <font> container with <br>
    # separators instead of semantic paragraphs. Prefer that prose block over
    # surrounding table-based navigation when it is clearly article-length.
    prose_fonts = [
        font
        for font in content_root.find_all("font")
        if is_prose_preformatted_text(font.get_text(" ", strip=True))
    ]
    if prose_fonts:
        return max(prose_fonts, key=lambda font: len(cleanup_text(font.get_text(" ", strip=True))))

    # 4. Heuristic: find the child div with the most <p> density
    best = None
    best_score = 0
    for div in content_root.find_all("div", recursive=True):
        text_len = len(cleanup_text(div.get_text(" ", strip=True)))
        p_count = len(div.find_all("p", recursive=False))
        if p_count >= 3 and text_len > 500:
            score = p_count * 20 + min(text_len, 10000) // 50
            if score > best_score:
                best_score = score
                best = div
    if best:
        return best

    return content_root


def _decompose_non_content(root: Tag) -> None:
    """Remove non-content regions from *root* (mutates the tree).

    Strips headers, comment sections, engagement UIs, and footer
    widgets so they don't contaminate extracted content.
    """
    # Blog index cards can accompany the full post in an otherwise valid
    # content container. The post title is already captured as metadata.
    for section in list(root.find_all("section", id=True)):
        if "newslist" in section.get("id", "").lower():
            section.decompose()

    # Disqus embeds leave static promotional links in saved HTML.
    for el in list(root.find_all(id=True)):
        if el.attrs and "disqus" in el.get("id", "").lower():
            el.decompose()
    for el in list(root.find_all(class_=True)):
        if not el.attrs:
            continue
        classes = " ".join(el.get("class", [])).lower()
        if "disqus" in classes or "dsq-" in classes:
            el.decompose()

    # Compact author/time/view headers are metadata, not article prose.
    for el in list(root.find_all(["span", "div"], class_=True)):
        if not el.attrs:
            continue
        classes = set(el.get("class", []))
        text = cleanup_text(el.get_text(" ", strip=True)).lower()
        has_profile_link = bool(el.find("a", href=re.compile(r"^/user/")))
        if "info" in classes and has_profile_link and re.search(
            r"\b(?:views?|ago|comments?)\b", text
        ):
            el.decompose()

    # Post header region (Substack / generic)
    for el in root.find_all(attrs={"role": "region"}):
        label = el.get("aria-label", "").lower()
        if "header" in label:
            el.decompose()

    # Post footer (Substack: "Top Posts Footer", etc.)
    for el in root.find_all(attrs={"aria-label": True}):
        label = el.get("aria-label", "").lower()
        if "footer" in label:
            el.decompose()

    # Comments / discussion section
    for el in root.find_all(attrs={"aria-label": True}):
        label = el.get("aria-label", "")
        if "select discussion type" in label.lower():
            # Walk up to the nearest major container sibling
            container = el
            for _ in range(6):
                container = container.parent
                if container is None or container is root:
                    break
                if container.name in ("div", "section") and container.get("class"):
                    classes = " ".join(container.get("class", []))
                    if any(kw in classes.lower() for kw in ("comment", "discussion", "footer")):
                        container.decompose()
                        break
            else:
                el.decompose()

    # Individual comments (Substack: "Comment by X")
    for el in list(root.find_all(attrs={"aria-label": True})):
        label = el.get("aria-label", "")
        if re.match(r"comment\s+by\s+", label, re.IGNORECASE):
            # Walk up to the comment container
            comment_root = el
            for _ in range(4):
                comment_root = comment_root.parent
                if comment_root is None or comment_root is root:
                    break
                if comment_root.name == "div" and comment_root.get("class"):
                    classes = " ".join(comment_root.get("class", []))
                    if "comment" in classes.lower():
                        comment_root.decompose()
                        break

    # Engagement / UFI buttons inside body: Like, Comment, Restack, Share
    for el in list(root.find_all(attrs={"aria-label": True})):
        label = el.get("aria-label", "").lower()
        if re.match(r"^(like|comment|restack|share|bookmark|view\s+comments?)", label):
            container = el
            for _ in range(3):
                container = container.parent
                if container is None or container is root:
                    break
                if container.name == "div" and container.get("class"):
                    classes_str = " ".join(container.get("class", []))
                    if any(kw in classes_str.lower() for kw in
                           ("post-ufi", "like-button", "restack", "share-button",
                            "ufi-button", "post-footer-cta")):
                        container.decompose()
                        break

    # Substack CTA boxes: subscribe prompts, "Ready for more?"
    for el in list(root.find_all("h3")):
        text = cleanup_text(el.get_text(" ", strip=True)).lower()
        if text in ("ready for more?", "subscribe to", "keep reading"):
            # Remove the enclosing CTA section
            container = el
            for _ in range(5):
                container = container.parent
                if container is None or container is root:
                    break
                if container.name in ("div", "section") and container.get("class"):
                    container.decompose()
                    break

    # Post-preview cards (Substack footer: "Post preview for ...")
    for el in list(root.find_all(attrs={"aria-label": True})):
        label = el.get("aria-label", "")
        if "post preview" in label.lower():
            container = el
            for _ in range(4):
                container = container.parent
                if container is None or container is root:
                    break
                if container.name == "div" and container.get("class"):
                    container.decompose()
                    break

    # Share buttons / engagement bars
    for el in list(root.find_all(string=lambda t: t and cleanup_text(t).lower() in ("share", "restack"))):
        container = el.parent
        for _ in range(4):
            if container is None or container is root:
                break
            if container.name in ("button", "a"):
                container.decompose()
                break
            container = container.parent

    # Archive / tab sections ("Archive sort tabs")
    for el in list(root.find_all(attrs={"aria-label": True})):
        label = el.get("aria-label", "").lower()
        if any(kw in label for kw in ("archive", "tabs", "sort tabs")):
            container = el
            for _ in range(4):
                container = container.parent
                if container is None or container is root:
                    break
                if container.name == "div" and container.get("class"):
                    container.decompose()
                    break


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
    if node.name == "pre":
        return is_prose_preformatted_text(node.get_text("\n", strip=True))
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
    """Extract structured content sections from an HTML content root.

    Handles the case where a ``<blockquote>`` contains nested ``<p>``
    elements (common on Substack) by only extracting the blockquote as
    one unit — inner ``<p>`` children are skipped.
    """
    sections: list[ContentSection] = []
    seen: set[str] = set()

    if title:
        sections.append(ContentSection(type=SectionType.HEADING, text=title, level=1))
        seen.add(cleanup_text(title).lower())

    # Collect all blockquote elements first — their descendants should
    # not be extracted separately.
    blockquote_ids: set[int] = set()
    for bq in root.find_all("blockquote"):
        blockquote_ids.add(id(bq))

    for node in root.descendants:
        if isinstance(node, NavigableString) or not isinstance(node, Tag):
            continue
        if node.name in SKIP_TAGS or not is_leaf_block(node):
            continue

        # Skip nodes that are inside an already-processed blockquote
        if node.name != "blockquote" and _has_ancestor(node, "blockquote"):
            # Only skip if the ancestor blockquote is in our tree
            # (not a nested quote from a different source)
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


def _filter_byline_sections(
    sections: list[ContentSection],
    author: str | None,
) -> list[ContentSection]:
    """Remove sections near the top that are byline/date metadata.

    Scans the first few sections for lines that match the detected
    author name or a date pattern, and removes them.
    """
    if not sections:
        return sections

    author_lower = cleanup_text(author).lower() if author else ""
    author_parts = set(author_lower.split()) if author_lower else set()

    to_remove: set[int] = set()
    for i in range(min(8, len(sections))):
        s = sections[i]
        if s.type != SectionType.PARAGRAPH:
            continue
        text_lower = cleanup_text(s.text).lower()

        # Exact author name match (e.g., "Johann Kurtz")
        if author_lower and text_lower == author_lower:
            to_remove.add(i)
            continue

        # Date-only line
        for pat in _DATE_PATTERNS:
            if pat.fullmatch(text_lower):
                to_remove.add(i)
                break
            # "Jul 14, 2026 • AuthorName"
            m = pat.match(text_lower)
            if m:
                rest = text_lower[m.end():].strip().lstrip("•-–—").strip()
                rest_words = set(rest.lower().split())
                if author_parts and rest_words & author_parts:
                    to_remove.add(i)
                    break
                # Short remainder likely a name
                if len(rest.split()) <= 2:
                    to_remove.add(i)
                    break

    if to_remove:
        return [s for i, s in enumerate(sections) if i not in to_remove]
    return sections


def extract_from_html(html_path: Path) -> Article:
    """Extract a complete Article from a saved HTML file.

    Args:
        html_path: Path to an HTML file (X.com article, Substack
                   newsletter, or any long-form article page).

    Returns:
        A structured Article with metadata and content sections.
    """
    html = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")

    # Read JSON-LD author metadata before removing scripts.
    author_name, author_handle = find_author(soup)
    author = author_name or "Unknown"
    handle = author_handle or ""

    # Strip unwanted tags before title fallback so site-brand headers cannot
    # outrank the document title.
    for tag in soup.find_all(SKIP_TAGS):
        tag.decompose()

    title = find_title(soup) or html_path.stem
    date = find_date(soup)
    description = find_description(soup) or ""
    lang = (soup.html.get("lang") if soup.html else None) or "en"

    # Find the broad content container, then narrow to the body
    content_root = find_content_root(soup)
    body_root = find_body_root(content_root)

    # Strip non-content regions from the body root and turn prose-only
    # preformatted blocks into paragraphs before markdownify sees them.
    _decompose_non_content(body_root)
    _normalise_prose_pre_blocks(body_root)

    # Store the raw HTML body for downstream markdownify usage
    html_body = str(body_root)

    sections = extract_sections(body_root, title=title)

    # Remove byline/date paragraphs that leaked into content
    sections = _filter_byline_sections(sections, author)

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
    return Article(metadata=metadata, sections=sections, html_body=html_body)
