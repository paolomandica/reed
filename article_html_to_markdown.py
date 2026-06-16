#!/usr/bin/env python3
import argparse
import re
import sys
from pathlib import Path
from bs4 import BeautifulSoup, Tag, NavigableString

SEMANTIC_BLOCKS = {
    'p', 'blockquote', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'
}
GENERIC_BLOCKS = {'div', 'section', 'article'}
SKIP_TAGS = {
    'script', 'style', 'noscript', 'svg', 'button', 'input', 'textarea',
    'form', 'footer', 'header', 'nav', 'aside', 'img', 'figure', 'video',
    'audio', 'canvas'
}
CONTAINER_HINTS = [
    {'data-testid': 'longformRichTextComponent'},
    {'data-testid': 'twitterArticleRichTextView'},
    {'data-testid': 'article'},
]
ROLE_HINTS = ['main', 'article']
CLASS_HINTS = ['longform', 'article', 'richtext', 'content', 'post', 'story', 'entry']


def normalize_ws(text: str) -> str:
    return re.sub(r'\s+', ' ', text.replace('\xa0', ' ')).strip()


def cleanup_text(text: str) -> str:
    text = normalize_ws(text)
    return re.sub(r'\s+([,.;:!?])', r'\1', text)


def is_probably_ui_text(text: str) -> bool:
    if not text:
        return True
    lowered = text.lower()
    ui_phrases = {
        'reply', 'repost', 'like', 'likes', 'bookmarked', 'share post',
        'view post analytics', 'upgrade to premium', 'want to publish your own article?',
        'premium', 'analytics'
    }
    if lowered in ui_phrases:
        return True
    return bool(re.fullmatch(r'[\d.,]+\s*(replies|reply|reposts|likes|views|bookmarks?)', lowered))


def find_title(soup: BeautifulSoup) -> str | None:
    selectors = [
        {'data-testid': 'twitter-article-title'},
        {'property': 'og:title'},
        {'name': 'twitter:title'},
    ]
    for attrs in selectors:
        node = soup.find(attrs=attrs)
        if not node:
            continue
        if node.name == 'meta':
            title = cleanup_text(node.get('content', ''))
        else:
            title = cleanup_text(node.get_text(' ', strip=True))
        if title:
            return title
    for tag in ['h1', 'title']:
        node = soup.find(tag)
        if node:
            title = cleanup_text(node.get_text(' ', strip=True))
            if title:
                return title
    return None


def score_candidate(node: Tag) -> int:
    score = 0
    text = cleanup_text(node.get_text(' ', strip=True))
    score += min(len(text), 4000) // 40
    for h in node.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
        if cleanup_text(h.get_text(' ', strip=True)):
            score += 25
    for p in node.find_all(['p', 'blockquote']):
        if cleanup_text(p.get_text(' ', strip=True)):
            score += 12
    classes = ' '.join(node.get('class', []))
    attrs = ' '.join(f'{k}={v}' for k, v in node.attrs.items())
    lowered = f'{classes} {attrs}'.lower()
    if any(hint in lowered for hint in CLASS_HINTS):
        score += 40
    return score


def find_content_root(soup: BeautifulSoup) -> Tag:
    for attrs in CONTAINER_HINTS:
        node = soup.find(attrs=attrs)
        if node:
            return node
    for role in ROLE_HINTS:
        node = soup.find(attrs={'role': role})
        if node:
            return node
    for tag in ['article', 'main']:
        node = soup.find(tag)
        if node:
            return node
    candidates = [n for n in soup.find_all(['div', 'section']) if cleanup_text(n.get_text(' ', strip=True))]
    return max(candidates, key=score_candidate) if candidates else (soup.body or soup)


def has_nested_block(node: Tag) -> bool:
    for child in node.children:
        if not isinstance(child, Tag):
            continue
        if child.name in SKIP_TAGS:
            continue
        if child.name in SEMANTIC_BLOCKS or child.name in GENERIC_BLOCKS:
            if cleanup_text(child.get_text(' ', strip=True)):
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


def line_from_node(node: Tag) -> str | None:
    if node.name in SKIP_TAGS:
        return None
    text = cleanup_text(node.get_text(' ', strip=True))
    if not text or is_probably_ui_text(text):
        return None
    if node.name in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
        return f"{'#' * int(node.name[1])} {text}"
    if node.name == 'blockquote':
        return '\n'.join(f'> {cleanup_text(line)}' for line in text.splitlines() if cleanup_text(line)) or f'> {text}'
    if node.name == 'li':
        return f'- {text}'
    return text


def extract_markdown(root: Tag, title: str | None = None) -> str:
    parts: list[str] = []
    seen = set()
    if title:
        parts.append(f'# {title}')
        seen.add(cleanup_text(title).lower())

    for node in root.descendants:
        if isinstance(node, NavigableString) or not isinstance(node, Tag):
            continue
        if node.name in SKIP_TAGS or not is_leaf_block(node):
            continue
        line = line_from_node(node)
        if not line:
            continue
        key = cleanup_text(re.sub(r'^#+\s*', '', line)).lower()
        if not key or len(key) < 2 or key in seen:
            continue
        seen.add(key)
        parts.append(line)

    output: list[str] = []
    prev_list = False
    for part in parts:
        is_list = part.startswith('- ')
        if output:
            if not (is_list and prev_list):
                output.append('')
        output.append(part)
        prev_list = is_list
    return '\n'.join(output).strip() + '\n'


def read_input(path: str | None) -> str:
    return Path(path).read_text(encoding='utf-8') if path else sys.stdin.read()


def main() -> int:
    parser = argparse.ArgumentParser(description='Convert article-like HTML into clean Markdown.')
    parser.add_argument('input', nargs='?', help='Input HTML file. If omitted, read from stdin.')
    parser.add_argument('-o', '--output', help='Write Markdown to this file instead of stdout.')
    args = parser.parse_args()

    html = read_input(args.input)
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup.find_all(SKIP_TAGS):
        tag.decompose()

    markdown = extract_markdown(find_content_root(soup), title=find_title(soup))
    if args.output:
        Path(args.output).write_text(markdown, encoding='utf-8')
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
